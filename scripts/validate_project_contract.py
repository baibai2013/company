#!/usr/bin/env python3
"""B2 patch §3 — 项目产物契约校验。

用法:
    python scripts/validate_project_contract.py ~/work/robot-dog/

返回:
    exit 0 = 全合规
    exit 1 = 有违规,stdout 列每条

5 项检查(对照 B2 §14 验收清单):
1. manifest.json 三必备字段(tags / hero_image / summary.cost_by_category 完整)
2. domains/electronics/bom.json 12 类强枚举 + vendors[]≥2
3. assembly.json 5 phase 齐全 + tools/assumptions
4. manifest.assembly.parts[].glb 文件真实存在
5. KiCad 源 *.kicad_sch → cad/exports/*.svg 配对
"""
from __future__ import annotations

import json
import sys
from pathlib import Path


VALID_BOM_CATEGORIES = {
    "microcontroller", "sensor", "actuator", "power", "module", "display",
    "structural", "enclosure", "mechanism", "hardware", "3D-printed", "generic",
}
REQUIRED_PHASES = ["Fabricate", "Wire", "Assemble", "Program", "Calibrate"]
REQUIRED_MANIFEST_FIELDS = ("tags", "hero_image", "summary")
REQUIRED_COST_KEYS = ("electrical", "mechanical", "total")


def _load_json(p: Path):
    return json.loads(p.read_text(encoding="utf-8"))


def check_manifest(root: Path, errors: list[str]) -> dict | None:
    manifest_path = root / "manifest.json"
    if not manifest_path.exists():
        errors.append("manifest.json 不存在")
        return None
    try:
        m = _load_json(manifest_path)
    except Exception as exc:
        errors.append(f"manifest.json 解析失败: {exc}")
        return None
    for f in REQUIRED_MANIFEST_FIELDS:
        if f not in m:
            errors.append(f"manifest.json 缺字段: {f}")
    summary = m.get("summary", {})
    cost = summary.get("cost_by_category", {})
    for k in REQUIRED_COST_KEYS:
        if k not in cost:
            errors.append(f"manifest.summary.cost_by_category 缺: {k}")
    if not isinstance(m.get("tags", []), list) or len(m.get("tags", [])) == 0:
        errors.append("manifest.tags[] 必须非空 list")
    return m


def check_bom(root: Path, errors: list[str]) -> None:
    """domains/electronics/bom.json(新结构)或 bom/leg-cost.json(旧结构)。"""
    candidates = [
        root / "domains" / "electronics" / "bom.json",
        root / "bom" / "leg-cost.json",
    ]
    bom_path = next((p for p in candidates if p.exists()), None)
    if bom_path is None:
        errors.append(f"BOM 未找到 (查过: {[str(c.relative_to(root)) for c in candidates]})")
        return
    try:
        bom = _load_json(bom_path)
    except Exception as exc:
        errors.append(f"{bom_path.name} 解析失败: {exc}")
        return
    items = bom.get("items", [])
    if not items:
        errors.append(f"{bom_path.name} items[] 为空")
    for i, item in enumerate(items):
        cat = item.get("category")
        if cat not in VALID_BOM_CATEGORIES:
            errors.append(
                f"{bom_path.name} items[{i}].category 非法: {cat!r} "
                f"(必须为 12 类之一)"
            )
        vendors = item.get("vendors", [])
        if len(vendors) < 2:
            errors.append(
                f"{bom_path.name} items[{i}].vendors 长度 {len(vendors)} < 2 "
                f"(name={item.get('name')})"
            )


def check_assembly(root: Path, errors: list[str]) -> None:
    p = root / "assembly.json"
    if not p.exists():
        errors.append("assembly.json 不存在")
        return
    try:
        a = _load_json(p)
    except Exception as exc:
        errors.append(f"assembly.json 解析失败: {exc}")
        return
    for f in ("tools", "assumptions"):
        if f not in a:
            errors.append(f"assembly.json 缺顶层字段: {f}")
    phase_names = [p.get("name") for p in a.get("phases", [])]
    for required in REQUIRED_PHASES:
        if required not in phase_names:
            errors.append(f"assembly.json 缺 phase: {required}")


def check_part_files(root: Path, manifest: dict | None, errors: list[str]) -> None:
    if not manifest:
        return
    for part in manifest.get("assembly", {}).get("parts", []):
        glb = part.get("glb", "")
        if not glb:
            errors.append(f"part {part.get('id')} 缺 glb 字段")
            continue
        if not (root / glb).exists():
            errors.append(f"part.glb 文件缺失: {glb}")


def check_kicad_pairing(root: Path, errors: list[str]) -> None:
    """每个 *.kicad_sch 应有同名 *-sch.svg(在 cad/exports/ 或同目录)。"""
    elec = root / "domains" / "electronics"
    if not elec.exists():
        return
    for sch in elec.rglob("*.kicad_sch"):
        stem = sch.stem  # e.g. "leg-driver"
        candidates = [
            elec / "cad" / "exports" / f"{stem}-sch.svg",
            sch.parent / f"{stem}-sch.svg",
        ]
        if not any(c.exists() for c in candidates):
            errors.append(
                f"KiCad 源 {sch.relative_to(root)} 没有 SVG 渲染件 "
                f"(应在 cad/exports/{stem}-sch.svg)"
            )


def main(argv: list[str]) -> int:
    if len(argv) < 2:
        print("usage: validate_project_contract.py <project_root>", file=sys.stderr)
        return 2
    root = Path(argv[1]).expanduser().resolve()
    if not root.exists():
        print(f"❌ 项目根不存在: {root}", file=sys.stderr)
        return 2

    errors: list[str] = []
    manifest = check_manifest(root, errors)
    check_bom(root, errors)
    check_assembly(root, errors)
    check_part_files(root, manifest, errors)
    check_kicad_pairing(root, errors)

    if errors:
        print(f"❌ {len(errors)} 项不合规 ({root}):")
        for e in errors:
            print(f"   • {e}")
        return 1
    print(f"✅ contract OK ({root})")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
