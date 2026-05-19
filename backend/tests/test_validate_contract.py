"""B2 patch P1 — validate_project_contract.py 的 pytest 包装。

不直接 invoke CLI,而是 import 函数级跑,方便覆盖 5 项检查。
"""
import json
import shutil
import sys
import tempfile
from pathlib import Path

import pytest

# 把 scripts/ 加 sys.path 以 import
_SCRIPT_DIR = Path(__file__).resolve().parent.parent.parent / "scripts"
sys.path.insert(0, str(_SCRIPT_DIR))
import validate_project_contract as vpc  # noqa: E402


@pytest.fixture
def tmp_project(tmp_path):
    """空白项目根。"""
    return tmp_path / "demo"


def _write_manifest(root: Path, **overrides):
    root.mkdir(parents=True, exist_ok=True)
    base = {
        "project": "demo",
        "name": "demo",
        "version": "0.1.0",
        "tags": ["TEST"],
        "hero_image": "renders/hero.png",
        "summary": {
            "mass_g": 100, "dof": 2, "parts_count": 1,
            "cost_by_category": {"electrical": 10.0, "mechanical": 5.0, "total": 15.0},
            "currency": "CNY",
        },
        "assembly": {"parts": []},
        "deliverables": [],
    }
    base.update(overrides)
    (root / "manifest.json").write_text(json.dumps(base))


def _write_bom(root: Path, **overrides):
    elec = root / "domains" / "electronics"
    elec.mkdir(parents=True, exist_ok=True)
    bom = {
        "currency": "CNY",
        "items": [{
            "category": "actuator",
            "name": "MG996R",
            "qty": 1, "unit_price": 28.0, "total": 28.0,
            "vendors": [
                {"name": "DigiKey", "url": "x", "price_cny": 88.0, "tier": "pro"},
                {"name": "AliExpress", "url": "y", "price_cny": 22.0, "tier": "budget"},
            ],
            "selected_vendor": "AliExpress",
        }],
        "summary": {"total": 28.0, "by_category": {"actuator": 28.0}},
    }
    bom.update(overrides)
    (elec / "bom.json").write_text(json.dumps(bom))


def _write_assembly(root: Path):
    a = {
        "tools": ["3D printer"],
        "assumptions": ["soldering"],
        "phases": [
            {"name": n, "icon": "✓", "steps": []}
            for n in ["Fabricate", "Wire", "Assemble", "Program", "Calibrate"]
        ],
    }
    (root / "assembly.json").write_text(json.dumps(a))


# ── 合规路径 ──────────────────────────────────────────────────────────────

def test_full_compliant_project_passes(tmp_project):
    """全部 5 项检查合规 → exit 0"""
    _write_manifest(tmp_project)
    _write_bom(tmp_project)
    _write_assembly(tmp_project)

    rc = vpc.main(["vpc", str(tmp_project)])
    assert rc == 0


# ── 各项不合规独立检查 ──────────────────────────────────────────────────────

def test_missing_manifest(tmp_project):
    _write_bom(tmp_project)
    _write_assembly(tmp_project)
    errors: list[str] = []
    vpc.check_manifest(tmp_project, errors)
    assert any("manifest.json 不存在" in e for e in errors)


def test_manifest_missing_tags(tmp_project):
    _write_manifest(tmp_project, tags=[])  # 空 tags
    errors: list[str] = []
    vpc.check_manifest(tmp_project, errors)
    assert any("tags" in e for e in errors)


def test_manifest_missing_cost_category(tmp_project):
    summary_bad = {
        "mass_g": 100, "dof": 2, "parts_count": 1,
        "cost_by_category": {"electrical": 10.0},  # 缺 mechanical / total
        "currency": "CNY",
    }
    _write_manifest(tmp_project, summary=summary_bad)
    errors: list[str] = []
    vpc.check_manifest(tmp_project, errors)
    assert any("mechanical" in e for e in errors)
    assert any("total" in e for e in errors)


def test_bom_invalid_category(tmp_project):
    elec = tmp_project / "domains" / "electronics"
    elec.mkdir(parents=True)
    bad_bom = {
        "items": [{
            "category": "rocket-engine",  # 非 12 类
            "name": "X", "qty": 1, "unit_price": 1.0, "total": 1.0,
            "vendors": [
                {"name": "A", "url": "", "price_cny": 1, "tier": "pro"},
                {"name": "B", "url": "", "price_cny": 1, "tier": "budget"},
            ],
        }],
    }
    (elec / "bom.json").write_text(json.dumps(bad_bom))
    errors: list[str] = []
    vpc.check_bom(tmp_project, errors)
    assert any("rocket-engine" in e for e in errors)


def test_bom_too_few_vendors(tmp_project):
    elec = tmp_project / "domains" / "electronics"
    elec.mkdir(parents=True)
    bom = {
        "items": [{
            "category": "actuator", "name": "MG996R",
            "qty": 1, "unit_price": 28.0, "total": 28.0,
            "vendors": [{"name": "A", "url": "", "price_cny": 1, "tier": "pro"}],  # 只 1 个
        }],
    }
    (elec / "bom.json").write_text(json.dumps(bom))
    errors: list[str] = []
    vpc.check_bom(tmp_project, errors)
    assert any("vendors" in e and "< 2" in e for e in errors)


def test_assembly_missing_phase(tmp_project):
    a = {
        "tools": [], "assumptions": [],
        "phases": [{"name": "Fabricate", "icon": "✓", "steps": []}],  # 缺 4 个
    }
    (tmp_project).mkdir(parents=True, exist_ok=True)
    (tmp_project / "assembly.json").write_text(json.dumps(a))
    errors: list[str] = []
    vpc.check_assembly(tmp_project, errors)
    for required in ("Wire", "Assemble", "Program", "Calibrate"):
        assert any(required in e for e in errors), f"应报缺 {required}"


def test_part_glb_missing_file(tmp_project):
    _write_manifest(tmp_project, assembly={
        "parts": [{
            "id": "femur", "name": "femur",
            "glb": "domains/mechanical/parts/femur.glb",  # 文件不存在
            "step": "", "transform": {}, "explode_offset": [0, 0, 0],
            "color": "#fff", "owner": "mechanical",
        }],
    })
    manifest = json.loads((tmp_project / "manifest.json").read_text())
    errors: list[str] = []
    vpc.check_part_files(tmp_project, manifest, errors)
    assert any("femur.glb" in e for e in errors)


def test_kicad_unpaired_sch_warns(tmp_project):
    """有 .kicad_sch 但没对应 -sch.svg → 报错"""
    elec = tmp_project / "domains" / "electronics"
    elec.mkdir(parents=True)
    (elec / "leg-driver.kicad_sch").write_text("(kicad_sch ...)")
    errors: list[str] = []
    vpc.check_kicad_pairing(tmp_project, errors)
    assert any("leg-driver" in e and "SVG" in e for e in errors)


def test_kicad_paired_sch_passes(tmp_project):
    elec = tmp_project / "domains" / "electronics"
    (elec / "cad" / "exports").mkdir(parents=True)
    (elec / "leg-driver.kicad_sch").write_text("(kicad_sch ...)")
    (elec / "cad" / "exports" / "leg-driver-sch.svg").write_text("<svg/>")
    errors: list[str] = []
    vpc.check_kicad_pairing(tmp_project, errors)
    assert errors == []
