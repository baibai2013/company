"""hardware · A2A server。

启动:``uvicorn agents_v2.hardware.server:app --port 8104``
"""
from agents_v2._server_factory import make_a2a_app

app = make_a2a_app("hardware")

__all__ = ["app"]
