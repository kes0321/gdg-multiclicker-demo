import asyncio
from contextlib import suppress
from dataclasses import dataclass, field

from fastapi import WebSocket, WebSocketDisconnect

from app.repositories.repository import RedisRepository


@dataclass
class Connection:
    player_id: str
    send_lock: asyncio.Lock = field(default_factory=asyncio.Lock)


class ConnectionManager:
    def __init__(self):
        # 같은 플레이어가 여러 탭으로 접속할 수 있으므로
        # player_id가 아닌 WebSocket을 키로 사용
        self.connections: dict[WebSocket, Connection] = {}

        # 여러 방송의 조회·전송 순서가 뒤섞이지 않도록 사용
        self.broadcast_lock = asyncio.Lock()

    def add(self, websocket: WebSocket, player_id: str) -> None:
        self.connections[websocket] = Connection(
            player_id=player_id,
        )

    def remove(self, websocket: WebSocket) -> None:
        self.connections.pop(websocket, None)

    async def send(
        self,
        websocket: WebSocket,
        message: dict,
    ) -> None:
        connection = self.connections.get(websocket)

        if connection is None:
            raise WebSocketDisconnect(code=1001)

        # 개인 응답과 방송이 같은 연결에 동시에 쓰지 않도록 보호
        async with connection.send_lock:
            await asyncio.wait_for(
                websocket.send_json(message),
                timeout=2,
            )

    async def broadcast_leaderboard(
        self,
        repository: RedisRepository,
    ) -> None:
        async with self.broadcast_lock:
            targets = list(self.connections)

            if not targets:
                return

            # 여러 탭으로 접속해도 랭킹에는 한 번만 표시
            player_ids = sorted({
                connection.player_id
                for connection in self.connections.values()
            })

            players = await repository.get_online_leaderboard(
                player_ids
            )

            message = {
                "type": "leaderboard",
                "players": players,
            }

            # 각 연결에 병렬 전송
            results = await asyncio.gather(
                *(self.send(ws, message) for ws in targets),
                return_exceptions=True,
            )

            # 전송에 실패한 연결 제거
            for websocket, result in zip(targets, results):
                if isinstance(result, Exception):
                    self.remove(websocket)

                    with suppress(
                        WebSocketDisconnect,
                        OSError,
                        RuntimeError,
                        TimeoutError,
                    ):
                        await asyncio.wait_for(
                            websocket.close(code=1001),
                            timeout=1,
                        )


manager = ConnectionManager()