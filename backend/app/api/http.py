# backend/app/api/http.py

from fastapi import APIRouter, HTTPException, Query, Request
from redis.asyncio import Redis
from redis.exceptions import RedisError

router = APIRouter(prefix="/api", tags=["HTTP"])

LEADERBOARD_KEY = "leaderboard"


@router.get("/health")
async def health(request: Request):
    redis: Redis = request.app.state.redis

    try:
        await redis.ping()
    except RedisError as exc:
        raise HTTPException(
            status_code=503,
            detail="Redis 연결 실패",
        ) from exc

    return {"status": "ok"}


@router.get("/leaderboard")
async def get_leaderboard(
    request: Request,
    limit: int = Query(default=10, ge=1, le=100),
):
    redis: Redis = request.app.state.redis

    try:
        entries = await redis.zrevrange(
            LEADERBOARD_KEY,
            0,
            limit - 1,
            withscores=True,
        )
    except RedisError as exc:
        raise HTTPException(
            status_code=503,
            detail="랭킹 조회 실패",
        ) from exc

    return {
        "players": [
            {
                "rank": rank,
                "user_id": user_id,
                "score": score,
            }
            for rank, (user_id, score) in enumerate(entries, start=1)
        ]
    }

