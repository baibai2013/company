"""cost_engineer · A2A server。

启动:``uvicorn agents_v2.cost_engineer.server:app --port 8108``
"""
from agents_v2._server_factory import make_a2a_app

app = make_a2a_app("cost_engineer")

__all__ = ["app"]
