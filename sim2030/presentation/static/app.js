/* 2030 仿真系统演示平面：工业级深色态势感知拓扑大屏脚本 */
"use strict";

const state = {
  scenarios: [],
  selectedScenario: null,
  runId: null,
  currentView: "system",
  systemData: null,
  selectedDeviceId: null,
  pollTimer: null,
  autoStepTimer: null,
  renderedTopologyOnce: false,
  renderedRunId: null,
  particleFilter: "all",
  selectedPacketMessageId: null,
  zoom: 1,
  fitZoom: 0,
  canvasSize: null,
  pan: { x: 0, y: 0 },
  viewTouched: false,
  dragMoved: false,
};

const $ = (selector) => document.querySelector(selector);

function esc(value) {
  if (value === null || value === undefined) return "";
  return String(value)
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;");
}

async function api(method, path, body) {
  const options = { method, headers: {} };
  if (body !== undefined) {
    options.headers["Content-Type"] = "application/json";
    options.body = JSON.stringify(body);
  }
  try {
    const response = await fetch(path, options);
    return await response.json();
  } catch (err) {
    console.error("API error:", err);
    return { error: String(err) };
  }
}

function setStatus(text, isError = false) {
  const bar = $("#status-bar");
  if (bar) {
    bar.textContent = text;
    bar.title = text;
  }
  const dot = $("#live-dot");
  if (dot) {
    dot.className = isError ? "pulse-dot danger" : "pulse-dot";
  }
}

/* 格式化微秒时间为秒 */
function formatUs(us) {
  if (typeof us !== "number") return "0.000 s";
  return (us / 1000000).toFixed(3) + " s";
}

let packetToastTimer = null;

function packetFieldValue(value) {
  if (value === null || value === undefined || value === "") return "—";
  return value;
}

function formatPacketPayload(payload) {
  try {
    return JSON.stringify(payload || {}, null, 2);
  } catch (err) {
    return String(payload ?? "");
  }
}

function showPacketToast(text) {
  const toast = $("#packet-inspector-toast");
  if (!toast) return;
  toast.textContent = text;
  toast.classList.add("show");
  window.clearTimeout(packetToastTimer);
  packetToastTimer = window.setTimeout(() => toast.classList.remove("show"), 2200);
}

function openPacketInspector(message) {
  const inspector = $("#packet-inspector");
  const body = $("#packet-inspector-body");
  const copyStatus = $("#packet-inspector-copy-status");
  if (!inspector || !body || !message) return;

  const rawJson = JSON.stringify(message, null, 2);
  inspector.dataset.rawJson = rawJson;
  if (copyStatus) copyStatus.textContent = "";
  state.selectedPacketMessageId = message.message_id || null;
  syncPacketSelection();

  const fields = [
    ["报文 ID", message.message_id],
    ["发送方", message.sender_id],
    ["接收方", message.receiver_id],
    ["业务类型", message.business_type],
    ["源端口", message.source_port],
    ["目标端口", message.target_port],
    ["关联请求", message.related_request],
    ["创建时间", formatUs(message.created_time_us)],
    ["送达时间", formatUs(message.deliver_time_us)],
  ];

  body.innerHTML = `
    <div class="packet-field-list">
      ${fields.map(([label, value]) => `
        <div class="packet-field">
          <span class="packet-field-label">${esc(label)}</span>
          <code class="packet-field-value">${esc(packetFieldValue(value))}</code>
        </div>
      `).join("")}
    </div>
    <div class="packet-payload-section">
      <div class="packet-payload-title">payload（业务载荷）</div>
      <pre class="packet-payload-json">${esc(formatPacketPayload(message.payload))}</pre>
    </div>
  `;

  inspector.classList.add("open");
  inspector.setAttribute("aria-hidden", "false");
  document.body.classList.add("packet-inspector-open");
  const closeButton = $("#packet-inspector-close");
  if (closeButton) closeButton.focus();
}

function closePacketInspector() {
  const inspector = $("#packet-inspector");
  if (!inspector) return;
  state.selectedPacketMessageId = null;
  syncPacketSelection();
  inspector.classList.remove("open");
  inspector.setAttribute("aria-hidden", "true");
  document.body.classList.remove("packet-inspector-open");
}

async function copyPacketJson() {
  const inspector = $("#packet-inspector");
  const status = $("#packet-inspector-copy-status");
  const raw = inspector?.dataset.rawJson || "";
  if (!raw) return;

  try {
    if (navigator.clipboard?.writeText) {
      await navigator.clipboard.writeText(raw);
    } else {
      const textarea = document.createElement("textarea");
      textarea.value = raw;
      textarea.style.position = "fixed";
      textarea.style.opacity = "0";
      document.body.appendChild(textarea);
      textarea.select();
      document.execCommand("copy");
      textarea.remove();
    }
    if (status) status.textContent = "已复制";
  } catch (err) {
    if (status) status.textContent = "复制失败";
  }
}

function particleCategory(businessType) {
  if (businessType === "sampling" || businessType === "status" || businessType === "sync") return "data";
  if (businessType === "command" || businessType === "protection" || businessType === "feedback") return "command";
  return "other";
}

/* 加载场景列表 */
async function loadScenarios() {
  const payload = await api("GET", "/api/scenarios");
  state.scenarios = payload.scenarios || [];
  const list = $("#scenario-list");
  list.innerHTML = "";
  state.scenarios.forEach((scenario) => {
    const li = document.createElement("li");
    li.innerHTML = `<strong>${esc(scenario.name || scenario.scenario_id)}</strong>`;
    li.dataset.scenarioId = scenario.scenario_id;
    li.addEventListener("click", () => {
      state.selectedScenario = scenario.scenario_id;
      list.querySelectorAll("li").forEach((node) => node.classList.remove("selected"));
      li.classList.add("selected");
      $("#create-run").disabled = false;
    });
    list.appendChild(li);
  });

  if (state.scenarios.length > 0 && !state.selectedScenario) {
    list.querySelector("li")?.click();
  }
  setStatus(`已就绪，发现场景 ${state.scenarios.length} 个`);
}

/* 创建运行 */
async function createRun() {
  if (!state.selectedScenario) return;
  setStatus("正在创建仿真运行...");
  const payload = await api("POST", "/api/runs", { scenario_id: state.selectedScenario });
  if (payload.error) {
    setStatus(payload.error, true);
    return;
  }
  state.runId = payload.run_id;
  state.renderedTopologyOnce = false;
  $("#run-id").textContent = payload.run_id;
  setStatus(`运行已创建：${payload.run_id}`);

  startPolling();
  await refreshViews();
}

/* 运行控制 (start/pause/stop) */
async function control(action) {
  if (!state.runId) return;
  const payload = await api("POST", `/api/runs/${state.runId}/operations`, {
    request_id: `ctrl-${Date.now()}`,
    target_asset_id: "run",
    operation_type: action,
  });
  if (payload.error) {
    setStatus(`控制失败: ${payload.error}`, true);
  } else {
    setStatus(`已执行指令: ${action}`);
  }
  await refreshViews();
}

/* 单步推演 */
async function stepSimulation() {
  if (!state.runId) return;
  const payload = await api("POST", `/api/runs/${state.runId}/operations`, {
    request_id: `step-${Date.now()}`,
    target_asset_id: "run",
    operation_type: "step",
  });
  if (payload.error) {
    setStatus(`步进错误: ${payload.error}`, true);
  } else {
    const timeUs = payload.summary?.time_us ?? state.systemData?.time_us;
    setStatus(`步进成功：当前时刻 ${formatUs(timeUs)}`);
  }
  await refreshViews();
}

/* 预定义设备中文名映射 */
const DEVICE_NAME_MAP = {
  gnss_sat: "授时卫星",
  peer_station: "其他电站",
  em_attack_node: "电磁攻击节点",
  spectrum_detect_node: "检测节点",
  station: "站端监控系统",
  time_svc: "授时系统",
  prot: "过流保护装置",
  mc: "测控装置",
  ctl_brk: "断路器智能终端",
  ctl_tap: "分接机构控制接口",
  ctl_cool: "冷却系统控制接口",
  brk: "断路器",
  tap: "变压器",
  cool: "冷却风机",
  comp: "无功补偿支路",
  ct_current: "电流互感器",
  vt_voltage: "电压互感器",
  oil_temp: "油温传感器",
  mu: "合并单元",
};

/* 面向人类阅读的监控交互翻译辅助 */
const BUSINESS_TYPE_ZH = {
  sampling: "采样数据",
  status: "状态量测",
  sync: "对时同步",
  command: "控制命令",
  protection: "保护动作",
  feedback: "执行反馈",
};

const ACTION_ZH = {
  close: "合闸",
  open: "分闸",
  trip: "跳闸",
  set_tap: "调节分接",
  start: "启动",
  stop: "停止",
  connect: "投入",
  disconnect: "切除",
  business_compensation: "油温补偿",
};

const STATUS_ZH = {
  dispatched: "已下发",
  rejected: "已拒绝",
  accepted: "已受理",
  completed: "已完成",
  failed: "已失败",
  planned: "已计划",
  executing: "执行中",
  succeeded: "已成功",
  suspected: "疑似",
  normal: "正常",
  anomaly: "异常",
};

function deviceName(id) {
  return DEVICE_NAME_MAP[id] || id || "-";
}

function zhBusiness(type) {
  return BUSINESS_TYPE_ZH[type] || type || "其他";
}

function zhAction(action) {
  return ACTION_ZH[action] || action || "-";
}

function zhStatus(status) {
  return STATUS_ZH[status] || status || "-";
}

function fmtValue(value, digits = 2) {
  const n = Number(value);
  if (!Number.isFinite(n)) return String(value ?? "-");
  return n.toFixed(digits);
}

function extractSample(payload) {
  if (!payload || typeof payload !== "object") return null;
  const sample = payload.sample || payload;
  if (sample && typeof sample === "object" && sample.samples && Object.keys(sample.samples).length) {
    return sample;
  }
  return null;
}

function renderSample(sample) {
  if (!sample || !sample.samples) return "";
  const samples = sample.samples || {};
  const units = sample.units || {};
  const parts = [];
  if ("ct_current" in samples) parts.push(`线路电流 ${fmtValue(samples.ct_current)} ${units.ct_current || "A"}`);
  if ("vt_voltage" in samples) parts.push(`母线电压 ${fmtValue(samples.vt_voltage)} ${units.vt_voltage || "kV"}`);
  if ("oil_temp" in samples) parts.push(`油温 ${fmtValue(samples.oil_temp, 1)} ${units.oil_temp === "C" ? "℃" : (units.oil_temp || "℃")}`);
  if (parts.length === 0) {
    return Object.entries(samples).map(([k, v]) => `${esc(k)} ${fmtValue(v)}`).join(" · ");
  }
  return parts.join(" · ");
}

function monitorRow(timeUs, badge, text) {
  return `
        <div class="monitor-row">
          <span class="monitor-time">${formatUs(timeUs)}</span>
          <span class="monitor-type">${esc(badge)}</span>
          <span class="monitor-text">${text}</span>
        </div>`;
}

/* 设备控制面板：能力描述由后端下发，前端只负责渲染 */
async function loadDeviceControls(deviceId) {
  const panel = $("#device-control-panel");
  if (!panel) return;
  if (!state.runId || !deviceId) {
    panel.innerHTML = '<div class="device-control-empty">请在拓扑中点选设备</div>';
    return;
  }
  panel.innerHTML = '<div class="device-control-empty">正在读取设备控制项…</div>';
  const payload = await api("GET", `/api/runs/${state.runId}/devices/${deviceId}/controls`);
  if (payload.error) {
    panel.innerHTML = `<div class="device-control-empty">读取失败：${esc(payload.error)}</div>`;
    return;
  }
  renderDeviceControlPanel(payload, deviceId);
}

function renderDeviceControlPanel(payload, deviceId) {
  const panel = $("#device-control-panel");
  if (!panel) return;
  const controls = payload.controls || [];

  if (!controls.length) {
    panel.innerHTML = '<div class="device-control-empty">该设备无可控项，仅支持观测</div>';
    return;
  }

  panel.innerHTML = controls.map((control) => {
    if (control.kind === "parameter") return renderParameterControl(control, deviceId);
    if (control.kind === "command") return renderCommandControl(control, deviceId);
    return "";
  }).join("");

  bindControlPanelEvents();
}

function renderParameterControl(control, deviceId) {
  const value = control.value != null ? control.value : "";
  const unit = control.unit ? ` <span class="control-unit">${esc(control.unit)}</span>` : "";
  return `
    <form class="operation-form device-control-form" data-control-type="parameter" data-device-id="${esc(deviceId)}">
      <div class="form-group">
        <label>${esc(control.label || control.key)}</label>
        <div class="input-with-unit">
          <input type="number" min="${control.input?.min ?? 0}" step="${control.input?.step ?? 1}" value="${esc(value)}" data-control-key="${esc(control.key)}">
          ${unit}
        </div>
      </div>
      <button class="btn btn-action btn-block" type="submit">应用</button>
    </form>`;
}

function renderCommandControl(control, deviceId) {
  const params = control.params || [];
  const paramFields = params.map((p) => {
    const value = p.value != null ? p.value : "";
    return `
      <div class="form-group">
        <label>${esc(p.label || p.key)}</label>
        <input type="number" min="${p.input?.min ?? 0}" max="${p.input?.max ?? ""}" step="${p.input?.step ?? 1}" value="${esc(value)}" data-param-key="${esc(p.key)}">
      </div>`;
  }).join("");

  return `
    <form class="operation-form device-control-form" data-control-type="command" data-device-id="${esc(deviceId)}" data-action="${esc(control.action)}">
      ${paramFields}
      <button class="btn btn-action btn-block" type="submit">${esc(control.label)}</button>
    </form>`;
}

function bindControlPanelEvents() {
  document.querySelectorAll(".device-control-form").forEach((form) => {
    form.addEventListener("submit", submitDeviceControl);
  });
}

async function submitDeviceControl(event) {
  event.preventDefault();
  if (!state.runId) return;
  const form = event.currentTarget;
  const deviceId = form.dataset.deviceId;
  const controlType = form.dataset.controlType;

  if (controlType === "parameter") {
    const input = form.querySelector("[data-control-key]");
    const key = input ? input.dataset.controlKey : "";
    const value = parseFloat(input ? input.value : "");
    if (!key || Number.isNaN(value)) {
      setStatus("请输入有效参数值", true);
      return;
    }
    const payload = await api("POST", `/api/runs/${state.runId}/devices/${deviceId}/parameters`, { key, value });
    if (payload.error) {
      setStatus(`参数设置失败: ${payload.error}`, true);
    } else {
      setStatus(`已更新 ${deviceId} 参数 ${key}: ${value}`);
    }
    await refreshViews();
    await loadDeviceControls(deviceId);
    return;
  }

  if (controlType === "command") {
    const action = form.dataset.action;
    const params = {};
    form.querySelectorAll("[data-param-key]").forEach((input) => {
      const key = input.dataset.paramKey;
      const raw = input.value;
      const num = Number(raw);
      params[key] = Number.isFinite(num) ? num : raw;
    });

    const opPayload = {
      request_id: `op-${Date.now()}`,
      target_asset_id: deviceId,
      action_type: action,
    };
    if (Object.keys(params).length) opPayload.parameters = params;

    const payload = await api("POST", `/api/runs/${state.runId}/operations`, opPayload);
    if (payload.error) {
      setStatus(`下发失败: ${payload.error}`, true);
    } else {
      setStatus(`已下发指令: ${action}@${deviceId} (${payload.status || "ok"})`);
    }
    await refreshViews();
    await loadDeviceControls(deviceId);
  }
}


async function seekReplay() {
  if (!state.runId) return;
  const timeUs = $("#replay-time").value || "0";
  const payload = await api("GET", `/api/runs/${state.runId}/views?view=snapshot&time_us=${timeUs}`);
  renderSnapshot(payload.data || {});
  showSubView();
}

/* 轮询管理 */
function startPolling() {
  if (state.pollTimer) clearInterval(state.pollTimer);
  state.pollTimer = setInterval(async () => {
    if (state.runId) {
      await refreshViews();
    }
  }, 1200);
}

function updateAutoStep(enable) {
  if (state.autoStepTimer) {
    clearInterval(state.autoStepTimer);
    state.autoStepTimer = null;
  }
  if (enable) {
    state.autoStepTimer = setInterval(async () => {
      if (state.runId && state.systemData?.status === "running") {
        await stepSimulation();
      }
    }, 1000);
  }
}

/* 视图刷新入口 */
async function refreshViews() {
  if (!state.runId) return;
  const payload = await api("GET", `/api/runs/${state.runId}/views?view=${state.currentView}`);
  const data = payload.data || {};

  if (state.currentView === "system") {
    showTopoView();
    state.systemData = data;
    updateMetricsRibbon(data);
    updateActualConditions(data);
    renderTopology(data);
    updatePacketFlow(data);
    updateInterferenceFlow(data);
    updateAlertFeed(data);
    updateMonitorPanel(data);
  } else {
    showSubView();
    renderOtherView(state.currentView, data);
    // 异步拉取 system 数据仅用于更新顶部统计条与右侧监控面板
    api("GET", `/api/runs/${state.runId}/views?view=system`).then((res) => {
      if (res.data) {
        state.systemData = res.data;
        updateMetricsRibbon(res.data);
        updateActualConditions(res.data);
        updateMonitorPanel(res.data);
      }
    });
  }
}

/* 更新顶部态势指标条 */
function updateMetricsRibbon(data) {
  const statusElem = $("#m-status");
  const status = data.status || "ready";
  statusElem.textContent = status.toUpperCase();
  statusElem.className = `metric-value status-tag ${status}`;

  $("#m-time").textContent = formatUs(data.time_us);

  const devices = data.topology?.devices || [];
  const links = data.topology?.links || [];
  $("#m-devices").textContent = devices.length;
  $("#m-links").textContent = links.length;

  const obs = data.observations || {};
  const eventsCount = obs.event_count ?? (obs.events ? obs.events.length : 0);
  const missingCount = (obs.missing || []).length;
  const lateCount = (obs.late || []).length;
  $("#m-obs-events").textContent = eventsCount;
  $("#m-obs-missing").textContent = missingCount;
  $("#m-obs-late").textContent = lateCount;

  const recList = data.recognition || [];
  const defList = data.defense || [];
  $("#m-threats").textContent = recList.length;
  $("#m-defenses").textContent = defList.length;
}

/* 侧栏图例“实际工况（真值）”：传感器显示度数，这里给出未受扰动的真实工况 */
function updateActualConditions(data) {
  const env = data.environment || {};
  const setValue = (id, value, digits) => {
    const elem = $(`#${id}`);
    if (!elem) return;
    elem.textContent = value == null ? "--" : Number(value).toFixed(digits);
  };
  setValue("m-actual-v", env.bus_voltage_kv, 2);
  setValue("m-actual-i", env.line_current_a, 1);
  setValue("m-actual-p", env.active_power_mw, 2);
  // 底座 shared["reactive_power_var"] 实际按 Mvar 存储（对应场景 environment.load_reactive_mvar）
  setValue("m-actual-q", env.reactive_power_var, 3);
  setValue("m-actual-t", env.ambient_temp_c, 1);
}

/* 更新右下角事件告警流 */
function zhSeverity(severity) {
  return { low: "低", medium: "中", high: "高", critical: "严重" }[severity] || severity || "-";
}

function zhAttackBehavior(behavior) {
  if (!behavior) return "疑似攻击";
  const map = {
    business_anomaly: "业务量异常",
    anomalous_traffic: "流量异常",
    "coordinated:business_anomaly": "协同攻击",
  };
  if (map[behavior]) return map[behavior];
  return behavior.startsWith("coordinated:") ? "协同攻击" : behavior;
}

function parseEventKey(v) {
  const s = String(v == null ? "" : v).trim();
  if (s.startsWith("[") && s.includes(",")) {
    return s.replace(/[\[\]"'\s]/g, "").split(",").filter(Boolean);
  }
  return [];
}

function providerLabel(v) {
  if (Array.isArray(v)) return providerLabel(v[0]);
  const s = String(v == null ? "" : v).trim();
  if (!s) return "监测通道";
  if (s === "business" || s === "p-business") return "业务监测";
  const parts = parseEventKey(s);
  if (parts.length) {
    if (parts[0] === "business" || parts[0] === "p-business") return "业务监测";
    return parts[0] || "监测通道";
  }
  return s;
}

function lateEventLabel(v) {
  const s = String(v == null ? "" : v).trim();
  const parts = parseEventKey(s);
  if (parts.length >= 2) {
    if (parts[0] === "business" || parts[0] === "p-business") return "业务监测事件 " + parts[1];
    return (parts[0] || "事件") + " " + parts[1];
  }
  return providerLabel(v);
}

function updateAlertFeed(data) {
  const feed = $("#alert-feed");
  const items = [];

  (data.recognition || []).forEach((r) => {
    items.push({
      time: r.time_us ?? r.timestamp_us ?? data.time_us ?? 0,
      type: "danger",
      title: `[威胁告警] ${esc(zhAttackBehavior(r.attack_behavior))}`,
      desc: `目标: ${esc((r.target_asset_ids || []).map(deviceName).join("、") || "-")} · 置信度: ${r.confidence ?? "-"} · 严重度: ${esc(zhSeverity(r.severity))}`,
    });
  });

  (data.defense || []).forEach((d) => {
    items.push({
      time: d.time_us ?? d.timestamp_us ?? data.time_us ?? 0,
      type: "normal",
      title: `[防御执行] ${esc(zhAction(d.action_type))}`,
      desc: `目标: ${esc((d.target_asset_ids || []).map(deviceName).join("、") || "-")} · 状态: ${esc(zhStatus(d.status))}`,
    });
  });

  const obs = data.observations || {};
  (obs.missing || []).forEach((m) => {
    items.push({
      time: m.time_us ?? m.window_end_us ?? data.time_us ?? 0,
      type: "warning",
      title: `[数据缺失] ${esc(providerLabel(m.provider_id))}`,
      desc: `监测源未按时递交数据`,
    });
  });
  (obs.late || []).forEach((l) => {
    items.push({
      time: l.scene_time_us ?? l.time_us ?? data.time_us ?? 0,
      type: "warning",
      title: `[数据迟到] ${esc(lateEventLabel(l.event_key || l.provider_id || l.source_type))}`,
      desc: `该事件超出允许观测时窗，未在时窗内入窗`,
    });
  });

  if (items.length === 0) {
    feed.innerHTML = '<div class="alert-empty">系统运行稳态，暂无告警记录</div>';
    return;
  }

  items.sort((a, b) => b.time - a.time);
  feed.innerHTML = items
    .slice(0, 30)
    .map(
      (item) => `
    <div class="alert-item ${item.type}">
      <div class="alert-time">${formatUs(item.time)}</div>
      <div class="alert-title">${item.title}</div>
      <div class="alert-detail">${item.desc}</div>
    </div>`
    )
    .join("");
}

/* 拓扑版式随场景加载：节点坐标取 device.layout，分区带取 layout.layers；
   场景未声明时按设备所在层自动推导，保证新增场景无需改前端即可显示。 */
const NODE_COORDINATES = {};           // 运行期由 applyScenarioLayout 填充
const NODE_SIZE = { w: 90, h: 48 };
const LAYER_ORDER = ["external", "station", "bay", "process"];
const LAYER_TITLES = {
  external: "外界层 (EXTERNAL LAYER)",
  station: "站控层 (STATION LAYER)",
  bay: "间隔层 (BAY LAYER)",
  process: "过程层 (PROCESS LAYER - 感知 / 采集 / 执行 / 一次)",
};
const LINK_TYPE_META = {
  wired: { label: "站内网络总线", cls: "line-wired", title: "站内以太网数字通信总线 (MMS/GOOSE/SV)" },
  wireless: { label: "无线链路", cls: "line-wireless", title: "跨站与星地无线链路（授时卫星↔授时系统、其他电站↔站端监控）" },
  interference: { label: "电磁干扰", cls: "line-interference", title: "电磁攻击节点对电流互感器的干扰耦合（红色虚线，手动触发）" },
  hardwire: { label: "控制硬接线", cls: "line-hardwire", title: "控制接口至一次设备电磁线圈的硬接线控制回路" },
  electrical: { label: "电气主回路", cls: "line-elec", title: "一次主干回路高压电气主接线" },
  physical: { label: "热传导耦合", cls: "line-physical", title: "变压器本体热传导至油温计的物理接触测量关系" },
};

/* 解析节点坐标：优先使用场景声明，缺失坐标的设备按所在层自动补位 */
function resolveNodeCoordinates(devices, layout) {
  const declaredBands = {};
  (Array.isArray(layout.layers) ? layout.layers : []).forEach((band) => {
    if (band && band.key) declaredBands[band.key] = band;
  });

  const coords = {};
  const counters = {};
  devices.forEach((dev) => {
    const id = dev.device_id;
    const declared = dev.layout || {};
    if (typeof declared.x === "number" && typeof declared.y === "number") {
      coords[id] = { x: declared.x, y: declared.y };
      return;
    }
    const layer = dev.layer || "process";
    const index = counters[layer] || 0;
    counters[layer] = index + 1;
    const band = declaredBands[layer];
    if (band) {
      coords[id] = { x: Number(band.x || 0) + 80 + index * 150, y: Number(band.y || 0) + 45 };
    } else {
      const row = Math.max(0, LAYER_ORDER.indexOf(layer));
      coords[id] = { x: 80 + index * 150, y: 45 + row * 140 };
    }
  });
  return coords;
}

/* 解析分区带：场景声明优先，否则按各层设备的外接矩形自动推导 */
function resolveLayerBands(layout, devices) {
  const declared = Array.isArray(layout.layers) ? layout.layers : null;
  if (declared && declared.length) return declared;

  const groups = new Map();
  devices.forEach((dev) => {
    const coord = NODE_COORDINATES[dev.device_id];
    if (!coord) return;
    const key = dev.layer || "other";
    if (!groups.has(key)) {
      groups.set(key, { key, minX: coord.x, minY: coord.y, maxX: coord.x, maxY: coord.y });
    }
    const group = groups.get(key);
    group.minX = Math.min(group.minX, coord.x);
    group.minY = Math.min(group.minY, coord.y);
    group.maxX = Math.max(group.maxX, coord.x + NODE_SIZE.w);
    group.maxY = Math.max(group.maxY, coord.y + NODE_SIZE.h);
  });

  const order = LAYER_ORDER.concat([...groups.keys()].filter((key) => !LAYER_ORDER.includes(key)));
  return order.filter((key) => groups.has(key)).map((key) => {
    const group = groups.get(key);
    return {
      key,
      title: LAYER_TITLES[key] || key,
      x: group.minX - 70,
      y: group.minY - 30,
      width: group.maxX - group.minX + 140,
      height: group.maxY - group.minY + 60,
    };
  });
}

/* 渲染分层背景带与标题 */
function renderLayerBands(bands) {
  const group = $("#topo-layers");
  if (!group) return;
  group.innerHTML = "";
  const frag = document.createDocumentFragment();
  bands.forEach((band) => {
    const rect = document.createElementNS("http://www.w3.org/2000/svg", "rect");
    rect.setAttribute("x", band.x);
    rect.setAttribute("y", band.y);
    rect.setAttribute("width", band.width);
    rect.setAttribute("height", band.height);
    rect.setAttribute("rx", "8");
    rect.setAttribute("class", `layer-band band-${band.key}`);
    frag.appendChild(rect);

    const text = document.createElementNS("http://www.w3.org/2000/svg", "text");
    text.setAttribute("x", Number(band.x) + 15);
    text.setAttribute("y", Number(band.y) + 23);
    text.setAttribute("class", "layer-title");
    text.textContent = band.title || band.key;
    frag.appendChild(text);
  });
  group.appendChild(frag);
}

/* 画布尺寸由场景版式外接矩形推导，也允许场景用 layout.canvas 覆盖 */
function applyCanvasSize(layout, bands) {
  const svg = $("#topo-svg");
  if (!svg) return;
  const canvas = layout.canvas || {};
  let width = Number(canvas.width) || 0;
  let height = Number(canvas.height) || 0;
  if (!width || !height) {
    let maxX = 0;
    let maxY = 0;
    bands.forEach((band) => {
      maxX = Math.max(maxX, Number(band.x || 0) + Number(band.width || 0));
      maxY = Math.max(maxY, Number(band.y || 0) + Number(band.height || 0));
    });
    Object.values(NODE_COORDINATES).forEach((coord) => {
      maxX = Math.max(maxX, coord.x + NODE_SIZE.w);
      maxY = Math.max(maxY, coord.y + NODE_SIZE.h);
    });
    width = width || maxX + 10;
    height = height || maxY + 20;
  }
  state.canvasSize = { width: Math.round(width), height: Math.round(height) };
  svg.setAttribute("viewBox", `0 0 ${state.canvasSize.width} ${state.canvasSize.height}`);
  if (state.viewTouched) applyTopoTransform();
  else fitTopoToViewport();
}

/* 应用当前缩放与平移：缩放改 SVG 像素尺寸，平移用 transform，与内容是否溢出无关 */
function applyTopoTransform() {
  const svg = $("#topo-svg");
  if (!svg || !state.canvasSize) return;
  svg.style.width = `${Math.round(state.canvasSize.width * state.zoom)}px`;
  svg.style.height = `${Math.round(state.canvasSize.height * state.zoom)}px`;
  svg.style.transform = `translate(${state.pan.x.toFixed(1)}px, ${state.pan.y.toFixed(1)}px)`;
  updateViewResetVisibility();
}

/* “重置视图”仅在比例或位置偏离默认（自动铺满 + 居中）时出现 */
function updateViewResetVisibility() {
  const button = $("#zoom-reset");
  if (!button) return;
  const rescaled = Math.abs(state.zoom - (state.fitZoom || state.zoom)) > 0.005;
  const moved = Math.abs(state.pan.x) > 1 || Math.abs(state.pan.y) > 1;
  button.style.display = rescaled || moved ? "" : "none";
}

/* 默认自动缩放到铺满视口并居中（长宽取较小比例，因此不会出现滚动条） */
function fitTopoToViewport() {
  const viewport = $("#topo-viewport");
  if (!viewport || !state.canvasSize) return;
  const width = viewport.clientWidth;
  const height = viewport.clientHeight;
  if (width > 0 && height > 0) {
    const fit = Math.min(width / state.canvasSize.width, height / state.canvasSize.height);
    state.zoom = Math.min(3, Math.max(0.2, fit));
  }
  state.fitZoom = state.zoom;
  state.pan = { x: 0, y: 0 };
  applyTopoTransform();
}

/* 拓扑视口交互：左键拖动自由平移，Ctrl+滚轮以光标为锚点缩放，窗口变化时自动重新铺满 */
function setupTopoInteraction() {
  const viewport = $("#topo-viewport");
  if (!viewport || viewport.dataset.bound === "1") return;
  viewport.dataset.bound = "1";

  let drag = null;
  viewport.addEventListener("mousedown", (event) => {
    if (event.button !== 0) return;
    if (event.target.closest("#zoom-reset")) return; // 点在重置按钮上不触发拖动
    drag = { x: event.clientX, y: event.clientY, panX: state.pan.x, panY: state.pan.y, moved: false };
    viewport.classList.add("dragging");
    event.preventDefault();
  });
  window.addEventListener("mousemove", (event) => {
    if (!drag) return;
    const dx = event.clientX - drag.x;
    const dy = event.clientY - drag.y;
    if (!drag.moved && (Math.abs(dx) > 3 || Math.abs(dy) > 3)) {
      drag.moved = true;
      state.dragMoved = true;
    }
    if (drag.moved) {
      state.pan = { x: drag.panX + dx, y: drag.panY + dy };
      state.viewTouched = true;
      applyTopoTransform();
    }
  });
  window.addEventListener("mouseup", () => {
    if (!drag) return;
    const moved = drag.moved;
    drag = null;
    viewport.classList.remove("dragging");
    if (moved) setTimeout(() => { state.dragMoved = false; }, 0); // 让紧随的 click 先被抑制
    else state.dragMoved = false;
  });
  viewport.addEventListener("wheel", (event) => {
    if (!event.ctrlKey) return;
    event.preventDefault();
    const rect = viewport.getBoundingClientRect();
    const localX = event.clientX - rect.left;
    const localY = event.clientY - rect.top;
    const prevZoom = state.zoom;
    const factor = event.deltaY < 0 ? 1.12 : 1 / 1.12;
    const nextZoom = Math.min(3, Math.max(0.2, prevZoom * factor));
    if (Math.abs(nextZoom - prevZoom) < 1e-6) return;

    // 保持光标下的内容点在缩放前后不动
    const contentX = state.canvasSize.width / 2 + (localX - rect.width / 2 - state.pan.x) / prevZoom;
    const contentY = state.canvasSize.height / 2 + (localY - rect.height / 2 - state.pan.y) / prevZoom;
    state.zoom = nextZoom;
    state.pan = {
      x: localX - rect.width / 2 - (contentX - state.canvasSize.width / 2) * nextZoom,
      y: localY - rect.height / 2 - (contentY - state.canvasSize.height / 2) * nextZoom,
    };
    state.viewTouched = true;
    applyTopoTransform();
  }, { passive: false });

  window.addEventListener("resize", () => {
    if (!state.viewTouched) fitTopoToViewport();
  });
}

/* 恢复默认视图：自动铺满并居中 */
function resetTopoView() {
  state.viewTouched = false;
  fitTopoToViewport();
}

/* 图例只展示当前场景实际存在的链路类型；“影响信号”仅在有电磁干扰链路时出现 */
function renderLinkLegend(links) {
  const box = $("#legend-links");
  if (!box) return;
  const present = [];
  links.forEach((link) => {
    const type = link.link_type || "wired";
    if (!present.includes(type)) present.push(type);
  });
  const ordered = Object.keys(LINK_TYPE_META)
    .filter((type) => present.includes(type))
    .concat(present.filter((type) => !LINK_TYPE_META[type]));
  box.innerHTML = ordered.map((type) => {
    const meta = LINK_TYPE_META[type] || { label: type, cls: "", title: type };
    return `<span class="legend-item" title="${esc(meta.title)}">`
      + `<span class="legend-line ${meta.cls}"></span>${esc(meta.label)}</span>`;
  }).join("");

  const attackItem = $("#legend-attack-particle");
  if (attackItem) attackItem.style.display = present.includes("interference") ? "" : "none";
}

/* 应用场景自带版式：节点坐标、分区带、画布、图例 */
function applyScenarioLayout(topo) {
  const devices = topo.devices || [];
  const layout = topo.layout || {};

  Object.keys(NODE_COORDINATES).forEach((key) => delete NODE_COORDINATES[key]);
  Object.assign(NODE_COORDINATES, resolveNodeCoordinates(devices, layout));

  const bands = resolveLayerBands(layout, devices);
  renderLayerBands(bands);
  applyCanvasSize(layout, bands);
  renderLinkLegend(topo.links || []);
}

/* 节点外观类型：攻击/检测节点使用固定方框配色 */
function nodeKindClass(deviceType) {
  if (deviceType === "em_attack_source") return "node-kind-attack";
  if (deviceType === "spectrum_detector") return "node-kind-detect";
  return "";
}

/* 提取设备即时动态物理指标 */
/* 感知设备“度数”与“实际值”的对应关系：实际值取自工况真值或一次设备本体 */
function sensorActualValue(devId, mgmt, env) {
  if (devId === "ct_current") return env?.line_current_a;
  if (devId === "vt_voltage") return env?.bus_voltage_kv;
  if (devId === "oil_temp") return mgmt["tap"]?.state?.oil_temp_c;
  return null;
}

/* 度数偏离实际值即视为被攻击篡改，用于节点高亮 */
function isSensorTampered(devId, mgmt, env) {
  const state = (mgmt[devId] || {}).state || {};
  const reading = state.last_value;
  const actual = sensorActualValue(devId, mgmt, env);
  if (reading == null || actual == null) return false;
  return Math.abs(Number(reading) - Number(actual)) > 1e-6;
}

/* 感知设备展示的是本机采样读数（度数），实际值见“实际工况”图例 */
function sensorReadingText(state, actualValue, unit, digits) {
  const reading = state && state.last_value != null ? Number(state.last_value) : null;
  if (reading == null) {
    return actualValue != null ? `[${Number(actualValue).toFixed(digits)} ${unit}]` : "";
  }
  return `[${reading.toFixed(digits)} ${unit}]`;
}

function getDeviceDynamicText(devId, mgmt, env) {
  const devMgmt = mgmt[devId] || {};
  const s = devMgmt.state || {};
  const overview = devMgmt.overview || {};

  // 一次受控对象简化标签
  if (devId === "brk") {
    return s.position === "closed" ? "[合闸]" : "[分闸]";
  }
  if (devId === "tap") {
    const tap = s.tap_position ?? 5;
    const temp = s.oil_temp_c != null ? s.oil_temp_c.toFixed(1) : "40.0";
    return `[${tap}档 ${temp}℃]`;
  }
  if (devId === "cool") {
    return s.running ? "[风机运行]" : "[风机停止]";
  }
  if (devId === "comp") {
    return s.connected ? "[补偿投入]" : "[补偿切除]";
  }

  // 传感器读数（度数）：显示本机采样结果，可能已被攻击篡改
  if (devId === "ct_current") {
    return sensorReadingText(s, env?.line_current_a, "A", 1);
  }
  if (devId === "vt_voltage") {
    return sensorReadingText(s, env?.bus_voltage_kv, "kV", 1);
  }
  if (devId === "oil_temp") {
    return sensorReadingText(s, mgmt["tap"]?.state?.oil_temp_c, "℃", 1);
  }
  if (devId === "mu") {
    return "[采集合并]";
  }
  if (devId === "prot") {
    const threshold = overview.overcurrent_threshold_a;
    return threshold != null ? `[定值 ${threshold}A]` : "[定值 -]";
  }
  if (devId === "mc") {
    const cmds = (overview.last_commands || []).length;
    const fb = (overview.last_feedback || []).length;
    return `[测控 令${cmds} 反${fb}]`;
  }
  if (devId === "station") {
    const rx = (overview.last_received || []).length;
    const tx = (overview.last_dispatched || []).length;
    return `[监视 收${rx} 发${tx}]`;
  }
  if (devId === "time_svc") {
    return "[GNSS授时]";
  }
  if (devId === "gnss_sat" || devId === "peer_station") {
    return "";
  }
  if (devId === "spectrum_detect_node") {
    return "频谱检测";
  }

  return "";
}

/* 渲染/增量更新态势拓扑图 (SVG) */
function renderTopology(data) {
  const topo = data.topology || {};
  const devices = topo.devices || [];
  const links = topo.links || [];
  const mgmt = data.management || {};
  const env = data.environment || {};
  const recList = data.recognition || [];

  // 切换运行/场景时重建拓扑 DOM，避免沿用上一个场景的节点
  if (state.renderedRunId !== data.run_id) {
    state.renderedRunId = data.run_id;
    state.renderedTopologyOnce = false;
    state.selectedDeviceId = null;
    state.viewTouched = false; // 切换场景时重新自动铺满并居中
    state.pan = { x: 0, y: 0 };
  }
  // 版式（节点坐标、分区带、画布、图例）全部来自当前场景数据
  applyScenarioLayout(topo);

  const attackedDeviceIds = new Set();
  const suspectDeviceIds = new Set();

  Object.entries(mgmt).forEach(([devId, devState]) => {
    if (devState && devState.effects && Object.keys(devState.effects).length > 0) {
      attackedDeviceIds.add(devId);
    }
  });

  recList.forEach((r) => {
    (r.target_asset_ids || []).forEach((id) => suspectDeviceIds.add(id));
    (r.affected_asset_ids || []).forEach((id) => suspectDeviceIds.add(id));
  });

  const linksGroup = $("#topo-links");
  const nodesGroup = $("#topo-nodes");

  // 渲染/同步连线
  if (!state.renderedTopologyOnce || linksGroup.children.length === 0) {
    linksGroup.innerHTML = "";
    links.forEach((link) => {
      const srcId = Array.isArray(link.endpoint_a) ? link.endpoint_a[0] : link.endpoint_a;
      const dstId = Array.isArray(link.endpoint_b) ? link.endpoint_b[0] : link.endpoint_b;

      const srcCoord = NODE_COORDINATES[srcId];
      const dstCoord = NODE_COORDINATES[dstId];
      if (!srcCoord || !dstCoord) return;

      const x1 = srcCoord.x + 45;
      const y1 = srcCoord.y + 24;
      const x2 = dstCoord.x + 45;
      const y2 = dstCoord.y + 24;

      const line = document.createElementNS("http://www.w3.org/2000/svg", "line");
      line.setAttribute("x1", x1);
      line.setAttribute("y1", y1);
      line.setAttribute("x2", x2);
      line.setAttribute("y2", y2);
      line.setAttribute("class", `topo-link ${link.link_type || "wired"}`);
      line.setAttribute("data-link-id", link.link_id || "");

      const title = document.createElementNS("http://www.w3.org/2000/svg", "title");
      title.textContent = `${srcId} ↔ ${dstId} (${link.link_type || "wired"})`;
      line.appendChild(title);

      linksGroup.appendChild(line);
    });
  }

  // 渲染/同步节点位置与动态状态
  if (!state.renderedTopologyOnce || nodesGroup.children.length === 0) {
    nodesGroup.innerHTML = "";
    devices.forEach((dev) => {
      const id = dev.device_id;
      const coord = NODE_COORDINATES[id] || { x: 100, y: 100 };

      let statusClass = "status-normal";
      if (attackedDeviceIds.has(id)) statusClass = "status-attacked";
      else if (suspectDeviceIds.has(id)) statusClass = "status-suspect";

      const isSelected = state.selectedDeviceId === id;
      const kindClass = nodeKindClass(dev.device_type);
      const tamperedClass = isSensorTampered(id, mgmt, env) ? "tampered" : "";
      const dynamicText = getDeviceDynamicText(id, mgmt, env);

      const g = document.createElementNS("http://www.w3.org/2000/svg", "g");
      g.setAttribute("class", `topo-node ${statusClass} ${kindClass} ${tamperedClass} ${isSelected ? "selected" : ""}`);
      g.setAttribute("transform", `translate(${coord.x}, ${coord.y})`);
      g.setAttribute("data-device-id", id);

      g.innerHTML = `
        <rect width="90" height="48" rx="6"></rect>
        <text x="45" y="22" text-anchor="middle" class="node-title">${esc(dev.name || id)}</text>
        <text x="45" y="42" text-anchor="middle" class="node-status-text" id="node-text-${esc(id)}">${esc(dynamicText)}</text>
      `;

      g.addEventListener("click", () => {
        if (state.dragMoved) return; // 刚拖动过视口，避免误选设备
        state.selectedDeviceId = id;
        document.querySelectorAll(".topo-node").forEach((node) => node.classList.remove("selected"));
        g.classList.add("selected");
        inspectDevice(id, true);
      });

      nodesGroup.appendChild(g);
    });
    state.renderedTopologyOnce = true;
  } else {
    // 增量同步：强制保持 transform 坐标与最新坐标字典一致，并更新文字状态
    devices.forEach((dev) => {
      const id = dev.device_id;
      const g = nodesGroup.querySelector(`[data-device-id="${id}"]`);
      if (!g) return;

      const coord = NODE_COORDINATES[id];
      if (coord) {
        g.setAttribute("transform", `translate(${coord.x}, ${coord.y})`);
      }

      let statusClass = "status-normal";
      if (attackedDeviceIds.has(id)) statusClass = "status-attacked";
      else if (suspectDeviceIds.has(id)) statusClass = "status-suspect";

      const isSelected = state.selectedDeviceId === id;
      const kindClass = nodeKindClass(dev.device_type);
      const tamperedClass = isSensorTampered(id, mgmt, env) ? "tampered" : "";
      g.setAttribute("class", `topo-node ${statusClass} ${kindClass} ${tamperedClass} ${isSelected ? "selected" : ""}`);

      const txtElem = $(`#node-text-${id}`);
      if (txtElem) {
        const dynamicText = getDeviceDynamicText(id, mgmt, env);
        txtElem.textContent = dynamicText;
      }
    });
  }
}

/* 粒子生命周期与平滑插值管理器：基于唯一 message_id 追踪生命周期，杜绝步进突变跳动 */
const particleMap = new Map();
const packetMessageMap = new Map();
const particleNodeMap = new Map();
const interferenceParticles = new Map();
let animFrameId = null;

function removeParticleNode(key) {
  const node = particleNodeMap.get(key);
  if (node) {
    node.remove();
    particleNodeMap.delete(key);
  }
}

function clearParticleNodes() {
  particleNodeMap.forEach((node) => node.remove());
  particleNodeMap.clear();
}

function ensureParticleNode(packetLayer, key, category, radius) {
  let node = particleNodeMap.get(key);
  if (!node) {
    node = document.createElementNS("http://www.w3.org/2000/svg", "g");
    node.setAttribute("class", "msg-particle-group");
    node.setAttribute("role", "button");

    const hit = document.createElementNS("http://www.w3.org/2000/svg", "circle");
    hit.setAttribute("class", "msg-particle-hit");
    hit.setAttribute("r", "22");
    node.appendChild(hit);

    const halo = document.createElementNS("http://www.w3.org/2000/svg", "circle");
    halo.setAttribute("class", "msg-particle-halo");
    halo.setAttribute("r", "11");
    node.appendChild(halo);

    const particle = document.createElementNS("http://www.w3.org/2000/svg", "circle");
    particle.setAttribute("class", "msg-particle");
    particle.setAttribute("r", radius);
    node.appendChild(particle);

    const label = document.createElementNS("http://www.w3.org/2000/svg", "text");
    label.setAttribute("class", "msg-particle-label");
    label.setAttribute("x", "0");
    label.setAttribute("y", "-17");
    label.setAttribute("text-anchor", "middle");
    label.textContent = "点击查看报文";
    node.appendChild(label);

    packetLayer.appendChild(node);
    particleNodeMap.set(key, node);
  }
  node.setAttribute("class", `msg-particle-group ${category}`);
  const particle = node.querySelector(".msg-particle");
  if (particle) particle.setAttribute("r", radius);
  return node;
}

function syncPacketSelection() {
  particleNodeMap.forEach((node) => {
    const messageId = node.getAttribute("data-message-id");
    const isSelected = Boolean(messageId) && messageId === state.selectedPacketMessageId;
    node.classList.toggle("selected", isSelected);
    if (messageId) node.setAttribute("aria-pressed", isSelected ? "true" : "false");
    else node.removeAttribute("aria-pressed");
  });
}

function animateParticles(now) {
  const packetLayer = $("#topo-packets");
  if (packetLayer) {
    // 每帧只更新持久节点，避免点击目标在 pointerdown 与 click 之间被销毁。
    const toDelete = [];
    const activeNodeKeys = new Set();

    particleMap.forEach((p, id) => {
      if (state.particleFilter !== "all" && p.category !== state.particleFilter) return;

      const elapsed = now - p.birthTime;
      const progress = Math.min(elapsed / p.duration, 1.0);

      // 报文已到达或被移除且已走完路径
      if (progress >= 1.0) {
        if (p.isDead) {
          toDelete.push(id);
          return;
        } else {
          // 若仍在传输队列中，循环继续推进流动
          p.birthTime = now;
        }
      }

      // 计算平滑贝塞尔或线性插值坐标
      const curX = p.x1 + (p.x2 - p.x1) * progress;
      const curY = p.y1 + (p.y2 - p.y1) * progress;

      // 尾迹与淡入淡出透明度
      let opacity = 1.0;
      if (progress < 0.15) opacity = progress / 0.15;
      else if (progress > 0.85) opacity = (1.0 - progress) / 0.15;

      const nodeKey = `msg:${id}`;
      activeNodeKeys.add(nodeKey);
      const group = ensureParticleNode(packetLayer, nodeKey, p.category, "4.5");
      group.setAttribute("transform", `translate(${curX.toFixed(1)} ${curY.toFixed(1)})`);
      if (p.messageId && packetMessageMap.has(p.messageId)) {
        group.setAttribute("data-message-id", p.messageId);
        group.setAttribute("aria-label", `查看报文 ${p.messageId}`);
      } else {
        group.removeAttribute("data-message-id");
        group.removeAttribute("aria-label");
      }
      group.setAttribute("opacity", opacity.toFixed(2));
      const isSelected = Boolean(p.messageId) && p.messageId === state.selectedPacketMessageId;
      group.classList.toggle("selected", isSelected);
      if (p.messageId) group.setAttribute("aria-pressed", isSelected ? "true" : "false");
      else group.removeAttribute("aria-pressed");
    });

    // 攻击影响信号：攻击进行时沿电磁干扰链路持续流动（不受报文过滤器影响）
    interferenceParticles.forEach((p, key) => {
      const progress = ((now - p.birthTime) / p.duration + p.offset) % 1.0;
      const nodeKey = `ifx:${key}`;
      activeNodeKeys.add(nodeKey);
      const group = ensureParticleNode(packetLayer, nodeKey, "attack", "4.2");
      const ifxX = (p.x1 + (p.x2 - p.x1) * progress).toFixed(1);
      const ifxY = (p.y1 + (p.y2 - p.y1) * progress).toFixed(1);
      group.setAttribute("transform", `translate(${ifxX} ${ifxY})`);
      group.removeAttribute("data-message-id");
      group.removeAttribute("aria-label");
      group.removeAttribute("aria-pressed");
      group.classList.remove("selected");
      group.setAttribute("opacity", "0.92");
    });

    toDelete.forEach((id) => {
      particleMap.delete(id);
      packetMessageMap.delete(id);
      removeParticleNode(`msg:${id}`);
    });
    Array.from(particleNodeMap.keys()).forEach((key) => {
      if (!activeNodeKeys.has(key)) removeParticleNode(key);
    });
  }

  animFrameId = requestAnimationFrame(animateParticles);
}

if (!animFrameId) {
  animFrameId = requestAnimationFrame(animateParticles);
}

/* 增量差分更新报文流，保持老报文连续运动不被销毁重置 */
function updatePacketFlow(data) {
  // 系统终止/停止或回放结束：彻底清理在途报文粒子，杜绝幽灵粒子传递
  if (data.status === "finished" || data.status === "stopped") {
    particleMap.clear();
    packetMessageMap.clear();
    interferenceParticles.clear();
    clearParticleNodes();
    return;
  }

  const inFlights = data.in_flight_messages || [];
  const incomingIds = new Set();
  const now = performance.now();

  inFlights.slice(0, 24).forEach((msg, idx) => {
    const messageId = msg.message_id || "";
    const id = messageId || `${msg.sender_id}->${msg.receiver_id}`;
    incomingIds.add(id);

    if (messageId) packetMessageMap.set(messageId, msg);

    // 如果该报文已在航线中，仅更新状态，绝对不重置 birthTime，确保运动连续
    if (particleMap.has(id)) {
      const existing = particleMap.get(id);
      existing.isDead = false;
      return;
    }

    // 新加入的在途报文
    const src = NODE_COORDINATES[msg.sender_id];
    const dst = NODE_COORDINATES[msg.receiver_id];
    if (!src || !dst) return;

    particleMap.set(id, {
      id: id,
      messageId: messageId,
      x1: src.x + 45,
      y1: src.y + 24,
      x2: dst.x + 45,
      y2: dst.y + 24,
      businessType: msg.business_type || "unknown",
      category: particleCategory(msg.business_type || "unknown"),
      birthTime: now,
      duration: 1100, // 约1.1秒平滑穿越
      isDead: false,
    });
  });

  // 不在当前队列的报文标记为 isDead，走完当次单程后自然销毁，避免瞬间凭空消失
  particleMap.forEach((p, id) => {
    if (!incomingIds.has(id)) {
      p.isDead = true;
    }
  });
}

/* 攻击影响信号：攻击节点发射期间，在电磁干扰链路上持续发送流动信号 */
function endpointDeviceId(endpoint) {
  return Array.isArray(endpoint) ? endpoint[0] : endpoint;
}

function isAttackNodeEmitting(mgmt, deviceId) {
  const device = mgmt[deviceId];
  return !!device && device.device_type === "em_attack_source"
    && !!(device.state && device.state.emitting);
}

function updateInterferenceFlow(data) {
  if (data.status === "finished" || data.status === "stopped") {
    interferenceParticles.clear();
    return;
  }

  const mgmt = data.management || {};
  const links = (data.topology || {}).links || [];
  const now = performance.now();
  const wanted = new Set();

  links.forEach((link) => {
    if (link.link_type !== "interference") return;
    const aId = endpointDeviceId(link.endpoint_a);
    const bId = endpointDeviceId(link.endpoint_b);

    // 信号方向固定由攻击节点流向被干扰设备
    let fromId = "";
    let toId = "";
    if (isAttackNodeEmitting(mgmt, aId)) { fromId = aId; toId = bId; }
    else if (isAttackNodeEmitting(mgmt, bId)) { fromId = bId; toId = aId; }
    if (!fromId) return;

    const src = NODE_COORDINATES[fromId];
    const dst = NODE_COORDINATES[toId];
    if (!src || !dst) return;

    const count = 4; // 链路上均匀分布的影响信号粒子
    for (let i = 0; i < count; i += 1) {
      const key = `ifx:${link.link_id}:${i}`;
      wanted.add(key);
      if (!interferenceParticles.has(key)) {
        interferenceParticles.set(key, {
          x1: src.x + 45,
          y1: src.y + 24,
          x2: dst.x + 45,
          y2: dst.y + 24,
          offset: i / count,
          birthTime: now,
          duration: 1400,
        });
      }
    }
  });

  // 攻击结束或链路消失后立即撤掉影响信号
  Array.from(interferenceParticles.keys()).forEach((key) => {
    if (!wanted.has(key)) interferenceParticles.delete(key);
  });
}

/* 固定展示站端监控系统与测控装置的实时交互信息 */
function updateMonitorPanel(data) {
  const panel = $("#monitor-panel");
  if (!panel) return;
  const mgmt = data.management || {};
  const station = mgmt.station || {};
  const mc = mgmt.mc || {};
  const stationOverview = station.overview || {};
  const mcOverview = mc.overview || {};

  let html = "";
  html += '<div class="monitor-device-title">站端监控系统</div>';
  html += renderStationMonitor(stationOverview);
  html += '<div class="monitor-device-title" style="margin-top:10px;">测控装置</div>';
  html += renderMcMonitor(mcOverview);

  if (!html.includes("monitor-row")) {
    html = '<div class="alert-empty">暂无监控交互数据，启动运行后自动刷新</div>';
  }
  panel.innerHTML = html;
}

function renderStationMonitor(overview) {
  if (!overview || Object.keys(overview).length === 0) {
    return '<div class="monitor-empty">等待站端监控系统接收报文…</div>';
  }

  let html = "";
  const received = Array.isArray(overview.last_received) ? overview.last_received : [];
  const dispatched = Array.isArray(overview.last_dispatched) ? overview.last_dispatched : [];

  if (received.length) {
    html += '<div class="monitor-log-title">最近接收</div>';
    received.slice(-4).reverse().forEach((r) => {
      const typeZh = zhBusiness(r.type);
      const fromZh = deviceName(r.from);
      let text = `来自 <strong>${esc(fromZh)}</strong> · ${esc(typeZh)}`;
      const sample = extractSample(r.payload);
      if (sample) text += `<span class="monitor-sample">${renderSample(sample)}</span>`;
      html += monitorRow(r.time_us, typeZh, text);
    });
  }

  if (dispatched.length) {
    html += '<div class="monitor-log-title">最近下发</div>';
    dispatched.slice(-4).reverse().forEach((d) => {
      const text = `目标 <strong>${esc(deviceName(d.target))}</strong> · ${esc(zhAction(d.action))} · ${esc(zhStatus(d.status))}${d.reason ? ` · 原因：${esc(d.reason)}` : ""}`;
      html += monitorRow(d.time_us, zhStatus(d.status), text);
    });
  }

  if (!received.length && !dispatched.length) {
    html += '<div class="monitor-empty">站端监控系统暂无接收/下发记录</div>';
  }
  return html;
}

function renderMcMonitor(overview) {
  if (!overview || Object.keys(overview).length === 0) {
    return '<div class="monitor-empty">等待测控装置形成量测与控制记录…</div>';
  }

  let html = "";
  const commands = Array.isArray(overview.last_commands) ? overview.last_commands : [];
  const feedback = Array.isArray(overview.last_feedback) ? overview.last_feedback : [];
  const latest = extractSample(overview.latest_sample);

  if (latest) {
    html += '<div class="monitor-log-title">最新采样</div>';
    html += monitorRow(latest.sample_time_us || 0, "量测", renderSample(latest));
  }

  if (commands.length) {
    html += '<div class="monitor-log-title">命令执行</div>';
    commands.slice(-4).reverse().forEach((c) => {
      const text = `${esc(zhAction(c.action))} · ${esc(zhStatus(c.status))}${c.reason ? ` · 原因：${esc(c.reason)}` : ""}${c.target ? ` · 目标：${esc(deviceName(c.target))}` : ""}`;
      html += monitorRow(c.time_us, zhStatus(c.status), text);
    });
  }

  if (feedback.length) {
    html += '<div class="monitor-log-title">执行反馈</div>';
    feedback.slice(-4).reverse().forEach((f) => {
      const payload = f.payload || {};
      const status = payload.status || "已反馈";
      const reason = payload.reason || "";
      const text = `${esc(zhStatus(status))}${reason ? ` · ${esc(reason)}` : ""}`;
      html += monitorRow(f.time_us, "反馈", text);
    });
  }

  if (!html) {
    html += '<div class="monitor-empty">测控装置暂无采样/命令/反馈记录</div>';
  }
  return html;
}

/* 选中设备：只驱动左侧控制面板，不再在右侧渲染设备详情 */
function inspectDevice(deviceId, fillTarget = false) {
  state.selectedDeviceId = deviceId;
  if (fillTarget) {
    loadDeviceControls(deviceId);
  }
}

/* 切换视图 */
function showTopoView() {
  $("#topo-container").style.display = "flex";
  $("#sub-view-container").style.display = "none";
  // 确保 DOM 连线和坐标立即与 NODE_COORDINATES 保持绝对一致
  syncTopologyPositions();
}

function syncTopologyPositions() {
  const nodesGroup = $("#topo-nodes");
  const linksGroup = $("#topo-links");
  if (!nodesGroup || !linksGroup) return;

  // 1. 同步所有节点绝对坐标
  Object.entries(NODE_COORDINATES).forEach(([devId, coord]) => {
    const g = nodesGroup.querySelector(`[data-device-id="${devId}"]`);
    if (g) {
      g.setAttribute("transform", `translate(${coord.x}, ${coord.y})`);
    }
  });

  // 2. 同步所有连线端点绝对坐标
  if (state.systemData?.topology?.links) {
    linksGroup.innerHTML = "";
    state.systemData.topology.links.forEach((link) => {
      const srcId = Array.isArray(link.endpoint_a) ? link.endpoint_a[0] : link.endpoint_a;
      const dstId = Array.isArray(link.endpoint_b) ? link.endpoint_b[0] : link.endpoint_b;
      const srcCoord = NODE_COORDINATES[srcId];
      const dstCoord = NODE_COORDINATES[dstId];
      if (!srcCoord || !dstCoord) return;

      const x1 = srcCoord.x + 45;
      const y1 = srcCoord.y + 24;
      const x2 = dstCoord.x + 45;
      const y2 = dstCoord.y + 24;

      const line = document.createElementNS("http://www.w3.org/2000/svg", "line");
      line.setAttribute("x1", x1);
      line.setAttribute("y1", y1);
      line.setAttribute("x2", x2);
      line.setAttribute("y2", y2);
      line.setAttribute("class", `topo-link ${link.link_type || "wired"}`);
      line.setAttribute("data-link-id", link.link_id || "");

      const title = document.createElementNS("http://www.w3.org/2000/svg", "title");
      title.textContent = `${srcId} ↔ ${dstId} (${link.link_type || "wired"})`;
      line.appendChild(title);

      linksGroup.appendChild(line);
    });
  }
}

function showSubView() {
  $("#topo-container").style.display = "none";
  $("#sub-view-container").style.display = "block";
}

function fmtPct(value, digits = 1) {
  if (value === null || value === undefined || Number.isNaN(Number(value))) return "-";
  return (Number(value) * 100).toFixed(digits) + "%";
}

function firstScalar(value) {
  if (Array.isArray(value)) return value[0];
  return value;
}

function severityClass(severity) {
  return { critical: "danger", high: "danger", medium: "warning", low: "normal" }[severity] || "normal";
}

function emptyState(text) {
  return `<div class="sub-empty">${esc(text)}</div>`;
}

function renderSnapshot(data) {
  const content = $("#view-content");
  const snapshots = data.snapshots || {};
  const entries = Object.entries(snapshots);
  let body = "";

  if (entries.length === 0) {
    body = emptyState("该时刻尚无设备状态快照，请推进仿真后再回放");
  } else {
    body = '<div class="sub-card-list">' + entries.map(([id, snap]) => {
      const s = snap?.state || {};
      const effects = snap?.effects || {};
      const parts = [];
      Object.entries(s).forEach(([k, v]) => {
        if (v === null || v === undefined) return;
        if (typeof v === "object") {
          Object.entries(v).forEach(([kk, vv]) => parts.push(`${esc(k)}.${esc(kk)}: ${esc(String(vv))}`));
        } else {
          parts.push(`${esc(k)}: ${esc(String(v))}`);
        }
      });
      const effectText = Object.keys(effects).length ? ` <span class="effects-badge">受影响 ${esc(Object.keys(effects).join("、"))}</span>` : "";
      return `
        <div class="sub-card">
          <div class="sub-card-head"><span class="sub-card-title">${esc(deviceName(id))}</span><span class="sub-card-id">${esc(id)}</span></div>
          <div class="sub-card-body">${parts.length ? parts.join(" · ") : "稳态无变化"}${effectText}</div>
        </div>`;
    }).join("") + "</div>";
  }

  content.innerHTML = `
    <div class="sub-view-header">
      <h3>时刻历史快照回放</h3>
      <span class="sub-view-meta">仿真时刻 ${formatUs(data.time_us ?? 0)}</span>
    </div>${body}`;
}

function renderTimeline(data) {
  const content = $("#view-content");
  const attacks = data.attacks || [];
  const submissions = data.attack_submissions || [];
  const recognitions = data.recognitions || data.recognition || [];
  const defenses = data.defenses || data.defense || [];
  const rows = [];

  const push = (list, icon, title, color, renderer) => {
    if (!list.length) {
      rows.push(`<div class="sub-section"><div class="sub-section-title" style="color:${color}">${icon} ${title} (0)</div><div class="sub-empty">暂无记录</div></div>`);
      return;
    }
    rows.push(`
      <div class="sub-section">
        <div class="sub-section-title" style="color:${color}">${icon} ${title} (${list.length})</div>
        <div class="sub-card-list">${list.map(renderer).join("")}</div>
      </div>`);
  };

  push(attacks, "🎯", "攻击计划队列", "#f59e0b", (a) => `
    <div class="sub-card">
      <div class="sub-card-head"><span class="sub-card-title">${esc(a.attack_type || "未知类型")}</span><span class="sub-card-time">${formatUs(a.time_us)}</span></div>
      <div class="sub-card-body">目标 ${esc((a.target_asset_ids || []).map(deviceName).join("、") || "-")} · 起 ${formatUs(a.start_time_us)} · 止 ${formatUs(a.end_time_us)}</div>
    </div>`);

  push(submissions, "⚡", "攻击生效提交", "#ef4444", (s) => `
    <div class="sub-card">
      <div class="sub-card-head"><span class="sub-card-title">${esc(s.effect_type || "效应注入")} → ${esc(deviceName(s.target_id))}</span><span class="sub-card-time">${formatUs(s.time_us)}</span></div>
      <div class="sub-card-body">受理状态 ${esc(zhStatus(s.status))}${s.reason ? ` · ${esc(s.reason)}` : ""}</div>
    </div>`);

  push(recognitions, "🔍", "智能攻击识别", "#06b6d4", (r) => `
    <div class="sub-card">
      <div class="sub-card-head"><span class="sub-card-title">${esc(zhAttackBehavior(r.attack_behavior))} · ${esc(zhSeverity(r.severity))}</span><span class="sub-card-time">${formatUs(r.time_us)}</span></div>
      <div class="sub-card-body">目标 ${esc((r.target_asset_ids || []).map(deviceName).join("、") || "-")} · 置信度 ${r.confidence ?? "-"} · 方法 ${esc(r.recognition_details?.method || "-")}</div>
    </div>`);

  push(defenses, "🛡", "主动防御动作", "#10b981", (d) => `
    <div class="sub-card">
      <div class="sub-card-head"><span class="sub-card-title">${esc(zhAction(d.action_type))}</span><span class="sub-card-time">${formatUs(d.time_us)}</span></div>
      <div class="sub-card-body">目标 ${esc((d.target_asset_ids || []).map(deviceName).join("、") || "-")} · 状态 ${esc(zhStatus(d.status))}${d.result?.note ? ` · ${esc(d.result.note)}` : ""}</div>
    </div>`);

  content.innerHTML = `
    <div class="sub-view-header">
      <h3>⏳ 攻防全时序过程推进</h3>
      <span class="sub-view-meta">共 ${attacks.length + submissions.length + recognitions.length + defenses.length} 条过程记录</span>
    </div>${rows.join("")}`;
}

function renderObservations(data) {
  const content = $("#view-content");
  let obs = data.observations || {};
  // 历史回放返回的是证据数组，活动运行返回 {events, missing, late} 对象。
  let events = [];
  let missing = [];
  let late = [];
  if (Array.isArray(obs)) {
    events = obs;
    obs = {};
  } else {
    events = Array.isArray(obs.events) ? obs.events : [];
    missing = Array.isArray(obs.missing) ? obs.missing : [];
    late = Array.isArray(obs.late) ? obs.late : [];
  }

  let body = "";
  if (!events.length) {
    body += emptyState("当前监测时窗内暂无观测事件");
  } else {
    body += '<div class="sub-section"><div class="sub-section-title" style="color:#38bdf8">📡 观测事件 (' + events.length + ')</div><div class="sub-card-list">' + events.slice(-30).reverse().map((e) => {
      const raw = e.raw || {};
      const data = raw.data || {};
      const metricName = data.metric_name || raw.metadata?.metric || "";
      const metricValue = data.value != null ? `${data.value} ${data.unit || ""}` : "";
      const asset = data.asset_id || (raw.related_asset_ids || [])[0];
      return `
        <div class="sub-card">
          <div class="sub-card-head"><span class="sub-card-title">${esc(providerLabel(e.provider_id))} · ${esc(e.source_type || "观测")}</span><span class="sub-card-time">${formatUs(e.time_us ?? e.received_time_us)}</span></div>
          <div class="sub-card-body">状态 ${esc(zhStatus(raw.status))} · 置信度 ${raw.confidence ?? "-"}${metricName ? ` · ${esc(metricName)} ${esc(metricValue)}` : ""}${asset ? ` · 目标 ${esc(deviceName(asset))}` : ""}</div>
        </div>`;
    }).join("") + '</div></div>';
  }

  if (missing.length) {
    body += '<div class="sub-section"><div class="sub-section-title" style="color:#f59e0b">⚠ 缺失监测源 (' + missing.length + ')</div><div class="sub-card-list">' + missing.slice(-20).reverse().map((m) => `
      <div class="sub-card">
        <div class="sub-card-body">${esc(providerLabel(m.provider_id))} · ${esc(m.reason || "no_data_in_window")}</div>
      </div>`).join("") + '</div></div>';
  }

  if (late.length) {
    body += '<div class="sub-section"><div class="sub-section-title" style="color:#f59e0b">⏱ 迟到事件 (' + late.length + ')</div><div class="sub-card-list">' + late.slice(-20).reverse().map((l) => `
      <div class="sub-card">
        <div class="sub-card-body">${esc(lateEventLabel(l.event_key || l.provider_id || l.source_type))}</div>
      </div>`).join("") + '</div></div>';
  }

  if (!body) body = emptyState("暂无全域监测数据");
  content.innerHTML = `
    <div class="sub-view-header">
      <h3>📡 五类全域监测</h3>
      <span class="sub-view-meta">事件 ${events.length} · 缺失 ${missing.length} · 迟到 ${late.length}</span>
    </div>${body}`;
}

function renderRecognition(data) {
  const content = $("#view-content");
  const list = data.recognition || data.recognitions || [];
  let body;
  if (!list.length) {
    body = emptyState("识别引擎尚未产生告警记录");
  } else {
    body = '<div class="sub-card-list">' + list.slice(-30).reverse().map((r) => `
      <div class="sub-card ${severityClass(r.severity)}">
        <div class="sub-card-head"><span class="sub-card-title">${esc(zhAttackBehavior(r.attack_behavior))} · ${esc(zhSeverity(r.severity))}</span><span class="sub-card-time">${formatUs(r.time_us)}</span></div>
        <div class="sub-card-body">目标 ${esc((r.target_asset_ids || []).map(deviceName).join("、") || "-")} · 置信度 ${r.confidence ?? "-"} · 方法 ${esc(r.recognition_details?.method || "-")}</div>
      </div>`).join("") + "</div>";
  }
  content.innerHTML = `
    <div class="sub-view-header">
      <h3>🔍 智能攻击识别</h3>
      <span class="sub-view-meta">累计告警 ${list.length} 条</span>
    </div>${body}`;
}

function renderDefense(data) {
  const content = $("#view-content");
  const list = data.defense || data.defenses || [];
  let body;
  if (!list.length) {
    body = emptyState("防御引擎尚未执行处置动作");
  } else {
    body = '<div class="sub-card-list">' + list.slice(-30).reverse().map((d) => `
      <div class="sub-card">
        <div class="sub-card-head"><span class="sub-card-title">${esc(zhAction(d.action_type))}</span><span class="sub-card-time">${formatUs(d.time_us)}</span></div>
        <div class="sub-card-body">目标 ${esc((d.target_asset_ids || []).map(deviceName).join("、") || "-")} · 状态 ${esc(zhStatus(d.status))}${d.result?.note ? ` · ${esc(d.result.note)}` : ""}</div>
      </div>`).join("") + "</div>";
  }
  content.innerHTML = `
    <div class="sub-view-header">
      <h3>🛡 主动安全防御</h3>
      <span class="sub-view-meta">累计动作 ${list.length} 条</span>
    </div>${body}`;
}

function renderEvaluation(data) {
  const content = $("#view-content");
  const comparison = data.comparison || {};
  const runs = Array.isArray(data.runs) ? data.runs : [];
  const row = runs[0] || {};
  const bm = row.business_metrics || {};
  const rm = row.recognition_metrics || {};
  const dm = row.defense_metrics || {};

  const impact = firstScalar(comparison.impact_reduction_rate);
  const score = firstScalar(comparison.overall_score);
  const detection = firstScalar(comparison.detection_result);
  const delay = rm.detection_delay_ms ?? row.detection_delay_ms;
  const actionSuccess = dm.action_success ?? row.action_success;
  const maxDeviation = bm.maximum_deviation ?? row.maximum_deviation;

  const reason = data.reason ? `<div class="sub-empty">${esc(data.reason)}</div>` : "";
  const cards = [];

  if (impact != null) cards.push({ label: "攻击影响降低率", value: fmtPct(impact), tone: "ok" });
  else if (bm.evaluable === false) cards.push({ label: "攻击影响降低率", value: "无攻击基准，暂无可比指标", tone: "muted" });

  if (score != null) cards.push({ label: "综合效能得分", value: Number(score).toFixed(2), tone: "ok" });
  if (detection != null) cards.push({ label: "识别与检出判定", value: esc(detection), tone: "warn" });
  if (delay != null) cards.push({ label: "识别响应时延", value: `${esc(delay)} ms`, tone: "warn" });
  if (actionSuccess != null) cards.push({ label: "防御动作成功率", value: fmtPct(actionSuccess), tone: "ok" });
  if (maxDeviation != null) cards.push({ label: "业务最大偏离", value: esc(String(maxDeviation)), tone: "muted" });

  const body = cards.length
    ? '<div class="sub-card-list">' + cards.map((c) => `
      <div class="sub-card">
        <div class="sub-card-head"><span class="sub-card-title">${esc(c.label)}</span></div>
        <div class="sub-card-value ${c.tone}">${c.value}</div>
      </div>`).join("") + "</div>"
    : (reason || emptyState("暂无评估结果，请完成一次完整运行"));

  content.innerHTML = `
    <div class="sub-view-header">
      <h3>📊 课题攻防效能综合评估</h3>
      <span class="sub-view-meta">单场景评估结论</span>
    </div>${body}`;
}

function renderOtherView(view, data) {
  const content = $("#view-content");
  if (!content) return;

  if (view === "timeline") return renderTimeline(data);
  if (view === "observations") return renderObservations(data);
  if (view === "recognition") return renderRecognition(data);
  if (view === "defense") return renderDefense(data);
  if (view === "evaluation") return renderEvaluation(data);

  content.innerHTML = `
    <div class="sub-view-header">
      <h3>${esc(view.toUpperCase())} 视图</h3>
    </div>
    <div class="sub-empty">该视图暂未提供可视化展示</div>`;
}
function bindEvents() {
  setupTopoInteraction();
  const zoomReset = $("#zoom-reset");
  if (zoomReset) zoomReset.addEventListener("click", resetTopoView);

  $("#create-run").addEventListener("click", createRun);

  document.querySelectorAll("[data-action]").forEach((button) => {
    button.addEventListener("click", () => control(button.dataset.action));
  });

  $("#btn-single-step").addEventListener("click", stepSimulation);
  $("#btn-refresh").addEventListener("click", refreshViews);

  $("#auto-step-toggle").addEventListener("change", (e) => {
    updateAutoStep(e.target.checked);
  });

  const packetLayer = $("#topo-packets");
  if (packetLayer) {
    packetLayer.addEventListener("click", (event) => {
      if (state.dragMoved) return;
      const particle = event.target.closest && event.target.closest("[data-message-id]");
      if (!particle) return;
      const message = packetMessageMap.get(particle.getAttribute("data-message-id"));
      if (!message) return;
      event.stopPropagation();
      openPacketInspector(message);
    });
  }

  const packetInspector = $("#packet-inspector");
  if (packetInspector) {
    const closeButton = $("#packet-inspector-close");
    if (closeButton) closeButton.addEventListener("click", closePacketInspector);
    document.querySelectorAll("[data-packet-close]").forEach((node) => {
      node.addEventListener("click", closePacketInspector);
    });
    const copyButton = $("#packet-inspector-copy");
    if (copyButton) copyButton.addEventListener("click", copyPacketJson);
  }

  document.addEventListener("keydown", (event) => {
    if (event.key === "Escape") closePacketInspector();
  });

  const particleFilter = $("#particle-filter");
  if (particleFilter) {
    particleFilter.addEventListener("change", (e) => {
      state.particleFilter = e.target.value;
      particleMap.forEach((particle, id) => {
        if (state.particleFilter !== "all" && particle.category !== state.particleFilter) {
          removeParticleNode(`msg:${id}`);
        }
      });
    });
  }

  $("#seek-replay").addEventListener("click", seekReplay);

  document.querySelectorAll(".tab").forEach((tab) => {
    tab.addEventListener("click", () => {
      document.querySelectorAll(".tab").forEach((node) => node.classList.remove("active"));
      tab.classList.add("active");
      state.currentView = tab.dataset.view;
      if (state.currentView === "system") {
        showTopoView();
      } else {
        showSubView();
      }
      refreshViews();
    });
  });
}

(async function init() {
  bindEvents();
  await loadScenarios();
})();










