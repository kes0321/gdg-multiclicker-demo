"""Server-rendered admin console. Every data endpoint requires its own session."""

import asyncio
import hashlib
import hmac
import json
import logging
from pathlib import Path
import re
import secrets

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from pydantic import BaseModel, ConfigDict, Field
from redis.exceptions import RedisError
from starlette.concurrency import run_in_threadpool

from app.admin.security import AdminSettings, verify_password
from app.api.websocket import broadcast_leaderboard
from app.balance import MAX_LEVEL, MAX_SAFE_INTEGER

logger = logging.getLogger("uvicorn.error")
STATIC = Path(__file__).parent / "static"


class Login(BaseModel):
    model_config = ConfigDict(extra="forbid")
    password: str = Field(min_length=1, max_length=128)


class PlayerEdit(BaseModel):
    model_config = ConfigDict(extra="forbid")
    score: int = Field(strict=True, ge=0, le=MAX_SAFE_INTEGER)
    level: int = Field(strict=True, ge=1, le=MAX_LEVEL)
    expected_score: int = Field(strict=True, ge=-MAX_SAFE_INTEGER, le=MAX_SAFE_INTEGER)
    expected_level: int = Field(strict=True, ge=-MAX_SAFE_INTEGER, le=MAX_SAFE_INTEGER)


def create_admin_router(settings: AdminSettings) -> APIRouter:
    router = APIRouter(prefix=settings.path, include_in_schema=False)
    if not settings.enabled:
        return router
    namespace = f"admin:{settings.fingerprint}:"
    password_slots = asyncio.Semaphore(2)

    def protect(response: Response) -> Response:
        response.headers.update({
            "Cache-Control": "no-store",
            "X-Content-Type-Options": "nosniff",
            "X-Frame-Options": "DENY",
            "Referrer-Policy": "no-referrer",
            "Content-Security-Policy": "default-src 'none'; script-src 'self'; style-src 'self'; connect-src 'self'; base-uri 'none'; form-action 'self'; frame-ancestors 'none'",
        })
        return response

    def check_origin(request: Request):
        if request.headers.get("origin") != str(request.base_url).rstrip("/"):
            raise HTTPException(status_code=403, detail="요청 출처가 올바르지 않습니다.")

    def session_key(token: str) -> str:
        return namespace + "session:" + hashlib.sha256(token.encode()).hexdigest()

    async def require_admin(request: Request):
        token = request.cookies.get("admin_session", "")
        if not token or len(token) > 128:
            raise HTTPException(status_code=401, detail="관리자 로그인이 필요합니다.")
        try:
            raw = await request.app.state.repository.redis.get(session_key(token))
        except RedisError as exc:
            raise HTTPException(status_code=503, detail="관리자 인증 저장소에 연결할 수 없습니다.") from exc
        if not raw:
            raise HTTPException(status_code=401, detail="관리자 세션이 만료되었습니다.")
        try:
            session = json.loads(raw)
            if session["role"] != "superadmin" or not isinstance(session["csrf"], str) or not 32 <= len(session["csrf"]) <= 128:
                raise ValueError("Invalid session")
        except (ValueError, KeyError, TypeError) as exc:
            raise HTTPException(status_code=401, detail="관리자 세션이 유효하지 않습니다.") from exc
        if request.method not in {"GET", "HEAD"}:
            check_origin(request)
            if not hmac.compare_digest(request.headers.get("x-csrf-token", "").encode(), session["csrf"].encode()):
                raise HTTPException(status_code=403, detail="CSRF 검증에 실패했습니다.")
        return session

    @router.get("")
    @router.get("/")
    async def page():
        html = (STATIC / "index.html").read_text(encoding="utf-8").replace("__ADMIN_PATH__", settings.path)
        return protect(HTMLResponse(html))

    @router.get("/assets/{asset}")
    async def asset(asset: str):
        if asset not in {"admin.js", "admin.css"}:
            raise HTTPException(status_code=404)
        return protect(FileResponse(STATIC / asset))

    @router.post("/login")
    async def login(body: Login, request: Request):
        check_origin(request)
        redis = request.app.state.repository.redis
        ip = request.client.host if request.client else "unknown"
        limit_key = namespace + "login:" + hashlib.sha256(ip.encode()).hexdigest()
        try:
            # Reserve an attempt atomically before the expensive password check.
            attempts = await redis.eval(
                "local n=redis.call('INCR',KEYS[1]); if n==1 then redis.call('EXPIRE',KEYS[1],300) end; return n",
                1, limit_key,
            )
            if attempts > 5:
                raise HTTPException(status_code=429, detail="로그인 시도가 많습니다. 5분 후 다시 시도해 주세요.", headers={"Retry-After": "300"})
            async with password_slots:
                valid = await run_in_threadpool(verify_password, body.password, settings.password_hash)
            if not valid:
                logger.warning("Admin login rejected")
                raise HTTPException(status_code=401, detail="비밀번호가 올바르지 않습니다.")
            token, csrf = secrets.token_urlsafe(32), secrets.token_urlsafe(32)
            # A fresh login revokes this browser's previous admin token.
            previous = request.cookies.get("admin_session")
            async with redis.pipeline(transaction=True) as pipe:
                if previous and len(previous) <= 128:
                    pipe.delete(session_key(previous))
                pipe.set(session_key(token), json.dumps({"role": "superadmin", "csrf": csrf}), ex=settings.ttl)
                pipe.delete(limit_key)
                await pipe.execute()
        except RedisError as exc:
            raise HTTPException(status_code=503, detail="관리자 로그인 처리 실패") from exc
        response = protect(JSONResponse({"role": "superadmin", "csrf_token": csrf}))
        response.set_cookie("admin_session", token, max_age=settings.ttl, httponly=True,
                            secure=settings.secure_cookie, samesite="strict", path=settings.path)
        return response

    @router.get("/session")
    async def session(session=Depends(require_admin)):
        return protect(JSONResponse({"role": "superadmin", "csrf_token": session["csrf"]}))

    @router.post("/logout")
    async def logout(request: Request, session=Depends(require_admin)):
        try:
            await request.app.state.repository.redis.delete(session_key(request.cookies["admin_session"]))
        except RedisError as exc:
            raise HTTPException(status_code=503, detail="로그아웃 처리 실패") from exc
        response = protect(JSONResponse({"ok": True}))
        response.delete_cookie("admin_session", path=settings.path, secure=settings.secure_cookie,
                               httponly=True, samesite="strict")
        return response

    @router.get("/users")
    async def users(request: Request, cursor: int = Query(default=0, ge=0, le=2**64 - 1),
                    count: int = Query(default=50, ge=1, le=100), session=Depends(require_admin)):
        try:
            result = await request.app.state.repository.admin_list_players(cursor, count)
        except RedisError as exc:
            raise HTTPException(status_code=503, detail="유저 조회 실패") from exc
        return protect(JSONResponse(result))

    @router.patch("/users/{player_id}")
    async def edit(player_id: str, body: PlayerEdit, request: Request, session=Depends(require_admin)):
        if not re.fullmatch(r"[A-Za-z0-9_-]{1,80}", player_id):
            raise HTTPException(status_code=422, detail="잘못된 플레이어 ID입니다.")
        repository = request.app.state.repository
        try:
            code = await repository.admin_edit_player(player_id, body.score, body.level, body.expected_score, body.expected_level)
            if code == "PLAYER_NOT_FOUND":
                raise HTTPException(status_code=404, detail="플레이어가 없습니다.")
            if code == "STATE_CHANGED":
                raise HTTPException(status_code=409, detail="유저 상태가 변경되었습니다. 목록을 새로고침한 뒤 다시 저장해 주세요.")
            player = await repository.get_player(player_id)
        except RedisError as exc:
            raise HTTPException(status_code=503, detail="유저 수정 실패") from exc
        logger.info("superadmin updated player_id=%s score=%s->%s level=%s->%s",
                    player_id, body.expected_score, body.score, body.expected_level, body.level)
        await broadcast_leaderboard(repository)
        return protect(JSONResponse({"player": player}))

    return router
