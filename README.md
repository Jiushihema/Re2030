# 2030 仿真系统（重构）

本目录为课题三仿真系统的重构实现。系统按“仿真底座 / 攻击 / 识别 / 防御 / 演示”五个平面
组织，外加场景装配、运行调度、记录与独立评估等共用支撑。当前实现仅使用 Python 标准库，
不依赖第三方包。

## 目录结构

- `sim2030/contracts.py`：跨模块数据契约（场景、消息、作用、动作、观测、识别/防御/评估结果等）。
- `sim2030/constants.py`：层级、连接类型与消息业务类型常量。
- `sim2030/scenario.py`：场景读取、校验与设备工厂注册。
- `sim2030/application.py`：单场景运行调度，按固定步序编排五个平面并负责分流记录。
- `sim2030/records.py`：运行记录按流分文件保存（JSON Lines）与回放读取。
- `sim2030/evaluation.py`：从已保存记录独立计算识别、防御、业务指标，支持基准/防御对比。
- `sim2030/base/`：过程层 / 间隔层 / 站控层设备、通信网络、工况影响模型、引擎与五类外部观测接入。
- `sim2030/attack/`：攻击计划解析与到期 `EffectRequest` 生成。
- `sim2030/recognition/`：识别引擎与单域/关联检测器接口。
- `sim2030/defense/`：防御策略选择、动作跟踪与恢复判据。
- `sim2030/presentation/`：标准库 HTTP 服务、展示视图与静态页面。
- `scenarios/substation-live.json`：基础 10kV 变电站场景。
- `scenarios/substation-network-attack.json`：基于基础场景的网络攻击前端展示。
- `scenarios/substation-goose-*.json`：message_core 交付包的正常、伪造、抑制、DoS 离线回放。
- `scenarios/substation-integrated-attack.json`：电磁、GOOSE、传感器、授时四类攻击/检测节点集成的拓扑场景。
- `tests/`：使用标准库 `unittest` 的回归测试。
- `main.py`：无界面运行入口或演示平面入口。

## 运行场景（无界面）

```powershell
cd codes
D:\python\python.exe main.py --scenario scenarios/substation-live.json --output runs
```

## 启动演示平面 UI

```powershell
cd codes
D:\python\python.exe main.py --ui --scenario scenarios/substation-live.json --output runs --port 8000
```

浏览器打开 `http://127.0.0.1:8000`。

Windows 下可直接双击 `start.bat` 一键启动（自动定位 Python，可选端口：`start.bat 9000`，默认 8000，启动后自动打开浏览器）。

推演控制浮层悬停在拓扑画布左侧（可拖拽、可收起），“开始推演 / 暂停推演”是同一个按钮，开始后会按所选速度自动推进。
倍速档位提供 0.125×、0.25×、0.5×、1×、2×、5× 六档，默认 0.125×。1× 沿用原来的每秒一步；六档分别每 8、4、2、1、0.5、0.2 秒推进一步。每步的仿真时间仍由场景 `dt_us` 决定。
暂停后可继续或单步推进；停止会结束本次运行，需要重新加载场景才能再次开始。
调速时不会积压步进请求，实际速度受浏览器和后端处理速度影响。

“变电站网络攻击场景”提供未授权控制命令的分阶段示意。加载后可在左侧选择攻击演示阶段，
查看命令路径、潜在执行风险及 IDS 可核验线索。阶段选择不会执行攻击、改变设备状态或生成真实告警。
资料依据与实现范围见 [网络攻击展示场景依据](docs/网络攻击展示场景依据.md)。

GOOSE 报文回放需先导入本地交付包：`python tools/import_message_core.py "路径/message_core.zip"`。
导入后选择“变电站网络攻击场景”。场景自带一组固定的实验条件：过程网交换机旁有一台红色**网络攻击节点**，点选它即可“下发攻击报文”，每次手动发送一帧红色报文，也可设置批量数量并下发洪泛流量。点击断路器智能终端可修改队列容量、处理速率及入口过滤，并查看逐帧接收结果。右侧展示设备后果、队列、曲线及逐帧判定，底部时间线可追溯到原始帧。模型条件和局限见接入说明。
样本决定工况与回放报文，并给出“保护判据是否启用”的默认值：`normal`/`dos`/`suppression` 会投入 900 A 故障，默认启用过流保护装置判据；`forgery` 不投入故障，默认停用判据。场景默认 `forgery`，因此默认看不到保护判据动作；要观察判据动作，把 `scenarios/substation-goose-lab.json` 的 `layout.goose_lab.sample` 改为 `dos`。保护判据越限后经**独立后备通道**直接跳闸，不占用智能终端的共享接收队列；GOOSE 回放跳闸与站控遥控命令才共用该队列，队满即丢。设备响应面板会标明本次分闸由哪一路触发。
原始数据和标签保存在 Git 忽略的 `data/message_core/`，接入说明见 [GOOSE 报文接入](docs/GOOSE报文接入.md)。

演示平面提供以下最小接口：

| 请求 | 作用 |
| --- | --- |
| `GET /api/scenarios` | 列出可选场景 ID、名称与接入模式。 |
| `POST /api/runs` | 创建运行，返回 `run_id` 与初始状态。 |
| `POST /api/runs/{run_id}/operations` | 提交启动/暂停/停止或业务操作，返回受理状态；重复 `request_id` 不重复执行。 |
| `GET /api/runs/{run_id}/views?view=...` | 返回 `system`/`timeline`/`observations`/`recognition`/`defense`/`evaluation` 等视图。 |
| `GET /api/runs/{run_id}/evidence` | 按提供方 ID、事件 ID 与片段范围读取证据；不接受任意文件路径。 |

运行产生的记录保存在 `runs/<run_id>/` 下，原始证据仍从场景配置指定的协同方数据目录读取。

## 运行测试

```powershell
cd codes
D:\python\python.exe -m unittest discover -s tests -t . -v
```

## 界面自检（可选）

需要本机安装了 Edge 或 Chrome，以及 Node 18+（脚本只用内置 `fetch` / `WebSocket`，无第三方依赖）。

```powershell
# 终端 1：启动演示平面
python main.py --ui --scenario scenarios/substation-em-attack.json --output runs --port 8765

# 终端 2：无头浏览器自动创建运行、步进、触发攻击并截图
node tools/ui_screenshot.mjs --url http://127.0.0.1:8765/ --scenario substation-em-attack --mode attack --out shot.png
```

该脚本会打印节点数、链路数、viewBox、链路图例与传感器读数，便于无人值守地核对界面效果。

## 关键约定

- 内部仿真时间统一为相对场景起点的整数微秒（`time_us` / `dt_us`）。
- 外部观测保留提供方原事件、原时间与原 `raw`；固定文件回放不会提前交付未来数据。
- 识别/防御接口不接收 `SimulationEngine`、完整 `ScenarioConfig`、攻击计划或真值读取器。
- 防御动作 `completed` 只表示执行完成，只有后续观测满足恢复判据才记为 `succeeded`。
- 识别/防御输出仅作为算法接入位置，规则基线只用于调通流程，不以预设攻击标签替代真实检测。
- 传感器节点展示的是本机采样“度数”（可能被攻击篡改）；一次侧“实际值”见拓扑图例“实际工况（真值）”，
  两者不一致时传感器读数会高亮提示。
- 拓扑版式随场景加载：设备 `layout` 给节点坐标，场景 `layout.layers` 给分层带，画布尺寸自动推导；
  链路图例只列出本场景实际出现的链路类型。
- 拓扑加载后默认自动缩放铺满视口并居中，不出现滚动条；左键拖动可自由平移，`Ctrl+滚轮` 以光标为锚点缩放，
  一旦比例或位置偏离默认，拓扑框右上角会出现“重置视图”按钮（点后回到自动铺满并居中）。

## 扩展入口

- 新设备：在对应设备文件新增模型，调用 `register_device` 注册类型。
- 新攻击：新增攻击计划步骤，必要时在 `base/environment.py` 注册新的 `EffectModel`。
- 新数据源：在 `base/observations.py` 实现新的 `ObservationSource`。
- 新识别方法：实现 `Detector` 并注册到 `RecognitionEngine`。
- 新防御方法：实现 `DefenseStrategy` 并注册到 `DefenseEngine`。
- 新场景/展示：增加场景 JSON、`presentation/views.py` 视图函数及页面区域。
