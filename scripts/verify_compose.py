"""Exercise only the selected deployment through Nginx, using new test players.

Run with backend's Python environment (requirements.txt includes websockets):
  python scripts/verify_compose.py --url http://localhost
Recreate the Compose services, then verify persisted state (renews test session TTLs):
  python scripts/verify_compose.py --url http://localhost --check-persistence
Session cookies are stored in the gitignored .deployment-test directory.
"""

import argparse
import asyncio
from http.cookiejar import CookieJar
import json
from pathlib import Path
import ssl
from urllib.error import HTTPError
from urllib.parse import urlsplit, urlunsplit
from urllib.request import HTTPRedirectHandler, HTTPCookieProcessor, HTTPSHandler, Request, build_opener
from uuid import uuid4

from websockets.asyncio.client import connect


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        # A local HTTP test must never follow the production HTTPS redirect
        # and accidentally create players in a different deployment.
        return None


class Browser:
    def __init__(self, base, tls_context=None):
        self.base = base.rstrip("/")
        self.cookies = CookieJar()
        self.opener = build_opener(
            HTTPCookieProcessor(self.cookies), HTTPSHandler(context=tls_context), NoRedirect(),
        )

    def request(self, path, body=None):
        data = json.dumps(body).encode() if body is not None else None
        with self.opener.open(Request(
            self.base + path, data=data,
            headers={"Content-Type": "application/json"} if data else {},
        ), timeout=10) as response:
            raw = response.read()
            return response.status, response.headers, raw

    def json(self, path, body=None):
        status, headers, raw = self.request(path, body)
        return status, headers, json.loads(raw)

    def cookie_header(self):
        request = Request(self.base + "/ws")
        self.cookies.add_cookie_header(request)
        return request.get_header("Cookie")


async def receive(ws, kind, predicate=lambda _: True):
    async with asyncio.timeout(10):
        while True:
            message = json.loads(await ws.recv())
            if message["type"] == kind and predicate(message):
                return message


async def action(ws, kind, **kwargs):
    await ws.send(json.dumps({"type": kind, **kwargs}))
    return await receive(ws, kind + "_result")


async def exercise(base, state_path, tls_context=None):
    first, second = Browser(base, tls_context), Browser(base, tls_context)
    assert first.json("/api/health")[2] == {"status": "ok"}
    status, headers, index = first.request("/")
    assert status == 200 and "text/html" in headers["Content-Type"]
    assert first.request("/some/spa/route")[2] == index
    for path in ("/api/does-not-exist", "/api"):
        try:
            first.request(path)
            raise AssertionError(f"{path} unexpectedly succeeded")
        except HTTPError as error:
            assert error.code == 404
            assert "application/json" in error.headers["Content-Type"]
    print("PASS: Nginx frontend, SPA fallback, API health and API 404 isolation")

    suffix = uuid4().hex[:8]
    status, headers, created = first.json("/api/players", {"nickname": f"docker-{suffix}"})
    assert status == 201 and created["created"]
    cookie = headers["Set-Cookie"]
    assert "HttpOnly" in cookie and "SameSite=lax" in cookie and "Path=/" in cookie
    secure = urlsplit(base).scheme == "https"
    assert next(iter(first.cookies)).secure == secure, "Cookie Secure must match HTTP/HTTPS mode"
    player_id = created["player"]["player_id"]
    status, _, restored = first.json("/api/players", {"nickname": "restore"})
    assert status == 200 and not restored["created"] and restored["player"] == created["player"]
    other = second.json("/api/players", {"nickname": f"observer-{suffix}"})[2]
    other_id = other["player"]["player_id"]
    print("PASS: player creation, proxied HttpOnly cookie and session restoration")

    parts = urlsplit(base)
    ws_url = urlunsplit(("wss" if parts.scheme == "https" else "ws", parts.netloc, "/ws", "", ""))
    tls_options = {"ssl": tls_context} if secure and tls_context else {}
    async with connect(ws_url, additional_headers={"Cookie": first.cookie_header()}, **tls_options) as a:
        assert (await receive(a, "connected"))["player"]["player_id"] == player_id
        async with connect(ws_url, additional_headers={"Cookie": second.cookie_header()}, **tls_options) as b:
            await receive(b, "connected")

            def both(message):
                return {player_id, other_id} <= {p["player_id"] for p in message["players"]}

            await receive(a, "leaderboard", both)
            await receive(b, "leaderboard", both)
            batch = await action(a, "click_batch", count=10)
            assert batch["success"] and batch["score"] == 100 and batch["accepted_clicks"] == 10
            clicked = await action(a, "click")
            assert clicked["success"] and clicked["score"] == 110 and clicked["total_clicks"] == 11
            upgraded = await action(a, "levelup")
            assert upgraded["success"] and upgraded["level"] == 2 and upgraded["score"] == 10
            assert upgraded["gold_per_click"] == 12

            def score_is(who, score):
                return lambda message: any(p["player_id"] == who and p["score"] == score for p in message["players"])

            await receive(b, "leaderboard", score_is(player_id, 10))
            observer_click = await action(b, "click")
            assert observer_click["success"] and observer_click["score"] == 10
            await receive(a, "leaderboard", score_is(other_id, 10))
            print("PASS: WebSocket upgrade, single click, batch, levelup and broadcasts in both directions")

            for _ in range(100):
                limited = await action(a, "click")
                if not limited["success"]:
                    assert limited["code"] == "RATE_LIMITED" and limited["remaining_tokens"] == 0
                    break
            else:
                raise AssertionError("Token bucket was never exhausted")
            before = first.json(f"/api/players/{player_id}")[2]
            rejected = await action(a, "click_batch", count=10)
            assert not rejected["success"] and rejected["code"] == "RATE_LIMITED"
            assert first.json(f"/api/players/{player_id}")[2] == before
            await asyncio.sleep(1.1)
            refilled = await action(a, "click")
            assert refilled["success"] and refilled["score"] == before["score"] + 12
            print("PASS: exhausted token rejection, atomic batch rejection and background refill")

    snapshots = []
    for browser, who in ((first, player_id), (second, other_id)):
        snapshots.append({"cookie": browser.cookie_header(), "player": browser.json(f"/api/players/{who}")[2]})
    state_path.parent.mkdir(parents=True, exist_ok=True)
    state_path.write_text(json.dumps({"base": base, "snapshots": snapshots}), encoding="utf-8")
    print(f"Saved persistence expectations: {state_path} (contains test session cookies)")


def check_persistence(base, state_path, tls_context=None):
    state = json.loads(state_path.read_text(encoding="utf-8"))
    assert state["base"] == base, "Select the same deployment used for exercise"
    browser = Browser(base, tls_context)
    assert browser.json("/api/health")[2] == {"status": "ok"}
    for snapshot in state["snapshots"]:
        player = snapshot["player"]
        assert browser.json(f"/api/players/{player['player_id']}")[2] == player
        # Also verifies that the session mapping survived; only its TTL is renewed.
        request = Request(base.rstrip("/") + "/api/players", data=b'{"nickname":"restore"}', headers={
            "Content-Type": "application/json", "Cookie": snapshot["cookie"],
        })
        with browser.opener.open(request, timeout=10) as response:
            restored = json.load(response)
            assert response.status == 200 and not restored["created"] and restored["player"] == player
    print("PASS: player state and cookie sessions survived Compose container recreation")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", default="http://localhost")
    parser.add_argument("--state", type=Path, default=Path(".deployment-test/state.json"))
    parser.add_argument("--check-persistence", action="store_true")
    parser.add_argument("--ca-file", type=Path, help="Additional trusted CA for local TLS tests; verification stays enabled")
    args = parser.parse_args()
    tls_context = None
    if args.ca_file:
        tls_context = ssl.create_default_context()
        tls_context.load_verify_locations(cafile=str(args.ca_file))
    if args.check_persistence:
        check_persistence(args.url, args.state, tls_context)
    else:
        asyncio.run(exercise(args.url, args.state, tls_context))
