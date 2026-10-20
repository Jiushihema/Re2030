# Re2030 交接文档（AI 协作入口）

> 本文件写给接手的开发人员 / AI 协作体，说明当前代码状态、运行方式、接口契约与下一步任务。
> 仓库：https://github.com/Jiushihema/Re2030
> 上游原型：https://github.com/Jiushihema/2030

## 1. 项目定位

这是“课题三仿真系统”的重构实现，目标是建立一套能运行正常业务、表达攻击影响、接入五类
外部观测、支撑识别与防御闭环的轻量仿真系统。系统按五个平面组织：

| 平面 | 目录 | 当前状态 |
| --- | --- | --- |
| 仿真底座 | `sim2030/base/` | 已完成（四层设备含外界层、通信、环境、观测接入） |
| 攻击 | `sim2030/attack/` | 已完成（计划解析、到期提交 `EffectRequest`） |
| 识别 | `sim2030/recognition/` | 已完成（引擎 + 单域/关联检测器接口，规则基线仅用于调通） |
| 防御 | `sim2030/defense/` | 已完成（策略选择、动作跟踪、恢复判据） |
| 演示 | `sim2030/presentation/` | 已完成（态势拓扑大屏 + 三行图例 + 报文粒子过滤 + 右侧监控详情 + 自动/单步推演） |

共用支撑：`application.py`（运行调度）、`records.py`（分流记录/回放）、`evaluation.py`（独立评估）、
`scenario.py`（场景校验与设备工厂）、`contracts.py`（数据契约）、`constants.py`（常量）。

约束：后端仅用 Python 标准库，无第三方依赖；测试使用 `unittest`。当前 `python -m unittest discover -s tests -t .`
共 **150 项**，全部通过。`substation-network-attack` 按既有策略不出现在 `GET /api/scenarios` 列表，
`tests/test_network_attack_scene.py` 已改为验证“列表隐藏但可按 ID 创建运行”。

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
├── scenarios/substation-em-attack.json # 电磁攻击场景（电流互感器读数篡改 + 频谱检测节点）
├── scenarios/substation-time-attack.json # 授时攻击场景（针对授时系统-2 的时钟欺骗 + 检测节点）
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
  自动推演使用 `step`，单步按钮使用 `step_once`；后者仅接受就绪或暂停状态，完成后保持暂停。开始、暂停、停止的回执包含权威状态 `summary`，`system` 视图额外提供 `dt_us`。
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

1. **拓扑图**：内联 SVG 按 `external / station / bay / process` 四层绘制 25 个设备与 34 条链路；链路端点取自 `topology.links[].endpoint_a[0]` → `endpoint_b[0]`。
   “外界层”位于站控层之上：授时卫星（`gnss_sat`）无线接入两套授时系统，其他电站（`peer_station`）无线接入站端监控。
   授时/采集链路为并列两列：`time_svc`+`mu`+`ct_current` 与 `time_svc_2`+`mu_2`+`ct_current_2`，分别在 `x=465` 与 `x=735`；卫星居中（`x=600`）同时向两套授时系统广播时间基准。冷却系统控制接口与冷却风机占 `x=870`。
   冷却风机右侧是**风机状态监测组**：风机振动/电流/转速三台传感器竖排于 `x=1005`，经物理耦合读取冷却风机的运行量（`vibration_mm_s`、`motor_current_a`、`rotation_speed_rpm`，额定 3.2 mm/s、8.5 A、1480 r/min），三路采集接线汇入 `fan_condition_monitor`（风机状态监测采集装置，`x=1140`）；该采集装置接过程网 P9，但输出未接业务链路，只做本地监测（与传感器攻击场景一致），因此测控装置的量测来源仍是两套合并单元。
   冷却风机启停会**实际改变变压器油温**：工况按 `electrical.rated_load_mw=4.0` 给出负载率（当前负荷 2.0 MW → 0.5），变压器热参数 `thermal{max_heat_rise_c:50, cooling_factor:0.45, time_constant_s:20}` 决定温升与响应速度——不通风机稳态约 **42.5 ℃**，通风机约 **35.6 ℃**，时间常数 20 s 便于演示中直接看出变化（此前 `load_pu` 无来源，冷却对油温实际没有作用）。
   两套合并单元都经过程网把量测送往测控装置、过流保护装置与差动保护装置：测控装置按来源分组上送（`sources`），站端如实分列显示并提示“两套量测不一致”；过流保护逐来源判断，任一来源持续越限即跳闸并标注触发来源；差动保护（`diff_prot`，间隔层）比较两套量测的绝对差，越限即跳闸，整定值可在演示平面修改。`ct_current_2` 通过 `channel: "ct_current"` 与首套共用同一量测通道名。
2. **设备状态可视**：
   - `effects` 非空 → 红色（受攻击作用影响）
   - 出现在 `recognition[].target_asset_ids / affected_asset_ids` → 琥珀色（疑似异常）
   - 正常 → 绿色
   - 状态文字显示开关 `position`、变压器 `tap_position`、冷却 `running`、补偿 `connected` 等；不再展示裸 `device_id`/端口小字。
3. **三行图例**：链路定义 / 节点状态 / 报文粒子；报文粒子按 `data`（采样/状态）与 `command`（命令）等大类过滤展示。
4. **右侧监控详情**：常态展示站端监控系统与测控装置的最近接收、最近下发、最新采样、命令执行与执行反馈，面向人可读。
5. **设备控制面板**：左侧不再固定下发业务操作，而是按所选设备的可控制项动态显示（例如保护装置阈值、断路器合闸）。
6. **动态推演**：开始与暂停共用一个按钮，开始后自动推进；倍速仅有 0.125×、0.25×、0.5×、1×，默认 0.125×；1× 以每秒一步为基准。就绪或暂停时可单步推进，支持停止及历史时刻快照查看。
7. **tabs 保留**：`system/timeline/observations/recognition/defense/evaluation`。
8. **版式随场景加载**：节点坐标取设备 `layout`（`{"x","y"}`），分层带取场景 `layout.layers`
   （`key/title/x/y/width/height`），画布 `viewBox` 由版式外接矩形推导（可用 `layout.canvas` 覆盖）；
   链路图例只显示本场景实际存在的 `link_type`，"影响信号"图例仅在有 `interference` 链路时出现。
   设备缺 `layout` 或场景缺 `layout.layers` 时前端自动推导，新增场景无需改前端即可显示。
   拓扑加载后按 `min(视口/画布)` **自动缩放铺满并居中**，不出现滚动条；左键拖动自由平移（transform
   实现，与内容是否溢出无关），`Ctrl+滚轮` 以光标为锚点缩放（20%–300%）。
   “重置视图”按钮固定在拓扑框右上角，只在比例或位置偏离默认（自动铺满 + 居中）时出现。
   窗口变化且未手动操作过时会自动重新铺满。注意“铺满”取长宽较小比例，画布高度不同的场景比例略有差异
   （实测 em-attack 92% / live 96%，节点尺寸差约 3%）。

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

## 6.1 GOOSE 接收实验：保护判据改为可选启用

问题：在 `substation-goose-lab` 里下发 DDoS 后，过流保护装置从不下发分闸。定位到两个叠加原因：

1. `scenarios/substation-goose-lab.json` 把 `prot.parameters.external_trip_replay` 固定为 `true`，`ProtectionDevice.step()`
   因此在 `sim2030/base/bay.py:76` 提前返回，保护判据从未被执行。
2. 加载运行时 `goose_options` 始终为空，实际样本恒为场景默认的 `forgery`——该样本不投入故障，
   线路电流只有约 115 A，低于 200 A 定值。

改动（4 个文件 + 2 处文档）：

- `sim2030/goose_lab.py`：`configure_lab` 接受 `protection` 布尔选项，按样本给默认值（`normal`/`dos`/`suppression`
  默认启用，`forgery` 默认停用）并写入 `external_trip_replay`；新增 `TRIP_SOURCES`；`receive_control` 把带
  `protection_trip` 标记的命令直接转交 `ctl_brk`，**不进入共享接收队列**，同时记录 `protection_trips`/`trip_source`；
  `view()` 新增 `protection_enabled`、`threshold_a`、`trip_source`、`trip_source_label`、`protection_trip_count`。
- `sim2030/base/bay.py`：保护判据产生的命令载荷增加 `protection_trip: true` 与 `sample_value`，供上式分流。
- `sim2030/presentation/static/app.js`：设备响应面板显示保护判据状态、定值与"本次分闸由谁触发"。
- `tests/test_goose_capture.py`：新增 2 个用例（判据默认值 + 显式覆盖 + 非法值拒绝；保护判据跳闸在队满时仍能下发）。
- 文档：`docs/GOOSE报文接入.md` 新增「两条跳闸通道」与本次修正说明，含真实电网依据与结论边界。

实验结论的姿态：**保护判据后备通道不受接收队列洪泛影响；GOOSE 回放跳闸与遥控命令仍共用该 FIFO**。
后者是刻意收窄的假设，不能对外表述为"DDoS 会阻塞真实变电站的保护跳闸"。

实验条件（样本 / 接收策略 / 终端处理能力 / 保护判据）后来固化成场景自身的一部分，页面不再有选择器：
断路器智能终端面板管接收策略与处理能力，网络攻击节点管攻击注入，`layout.goose_lab` 管样本与判据默认值。
`prot.parameters.external_trip_replay` 在 JSON 里写着 `true`，它正好等于本场景默认样本 `forgery` 的取值
（`external_trip_replay = not protection_enabled`，未投入故障即停用判据、回放跳闸），但加载时 `configure_lab` 总会按样本重算并覆盖它，
因此换样本时不需要改这个字段。

## 6.2 GOOSE 网络攻击：攻击能力移到红色攻击节点（本次）

问题：攻击注入能力原先挂在过程网交换机上（点击交换机 → “下发攻击报文”）。但 `sw-process` 是**部署资产而不是设备**
（不在任何场景的 `devices` 名单里），所以它既没有 `describe_controls()`，也无法成为 `start_attack`/`stop_attack` 的目标；
把"攻击"当成交换机自身被控，也与"攻击者接入过程网"的实际场景不符。

改动：新增设备类型 `network_attack_source`（`sim2030/base/security.py` 的 `NetworkAttackNode`），在
`scenarios/substation-goose-lab.json` 里以 `net_attack_node`（红色节点，`layer: process`，坐标 1295/580）接入 `sw-process`，
由 `layout.goose_lab.attack_origin` 声明：

```json
"attack_origin": {"asset_id": "net_attack_node", "name": "网络攻击节点",
                  "switch_id": "sw-process", "assumed_compromised": true, "capability": "frame_injection"}
```

- `sim2030/base/security.py`：`NetworkAttackNode.describe_controls()` 返回三个下发命令（伪造跳闸帧 / 消息抑制帧 / 批量下发）。
- `sim2030/scenario.py`：`DEVICE_TYPES` 注册 `"network_attack_source"`。
- `sim2030/goose_lab.py`：注入报文的 `sender_id`、下发事件文案、接收记录来源、未自动下发的提示语都取自 `attack_origin`
  （缺省回落到 `sw-process`，旧场景与旧运行记录不受影响）。
- `sim2030/application.py`：攻击下发目标由硬编码的 `"sw-process"` 改为场景声明的攻击节点（`_attack_node_locked()` +
  `ATTACK_DISPATCH_ACTIONS`）。交换机目标现在返回 `未知目标设备 sw-process`。
- 前端：`#attack-node-panel` 承载三个攻击按钮与批量输入；`#switch-control-panel` 只剩端口清单与镜像说明；
  `nodeKindClass` 把 `network_attack_source` 也映射到红色 `node-kind-attack`；`sendAttackFrame` 的目标改为 `attackOriginId()`。
  交换机**保持常规配色**：`goose-attached` 变体与 `.network-asset.goose-attached` 着色规则已删除，
  只在节点下方多一行"有攻击节点接入"文字、面板内多一句提示。
- `network-topology.js`：攻击节点是设备而非部署资产，额外生成一条 `attack-access` 红色虚线（节点顶边垂直上联到交换机底边）。
- 测试：`test_goose_capture.py` 新增 `test_attack_node_owns_the_injection_actions`、
  `test_attack_origin_decides_the_frame_source`；`test_network_topology.cjs` 新增攻击接入线用例。

## 7. 协作规范

- 遵循 `docs/AGENTS.md`：不做无关重构；新增功能必须补充或更新测试；不提交密钥/`.env`。
- 前端改动集中在 `sim2030/presentation/static/`，必要时改 `views.py`/`server.py` 的视图组装，但保持接口契约向后兼容。
- 后端改动保持“标准库 only”，跑通 `python -m unittest` 后再提交。
- 提交信息尽量写清楚“改了什么 / 为什么 / 影响哪些接口”。
- 若多人/AI 并行：后端与前端分开分支，避免同时改 `application.py`/`server.py` 造成冲突。

## 8. 验证与已知风险

- 验证命令：`python -m unittest discover -s tests -t .` → `Ran 150 tests`，全部通过；
  `node --test tests/test_network_topology.cjs tests/test_measurement_sources.cjs tests/test_simulation_player.cjs tests/test_cyber_demo.cjs` → 52 passed；
  前端语法：`node --check sim2030/presentation/static/{app,simulation-player,network-topology}.js` 均通过。
- 演示页面已经完成态势拓扑大屏；使用 `substation-attack-demo` 自动推演到约 1.5s 后可见：
  识别疑似异常（tap 琥珀色）→ 防御动作 planned/executing/succeeded → 攻击结束后恢复正常。
- `substation-live.json` 未启用 `recognition/defense`，`attacks=[]`，用于长周期正常业务态势；
  如需演示告警/攻防闭环，使用 `substation-attack-demo.json`。
- `substation-em-attack.json` 已**与基准场景同步**（25 台设备 / 34 条链路，含复数授时/采集链、差动保护、风机状态监测组），在此之上新增“电磁攻击节点”（`em_attack_node`，过程层 `x=465, y=800`，电流互感器正下方）与“检测节点”（`spectrum_detect_node`，过程层 `x=600, y=800`，电压互感器正下方；过程层分层带加高到 320）。干扰链路 `L-em-ct` 从攻击节点顶边垂直接到 `ct_current` 底边；检测节点通过 `layout.report_points` 从右边缘 `x=690` 出发，沿 `y=824` 向右到 `x=1675`，沿两台 IDS 连线右侧上行到 `y=142`，再左接到安全监测平台右下侧。攻击为**演示平面手动触发**（点选攻击节点 → “开始攻击”/“结束攻击”，对应 `start_attack`/`stop_attack`），作用为对 `ct_current` 施加 `reading_offset=+40A`。基准场景有两套互感器和差动保护：只篡改一套 CT 会先产生约 40 A 差流，随后差动保护越限跳闸、断路器分闸；这是预期后果，不是攻击结束后的自动恢复。
- `substation-sensor-attack.json` 已**与基准场景同步**，新增“传感器攻击节点”（`sensor_attack_node`，过程层 `x=1140, y=588`，振动传感器右侧）与“检测节点”（`spectrum_detect_node`，过程层 `x=1260, y=588`，攻击节点右侧，允许超过程序层右边界）。目标为风机振动传感器 `fan_vibration`，`reading_offset=40.0`；干扰链路 `L-sensor-fan_vibration` 从攻击节点左边缘水平接到振动传感器右边缘。检测节点通过 `layout.report_points` 从右边缘 `x=1350` 出发，向右到 `x=1675`，沿两台 IDS 连线右侧上行到 `y=142`，再左接到安全监测平台右下侧。风机运行时手动攻击后振动读数 3.2 → 43.2 mm/s，风机电流/转速读数不受影响，结束攻击后恢复。
- `substation-network-attack.json` 是**另一套前端只读展示场景**，不是 GOOSE 接收实验；已与基准同步（25 台设备 / 34 条链路），只保留 `layout.cyber_demo` 的七阶段配置，场景列表仍按既有策略隐藏，可按场景 ID 创建运行。
- `substation-goose-lab.json` 已**与基准场景同步**（26 台设备 / 34 条链路），保留 `layout.packet_replay` 与 `layout.goose_lab`，新增红色“网络攻击节点”（`net_attack_node`，`network_attack_source`，过程层 `x=1295, y=580`，过程网交换机正下方、设备分层区之外）。断路器保持初始合闸（`initial_position: closed`、`default_duration_ms: 40`），并清空基准的自动操作序列；GOOSE 样本、接收队列、保护判据后备通道和手动下发行为不变。
- `substation-time-attack.json` 已**与基准场景同步**（同样的 25 台设备/34 条链路、复数授时链、差动保护、
  风机状态监测组与版式），额外新增：
  - “攻击节点”（`time_attack_node`，`em_attack_source`，站控层 `x=1005`）放在**授时系统-2 右侧**同一排，与授时系统-2 留出两列间距，
    `target_asset_id: time_svc_2`、`effect_type: timing_offset`（+100 ms）；红色虚线干扰链路
    `L-time_attack-time_svc_2` 接到 `time_svc_2.gnss`，走线不额外声明——两个节点同排，连线就是一条水平直线
    （`y=204`），正好从检测节点（底边 `y=198`）下方 6 px 穿过。手动触发后授时系统-2 的
    `sync_state` 变为 `spoofed`、`timing_offset_us=100000`，第一套授时系统不受影响；结束攻击后恢复。
  - “检测节点”（`time_detect_node`，`spectrum_detector`，站控层 `x=870, y=150`）放在两个节点之间的空档，
    **顶部与站控层分层带顶部对齐**（占 `y=150..198`，水平居中在连线中点上方）；并在 `network-topology.js` 中
    按“检测节点 → 安全监测平台”生成一条监测上报线（`<device>:report`，与 IDS 管理口同一样式），表示检测结论上报平台。
- 活动运行的 `time_us` 由步进推进。前端点击“开始推演”会自动按所选速度串行调用 `step`；暂停／停止先停止调度并等待在途步骤完成。`step_once` 在就绪或暂停时原子推进一步，完成后保持暂停，到期则保持 finished。
- 界面自检：`node tools/ui_screenshot.mjs --url http://127.0.0.1:8765/ --scenario <id> --mode attack|none --out shot.png`
  可无头启动 Edge/Chrome、自动创建运行并截图，脚本同时打印节点/链路/画布/图例/传感器读数用于核对。
