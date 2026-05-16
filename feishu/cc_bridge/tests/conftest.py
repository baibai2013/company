"""
thread_router 单测公用夹具。

关键隔离点：
- PERSIST_PATH：每个测试用 tmp_path 下的临时文件，不污染 ~/.claude
- ALLOWED_CWD_PREFIX：set_cwd 测试要打到磁盘真实目录，把白名单临时改成 tmp_path
- THREAD_CAP / MSG_MAP_CAP / IDLE_DROP：测试场景下需要把上限调小，monkeypatch 即可生效
  （模块函数引用的是模块级名字，运行时查 module.__dict__）
"""
import pytest

from feishu.cc_bridge import thread_router


@pytest.fixture
def isolated_persist(tmp_path, monkeypatch):
    """把 PERSIST_PATH 指到 tmp_path/threads.json，避免动用户家目录。"""
    p = tmp_path / "threads.json"
    monkeypatch.setattr(thread_router, "PERSIST_PATH", p)
    return p


@pytest.fixture
def router(isolated_persist):
    """全新 ThreadRouter（每个用例独立实例）。"""
    return thread_router.ThreadRouter()


@pytest.fixture
def small_caps(monkeypatch):
    """把 LRU / msg map 上限调小，便于触发淘汰逻辑。"""
    monkeypatch.setattr(thread_router, "THREAD_CAP", 2)
    monkeypatch.setattr(thread_router, "MSG_MAP_CAP", 3)
