import logging
import json
from typing import TypedDict

from redis.asyncio import Redis

from pathlib import Path
from secrets import token_urlsafe
from time import time_ns
from uuid import uuid4

from app.balance import (
    InvalidPlayerLevel, NumericLimitExceeded, LUA_BALANCE_HELPER,
    safe_balance_for_level,
)

logger = logging.getLogger(__name__)


class Player(TypedDict):
    player_id: str
    nickname: str
    score: int
    level: int
    total_clicks: int
    created_at_ms: int
    gold_per_click: int
    next_gold_per_click: int | None
    next_upgrade_cost: int | None


class RankingEntry(TypedDict):
    rank: int
    player_id: str
    nickname: str
    score: int


class RedisRepository:
    LEADERBOARD_KEY = "leaderboard"

    SESSION_TTL_SECONDS = 30 * 24 * 60 * 60
    BUCKET_TTL_SECONDS = 60
    BUCKET_CAPACITY = 30

    def __init__(self, redis: Redis):
        # main.py에서 만든 Redis 클라이언트를 받아 재사용
        # decode_responses=True로 생성한 클라이언트를 사용해야 함
        self.redis = redis

        script_path = Path(__file__).parent / "scripts" / "create_player.lua"
        self._create_player_script = redis.register_script(
            script_path.read_text(encoding="utf-8")
        )
        
        click_path = Path(__file__).parent / "scripts" / "click.lua"
        self._click_script = redis.register_script(
            LUA_BALANCE_HELPER + click_path.read_text(encoding="utf-8")
        )
        
        batch_path = Path(__file__).parent / "scripts" / "click_batch.lua"
        self._click_batch_script = redis.register_script(
            LUA_BALANCE_HELPER + batch_path.read_text(encoding="utf-8")
        )
        
        levelup_path = Path(__file__).parent / "scripts" / "levelup.lua"
        self._levelup_script = redis.register_script(
            LUA_BALANCE_HELPER + levelup_path.read_text(encoding="utf-8")
        )

        refill_path = (Path(__file__).parent / "scripts" / "refill_buckets.lua")
        self._refill_buckets_script = redis.register_script(
            refill_path.read_text(encoding="utf-8")
        )



    async def ping(self) -> bool:
        """Redis 연결 상태 확인."""
        return await self.redis.ping()

    async def get_player(self, player_id: str) -> Player | None:
        """플레이어 조회. 없으면 None 반환."""

        # player_id가 "p001"이면 key는 "player:p001"
        key = f"player:{player_id}"

        # CLI: HGETALL player:p001
        data = await self.redis.hgetall(key)

        # 존재하지 않는 Hash는 빈 dict {}로 반환됨
        if not data:
            return None

        # Invalid legacy data is reported without modifying the player.
        try:
            level = int(data["level"])
            balance = safe_balance_for_level(level)
        except NumericLimitExceeded:
            logger.error("NUMERIC_LIMIT_EXCEEDED player_id=%s", player_id)
            raise
        except (KeyError, ValueError) as exc:
            logger.error("INVALID_PLAYER_LEVEL player_id=%s level=%r", player_id, data.get("level"))
            raise InvalidPlayerLevel(f"Invalid level for {player_id}") from exc

        # Hash의 값은 숫자도 문자열로 반환되므로 int로 변환
        return {
            "player_id": player_id,
            "nickname": data["nickname"],
            "score": int(data["score"]),
            "level": level,
            "total_clicks": int(data["total_clicks"]),
            "created_at_ms": int(data["created_at_ms"]),
            **balance,
        }

    async def get_leaderboard(self, limit: int = 10) -> list[RankingEntry]:
        """보유 골드 기준 상위 플레이어 조회."""

        if not 1 <= limit <= 100:
            raise ValueError("limit은 1~100이어야 합니다.")

        # CLI: ZREVRANGE leaderboard 0 9 WITHSCORES
        entries = await self.redis.zrevrange(
            self.LEADERBOARD_KEY,
            0,
            limit - 1,
            withscores=True,
        )
        # entries 예시:
        # [("p001", 1500.0), ("p002", 900.0)]
        
        # redis pipeline을 사용하여 여러 플레이어의 닉네임 조회 명령을 한 번에 실행
        async with self.redis.pipeline(transaction=False) as pipe:
            for player_id, _ in entries:
                pipe.hget(f"player:{player_id}", "nickname")

            nicknames = await pipe.execute()
        
        
        return [
            {
                "rank": rank,
                "player_id": player_id,
                "nickname": nickname if nickname is not None else "Unknown",
                "score": int(score),
            }
            for rank, ((player_id, score), nickname) in enumerate(
                zip(entries, nicknames),
                start=1,
            )
        ]
        
    
    async def get_online_leaderboard(
        self,
        player_ids: list[str],
    ) -> list[RankingEntry]:
        if not player_ids:
            return []

        # 접속 중인 플레이어들의 점수만 한 번에 조회
        scores = await self.redis.zmscore(
            self.LEADERBOARD_KEY,
            player_ids,
        )

        # 닉네임 조회 명령을 묶어서 전송
        async with self.redis.pipeline(transaction=False) as pipe:
            for player_id in player_ids:
                pipe.hget(f"player:{player_id}", "nickname")

            nicknames = await pipe.execute()

        players = [
            {
                "player_id": player_id,
                "nickname": nickname if nickname is not None else "Unknown",
                "score": int(score),
            }
            for player_id, score, nickname in zip(
                player_ids, scores, nicknames
            )
            if score is not None
        ]

        # 점수 내림차순 정렬
        # 동점이면 player_id 기준으로 순서를 고정
        players.sort(
            key=lambda player: (-player["score"], player["player_id"])
        )

        return [
            {"rank": rank, **player}
            for rank, player in enumerate(players, start=1)
        ]
        

    async def get_player_rank(self, player_id: str) -> int | None:
        """개인 순위 조회. 랭킹에 없으면 None 반환."""

        # CLI: ZREVRANK leaderboard p001
        rank = await self.redis.zrevrank(
            self.LEADERBOARD_KEY,
            player_id,
        )

        # Redis 순위는 0부터 시작하므로 화면 표시용으로 +1
        if rank is None:
            return None

        return rank + 1

    async def get_session_player_id(self, token: str) -> str | None:
        """세션 토큰에 대응하는 플레이어 ID 조회."""

        # CLI: GET session:dev-token-001
        return await self.redis.get(f"session:{token}")


    async def create_player(self, nickname: str) -> tuple[str, str]:
        """신규 플레이어 생성 후 (player_id, token) 반환."""

        nickname = nickname.strip()

        if not 1 <= len(nickname) <= 20:
            raise ValueError("닉네임은 1~20자여야 합니다.")

        # ID와 토큰은 클라이언트가 아닌 서버에서 생성
        player_id = f"p{uuid4().hex}"
        token = token_urlsafe(32)
        now_ms = time_ns() // 1_000_000

        result = await self._create_player_script(
            keys=[
                f"player:{player_id}",
                self.LEADERBOARD_KEY,
                f"rate_limit:click:{player_id}",
                f"session:{token}",
            ],
            args=[
                player_id,
                nickname,
                now_ms,
                self.BUCKET_CAPACITY,
                self.BUCKET_TTL_SECONDS,
                self.SESSION_TTL_SECONDS,
            ],
        )

        # Lua의 {1, player_id}가 Python에서는 [1, player_id]로 반환
        if result[0] != 1:
            raise ValueError(f"플레이어 생성 실패: {result[1]}")

        return player_id, token


    async def click(self, player_id: str) -> dict:
        return await self.click_batch(player_id, count=1)

    async def _run_game_script(self, script, keys, args, *, levelup=False):
        payload = "{}"
        while True:
            result = await script(keys=keys, args=[*args, payload])
            if result[:2] != [0, "BALANCE_REQUIRED"]:
                return result
            # Lua reports the actual level without writing. It rechecks this
            # level on retry, so another connection's upgrade cannot go stale.
            level = int(result[2])
            try:
                balance = {"level": level, **safe_balance_for_level(level)}
                if levelup:
                    safe_balance_for_level(level + 1)
            except NumericLimitExceeded:
                logger.error("NUMERIC_LIMIT_EXCEEDED player_id=%s level=%s", args[0], level)
                return [0, "NUMERIC_LIMIT_EXCEEDED"]
            payload = json.dumps(balance)

        
    async def click_batch(self, player_id: str, count: int) -> dict:
        if type(count) is not int or not 1 <= count <= 10:
            raise ValueError("count는 1~10의 정수여야 합니다.")

        result = await self._run_game_script(
            self._click_batch_script,
            keys=[
                f"player:{player_id}",
                self.LEADERBOARD_KEY,
                f"rate_limit:click:{player_id}",
            ],
            args=[player_id, count],
        )

        if result[0] == 0:
            if result[1] == "INVALID_PLAYER_LEVEL":
                logger.error("INVALID_PLAYER_LEVEL player_id=%s", player_id)
            response = {
                "success": False,
                "code": result[1],
            }

            if result[1] == "RATE_LIMITED":
                response["remaining_tokens"] = int(result[2])

            return response

        return {
            "success": True,
            "player_id": player_id,
            "score": int(result[1]),
            "level": int(result[2]),
            "total_clicks": int(result[3]),
            "gold_gained": int(result[4]),
            "accepted_clicks": int(result[5]),
            **safe_balance_for_level(int(result[2])),
        }



    async def refill_click_buckets(self) -> None:
        # SCAN은 같은 키를 중복 반환할 수 있어 set으로 중복 제거
        bucket_keys = set()

        async for key in self.redis.scan_iter(
            match="rate_limit:click:*",
            count=100,
        ):
            bucket_keys.add(key)

        if not bucket_keys:
            return

        await self._refill_buckets_script(
            keys=sorted(bucket_keys),
            args=[self.BUCKET_CAPACITY, 4],
        )

        
    async def levelup(self, player_id: str) -> dict:
        result = await self._run_game_script(
            self._levelup_script,
            keys=[
                f"player:{player_id}",
                self.LEADERBOARD_KEY,
            ],
            args=[player_id],
            levelup=True,
        )

        if result[0] == 0:
            if result[1] == "INVALID_PLAYER_LEVEL":
                logger.error("INVALID_PLAYER_LEVEL player_id=%s", player_id)
            error = {
                "success": False,
                "code": result[1],
            }

            if result[1] == "INSUFFICIENT_GOLD":
                error["score"] = int(result[2])
                error["required_gold"] = int(result[3])

            if result[1] == "MAX_LEVEL_REACHED":
                error.update({
                    "score": int(result[2]),
                    "level": int(result[3]),
                    **safe_balance_for_level(int(result[3])),
                })

            return error

        return {
            "success": True,
            "player_id": player_id,
            "score": int(result[1]),
            "level": int(result[2]),
            "gold_spent": int(result[3]),
            **safe_balance_for_level(int(result[2])),
        }
