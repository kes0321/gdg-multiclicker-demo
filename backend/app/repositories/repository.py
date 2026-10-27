from typing import TypedDict

from redis.asyncio import Redis

from pathlib import Path
from secrets import token_urlsafe
from time import time_ns
from uuid import uuid4


class Player(TypedDict):
    player_id: str
    nickname: str
    score: int
    level: int
    total_clicks: int
    created_at_ms: int


class RankingEntry(TypedDict):
    rank: int
    player_id: str
    score: int


class RedisRepository:
    LEADERBOARD_KEY = "leaderboard"

    SESSION_TTL_SECONDS = 30 * 24 * 60 * 60
    BUCKET_TTL_SECONDS = 60
    BUCKET_CAPACITY = 35

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
            click_path.read_text(encoding="utf-8")
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

        # Hash의 값은 숫자도 문자열로 반환되므로 int로 변환
        return {
            "player_id": player_id,
            "nickname": data["nickname"],
            "score": int(data["score"]),
            "level": int(data["level"]),
            "total_clicks": int(data["total_clicks"]),
            "created_at_ms": int(data["created_at_ms"]),
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
        return [
            {
                "rank": rank,
                "player_id": player_id,
                "score": int(score),
            }
            for rank, (player_id, score) in enumerate(entries, start=1)
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


    async def click(self, player_id: str) -> dict | None:
        result = await self._click_script(
            keys=[
                f"player:{player_id}",
                self.LEADERBOARD_KEY,
            ],
            args=[player_id],
        )

        if result[0] == 0:
            return None

        return {
            "player_id": player_id,
            "score": int(result[1]),
            "level": int(result[2]),
            "total_clicks": int(result[3]),
            "gold_gained": int(result[4]),
        }