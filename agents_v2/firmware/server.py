"""firmware · A2A server。

启动:``uvicorn agents_v2.firmware.server:app --port 8105``
"""
from agents_v2._server_factory import make_a2a_app

app = make_a2a_app("firmware")

__all__ = ["app"]
