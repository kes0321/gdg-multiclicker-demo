import asyncio
import logging

from redis.exceptions import RedisError

from app.repositories.repository import RedisRepository

logger = logging.getLogger(__name__)


async def run_token_refill(repository: RedisRepository) -> None:
    interval = 0.5
    loop = asyncio.get_running_loop()
    next_tick = loop.time() + interval

    while True:
        await asyncio.sleep(max(0, next_tick - loop.time()))

        try:
            await repository.refill_click_buckets()
        except RedisError:
            logger.exception("토큰 충전 실패")

        next_tick += interval

        # 작업이 오래 지연된 경우 밀린 충전을 연속 실행하지 않음
        if next_tick <= loop.time():
            next_tick = loop.time() + interval