"""平台适配器接口。

任何通讯平台（飞书、看板、QQ 等）实现此接口后即可接入 group_chat 引擎，
引擎侧无需任何改动。

Redis 事件协议（适配器必须遵守）
─────────────────────────────────────────────────────────────────────────────
  进 → 引擎：
    频道  group_msg:{channel_id}
    payload {
        "message_id": str,       # 平台消息 ID（可用 uuid 生成）
        "chat_id":    str,       # 频道 / 群组 ID
        "sender":     str,       # 发送者 key，如 "user"
        "text":       str,       # 消息文本
        "image_base64": str,     # 可选，图片
        "mentions":   list[str], # 可选，@提及的 employee key 列表
    }

  出 → 适配器（引擎请求员工发言）：
    频道  speak_req:{employee}:{channel_id}
    payload SpeakRequest（见 models.py）

  出 → 引擎（适配器回复发言结果）：
    频道  speak_resp:{session_id}
    payload SpeakResponse（见 models.py）

  进 → 引擎（游戏场景用户输入）：
    频道  user_input:{channel_id}
    payload {
        "channel_id": str,
        "text":       str,
        "message_id": str,
        "sender":     str,
    }
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .event_bus import GroupEventBusPool


class PlatformAdapter(ABC):
    """通讯平台适配器抽象基类。

    接入新平台步骤：
      1. 继承此类，实现 platform_id、start()、stop()
      2. 在 start() 中监听平台消息，将其转为 MessageEvent 发布到 Redis
      3. 在 start() 中订阅 speak_req 频道，调用平台 API 发送消息，
         完成后 publish speak_resp 通知引擎
    """

    @property
    @abstractmethod
    def platform_id(self) -> str:
        """平台唯一标识，如 'feishu'、'kanban'、'qq'。"""

    @abstractmethod
    async def start(self, bus_pool: "GroupEventBusPool") -> None:
        """启动平台连接，开始双向消息转发。"""

    @abstractmethod
    async def stop(self) -> None:
        """关闭连接，释放资源。"""
