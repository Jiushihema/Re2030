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
- `scenarios/substation-live.json`：当前唯一 10kV 变电站场景。
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

浏览器打开 `http://127.0.0.1:8000`。演示平面提供以下最小接口：

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

## 关键约定

- 内部仿真时间统一为相对场景起点的整数微秒（`time_us` / `dt_us`）。
- 外部观测保留提供方原事件、原时间与原 `raw`；固定文件回放不会提前交付未来数据。
- 识别/防御接口不接收 `SimulationEngine`、完整 `ScenarioConfig`、攻击计划或真值读取器。
- 防御动作 `completed` 只表示执行完成，只有后续观测满足恢复判据才记为 `succeeded`。
- 识别/防御输出仅作为算法接入位置，规则基线只用于调通流程，不以预设攻击标签替代真实检测。

## 扩展入口

- 新设备：在对应设备文件新增模型，调用 `register_device` 注册类型。
- 新攻击：新增攻击计划步骤，必要时在 `base/environment.py` 注册新的 `EffectModel`。
- 新数据源：在 `base/observations.py` 实现新的 `ObservationSource`。
- 新识别方法：实现 `Detector` 并注册到 `RecognitionEngine`。
- 新防御方法：实现 `DefenseStrategy` 并注册到 `DefenseEngine`。
- 新场景/展示：增加场景 JSON、`presentation/views.py` 视图函数及页面区域。
