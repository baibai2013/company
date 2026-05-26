"""algorithm · A2A server。

启动:``uvicorn agents_v2.algorithm.server:app --port 8106``
"""
from agents_v2._server_factory import make_a2a_app

app = make_a2a_app("algorithm")

__all__ = ["app"]
