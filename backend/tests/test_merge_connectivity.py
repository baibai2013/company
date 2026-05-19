"""B2-connectivity-view §2.4 — merge_connectivity.py 单测。

不直接 invoke CLI,import merge / _validate 函数级跑。
"""
import json
import sys
from pathlib import Path

import pytest

_SCRIPT_DIR = Path(__file__).resolve().parent.parent.parent / "scripts"
sys.path.insert(0, str(_SCRIPT_DIR))
import merge_connectivity as mc  # noqa: E402


@pytest.fixture
def proj(tmp_path):
    """空白 robot-dog 根。"""
    root = tmp_path / "rd"
    (root / "domains" / "electronics").mkdir(parents=True)
    (root / "domains" / "mechanical" / "parts").mkdir(parents=True)
    (root / "domains" / "firmware").mkdir(parents=True)
    return root


def _write_bom(root: Path, items: list[dict]):
    (root / "domains" / "electronics" / "bom.json").write_text(json.dumps({
        "currency": "CNY", "items": items,
        "summary": {"total": 0, "by_category": {}},
    }))


def _write_part(root: Path, stem: str, meta: dict):
    (root / "domains" / "mechanical" / "parts" / f"{stem}.json").write_text(json.dumps(meta))


def _write_wiring(root: Path, connections: list[dict]):
    (root / "domains" / "firmware" / "wiring.json").write_text(json.dumps({
        "version": "1.0", "connections": connections,
    }))


# ── 节点合成 ──────────────────────────────────────────────────────────────

def test_bom_to_electronic_nodes(proj):
    """BOM item.id → connectivity node;category → kind 映射"""
    _write_bom(proj, [
        {
            "id": "esp32_main", "category": "microcontroller", "name": "ESP32",
            "qty": 1, "unit_price": 32, "total": 32,
            "vendors": [{"name": "A", "url": "", "price_cny": 1, "tier": "pro"},
                        {"name": "B", "url": "", "price_cny": 1, "tier": "budget"}],
            "interfaces": [{"id": "GPIO13", "kind": "data"}],
        },
    ])
    doc = mc.merge(proj)
    assert len(doc["nodes"]) == 1
    n = doc["nodes"][0]
    assert n["id"] == "esp32_main"
    assert n["kind"] == "mcu"  # microcontroller → mcu
    assert n["domain"] == "electronics"
    assert n["owner"] == "hardware"
    assert n["interfaces"] == [{"id": "GPIO13", "kind": "data"}]


def test_bom_cross_domain_adds_cad_model(proj):
    """cross_domain=true 时 kind=actuator_cross_domain + ref.cad_model"""
    _write_bom(proj, [
        {
            "id": "mg996r_fl_hip", "category": "actuator", "name": "MG996R FL Hip",
            "qty": 1, "unit_price": 28, "total": 28,
            "vendors": [{"name": "A", "url": "", "price_cny": 1, "tier": "pro"},
                        {"name": "B", "url": "", "price_cny": 1, "tier": "budget"}],
            "cross_domain": True,
            "cad_model": "domains/mechanical/parts/mg996r.glb",
            "interfaces": [
                {"id": "signal", "kind": "data"},
                {"id": "body", "kind": "mechanical"},
            ],
        },
    ])
    doc = mc.merge(proj)
    n = doc["nodes"][0]
    assert n["kind"] == "actuator_cross_domain"
    assert n["ref"]["cad_model"] == "domains/mechanical/parts/mg996r.glb"


def test_mechanical_parts_to_cad_nodes_and_mount_edges(proj):
    """parts/<x>.json → cad_part 节点;mount_points.mounted_to → mechanical 边"""
    _write_part(proj, "leg_fl_thigh", {
        "id": "leg_fl_thigh",
        "name": "FL Thigh",
        "owner": "mechanical",
        "mass_g": 42,
        "material": "PETG",
        "explode_offset": [0, 50, 0],
        "mount_points": [
            {"id": "hip_mount", "mounted_to": "mg996r_fl_hip:body", "fastener": "M3×4"},
        ],
    })
    # 配套 bom 节点(否则 _validate 会报"引用不存在")
    _write_bom(proj, [
        {
            "id": "mg996r_fl_hip", "category": "actuator", "name": "MG996R",
            "qty": 1, "unit_price": 28, "total": 28,
            "vendors": [{"name": "A", "url": "", "price_cny": 1, "tier": "pro"},
                        {"name": "B", "url": "", "price_cny": 1, "tier": "budget"}],
            "cross_domain": True,
            "interfaces": [{"id": "body", "kind": "mechanical"}],
        },
    ])
    doc = mc.merge(proj)
    cad_nodes = [n for n in doc["nodes"] if n["kind"] == "cad_part"]
    assert len(cad_nodes) == 1
    assert cad_nodes[0]["id"] == "leg_fl_thigh"
    # 自动生成跨域机械边
    mech_edges = [e for e in doc["edges"] if e["kind"] == "mechanical"]
    assert len(mech_edges) == 1
    assert mech_edges[0]["from"] == "mg996r_fl_hip:body"
    assert mech_edges[0]["to"] == "leg_fl_thigh:hip_mount"
    assert mech_edges[0]["label"] == "M3×4"


def test_wiring_json_to_data_power_edges(proj):
    _write_bom(proj, [
        {"id": "esp32_main", "category": "microcontroller", "name": "ESP",
         "qty": 1, "unit_price": 1, "total": 1,
         "vendors": [{"name": "A", "url": "", "price_cny": 1, "tier": "pro"},
                     {"name": "B", "url": "", "price_cny": 1, "tier": "budget"}],
         "interfaces": [{"id": "GPIO13", "kind": "data"}, {"id": "VIN", "kind": "power"}]},
        {"id": "battery", "category": "power", "name": "Bat",
         "qty": 1, "unit_price": 1, "total": 1,
         "vendors": [{"name": "A", "url": "", "price_cny": 1, "tier": "pro"},
                     {"name": "B", "url": "", "price_cny": 1, "tier": "budget"}],
         "interfaces": [{"id": "positive", "kind": "power"}]},
        {"id": "mg996r", "category": "actuator", "name": "Servo",
         "qty": 1, "unit_price": 1, "total": 1,
         "vendors": [{"name": "A", "url": "", "price_cny": 1, "tier": "pro"},
                     {"name": "B", "url": "", "price_cny": 1, "tier": "budget"}],
         "interfaces": [{"id": "signal", "kind": "data"}]},
    ])
    _write_wiring(proj, [
        {"from": "esp32_main:GPIO13", "to": "mg996r:signal", "kind": "data", "label": "PWM"},
        {"from": "battery:positive", "to": "esp32_main:VIN", "kind": "power", "label": "+12V"},
    ])
    doc = mc.merge(proj)
    kinds = [e["kind"] for e in doc["edges"]]
    assert kinds.count("data") == 1
    assert kinds.count("power") == 1
    pwm = next(e for e in doc["edges"] if e["kind"] == "data")
    assert pwm["label"] == "PWM"


# ── 校验 ──────────────────────────────────────────────────────────────

def test_validate_detects_duplicate_node_id():
    nodes = [
        {"id": "x", "interfaces": [{"id": "a", "kind": "data"}]},
        {"id": "x", "interfaces": [{"id": "b", "kind": "data"}]},
    ]
    errs = mc._validate(nodes, [])
    assert any("重复" in e for e in errs)


def test_validate_detects_unknown_interface_in_edge():
    nodes = [{"id": "a", "interfaces": [{"id": "p1", "kind": "data"}]}]
    edges = [{"id": "e1", "kind": "data", "from": "a:p1", "to": "a:nope"}]
    errs = mc._validate(nodes, edges)
    assert any("nope" in e and "不在" in e for e in errs)


def test_validate_detects_invalid_edge_kind():
    nodes = [{"id": "a", "interfaces": [{"id": "p", "kind": "data"}]}]
    edges = [{"id": "e1", "kind": "wireless", "from": "a:p", "to": "a:p"}]
    errs = mc._validate(nodes, edges)
    assert any("kind 非法" in e for e in errs)


def test_validate_passes_on_clean_doc():
    nodes = [
        {"id": "a", "interfaces": [{"id": "p1", "kind": "data"}]},
        {"id": "b", "interfaces": [{"id": "q1", "kind": "data"}]},
    ]
    edges = [{"id": "e1", "kind": "data", "from": "a:p1", "to": "b:q1"}]
    assert mc._validate(nodes, edges) == []


# ── 端到端 ──────────────────────────────────────────────────────────────

def test_full_main_writes_connectivity_json(proj):
    _write_bom(proj, [
        {"id": "esp32_main", "category": "microcontroller", "name": "ESP",
         "qty": 1, "unit_price": 1, "total": 1,
         "vendors": [{"name": "A", "url": "", "price_cny": 1, "tier": "pro"},
                     {"name": "B", "url": "", "price_cny": 1, "tier": "budget"}],
         "interfaces": [{"id": "GPIO13", "kind": "data"}]},
    ])
    rc = mc.main(["mc", str(proj)])
    assert rc == 0
    out = proj / "connectivity.json"
    assert out.exists()
    written = json.loads(out.read_text())
    assert written["nodes"][0]["id"] == "esp32_main"
