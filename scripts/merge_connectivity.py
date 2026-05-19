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

    nodes = bom_nodes + mech_nodes
    edges = wire_edges + mech_edges
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
