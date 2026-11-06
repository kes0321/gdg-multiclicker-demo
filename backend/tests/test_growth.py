"""Run: .venv/Scripts/python.exe -B -m unittest discover -s tests -v

Uses REDIS_TEST_URL (default localhost:6379/15), unique keys, and cleans up
only its own fixtures. No flush, production refill scan, or server startup.
"""

import asyncio
import json
import os
import unittest
from fractions import Fraction
from http.cookies import SimpleCookie
from types import SimpleNamespace
from unittest.mock import patch
from uuid import uuid4

from fastapi import FastAPI
from redis.asyncio import Redis

from app.balance import InvalidPlayerLevel, MAX_SAFE_INTEGER, balance_for_level, safe_balance_for_level
from app.api import http, websocket
from app.repositories.repository import RedisRepository
from app.services.connection_manager import ConnectionManager


class BalanceTests(unittest.TestCase):
    def test_reference_values(self):
        for level, gold, cost, next_gold in (
            (1, 10, 100, 12), (2, 12, 160, 15), (5, 24, 656, 30),
            (9, 59, 4295, 74), (10, 74, 6872, 93), (15, 227, 72058, 284),
            (30, 6462, 83076750, 8077), (31, 8077, 132922800, 10097),
            (39, 48148, 5708990771, 60185), (40, 60185, None, None),
        ):
            with self.subTest(level=level):
                self.assertEqual(balance_for_level(level), {
                    "gold_per_click": gold, "next_upgrade_cost": cost,
                    "next_gold_per_click": next_gold,
                })

    def test_decimal_matches_independent_exact_rationals_through_level40(self):
        for level in range(1, 41):
            def exact(base, growth, exponent, ceil=False):
                value = base * growth**exponent
                return -(-value.numerator // value.denominator) if ceil else value.numerator // value.denominator
            self.assertEqual(balance_for_level(level), {
                "gold_per_click": exact(10, Fraction(5, 4), level - 1),
                "next_gold_per_click": exact(10, Fraction(5, 4), level) if level < 40 else None,
                "next_upgrade_cost": exact(100, Fraction(8, 5), level - 1, ceil=True) if level < 40 else None,
            })

    def test_level_guard_rejects_extreme_stored_levels_without_huge_allocations(self):
        with self.assertRaises(InvalidPlayerLevel):
            safe_balance_for_level(1_000_000_000)


class GrowthTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.url = os.environ.get("REDIS_TEST_URL", "redis://localhost:6379/15")
        self.redis = Redis.from_url(self.url, decode_responses=True)
        await self.redis.ping()
        self.repository = RedisRepository(self.redis)
        self.prefix = f"growth-test-{uuid4().hex}"
        self.repository.LEADERBOARD_KEY = f"{self.prefix}:leaderboard"
        self.keys = {self.repository.LEADERBOARD_KEY}
        self.tasks = []
        self.manager_patch = patch.object(websocket, "manager", ConnectionManager())
        self.manager_patch.start()
        self.app = FastAPI()
        self.app.state.repository = self.repository
        self.app.include_router(http.router)
        self.app.include_router(websocket.router)

    async def asyncTearDown(self):
        for task in self.tasks:
            task.cancel()
        await asyncio.gather(*self.tasks, return_exceptions=True)
        self.manager_patch.stop()
        await self.redis.delete(*self.keys)
        await self.redis.aclose()

    async def seed(self, level=1, score=0, tokens=30):
        player_id = f"{self.prefix}-{len(self.keys)}"
        player_key = f"player:{player_id}"
        bucket_key = f"rate_limit:click:{player_id}"
        self.keys.update((player_key, bucket_key))
        await self.redis.hset(player_key, mapping={
            "nickname": "fixture", "score": score, "level": level,
            "total_clicks": 0, "created_at_ms": 123,
        })
        await self.redis.hset(bucket_key, mapping={"tokens": tokens, "last_refill_ms": 123})
        await self.redis.expire(bucket_key, 60)
        await self.redis.zadd(self.repository.LEADERBOARD_KEY, {player_id: score})
        return player_id

    async def snapshot(self, player_id):
        return (
            await self.redis.hgetall(f"player:{player_id}"),
            await self.redis.hgetall(f"rate_limit:click:{player_id}"),
            await self.redis.zscore(self.repository.LEADERBOARD_KEY, player_id),
        )

    async def request(self, method, path, body=None, token=None):
        messages = []
        payload = json.dumps(body).encode() if body is not None else b""
        headers = [(b"content-type", b"application/json")]
        if token:
            headers.append((b"cookie", f"session_token={token}".encode()))

        async def receive():
            return {"type": "http.request", "body": payload, "more_body": False}

        async def send(message):
            messages.append(message)

        await self.app({
            "type": "http", "method": method, "path": path, "root_path": "",
            "query_string": b"", "headers": headers, "scheme": "http",
            "server": ("test", 80), "client": ("test", 1), "http_version": "1.1",
        }, receive, send)
        return messages[0], json.loads(b"".join(
            message.get("body", b"") for message in messages[1:]
        ))

    async def connect(self, token):
        incoming, outgoing = asyncio.Queue(), asyncio.Queue()
        await incoming.put({"type": "websocket.connect"})
        task = asyncio.create_task(self.app({
            "type": "websocket", "path": "/ws", "root_path": "",
            "query_string": b"", "headers": [(b"cookie", f"session_token={token}".encode())],
            "scheme": "ws", "server": ("test", 80), "client": ("test", 1),
            "subprotocols": [],
        }, incoming.get, outgoing.put))
        self.tasks.append(task)
        return SimpleNamespace(incoming=incoming, outgoing=outgoing, task=task)

    async def next_message(self, session, message_type):
        while True:
            event = await asyncio.wait_for(session.outgoing.get(), 3)
            if event["type"] == "websocket.send":
                message = json.loads(event["text"])
                if message["type"] == message_type:
                    return message
            elif event["type"] == "websocket.close":
                self.fail(f"Unexpected close: {event}")

    async def send(self, session, message):
        await session.incoming.put({"type": "websocket.receive", "text": json.dumps(message)})

    async def session_fixture(self):
        player_id = await self.seed(level=13, score=3234, tokens=7)
        await self.redis.hset(f"player:{player_id}", "total_clicks", 17)
        token = f"{self.prefix}-entry"
        self.keys.add(f"session:{token}")
        await self.redis.set(f"session:{token}", player_id, ex=120)
        return player_id, token

    async def test_session_preview_is_read_only_and_cookie_scoped(self):
        player_id, token = await self.session_fixture()
        before = await self.snapshot(player_id)
        ttl = await self.redis.pttl(f"session:{token}")
        response, body = await self.request("GET", "/api/session", token=token)
        self.assertEqual(response["status"], 200)
        self.assertEqual(body["player"]["player_id"], player_id)
        self.assertEqual((body["player"]["level"], body["player"]["score"]), (13, 3234))
        self.assertIn((b"cache-control", b"no-store"), response["headers"])
        self.assertFalse(any(key == b"set-cookie" for key, _ in response["headers"]))
        self.assertLessEqual(await self.redis.pttl(f"session:{token}"), ttl)
        self.assertEqual(await self.snapshot(player_id), before)
        for missing in (None, "unknown-token"):
            response, body = await self.request("GET", "/api/session", token=missing)
            self.assertEqual((response["status"], body), (200, {"player": None}))
        await self.redis.expire(f"session:{token}", 0)
        self.assertEqual((await self.request("GET", "/api/session", token=token))[1], {"player": None})

    async def test_resume_and_rename_preserve_progress(self):
        player_id, token = await self.session_fixture()
        before = await self.snapshot(player_id)
        response, body = await self.request("POST", "/api/players", {
            "nickname": "ignored", "action": "resume", "expected_player_id": player_id,
        }, token)
        self.assertEqual(response["status"], 200)
        self.assertEqual(body["player"]["nickname"], "fixture")
        self.assertEqual(await self.snapshot(player_id), before)
        response, body = await self.request("POST", "/api/players", {
            "nickname": "  새 이름  ", "action": "rename", "expected_player_id": player_id,
        }, token)
        self.assertEqual(response["status"], 200)
        self.assertFalse(body["created"])
        self.assertEqual(body["player"]["nickname"], "새 이름")
        after = await self.snapshot(player_id)
        self.assertEqual(after[0], {**before[0], "nickname": "새 이름"})
        self.assertEqual(after[1:], before[1:])
        self.assertGreater(await self.redis.ttl(f"session:{token}"), 120)

    async def test_restart_resets_progress_bucket_and_ranking_with_existing_socket(self):
        player_id, token = await self.session_fixture()
        other_id = await self.seed(level=20, score=9999)
        other_before = await self.snapshot(other_id)
        socket = await self.connect(token)
        await self.next_message(socket, "connected")
        response, body = await self.request("POST", "/api/players", {
            "nickname": "처음부터", "action": "restart", "expected_player_id": player_id,
        }, token)
        self.assertEqual(response["status"], 200)
        player = body["player"]
        self.assertEqual((player["player_id"], player["nickname"], player["level"], player["score"], player["total_clicks"]), (player_id, "처음부터", 1, 0, 0))
        self.assertGreater(player["created_at_ms"], 123)
        self.assertEqual((player["gold_per_click"], player["next_gold_per_click"], player["next_upgrade_cost"]), (10, 12, 100))
        self.assertEqual(await self.redis.zscore(self.repository.LEADERBOARD_KEY, player_id), 0)
        self.assertEqual(await self.redis.hmget(f"rate_limit:click:{player_id}", "tokens", "last_refill_ms"), ["30", str(player["created_at_ms"])])
        self.assertGreater(await self.redis.ttl(f"rate_limit:click:{player_id}"), 0)
        self.assertEqual(await self.snapshot(other_id), other_before)
        await self.send(socket, {"type": "click"})
        result = await self.next_message(socket, "click_result")
        self.assertEqual((result["level"], result["gold_gained"], result["score"], result["total_clicks"]), (1, 10, 10, 1))

    async def test_entry_actions_reject_stale_sessions_and_invalid_requests(self):
        player_id, token = await self.session_fixture()
        before = await self.snapshot(player_id)
        for body, cookie, status in (
            ({"nickname": "new", "action": "restart"}, token, 409),
            ({"nickname": "new", "action": "rename", "expected_player_id": "other"}, token, 409),
            ({"nickname": "new", "action": "restart", "expected_player_id": player_id}, None, 409),
            ({"nickname": "new", "action": "delete"}, token, 422),
            ({"nickname": " ", "action": "rename", "expected_player_id": player_id}, token, 422),
            ({"nickname": "x" * 21, "action": "restart", "expected_player_id": player_id}, token, 422),
        ):
            response, _ = await self.request("POST", "/api/players", body, cookie)
            self.assertEqual(response["status"], status)
            self.assertEqual(await self.snapshot(player_id), before)
        other_id = await self.seed()
        other_before = await self.snapshot(other_id)
        await self.redis.set(f"session:{token}", other_id, ex=120)
        response, _ = await self.request("POST", "/api/players", {
            "nickname": "new", "action": "restart", "expected_player_id": player_id,
        }, token)
        self.assertEqual(response["status"], 409)
        self.assertEqual(await self.snapshot(player_id), before)
        self.assertEqual(await self.snapshot(other_id), other_before)
        self.assertFalse(await self.repository.update_player_session(token, player_id, "new", "restart"))

    async def test_restart_validates_redis_types_before_any_write(self):
        player_id, token = await self.session_fixture()
        before = await self.redis.hgetall(f"player:{player_id}")
        await self.redis.set(f"rate_limit:click:{player_id}", "wrong-type")
        response, _ = await self.request("POST", "/api/players", {
            "nickname": "new", "action": "restart", "expected_player_id": player_id,
        }, token)
        self.assertEqual(response["status"], 503)
        self.assertEqual(await self.redis.hgetall(f"player:{player_id}"), before)
        self.assertEqual(await self.redis.zscore(self.repository.LEADERBOARD_KEY, player_id), 3234)

    async def test_all_levels_share_response_and_lua_values(self):
        for level in range(1, 41):
            with self.subTest(level=level):
                balance = balance_for_level(level)
                player_id = await self.seed(level, score=(balance["next_upgrade_cost"] or 0) + 1_000_000_000)
                player = await self.repository.get_player(player_id)
                for key, value in balance.items():
                    self.assertEqual(player[key], value)
                single = await self.repository.click(player_id)
                batch = await self.repository.click_batch(player_id, 3)
                for result, count in ((single, 1), (batch, 3)):
                    self.assertTrue(result["success"])
                    self.assertEqual(result["accepted_clicks"], count)
                    self.assertEqual(result["gold_gained"], balance["gold_per_click"] * count)
                    for key, value in balance.items():
                        self.assertEqual(result[key], value)
                self.assertEqual(batch["total_clicks"], 4)
                self.assertEqual(await self.redis.hget(f"rate_limit:click:{player_id}", "tokens"), "26")
                before = await self.snapshot(player_id)
                upgrade = await self.repository.levelup(player_id)
                if level == 40:
                    self.assertEqual(upgrade["code"], "MAX_LEVEL_REACHED")
                    self.assertEqual(upgrade["gold_per_click"], 60185)
                    self.assertIsNone(upgrade["next_gold_per_click"])
                    self.assertIsNone(upgrade["next_upgrade_cost"])
                    self.assertEqual(await self.snapshot(player_id), before)
                else:
                    self.assertTrue(upgrade["success"])
                    self.assertEqual(upgrade["gold_spent"], balance["next_upgrade_cost"])
                    self.assertEqual(upgrade["score"], batch["score"] - balance["next_upgrade_cost"])
                    self.assertEqual(upgrade["level"], level + 1)
                    for key, value in balance_for_level(level + 1).items():
                        self.assertEqual(upgrade[key], value)
                    self.assertEqual(await self.redis.zscore(self.repository.LEADERBOARD_KEY, player_id), upgrade["score"])
                stored = await self.redis.hgetall(f"player:{player_id}")
                self.assertNotIn("gold_per_click", stored)
                self.assertNotIn("next_gold_per_click", stored)
                self.assertNotIn("next_upgrade_cost", stored)

    async def test_rejections_preserve_state_and_batch_validation(self):
        player_id = await self.seed(level=5, score=655, tokens=2)
        before = await self.snapshot(player_id)
        insufficient = await self.repository.levelup(player_id)
        self.assertEqual(insufficient, {"success": False, "code": "INSUFFICIENT_GOLD", "score": 655, "required_gold": 656})
        self.assertEqual(await self.snapshot(player_id), before)
        self.assertEqual(await self.repository.click_batch(player_id, 3), {
            "success": False, "code": "RATE_LIMITED", "remaining_tokens": 2,
        })
        self.assertEqual(await self.snapshot(player_id), before)
        for count in (0, 11, True, "3", 3.0, None):
            with self.assertRaises(ValueError):
                await self.repository.click_batch(player_id, count)
            self.assertEqual(await self.snapshot(player_id), before)
        for count in (0, 11, 1.5):
            result = await self.repository._click_batch_script(
                keys=[f"player:{player_id}", self.repository.LEADERBOARD_KEY, f"rate_limit:click:{player_id}"],
                args=[player_id, count],
            )
            self.assertEqual(result, [0, "INVALID_BATCH_COUNT"])
            self.assertEqual(await self.snapshot(player_id), before)
        await self.redis.hset(f"rate_limit:click:{player_id}", "tokens", 0)
        before = await self.snapshot(player_id)
        self.assertEqual((await self.repository.click(player_id))["code"], "RATE_LIMITED")
        self.assertEqual(await self.snapshot(player_id), before)

    async def test_invalid_legacy_levels_are_reported_without_changes(self):
        for level in (-1, 0, 41, "1.5", "invalid"):
            player_id = await self.seed(level, score=1000)
            before = await self.snapshot(player_id)
            with self.assertLogs("app.repositories.repository", level="ERROR"):
                with self.assertRaises(InvalidPlayerLevel):
                    await self.repository.get_player(player_id)
                for operation in (self.repository.click, self.repository.levelup):
                    self.assertEqual((await operation(player_id))["code"], "INVALID_PLAYER_LEVEL")
            self.assertEqual(await self.snapshot(player_id), before)
        token = f"{self.prefix}-invalid"
        self.keys.add(f"session:{token}")
        await self.redis.set(f"session:{token}", player_id)
        with self.assertLogs("app.repositories.repository", level="ERROR"):
            for method, path, body in (("GET", f"/api/players/{player_id}", None), ("POST", "/api/players", {"nickname": "restore"})):
                response, data = await self.request(method, path, body, token)
                self.assertEqual(response["status"], 409)
                self.assertEqual(data, {"detail": {"code": "INVALID_PLAYER_LEVEL"}})
            session = await self.connect(token)
            close = await asyncio.wait_for(session.outgoing.get(), 3)
            self.assertEqual(close, {"type": "websocket.close", "code": 1008, "reason": "INVALID_PLAYER_LEVEL"})
            await session.task
        self.assertEqual(await self.snapshot(player_id), before)

    async def test_two_redis_connections_use_execution_level_atomically(self):
        player_id = await self.seed(level=30, score=1_000_000_000_000)
        other_client = Redis.from_url(self.url, decode_responses=True)
        other = RedisRepository(other_client)
        other.LEADERBOARD_KEY = self.repository.LEADERBOARD_KEY
        try:
            actions = []
            for _ in range(6):
                actions.extend((self.repository.click_batch(player_id, 3), other.levelup(player_id)))
            results = await asyncio.gather(*actions)
        finally:
            await other_client.aclose()
        gained = spent = 0
        for result in results:
            self.assertTrue(result["success"])
            if "gold_gained" in result:
                self.assertEqual(result["gold_gained"], balance_for_level(result["level"])["gold_per_click"] * 3)
                gained += result["gold_gained"]
            else:
                self.assertEqual(result["gold_spent"], balance_for_level(result["level"] - 1)["next_upgrade_cost"])
                spent += result["gold_spent"]
        player = await self.repository.get_player(player_id)
        self.assertEqual((player["level"], player["total_clicks"]), (36, 18))
        self.assertEqual(player["score"], 1_000_000_000_000 + gained - spent)
        self.assertEqual(await self.redis.zscore(self.repository.LEADERBOARD_KEY, player_id), player["score"])

    async def test_lua_rechecks_level_when_it_changes_during_balance_generation(self):
        player_id = await self.seed(level=30, score=1_000_000_000)
        required_levels = []

        async def interleaved_script(*, keys, args):
            before = await self.snapshot(player_id)
            result = await self.repository._click_batch_script(keys=keys, args=args)
            if result[:2] == [0, "BALANCE_REQUIRED"]:
                self.assertEqual(await self.snapshot(player_id), before)
                required_levels.append(result[2])
                if len(required_levels) == 1:
                    self.assertTrue((await self.repository.levelup(player_id))["success"])
            return result

        result = await self.repository._run_game_script(
            interleaved_script,
            keys=[f"player:{player_id}", self.repository.LEADERBOARD_KEY, f"rate_limit:click:{player_id}"],
            args=[player_id, 3],
        )
        self.assertEqual(required_levels, [30, 31])
        self.assertEqual(result[0], 1)
        self.assertEqual((result[2], result[4]), (31, 8077 * 3))
        self.assertEqual(result[1], 1_000_000_000 - 83076750 + 8077 * 3)
        self.assertEqual(await self.redis.hget(f"rate_limit:click:{player_id}", "tokens"), "27")

    async def test_http_and_two_websockets_use_authoritative_growth(self):
        response, body = await self.request("POST", "/api/players", {"nickname": "fixture"})
        player_id = body["player"]["player_id"]
        cookie = SimpleCookie()
        cookie.load(next(value.decode() for key, value in response["headers"] if key == b"set-cookie"))
        token = cookie["session_token"].value
        self.keys.update((f"player:{player_id}", f"rate_limit:click:{player_id}", f"session:{token}"))
        self.assertEqual(response["status"], 201)
        self.assertEqual(body["player"]["gold_per_click"], 10)
        self.assertEqual(body["player"]["next_gold_per_click"], 12)
        self.assertEqual(body["player"]["next_upgrade_cost"], 100)
        response, restored = await self.request("POST", "/api/players", {"nickname": "restore"}, token)
        self.assertEqual(response["status"], 200)
        self.assertFalse(restored["created"])
        first, second = await self.connect(token), await self.connect(token)
        for session in (first, second):
            connected = await self.next_message(session, "connected")
            self.assertEqual(connected["player"], body["player"])
        await self.send(first, {"type": "click_batch", "count": 10, "gold_per_click": 999})
        batch = await self.next_message(first, "click_batch_result")
        self.assertEqual((batch["score"], batch["gold_gained"]), (100, 100))
        await self.send(second, {"type": "levelup", "cost": 0})
        upgraded = await self.next_message(second, "levelup_result")
        self.assertEqual((upgraded["level"], upgraded["score"], upgraded["gold_spent"]), (2, 0, 100))
        self.assertEqual((upgraded["gold_per_click"], upgraded["next_upgrade_cost"]), (12, 160))
        self.assertEqual(upgraded["next_gold_per_click"], 15)
        await self.send(first, {"type": "click"})
        clicked = await self.next_message(first, "click_result")
        self.assertEqual((clicked["score"], clicked["gold_gained"], clicked["gold_per_click"]), (12, 12, 12))
        self.assertEqual(clicked["next_upgrade_cost"], 160)
        response, current = await self.request("GET", f"/api/players/{player_id}")
        self.assertEqual(response["status"], 200)
        self.assertEqual((current["level"], current["gold_per_click"], current["next_upgrade_cost"]), (2, 12, 160))
        # Former boundary fixture only: production state is never migrated/reset.
        await self.redis.hset(f"player:{player_id}", mapping={"level": 30, "score": 83076750})
        await self.redis.zadd(self.repository.LEADERBOARD_KEY, {player_id: 83076750})
        await self.send(second, {"type": "levelup"})
        beyond_thirty = await self.next_message(second, "levelup_result")
        self.assertTrue(beyond_thirty["success"])
        self.assertEqual((beyond_thirty["level"], beyond_thirty["score"]), (31, 0))
        self.assertEqual(beyond_thirty["gold_spent"], 83076750)
        self.assertEqual(beyond_thirty["next_upgrade_cost"], 132922800)
        await self.send(first, {"type": "click"})
        continued_click = await self.next_message(first, "click_result")
        self.assertEqual(continued_click["gold_gained"], 8077)
        self.assertEqual(continued_click["next_gold_per_click"], 10097)
        self.assertEqual(continued_click["next_upgrade_cost"], 132922800)

    async def test_numeric_limits_reject_before_any_state_changes(self):
        for level, score, operation in (
            (20, MAX_SAFE_INTEGER - 1, self.repository.click),
            (39, MAX_SAFE_INTEGER + 1, self.repository.levelup),
            (40, MAX_SAFE_INTEGER - 1, self.repository.click),
        ):
            player_id = await self.seed(level=level, score=score)
            before = await self.snapshot(player_id)
            result = await operation(player_id)
            self.assertEqual(result, {"success": False, "code": "NUMERIC_LIMIT_EXCEEDED"})
            self.assertEqual(await self.snapshot(player_id), before)

    async def test_deleted_player_cannot_resume_or_recreate_state_from_open_sockets(self):
        player_id, token = await self.session_fixture()
        first, second = await self.connect(token), await self.connect(token)
        for session in (first, second):
            await self.next_message(session, "connected")
        self.assertTrue(await self.repository.admin_delete_player(player_id))
        await websocket.manager.disconnect_player(player_id)
        for session in (first, second):
            while True:
                event = await asyncio.wait_for(session.outgoing.get(), 3)
                if event["type"] == "websocket.close":
                    self.assertEqual((event["code"], event["reason"]), (1008, "PLAYER_DELETED"))
                    break
            await session.incoming.put({"type": "websocket.disconnect", "code": 1008})
            await session.task
        self.assertEqual(websocket.manager.connections, {})
        self.assertEqual((await self.request("GET", "/api/session", token=token))[1], {"player": None})
        self.assertEqual((await self.request("GET", f"/api/players/{player_id}"))[0]["status"], 404)
        self.assertEqual((await self.request("POST", "/api/players", {
            "nickname": "fixture", "action": "restart", "expected_player_id": player_id,
        }, token))[0]["status"], 409)
        self.assertFalse(await self.repository.update_player_session(token, player_id, "fixture", "restart"))
        denied = await self.connect(token)
        self.assertEqual((await asyncio.wait_for(denied.outgoing.get(), 3))["code"], 1008)
        await denied.task
        await self.repository._refill_buckets_script(keys=[f"rate_limit:click:{player_id}"], args=[30, 4])
        self.assertEqual(await self.snapshot(player_id), ({}, {}, None))

    async def test_deletion_during_websocket_handshake_cannot_leave_a_registered_connection(self):
        player_id, token = await self.session_fixture()
        original = self.repository.get_player
        async def delete_after_lookup(current_id):
            player = await original(current_id)
            await self.repository.admin_delete_player(current_id)
            await websocket.manager.disconnect_player(current_id)
            return player
        with patch.object(self.repository, "get_player", delete_after_lookup):
            session = await self.connect(token)
            self.assertEqual((await asyncio.wait_for(session.outgoing.get(), 3))["type"], "websocket.accept")
            close = await asyncio.wait_for(session.outgoing.get(), 3)
            self.assertEqual((close["type"], close["code"], close["reason"]), ("websocket.close", 1008, "PLAYER_DELETED"))
            await session.task
        self.assertEqual(websocket.manager.connections, {})

    async def test_two_websockets_stop_at_level40_and_keep_clicking(self):
        cost = 5708990771
        player_id = await self.seed(level=39, score=cost * 2 + 100)
        token = f"{self.prefix}-max"
        self.keys.add(f"session:{token}")
        await self.redis.set(f"session:{token}", player_id)
        first, second = await self.connect(token), await self.connect(token)
        for session in (first, second):
            connected = await self.next_message(session, "connected")
            self.assertEqual(connected["player"]["next_gold_per_click"], 60185)
        await asyncio.gather(self.send(first, {"type": "levelup"}), self.send(second, {"type": "levelup"}))
        results = await asyncio.gather(self.next_message(first, "levelup_result"), self.next_message(second, "levelup_result"))
        self.assertEqual(sum(result["success"] for result in results), 1)
        for result in results:
            self.assertEqual(result["level"], 40)
            self.assertEqual(result["score"], cost + 100)
            self.assertEqual(result["gold_per_click"], 60185)
            self.assertIsNone(result["next_gold_per_click"])
            self.assertIsNone(result["next_upgrade_cost"])
            if result["success"]:
                self.assertEqual(result["gold_spent"], cost)
            else:
                self.assertEqual(result["code"], "MAX_LEVEL_REACHED")
        before = await self.snapshot(player_id)
        await self.send(first, {"type": "levelup"})
        self.assertEqual((await self.next_message(first, "levelup_result"))["code"], "MAX_LEVEL_REACHED")
        self.assertEqual(await self.snapshot(player_id), before)
        for session, message, count in ((first, {"type": "click"}, 1), (second, {"type": "click_batch", "count": 3}, 3)):
            await self.send(session, message)
            result = await self.next_message(session, message["type"] + "_result")
            self.assertEqual(result["gold_gained"], 60185 * count)
            self.assertIsNone(result["next_upgrade_cost"])
        response, current = await self.request("GET", f"/api/players/{player_id}")
        self.assertEqual(response["status"], 200)
        self.assertEqual((current["level"], current["score"], current["total_clicks"]), (40, cost + 100 + 60185 * 4, 4))
        self.assertIsNone(current["next_gold_per_click"])
        self.assertIsNone(current["next_upgrade_cost"])
        self.assertEqual(await self.redis.zscore(self.repository.LEADERBOARD_KEY, player_id), current["score"])
        response, restored = await self.request("POST", "/api/players", {"nickname": "restore"}, token)
        self.assertEqual(restored["player"], current)
        at_max = await self.connect(token)
        self.assertEqual((await self.next_message(at_max, "connected"))["player"], current)


if __name__ == "__main__":
    unittest.main()
