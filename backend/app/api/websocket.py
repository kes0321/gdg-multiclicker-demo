# backend/app/api/websocket.py

from json import JSONDecodeError
import logging

from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from redis.exceptions import RedisError

from app.repositories.repository import RedisRepository

router = APIRouter()
logger = logging.getLogger(__name__)


async def handle_click(
    websocket: WebSocket,
    repository: RedisRepository,
    player_id: str,
) -> bool:
    """클릭 처리. 플레이어가 없으면 연결 종료 후 False 반환."""

    result = await repository.click(player_id)

    if result is None:
        await websocket.close(
            code=1008,
            reason="Player not found",
        )
        return False

    await websocket.send_json({
        "type": "click_result",
        **result,
    })

    return True


@router.websocket("/ws")
async def game_websocket(websocket: WebSocket):
    repository: RedisRepository = (
        websocket.app.state.repository
    )

    try:
        # 브라우저가 연결 요청에 함께 보낸 쿠키 확인
        token = websocket.cookies.get("session_token")

        if not token:
            await websocket.close(code=1008)
            return

        player_id = await repository.get_session_player_id(token)

        if player_id is None:
            await websocket.close(code=1008)
            return

        player = await repository.get_player(player_id)

        if player is None:
            await websocket.close(code=1008)
            return

        # 세션이 유효한 경우에만 연결 수락
        await websocket.accept()

        await websocket.send_json({
            "type": "connected",
            "player": player,
        })

        while True:
            try:
                message = await websocket.receive_json()
            except JSONDecodeError:
                await websocket.send_json({
                    "type": "error",
                    "code": "INVALID_JSON",
                })
                continue

            # JSON 배열이나 숫자 등은 거절
            if not isinstance(message, dict):
                await websocket.send_json({
                    "type": "error",
                    "code": "INVALID_MESSAGE",
                })
                continue

            if message.get("type") == "click":
                connected = await handle_click(
                    websocket,
                    repository,
                    player_id,
                )

                if not connected:
                    return

            else:
                await websocket.send_json({
                    "type": "error",
                    "code": "UNKNOWN_MESSAGE_TYPE",
                })

    except WebSocketDisconnect:
        # 브라우저 종료, 새로고침, 네트워크 단절 등
        pass

    except RedisError:
        logger.exception("WebSocket Redis operation failed")

        await websocket.close(
            code=1011,
            reason="Redis operation failed",
        )