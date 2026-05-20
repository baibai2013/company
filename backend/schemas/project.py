"""B2 项目展示页数据契约(schema freeze)。

对应 doc/design/B2-showcase-frontend.md §2.2 / §2.3 / §2.5。

冻结点(并行 subagent 必须遵守):
- ManifestRead(含 assembly.parts[] / deliverables[] / summary / tags)
- BomDoc(items[].category 12 类强枚举 / vendors[] >=2)
- AssemblyDoc(phases[] 5 段 + tools / assumptions)
- TreeNode(path/kind/size/mtime)
- PipelineSnapshot(nodes/edges,前端 elkjs 算坐标)
"""
from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field


# ── BOM ────────────────────────────────────────────────────────────────────────

BomCategory = Literal[
    "microcontroller", "sensor", "actuator", "power", "module", "display",
    "structural", "enclosure", "mechanism", "hardware", "3D-printed", "generic",
]
VendorTier = Literal["pro", "maker", "budget", "instant"]


class BomVendor(BaseModel):
    name: str
    url: str = ""
    price_cny: float
    tier: VendorTier = "pro"


class BomItem(BaseModel):
    category: BomCategory
    subcategory: str = ""
    name: str
    qty: int
    unit_price: float
    total: float
    datasheet: str = ""
    vendors: list[BomVendor] = Field(default_factory=list)
    selected_vendor: str = ""


class BomSummary(BaseModel):
    total: float
    by_category: dict[str, float] = Field(default_factory=dict)


class BomDoc(BaseModel):
    currency: str = "CNY"
    items: list[BomItem] = Field(default_factory=list)
    summary: BomSummary = Field(default_factory=lambda: BomSummary(total=0.0))


# ── Manifest ───────────────────────────────────────────────────────────────────

class AssemblyPart(BaseModel):
    id: str
    name: str
    glb: str = ""
    step: str = ""
    transform: dict = Field(default_factory=lambda: {
        "translation": [0, 0, 0],
        "rotation": [0, 0, 0, 1],
    })
    explode_offset: list[float] = Field(default_factory=lambda: [0.0, 0.0, 0.0])
    color: str = "#a0a0a0"
    owner: str = ""
    cad_only: bool = False
    missing: bool = False


class AssemblyGroup(BaseModel):
    id: str
    name: str
    parts: list[str] = Field(default_factory=list)


class Assembly(BaseModel):
    parts: list[AssemblyPart] = Field(default_factory=list)
    groups: list[AssemblyGroup] = Field(default_factory=list)


DeliverableKind = Literal[
    "prd", "cad", "schematic", "pcb", "firmware", "algorithm",
    "bom", "test_report", "video", "status", "urdf", "sim_video", "image",
]


class Deliverable(BaseModel):
    kind: DeliverableKind
    path: str
    owner: str = ""
    extra: dict = Field(default_factory=dict)


class ManifestSummary(BaseModel):
    mass_g: int = 0
    dof: int = 0
    parts_count: int = 0
    cost_by_category: dict[str, float] = Field(default_factory=dict)
    currency: str = "CNY"
    bbox: list[float] = Field(default_factory=lambda: [0.0, 0.0, 0.0])


class ManifestRead(BaseModel):
    project: str
    name: str = ""
    version: str = "0.0.1"
    updated_at: datetime | None = None

    tags: list[str] = Field(default_factory=list)
    hero_image: str = ""
    summary: ManifestSummary = Field(default_factory=ManifestSummary)

    assembly: Assembly = Field(default_factory=Assembly)
    deliverables: list[Deliverable] = Field(default_factory=list)

    fallback: bool = False  # True = 后端目录扫描兜底,非 PM 确认


# ── Tree ──────────────────────────────────────────────────────────────────────

FileKind = Literal[
    "cad", "model3d", "schematic_src", "pcb_src", "kicad_project",
    "gerber", "archive", "netlist", "csv",
    "markdown", "pdf", "code", "json", "yaml",
    "image",
    "dir", "binary",
]


class TreeNode(BaseModel):
    path: str
    kind: FileKind
    size: int = 0
    mtime: datetime | None = None
    children: list["TreeNode"] = Field(default_factory=list)


# ── Assembly instructions ─────────────────────────────────────────────────────

class AssemblyStep(BaseModel):
    id: str
    text: str
    parts: int = 0
    refs: list[str] = Field(default_factory=list)


class AssemblyPhase(BaseModel):
    name: str
    icon: str = ""
    steps: list[AssemblyStep] = Field(default_factory=list)


class AssemblyDoc(BaseModel):
    tools: list[str] = Field(default_factory=list)
    assumptions: list[str] = Field(default_factory=list)
    phases: list[AssemblyPhase] = Field(default_factory=list)


# ── Pipeline (Workflow 视图) ──────────────────────────────────────────────────

class WorkflowPort(BaseModel):
    id: str
    label: str
    type: Literal["task", "doc", "cad", "schematic", "pcb", "code", "data", "signal"]
    position: Literal["left", "right"] = "left"


class WorkflowField(BaseModel):
    label: str
    value: str
    variant: Literal["select", "readonly", "badge"] = "readonly"


class WorkflowNodeData(BaseModel):
    seq: int
    title: str
    subtitle: str = ""
    headerColor: str = "#6366f1"
    owner: str
    status: Literal["pending", "running", "done", "failed"] = "pending"
    duration_ms: int | None = None
    inputs: list[WorkflowPort] = Field(default_factory=list)
    outputs: list[WorkflowPort] = Field(default_factory=list)
    fields: list[WorkflowField] = Field(default_factory=list)
    deliverable: dict = Field(default_factory=dict)


class WorkflowNode(BaseModel):
    id: str
    type: str = "deliverable"
    position: dict = Field(default_factory=lambda: {"x": 0, "y": 0})
    data: WorkflowNodeData


class WorkflowEdge(BaseModel):
    id: str
    source: str
    sourceHandle: str
    target: str
    targetHandle: str
    type: str = "typed"
    data: dict = Field(default_factory=dict)


class PipelineSnapshot(BaseModel):
    task_id: str = ""
    nodes: list[WorkflowNode] = Field(default_factory=list)
    edges: list[WorkflowEdge] = Field(default_factory=list)


# ── Connectivity (B2-connectivity-view §2.1) ─────────────────────────────────

NodeKind = Literal[
    "mcu", "sensor", "actuator", "power", "module", "display",
    "cad_part", "actuator_cross_domain", "generic",
    # B2-connectivity-view 扩展(2026-05-20):算法 / PCB 板 / 固件
    "algorithm", "pcb", "firmware",
]
EdgeKind = Literal["mechanical", "power", "data"]
InterfaceKind = Literal["data", "power", "mechanical"]


class NodeInterface(BaseModel):
    id: str
    kind: InterfaceKind


class ConnectivityRef(BaseModel):
    bom: str | None = None
    datasheet: str | None = None
    schematic_block: str | None = None
    step: str | None = None
    glb: str | None = None
    part_meta: str | None = None
    cad_model: str | None = None


class ConnectivityNode(BaseModel):
    id: str
    kind: NodeKind
    label: str
    domain: str = ""
    owner: str
    owner_label: str = ""
    ref: ConnectivityRef = Field(default_factory=ConnectivityRef)
    interfaces: list[NodeInterface] = Field(default_factory=list)


class ConnectivityEdge(BaseModel):
    id: str
    from_: str = Field(alias="from")
    to: str
    kind: EdgeKind
    label: str = ""
    data_subtype: str = ""

    model_config = {"populate_by_name": True}


class ConnectivityDoc(BaseModel):
    version: str = "1.0"
    generated_at: datetime | None = None
    nodes: list[ConnectivityNode] = Field(default_factory=list)
    edges: list[ConnectivityEdge] = Field(default_factory=list)
    merge_failed: bool = False


# ── 项目列表条目 ────────────────────────────────────────────────────────────────

class ProjectListItem(BaseModel):
    name: str
    title: str = ""
    has_manifest: bool = False
    has_charter: bool = False
    parts_count: int = 0
    updated_at: datetime | None = None
