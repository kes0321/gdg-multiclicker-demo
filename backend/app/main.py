import asyncio
from contextlib import asynccontextmanager, suppress

from app.repositories.repository import RedisRepository
from app.services.token_refill import run_token_refill
from app.config import REDIS_HOST, REDIS_PORT

from fastapi import FastAPI, Request
from redis.asyncio import Redis
from app.api.http import router as http_router
from app.api.websocket import router as websocket_router
from app.admin.router import create_admin_router
from app.admin.security import AdminSettings
from app.config import ADMIN_PATH, ADMIN_PASSWORD_HASH, ADMIN_SESSION_TTL_SECONDS, SESSION_COOKIE_SECURE


@asynccontextmanager
async def lifespan(app: FastAPI):
    client = Redis(
        host=REDIS_HOST,
        port=REDIS_PORT,
        decode_responses=True,  # bytes 대신 문자열로 반환
    )
    refill_task = None

    try:
        await client.ping()
        print("Redis 연결 성공!")
        repository = RedisRepository(client)

        app.state.redis = client
        app.state.repository = repository

        refill_task = asyncio.create_task(
            run_token_refill(repository)
        )

        yield

    finally:
        if refill_task is not None:
            refill_task.cancel()

            with suppress(asyncio.CancelledError):
                await refill_task

        await client.aclose()


app = FastAPI(lifespan=lifespan)
app.include_router(http_router)
app.include_router(websocket_router)

admin_settings = AdminSettings(ADMIN_PATH, ADMIN_PASSWORD_HASH, ADMIN_SESSION_TTL_SECONDS, SESSION_COOKIE_SECURE)
if admin_settings.enabled:
    app.include_router(create_admin_router(admin_settings))


@app.get("/hello")
async def hello(request: Request):
    redis = request.app.state.redis

    await redis.set("hello", "Hello Redis!")
    value = await redis.get("hello")

    return {"message": value}


@app.get("/counter")
async def counter(request: Request):
    redis = request.app.state.redis

    # 호출할 때마다 값을 1 증가시킴
    count = await redis.incr("counter")

    return {"count": count}
