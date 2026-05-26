"""mechanical · A2A server。

启动:``uvicorn agents_v2.mechanical.server:app --port 8103``
"""
from agents_v2._server_factory import make_a2a_app

app = make_a2a_app("mechanical")

__all__ = ["app"]
