# backend/app/api/http.py

from fastapi import APIRouter, HTTPException, Query, Request, Response
from redis.asyncio import Redis
from redis.exceptions import RedisError
from pydantic import BaseModel, Field, field_validator

from app.repositories.repository import RedisRepository


class CreatePlayerRequest(BaseModel):
    nickname: str = Field(min_length=1, max_length=20)

    @field_validator("nickname", mode="before")
    @classmethod
    def strip_nickname(cls, value):
        return value.strip() if isinstance(value, str) else value

router = APIRouter(prefix="/api", tags=["HTTP"])

LEADERBOARD_KEY = "leaderboard"


@router.get("/health")
async def health(request: Request):
    repository = request.app.state.repository

    try:
        await repository.ping()
    except RedisError as exc:
        raise HTTPException(
            status_code=503,
            detail="Redis 연결 실패",
        ) from exc

    return {"status": "ok"}


@router.get("/players/{player_id}")
async def get_player(player_id: str, request: Request):
    repository = request.app.state.repository

    try:
        player = await repository.get_player(player_id)
    except RedisError as exc:
        raise HTTPException(
            status_code=503,
            detail="플레이어 조회 실패",
        ) from exc

    if player is None:
        raise HTTPException(
            status_code=404,
            detail="플레이어가 없습니다.",
        )

    return player


@router.get("/leaderboard")
async def get_leaderboard(
    request: Request,
    limit: int = Query(default=10, ge=1, le=100),
):
    repository = request.app.state.repository

    try:
        players = await repository.get_leaderboard(limit)
    except RedisError as exc:
        raise HTTPException(
            status_code=503,
            detail="랭킹 조회 실패",
        ) from exc

    return {"players": players}


@router.post("/players")
async def create_player(
    body: CreatePlayerRequest,
    request: Request,
    response: Response,
):
    repository = request.app.state.repository
    token = request.cookies.get("session_token")

    try:
        # 1. 기존 세션이 있으면 해당 플레이어 조회
        if token:
            player_id = await repository.get_session_player_id(token)

            if player_id:
                player = await repository.get_player(player_id)

                if player is not None:
                    # 정상 재접속 시 세션 유효기간 연장
                    renewed = await repository.redis.expire(
                        f"session:{token}",
                        repository.SESSION_TTL_SECONDS,
                    )

                    if renewed:
                        response.set_cookie(
                            key="session_token",
                            value=token,
                            max_age=repository.SESSION_TTL_SECONDS,
                            httponly=True,
                            samesite="lax",
                            secure=False,  # 로컬 HTTP용. HTTPS 배포 시 True
                            path="/",
                        )

                        return {"created": False, "player": player}

        # 2. 유효한 세션이 없으면 Lua로 신규 생성
        player_id, token = await repository.create_player(body.nickname)
        player = await repository.get_player(player_id)

        if player is None:
            raise HTTPException(
                status_code=500,
                detail="생성된 플레이어를 조회할 수 없습니다.",
            )

    except RedisError as exc:
        raise HTTPException(
            status_code=503,
            detail="Redis 처리 실패",
        ) from exc

    # 3. 브라우저에 식별 토큰 저장
    response.set_cookie(
        key="session_token",
        value=token,
        max_age=repository.SESSION_TTL_SECONDS,
        httponly=True,
        samesite="lax",
        secure=False,  # 로컬 HTTP용. HTTPS 배포 시 True
        path="/",
    )
    response.status_code = 201

    return {"created": True, "player": player}