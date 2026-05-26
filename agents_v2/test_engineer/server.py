"""test_engineer · A2A server。

启动:``uvicorn agents_v2.test_engineer.server:app --port 8107``
"""
from agents_v2._server_factory import make_a2a_app

app = make_a2a_app("test_engineer")

__all__ = ["app"]
