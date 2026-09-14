# Re2030 交接文档（AI 协作入口）

> 本文件写给接手的开发人员 / AI 协作体，说明当前代码状态、运行方式、接口契约与下一步任务。
> 仓库：https://github.com/Jiushihema/Re2030
> 上游原型：https://github.com/Jiushihema/2030

## 1. 项目定位

这是“课题三仿真系统”的重构实现，目标是建立一套能运行正常业务、表达攻击影响、接入五类
外部观测、支撑识别与防御闭环的轻量仿真系统。系统按五个平面组织：

| 平面 | 目录 | 当前状态 |
| --- | --- | --- |
| 仿真底座 | `sim2030/base/` | 已完成（三层设备、通信、环境、观测接入） |
| 攻击 | `sim2030/attack/` | 已完成（计划解析、到期提交 `EffectRequest`） |
| 识别 | `sim2030/recognition/` | 已完成（引擎 + 单域/关联检测器接口，规则基线仅用于调通） |
| 防御 | `sim2030/defense/` | 已完成（策略选择、动作跟踪、恢复判据） |
| 演示 | `sim2030/presentation/` | 已完成（态势拓扑大屏 + 三行图例 + 报文粒子过滤 + 右侧监控详情 + 自动/单步推演） |

共用支撑：`application.py`（运行调度）、`records.py`（分流记录/回放）、`evaluation.py`（独立评估）、
`scenario.py`（场景校验与设备工厂）、`contracts.py`（数据契约）、`constants.py`（常量）。

约束：后端仅用 Python 标准库，无第三方依赖；测试使用 `unittest`。当前 `python -m unittest` **50 项全部通过**。

## 2. 目录结构

```
Re2030/
├── sim2030/
│   ├── application.py        # 五平面编排 + 单步调度 + get_view 展示视图
│   ├── contracts.py          # 数据契约（场景/消息/作用/动作/观测/识别/防御/评估）
│   ├── records.py            # JSON Lines 分流记录 + 回放读取
│   ├── evaluation.py         # 独立评估（识别/防御/业务指标）
│   ├── scenario.py           # 场景加载、校验、设备工厂
│   ├── constants.py
│   ├── base/                 # 底座：device/process/bay/station/engine/environment/communication/observations
│   ├── attack/               # 攻击平面
│   ├── recognition/          # 识别平面
│   ├── defense/              # 防御平面
│   └── presentation/         # 演示平面：server.py + views.py + static/(index.html app.js style.css)
├── scenarios/substation-live.json      # 变电站长周期运行态势场景
├── scenarios/substation-attack-demo.json # 攻防演示场景（攻击→识别→防御→评估闭环）
├── scenarios/observations/           # 文件回放观测数据（events.jsonl）
├── tests/                    # unittest 回归测试
├── docs/                     # 设计文档（含接口规范）
├── main.py                   # 无界面运行 / --ui 启动演示平面
├── README.md
└── HANDOFF.md                # 本文件
```

设计文档索引（均位于 `docs/`）：

- `课题三仿真系统详细开发设计.md`：文件树、类、函数与调用关系（最核心）。
- `课题三仿真系统重构开发文档.md`：架构组成、五平面职责、运行与建模原则。
- `系统角色设计.md`：攻击者 / 防御者 / 各平面角色与字段。
- `五类监测接口数据交付规范.md`：五类外部观测数据规范。
- `interfaces/monitoring-event.schema.json`：监测事件 JSON Schema。
- `AGENTS.md`：项目级协作规范（不要提交密钥、不要做无关重构、改动要补测试）。

> `docs/references/` 与 `docs/*.docx` 为大型二进制参考资料，已在 `.gitignore` 排除（本地 `D:\2030\docs` 仍保留），
> 需要时可向仓库负责人索取或本地查阅。

## 3. 快速运行

环境：Python 3.10（当前机器使用 `D:\python\python.exe`），无第三方依赖。

```powershell
cd D:\2030\codes

# 无界面跑到结束（长周期正常业务态势）
D:\python\python.exe main.py --scenario scenarios/substation-live.json --output runs

# 无界面跑到结束（攻防演示，跑攻击→识别→防御→评估闭环）
D:\python\python.exe main.py --scenario scenarios/substation-attack-demo.json --output runs

# 启动演示平面 UI（想看攻防效果推荐用这个场景）
D:\python\python.exe main.py --ui --scenario scenarios/substation-attack-demo.json --output runs --port 8000
```

浏览器打开 `http://127.0.0.1:8000`。

跑测试：

```powershell
cd D:\2030\codes
D:\python\python.exe -m unittest discover -s tests -t . -v
# 预期：Ran 50 tests ... OK
```

## 4. 演示平面 HTTP 接口契约

`sim2030/presentation/server.py` 用标准库 `ThreadingHTTPServer` 提供接口，返回统一
JSON（`{"status": ...}` 或 `{"error": ...}`；静态资源走 `__raw__`）。路径：

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| GET | `/` `/index.html` `/app.js` `/style.css` | 静态页面 |
| GET | `/api/scenarios` | 场景列表：`scenario_id,name,device_count,attack_count,recognition_enabled,defense_enabled` |
| POST | `/api/runs` | body `{scenario_id}`，返回 `{run_id, scenario_id, status, mode}`，status 初始 `ready` |
| POST | `/api/runs/{run_id}/operations` | 控制/业务操作，见下 |
| GET | `/api/runs/{run_id}/views?view=system\|timeline\|observations\|recognition\|defense\|evaluation\|snapshot\|status\|capabilities\|management` | 展示视图 |
| GET | `/api/runs/{run_id}/evidence` | 按 provider_id/event_id/start/count 读取证据片段 |

### 4.1 控制/业务操作 `POST .../operations`

body 示例：

```json
{"request_id":"r1","target_asset_id":"run","action_type":"start"}
{"request_id":"r2","target_asset_id":"run","action_type":"step"}
{"request_id":"r3","target_asset_id":"brk","action_type":"close"}
```

- `target_asset_id == "run"` 或 `action_type ∈ {start, pause, stop, step, start_run, pause_run, stop_run}` 走控制通道。
- `start` / `pause` / `stop` 改变运行状态。
- **`step`**：推进一个仿真步（调用 `Application.step()`），返回 `{"status":"accepted","action":"step","summary":{...}}`。
  这是本仓库为演示页面新加的接口，用于前端“自动运行/步进”，否则 UI 只改状态、时间不前进。
- 其他 `target_asset_id` 走业务操作，进入 `_pending_operations`，在下一步边界执行。
- 重复 `request_id` 返回 `{"status":"duplicate","previous":...}`，不会重复执行。

### 4.2 视图数据契约（前端重点）

`GET /api/runs/{run_id}/views?view=system` 分两种路径：

1. **活动运行**（`run_id == self.application.run_id`）：返回 `Application.get_view("system")`。
2. **已结束运行**（目录存在）：返回 `presentation_views.build_system_view(run_dir)`。

两者字段已尽量对齐，前端需兼容以下差异：

#### 活动运行 `system`（实时）

```json
{
  "run_id": "...",
  "status": "ready|running|paused|finished|error",
  "time_us": 0,
  "topology": {
    "devices": [
      {"device_id":"station","device_type":"station_control","layer":"station","name":"站端监控系统","ports":{...}}
    ],
    "links": [
      {"link_id":"L-station-mc","endpoint_a":["station","mc_breaker"],"endpoint_b":["mc","up"],"link_type":"wired","parameters":{...},"references":[]}
    ]
  },
  "management": {
    "brk": {"asset_id":"brk","device_type":"switch","layer":"process","state":{"position":"open"},"effects":{}}
  },
  "observations": {"events":[...], "missing":[...], "late":[...], "rejected":[...]},
  "recognition": [ /* RecognitionResult asdict 列表 */ ],
  "defense": [ /* DefenseResult asdict 列表 */ ]
}
```

- 设备状态统一从 `management[device_id].state / .effects` 取（`get_management` 返回的是 `Device.snapshot()`）。
- `links[].endpoint_a / endpoint_b` 是 `[device_id, port]` 两元素数组。

#### 已结束运行 `system`（回放）

```json
{
  "run_id": "...",
  "scenario_id": "...",
  "status": "finished",
  "time_us": 0,
  "topology": {
    "devices": [
      {"device_id":"...","device_type":"...","layer":"...","state":{...},"effects":{...}}
    ],
    "links": [ /* 同活动运行结构 */ ]
  },
  "device_snapshots": {"device_id": {"asset_id":"...","device_type":"...","layer":"...","state":{...},"effects":{...}}},
  "observations": {"event_count": 0, "by_source": {}, "missing": [...], "late": [...]},
  "recognition": [...],
  "defense": [...]
}
```

- 回放路径设备状态在 `topology.devices[].state/effects` 与 `device_snapshots[device_id].state/effects` 两处都有，
  前端取 `management || device_snapshots || 空` 即可归一。
- 回放路径 `observations` 是摘要计数（`event_count/by_source/missing/late`），实时路径是 `events/missing/late/rejected` 列表。

其它视图：

- `view=timeline`：`attacks / attack_submissions / recognitions / defenses / business`（实时路径只有 `attacks/recognition/defense` 等子集）。
- `view=observations`：`{"observations":{"events":[...]}}`。
- `view=recognition`：`{"recognition":[...]}`。
- `view=defense`：`{"defense":[...]}`。
- `view=evaluation`：`{"evaluation":{...}}`（运行未结束为 `{"evaluation":null,"reason":...}`）。
- `view=snapshot`：`{"snapshots":{...}}`（按 `time_us` 回放真值快照）。

## 5. 当前演示平面已实现的能力

前端 `sim2030/presentation/static/` 已完成态势拓扑页，可直接“双击即跑”，无构建工具：

1. **拓扑图**：内联 SVG 按 `station / bay / process` 三层绘制 15 个设备与 17 条链路；链路端点取自 `topology.links[].endpoint_a[0]` → `endpoint_b[0]`。
2. **设备状态可视**：
   - `effects` 非空 → 红色（受攻击作用影响）
   - 出现在 `recognition[].target_asset_ids / affected_asset_ids` → 琥珀色（疑似异常）
   - 正常 → 绿色
   - 状态文字显示开关 `position`、变压器 `tap_position`、冷却 `running`、补偿 `connected` 等；不再展示裸 `device_id`/端口小字。
3. **三行图例**：链路定义 / 节点状态 / 报文粒子；报文粒子按 `data`（采样/状态）与 `command`（命令）等大类过滤展示。
4. **右侧监控详情**：常态展示站端监控系统与测控装置的最近接收、最近下发、最新采样、命令执行与执行反馈，面向人可读。
5. **设备控制面板**：左侧不再固定下发业务操作，而是按所选设备的可控制项动态显示（例如保护装置阈值、断路器合闸）。
6. **动态推演**：支持开始/暂停/停止、单步步进、自动推演（1s/步）、历史时刻快照回放。
7. **tabs 保留**：`system/timeline/observations/recognition/defense/evaluation`。

## 6. 本次交接前的后端改动记录

相对上游 `2030`，本仓库在演示相关接口上做了以下兼容性改动（均已通过 50 项测试）：

1. `application.py::_config_summary()` 新增 `links` 字段，使 `manifest.json` 保存拓扑链路；
   `presentation/views.py::build_system_view()` 因此能从 `manifest.get("links")` 取到回放拓扑（此前回放链路为空）。
2. `application.py::_system_view()` 新增 `status`；`build_system_view()` 新增 `status:"finished"`、
   `recognition`、`defense` 字段，让前端一张 `system` 视图即可画态势卡片。
3. `application.py` 新增 `step` 控制动作（`_submit_control` 中处理），并把 `submit_operation/step/get_view`
   用 `threading.RLock` 串行化，避免 `ThreadingHTTPServer` 下轮询与步进并发读写的竞态。
4. 前端三栏态势页已落地：`index.html/app.js/style.css` 当前是完整拓扑大屏，不再是表格基线；
   如需继续改前端，仍集中在 `sim2030/presentation/static/`，保持 `/api` 接口路径不变。
5. 新增演示场景 `scenarios/substation-attack-demo.json` 与回放观测 `scenarios/observations/events.jsonl`：
   - 攻击：`attack_type=electromagnetic`，`effect_type=reading_offset`，目标 `oil_temp`，偏移 `+55℃`，1.0s–3.0s 生效；
   - 识别：`recognition.enabled=true`，1.0s 窗口，规则基线对 `oil_temp_c` 异常产生 suspected；
   - 防御：`defense.enabled=true`，`business_compensation` 对 `tap` 做油温补偿，planned→executing→succeeded；
   - 评估：`overall_score=1.0`，识别 delay=500ms，防御 delay=500ms，主线闭环跑通。

## 7. 协作规范

- 遵循 `docs/AGENTS.md`：不做无关重构；新增功能必须补充或更新测试；不提交密钥/`.env`。
- 前端改动集中在 `sim2030/presentation/static/`，必要时改 `views.py`/`server.py` 的视图组装，但保持接口契约向后兼容。
- 后端改动保持“标准库 only”，跑通 `python -m unittest` 后再提交。
- 提交信息尽量写清楚“改了什么 / 为什么 / 影响哪些接口”。
- 若多人/AI 并行：后端与前端分开分支，避免同时改 `application.py`/`server.py` 造成冲突。

## 8. 验证与已知风险

- 验证命令：`D:\python\python.exe -m unittest discover -s tests -t . -v` → `Ran 50 tests ... OK`。
- 演示页面已经完成态势拓扑大屏；使用 `substation-attack-demo` 自动推演到约 1.5s 后可见：
  识别疑似异常（tap 琥珀色）→ 防御动作 planned/executing/succeeded → 攻击结束后恢复正常。
- `substation-live.json` 未启用 `recognition/defense`，`attacks=[]`，用于长周期正常业务态势；
  如需演示告警/攻防闭环，使用 `substation-attack-demo.json`。
- 活动运行的 `time_us` 只在调用 `step` 后前进；纯点“开始”不会自动推进，前端需打开“自动推演步进”或手动单步。
