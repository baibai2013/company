"""B2.1 — projects 路由测试。

覆盖:
- 路径越界(. ./../etc/passwd / 绝对路径 / 符号链接)被 403/404
- manifest 兜底扫描(目录里没有 manifest.json 也能拼一份)
- bom 12 类 category 校验通过
- ETag → 304 复用
- list_projects 正确识别 charter.md / manifest.json
"""
import json
import os
import shutil
import tempfile
from pathlib import Path

import pytest
from httpx import ASGITransport, AsyncClient

# 必须在 import app 之前设置 PROJECTS_ROOT
_TMP_ROOT = tempfile.mkdtemp(prefix="b2-tests-")
os.environ["PROJECTS_ROOT"] = _TMP_ROOT


@pytest.fixture(scope="module", autouse=True)
def _override_projects_root():
    """每个模块共用一个临时 PROJECTS_ROOT,test 末统一清掉。"""
    # 保险起见再改一次
    from backend.services import project_service
    project_service.PROJECTS_ROOT = Path(_TMP_ROOT).resolve()
    yield
    shutil.rmtree(_TMP_ROOT, ignore_errors=True)


@pytest.fixture
async def projects_client():
    """独立的 client fixture,绕开 conftest 的 SQLite 全建表(我们这组测试不用 DB)。"""
    from backend.main import app
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        yield c


def _make_project(name: str) -> Path:
    p = Path(_TMP_ROOT) / name
    p.mkdir(parents=True, exist_ok=True)
    return p


# ── B2-1: list_projects 识别 charter / manifest ────────────────────────────

async def test_list_projects_filters_by_marker(projects_client):
    """B2-1: 只返回含 manifest.json 或 charter.md 的目录"""
    a = _make_project("only-charter")
    (a / "charter.md").write_text("# only-charter\n")

    b = _make_project("only-manifest")
    (b / "manifest.json").write_text(json.dumps({
        "project": "only-manifest", "name": "M",
        "assembly": {"parts": []},
    }))

    c = _make_project("nothing")  # 不应被列出

    r = await projects_client.get("/api/projects")
    assert r.status_code == 200
    names = {p["name"] for p in r.json()}
    assert "only-charter" in names
    assert "only-manifest" in names
    assert "nothing" not in names

    # cleanup
    for d in (a, b, c):
        shutil.rmtree(d)


# ── B2-2: manifest 兜底扫描 ──────────────────────────────────────────────────

async def test_manifest_fallback_scans_directory(projects_client):
    """B2-2: 没有 manifest.json 时按子目录拼一份,fallback=True"""
    proj = _make_project("scan-test")
    (proj / "charter.md").write_text("# scan-test\n")
    (proj / "prd").mkdir()
    (proj / "prd" / "leg-2dof.md").write_text("# PRD")
    (proj / "parts").mkdir()
    (proj / "parts" / "femur.glb").write_bytes(b"fake-glb")
    (proj / "parts" / "femur.step").write_text("ISO-10303-21;\nfake step")
    (proj / "firmware").mkdir()
    (proj / "firmware" / "leg_pwm.c").write_text("// pwm")

    r = await projects_client.get("/api/projects/scan-test/manifest")
    assert r.status_code == 200
    body = r.json()
    assert body["fallback"] is True
    kinds = {d["kind"] for d in body["deliverables"]}
    assert {"prd", "cad", "firmware"}.issubset(kinds)
    assert any(p["id"] == "femur" for p in body["assembly"]["parts"])

    shutil.rmtree(proj)


# ── B2-3: 路径越界拦截 ───────────────────────────────────────────────────────

async def test_file_path_traversal_blocked(projects_client):
    """B2-3: ../../etc/passwd 一类越界 → 403"""
    proj = _make_project("safe-test")
    (proj / "charter.md").write_text("# safe-test\n")

    r1 = await projects_client.get("/api/projects/safe-test/file?path=../../etc/passwd")
    assert r1.status_code == 403, r1.text

    r2 = await projects_client.get("/api/projects/safe-test/file?path=/etc/passwd")
    assert r2.status_code == 403, r2.text

    shutil.rmtree(proj)


async def test_file_symlink_escape_blocked(projects_client):
    """B2-3b: 符号链接逃逸也被 403"""
    proj = _make_project("symlink-test")
    (proj / "charter.md").write_text("# symlink-test\n")
    secret_dir = Path(_TMP_ROOT) / "_outside_secret"
    secret_dir.mkdir(exist_ok=True)
    (secret_dir / "secret.txt").write_text("DO NOT LEAK")
    (proj / "evil_link").symlink_to(secret_dir / "secret.txt")

    r = await projects_client.get("/api/projects/symlink-test/file?path=evil_link")
    assert r.status_code == 403, r.text

    shutil.rmtree(proj)
    shutil.rmtree(secret_dir, ignore_errors=True)


async def test_file_normal_read_works(projects_client):
    """B2-3c: 正常文件读返回 200 + 内容"""
    proj = _make_project("read-test")
    (proj / "charter.md").write_text("# hello\n", encoding="utf-8")

    r = await projects_client.get("/api/projects/read-test/file?path=charter.md")
    assert r.status_code == 200, r.text
    assert "hello" in r.text

    shutil.rmtree(proj)


# ── B2-4: tree 不暴露隐藏目录 ────────────────────────────────────────────────

async def test_tree_skips_dotfiles_and_pycache(projects_client):
    """B2-4: .git / __pycache__ / .claude 一类不出现在 tree 中"""
    proj = _make_project("tree-test")
    (proj / "charter.md").write_text("# tree-test\n")
    (proj / ".git").mkdir()
    (proj / "__pycache__").mkdir()
    (proj / "src").mkdir()
    (proj / "src" / "main.py").write_text("# code")

    r = await projects_client.get("/api/projects/tree-test/tree")
    assert r.status_code == 200
    paths = {n["path"] for n in r.json()}
    assert "src" in paths
    assert ".git" not in paths
    assert "__pycache__" not in paths

    shutil.rmtree(proj)


# ── B2-5: ETag 304 复用 ─────────────────────────────────────────────────────

async def test_manifest_etag_304(projects_client):
    """B2-5: If-None-Match 与 ETag 一致时返回 304"""
    proj = _make_project("etag-test")
    (proj / "charter.md").write_text("# etag-test\n")

    r1 = await projects_client.get("/api/projects/etag-test/manifest")
    assert r1.status_code == 200
    etag = r1.headers.get("etag")
    assert etag, "no etag header"

    r2 = await projects_client.get(
        "/api/projects/etag-test/manifest",
        headers={"if-none-match": etag},
    )
    assert r2.status_code == 304, r2.text

    shutil.rmtree(proj)


# ── B2-6: BOM 12 类强枚举 ───────────────────────────────────────────────────

async def test_bom_validates_category_enum(projects_client):
    """B2-6: 合法 12 类 category 通过;非法值后端 500/422"""
    proj = _make_project("bom-test")
    (proj / "charter.md").write_text("# bom-test\n")
    (proj / "bom").mkdir()
    valid = {
        "currency": "CNY",
        "items": [
            {
                "category": "actuator", "subcategory": "舵机", "name": "MG996R",
                "qty": 2, "unit_price": 28.0, "total": 56.0,
                "vendors": [
                    {"name": "AliExpress", "url": "https://x", "price_cny": 22.4, "tier": "budget"},
                    {"name": "DigiKey", "url": "https://y", "price_cny": 88.5, "tier": "pro"},
                ],
                "selected_vendor": "AliExpress",
            }
        ],
        "summary": {"total": 56.0, "by_category": {"actuator": 56.0}},
    }
    (proj / "bom" / "leg-cost.json").write_text(json.dumps(valid))

    r = await projects_client.get("/api/projects/bom-test/bom")
    assert r.status_code == 200
    data = r.json()
    assert data["items"][0]["category"] == "actuator"
    assert len(data["items"][0]["vendors"]) >= 2

    shutil.rmtree(proj)


async def test_bom_invalid_category_rejected(projects_client):
    """B2-6b: category 不在 12 类内 → 后端拒绝"""
    proj = _make_project("bom-bad")
    (proj / "charter.md").write_text("# bom-bad\n")
    (proj / "bom").mkdir()
    bad = {
        "currency": "CNY",
        "items": [
            {
                "category": "rocket-engine",  # 不在 12 类
                "name": "X", "qty": 1, "unit_price": 1.0, "total": 1.0,
                "vendors": [],
            }
        ],
        "summary": {"total": 1.0},
    }
    (proj / "bom" / "leg-bad.json").write_text(json.dumps(bad))
    # Bad file is not at standard path; load_bom only reads leg-cost.json
    (proj / "bom" / "leg-cost.json").write_text(json.dumps(bad))

    r = await projects_client.get("/api/projects/bom-bad/bom")
    assert r.status_code == 500, r.text  # pydantic validation error 走 500

    shutil.rmtree(proj)


# ── B2-7: 不存在的项目 404 ───────────────────────────────────────────────────

async def test_unknown_project_returns_404(projects_client):
    r = await projects_client.get("/api/projects/no-such-project/manifest")
    assert r.status_code == 404
    r2 = await projects_client.get("/api/projects/no-such-project/tree")
    assert r2.status_code == 404
