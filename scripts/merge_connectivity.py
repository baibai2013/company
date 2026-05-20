#!/usr/bin/env python3
"""B2-connectivity-view §2.4 — 合并各 domain 产物为 connectivity.json。

数据来源:
1. domains/electronics/bom.json      → 电子节点(每行 1 个 node,以 item.id 为键)
2. domains/mechanical/parts/*.json   → CAD 节点 + 跨域机械边(mount_points.mounted_to)
3. domains/firmware/wiring.json      → data/power 边(GPIO 接 → 信号 / 电源轨)

产出:
- 写到 <project_root>/connectivity.json

校验(_validate):
- node id 全局唯一
- edge.from / to 必须解析为合法 node:interface
- edge.kind ∈ {mechanical, power, data}

用法:
    python scripts/merge_connectivity.py ~/work/robot-dog/

返回 0=成功;非 0=校验失败(stdout 列违规)。
"""
from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path

VALID_EDGE_KINDS = {"mechanical", "power", "data"}

# 12 类 BOM category → connectivity node kind 映射
CATEGORY_TO_KIND = {
    "microcontroller": "mcu",
    "sensor": "sensor",
    "actuator": "actuator",
    "power": "power",
    "module": "module",
    "display": "display",
    "structural": "generic",
    "enclosure": "generic",
    "mechanism": "generic",
    "hardware": "generic",
    "3D-printed": "generic",
    "generic": "generic",
}


def _kind_from_category(category: str) -> str:
    return CATEGORY_TO_KIND.get(category, "generic")


def _load_bom_nodes(root: Path) -> list[dict]:
    bom_path = root / "domains" / "electronics" / "bom.json"
    if not bom_path.exists():
        return []
    bom = json.loads(bom_path.read_text())
    nodes = []
    for i, item in enumerate(bom.get("items", [])):
        if "id" not in item:
            print(f"⚠  bom.items[{i}] 缺 id 字段,跳过 ({item.get('name','?')})")
            continue
        node = {
            "id": item["id"],
            "kind": _kind_from_category(item.get("category", "generic")),
            "label": item.get("name", item["id"]),
            "domain": "electronics",
            "owner": item.get("owner", "hardware"),
            "owner_label": "大法师",
            "ref": {
                "bom": f"bom.json#items/{i}",
                "datasheet": item.get("datasheet"),
            },
            "interfaces": item.get("interfaces", []),
        }
        if item.get("cross_domain"):
            node["kind"] = "actuator_cross_domain"
            if "cad_model" in item:
                node["ref"]["cad_model"] = item["cad_model"]
        nodes.append(node)
    return nodes


def _load_mechanical_nodes_and_edges(root: Path) -> tuple[list[dict], list[dict]]:
    parts_dir = root / "domains" / "mechanical" / "parts"
    if not parts_dir.exists():
        return [], []
    nodes, edges = [], []
    for pj in sorted(parts_dir.glob("*.json")):
        try:
            meta = json.loads(pj.read_text())
        except Exception as exc:
            print(f"⚠  parts/{pj.name} 解析失败: {exc}")
            continue
        if "id" not in meta:
            print(f"⚠  parts/{pj.name} 缺 id 字段,跳过")
            continue
        nodes.append({
            "id": meta["id"],
            "kind": "cad_part",
            "label": meta.get("name", meta["id"]),
            "domain": "mechanical",
            "owner": meta.get("owner", "mechanical"),
            "owner_label": "Dave",
            "ref": {
                "step": f"domains/mechanical/parts/{pj.stem}.step",
                "glb":  f"domains/mechanical/parts/{pj.stem}.glb",
                "part_meta": str(pj.relative_to(root)),
            },
            "interfaces": [
                {"id": mp["id"], "kind": "mechanical"}
                for mp in meta.get("mount_points", [])
            ],
        })
        # 跨域机械边: mount_points[].mounted_to → 自动生成 edge
        for mp in meta.get("mount_points", []):
            if "mounted_to" in mp:
                edges.append({
                    "id": f"e_mech_{len(edges):03d}",
                    "from": mp["mounted_to"],            # "mg996r_fl_hip:body"
                    "to":   f"{meta['id']}:{mp['id']}",  # "leg_fl_thigh:hip_mount"
                    "kind": "mechanical",
                    "label": mp.get("fastener", ""),
                })
    return nodes, edges


def _load_wiring_edges(root: Path) -> list[dict]:
    p = root / "domains" / "firmware" / "wiring.json"
    if not p.exists():
        return []
    try:
        wiring = json.loads(p.read_text())
    except Exception as exc:
        print(f"⚠  firmware/wiring.json 解析失败: {exc}")
        return []
    edges = []
    for w in wiring.get("connections", []):
        e = {
            "id": f"e_wire_{len(edges):03d}",
            "from": w["from"],
            "to": w["to"],
            "kind": w.get("kind", "data"),
        }
        if w.get("label"):
            e["label"] = w["label"]
        if w.get("data_subtype"):
            e["data_subtype"] = w["data_subtype"]
        edges.append(e)
    return edges


def _load_pcb_node(root: Path, bom_nodes: list[dict]) -> tuple[list[dict], list[dict]]:
    """扫 domains/electronics/*.kicad_pcb → 1 个 PCB 板节点 + 边连承载的 BOM 元件。

    PCB 是 mcu/sensor/power/module 等元件的物理载体,作为单独节点能让"哪些元件焊在
    同一块板上"在 connectivity 视图里直观可见(B2-connectivity-view 反馈 §A 加 PCB)。
    """
    elec = root / "domains" / "electronics"
    if not elec.exists():
        return [], []
    pcb_files = list(elec.glob("*.kicad_pcb"))
    if not pcb_files:
        return [], []

    pcb_path = pcb_files[0]
    pcb_id = f"pcb_{pcb_path.stem.replace('-', '_')}"
    # PCB 承载的元件 = bom_nodes 里的 mcu/sensor/power/module/display/generic
    # (actuator_cross_domain 是机械固定的不焊在 PCB 上)
    PCB_BORN_KINDS = {"mcu", "sensor", "power", "module", "display", "generic"}
    born_nodes = [n for n in bom_nodes if n["kind"] in PCB_BORN_KINDS]

    # PCB 自身 interfaces:每个承载元件一个机械固定端口(seat_<id>)
    pcb_interfaces = [{"id": f"seat_{n['id']}", "kind": "mechanical"} for n in born_nodes]

    pcb_node = {
        "id": pcb_id,
        "kind": "pcb",
        "label": pcb_path.stem.replace("-", " ").title() + " PCB",
        "domain": "electronics",
        "owner": "hardware",
        "owner_label": "大法师",
        "ref": {
            "schematic_block": str(pcb_path.relative_to(root)),
            "step": str((elec / "cad" / "exports" / f"{pcb_path.stem}.step").relative_to(root))
                    if (elec / "cad" / "exports" / f"{pcb_path.stem}.step").exists() else None,
        },
        "interfaces": pcb_interfaces,
    }

    # 边:PCB → 每个承载元件的"机械承载"链接(用 mechanical kind,但不是真实 mount)
    # 为了让承载元件能挂上去,我们在每个承载元件添加一个 board_seat interface
    edges = []
    for n in born_nodes:
        # 在元件 interfaces 末尾追加一个 board_seat 端口(merge 一次性写,不影响原 BOM 文件)
        if not any(i["id"] == "board_seat" for i in n["interfaces"]):
            n["interfaces"].append({"id": "board_seat", "kind": "mechanical"})
        edges.append({
            "id": f"e_pcb_{len(edges):03d}",
            "from": f"{pcb_id}:seat_{n['id']}",
            "to":   f"{n['id']}:board_seat",
            "kind": "mechanical",
            "label": "PCB seat (SMD/THT)",
        })
    return [pcb_node], edges


def _load_algorithm_nodes(root: Path) -> tuple[list[dict], list[dict]]:
    """扫 domains/firmware/algo/*.py → 算法节点(每个 *.py 一个),边连 firmware。

    算法节点 interfaces:
      - input:target  (上游输入,用于接 PRD 的目标位姿/步态指令)
      - output:angles (下游输出,接 firmware 写舵机)
    """
    algo_dir = root / "domains" / "firmware" / "algo"
    if not algo_dir.exists():
        return [], []
    nodes, edges = [], []
    for py in sorted(algo_dir.glob("*.py")):
        if py.name.startswith("_") or py.name == "__init__.py":
            continue
        algo_id = f"algo_{py.stem}"
        nodes.append({
            "id": algo_id,
            "kind": "algorithm",
            "label": f"{py.stem.replace('_', ' ').upper()} Solver",
            "domain": "firmware",
            "owner": "algorithm",
            "owner_label": "喵喵球",
            "ref": {
                "part_meta": str(py.relative_to(root)),
            },
            "interfaces": [
                {"id": "input",  "kind": "data"},  # 接外部目标
                {"id": "output", "kind": "data"},  # 输出到 firmware
            ],
        })
        # 边:algorithm.output → fw_main.input(假设 firmware 节点 id=fw_main)
        edges.append({
            "id": f"e_algo_{len(edges):03d}",
            "from": f"{algo_id}:output",
            "to":   "fw_main:algo_in",
            "kind": "data",
            "label": f"{py.stem} angles",
        })
    return nodes, edges


def _load_firmware_node(root: Path) -> tuple[list[dict], list[dict]]:
    """firmware 节点(承上启下):接 algorithm 输出 + 写舵机/读 IMU 的 GPIO。

    interfaces:
      - algo_in    (data)  接收算法输出
      - to_mcu     (data)  写 PWM/I2C(实际由 wiring.json 描述具体 GPIO,这里只代表逻辑节点)
    """
    fw_src = root / "domains" / "firmware" / "src"
    if not fw_src.exists():
        return [], []
    src_files = list(fw_src.glob("*.c")) + list(fw_src.glob("*.cpp"))
    if not src_files:
        return [], []
    main_src = src_files[0]
    node = {
        "id": "fw_main",
        "kind": "firmware",
        "label": f"Firmware ({main_src.stem})",
        "domain": "firmware",
        "owner": "firmware",
        "owner_label": "小布丁",
        "ref": {
            "part_meta": str(main_src.relative_to(root)),
        },
        "interfaces": [
            {"id": "algo_in", "kind": "data"},
            {"id": "to_mcu",  "kind": "data"},
        ],
    }
    edges = [{
        "id": "e_fw_to_mcu",
        "from": "fw_main:to_mcu",
        "to":   "esp32_main:GPIO13",  # 控制流逻辑边(实际 GPIO 接由 wiring.json 描述)
        "kind": "data",
        "label": "deploy → MCU",
    }]
    return [node], edges


def _validate(nodes: list[dict], edges: list[dict]) -> list[str]:
    errors: list[str] = []

    # 1. node id 唯一
    seen: set[str] = set()
    iface_index: dict[str, set[str]] = {}
    for n in nodes:
        nid = n.get("id")
        if not nid:
            errors.append(f"node 缺 id: {n}")
            continue
        if nid in seen:
            errors.append(f"node id 重复: {nid}")
        seen.add(nid)
        iface_index[nid] = {iface["id"] for iface in n.get("interfaces", [])}

    # 2. edge.from / to 引用合法 node:interface
    for e in edges:
        if e.get("kind") not in VALID_EDGE_KINDS:
            errors.append(f"edge {e.get('id')} kind 非法: {e.get('kind')}")
        for end in ("from", "to"):
            ep = e.get(end, "")
            if ":" not in ep:
                errors.append(f"edge {e.get('id')} {end}={ep!r} 不含 ':'")
                continue
            node_id, iface = ep.split(":", 1)
            if node_id not in iface_index:
                errors.append(f"edge {e.get('id')} {end} 引用不存在 node: {node_id}")
            elif iface not in iface_index[node_id]:
                errors.append(
                    f"edge {e.get('id')} {end}={ep!r} interface 不在 {node_id} 的 "
                    f"interfaces {sorted(iface_index[node_id])} 中"
                )
    return errors


def merge(root: Path) -> dict:
    bom_nodes = _load_bom_nodes(root)
    mech_nodes, mech_edges = _load_mechanical_nodes_and_edges(root)
    wire_edges = _load_wiring_edges(root)
    # 注意顺序:_load_pcb_node 会就地往 bom_nodes 的 interfaces[] 加 board_seat 端口,
    # 必须在 wire_edges/_validate 之前完成,但 wiring.json 不会引用 board_seat,无冲突
    pcb_nodes, pcb_edges = _load_pcb_node(root, bom_nodes)
    algo_nodes, algo_edges = _load_algorithm_nodes(root)
    fw_nodes, fw_edges = _load_firmware_node(root)

    # algorithm/fw_edges 引用 fw_main / esp32_main 的端点;若 firmware 节点存在则保留,
    # 若没有 firmware 节点则去掉这些边(避免 _validate 报"引用不存在")
    if not fw_nodes:
        algo_edges = []   # 没 firmware 节点就没法接 algo

    nodes = bom_nodes + mech_nodes + pcb_nodes + algo_nodes + fw_nodes
    edges = wire_edges + mech_edges + pcb_edges + algo_edges + fw_edges
    return {
        "version": "1.0",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "nodes": nodes,
        "edges": edges,
    }


def main(argv: list[str]) -> int:
    if len(argv) < 2:
        print("usage: merge_connectivity.py <project_root>", file=sys.stderr)
        return 2
    root = Path(argv[1]).expanduser().resolve()
    if not root.exists():
        print(f"❌ 项目根不存在: {root}", file=sys.stderr)
        return 2

    doc = merge(root)
    errors = _validate(doc["nodes"], doc["edges"])
    if errors:
        print(f"❌ {len(errors)} 项校验失败:")
        for e in errors:
            print(f"   • {e}")
        # 失败时仍写出 doc + merge_failed=true,前端可显示告警
        doc["merge_failed"] = True

    out = root / "connectivity.json"
    out.write_text(json.dumps(doc, indent=2, ensure_ascii=False))
    print(f"✅ wrote {out} ({len(doc['nodes'])} nodes, {len(doc['edges'])} edges)")
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
