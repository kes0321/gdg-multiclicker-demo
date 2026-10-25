from contextlib import asynccontextmanager

from app.repositories.repository import RedisRepository
from fastapi import FastAPI, Request
from redis.asyncio import Redis
from app.api.http import router as http_router


@asynccontextmanager
async def lifespan(app: FastAPI):
    client = Redis(
        host="localhost",
        port=6379,
        decode_responses=True,  # bytes 대신 문자열로 반환
    )
    app.state.redis = client
    app.state.repository = RedisRepository(client)

    try:
        await client.ping()
        print("Redis 연결 성공!")
        yield
    finally:
        await client.aclose()


app = FastAPI(lifespan=lifespan)
app.include_router(http_router)


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
