"""看板聊天 WebSocket endpoint。

连接：ws://{host}/api/ws/chat/{channel_id}

客户端发送：{"type": "message", "content": "...", "sender": "CEO"}
服务端推送：{"type": "message", "role": "assistant", "sender": "{employee}", "content": "..."}

页面初次加载历史记录仍通过 HTTP GET /api/chat/{channel_id}/history。
"""
from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from backend.chat.kanban_adapter import kanban_adapter

router = APIRouter(prefix="/api/ws", tags=["websocket"])


@router.websocket("/chat/{channel_id}")
async def ws_chat(websocket: WebSocket, channel_id: str) -> None:
    await kanban_adapter.connect(channel_id, websocket)
    try:
        while True:
            data = await websocket.receive_json()
            if data.get("type") == "message":
                await kanban_adapter.on_message(
                    channel_id=channel_id,
                    sender=data.get("sender", "user"),
                    text=data.get("content", ""),
                )
    except WebSocketDisconnect:
        kanban_adapter.disconnect(channel_id, websocket)
    except Exception:
        kanban_adapter.disconnect(channel_id, websocket)
