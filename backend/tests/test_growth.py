"""Run: .venv/Scripts/python.exe -B -m unittest discover -s tests -v

Uses REDIS_TEST_URL (default localhost:6379/15), unique keys, and cleans up
only its own fixtures. No flush, production refill scan, or server startup.
"""

import asyncio
import json
import os
import unittest
from http.cookies import SimpleCookie
from types import SimpleNamespace
from unittest.mock import patch
from uuid import uuid4

from fastapi import FastAPI
from redis.asyncio import Redis

from app.balance import GOLD_PER_CLICK, InvalidPlayerLevel, balance_for_level
from app.api import http, websocket
from app.repositories.repository import RedisRepository
from app.services.connection_manager import ConnectionManager


class BalanceTests(unittest.TestCase):
    def test_reference_values(self):
        for level, gold, cost in (
            (1, 10, 100), (2, 12, 160), (5, 24, 656),
            (10, 74, 6872), (15, 227, 72058), (30, 6462, None),
        ):
            with self.subTest(level=level):
                self.assertEqual(balance_for_level(level), {
                    "gold_per_click": gold, "next_upgrade_cost": cost,
                })


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

    async def test_all_levels_share_response_and_lua_values(self):
        for level in range(1, 31):
            with self.subTest(level=level):
                player_id = await self.seed(level, score=1_000_000_000)
                balance = balance_for_level(level)
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
                if level == 30:
                    self.assertEqual(upgrade["code"], "MAX_LEVEL_REACHED")
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
        for level in (-1, 0, 31, "1.5", "invalid"):
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
        player_id = await self.seed(score=1_000_000)
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
                self.assertEqual(result["gold_gained"], GOLD_PER_CLICK[result["level"] - 1] * 3)
                gained += result["gold_gained"]
            else:
                self.assertEqual(result["gold_spent"], balance_for_level(result["level"] - 1)["next_upgrade_cost"])
                spent += result["gold_spent"]
        player = await self.repository.get_player(player_id)
        self.assertEqual((player["level"], player["total_clicks"]), (7, 18))
        self.assertEqual(player["score"], 1_000_000 + gained - spent)
        self.assertEqual(await self.redis.zscore(self.repository.LEADERBOARD_KEY, player_id), player["score"])

    async def test_http_and_two_websockets_use_authoritative_growth(self):
        response, body = await self.request("POST", "/api/players", {"nickname": "fixture"})
        player_id = body["player"]["player_id"]
        cookie = SimpleCookie()
        cookie.load(next(value.decode() for key, value in response["headers"] if key == b"set-cookie"))
        token = cookie["session_token"].value
        self.keys.update((f"player:{player_id}", f"rate_limit:click:{player_id}", f"session:{token}"))
        self.assertEqual(response["status"], 201)
        self.assertEqual(body["player"]["gold_per_click"], 10)
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
        await self.send(first, {"type": "click"})
        clicked = await self.next_message(first, "click_result")
        self.assertEqual((clicked["score"], clicked["gold_gained"], clicked["gold_per_click"]), (12, 12, 12))
        self.assertEqual(clicked["next_upgrade_cost"], 160)
        response, current = await self.request("GET", f"/api/players/{player_id}")
        self.assertEqual(response["status"], 200)
        self.assertEqual((current["level"], current["gold_per_click"], current["next_upgrade_cost"]), (2, 12, 160))
        # Boundary fixture only: production state is never migrated/reset.
        await self.redis.hset(f"player:{player_id}", "level", 30)
        before = await self.snapshot(player_id)
        await self.send(second, {"type": "levelup"})
        maximum = await self.next_message(second, "levelup_result")
        self.assertEqual(maximum["code"], "MAX_LEVEL_REACHED")
        self.assertIsNone(maximum["next_upgrade_cost"])
        self.assertEqual(await self.snapshot(player_id), before)
        await self.send(first, {"type": "click"})
        maximum_click = await self.next_message(first, "click_result")
        self.assertEqual(maximum_click["gold_gained"], 6462)
        self.assertIsNone(maximum_click["next_upgrade_cost"])


if __name__ == "__main__":
    unittest.main()
