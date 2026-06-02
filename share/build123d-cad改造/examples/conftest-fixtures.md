# conftest fixtures 清单(P0-7 tests 骨架)

- 作者:testing · 2026-06-02
- 配套文档:[../07-测试与验证基建.md](../07-测试与验证基建.md) §6
- 用途:每个子技能 Owner 写本子技能 `tests/conftest.py` 时直接对照本表;父级 `tests/conftest.py` 在文末。

---

## 0. 命名约定

- fixture 名小写蛇形,**前缀必须能看出归属**:子技能内 fixture 用 `<skill>_<thing>`(如 `viewer_server`),父级跨技能 fixture 不加前缀(如 `tmp_output_dir`)。
- session 级 fixture 必须无副作用可复用,不写 cleanup 反向依赖。
- 任何启动子进程的 fixture 必须 `try/finally` kill,不能依赖 GC。

---

## 1. 父根 `tests/conftest.py`(全员共享)

| fixture | scope | 产出 | 用途 |
|---|---|---|---|
| `skill_root` | session | `Path` | super skill 仓根目录,用 `Path(__file__).resolve().parents[1]` |
| `tmp_output_dir` | function | `Path` | `<tmp_path>/output/task/`,按 [08 §2.0] handoff 协议供子技能写入;每个 test 独立 |
| `workspace_root` | session | `Path` | session 级临时项目工作区(`tmp_path_factory.mktemp("ws")`),配合 `output_paths.output_dir(task, workspace_root)` |
| `mechanical_hip_bracket_step` | session | `Path` | 跑一次 `skills/mechanical/scripts/parts/hip_bracket.py` 出 STEP,viewer/urdf 复用;mechanical 子技能未就位时 `pytest.skip` |
| `joints_yaml_minimal` | session | `Path` | 最小可用 `joints.yaml`(2 link 1 joint),配合 [08 §2.1] schema 校验 |
| `b3d_session` | session | `dict` | build123d / OCP 版本探测(空字典或 `{"build123d":"0.6.x"}`);用于 `pytest.mark.skipif(not b3d_session)` |
| `slow_marker_filter` | autouse | — | 如 env `RUN_SLOW=0`(默认),自动 skip `@pytest.mark.slow`;CI nightly 设 `RUN_SLOW=1` |

参考实现见 [../07-测试与验证基建.md](../07-测试与验证基建.md) §6.2。

---

## 2. 子技能 fixture 清单

### 2.1 `skills/mechanical/tests/conftest.py`(Dave)

| fixture | scope | 产出 | 用途 |
|---|---|---|---|
| `mech_tmp_part_dir` | function | `Path` | 本测试临时输出目录(子目录 `parts/` `meshes/`)|
| `mech_part_factory` | function | callable `(name, fn)→Path` | 接受函数返回 `Part`,自动 export STEP 到 `mech_tmp_part_dir/parts/<name>.step` |
| `mech_validate_step` | function | callable `(path)→dict` | 封装 `skills/mechanical/scripts/validate/run.py`,返回 `{step_reimport_ok, is_manifold, volume_mm3, bbox_mm, ...}` |
| `mech_calibration_block_step` | session | `Path` | benchmark #1 共享样件(其他 smoke test 复用) |

smoke 关键断言(必跑):
- `test_skill_md_exists`(父级模板)
- `test_calibration_block_volume_in_47k_48k`(用 `mech_calibration_block_step` + `mech_validate_step`)
- `test_b3d_part_imports_ok`(`from build123d import Part` 不抛)

### 2.2 `skills/viewer/tests/conftest.py`(fullstack)

| fixture | scope | 产出 | 用途 |
|---|---|---|---|
| `free_port` | function | `int` | `socket` 抢占空闲端口,防并发测试冲突 |
| `viewer_server` | function | `dict {url, port, proc}` | `subprocess.Popen` 起 server,轮询 `/health` 至 200(超时 10s),teardown 时 `proc.terminate()` |
| `viewer_router` | session | callable `(filename)→engine` | import `skills.viewer.scripts.router.resolve_engine`,跨 case 复用 |
| `viewer_assets_dir` | session | `Path` | `skills/viewer/assets/test_files/` 占位文件目录(空 STEP / 空 PCB) |

smoke 关键断言:
- `test_health_endpoint_200`(用 `viewer_server`)
- `test_suffix_routing_table`(用 `viewer_router`,详见 07 §6.4 参数表)
- `test_no_directory_traversal`(请求 `?file=../../etc/passwd` 必须 400/403)

### 2.3 `skills/urdf/tests/conftest.py`(algorithm)

| fixture | scope | 产出 | 用途 |
|---|---|---|---|
| `urdf_sample_joints_yaml` | session | `Path` | 复用父级 `joints_yaml_minimal` 或本地更细的 dog 单腿样例 |
| `urdf_sample_step` | session | `Path` | 复用 `mechanical_hip_bracket_step` |
| `urdf_xml_validator` | session | callable `(urdf_path)→bool` | `xmllint --noout`,返回 `True` / 抛 |
| `urdf_pybullet_load` | session | callable `(urdf_path)→robot_id` 或 `pytest.skip` | 若 `import pybullet` 失败则 fixture 自身 skip,不污染 case |

smoke 关键断言:
- `test_export_urdf_xmllint_ok`(用 `urdf_sample_step + urdf_sample_joints_yaml + urdf_xml_validator`)
- `test_joints_yaml_passes_schema`(用 `shared.python.handoff.validate.validate_joints`)
- `test_pybullet_loads_urdf`(用 `urdf_pybullet_load`,pybullet 缺则自动 skip)

### 2.4 `skills/srdf/tests/conftest.py`(algorithm,P1)

| fixture | scope | 产出 | 用途 |
|---|---|---|---|
| `srdf_sample_urdf` | session | `Path` | 上游 URDF(本地或 urdf 子技能 fixture 链式)|
| `srdf_xml_validator` | session | callable | `xmllint --noout` |
| `srdf_disable_collisions_pairs` | session | `set[tuple[str,str]]` | 静态推导出的禁用碰撞对(rdb fixture)|

smoke:`test_skill_md_exists` + `test_srdf_xml_validates`(P0 占位,P1 真跑)

### 2.5 `skills/sdf/tests/conftest.py`(algorithm,P1)

| fixture | scope | 产出 | 用途 |
|---|---|---|---|
| `sdf_sample_world_yaml` | session | `Path` | 最小 world.yaml |
| `sdf_xml_validator` | session | callable | gz sdf --check 探活;缺工具则 fixture skip |

smoke:`test_skill_md_exists`(P0 占位)

### 2.6 `skills/gcode/tests/conftest.py`(cost,P1)

| fixture | scope | 产出 | 用途 |
|---|---|---|---|
| `gcode_sample_stl` | session | `Path` | 简单立方体 STL(20×20×20,实心) |
| `gcode_slicer_cli` | session | `str` 或 `pytest.skip` | OrcaSlicer / PrusaSlicer 路径探测;缺则跳 |
| `gcode_slice_report` | function | callable `(stl)→dict` | 跑 slicer + 解析 `slice_report.json`,返回 `{est_seconds, est_grams, layers}` |

smoke:`test_skill_md_exists` + `test_slice_report_json_schema`(P0 占位,P1 实跑)

### 2.7 `skills/sendcutsend/tests/conftest.py`(cost,P1)

| fixture | scope | 产出 | 用途 |
|---|---|---|---|
| `scs_sample_dxf` | session | `Path` | 简单矩形 DXF(50×30,1 closed polyline) |
| `scs_dxf_precheck` | function | callable `(dxf)→dict` | 解析 DXF + 计算面积 / 边长,返回 `dxf_precheck.json` 内容 |

smoke:`test_skill_md_exists` + `test_dxf_precheck_no_open_loops`(P0 占位)

### 2.8 `skills/parts-catalog/tests/conftest.py`(cost,P0)

| fixture | scope | 产出 | 用途 |
|---|---|---|---|
| `cat_query` | session | callable `(spec)→list[dict]` | 包装 step-parts / 本地 parts-lib 查询;缺数据源时返回空 |
| `cat_local_parts_lib_root` | session | `Path` | `~/work/build123d-parts-lib/` 探测;缺则 fixture skip 整个 case |

smoke 关键断言:
- `test_skill_md_exists`
- `test_query_m3x10_returns_nonempty`(用 `cat_query("M3x10")`,允许 skip 当数据源全缺) 

### 2.9 `skills/bambu-labs/tests/conftest.py`(cost,P2 占位)

| fixture | scope | 产出 | 用途 |
|---|---|---|---|
| `bambu_creds_stub` | session | `dict` 或 `pytest.skip` | 探测环境变量 `BAMBU_USER/PASS`,缺则 skip |

smoke:仅 `test_skill_md_exists`(P0)

### 2.10 `skills/pcb/tests/conftest.py`(hardware,P3 占位)

仅 smoke:`test_skill_md_exists` + `test_readme_has_p3_roadmap`。无 fixture。

### 2.11 `skills/electronics-bom/tests/conftest.py`(hardware,P3 占位)

同 2.10。

---

## 3. fixture 依赖图

```
父级 conftest
  ├─ skill_root ────────────────── 全员
  ├─ tmp_output_dir ───────────── mechanical / viewer / urdf / gcode / scs (handoff 协议落点)
  ├─ workspace_root ───────────── shared.python.handoff.output_paths.output_dir 入参
  ├─ mechanical_hip_bracket_step ─→ urdf_sample_step / viewer e2e
  ├─ joints_yaml_minimal ────────→ urdf_sample_joints_yaml
  └─ b3d_session ─────────────── mechanical / urdf 关键断言前置
```

跨子技能 fixture 链(用于父级 `tests/test_e2e_design_to_print.py`):

```
mechanical_hip_bracket_step (session)
  → 写入 tmp_output_dir
  → viewer_server 起在指向 tmp_output_dir 的 dir
  → 浏览器抓 / 截屏 / 关节滑块(P1)
  → urdf 子技能读 STEP+joints.yaml → 出 URDF → pybullet.loadURDF (skip if 缺)
```

---

## 4. 验收(testing 把这套 fixture 接好 = P0-7 完成)

```bash
# 每子技能 conftest + smoke 至少存在
for s in mechanical viewer urdf srdf sdf gcode sendcutsend parts-catalog bambu-labs pcb electronics-bom; do
  test -f "skills/$s/tests/conftest.py" || echo "✗ $s/tests/conftest.py"
  test -f "skills/$s/tests/test_smoke.py" || echo "✗ $s/tests/test_smoke.py"
done

# 父根 conftest 至少 5 个 fixture(用 pytest --fixtures 自查)
pytest --fixtures tests/ | grep -E "tmp_output_dir|skill_root|workspace_root|joints_yaml_minimal|mechanical_hip_bracket_step" \
  | wc -l  # 应 ≥ 5

# 全部 smoke 跑通
pytest -q -m smoke
```

---

## 5. 给协作者的工作流

1. **mechanical (Dave)**:本表 §2.1 的 4 个 fixture + 3 条 smoke 断言,P0-7 内交付
2. **fullstack**:§2.2 的 4 个 fixture + 3 条 smoke,与 03 §6/§7 路由表 + /health endpoint 同步
3. **algorithm**:§2.3 的 4 个 fixture + 3 条 smoke,P0-4 完成时一起交;§2.4 §2.5 P1 阶段补
4. **cost**:§2.8 的 2 个 fixture + 2 条 smoke,P0-5 完成时一起交;§2.6 §2.7 §2.9 P1 阶段补
5. **hardware**:§2.10 §2.11 P0 占位即可,P3 阶段重写
6. **testing(本人)**:父根 §1 全部 fixture + `tests/test_e2e_design_to_print.py` 框架(skip pending fixtures)
