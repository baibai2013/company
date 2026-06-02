# benchmarks 10 题 — 完整题面

- 作者:testing · 2026-06-02
- 配套文档:[../07-测试与验证基建.md](../07-测试与验证基建.md) §4
- 用途:每题作为 mechanical 子技能 agent-eval 的输入 prompt;`golden.json` 字段以本文「验收维度」为准。
- 难度梯度:★(校准) → ★★★★★(装配 / 螺旋 / 高扇出)
- **fast 子集** = #1 / #2 / #3 (PR 必跑,合计 <60s);**full** = 全部 10 题(nightly + merge-to-main)。

---

> 每题给 6 段:CN prompt(给中文 LLM)/ EN prompt(给 earthtojake / OpenAI 系)/ 关键尺寸约束(必须满足)/ 验收维度(自动判分)/ golden 关键字段(种子)/ 难度。
> CN 和 EN 是**等价**的两个版本,agent-eval 默认跑 CN;EN 留给跨模型对比。

---

## #1 calibration_block ★

**CN prompt**
> 用 build123d 设计一个校准块作为 CAD 输出基准:外形 60×40×20 mm 长方体(沿 X×Y×Z),底面 4 个角各开 1 个 Φ4 通孔(贯穿 Z 方向),孔中心距各侧外缘 5 mm。无倒角无圆角,单一 solid。导出 STEP 到 `output/calibration_block.step`。

**EN prompt**
> Using build123d, design a calibration block as a CAD baseline: a 60×40×20 mm rectangular solid (X×Y×Z) with four Φ4 through-holes (along Z) at the four bottom corners, each hole center offset 5 mm from the two adjacent outer edges. No fillets, no chamfers. Single solid. Export STEP to `output/calibration_block.step`.

**关键尺寸约束**
- 外形 bbox = 60×40×20 mm(严格)
- 4 孔 Φ4 贯穿,中心位于 (5,5,*) (55,5,*) (5,35,*) (55,35,*)
- 单一 solid,无倒角圆角
- 文件大小 ≥ 5 KB

**验收维度**
- BRep 通过 + STEP reimport_ok = true
- volume = 60×40×20 − 4·π·2²·20 ≈ 47 497.79 mm³(±0.5 %)
- bbox = [60.0, 40.0, 20.0](±0.05 mm 各分量)
- solid_count = 1, is_manifold = true

**golden 关键字段**
```json
{"volume_mm3": 47497.79, "bbox_mm": [60.0, 40.0, 20.0],
 "solid_count": 1, "is_manifold": true}
```

**难度**:★(L0 健康检查)

---

## #2 flange_4hole ★★

**CN prompt**
> 设计一个 4 孔法兰盘:外径 Φ80,内孔 Φ40 通孔,厚度 8 mm,在 PCD 60 mm 上均布 4 个 Φ6 通孔(0°/90°/180°/270°)。法兰外圆边倒 R1 圆角。导出 STEP 到 `output/flange_4hole.step`,并导出顶视图 DXF 到 `output/flange_4hole.dxf`。

**EN prompt**
> Design a 4-hole flange: OD Φ80, ID Φ40 through-bore, thickness 8 mm, with 4× Φ6 through-holes evenly distributed on PCD 60 mm at 0°/90°/180°/270°. Outer rim filleted R1. Export STEP to `output/flange_4hole.step` and a top-view DXF to `output/flange_4hole.dxf`.

**关键尺寸约束**
- OD 80, ID 40, t 8 mm
- 4×Φ6 PCD60,角度严格 0/90/180/270
- 外圆 R1 圆角(只外圆,不含孔边)
- 同心度严格(OD/ID/孔中心绕同一轴)

**验收维度**
- BRep + STEP reimport
- volume ≈ π·(40²−20²)·8 − 4·π·9·8 ≈ 29 154.6 mm³(±0.5 %;扣圆角增量 ±1 %)
- bbox = [80, 80, 8](±0.05)
- 4 孔中心点位置 (0,±30,*) (±30,0,*) ±0.1 mm(BRep 边沿测量)
- DXF 顶视图含 OD 圆 + ID 圆 + 4 孔(`ezdxf` 解析后 entity 数 ≥ 6)

**golden 关键字段**
```json
{"volume_mm3": 29154.6, "bbox_mm": [80.0, 80.0, 8.0],
 "solid_count": 1, "is_manifold": true,
 "hole_centers_mm": [[30,0],[0,30],[-30,0],[0,-30]],
 "dxf_entity_count_ge": 6}
```

**难度**:★★(圆环 + 阵列,机械装配最常见入口)

---

## #3 l_bracket ★★

**CN prompt**
> 设计一个带加强肋的 L 支架:水平翼板 50×50×5 mm(在 XY 平面),竖直翼板 50×40×5 mm(沿 +Z 方向,与水平翼板共用 X=0 边),夹角 90°。在 L 形内角处加 2 条三角加强肋:厚 3 mm,从内角沿 X 与 Z 各延伸 30 mm。每翼板各开 2 个 Φ5 安装孔(对称分布)。导出 STEP。

**EN prompt**
> Design an L-shaped bracket with stiffening ribs: horizontal flange 50×50×5 mm (in XY plane), vertical flange 50×40×5 mm (extruded +Z, sharing the X=0 edge with the horizontal flange), 90° corner. Add 2 triangular gussets at the inner corner: 3 mm thick, extending 30 mm along X and 30 mm along Z. Each flange has 2× Φ5 mounting holes, symmetrically placed. Export STEP.

**关键尺寸约束**
- 水平 50×50×5,竖直 50×40×5,夹角 90°
- 2 条三角肋 t=3,沿 X/Z 各 30 mm,贴内角
- 4 安装孔 Φ5(每翼板 2 个,关于翼板中线对称)
- 单一 solid

**验收维度**
- volume(±0.5 %,种子由 mechanical Owner 首跑后填)
- bbox = [50, 50, 40](±0.05)
- solid_count = 1, is_manifold = true
- 内角处 boolean union 不漏接(STEP reimport 后 face count 与基线一致)

**golden 关键字段**
```json
{"bbox_mm": [50.0, 50.0, 40.0], "solid_count": 1, "is_manifold": true,
 "face_count_seed": 0, "_seed_note": "由 mechanical Owner 首版 freeze"}
```

**难度**:★★(布尔加 + 拉伸,机械结构基本款)

---

## #4 stepped_shaft ★★★

**CN prompt**
> 阶梯轴(三段同轴):Φ10×L20 → Φ16×L30 → Φ12×L15,从 Z=0 沿 +Z 拼接。各段过渡处加 R1 圆角(外凸面)。中央段(Φ16)开 1 条键槽:宽 5 mm 深 3 mm,长 20 mm,沿 +X 方向居中(键槽中心在中央段轴向中点)。两端面各倒角 0.5×45°。导出 STEP。

**EN prompt**
> Stepped shaft, 3 coaxial segments: Φ10×20 → Φ16×30 → Φ12×15 stacked along +Z from Z=0, with R1 fillet at each step (convex). Central Φ16 segment has a keyway: 5 mm wide, 3 mm deep, 20 mm long, opening +X, centered on the central segment's mid-plane. Both axial ends 0.5×45° chamfer. Export STEP.

**关键尺寸约束**
- 三段长度严格 20+30+15 = 65
- 各段直径 Φ10 / Φ16 / Φ12
- R1 过渡 + 0.5×45° 端倒角
- 键槽 5×3×20,沿 +X 开口,中心 Z=20+15=35

**验收维度**
- volume(±1 %,圆角倒角影响放宽)
- bbox = [16, 16, 65](±0.05)
- 单 solid manifold
- 键槽位置:键槽底面中心点 ≈ (5, 0, 35) ±0.1 mm

**golden 关键字段**
```json
{"bbox_mm": [16.0, 16.0, 65.0], "solid_count": 1, "is_manifold": true,
 "keyway_center_mm": [5.0, 0.0, 35.0]}
```

**难度**:★★★(多段对齐 + 局部特征 + 圆角)

---

## #5 enclosure_box ★★★

**CN prompt**
> 电子外壳:外形 100×60×30 mm(X×Y×Z),壁厚 2.5 mm 抽壳(顶面 +Z 开口,移除顶盖),底面四角各 1 个 M3 螺柱(外径 Φ5 内孔 Φ2.5,高 25 mm,从底面内表面向上),底面侧边(沿 +X 长边方向)中央位置 1 个 USB-A 通孔 12×6 mm(穿透壁面),顶部开口边缘倒 R0.5 圆角。导出 STEP。

**EN prompt**
> Electronics enclosure: outer 100×60×30 mm (X×Y×Z), 2.5 mm wall thickness obtained by shelling (top +Z face open / removed). 4× M3 standoffs at the bottom corners (OD Φ5, ID Φ2.5, height 25 mm, extruded upward from inner bottom). One USB-A cutout 12×6 mm centered on the +X long side wall (through the wall). Top opening edge filleted R0.5. Export STEP.

**关键尺寸约束**
- 外 bbox 100×60×30,壁 2.5
- 4 螺柱 M3 OD5/ID2.5/H25,在内底四角(距内壁 5 mm)
- USB 12×6 切口,在 +X 侧壁中央
- 顶口边 R0.5
- 单 solid manifold(壳体 + 螺柱合并)

**验收维度**
- volume(±1 %)
- bbox = [100, 60, 30](±0.05)
- 单 solid manifold
- USB 切口尺寸:12×6 ±0.1 mm(BRep 边沿测量)
- 4 螺柱顶面圆心 ±0.1 mm

**golden 关键字段**
```json
{"bbox_mm": [100.0, 60.0, 30.0], "solid_count": 1, "is_manifold": true,
 "usb_cutout_mm": [12.0, 6.0],
 "standoff_top_centers_mm": [[5,5,27.5],[95,5,27.5],[5,55,27.5],[95,55,27.5]]}
```

**难度**:★★★(抽壳 + 多特征 + 切口)

---

## #6 clevis_yoke ★★★

**CN prompt**
> 叉耳(双耳片 + 横梁):横梁 30×20×10 mm(X×Y×Z,Z=0~10),横梁顶面 Z=10 沿 +Z 竖出 2 块平行耳片,左右对称(关于 YZ 中面),耳片间距 14 mm(内侧距离),各厚 6 mm,高 30 mm(Z=10~40),沿 Y 方向长度 30 mm。耳片头部(+Z 端)半圆 R10。耳片中心位置开 Φ6 销孔贯穿(轴沿 X 方向,孔中心 Z=25,Y=15)同轴。导出 STEP。

**EN prompt**
> Clevis yoke: base beam 30×20×10 mm (X×Y×Z, Z=0..10). Two parallel ears extruded +Z from the beam top (Z=10), symmetric about the YZ midplane, gap 14 mm (inner distance), each 6 mm thick × 30 mm tall (Z=10..40) × 30 mm long (along Y). Ear top end rounded R10 semicircle. A Φ6 pin hole through both ears, axis along X, hole center at Z=25, Y=15 (coaxial). Export STEP.

**关键尺寸约束**
- 横梁 30×20×10
- 2 耳片间距 14(内侧),厚 6,高 30,长 Y=30
- 耳片头部 R10 半圆
- Φ6 销孔同轴 (X 任意, Y=15, Z=25)

**验收维度**
- volume(±1 %)
- bbox = [30, 32, 40](耳片厚 6×2 + 间距 14 = 26 < 30 横梁宽,主导项 = 横梁宽 30 . 注意 Y 不大于 30 横梁本身;若耳片在横梁内侧则 [30, 30, 40])
- solid_count = 1
- 同轴度:两耳孔中心连线方向向量 = (1,0,0) ±1e-3
- Φ6 孔中心 (0, 15, 25) ±0.1 mm(任一耳片端面外)

**golden 关键字段**
```json
{"bbox_mm": [30.0, 30.0, 40.0], "solid_count": 1, "is_manifold": true,
 "pin_hole_axis": [1.0, 0.0, 0.0],
 "pin_hole_center_seed_mm": [0.0, 15.0, 25.0]}
```

**难度**:★★★(对称特征 + 同轴度断言)

---

## #7 radial_cylinder ★★★★

**CN prompt**
> 径向气缸缸体:主缸沿 Z 轴布置,外径 Φ40 内孔 Φ20 长度 80 mm(Z=0~80)。侧面径向连接一段 Φ12 进气管:从 Z=40 处沿 +X 方向伸出 25 mm,管壁厚 1.5 mm(管内径 Φ9),进气管内孔与主缸内孔贯通。Z=80 端配 4 孔法兰:法兰外径 Φ60 厚 8 mm(Z=80~88),4 个 Φ5 通孔均布 PCD 45 mm。法兰与缸体一体成型(单 solid)。导出 STEP。

**EN prompt**
> Radial pneumatic cylinder body. Main bore along Z: OD Φ40, ID Φ20, length 80 mm (Z=0..80). A radial inlet tube on the side: Φ12 outer × Φ9 inner (1.5 mm wall), extruded +X for 25 mm starting from Z=40 mid; inlet bore intersects main bore. End flange at Z=80: OD Φ60 × 8 mm thick (Z=80..88), 4× Φ5 through-holes evenly on PCD 45 mm. Flange and cylinder are a single solid. Export STEP.

**关键尺寸约束**
- 主缸 Φ40/Φ20×80
- 进气管 Φ12 外/Φ9 内,从 Z=40 +X 25 mm,通主缸
- 法兰 Φ60×8,4×Φ5 PCD45
- 单 solid manifold

**验收维度**
- volume(±1.5 %,布尔运算累积容差更宽)
- bbox 大致 [60, 40, 88](X 方向:法兰 60 vs 主缸 40+进气管 25=65 → 取 65;若进气管贴外径起点则 X=20+25=45;以 prompt 中"伸出 25 mm"为准 → X bbox = 20+25=45,法兰 60 主导 → 60)
- solid_count = 1, is_manifold = true
- 内孔贯通:reimport 后,主缸 Φ20 内体积 + 进气管 Φ9 内体积 ≈ 24 938 mm³(±2 %)

**golden 关键字段**
```json
{"bbox_mm": [60.0, 60.0, 88.0], "solid_count": 1, "is_manifold": true,
 "inner_void_volume_mm3_seed": 24938.0,
 "_seed_note": "首版由 Dave 验证内通"}
```

**难度**:★★★★(多特征布尔 + 内通连)

---

## #8 impeller_3blade ★★★★

**CN prompt**
> 三叶离心叶轮:轮毂 Φ20 高 30 mm(Z=0~30),沿 Z 轴 Φ8 通孔(贯穿轮毂)。3 片叶片均布 120°,每片以螺旋扫掠方式从轮毂外表面沿径向向外延伸,弦长 30 mm(径向距离),叶片厚 2 mm,扫掠 path 在出口处沿 +Z 上扬 15°(弦中线在 Z 方向有 15° 倾角)。叶片顶端外接圆 Φ80(即叶片最外径 Φ80)。叶片与轮毂布尔合并为单 solid。导出 STEP。

**EN prompt**
> 3-blade centrifugal impeller. Hub Φ20 × 30 mm tall (Z=0..30) with Φ8 through-bore along Z. Three blades evenly at 120°, each helically swept from the hub OD radially outward, chord 30 mm (radial), thickness 2 mm, with 15° axial pitch at the outlet (mid-chord rises 15° along +Z). Outer blade tip envelope Φ80. Blades and hub merged into a single solid. Export STEP.

**关键尺寸约束**
- 轮毂 Φ20×30,Φ8 中心通孔
- 3 叶 120° 均布
- 叶片 t=2,弦 30 径向,15° 轴向倾角
- 外径 Φ80
- 单 solid manifold(扫掠最易非流形,严格)

**验收维度**
- volume(±2 %,扫掠样条容差更宽)
- bbox 大致 [80, 80, 30+δ](δ 来自 15° 倾角导致的 Z 延伸,< 8 mm)
- solid_count = 1
- BRep manifold(扫掠拓扑修复严格)
- STEP reimport_ok = true

**golden 关键字段**
```json
{"bbox_mm_z_min": 30.0, "bbox_mm_z_max": 38.0,
 "solid_count": 1, "is_manifold": true,
 "_volume_tolerance_pct": 2.0}
```

**难度**:★★★★(螺旋扫掠 + 多叶 union,样条曲面是建模能力分水岭)

---

## #9 spiral_staircase ★★★★★

**CN prompt**
> 螺旋楼梯:中柱沿 Z 轴 Φ60 高度 2200 mm(Z=0~2200);共 12 阶踏板,每阶为半径 100 mm 厚 30 mm 的扇形,扇形夹角 22.5°,内径与中柱外径相切(内径 Φ60 = 中柱外径)。每阶相对前一阶绕 Z 轴旋转 +22.5°,垂直上升 180 mm(总升 12×180=2160 mm)。第 1 阶底面 Z=20,第 12 阶底面 Z=20+11×180=2000(顶面 Z=2030)。中柱顶/底端各加 Φ70 厚 5 mm 封板。所有踏板与中柱布尔合并为单 solid。导出 STEP。

**EN prompt**
> Spiral staircase. Central column along Z: Φ60 × 2200 mm tall (Z=0..2200). 12 steps, each a sector of radius 100 mm × 30 mm thick × 22.5° fan angle, inner radius tangent to the column OD (ID Φ60 = column OD). Each step rotated +22.5° around Z relative to the previous and rises 180 mm vertically (total rise 12×180=2160 mm). Step #1 bottom at Z=20; step #12 bottom at Z=2000 (top at Z=2030). Top and bottom of the column capped by Φ70 × 5 mm plates. All steps merged with the column into a single solid. Export STEP.

**关键尺寸约束**
- 中柱 Φ60×2200
- 12 阶,每阶 R100×t30×22.5°
- 旋转步长 22.5°,Z 步长 180
- 顶底封板 Φ70×5
- 单 solid manifold

**验收维度**
- volume(±1 %,简单几何阵列)
- bbox = [200, 200, 2200](±0.1 中柱主导)
- solid_count = 1(严格,union 必须合并)
- step_reimport_ok = true(高 Z 跨度对 STEP 序列化是压力测试)

**golden 关键字段**
```json
{"bbox_mm": [200.0, 200.0, 2200.0],
 "solid_count": 1, "is_manifold": true,
 "step_count_seed": 12}
```

**难度**:★★★★★(规模阵列 + 高 Z 跨度 + 单 solid 强约束)

---

## #10 planetary_gear_set ★★★★★

**CN prompt**
> 行星齿轮组(简化版,装配体):太阳轮 18 齿 模数 1,内齿圈 54 齿 模数 1,3 个行星轮 18 齿 模数 1。所有齿轮厚度 8 mm(沿 Z),节圆均在 XY 平面上(Z=0~8)。3 个行星轮均布 120°,行星轮中心位于 PCD = (太阳轮节圆半径 + 行星轮节圆半径) = (9 + 9) = 18 mm 圆周上(0°/120°/240°)。装配:太阳轮居中,3 行星轮位姿正确,内齿圈居外。每个齿轮单独是一个 solid(装配体共 5 solid)。**齿廓允许用近似圆/简化齿形,不要求渐开线;关键是中心距与位姿。** 导出装配 STEP。

**EN prompt**
> Planetary gear set (simplified, assembly). Sun gear 18T m=1, ring gear 54T m=1, 3× planet gears 18T m=1. All face widths 8 mm (Z=0..8), pitch circles in XY plane. 3 planets evenly at 120°, planet centers on PCD = (sun pitch radius + planet pitch radius) = (9+9) = 18 mm at 0°/120°/240°. Assembly: sun centered, 3 planets positioned correctly, ring outermost. Each gear is its own solid (5 solids total). **Tooth profile may be approximated (simple circular teeth); the key constraint is center distance and pose, not involute accuracy.** Export assembly STEP.

**关键尺寸约束**
- 太阳/行星 18T m=1(节圆 Φ18),内齿圈 54T m=1(节圆 Φ54)
- 厚度 8 mm
- 行星轮中心 (18,0,*) (−9,15.59,*) (−9,−15.59,*) ±0.5 mm(PCD 18,120° 均布)
- 装配 STEP 含 5 个独立 solid
- 各 solid manifold(齿形可近似)

**验收维度**
- 装配 STEP reimport 后 solid_count = 5(严格)
- 各 solid is_manifold = true
- 总 bbox 约 [80, 80, 8](由内齿圈外径 Φ60+齿高 决定,容差 ±2 mm)
- 总 volume ±2 %(简化齿形浮动大)
- 各 solid 中心位置(行星轮 PCD)±0.5 mm

**golden 关键字段**
```json
{"solid_count": 5, "all_manifold": true,
 "bbox_mm_xy_seed": [80.0, 80.0],
 "planet_centers_mm_seed": [[18,0],[-9,15.59],[-9,-15.59]],
 "_volume_tolerance_pct": 2.0,
 "_note": "简化齿形,不验渐开线啮合,只验中心距与位姿"}
```

**难度**:★★★★★(装配位姿 + 多 solid + 几何关系)

---

## fast / full 套件归档

| 套件 | 题号 | 总耗时目标 | 触发 |
|---|---|---|---|
| **fast** | #1 calibration_block / #2 flange_4hole / #3 l_bracket | < 60 s | PR 必跑 |
| **full** | #1 ~ #10 全部 | < 10 min | nightly + merge-to-main |

详细取舍理由见 [../07-测试与验证基建.md](../07-测试与验证基建.md) §4.3。

---

## golden.json 种子产生流程

1. mechanical Owner(Dave)首版:本地跑 `python skills/mechanical/benchmarks/run_all.py --suite full --emit-golden` → 生成 `output/<case>.step` + 候选 `golden.candidate.json`
2. Dave 人工审过(尺寸 / 拓扑 / 视觉) → 重命名为 `golden.json` + git commit
3. 后续 PR 改 `golden.json` 必须 `@tech_lead + @mechanical(Dave)` 双签
4. 失败回归只跑 `compare_golden.py`,不允许 `--emit-golden` 静默更新基线
