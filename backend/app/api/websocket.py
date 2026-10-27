import asyncio
import logging
from contextlib import suppress
from json import JSONDecodeError

from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from redis.exceptions import RedisError

from app.repositories.repository import RedisRepository
from app.services.connection_manager import manager

router = APIRouter()
logger = logging.getLogger(__name__)


async def broadcast_leaderboard(
    repository: RedisRepository,
) -> None:
    try:
        await manager.broadcast_leaderboard(repository)
    except RedisError:
        # 골드 변경 성공과 랭킹 조회 실패는 별도로 처리
        logger.exception("리더보드 방송 실패")


async def handle_click(
    websocket: WebSocket,
    repository: RedisRepository,
    player_id: str,
) -> bool:
    result = await repository.click(player_id)

    if result is None:
        await websocket.close(
            code=1008,
            reason="Player not found",
        )
        return False

    # 골드 변경에 성공했으므로 즉시 방송
    await broadcast_leaderboard(repository)

    await manager.send(websocket, {
        "type": "click_result",
        **result,
    })

    return True


async def handle_click_batch(
    websocket: WebSocket,
    repository: RedisRepository,
    player_id: str,
    message: dict,
) -> bool:
    count = message.get("count")

    if type(count) is not int or not 1 <= count <= 10:
        await manager.send(websocket, {
            "type": "click_batch_result",
            "success": False,
            "code": "INVALID_BATCH_COUNT",
        })
        return True

    result = await repository.click_batch(player_id, count)

    if not result["success"]:
        if result["code"] == "PLAYER_NOT_FOUND":
            await websocket.close(
                code=1008,
                reason="Player not found",
            )
            return False
    else:
        await broadcast_leaderboard(repository)

    await manager.send(websocket, {
        "type": "click_batch_result",
        **result,
    })

    return True


async def handle_levelup(
    websocket: WebSocket,
    repository: RedisRepository,
    player_id: str,
) -> bool:
    result = await repository.levelup(player_id)

    if not result["success"]:
        if result["code"] == "PLAYER_NOT_FOUND":
            await websocket.close(
                code=1008,
                reason="Player not found",
            )
            return False
    else:
        # 레벨업으로 골드가 차감되므로 방송
        await broadcast_leaderboard(repository)

    await manager.send(websocket, {
        "type": "levelup_result",
        **result,
    })

    return True


@router.websocket("/ws")
async def game_websocket(websocket: WebSocket):
    repository: RedisRepository = (
        websocket.app.state.repository
    )
    registered = False

    try:
        # 1. 세션 확인
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

        # 2. 연결 수락 및 등록
        await websocket.accept()

        manager.add(websocket, player_id)
        registered = True

        await manager.send(websocket, {
            "type": "connected",
            "player": player,
        })

        # 접속자가 추가됐으므로 랭킹 방송
        await broadcast_leaderboard(repository)

        # 3. 요청 수신
        while True:
            try:
                message = await websocket.receive_json()
            except JSONDecodeError:
                await manager.send(websocket, {
                    "type": "error",
                    "code": "INVALID_JSON",
                })
                continue

            if not isinstance(message, dict):
                await manager.send(websocket, {
                    "type": "error",
                    "code": "INVALID_MESSAGE",
                })
                continue

            message_type = message.get("type")

            if message_type == "click":
                connected = await handle_click(
                    websocket,
                    repository,
                    player_id,
                )

            elif message_type == "click_batch":
                connected = await handle_click_batch(
                    websocket,
                    repository,
                    player_id,
                    message,
                )

            elif message_type == "levelup":
                connected = await handle_levelup(
                    websocket,
                    repository,
                    player_id,
                )

            else:
                await manager.send(websocket, {
                    "type": "error",
                    "code": "UNKNOWN_MESSAGE_TYPE",
                })
                continue

            if not connected:
                return

    except WebSocketDisconnect:
        pass

    except RedisError:
        logger.exception("WebSocket Redis 처리 실패")

        with suppress(
            WebSocketDisconnect,
            OSError,
            RuntimeError,
        ):
            await websocket.close(code=1011)

    except (TimeoutError, OSError):
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

    finally:
        # 정상 종료·오류 모두 연결 정리
        if registered:
            manager.remove(websocket)

            # 접속자 변경을 나머지 클라이언트에 반영
            await broadcast_leaderboard(repository)