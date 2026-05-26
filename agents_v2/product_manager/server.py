"""product_manager · A2A server。

启动:``uvicorn agents_v2.product_manager.server:app --port 8101``
"""
from agents_v2._server_factory import make_a2a_app

app = make_a2a_app("product_manager")

__all__ = ["app"]
