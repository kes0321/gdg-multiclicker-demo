"""Admin boundary tests against unique keys in REDIS_TEST_URL (DB 15 by default)."""

import hashlib
import json
import os
from http.cookies import SimpleCookie
from unittest import IsolatedAsyncioTestCase, TestCase
from unittest.mock import AsyncMock, patch
from uuid import uuid4

from fastapi import FastAPI
from redis.asyncio import Redis
from redis.exceptions import RedisError

from app.admin import router as admin_router
from app.admin.router import create_admin_router
from app.admin.security import AdminSettings, hash_password, verify_password
from app.balance import MAX_SAFE_INTEGER
from app.repositories.repository import RedisRepository
from app.services.connection_manager import ConnectionManager

PASSWORD = "Test-only-password-2026!"
PASSWORD_HASH = hash_password(PASSWORD)


class SettingsTests(TestCase):
    def test_hash_and_fail_closed_settings(self):
        self.assertNotIn(PASSWORD, PASSWORD_HASH)
        self.assertTrue(verify_password(PASSWORD, PASSWORD_HASH))
        self.assertFalse(verify_password("wrong", PASSWORD_HASH))
        self.assertFalse(AdminSettings().enabled)
        self.assertFalse(AdminSettings(path="/control-test").enabled)
        self.assertFalse(AdminSettings(password_hash=PASSWORD_HASH).enabled)
        for kwargs in ({"path": "/api"}, {"path": "/abc/defgh"}, {"path": "/abc;injection"},
                       {"password_hash": "plaintext"}, {"ttl": 0}):
            with self.assertRaises(ValueError):
                AdminSettings(**kwargs)


class AdminTests(IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.redis = Redis.from_url(os.environ.get("REDIS_TEST_URL", "redis://localhost:6379/15"), decode_responses=True)
        await self.redis.ping()
        self.settings = AdminSettings("/control-" + uuid4().hex, PASSWORD_HASH)
        self.namespace = "admin:" + self.settings.fingerprint + ":"
        self.repository = RedisRepository(self.redis)
        self.repository.LEADERBOARD_KEY = self.settings.path + ":leaderboard"
        self.keys = {self.repository.LEADERBOARD_KEY}
        self.app = FastAPI()
        self.app.state.repository = self.repository
        self.app.include_router(create_admin_router(self.settings))
        self.player_id = "admin-test-" + uuid4().hex
        self.keys.add("player:" + self.player_id)
        await self.redis.hset("player:" + self.player_id, mapping={
            "nickname": "<script>demo</script>", "score": 100, "level": 3,
            "total_clicks": 10, "created_at_ms": 123,
        })
        await self.redis.zadd(self.repository.LEADERBOARD_KEY, {self.player_id: 100})
        self.cookie = ""
        self.csrf = ""

    async def asyncTearDown(self):
        self.keys.update([key async for key in self.redis.scan_iter(match=self.namespace + "*")])
        await self.redis.delete(*self.keys)
        await self.redis.aclose()

    async def request(self, method="GET", path="/session", body=None, *, cookie=None,
                      csrf=None, origin="http://test", app=None, extra_headers=()):
        raw_path, _, query = path.partition("?")
        headers = [(b"host", b"test"), (b"content-type", b"application/json")]
        if origin is not None:
            headers.append((b"origin", origin.encode()))
        value = self.cookie if cookie is None else cookie
        if value:
            headers.append((b"cookie", value.encode()))
        token = self.csrf if csrf is None else csrf
        if token:
            headers.append((b"x-csrf-token", token.encode()))
        headers.extend(extra_headers)
        messages = []
        async def receive():
            return {"type": "http.request", "body": json.dumps(body).encode() if body is not None else b"", "more_body": False}
        async def send(message):
            messages.append(message)
        await (app or self.app)({
            "asgi": {"version": "3.0", "spec_version": "2.4"},
            "type": "http", "method": method, "path": self.settings.path + raw_path,
            "root_path": "", "query_string": query.encode(), "headers": headers,
            "scheme": "http", "server": ("test", 80), "client": ("127.0.0.1", 1), "http_version": "1.1",
        }, receive, send)
        raw = b"".join(message.get("body", b"") for message in messages[1:])
        content_type = dict(messages[0]["headers"]).get(b"content-type", b"")
        data = json.loads(raw) if b"application/json" in content_type else raw.decode()
        return messages[0], data

    async def login(self):
        response, body = await self.request("POST", "/login", {"password": PASSWORD})
        self.assertEqual(response["status"], 200)
        cookie = SimpleCookie()
        cookie.load(dict(response["headers"])[b"set-cookie"].decode())
        self.cookie = "admin_session=" + cookie["admin_session"].value
        self.csrf = body["csrf_token"]
        return response, body

    async def test_ordinary_users_cannot_read_edit_or_logout(self):
        for cookie in ("", "session_token=ordinary", "admin_session=forged"):
            for method, path, body in (("GET", "/users", None), ("GET", "/session", None),
                                       ("PATCH", "/users/" + self.player_id, self.edit()),
                                       ("DELETE", "/users/" + self.player_id, {"confirm_player_id": self.player_id}),
                                       ("POST", "/logout", None)):
                response, _ = await self.request(method, path, body, cookie=cookie)
                self.assertEqual(response["status"], 401)
        self.assertEqual(await self.redis.hget("player:" + self.player_id, "score"), "100")

    def edit(self, **changes):
        return {"score": 500, "level": 5, "expected_score": 100, "expected_level": 3, **changes}

    async def test_no_configuration_no_routes_or_openapi_disclosure(self):
        app = FastAPI()
        app.include_router(create_admin_router(AdminSettings(path=self.settings.path)))
        response, _ = await self.request(path="", app=app)
        self.assertEqual(response["status"], 404)
        self.assertEqual(self.app.openapi()["paths"], {})
        response, html = await self.request(path="")
        self.assertEqual(response["status"], 200)
        self.assertNotIn(PASSWORD_HASH, html)
        self.assertNotIn(PASSWORD, html)
        self.assertIn((b"cache-control", b"no-store"), response["headers"])
        for asset in ("admin.js", "admin.css"):
            response, text = await self.request(path="/assets/" + asset)
            self.assertEqual(response["status"], 200)
            self.assertNotIn(PASSWORD_HASH, text)
            self.assertNotIn("innerHTML", text)
        self.assertEqual((await self.request(path="/assets/security.py"))[0]["status"], 404)

    async def test_login_throttling_and_origin_checks(self):
        for origin in (None, "https://evil.example"):
            response, _ = await self.request("POST", "/login", {"password": PASSWORD}, origin=origin)
            self.assertEqual(response["status"], 403)
        for attempt in range(6):
            response, _ = await self.request("POST", "/login", {"password": "wrong"},
                                             extra_headers=[(b"x-forwarded-for", f"1.2.3.{attempt}".encode())])
            self.assertEqual(response["status"], 401 if attempt < 5 else 429)
        response, _ = await self.request("POST", "/login", {"password": PASSWORD})
        self.assertEqual(response["status"], 429)

    async def test_session_cookie_logout_expiry_and_password_rotation(self):
        response, body = await self.login()
        header = dict(response["headers"])[b"set-cookie"].decode()
        for value in ("HttpOnly", "SameSite=strict", "Max-Age=1800", "Path=" + self.settings.path):
            self.assertIn(value, header)
        self.assertEqual(body["role"], "superadmin")
        self.assertEqual((await self.request())[0]["status"], 200)
        session_key = self.namespace + "session:" + hashlib.sha256(self.cookie.split("=", 1)[1].encode()).hexdigest()
        for malformed in ({"role": "superadmin", "csrf": ""}, {"role": "player", "csrf": self.csrf}, []):
            await self.redis.set(session_key, json.dumps(malformed), ex=60)
            self.assertEqual((await self.request())[0]["status"], 401)
        await self.redis.set(session_key, json.dumps({"role": "superadmin", "csrf": self.csrf}), ex=60)
        other = FastAPI()
        other.state.repository = self.repository
        other.include_router(create_admin_router(AdminSettings(self.settings.path, hash_password("Another-test-password!"))))
        self.assertEqual((await self.request(app=other))[0]["status"], 401)
        secure = FastAPI()
        secure.state.repository = self.repository
        secure.include_router(create_admin_router(AdminSettings(self.settings.path, PASSWORD_HASH, secure_cookie=True)))
        secured, _ = await self.request("POST", "/login", {"password": PASSWORD}, app=secure)
        self.assertIn("Secure", dict(secured["headers"])[b"set-cookie"].decode())
        await self.login()
        keys = [key async for key in self.redis.scan_iter(match=self.namespace + "session:*")]
        for key in keys:
            await self.redis.expire(key, 0)
        self.assertEqual((await self.request())[0]["status"], 401)
        await self.login()
        old_cookie = self.cookie
        self.assertEqual((await self.request("POST", "/logout"))[0]["status"], 200)
        self.assertEqual((await self.request(cookie=old_cookie))[0]["status"], 401)

    async def test_all_players_are_paginated_and_edits_are_atomic(self):
        await self.login()
        offline = self.player_id + "-offline"
        self.keys.add("player:" + offline)
        await self.redis.hset("player:" + offline, mapping={"nickname": "offline", "score": 0, "level": 1})
        cursor = "0"
        found = set()
        while True:
            response, body = await self.request(path="/users?count=1&cursor=" + cursor)
            self.assertEqual(response["status"], 200)
            found.update(player["player_id"] for player in body["players"])
            cursor = body["next_cursor"]
            self.assertIsInstance(cursor, str)
            if cursor == "0": break
        self.assertTrue({self.player_id, offline} <= found)
        before = await self.redis.hgetall("player:" + self.player_id)
        for args in ({"csrf": ""}, {"csrf": "forged"}, {"origin": "http://evil.example"}):
            response, _ = await self.request("PATCH", "/users/" + self.player_id, self.edit(), **args)
            self.assertEqual(response["status"], 403)
        for changes in ({"level": 41}, {"level": True}, {"score": -1}, {"score": "500"},
                        {"score": MAX_SAFE_INTEGER + 1}, {"is_admin": True}):
            response, _ = await self.request("PATCH", "/users/" + self.player_id, self.edit(**changes))
            self.assertEqual(response["status"], 422)
        self.assertEqual(await self.redis.hgetall("player:" + self.player_id), before)
        response, body = await self.request("PATCH", "/users/" + self.player_id, self.edit())
        self.assertEqual(response["status"], 200)
        self.assertEqual((body["player"]["score"], body["player"]["level"], body["player"]["gold_per_click"]), (500, 5, 24))
        self.assertEqual(await self.redis.zscore(self.repository.LEADERBOARD_KEY, self.player_id), 500)
        after = await self.redis.hgetall("player:" + self.player_id)
        self.assertEqual(after, {**before, "score": "500", "level": "5"})
        self.assertEqual((await self.request("PATCH", "/users/" + self.player_id, self.edit()))[0]["status"], 409)
        self.assertEqual((await self.request("PATCH", "/users/missing", self.edit()))[0]["status"], 404)

    async def test_game_click_conflicts_and_redis_failure_deny_safely(self):
        await self.login()
        self.keys.add("rate_limit:click:" + self.player_id)
        await self.repository.click(self.player_id)
        response, _ = await self.request("PATCH", "/users/" + self.player_id, self.edit())
        self.assertEqual(response["status"], 409)
        self.assertEqual(await self.redis.hget("player:" + self.player_id, "score"), "115")
        await self.redis.set(self.repository.LEADERBOARD_KEY, "invalid-type")
        response, _ = await self.request("PATCH", "/users/" + self.player_id, self.edit(expected_score=115))
        self.assertEqual(response["status"], 503)
        self.assertEqual(await self.redis.hget("player:" + self.player_id, "score"), "115")
        with patch.object(self.redis, "get", new=AsyncMock(side_effect=RedisError("offline"))):
            self.assertEqual((await self.request(path="/users"))[0]["status"], 503)

    async def test_delete_requires_confirmation_csrf_and_origin(self):
        await self.login()
        path = "/users/" + self.player_id
        body = {"confirm_player_id": self.player_id}
        before = await self.redis.hgetall("player:" + self.player_id)
        for options in ({"csrf": ""}, {"csrf": "forged"}, {"origin": None}, {"origin": "https://evil.example"}):
            self.assertEqual((await self.request("DELETE", path, body, **options))[0]["status"], 403)
        for invalid in (None, {}, {"confirm_player_id": "another-player"}, {**body, "extra": True}):
            self.assertEqual((await self.request("DELETE", path, invalid))[0]["status"], 422)
        self.assertEqual(await self.redis.hgetall("player:" + self.player_id), before)
        self.assertEqual(await self.redis.zscore(self.repository.LEADERBOARD_KEY, self.player_id), 100)

    async def test_delete_removes_all_player_state_sessions_and_only_target_connections(self):
        await self.login()
        own_sessions = ["session:" + self.player_id + suffix for suffix in ("-a", "-b")]
        other_session = "session:" + self.player_id + "-other"
        bucket = "rate_limit:click:" + self.player_id
        self.keys.update([*own_sessions, other_session, bucket])
        for key in own_sessions:
            await self.redis.set(key, self.player_id, ex=120)
        await self.redis.set(other_session, "another-player", ex=120)
        await self.redis.hset(bucket, mapping={"tokens": 7})
        await self.redis.zadd(self.repository.LEADERBOARD_KEY, {"another-player": 999})
        manager = ConnectionManager()
        sockets = [AsyncMock() for _ in range(3)]
        for socket, player_id in zip(sockets, (self.player_id, self.player_id, "another-player")):
            manager.add(socket, player_id)
        with patch.object(admin_router, "manager", manager), patch.object(admin_router, "broadcast_leaderboard", new=AsyncMock()) as broadcast:
            response, body = await self.request("DELETE", "/users/" + self.player_id, {"confirm_player_id": self.player_id})
            self.assertEqual((response["status"], body), (200, {"ok": True, "player_id": self.player_id}))
            broadcast.assert_awaited_once_with(self.repository)
        self.assertIn((b"cache-control", b"no-store"), response["headers"])
        self.assertEqual(await self.redis.exists("player:" + self.player_id, bucket, *own_sessions), 0)
        self.assertIsNone(await self.redis.zscore(self.repository.LEADERBOARD_KEY, self.player_id))
        self.assertEqual(await self.redis.zscore(self.repository.LEADERBOARD_KEY, "another-player"), 999)
        self.assertEqual(await self.redis.get(other_session), "another-player")
        self.assertEqual((await self.request())[0]["status"], 200)
        self.assertEqual(list(manager.connections), [sockets[2]])
        for socket in sockets[:2]:
            socket.close.assert_awaited_once_with(code=1008, reason="PLAYER_DELETED")
        sockets[2].close.assert_not_awaited()
        self.assertEqual((await self.request("DELETE", "/users/" + self.player_id, {"confirm_player_id": self.player_id}))[0]["status"], 404)

    async def test_delete_validates_before_writes_and_rechecks_session_ownership(self):
        await self.login()
        session = "session:" + self.player_id
        bucket = "rate_limit:click:" + self.player_id
        self.keys.update([session, bucket])
        await self.redis.set(session, self.player_id)
        await self.redis.hset(bucket, mapping={"tokens": 7})
        before = await self.redis.hgetall("player:" + self.player_id)
        await self.redis.set(self.repository.LEADERBOARD_KEY, "wrong-type")
        response, _ = await self.request("DELETE", "/users/" + self.player_id, {"confirm_player_id": self.player_id})
        self.assertEqual(response["status"], 503)
        self.assertEqual(await self.redis.hgetall("player:" + self.player_id), before)
        self.assertEqual(await self.redis.hget(bucket, "tokens"), "7")
        self.assertEqual(await self.redis.get(session), self.player_id)
        await self.redis.delete(self.repository.LEADERBOARD_KEY)
        await self.redis.zadd(self.repository.LEADERBOARD_KEY, {self.player_id: 100})
        original_script = self.repository._admin_delete_script
        async def interleaved(*, keys, args):
            self.assertIn(session, keys)
            await self.redis.set(session, "another-player")
            await self.repository.click(self.player_id)
            return await original_script(keys=keys, args=args)
        with patch.object(self.repository, "_admin_delete_script", interleaved):
            self.assertTrue(await self.repository.admin_delete_player(self.player_id))
        self.assertEqual(await self.redis.get(session), "another-player")
        for operation in (self.repository.click, self.repository.levelup):
            self.assertEqual(await operation(self.player_id), {"success": False, "code": "PLAYER_NOT_FOUND"})
        self.assertEqual(await self.redis.exists("player:" + self.player_id, bucket), 0)
