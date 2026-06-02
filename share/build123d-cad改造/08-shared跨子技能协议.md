# 08 · shared 跨子技能协议

- 负责人：tech_lead · 2026-06-02
- 协作人：全员（任何跨技能接口变更在此登记）
- 优先级：P0
- 状态：草稿(已细化, joints schema 草案 + 命名约定 + cadpy 共享 + output 决议 + 变更登记机制全部落地, 待 Gate 1 评审)
- 依赖：[00-总览与目标架构](00-总览与目标架构.md)

---

## 1. 目标与范围

子技能之间**零互引用**(不读彼此的 references),所有跨技能协作走 super skill 根目录下 `shared/` 的三份协议 + 两份「数据契约 / 公共代码」:

- `handoff-protocols.md` — 子技能怎么串接(数据交换)
- `multi-skill-router.md` — 父 SKILL.md 路由依据
- `dependencies.md` — 谁依赖谁(高扇入/扇出节点识别)
- `schemas/` — 跨技能 JSON Schema(joints / world / srdf 等),见 §2.1
- `python/` — 共享 Python 包(`cadpy_metadata` 等),见 §5

这是「防耦合」的关键基建:子技能可独立演进,只要遵守 shared 接口。

**范围红线**:本文只定接口,不定实现。`gen_urdf()` 怎么写归 04;路由表怎么实现归 03;mechanical 出件怎么导归 02。

## 2. handoff-protocols.md（子技能串接）

子技能之间**不做函数调用**,通过**约定输出文件路径**交换。

### 2.0 标准 output 约定

每个任务一个 `output/<task>/` 目录,**统一落在项目工作区**而非 skill 内(决议见 §6):

```
<project_root>/                     # e.g. ~/work/robot-dog/
└── domains/<domain>/               # mechanical / electronics / firmware / ...
    └── output/<task>/              # 一个任务一个目录, 跨子技能共享
        ├── parts/<part>.step       # mechanical 写
        ├── parts/<part>.stl        # mechanical 写(STEP 失败 .stl 兜底)
        ├── parts/<part>.glb        # mechanical 写(viewer 加载更快, optional)
        ├── parts/<part>.dxf        # mechanical 写(钣金 / 激光切割)
        ├── joints.yaml             # mechanical 写(joints_from_assembly.py)或人工写
        ├── robot.urdf              # urdf 写
        ├── meshes/<link>.{stl,glb} # urdf 写(L2 适配层产物)
        ├── srdf.xml                # srdf 写(P1)
        ├── world.sdf               # sdf 写(P1)
        ├── slice_report.json       # gcode 写(P1, 切片预检)
        ├── dxf_precheck.json       # sendcutsend 写(P1)
        ├── viewer.url              # viewer 写(本次会话起的 URL, 可空)
        └── _errors/<上游>.json     # 任一子技能失败时写, 见 §2.3
```

**写者守则**:
- 每个子技能只写自己声明过的产物名(见上表),**不覆盖他人**;
- 文件名小写 + 短横线,版本管理交给 git/项目级 manifest,不在文件名带 `-v1`;
- 写之前 `mkdir -p output/<task>/{parts,meshes,_errors}`;不要假定目录已建。

**读者守则**:
- 通过文件存在性判断上游是否就绪;不要 IPC/socket;
- 必需字段缺失 → `raise FileNotFoundError(<path>)` 报告该字段属于哪个 handoff,**不要尝试自动重跑上游**;
- 读之前先看 `_errors/`,失败 fallback 由调用方决策,不要静默吞错。

### 2.1 跨子技能 4 条标准 handoff(P0)

| 上游 | 产物（文件接口） | 下游 | 下游动作 |
|---|---|---|---|
| mechanical | `output/<task>/<part>.step` | viewer | 起 server 显示 |
| mechanical | `output/<task>/<part>.step` + `joints.yaml` | urdf | 转 URDF + meshes/ |
| mechanical | `output/<task>/parts/<part>.stl` / `.dxf` | gcode / sendcutsend | 切片 / 切割预检 |
| urdf | `output/<task>/robot.urdf` + `meshes/<link>.{stl,glb}` | viewer | cad 引擎 + 关节滑块 |
| parts-catalog | 返回 STEP 候选(用户/mechanical 选用) | mechanical | 装配引用,不再重复建模 |

[P3 预留]

| pcb (P3) | `output/<task>/board.kicad_pcb` → `kicad-cli pcb export gltf` → `board.glb` | viewer | engine=pcb 走原生 / engine=cad 加载 GLB |
| pcb (P3) | `output/<task>/<board>.dxf`(板框) | mechanical | 反向给 mechanical 做外壳让位 |
| sim (P3) | `output/<task>/trajectory.json` + `robot.urdf` | viewer | engine=sim 关节时间序列回放 |

### 2.2 高扇入 / 扇出节点(改动需全员同步)

- **viewer = 高扇入**:几乎所有子技能都把产物给它预览。改 URL 协议 / 后缀路由表 / health endpoint 路径,必须在 §8「变更登记」备案并 @全员。
- **mechanical = 高扇出**:多数链路起点。迁移(02)时必须保证 `output/<task>/parts/<part>.step` 路径稳定。

### 2.3 错误传播约定

handoff 失败的统一格式 — 写到 `output/<task>/_errors/<上游>.json`:

```json
{
  "skill": "mechanical",
  "task": "m2-demo",
  "stage": "export.step",
  "code": "STEP_EXPORT_FAILED",
  "message": "OCCT export refused: non-manifold body",
  "fallback": "stl_written",
  "ts": "2026-06-02T18:00:00+08:00"
}
```

下游优先读 `_errors/` 再决定是否用 fallback 文件;不要静默吞错。统一封装在 `shared/python/handoff/errors.py:write_error(...)`,所有子技能脚本必须走它。

### 跨技能数据 schema 状态

- [x] `joints.yaml`(mechanical → urdf):草案见 [§2.1](#21-jointsyaml-schema草案)(algorithm @ 2026-06-02)。**tech_lead 评审已回复见 §2.1 末**;待 mechanical 评审 mount_points 兼容性。
- [x] viewer URL 协议:`?engine=<cad|pcb|sch|sim>&dir=&file=` 锁定,详见 §2.A.2。
- [ ] `world.yaml`(sdf 子技能,P1 由 algorithm 在 04 §T6 给出)
- [ ] `srdf.yaml`(srdf 子技能,P1 由 algorithm 在 04 §T5 给出)
- [ ] `slice_report.json`(gcode 子技能,P1 由 cost 在 05 §T2 给出)
- [ ] `dxf_precheck.json`(sendcutsend 子技能,P1 由 cost 在 05 §T3 给出)
- [ ] [P3] 机械外壳边框 ⇄ PCB 边框互导格式(DXF/STEP),pcb 子技能落地时由 hardware 在 06 给出

### 2.1 joints.yaml schema 草案

**作者**: algorithm @ 2026-06-02 · **状态**: 待评审 · **关联文档**: [04 §6/§7.1](04-机器人描述子技能-urdf-srdf-sdf.md) / [02](02-mechanical子技能迁移.md)

**目标**: 锁住 mechanical → urdf 的契约,使 mechanical 出 STEP + joints.yaml 后,urdf 子技能可零问询自动生成 URDF + meshes/。

**最小完整样例**(单腿 3-link 机器狗腿):

```yaml
# 文件名: <robot>.joints.yaml,放在 output/<task>/ 同目录
schema_version: 1                  # 用于以后兼容性切换
robot: dog_left_front_leg          # → URDF <robot name="...">
units:
  length: m                        # 仅声明,URDF 强制 m
  angle: rad                       # 仅声明,URDF 强制 rad
mesh_units: mm                     # STEP 习惯 mm; export_urdf 据此设 scale="0.001 0.001 0.001"
mesh_uri_style: relative           # relative | package
package_name: dog_description      # 当 mesh_uri_style=package 时必填

links:
  - name: base_link
    mesh: base.step                # 相对 joints.yaml 所在目录
    collision: same_as_visual      # same_as_visual | primitive | mesh:<path>
    inertial:                      # 缺失时 export_urdf 给 1kg + 0.001*I 并 WARN
      mass: 0.42
      origin: {xyz: [0, 0, 0.01], rpy: [0, 0, 0]}
      inertia: {ixx: 0.001, iyy: 0.001, izz: 0.001, ixy: 0, ixz: 0, iyz: 0}

  - name: fl_hip
    mesh: hip.step
    collision: primitive            # 用 primitive 时下面给 box/cylinder/sphere
    collision_primitive:
      type: cylinder
      radius: 0.035
      length: 0.08
      origin: {xyz: [0, 0, 0], rpy: [0, 0, 0]}
    inertial: {mass: 0.18, origin: {xyz: [0, 0, 0], rpy: [0, 0, 0]},
               inertia: {ixx: 0.0003, iyy: 0.0003, izz: 0.0001, ixy: 0, ixz: 0, iyz: 0}}

  - name: fl_thigh
    mesh: thigh.step
    inertial: {mass: 0.25, origin: {xyz: [0, 0, -0.06], rpy: [0, 0, 0]},
               inertia: {ixx: 0.0008, iyy: 0.0008, izz: 0.0001, ixy: 0, ixz: 0, iyz: 0}}

joints:
  - name: fl_hip_joint
    type: revolute                 # fixed | revolute | continuous | prismatic
    parent: base_link
    child: fl_hip
    origin: {xyz: [0.10, 0.05, 0], rpy: [0, 0, 0]}   # 父 link 系下到 joint 系
    axis: [0, 0, 1]                # joint 系下,自动 normalize
    limit:
      lower: -1.57                 # rad,revolute 必填
      upper: 1.57
      effort: 12                   # N·m
      velocity: 6.0                # rad/s
    positive_motion: "+Z 旋转 = 髋外展"   # 文档化,给 design ledger

  - name: fl_thigh_joint
    type: revolute
    parent: fl_hip
    child: fl_thigh
    origin: {xyz: [0, 0, -0.02], rpy: [0, 0, 0]}
    axis: [0, 1, 0]
    limit: {lower: -2.0, upper: 2.0, effort: 12, velocity: 6.0}

# 可选: mimic / transmission / ros2_control,P1 再说
```

**JSON Schema(强校验,放 `skills/urdf/references/joints.schema.json`)**, 关键约束摘要:

```jsonc
{
  "$schema": "https://json-schema.org/draft/2020-12/schema",
  "type": "object",
  "required": ["schema_version", "robot", "links", "joints"],
  "properties": {
    "schema_version": {"const": 1},
    "robot": {"type": "string", "minLength": 1, "pattern": "^[a-z][a-z0-9_]*$"},
    "mesh_units": {"enum": ["mm", "m"]},
    "mesh_uri_style": {"enum": ["relative", "package"]},
    "links": {
      "type": "array", "minItems": 1,
      "items": {
        "type": "object",
        "required": ["name"],
        "properties": {
          "name": {"type": "string", "pattern": "^[a-z][a-z0-9_]*$"},
          "mesh": {"type": "string"},
          "collision": {"oneOf": [
            {"const": "same_as_visual"},
            {"const": "primitive"},
            {"type": "string", "pattern": "^mesh:"}
          ]},
          "inertial": {
            "type": "object",
            "required": ["mass", "origin", "inertia"],
            "properties": {
              "mass": {"type": "number", "exclusiveMinimum": 0},
              "inertia": {
                "type": "object",
                "required": ["ixx","iyy","izz","ixy","ixz","iyz"],
                "properties": {
                  "ixx": {"type": "number", "exclusiveMinimum": 0},
                  "iyy": {"type": "number", "exclusiveMinimum": 0},
                  "izz": {"type": "number", "exclusiveMinimum": 0}
                }
              }
            }
          }
        }
      }
    },
    "joints": {
      "type": "array",
      "items": {
        "type": "object",
        "required": ["name", "type", "parent", "child", "origin"],
        "properties": {
          "name": {"type": "string", "pattern": "^[a-z][a-z0-9_]*$"},
          "type": {"enum": ["fixed", "revolute", "continuous", "prismatic"]},
          "axis": {"type": "array", "minItems": 3, "maxItems": 3,
                   "items": {"type": "number"}},
          "limit": {
            "type": "object",
            "required": ["lower", "upper"],
            "properties": {
              "lower": {"type": "number"},
              "upper": {"type": "number"}
            }
          }
        },
        "allOf": [
          {"if": {"properties": {"type": {"const": "revolute"}}},
           "then": {"required": ["axis", "limit"]}},
          {"if": {"properties": {"type": {"const": "prismatic"}}},
           "then": {"required": ["axis", "limit"]}},
          {"if": {"properties": {"type": {"const": "continuous"}}},
           "then": {"required": ["axis"], "not": {"required": ["limit"]}}}
        ]
      }
    }
  }
}
```

**强不变量**(超出 JSON Schema 的, 由 `export_urdf.py` 校验):

1. links[].name + joints[].name 全局唯一
2. 每个 joint 的 parent / child 必须在 links[] 出现
3. 整个图: 单根、连通、无环、链路数 = links - 1
4. axis 向量非零(三分量平方和 > 1e-9)
5. revolute / prismatic 的 lower < upper
6. continuous 不能给 limit
7. mesh 路径在 joints.yaml 同目录下能 stat 到(否则 stage 报错, 不写 URDF)

**命名约定**(见 04 §7.5):

- 多腿用 `<leg>_<segment>`: `fl_/fr_/rl_/rr_` 前缀; segment 用 `hip / thigh / shank / foot`
- joint 名 = link 名 + `_joint`(child 端 link 名)
- 全部小写 + 下划线; 不允许大写 / 短横线 / 数字开头(由 schema 的 pattern 约束)

**评审请关注**(@mechanical / @tech_lead):

1. `mesh_uri_style: package` 还是 `relative` 当默认? 提案 default=relative, ROS 用户可在自己的 yaml 顶层覆盖
2. `inertial` 是否允许在 `joints.yaml` 整体缺省, 让 `export_urdf` 全部走 1kg+0.001*I fallback? 提案: 允许, 但全局打 WARN 并写进 URDF 注释
3. 是否需要顶层 `materials:` 段(`fl_thigh_material: PLA → density 1.24g/cm³`), 给 P1 的 `compute_inertial_from_step.py` 用? 提案: 加进 schema 但 P0 标 optional

**tech_lead 评审回应(2026-06-02)**:

| # | algorithm 提案 | tech_lead 决议 | 理由 |
|---|---|---|---|
| 1 | default=`relative` | **同意 default=`relative`** | PyBullet / 三维 web viewer 都以 .urdf 同目录解析,ROS `package://` 是 ROS 专属,默认 relative 让 90% 用户零配置;ROS 用户在 yaml 顶层加 `mesh_uri_style: package` + `package_name: <>` 即可。 |
| 2 | inertial 整体缺省允许 + WARN | **同意,但分档**:链路上**任意一个 link** 缺 inertial → WARN(URDF 注释打 `<!-- INERTIAL-FALLBACK -->`);**所有 link 都缺** → ERROR 拒写 URDF | PyBullet 单 link 缺 inertial 仿真不爆,但全缺会让仿真失真到无意义。强制 ≥ 1 个有真值,至少把全局尺度锁住。 |
| 3 | 顶层 `materials:` 段 P0 optional | **同意**,字段名定为 `materials_library`,值为 `material_id → {density_kg_m3, color_rgba?}`;link 内引用 `material: <id>` | 与 mechanical 的 data-sources 材料库(02 §T4)对齐;P0 不做实现,P1 由 `compute_inertial_from_step.py` 消费;现在锁字段名避免后续重命名波及 schema_version。 |

**额外决议**:
- `schema_version: 1` 锁死,后续破坏性改动升 `2`,L2 适配层至少兼容读上一个版本一个 sprint;
- **JSON Schema 文件**最终落 `shared/schemas/joints.schema.json`(不是 `skills/urdf/references/`),让 testing/handoff 都能直接 import,不耦合到 urdf 子技能;
- **校验入口**统一 `shared.python.handoff.validate.validate_joints(<path>)`,被 `export_urdf.py` / `test_handoff_joints_yaml.py` / agent-eval 三处复用,见 §5。

### 2.2 命名约定(被 04 §7.5 / 02 / 07 共同消费)

被多个子技能共同消费的标识符,统一在此定义,避免各自各定一份。

#### 2.2.1 link / joint 命名(机器狗多腿场景)

`<位置前缀>_<部位>` + joint 加 `_joint` 后缀:

| 位置前缀 | 含义 |
|---|---|
| `fl` | front-left  前左 |
| `fr` | front-right 前右 |
| `rl` | rear-left   后左 |
| `rr` | rear-right  后右 |
| `body` | 躯干本体 |
| `head` | 头部传感器舱 |

部位(从近端到远端):`hip → thigh → shank → foot`。

完整示例(单腿 4 link 3 joint):

```
links:  fl_hip, fl_thigh, fl_shank, fl_foot
joints: fl_hip_joint(body→fl_hip), fl_knee_joint(fl_thigh→fl_shank), fl_ankle_joint(fl_shank→fl_foot)
```

> 单腿可少一段(无足只 3 link 2 joint),命名仍按位置取头/尾。
> 非机器狗场景(机械臂等)用 `<robot_name>_<segment>`,在 `joints.yaml` 顶层加 `naming_convention: arm` 字段声明,本节后续增补对应映射。

#### 2.2.2 task 命名

`output/<task>/` 的 `<task>` 命名:`<里程碑>_<短描述>` 或 `<日期>_<短描述>`,小写短横线。
例:`m2-demo`、`2026-06-02_hip-bracket-tuning`。

#### 2.2.3 frame 命名(URDF 内部)

- 每个 link 的 frame 与 link 同名;
- 每个 joint 的 origin 是 child 在 parent frame 的位姿;
- 世界 frame 名 `world`(L2 在生成 URDF 时若无 base_joint 则自动加 fixed `world → base_link`)。

#### 2.2.4 文件名小写 + 短横线

沿用 mechanical 既有契约(02 §7):`hip_bracket.step` ✅、`HipBracket-V1.STEP` ❌。

### 2.A.2 viewer URL schema(已锁定)

```
http://127.0.0.1:<port>/?engine=<cad|pcb|sch|sim>&dir=<workspace_root>&file=<relative_path>
```

- `engine` ∈ `{cad, pcb, sch, sim}`,由 03 §4 后缀路由表产生;
- `dir` 是 server 启动时挂载的根目录,所有静态文件相对它解析;
- `file` 是相对 `dir` 的路径,**不允许 `..` 跨目录**(server 端校验,在 03 §6 落实)。

变更必须在 §8 「变更登记」备案。

## 3. multi-skill-router.md（父级路由依据）

父 SKILL.md（≤ 200 行）只做两层路由的第一层：

```
需求关键词 → 子技能映射
- "建模/装配/反求/零件"           → mechanical
- "预览/分享链接/截图/headless"     → viewer
- "URDF/机器人描述"                → urdf
- "MoveIt/规划组"                  → srdf
- "Gazebo/仿真世界"                → sdf
- "切片/打印估时/支撑"              → gcode
- "激光切割/钣金/DXF 报价"          → sendcutsend
- "找现成件/标准件 STEP"            → parts-catalog
- "Bambu/打印作业"                 → bambu-labs
- "PCB/原理图/DRC/电子 BOM" (WIP)   → pcb / electronics-bom
```

匹配后 **Read 对应 `skills/<name>/SKILL.md`** 再开始答题；父级不读子技能完整内容。

## 4. dependencies.md（依赖图）

```
mechanical ──→ viewer        (出 STEP 给预览)
mechanical ──→ urdf           (出 STEP + joints)
mechanical ──→ gcode          (出 STL)
mechanical ──→ sendcutsend    (出 DXF)
urdf       ──→ viewer         (出 URDF 给预览)
gcode/sendcutsend ──→ viewer  (出 gcode/dxf 给预览)
[P3] pcb   ──→ viewer         (出 GLB/Gerber)
[P3] pcb  ⇄  mechanical       (边框互导)
```

**高扇入节点 = viewer**：几乎所有子技能都把产物交给它预览。
→ 改 viewer 的 URL 协议 / 后缀路由表必须全员同步（在本文登记变更）。

`mechanical` 是高扇出根节点（多数链路起点），迁移(02)时务必保证产物路径稳定。

## 5. shared/python/(共享 Python 包,应 04 §8 R4)

**问题背景**:earthtojake 的 L1 `urdf / srdf / sdf` 都依赖 `scripts/packages/cadpy_metadata`。我们 monorepo 化后若每个子技能复制一份,会形成隐性 fork —— 上游升级时各子技能 drift。

**决议**:统一抽到 super skill 的 `shared/python/`,各子技能以 import 引用,不做拷贝。

```
shared/python/
├── cadpy_metadata/         # 整块复刻 earthtojake 的同名包,版本锁定
│   ├── __init__.py
│   └── ...                 # 由 P0-1 骨架时拷贝一份,版本号刻在 __init__.py
└── handoff/
    ├── __init__.py
    ├── output_paths.py     # output_dir(task)/parts_path(...)/joints_path(...) 统一拼路径
    ├── validate.py         # validate_joints/validate_world/...,封装 JSON Schema
    └── errors.py           # 标准化 _errors/<上游>.json 写入(见 §2.3)
```

**导入路径**:子技能内部以 `from shared.python.handoff import output_paths` 引用。父级 `pyproject.toml`:

```toml
[tool.setuptools.packages.find]
where  = ["."]
include = ["shared.python*", "skills.*.scripts*"]
```

`pip install -e .` 后可被 import。

**规则**:
- 改 `shared/python/` 的公共 API 必须在 §8「变更登记」记录,理由「会同时影响 ≥2 个子技能」;
- `shared/python/` 不能反向 import 任何 `skills/<name>/`,否则形成循环;
- 引入新依赖必须在 super skill 根 `requirements.txt` 锁版本(如 `jsonschema>=4.20`)。

## 6. shared/output 路径决议(应 §10 待讨论 Q1)

**结论**:`output/<task>/` 落**项目工作区**(`<project_root>/domains/<domain>/output/<task>/`),**不落 skill 内**。

理由:
- skill 是工具,工作区是产物。skill 目录纯净便于 `git clone` 复用 + 不同项目无产物互污染;
- 项目工作区已经按域分文件夹(`~/work/robot-dog/domains/{mechanical,electronics,firmware,...}`),product → output 路径一目了然;
- 测试/agent-eval 跑临时任务时,用 `tmp_path / "output" / "task"`(见 07 §6.2 `tmp_output_dir` fixture)。

**默认值**:子技能脚本未传 `--workspace` 时默认 `$PWD`;父级编排时显式传项目根。
跨技能封装在 `shared/python/handoff/output_paths.py:output_dir(task, workspace=None)`,所有子技能脚本必须走它,不要自己 `Path("output/...")` 拼字符串。

## 7. 加新子技能标准流程(`docs/adding-new-subskill.md` 摘要,P0-8 落盘)

新增一个子技能(如 P3 的 `pcb`)的标准 9 步:

```bash
NAME=pcb

# 1. 起目录(用 P0-1 模板)
mkdir -p skills/$NAME/{references,scripts,tests,benchmarks}
touch skills/$NAME/{SKILL.md,README.md,tests/conftest.py,tests/test_smoke.py}

# 2. 写 SKILL.md(≤ 250 行,声明触发场景 + 路由到本技能 references/scripts)
$EDITOR skills/$NAME/SKILL.md

# 3. 父 SKILL.md 路由表加一行(00 §4 表)
$EDITOR SKILL.md

# 4. shared/multi-skill-router.md 加关键词映射(本文 §3)
$EDITOR shared/multi-skill-router.md

# 5. shared/dependencies.md 标依赖(本文 §4),更新扇入扇出表
$EDITOR shared/dependencies.md

# 6. 如有跨技能 handoff:shared/handoff-protocols.md 加一行(本文 §2.1)
$EDITOR shared/handoff-protocols.md

# 7. 如引入新数据契约:shared/schemas/<x>.schema.json + shared/python/handoff/validate.py 加 validator
$EDITOR shared/schemas/<x>.schema.json
$EDITOR shared/python/handoff/validate.py

# 8. tests:smoke 至少测 SKILL.md 存在 + 主脚本可 import,benchmarks 占位 1 题
$EDITOR skills/$NAME/tests/test_smoke.py

# 9. 本表 §8 登记一次「新增子技能」变更
$EDITOR shared/CHANGELOG.md
```

**质量门**(在 `docs/adding-new-subskill.md` 落盘后由 testing 加到 CI):

- `wc -l skills/$NAME/SKILL.md` ≤ 250
- `pytest skills/$NAME/tests/ -q` 全绿
- `grep -rE "from skills\\." skills/$NAME/` 应为空(子技能间不互引用)
- shared 三份协议在本次 PR 都有改动(否则 CI 提示「确认本子技能不需要跨技能交互?」)

新加子技能的 PR 必须 @tech_lead 评审 —— 这是 super skill 架构的关键扩展点。

## 8. 变更登记(`shared/CHANGELOG.md`)

> 跨技能接口的改动必须在此登记,落盘到 `shared/CHANGELOG.md`,理由:同一个改动会同时影响 ≥2 个子技能。

| 日期 | 变更 | 影响子技能 | Owner | 备注 |
|---|---|---|---|---|
| 2026-06-02 | 初版 handoff/router/dependencies 协议草案 | 全员 | tech_lead | Gate 1 通过后锁定 |
| 2026-06-02 | `joints.yaml` schema v1 草案 | mechanical · urdf · srdf · testing | algorithm + tech_lead | 待 mechanical 评审 mount_points 兼容性 |
| 2026-06-02 | `output/<task>/` 落项目工作区(非 skill 内) | 全员 | tech_lead | 决议来自 §6 |
| 2026-06-02 | viewer URL schema `?engine=&dir=&file=` 锁定 | viewer · 所有上游 | fullstack + tech_lead | 与 03 §4 同步 |
| 2026-06-02 | `cadpy_metadata` 抽到 `shared/python/` | urdf · srdf · sdf | tech_lead | 决议来自 §5,应 04 §8 R4 |
| 2026-06-02 | 标准 task 目录结构 + `_errors/` 错误传播约定 | 全员 | tech_lead | 见 §2.0 / §2.3 |
| 2026-06-02 | 命名约定 v1(`fl/fr/rl/rr` + `hip/thigh/shank/foot`) | mechanical · urdf · srdf | tech_lead | 见 §2.2 |
| _未来变更_ | _填变更内容_ | _填影响子技能_ | _填_ | _填_ |

**登记规则**:
- 任何修改 `shared/handoff-protocols.md`、`shared/multi-skill-router.md`、`shared/dependencies.md`、`shared/schemas/`、`shared/python/handoff/` 公共 API 的 PR,**必须新增一行 CHANGELOG**;
- CI 检查 `git diff shared/` 与 `shared/CHANGELOG.md` 必须同时改动,否则 block;
- 破坏性变更(改字段名/字段类型/枚举值)需 @tech_lead 评审通过 + 在 CHANGELOG 用 ⚠️ 标记。

## 9. 任务拆解(P0-6 落盘)

P0-1 骨架合入后,本任务约 3h:

- [ ] **T1** 写 `shared/handoff-protocols.md`(摘抄本文 §2.0/§2.1/§2.2/§2.3)
- [ ] **T2** 写 `shared/multi-skill-router.md`(摘抄 §3,关键词映射 + 冲突处理)
- [ ] **T3** 写 `shared/dependencies.md`(摘抄 §4,依赖图 + 扇入扇出统计)
- [ ] **T4** 落 `shared/schemas/joints.schema.json`(摘抄 §2.1 JSON Schema 块)+ `shared/schemas/example/joints.yaml`(摘抄 §2.1 yaml 样例)
- [ ] **T5** 起 `shared/python/handoff/`:`output_paths.py`(`output_dir/parts_path/joints_path`)、`validate.py`(`validate_joints`)、`errors.py`(`write_error`)
- [ ] **T6** 落 `shared/python/cadpy_metadata/`(整块拷自 `~/.agents/skills/urdf/scripts/packages/cadpy_metadata`,注 LICENSE 与 provenance)
- [ ] **T7** 落 `docs/adding-new-subskill.md`(摘抄 §7)+ `shared/CHANGELOG.md`(摘抄 §8 表头 + 7 条初始记录)
- [ ] **T8** 父 `pyproject.toml` 加 `[tool.setuptools.packages.find]`(见 §5),让 `shared.python` 可 import
- [ ] **T9** 父根 CI workflow 加「shared/ 改动必须改 CHANGELOG」检查(摘 §8 登记规则)

## 10. 验收脚本

```bash
# shared 文件齐全
for f in handoff-protocols.md multi-skill-router.md dependencies.md CHANGELOG.md; do
  test -f "shared/$f" && echo "✓ shared/$f" || echo "✗ MISSING shared/$f"
done

# joints schema 自检 + 例子能过校验
python -c "
import json, jsonschema
jsonschema.Draft202012Validator.check_schema(json.load(open('shared/schemas/joints.schema.json')))
print('schema OK')
"
python -c "
import yaml, json, jsonschema
schema = json.load(open('shared/schemas/joints.schema.json'))
data   = yaml.safe_load(open('shared/schemas/example/joints.yaml'))
jsonschema.validate(data, schema); print('example OK')
"

# shared.python.handoff 可 import + 三大公共函数存在
python -c "
from shared.python.handoff.output_paths import output_dir
from shared.python.handoff.validate     import validate_joints
from shared.python.handoff.errors       import write_error
print('shared.python OK')
"

# 子技能间零 import 子技能(防耦合红线)
test -z "$(grep -rE 'from skills\\.[a-z_]+' skills/ | grep -v 'tests/')" && echo "✓ 零互引用" || echo "✗ 存在子技能互引用"
```

## 11. 与其他文档的接口

- 被引用最频繁的章节:§2.1 joints schema(被 04 §1.1/§7.1/§T2/T3 锚链)、§2.2 命名约定(被 04 §7.5)、§5 cadpy_metadata 共享(应 04 §8 R4)、§6 output 决议(被 02/04/05/07 共用)。
- 任何子技能新增/调整跨技能字段、产物路径、URL 协议、命名约定 → 在本文 §8 登记 + 通知 01 项目经理同步排期。
- 父 SKILL.md(00 §4)的路由表是 §3 的实现,必须回链一致(03 viewer 后缀路由表是 §3 的二级路由,改动同步登记)。
- `docs/adding-new-subskill.md`(§7)与 `docs/architecture.md`(P0-8)由 tech_lead 在 P0-1 骨架合入后落盘。

## 12. 待讨论(已锁定建议结论,待 Gate 1 拍板)

| # | 议题 | 建议结论 | 决策权 | 状态 |
|---|---|---|---|---|
| Q1 | `output/<task>/` 放 super skill 内还是项目工作区 | **项目工作区**(见 §6) | tech_lead | 已锁,待 Gate 1 |
| Q2 | 跨技能 schema 用 JSON Schema 还是约定式 YAML | **YAML 写入 + JSON Schema 强校验**(同 04 §7.1) | tech_lead | 已锁,待 Gate 1 |
| Q3 | cadpy_metadata 放每个子技能复制还是 super skill `shared/python/` | **`shared/python/`**(见 §5) | tech_lead | 已锁,待 Gate 1 |
| Q4 | joints schema 是否 Day1 强 CI 校验 | **是**(handoff 不校验 = 没接口);失败给清晰错误,不静默兜底 | tech_lead + algorithm | 已锁,待 Gate 1 |
| Q5 | `shared/CHANGELOG.md` 是 PR-level 还是周更 | **PR-level**(每个改 shared 的 PR 必须加一行,否则 CI block) | tech_lead | 已锁,待 Gate 1 |
| Q6 | base_link 多个时怎么办(浮动 base / 多机器狗共载) | **不支持,joints.yaml `base_link` 只接受单根**;浮动 base 走 floating joint 把 base 接 world | algorithm | 已锁,P1 复核 |
| Q7 | 命名约定外语言(英语/拼音)混用风险 | **强制英文 snake_case**(见 §2.2);中文只在文档与注释 | tech_lead | 已锁,待 Gate 1 |
| Q8 | algorithm §2.1 三个评审项 | 见 §2.1 末「tech_lead 评审回应」表 | tech_lead | 已回应,等 mechanical 复核 |

> 全表无异议时 Gate 1 一次性锁定;后续改动经 §8 登记。
