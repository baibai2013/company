# 04 · 机器人描述子技能（urdf / srdf / sdf）

- 负责人：algorithm @ 2026-06-02
- 协作人：mechanical（提供 STEP + 关节配置）
- 优先级：P0（urdf）/ P1（srdf · sdf）
- 状态：草稿(P0 规格完成 / P1 srdf+sdf 规格已细化 · 2026-06-02 EOD,等 P0-1 骨架就位再动手实现)
- 依赖：P0-1 骨架、mechanical 子技能(02)、[08-shared协议](08-shared跨子技能协议.md)

---

## 1. 目标与范围

复刻 earthtojake 的 `urdf / srdf / sdf` 三个 sibling skill,内化成三个子技能:

- **urdf(P0)**: 从 STEP + 关节配置生成机器人 URDF + meshes/, 喂给 viewer 的 cad 引擎和 PyBullet。
- **srdf(P1)**: MoveIt 规划组(planning groups / 末端 / 碰撞矩阵)。
- **sdf(P1)**: Gazebo 仿真世界(world + 物理参数)。

机器狗项目直接消费这三者:URDF → PyBullet 步态仿真;SRDF → MoveIt 规划;SDF → Gazebo。

### 1.1 范围澄清(v2 决议)

earthtojake 的 urdf skill 走的是「Python `gen_urdf()` 函数为源 → CLI 序列化 + 校验 → 写 .urdf」。
我们的子技能在**两层**叠加:

- **L1 复刻层**: 整块复刻 earthtojake 的 references + scripts/urdf 生成器/校验器(几乎零改动)。
- **L2 适配层**: 我们自己的 `scripts/export_urdf.py`, 吃 mechanical 的 STEP + `joints.yaml` → 自动生成 L1 风格的 `gen_urdf()` Python 源(也就是「joints.yaml + STEP → 写一份生成器源 → 调 L1 CLI → 出 URDF + meshes/」)。

这样 earthtojake 上游的更新可以直接 rebase, 我们的扩展不污染上游契约。

## 2. 现状

- 本机已有 earthtojake `urdf / srdf / sdf` skill, 路径 `~/.agents/skills/{urdf,srdf,sdf}/`, 可直接复刻
  - urdf: SKILL.md ≈ 66 行, 6 份 references(design-ledger / frame-semantics / generator-contract / gen-urdf / urdf-workflow / validation), `scripts/urdf` 是个 Python 包(`cli.py / source.py`)做生成 + 校验, 共享 `scripts/packages/cadpy_metadata`
  - srdf: SKILL.md ≈ 77 行, 引用 URDF, 关注 planning groups / end effector / disabled collisions
  - sdf: SKILL.md ≈ 104 行, SDFormat 1.12, 关注 world / physics / sensors / plugins
- 现 build123d-cad 的 `references/simulation/` 已有 FK/IK/gait/URDF/PyBullet 方法论(归 mechanical, 本子技能复用其产物, 不互引用 references)
- mechanical 的产出契约里 `mount_points[]` 已声明跨域装配边, 可作为关节信息来源

## 3. 目标目录

```
skills/urdf/                            # P0
├── SKILL.md                            # 父入口 ≤ 120 行: L1 复刻原文 + L2 适配层入口
├── README.md
├── references/                         # 整块复刻 earthtojake/urdf 的 6 份 references
│   ├── design-ledger.md
│   ├── frame-semantics.md
│   ├── gen-urdf.md
│   ├── generator-contract.md
│   ├── urdf-workflow.md
│   ├── validation.md
│   └── joints-yaml.md                  # ★ 新增,L2 入口,讲 joints.yaml → URDF 字段映射
├── scripts/
│   ├── urdf/                           # 整块复刻 L1 生成器(__main__.py + cli.py + source.py)
│   ├── packages/cadpy_metadata/        # 整块复刻
│   └── export_urdf.py                  # ★ L2 适配层: STEP + joints.yaml → gen_urdf 源 + meshes/
└── tests/
    ├── conftest.py
    ├── fixtures/                       # 1 个最小机器狗例子: 3 link 单腿 + joints.yaml
    │   ├── single_leg.step             # 占位/小样
    │   └── single_leg.joints.yaml
    ├── test_export.py                  # export_urdf.py 行为
    ├── test_urdf_valid.py              # link-joint 树闭合 + 单根 + 无环 + 单位
    └── test_l1_passthrough.py          # earthtojake 原 CLI 不破

skills/srdf/   (P1)                     # 同结构, MoveIt 规划组
skills/sdf/    (P1)                     # 同结构, Gazebo world
```

## 4. 任务拆解

### P0 · urdf

- [ ] **T1 L1 复刻(零改动搬运)**
  - [ ] 整块拷 `~/.agents/skills/urdf/{SKILL.md,references/,scripts/}` → `skills/urdf/`
  - [ ] 在头部 frontmatter 追加一行 `provenance: earthtojake/text-to-cad`(已在原文里), 不改它的契约
  - [ ] L1 contract 不变: `gen_urdf()` 必须返回 `ET.Element` / XML 字符串 / `{"xml": ...}` 三选一

- [ ] **T2 L2 适配层 `scripts/export_urdf.py`**(★ 本子技能的核心新增价值)
  - 入口: `python scripts/export_urdf.py <joints.yaml> [-o output_dir/]`
  - 行为:
    1. 读 `joints.yaml` (schema 见 [08 §2.1](08-shared跨子技能协议.md#21-jointsyaml-schema草案))
    2. 把每个 `link[].mesh` (STEP 路径) 转换为 `meshes/<link>.glb` + `meshes/<link>.stl`(走 mechanical 的 build123d export, 失败 .stl 兜底)
    3. 生成 `<task>_gen_urdf.py` Python 源, 内嵌为 `gen_urdf()`(用 `xml.etree.ElementTree`), 用具名常量, mesh 走相对路径 `meshes/<link>.stl`
    4. 调 `python scripts/urdf <task>_gen_urdf.py -o robot.urdf` 跑 L1 校验 + 写 URDF
    5. 报告: 生成的 link/joint 数, 缺失字段, 校验告警
  - **不做**: 自动猜 inertial(质量/转动惯量必须由 yaml 给, 缺则 fallback 到 1kg + 单位对角阵 0.001 + 醒目 TODO 标记并打到 stdout)
  - **依赖**: build123d / OCP(可选; 缺时用 trimesh 把 STEP→STL); 强烈建议先与 mechanical 对齐 STEP→GLB 转换通道, 复用 [02](02-mechanical子技能迁移.md) 的 `scripts/export`

- [ ] **T3 测试**(`tests/test_*.py`)
  - test_urdf_valid: link-joint 树闭合 / 单根 / 无环 / `revolute` 限位是 radian / `continuous` 无 fake limit / mesh 文件存在 / mesh scale 三个正数
  - test_export: 给 `fixtures/single_leg.joints.yaml` + STEP, 跑 export_urdf.py, 断言 URDF link 数 = yaml.links 长度, joint 数 = yaml.joints 长度, axis 向量非零
  - test_l1_passthrough: 直接复用 earthtojake 自带例子, 断言 L1 CLI 行为不破

- [ ] **T4 handoff 接 viewer**
  - URDF 产物路径 → viewer cad 引擎(urdf-loader-three + 关节滑块), 接口见 [08 §2](08-shared跨子技能协议.md)
  - viewer 端要在 router 加后缀 `.urdf → engine=cad`(已确认在 03 文档范围内)

### P1 · srdf / sdf / 接 mechanical 关节源

- [ ] **T5 srdf 复刻(P1)**
  - L1 整块复刻
  - L2 `export_srdf.py`: 吃 `srdf.yaml`(规划组/末端/group_state), 输出 `gen_srdf()` 源
  - 自碰撞矩阵生成: P1 走「**脚本静态推导邻接 + 用户列表覆盖**」, 不强依赖 MoveIt setup assistant(理由见 §7 决策)
- [ ] **T6 sdf 复刻(P1)**
  - L1 整块复刻
  - L2 `export_sdf.py`: 吃 `world.yaml`(地面/重力/光照/sensor 列表), 输出 `gen_sdf()` 源
  - 默认 sdformat version 1.12
- [ ] **T7 接 mechanical 关节源(P1)**
  - 从 mechanical 的 `mount_points[]` + 装配关系推导 joints.yaml 草案
  - 落在 mechanical 子技能的 `scripts/export/joints_from_assembly.py`(归 mechanical 维护, 我们消费, 见 [02](02-mechanical子技能迁移.md))

## 5. 验收标准

```bash
# T1 L1 通过
cd skills/urdf && pytest tests/test_l1_passthrough.py -v

# T2/T3 L2 通过
cd skills/urdf && pytest tests/test_export.py tests/test_urdf_valid.py -v
# 期望: export_urdf.py 跑 fixtures/single_leg.joints.yaml 出 URDF
#       URDF 可被 pybullet.loadURDF 加载, 关节数对得上配置

# T4 handoff 闭环(M2 端到端 demo)
python scripts/export_urdf.py fixtures/single_leg.joints.yaml -o /tmp/dog/
bash skills/viewer/scripts/start.sh /tmp/dog/robot.urdf /tmp/dog/
# 期望: viewer engine=cad, 关节滑块可拖动
```

`pybullet.loadURDF` 是 P0 必跑的硬验收;  RViz / Gazebo / MoveIt 加载是 P1 软验收(本机不一定有 ROS 环境, 在 docs 里写清如何在 ROS docker 里跑)。

## 6. 与其他文档的接口

- 上游 **mechanical(02)**:
  - 消费其 `output/<task>/<part>.step`(每 link 一份)
  - 消费其 `output/<task>/joints.yaml`(可由 02 T-extend 的 `joints_from_assembly.py` 自动生成草案,人工补齐)
  - **不读** mechanical 的 references
- 下游 **viewer(03)**:
  - URDF 走 cad 引擎, 见 03 §router
  - 轨迹回放(P3)走 sim 引擎
- 跨技能数据格式(`joints.yaml` schema)定义在 [08 §2.1](08-shared跨子技能协议.md#21-jointsyaml-schema草案)
- **shared/output 约定**: 我们的产物落 `output/<task>/robot.urdf` + `output/<task>/meshes/<link>.{glb,stl}`

## 7. 决策定稿(2026-06-02 · Gate 1 候选)

> 表中「已定稿」与 [01 §8](01-分工与排期.md) 的「建议结论」一致,Gate 1 通过即一次性锁定。

### 7.1 joints 配置标准格式 — 已定稿

- **YAML 写入 + JSON Schema 强校验**。
- 文件名: `joints.yaml`(单机器人)或 `<robot>.joints.yaml`(多机器人共存)。
- Schema 文件落 `shared/schemas/joints.schema.json`(由 [08 §2.1 末决议](08-shared跨子技能协议.md))。
- 字段全集 + 注释见 [§8](#8-jointsyaml-字段全集每字段含义示例)。

### 7.2 SRDF 自碰撞矩阵 — 已定稿

- **P1 阶段静态推导,不引入 MoveIt 运行时依赖**。
- 三条静态规则:相邻 link 默认 disable、同腿但不可达节段对 disable、其余默认 enable;
- escape hatch: `srdf.yaml` 顶层 `disabled_pairs: []` 覆盖 + `enable_default_adjacent: true`;
- P2 升级:Monte Carlo 采样碰撞分析脚本(放 mechanical/code-sources/simulation),离线一次性产 disable 清单存档。

### 7.3 inertial 数据来源 — 已定稿

- yaml 必填,缺时分档 fallback(由 tech_lead 在 [08 §2.1 末](08-shared跨子技能协议.md) 拍板):
  - 部分 link 缺 → WARN + URDF 注释 `<!-- INERTIAL-FALLBACK -->`;
  - 所有 link 缺 → ERROR 拒写 URDF。
- P1 提供 `compute_inertial_from_step.py`,读 STEP 用 OCP 算质量/质心/惯量,密度走 `materials_library`(在 mechanical data-sources)。

### 7.4 mesh 路径表达 — 已定稿

- 默认 `relative`(`meshes/<link>.stl`);ROS 用户在 yaml 顶层加 `mesh_uri_style: package` + `package_name: <pkg>` 切换;
- 强制 `scale="0.001 0.001 0.001"` 当 `mesh_units: mm`,STEP 习惯 mm,URDF 是 m。

### 7.5 multi-leg 命名 — 已定稿

`<位置前缀>_<部位>` + joint 加 `_joint`,见 [08 §2.2](08-shared跨子技能协议.md#22-命名约定被-04-755-02--07-共同消费)。

## 8. joints.yaml 字段全集(每字段含义+示例)

> 本节是 [08 §2.1](08-shared跨子技能协议.md#21-jointsyaml-schema草案) 的字段级展开,Schema 文件以 [08 §2.1 JSON Schema 块](08-shared跨子技能协议.md#21-jointsyaml-schema草案) 为权威。
> 字段汇总(顶层 6 + links 子段 7 + joints 子段 9 = **22 个字段**),全部小写 + snake_case。

### 8.1 顶层字段

| 字段 | 类型 | 必填 | 默认 | 示例 | 说明 |
|---|---|---|---|---|---|
| `schema_version` | int(const) | ✅ | — | `1` | 当前固定 1; 破坏性改动升 2,L2 兼容上一版本一个 sprint |
| `robot` | string | ✅ | — | `dog_left_front_leg` | URDF 根 `<robot name="...">`; 强制 `^[a-z][a-z0-9_]*$` |
| `units.length` | enum | ⛳ 声明 | `m` | `m` | URDF 强制 m,声明用,unit mismatch 时报警 |
| `units.angle` | enum | ⛳ 声明 | `rad` | `rad` | URDF 强制 rad,limits 上下限按此解释 |
| `mesh_units` | enum`{mm,m}` | ⛳ 声明 | `mm` | `mm` | STEP 习惯 mm,export_urdf 据此设 `scale="0.001 0.001 0.001"` |
| `mesh_uri_style` | enum`{relative,package}` | ⛳ 声明 | `relative` | `relative` | ROS 用 `package`,URDF visual mesh URI 由此渲染 |
| `package_name` | string | ⚙️ `package` 时必填 | — | `dog_description` | `package://<package_name>/meshes/<link>.stl` |
| `materials_library` | object\<id, mat\> | ❌ optional | `{}` | 见下 | P1 给 `compute_inertial_from_step.py` 用 |
| `naming_convention` | enum`{quadruped,arm,custom}` | ❌ | `quadruped` | `quadruped` | 影响 link 名校验;arm 用 `<robot>_<segment>` |

`materials_library` 子结构:

```yaml
materials_library:
  pla:    {density_kg_m3: 1240, color_rgba: [0.9, 0.9, 0.9, 1.0]}
  al6061: {density_kg_m3: 2700, color_rgba: [0.7, 0.7, 0.7, 1.0]}
```

link 用 `material: pla` 引用。P0 不消费(仅声明),P1 由 inertial 自动计算工具消费。

### 8.2 `links[]` 子段字段

| 字段 | 类型 | 必填 | 默认 | 示例 | 说明 |
|---|---|---|---|---|---|
| `name` | string | ✅ | — | `fl_thigh` | 全局唯一,正则 `^[a-z][a-z0-9_]*$`,见 [08 §2.2](08-shared跨子技能协议.md#22-命名约定被-04-755-02--07-共同消费) |
| `mesh` | string(path) | ⚙️ visual 必填 | — | `thigh.step` | 相对 yaml 同目录;存在性检查在 export_urdf 校验 |
| `material` | string(id) | ❌ | — | `pla` | 引用 `materials_library` 里的 id |
| `collision` | enum / `mesh:<path>` | ❌ | `same_as_visual` | `primitive` | 三选一: `same_as_visual` / `primitive` / `mesh:<path>` |
| `collision_primitive` | object | ⚙️ `primitive` 时必填 | — | 见下 | `type: box/cylinder/sphere`,带 origin |
| `inertial.mass` | float(>0) | ⛳ 见 7.3 fallback | — | `0.18` | 单位 kg |
| `inertial.origin` | `{xyz:[3], rpy:[3]}` | ⛳ | `{xyz:[0,0,0], rpy:[0,0,0]}` | — | 质心在 link 系下位姿 |
| `inertial.inertia` | `{ixx,iyy,izz,ixy,ixz,iyz}` | ⛳ | — | — | 主对角必 > 0;非对角可零;单位 kg·m² |

`collision_primitive` 子结构:

```yaml
collision_primitive:
  type: cylinder              # box | cylinder | sphere
  radius: 0.035               # cylinder/sphere
  length: 0.08                # cylinder
  size: [0.04, 0.02, 0.06]    # box, 仅 type=box 时
  origin: {xyz: [0,0,0], rpy: [0,0,0]}
```

### 8.3 `joints[]` 子段字段

| 字段 | 类型 | 必填 | 默认 | 示例 | 说明 |
|---|---|---|---|---|---|
| `name` | string | ✅ | — | `fl_hip_joint` | 全局唯一,惯例 `<child>_joint` |
| `type` | enum | ✅ | — | `revolute` | `fixed / revolute / continuous / prismatic / floating` |
| `parent` | string(link) | ✅ | — | `body` | 必须在 `links[].name` 出现 |
| `child` | string(link) | ✅ | — | `fl_hip` | 必须在 `links[].name` 出现 |
| `origin` | `{xyz:[3], rpy:[3]}` | ✅ | — | `{xyz:[0.10,0.05,0], rpy:[0,0,0]}` | 父 link 系下到 joint 系;来源:见 [§9 mount_points 映射](#9-mount_points--urdf-visualorigin-映射规则与-mechanical-已对齐--2026-06-02) |
| `axis` | array[3] | ⚙️ revolute/prismatic/continuous 必填 | — | `[0, 0, 1]` | joint 系下,自动 normalize;非零 |
| `limit.lower` | float | ⚙️ revolute/prismatic 必填 | — | `-1.57` | rad(revolute)或 m(prismatic);< upper |
| `limit.upper` | float | ⚙️ revolute/prismatic 必填 | — | `1.57` | 同上 |
| `limit.effort` | float | ❌ | `0`(无限制) | `12` | URDF effort 单位 N·m / N |
| `limit.velocity` | float | ❌ | `0`(无限制) | `6.0` | URDF velocity 单位 rad/s / m/s |
| `dynamics.damping` | float(≥0) | ❌ | `0` | `0.1` | URDF `<dynamics damping="..."/>` |
| `dynamics.friction` | float(≥0) | ❌ | `0` | `0.05` | URDF `<dynamics friction="..."/>` |
| `mimic` | object | ❌ | — | `{joint: fl_hip_joint, multiplier: 1.0, offset: 0.0}` | 平行四杆等被动关节,P1 启用 |
| `transmission` | object | ❌ | — | 见下 | `ros2_control` 接入,P1 启用 |
| `positive_motion` | string(doc) | ❌ | — | `+Z 旋转 = 髋外展` | 文档化方向语义,写进 design ledger,不影响 URDF |

`type=continuous` 不能给 `limit`(JSON Schema 已 `"not": {"required":["limit"]}`);
`type=fixed` 不需要 `axis / limit / dynamics`,export_urdf 忽略并 WARN;
`type=floating` 仅在 robot ↔ world 边出现,不进 SRDF planning group。

`transmission`(P1)子结构:

```yaml
transmission:
  type: SimpleTransmission                   # ros2_control 标准类型
  hardware_interface: PositionJointInterface # 或 VelocityJointInterface / EffortJointInterface
  actuator: {name: fl_hip_motor, mechanical_reduction: 1.0}
```

### 8.4 强不变量(超出 JSON Schema,由 export_urdf.py 强校验)

承袭 [08 §2.1](08-shared跨子技能协议.md#21-jointsyaml-schema草案) 末 7 条不变量,本节不重复列出;如有新增,同步两处。

### 8.5 字段数自检

- 顶层: 9
- links 子段: 8
- joints 子段: 14
- 合计 31 个 schema 字段(其中 9 个 P1 启用,P0 必跑 22 个)。

## 9. mount_points → URDF visual.origin 映射规则(与 mechanical 已对齐 · 2026-06-02)

> 回应 [02 §9 协作请求](02-mechanical子技能迁移.md#9-跨技能协作请求来自-04-algorithm--2026-06-02) 第一条 `mount_points[]` 映射。
> 状态:**与 mechanical 已对齐(基于 mechanical Playbook 装配契约)** · 2026-06-02。
> 后续如 mechanical 在 02 §3 给出 `mount_points` 实际数据结构(02 P0-2 迁移完成后),本节随之精化字段名。

### 9.1 mechanical 的 mount_points[] 含义(基于 [02 §3](02-mechanical子技能迁移.md))

mount_points 是 mechanical Playbook 在 multi-part 装配里给每个 STEP 标记的「跨域装配边」,描述「这个零件在装配体里如何与其它零件对接」。每个 mount_point 至少有:

- `id`: 装配点 id(如 `fl_thigh_to_fl_hip_axis`)
- `frame`: 装配点在零件 STEP 局部坐标系下的位姿(`xyz` mm + `rpy` rad)
- `axis`: 该装配点的主轴向(joint 旋转/平移方向)
- `mate_to`: 这个装配点要对接的对方装配点 id(可空,空则属顶层装配)
- `kind`(可选): `revolute / prismatic / fixed`,提示对接方式

> 注: 此结构基于 02 §3「mount_points[] 已声明跨域装配边」的描述提炼,具体字段在 mechanical 子技能迁移完成后由 mechanical 在 02 §3 给出权威定义,届时本节字段名以 mechanical 为准。

### 9.2 export_urdf.py 映射规则

给定 mechanical 产出的 STEP + `assembly_meta.json`(含 `mount_points[]`)+ 用户/`joints_from_assembly.py` 推导的 joints.yaml 草案,export_urdf 按以下规则填 URDF:

| URDF 字段 | mechanical 来源 | 转换规则 |
|---|---|---|
| `<link name>` | mount_point 所在零件文件名(stem) | 直接取(如 `fl_thigh.step` → `fl_thigh`) |
| `<link><visual><origin>` | **零件 STEP 局部原点 →  link frame 原点** | 当 link frame = STEP 原点时 `xyz=[0,0,0] rpy=[0,0,0]`(默认) |
| `<link><visual><geometry mesh filename>` | `meshes/<link>.stl`(由 STEP→STL 转换得) | 走 mechanical `scripts/export/`,见 [§9.4](#94-step--stlglb-通道复用) |
| `<link><visual><geometry mesh scale>` | `mesh_units` | `mm` → `0.001 0.001 0.001` / `m` → `1 1 1` |
| `<joint origin>` | **匹配的两个 mount_point 之间的相对位姿** | 见 [§9.3](#93-joint-origin-映射) |
| `<joint axis>` | mount_point.axis(归一化到 joint 系) | 直接取,需先转到 joint 系 |
| `<joint type>` | mount_point.kind / yaml `joints[].type` | yaml 优先,mount_point.kind 仅作 lint warn |

### 9.3 joint origin 映射

joint origin 是 child link 在 parent link frame 下的位姿。给定一对 `mount_point_p`(parent 端)+ `mount_point_c`(child 端),且 `mate_to` 指向对方:

```
T_parent_to_joint  = mount_point_p.frame      # parent link 系 → 装配点系
T_child_to_joint   = mount_point_c.frame      # child  link 系 → 装配点系(同一装配点)
T_joint_to_child   = inverse(T_child_to_joint)
T_parent_to_child  = T_parent_to_joint * T_joint_to_child   # 即 joint origin

# joint axis 由 mount_point_p.axis 转到 joint 系(等于装配点系):
axis_in_joint = T_parent_to_joint.rotation.inverse() * mount_point_p.axis  # 实际就是 mount_point_p.axis 本身,因 frame 已表达在 parent 系
```

**单位转换**:mount_point.frame.xyz 默认 mm,export_urdf 必须乘 1e-3 转成 m 后写 URDF;rpy 默认 rad 不变。

### 9.4 STEP → STL/GLB 通道复用

回应 [02 §9 第二条](02-mechanical子技能迁移.md#9-跨技能协作请求来自-04-algorithm--2026-06-02):

```python
# urdf 子技能 export_urdf.py 调用 mechanical 共享导出函数, 不重复造轮子
from skills.mechanical.scripts.export import export_mesh
# 但是子技能间不允许 import (08 §10 红线), 改走 shared.python:
from shared.python.handoff.mesh_export import export_mesh
# 接口约定: export_mesh(step_path, output_path, format="stl"|"glb", units="mm", linear_deflection=0.1, angular_deflection=0.5) -> dict{ok, fallback_used}
```

mechanical 在 02 P0-2 把 `scripts/export/` 提供给 shared,签名约定见 [08 §5 shared/python 共享](08-shared跨子技能协议.md#5-sharedpython共享-python-包应-04-8-r4)。失败兜底:STEP 失败 → STL,STL 失败 → 报 `_errors/mechanical.json` 不静默,见 [08 §2.3](08-shared跨子技能协议.md#23-错误传播约定)。

### 9.5 自动推导 joints.yaml(P1 · `joints_from_assembly.py`)

回应 [02 §9 第三条](02-mechanical子技能迁移.md#9-跨技能协作请求来自-04-algorithm--2026-06-02):

- 落在 mechanical 子技能的 `scripts/export/joints_from_assembly.py`(归 mechanical 维护);
- 输入:mechanical 装配产物 `output/<task>/assembly_meta.json` + `parts/*.step`;
- 输出:`output/<task>/joints.yaml`(草案,inertial 留 TODO,人工补齐);
- 算法:扫 `mount_points[]` 的 `mate_to` 配对 → 配对的两端 = 一对 (parent_link, child_link) → 用 §9.3 公式算 origin/axis → 按 mount_point.kind 给 type 草案。

mechanical 在 02 P1-3(code-sources/simulation 补齐)时实现,urdf 子技能消费。


### 7.3 inertial 数据来源

**建议结论: yaml 必填, 缺时 fallback 到 1kg + 0.001 对角张量并强制打 WARN**。

进阶: P1 提供 `compute_inertial_from_step.py`, 读 STEP 用 OCP 算体积/质心/惯量(密度由 mechanical data-sources 的材料库给), 输出可塞进 `joints.yaml` 的 inertial 块。

### 7.4 mesh 路径在 URDF 里写什么?

**建议结论: 默认相对路径 `meshes/<link>.stl`, 可选 `package://<robot>_description/meshes/<link>.stl`**。

- 默认相对路径在 PyBullet / 三维 web viewer 里都能解析(它们以 .urdf 同目录为基);
- ROS 环境用 `package://` URI, 由 `joints.yaml` 顶层 `mesh_uri_style: package` 切换;
- 永远 `scale="0.001 0.001 0.001"`(STEP 习惯 mm, URDF 是 m), 由 `joints.yaml` 的 `mesh_units: mm|m` 控制。

### 7.5 multi-leg 机器狗 link 命名约定?

**建议结论**: `<leg>_<segment>` (如 `fl_hip / fl_thigh / fl_shank / fl_foot`), 腿前缀 `fl/fr/rl/rr` (front-left/right, rear-left/right)。joints 同步: `fl_hip_joint / fl_knee_joint / fl_ankle_joint`。
锁在 [08 §2.2 命名约定](08-shared跨子技能协议.md) (待 tech_lead 评审)。

## 10. P1-1 srdf 复刻规格(2026-06-02 细化)

### 10.1 范围回顾

srdf 子技能在 URDF 之上叠加 MoveIt 规划语义:**planning groups + group_states + virtual_joint + passive_joints + disabled_collisions + end_effectors**。本节锁定:
- 从 URDF 派生哪些字段(数据来源 + 推导规则);
- `srdf.yaml`(L2 入口)字段全集;
- 最小 srdf XML 模板;
- 自碰撞矩阵的静态推导脚本。

### 10.2 从 URDF 派生的 SRDF 字段

| SRDF XML | 来源 | 派生规则 |
|---|---|---|
| `<robot name>` | URDF `<robot name>` | 必须一致(MoveIt 强校验) |
| `<group><chain base_link tip_link>` | URDF link 树 | 由 `srdf.yaml.groups[].chain` 显式给定;chain 必须是 URDF 里一条真实路径(单根+无环已由 04 §8 保证) |
| `<group><joint name>` | URDF joints | 显式枚举,每个 joint 必须在 URDF 出现 |
| `<group_state name>` | yaml | 用户给关节角度,units 与 URDF 一致(rad/m) |
| `<end_effector parent_group parent_link group>` | yaml | parent_group 必须是已定义的 group;group 与 parent_group 不能共享 link(MoveIt 硬约束) |
| `<virtual_joint type=floating>` | yaml | 浮动 base 用,接 `world → base_link`;type ∈ {fixed, floating, planar} |
| `<passive_joint name>` | URDF mimic / yaml | URDF 里所有 `mimic` joint 自动成为 passive,yaml 可追加 |
| `<disable_collisions link1 link2 reason>` | URDF + 静态推导 | 见 [§10.4](#104-自碰撞矩阵静态推导算法) |

### 10.3 srdf.yaml(L2 入口)schema 草案

```yaml
schema_version: 1
robot: dog                            # 必须等于 URDF 的 robot name
urdf: robot.urdf                      # 相对 srdf.yaml 同目录,L2 校验时打开它

virtual_joints:
  - name: world_to_base
    parent_frame: world
    child_link: base_link
    type: floating                    # fixed | floating | planar

groups:
  - name: fl_leg                      # 单腿规划组(IK)
    chain:
      base_link: body
      tip_link:  fl_foot
  - name: all_legs                    # 多组合并组(协调步态规划)
    subgroups: [fl_leg, fr_leg, rl_leg, rr_leg]
  - name: locomotion                  # 不带末端,仅枚举 joint
    joints: [fl_hip_joint, fl_knee_joint, fr_hip_joint, ...]

group_states:
  - name: stand                       # 站立默认姿态
    group: all_legs
    values:
      fl_hip_joint:   0.0
      fl_knee_joint: -0.3
      fl_ankle_joint: 0.6
      # ... 其它 12 个关节
  - name: lie_down
    group: all_legs
    values: { ... }

end_effectors:
  - name: fl_foot_ee
    parent_group: fl_leg
    parent_link:  fl_shank
    group:        fl_foot_group       # 必须是另一个 group, 仅含 fl_foot 一个 link

passive_joints: []                    # URDF mimic 已自动算; 这里追加纯 passive

disabled_collisions:
  enable_default_adjacent: true       # 相邻 link 默认 disable(MoveIt 默认行为)
  static_rules:
    - "same_leg_skip_one"             # 同腿跨段(hip↔shank)默认 disable
    - "fixed_chain"                   # fixed joint 链默认 disable
  pairs:                              # 用户手填 escape hatch
    - {link1: body,    link2: head,    reason: "head 套件不会撞机身"}
    - {link1: fl_foot, link2: rr_foot, reason: "对角腿物理可达但低频" }
```

### 10.4 自碰撞矩阵静态推导算法

落 `skills/srdf/scripts/disable_collisions_static.py`(P1):

```
输入:URDF link 列表 + URDF joint 树
输出:list[(link1, link2, reason)]

规则集(按优先级):
  1. adjacent: 任意 (parent, child) 对 → disable(reason="adjacent")
  2. fixed_chain: fixed joint 两端 link → disable(reason="fixed-chain")
  3. same_leg_skip_one: 同腿前缀(fl_/fr_/rl_/rr_)且 segment 距离 ≥ 2 → disable(reason="same-leg-non-adjacent")
  4. enable_default_adjacent: 默认开,关闭后规则 1 不生效(给玩具 robot 用)
  5. user pairs (yaml.disabled_collisions.pairs) 直接合并,reason 用 yaml 提供的字符串

不做(P2 升级):
  - never-collide(基于 sample-based 全状态空间扫,需要 MoveIt 或 PyBullet)
  - default-in-collision(同上)
```

P0 不跑此脚本(P0 阶段没 URDF/srdf 真件可测),P1 写完后跑 fixtures 单腿样例 → disable 列表为 0(单腿无 same-leg-skip-one);跑 4 腿狗样例 → 期望 ≥ 12 条(4 腿 × 同腿跨段) + 12 条(每条腿 3 个相邻对)= 24 条。

### 10.5 最小 srdf XML 模板(单腿,3 link 2 joint)

```xml
<?xml version="1.0"?>
<robot name="dog_left_front_leg">

  <virtual_joint name="world_to_base" type="fixed"
                 parent_frame="world" child_link="base_link"/>

  <group name="fl_leg">
    <chain base_link="base_link" tip_link="fl_thigh"/>
  </group>

  <group_state name="stand" group="fl_leg">
    <joint name="fl_hip_joint"   value="0.0"/>
    <joint name="fl_thigh_joint" value="-0.3"/>
  </group_state>

  <end_effector name="fl_foot_ee"
                parent_link="fl_thigh"
                group="fl_foot_group"
                parent_group="fl_leg"/>

  <disable_collisions link1="base_link" link2="fl_hip"
                      reason="adjacent"/>
  <disable_collisions link1="fl_hip"    link2="fl_thigh"
                      reason="adjacent"/>

</robot>
```

### 10.6 接 mechanical / urdf 的依赖

- 强依赖 URDF(本子技能不读 STEP / build123d);
- yaml 校验时 L2 打开 URDF,验:robot name 一致 / chain 路径连通 / group_states value 在 limit 内 / end_effector 与 parent_group 无 link 重叠;
- 失败统一写 `output/<task>/_errors/srdf.json`(走 [08 §2.3](08-shared跨子技能协议.md#23-错误传播约定))。

## 11. P1-1 sdf 复刻规格(2026-06-02 细化)

### 11.1 SDF 与 URDF 的差异

| 维度 | URDF | SDF | 备注 |
|---|---|---|---|
| 顶层 | `<robot>` 单根 | `<sdf><world>` 或 `<sdf><model>` 嵌套 | SDF 可装多 model |
| 文件版本 | 无显式版本 | `<sdf version="1.12">` 必填 | 默认锁 1.12 |
| frame 表达 | joint origin = child 在 parent 下位姿 | `<pose relative_to="...">` 显式声明参考 frame | SDF 更灵活,但 export 要小心 |
| 物理 | URDF 没有 | `<physics><gravity><dt><real_time_factor>` | 仿真世界级参数 |
| sensor | URDF 没有(走 ROS) | `<sensor type="camera/lidar/imu">` 内嵌 | 给 Gazebo 用 |
| plugin | URDF 没有 | `<plugin filename="..." name="...">` | Gazebo 仿真插件 |
| inertial | 一致 | 一致 | 字段相同 |
| visual/collision/geometry/mesh | 一致 | 一致 | mesh 路径表达可换 `model://` URI |
| joint | 一致 | 一致(但叫法 `<joint><axis><xyz>` 略不同) | 转换层处理 |
| material | URDF 内嵌 color | SDF 走 `<material><script>` | 转换层加 default 即可 |

### 11.2 world.yaml(L2 入口)schema 草案

```yaml
schema_version: 1
sdformat_version: "1.12"
world_name: dog_playground

physics:
  type: ode                          # ode | bullet | dart | simbody
  max_step_size: 0.001               # s
  real_time_factor: 1.0
  gravity: [0, 0, -9.81]

ground:
  type: plane                         # plane | heightmap | mesh
  size: [100, 100]
  friction: {mu: 0.8, mu2: 0.8}

light:
  - {name: sun, type: directional, direction: [-0.5,-0.5,-1.0], cast_shadows: true}

include_models:
  - uri: model://dog                  # 走 SDF model:// URI; export 把 URDF→SDF 后放这里
    pose: {xyz: [0, 0, 0.4], rpy: [0, 0, 0]}
    name: dog_instance

sensors:                              # P1 启用; 挂在某个 link 上
  - link: head
    name: front_camera
    type: camera
    camera: {horizontal_fov: 1.047, image: {width: 640, height: 480}}
  - link: body
    name: imu
    type: imu

plugins: []                           # P2 启用
```

### 11.3 URDF → SDF 转换路径选型

**结论:用 ign-tools(现已更名 gz-tools)的 `gz sdf -p robot.urdf > robot.sdf` 做 model-level 转换,不自己写。**

理由对比:

| 方案 | 优点 | 缺点 | 工作量 |
|---|---|---|---|
| **gz-tools `gz sdf -p`** ✅ | 官方维护,语义保真,版本跟随 sdformat | 依赖 `libgz-tools2`(本机 brew 装一行),headless 可跑 | 1h 集成 + 0.5h CI 安装 |
| 自写转换器 | 零外部依赖 | sdformat 1.12 字段多变,长期维护 cost 高;边界 case 容易漏 | 2~3d |
| 套用 `urdf-to-sdf`(开源 python) | 纯 python | 维护停滞,sdformat 版本支持滞后(只到 1.7) | 0.5d 接,但 1.12 兼容是大坑 |

**集成方式**:

```bash
# 安装(macOS / Linux)
brew install ignition-tools     # macOS
sudo apt install libgz-tools2   # Ubuntu

# 转换
gz sdf -p robot.urdf > /tmp/dog/model.sdf

# 校验
gz sdf --check /tmp/dog/model.sdf
```

L2 `export_sdf.py` 流程:

1. 读 `world.yaml` + 上游 URDF 路径;
2. 调 `gz sdf -p` 把 URDF → 中间 model SDF;
3. 用 ElementTree 把 model SDF 包进 `<sdf version="1.12"><world>...<include>...</include>...</world></sdf>`;
4. 注入 yaml 里的 physics / ground / light / sensors / plugins;
5. 调 `gz sdf --check` 做最终校验,失败拒写;
6. 如本机 `gz` 不存在:fallback 走「自写转换 model 部分(只走 link/joint/inertial/visual/collision 5 个 tag,sensor/plugin 缺省)」+ WARN,写到 `_errors/sdf.json` 标 `gz_unavailable=true`。

### 11.4 最小 sdf XML 模板(model + world)

**model SDF**(由 `gz sdf -p robot.urdf` 自动生成,不手写):

```xml
<?xml version="1.0"?>
<sdf version="1.12">
  <model name="dog">
    <link name="base_link">
      <inertial>
        <mass>0.42</mass>
        <pose>0 0 0.01 0 0 0</pose>
        <inertia><ixx>0.001</ixx><iyy>0.001</iyy><izz>0.001</izz>
                 <ixy>0</ixy><ixz>0</ixz><iyz>0</iyz></inertia>
      </inertial>
      <visual name="base_visual">
        <geometry><mesh><uri>meshes/base.stl</uri><scale>0.001 0.001 0.001</scale></mesh></geometry>
      </visual>
      <collision name="base_collision">
        <geometry><mesh><uri>meshes/base.stl</uri><scale>0.001 0.001 0.001</scale></mesh></geometry>
      </collision>
    </link>
    <!-- joints 与 URDF 一致, 略 -->
  </model>
</sdf>
```

**world SDF**(本子技能 L2 注入 physics/ground/include/sensors):

```xml
<?xml version="1.0"?>
<sdf version="1.12">
  <world name="dog_playground">
    <physics type="ode">
      <max_step_size>0.001</max_step_size>
      <real_time_factor>1</real_time_factor>
    </physics>
    <gravity>0 0 -9.81</gravity>
    <light name="sun" type="directional">
      <direction>-0.5 -0.5 -1</direction>
      <cast_shadows>true</cast_shadows>
    </light>
    <include>
      <uri>model://dog</uri>
      <pose>0 0 0.4 0 0 0</pose>
      <name>dog_instance</name>
    </include>
    <model name="ground_plane">
      <static>true</static>
      <link name="link">
        <collision name="collision">
          <geometry><plane><normal>0 0 1</normal><size>100 100</size></plane></geometry>
          <surface><friction><ode><mu>0.8</mu><mu2>0.8</mu2></ode></friction></surface>
        </collision>
        <visual name="visual">
          <geometry><plane><normal>0 0 1</normal><size>100 100</size></plane></geometry>
        </visual>
      </link>
    </model>
  </world>
</sdf>
```

### 11.5 接 viewer

P1 不强求(viewer 03 P1 才接 sim 引擎);P2 落地时 sdf 产物 → viewer engine=sim(走 SDFormat parser + Three.js 关节回放)。本子技能 P1 阶段 `viewer.url` 留空。

## 12. P1-1 工作量重估(2026-06-02)

> 触发自 CEO 安排第三步:srdf/sdf 规格细化后重估。

### 12.1 原估算回顾

[01 §3](01-分工与排期.md#3-p0-工作量明细--4-天) P1 总周期 ≈ 3 天(2026-06-09 ~ 2026-06-15,P1-1 / P1-2 / P1-3 / P1-4 / P1-5 五个任务并行)。

P1-1 对 algorithm 的份额(srdf + sdf):**原估 1 天**(参考 P0-4 urdf 复刻 2h × 2)。

### 12.2 新估算明细(细化后)

| # | 子任务 | 工作量 | 说明 |
|---|---|---|---|
| P1-1.1 | srdf L1 整块复刻 + LICENSE/provenance | 1h | 与 P0-4 urdf L1 同模式 |
| P1-1.2 | srdf L2 `export_srdf.py`(YAML→`gen_srdf` 源 + L1 校验)| 3h | 类比 export_urdf.py;字段比 urdf 简单 |
| P1-1.3 | srdf 自碰撞矩阵静态推导 `disable_collisions_static.py` | 2h | 见 §10.4,纯 graph 算法 |
| P1-1.4 | srdf tests(test_export / test_chain_valid / test_l1_passthrough)| 2h | 复用 urdf tests 模板 |
| P1-1.5 | sdf L1 整块复刻 | 1h | 同 srdf |
| P1-1.6 | sdf L2 `export_sdf.py`(world.yaml + URDF → SDF,调 gz sdf -p) | 4h | 含 fallback 自写转换;**风险点**见 §13 R3 |
| P1-1.7 | sdf tests + `gz sdf --check` 集成 | 2h | 需先 brew/apt 装 gz-tools |
| P1-1.8 | 与 viewer P1 sim 引擎对接(P2 落地占位) | 0.5h | P1 阶段仅写 README,不联调 |
| **合计** | | **15.5h ≈ 2 天** | 比原估 +1d |

### 12.3 与 P1 周期匹配性 — 不冲突

- P1 总周期 3 天,algorithm 一人独占,15.5h ≈ 2 天 < 3 天,有 1 天 buffer;
- gz-tools 安装失败时 fallback 自写转换器多 2~3h(放 R3 风险池);
- 如果 R3 触发,需要 +0.5d(总 2.5 天),仍在 P1 周期内。

### 12.4 §1 修订建议

工作量重估结论已同步到 [01 §3](01-分工与排期.md#3-p0-工作量明细--4-天) P1 工作量明细补行(由 algorithm 在交回结果卡时由 project_manager 在 01 落盘,本人不直接编辑 01 §3)。如 P1 周期触发 R3,project_manager 通知 → algorithm 申请加半天。

## 13. 风险与待办

- [ ] **R1** earthtojake L1 的 LICENSE 是否兼容内部 fork? 复刻时连 `LICENSE` 一起带过来, README 注明 provenance(沿用 P0)
- [ ] **R2** STEP→GLB/STL 转换通道与 mechanical 重叠, 必须复用同一份 `scripts/export/`(由 [02](02-mechanical子技能迁移.md) 维护), 否则两边语义会漂(沿用 P0)
- [ ] **R3 ★ 新增** `gz sdf --check` 与 URDF→SDF 转换依赖 `gz-tools`,本机无则 fallback 自写转换器,sdformat 1.12 多字段会漏;P1 起手前 algorithm 在自己机器先 `brew install ignition-tools` 验通过,再写 export_sdf
- [ ] **R4** PyBullet 版本: 锁 `pybullet>=3.2.5`, 写进 `skills/urdf/requirements.txt`(沿用 P0)
- [ ] **R5** L1 的 `cadpy_metadata` 包跨多个 sibling skill 共用 — **已在 [08 §5](08-shared跨子技能协议.md#5-sharedpython共享-python-包应-04-8-r4) 落锤**,抽到 `shared/python/cadpy_metadata/`,本风险关闭
- [ ] **R6 ★ 新增** srdf 静态自碰撞规则只覆盖 80%,never-collide / default-in-collision 漏检会让 MoveIt 规划保守(增加规划失败率);P2 升级 sample-based 分析脚本兜底,P1 阶段在 srdf XML 注释标 `<!-- DISABLE-COLLISION-FALLBACK: static-only -->`
- [ ] **R7 ★ 新增** sdf 的 `<sensor>` / `<plugin>` 字段在 yaml 里支持但本子技能 P1 不做仿真测试(本机无 Gazebo),只过 `gz sdf --check`;真跑仿真留 P2,在 README 写清。

