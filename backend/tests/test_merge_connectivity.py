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


# ── 扩展节点(algorithm / pcb / firmware) ──────────────────────────────────

def test_pcb_node_attaches_to_bom_components(proj):
    """domains/electronics/*.kicad_pcb → 1 个 PCB 节点 + 边连承载的 BOM 元件"""
    _write_bom(proj, [
        {"id": "esp32_main", "category": "microcontroller", "name": "ESP32",
         "qty": 1, "unit_price": 1, "total": 1,
         "vendors": [{"name": "A", "url": "", "price_cny": 1, "tier": "pro"},
                     {"name": "B", "url": "", "price_cny": 1, "tier": "budget"}],
         "interfaces": [{"id": "GPIO13", "kind": "data"}]},
        {"id": "imu", "category": "sensor", "name": "IMU",
         "qty": 1, "unit_price": 1, "total": 1,
         "vendors": [{"name": "A", "url": "", "price_cny": 1, "tier": "pro"},
                     {"name": "B", "url": "", "price_cny": 1, "tier": "budget"}],
         "interfaces": [{"id": "sda", "kind": "data"}]},
    ])
    (proj / "domains/electronics/leg-driver.kicad_pcb").write_text("(kicad_pcb ...)")

    doc = mc.merge(proj)
    pcbs = [n for n in doc["nodes"] if n["kind"] == "pcb"]
    assert len(pcbs) == 1
    assert pcbs[0]["id"] == "pcb_leg_driver"
    assert pcbs[0]["owner"] == "hardware"
    # PCB 接 2 个元件 → 2 个 mechanical 边
    pcb_edges = [e for e in doc["edges"] if e["from"].startswith("pcb_leg_driver:")]
    assert len(pcb_edges) == 2
    # 元件被加了 board_seat 端口
    esp32 = next(n for n in doc["nodes"] if n["id"] == "esp32_main")
    assert any(i["id"] == "board_seat" for i in esp32["interfaces"])


def test_algorithm_nodes_from_algo_dir(proj):
    """domains/firmware/algo/*.py → 算法节点(每文件一个),__init__.py 跳过"""
    algo_dir = proj / "domains/firmware/algo"
    algo_dir.mkdir(parents=True, exist_ok=True)
    (algo_dir / "ik_2dof.py").write_text("def ik(...): pass")
    (algo_dir / "fk_2dof.py").write_text("def fk(...): pass")
    (algo_dir / "__init__.py").write_text("")
    (algo_dir / "_helpers.py").write_text("# private")  # 下划线开头跳过

    doc = mc.merge(proj)
    algos = [n for n in doc["nodes"] if n["kind"] == "algorithm"]
    assert {n["id"] for n in algos} == {"algo_ik_2dof", "algo_fk_2dof"}
    for a in algos:
        assert a["owner"] == "algorithm"
        # 应有 input/output 两个端口
        iface_ids = {i["id"] for i in a["interfaces"]}
        assert iface_ids == {"input", "output"}


def test_firmware_node_from_src_files(proj):
    """domains/firmware/src/*.c → 1 个 firmware 节点 + 接 mcu 控制流边"""
    src = proj / "domains/firmware/src"
    src.mkdir(parents=True, exist_ok=True)
    (src / "leg_pwm.c").write_text("/* main */")
    # 加配套 BOM 让 esp32_main:GPIO13 存在
    _write_bom(proj, [
        {"id": "esp32_main", "category": "microcontroller", "name": "ESP",
         "qty": 1, "unit_price": 1, "total": 1,
         "vendors": [{"name": "A", "url": "", "price_cny": 1, "tier": "pro"},
                     {"name": "B", "url": "", "price_cny": 1, "tier": "budget"}],
         "interfaces": [{"id": "GPIO13", "kind": "data"}]},
    ])
    doc = mc.merge(proj)
    fws = [n for n in doc["nodes"] if n["kind"] == "firmware"]
    assert len(fws) == 1
    assert fws[0]["id"] == "fw_main"
    assert fws[0]["owner"] == "firmware"
    # firmware → mcu 控制流边
    deploy_edges = [e for e in doc["edges"] if e["from"] == "fw_main:to_mcu"]
    assert len(deploy_edges) == 1
    assert deploy_edges[0]["to"] == "esp32_main:GPIO13"


def test_algo_to_fw_edge_when_both_exist(proj):
    """algorithm 输出 → fw_main:algo_in;若没 firmware 节点,这条边被剥掉(防引用悬空)"""
    algo_dir = proj / "domains/firmware/algo"
    algo_dir.mkdir(parents=True, exist_ok=True)
    (algo_dir / "ik.py").write_text("# ik")
    src = proj / "domains/firmware/src"
    src.mkdir(parents=True, exist_ok=True)
    (src / "main.c").write_text("/* */")
    _write_bom(proj, [
        {"id": "esp32_main", "category": "microcontroller", "name": "ESP",
         "qty": 1, "unit_price": 1, "total": 1,
         "vendors": [{"name": "A", "url": "", "price_cny": 1, "tier": "pro"},
                     {"name": "B", "url": "", "price_cny": 1, "tier": "budget"}],
         "interfaces": [{"id": "GPIO13", "kind": "data"}]},
    ])
    doc = mc.merge(proj)
    # algorithm 节点存在,fw_main 存在 → 中间有边
    algo_to_fw = [e for e in doc["edges"] if e["from"].startswith("algo_") and e["to"].startswith("fw_main:")]
    assert len(algo_to_fw) == 1
    assert algo_to_fw[0]["kind"] == "data"


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
