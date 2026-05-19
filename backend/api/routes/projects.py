"""B2 项目展示页 5 个 GET 端点。

doc/design/B2-showcase-frontend.md §3.1。

所有路径输入都过 _resolve_safe,防止 ../ traversal 与符号链接逃逸。
tree/manifest/bom 三端点支持 If-None-Match → 304。
file 端点对大文件走 StreamingResponse(§11 风险 5)。
"""
from __future__ import annotations

import logging

from fastapi import APIRouter, Header, HTTPException, Query, Response
from fastapi.responses import FileResponse, StreamingResponse

from backend.schemas.project import (
    AssemblyDoc, BomDoc, ConnectivityDoc, ManifestRead, PipelineSnapshot,
    ProjectListItem, TreeNode,
)
from backend.services.project_service import (
    build_tree, etag_for_dir, etag_for_path, list_projects, load_assembly_doc,
    load_bom, load_connectivity, load_manifest, project_root, _resolve_safe,
)

log = logging.getLogger(__name__)
router = APIRouter(prefix="/api/projects", tags=["projects"])


_MANIFEST_KEY_FILES = ["manifest.json", "charter.md"]


@router.get("", response_model=list[ProjectListItem])
async def get_projects():
    return list_projects()


@router.get("/{project}/manifest", response_model=ManifestRead)
async def get_manifest(
    project: str,
    response: Response,
    if_none_match: str | None = Header(default=None),
):
    root = project_root(project)
    if not root.exists():
        raise HTTPException(404, f"project '{project}' not found")
    etag = etag_for_dir(root, _MANIFEST_KEY_FILES + ["bom/leg-cost.json"])
    if if_none_match == etag:
        return Response(status_code=304)
    response.headers["ETag"] = etag
    return load_manifest(project)


@router.get("/{project}/tree", response_model=list[TreeNode])
async def get_tree(
    project: str,
    response: Response,
    if_none_match: str | None = Header(default=None),
):
    root = project_root(project)
    if not root.exists():
        raise HTTPException(404, f"project '{project}' not found")
    # 对项目根 mtime 取 ETag(tree 改动多由文件增删触发 root mtime)
    etag = etag_for_path(root)
    if if_none_match == etag:
        return Response(status_code=304)
    response.headers["ETag"] = etag
    return build_tree(project)


@router.get("/{project}/file")
async def get_file(project: str, path: str = Query(..., description="项目相对路径")):
    target = _resolve_safe(project, path)
    if not target.exists():
        raise HTTPException(404, f"file not found: {path}")
    if target.is_dir():
        raise HTTPException(400, "path is a directory; use /tree instead")
    # FileResponse 自动处理 Content-Type / Range / 大文件流式
    return FileResponse(str(target))


@router.get("/{project}/bom", response_model=BomDoc)
async def get_bom(
    project: str,
    response: Response,
    if_none_match: str | None = Header(default=None),
):
    root = project_root(project)
    if not root.exists():
        raise HTTPException(404, f"project '{project}' not found")
    etag = etag_for_path(root / "bom" / "leg-cost.json")
    if if_none_match == etag:
        return Response(status_code=304)
    response.headers["ETag"] = etag
    return load_bom(project)


@router.get("/{project}/assembly", response_model=AssemblyDoc)
async def get_assembly(project: str):
    return load_assembly_doc(project)


@router.get("/{project}/connectivity", response_model=ConnectivityDoc)
async def get_connectivity(
    project: str,
    response: Response,
    if_none_match: str | None = Header(default=None),
):
    """返回 connectivity.json(部件互连图,B2-connectivity-view.md)。

    不存在时返回空 doc(空 nodes/edges),前端走"暂无互连数据"占位;
    解析失败时 merge_failed=True,前端可显示告警。
    """
    root = project_root(project)
    if not root.exists():
        raise HTTPException(404, f"project '{project}' not found")
    etag = etag_for_path(root / "connectivity.json")
    if if_none_match == etag:
        return Response(status_code=304)
    response.headers["ETag"] = etag
    return load_connectivity(project)


@router.get("/{project}/pipeline", response_model=PipelineSnapshot)
async def get_pipeline(project: str):
    """从最新一条 task 拼出 Workflow 节点/边。

    数据源策略:
    - 优先:从 manifest.deliverables[].owner 推断节点 + robot_engineering 拓扑
    - 后续:也可读 task_step 表的真实状态/duration 叠加(scope 增加,留 B2.7)

    位置 (0,0),前端 elkjs 算坐标。
    """
    from backend.schemas.project import (
        WorkflowEdge, WorkflowField, WorkflowNode, WorkflowNodeData, WorkflowPort,
    )
    m = load_manifest(project)

    # owner → 头条色(与 doc §6.2.3 一致)
    OWNER_COLORS = {
        "product_manager": "#7c3aed",
        "mechanical":      "#db2777",
        "hardware":        "#06b6d4",
        "firmware":        "#0ea5e9",
        "algorithm":       "#10b981",
        "cost":            "#f59e0b",
        "testing":         "#ef4444",
        "project_manager": "#6366f1",
        "sysadmin":        "#64748b",
    }
    OWNER_TITLES = {
        "product_manager": "📄 Product Manager",
        "mechanical":      "⚙ Mechanical",
        "hardware":        "🛠 Hardware",
        "firmware":        "🔌 Firmware",
        "algorithm":       "🧮 Algorithm",
        "cost":            "💰 Cost",
        "testing":         "🧪 Testing",
    }
    KIND_PORT = {
        "prd": "doc",
        "cad": "cad",
        "schematic": "schematic",
        "pcb": "pcb",
        "firmware": "code",
        "algorithm": "code",
        "bom": "data",
        "test_report": "doc",
    }

    nodes: list[WorkflowNode] = []

    # 按 owner 聚合 deliverables(同 owner 多个 deliverable 还是一个节点,inputs/outputs 合并)
    by_owner: dict[str, list] = {}
    for d in m.deliverables:
        by_owner.setdefault(d.owner, []).append(d)

    seq = 0
    for owner, dels in by_owner.items():
        seq += 1
        # 输出端口 = 该 owner 的所有 deliverable kind 映射
        outputs = [
            WorkflowPort(
                id=KIND_PORT.get(d.kind, "doc"),
                label=d.kind,
                type=KIND_PORT.get(d.kind, "doc"),
                position="right",
            )
            for d in dels
        ]
        # 输入端口:除 product_manager 外都从 PRD 接 task
        inputs = []
        if owner != "product_manager":
            inputs.append(WorkflowPort(id="task", label="task", type="task", position="left"))

        # 主 deliverable(节点点击弹层默认显示这个)
        primary = dels[0]
        nodes.append(WorkflowNode(
            id=owner,
            type="deliverable",
            position={"x": 0, "y": 0},
            data=WorkflowNodeData(
                seq=seq,
                title=OWNER_TITLES.get(owner, owner),
                subtitle=f"[{primary.kind}]",
                headerColor=OWNER_COLORS.get(owner, "#6366f1"),
                owner=owner,
                status="done",
                duration_ms=None,
                inputs=inputs,
                outputs=outputs,
                fields=[
                    WorkflowField(label="files", value=str(len(dels)), variant="badge"),
                    WorkflowField(label="kind", value=primary.kind, variant="readonly"),
                ],
                deliverable={"kind": primary.kind, "path": primary.path},
            ),
        ))

    # edges:robot_engineering 拓扑 — PRD → 机械/固件/算法 fanout → 成本
    edges = []
    if "product_manager" in by_owner:
        for downstream in ("mechanical", "hardware", "firmware", "algorithm"):
            if downstream in by_owner:
                edges.append(WorkflowEdge(
                    id=f"e-pm-{downstream}",
                    source="product_manager",
                    sourceHandle="doc",
                    target=downstream,
                    targetHandle="task",
                    type="typed",
                    data={"portType": "doc", "crossOwner": True, "status": "done"},
                ))
    # 各工种 → cost
    for upstream in ("mechanical", "hardware"):
        if upstream in by_owner and "cost" in by_owner:
            edges.append(WorkflowEdge(
                id=f"e-{upstream}-cost",
                source=upstream,
                sourceHandle="cad" if upstream == "mechanical" else "schematic",
                target="cost",
                targetHandle="task",
                type="typed",
                data={"portType": "data", "crossOwner": True, "status": "done"},
            ))

    return PipelineSnapshot(task_id="", nodes=nodes, edges=edges)
