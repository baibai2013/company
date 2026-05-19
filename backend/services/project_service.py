"""B2 — 项目目录扫描 / manifest 兜底 / 文件类型识别。

doc/design/B2-showcase-frontend.md §3.2 / §3.3。

PROJECTS_ROOT 通过环境变量可改,默认 ~/work/projects。
路径越界保护以 _resolve_safe 为唯一入口。
"""
from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from fastapi import HTTPException

from backend.schemas.project import (
    Assembly, AssemblyDoc, AssemblyPart, BomDoc, BomItem, BomSummary,
    Deliverable, FileKind, ManifestRead, ManifestSummary, ProjectListItem,
    TreeNode,
)

log = logging.getLogger(__name__)


PROJECTS_ROOT = Path(
    os.environ.get("PROJECTS_ROOT", str(Path.home() / "work" / "projects"))
).resolve()


# ── 文件类型识别 ────────────────────────────────────────────────────────────

KIND_BY_EXT: dict[str, FileKind] = {
    # CAD
    ".step": "cad", ".stp": "cad",
    ".glb": "model3d", ".gltf": "model3d", ".stl": "model3d",
    # EDA
    ".kicad_sch": "schematic_src", ".kicad_pcb": "pcb_src", ".kicad_pro": "kicad_project",
    ".sch": "schematic_src", ".brd": "pcb_src",
    ".gerber": "gerber", ".zip": "archive",
    ".net": "netlist", ".csv": "csv",
    # 文档 / 代码
    ".md": "markdown", ".pdf": "pdf",
    ".c": "code", ".h": "code", ".cpp": "code", ".py": "code",
    ".rs": "code", ".js": "code", ".ts": "code",
    ".json": "json", ".yaml": "yaml", ".yml": "yaml",
    # 图片
    ".png": "image", ".jpg": "image", ".jpeg": "image", ".svg": "image",
}


def detect_kind(path: Path) -> FileKind:
    if path.is_dir():
        return "dir"
    return KIND_BY_EXT.get(path.suffix.lower(), "binary")


# ── 路径安全 ────────────────────────────────────────────────────────────────

def project_root(name: str) -> Path:
    """返回项目根。不要把这个直接给 _resolve_safe 当 root,会丢符号链接保护。"""
    safe = name.strip("/").replace("..", "")  # 防 traversal in 项目名
    return (PROJECTS_ROOT / safe).resolve()


def _resolve_safe(project: str, rel: str) -> Path:
    root = project_root(project)
    if not root.exists():
        raise HTTPException(404, f"project '{project}' not found")
    # 绝对路径直接拒绝(避免 lstrip 后还能解析到无关目录)
    if rel.startswith("/") or (len(rel) >= 2 and rel[1] == ":"):
        raise HTTPException(403, "absolute path not allowed")
    try:
        target = (root / rel).resolve()
    except (OSError, ValueError):
        raise HTTPException(403, "invalid path")
    if target != root and not str(target).startswith(str(root) + os.sep):
        raise HTTPException(403, "path escapes project root")
    return target


# ── 项目枚举 ────────────────────────────────────────────────────────────────

def list_projects() -> list[ProjectListItem]:
    """扫 PROJECTS_ROOT 下所有含 manifest.json 或 charter.md 的目录。"""
    if not PROJECTS_ROOT.exists():
        return []
    out: list[ProjectListItem] = []
    for child in sorted(PROJECTS_ROOT.iterdir()):
        if not child.is_dir() or child.name.startswith("."):
            continue
        manifest = child / "manifest.json"
        charter = child / "charter.md"
        if not manifest.exists() and not charter.exists():
            continue
        title = ""
        parts_count = 0
        updated = None
        if manifest.exists():
            try:
                m = json.loads(manifest.read_text())
                title = m.get("name", "")
                parts_count = len(m.get("assembly", {}).get("parts", []))
                updated = manifest.stat().st_mtime
            except Exception:
                updated = manifest.stat().st_mtime
        elif charter.exists():
            updated = charter.stat().st_mtime
            try:
                first_line = charter.read_text(errors="ignore").splitlines()[0]
                title = first_line.lstrip("# ").strip()
            except Exception:
                pass
        out.append(ProjectListItem(
            name=child.name,
            title=title,
            has_manifest=manifest.exists(),
            has_charter=charter.exists(),
            parts_count=parts_count,
            updated_at=datetime.fromtimestamp(updated, tz=timezone.utc) if updated else None,
        ))
    return out


# ── manifest ──────────────────────────────────────────────────────────────────

_DEFAULT_OWNERS = {
    "prd": "product_manager",
    "cad": "mechanical",
    "schematic": "hardware",
    "pcb": "hardware",
    "firmware": "firmware",
    "algorithm": "algorithm",
    "bom": "cost",
    "test_report": "testing",
    "video": "testing",
    "urdf": "algorithm",
    "sim_video": "algorithm",
}


def load_manifest(project: str) -> ManifestRead:
    """读 manifest.json。不存在时按目录扫描兜底产生一份(fallback=True)。"""
    root = project_root(project)
    if not root.exists():
        raise HTTPException(404, f"project '{project}' not found")

    manifest_path = root / "manifest.json"
    if manifest_path.exists():
        try:
            data = json.loads(manifest_path.read_text())
            data.setdefault("project", project)
            m = ManifestRead(**data)
            m.fallback = False
            _check_part_files(m, root)
            return m
        except Exception as exc:
            log.warning("manifest parse failed for %s: %s, falling back to scan",
                        project, exc)

    return _scan_fallback(project, root)


def _check_part_files(m: ManifestRead, root: Path) -> None:
    """对 assembly.parts[] 检查 .glb / .step 是否存在,设置 cad_only / missing。"""
    for p in m.assembly.parts:
        glb_ok = bool(p.glb) and (root / p.glb).exists()
        step_ok = bool(p.step) and (root / p.step).exists()
        p.cad_only = step_ok and not glb_ok
        p.missing = not glb_ok and not step_ok


def _scan_fallback(project: str, root: Path) -> ManifestRead:
    """目录扫描:按约定子目录推断 deliverables。assembly 部分按 parts/*.glb 扫。"""
    deliverables: list[Deliverable] = []

    if (root / "prd" / "leg-2dof.md").exists():
        deliverables.append(Deliverable(kind="prd", path="prd/leg-2dof.md",
                                        owner=_DEFAULT_OWNERS["prd"]))
    elif (root / "prd").exists():
        for f in (root / "prd").glob("*.md"):
            deliverables.append(Deliverable(kind="prd", path=f"prd/{f.name}",
                                            owner=_DEFAULT_OWNERS["prd"]))

    if (root / "parts").exists():
        deliverables.append(Deliverable(kind="cad", path="parts/",
                                        owner=_DEFAULT_OWNERS["cad"]))

    if (root / "electronics").exists():
        sch_svg = next((root / "electronics").glob("*-sch.svg"), None)
        if sch_svg:
            deliverables.append(Deliverable(
                kind="schematic", path=f"electronics/{sch_svg.name}",
                owner=_DEFAULT_OWNERS["schematic"],
            ))
        pcb_top = next((root / "electronics").glob("*-pcb-top.svg"), None)
        if pcb_top:
            deliverables.append(Deliverable(
                kind="pcb", path=f"electronics/{pcb_top.name}",
                owner=_DEFAULT_OWNERS["pcb"],
            ))

    for sub_kind, sub_dir in (
        ("firmware", "firmware"),
        ("algorithm", "algorithm"),
    ):
        sub = root / sub_dir
        if sub.exists():
            for f in sub.iterdir():
                if f.is_file() and f.suffix in (".c", ".h", ".cpp", ".py"):
                    deliverables.append(Deliverable(
                        kind=sub_kind, path=f"{sub_dir}/{f.name}",
                        owner=_DEFAULT_OWNERS[sub_kind],
                    ))
                    break

    bom = root / "bom" / "leg-cost.json"
    if bom.exists():
        deliverables.append(Deliverable(kind="bom", path="bom/leg-cost.json",
                                        owner=_DEFAULT_OWNERS["bom"]))

    parts = []
    parts_dir = root / "parts"
    if parts_dir.exists():
        for glb in sorted(parts_dir.glob("*.glb")):
            stem = glb.stem
            step = parts_dir / f"{stem}.step"
            parts.append(AssemblyPart(
                id=stem, name=stem, glb=f"parts/{glb.name}",
                step=f"parts/{step.name}" if step.exists() else "",
                owner=_DEFAULT_OWNERS["cad"],
            ))

    name = project
    title = project
    charter = root / "charter.md"
    if charter.exists():
        try:
            first = charter.read_text(errors="ignore").splitlines()[0]
            title = first.lstrip("# ").strip() or project
        except Exception:
            pass

    summary = ManifestSummary(parts_count=len(parts))
    bom_doc = _try_load_bom(root)
    if bom_doc:
        summary.cost_by_category = bom_doc.summary.by_category
        summary.currency = bom_doc.currency

    updated = None
    if (root / "manifest.json").exists():
        updated = (root / "manifest.json").stat().st_mtime
    elif parts:
        updated = max((parts_dir / Path(p.glb).name).stat().st_mtime for p in parts)
    elif charter.exists():
        updated = charter.stat().st_mtime

    m = ManifestRead(
        project=project, name=title,
        updated_at=datetime.fromtimestamp(updated, tz=timezone.utc) if updated else None,
        summary=summary,
        assembly=Assembly(parts=parts),
        deliverables=deliverables,
        fallback=True,
    )
    _check_part_files(m, root)
    return m


def _try_load_bom(root: Path) -> BomDoc | None:
    bom_path = root / "bom" / "leg-cost.json"
    if not bom_path.exists():
        return None
    try:
        data = json.loads(bom_path.read_text())
        return BomDoc(**data)
    except Exception as exc:
        log.warning("bom parse failed at %s: %s", bom_path, exc)
        return None


def load_bom(project: str) -> BomDoc:
    """严格读 BOM:文件不存在 → 404,解析失败 → 500。"""
    root = project_root(project)
    bom_path = root / "bom" / "leg-cost.json"
    if not bom_path.exists():
        raise HTTPException(404, "bom file not found at bom/leg-cost.json")
    try:
        return BomDoc(**json.loads(bom_path.read_text()))
    except Exception as exc:
        raise HTTPException(500, f"bom parse error: {exc}")


def load_assembly_doc(project: str) -> AssemblyDoc:
    root = project_root(project)
    p = root / "assembly.json"
    if not p.exists():
        raise HTTPException(404, "assembly.json not found")
    try:
        return AssemblyDoc(**json.loads(p.read_text()))
    except Exception as exc:
        raise HTTPException(500, f"assembly.json parse error: {exc}")


# ── tree ──────────────────────────────────────────────────────────────────────

_HIDDEN_PREFIXES = (".", "__pycache__")


def build_tree(project: str, max_depth: int = 4) -> list[TreeNode]:
    root = project_root(project)
    if not root.exists():
        raise HTTPException(404, f"project '{project}' not found")

    def walk(p: Path, depth: int) -> list[TreeNode]:
        if depth > max_depth:
            return []
        out: list[TreeNode] = []
        try:
            entries = sorted(p.iterdir(), key=lambda x: (not x.is_dir(), x.name.lower()))
        except PermissionError:
            return out
        for child in entries:
            if any(child.name.startswith(prefix) for prefix in _HIDDEN_PREFIXES):
                continue
            rel = str(child.relative_to(root))
            stat = child.stat()
            node = TreeNode(
                path=rel,
                kind=detect_kind(child),
                size=stat.st_size if child.is_file() else 0,
                mtime=datetime.fromtimestamp(stat.st_mtime, tz=timezone.utc),
            )
            if child.is_dir():
                node.children = walk(child, depth + 1)
            out.append(node)
        return out

    return walk(root, 0)


# ── ETag helpers ─────────────────────────────────────────────────────────────

def etag_for_path(path: Path) -> str:
    """简易 ETag:size + mtime ns。"""
    if not path.exists():
        return "0"
    s = path.stat()
    return f'W/"{s.st_size:x}-{int(s.st_mtime_ns):x}"'


def etag_for_dir(root: Path, candidates: list[str]) -> str:
    """给目录算 ETag:对一组关键文件 mtime/size 哈希。"""
    parts: list[str] = []
    for rel in candidates:
        p = root / rel
        if p.exists():
            s = p.stat()
            parts.append(f"{rel}:{s.st_size}:{s.st_mtime_ns}")
    return f'W/"{abs(hash("|".join(parts))):x}"'
