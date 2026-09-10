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

/* 加载场景列表 */
async function loadScenarios() {
  const payload = await api("GET", "/api/scenarios");
  state.scenarios = payload.scenarios || [];
  const list = $("#scenario-list");
  list.innerHTML = "";
  state.scenarios.forEach((scenario) => {
    const li = document.createElement("li");
    li.innerHTML = `<strong>${esc(scenario.scenario_id)}</strong> · ${esc(scenario.name || "")}<br><span style="color:#94a3b8;font-size:11px;">设备 ${scenario.device_count} | 攻击 ${scenario.attack_count} | 攻防:${scenario.recognition_enabled ? "开" : "关"}</span>`;
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

/* 预定义设备可执行能力动作字典与纯净中文选项（去除英文括号） */
const DEVICE_CAPABILITY_MAP = {
  brk: [
    { action: "close", label: "合闸" },
    { action: "open", label: "分闸" },
  ],
  tap: [
    { action: "set_tap", label: "调节分接档位", needParam: "tap_position" },
  ],
  cool: [
    { action: "start", label: "启动冷却风机" },
    { action: "stop", label: "停止冷却风机" },
  ],
  comp: [
    { action: "connect", label: "投入无功补偿" },
    { action: "disconnect", label: "切除无功补偿" },
  ],
};

/* 更新操作表单的目标设备与动作下拉列表 */
function updateOperationForm(deviceId) {
  const targetInput = $("#op-target");
  const actionSelect = $("#op-action-select");
  const paramGroup = $("#op-param-group");
  const submitBtn = $("#btn-submit-op");

  if (!targetInput || !actionSelect || !submitBtn) return;

  const caps = DEVICE_CAPABILITY_MAP[deviceId];
  const devName = DEVICE_NAME_MAP[deviceId] || deviceId;
  const fullName = `${devName} ${deviceId}`;

  // 非受控设备（传感器、采集单元等）：直接显示中文名+代号，禁用动作选项，不加任何冗余括号后缀
  if (!caps || caps.length === 0) {
    targetInput.value = fullName;
    targetInput.dataset.realId = "";
    actionSelect.innerHTML = `<option value="">无可执行指令</option>`;
    actionSelect.disabled = true;
    paramGroup.style.display = "none";
    submitBtn.disabled = true;
    return;
  }

  targetInput.value = fullName;
  targetInput.dataset.realId = deviceId;
  actionSelect.innerHTML = caps.map((c) => `<option value="${c.action}">${c.label}</option>`).join("");
  actionSelect.disabled = false;
  submitBtn.disabled = false;

  const checkParamNeed = () => {
    const selectedAction = actionSelect.value;
    const curCap = caps.find((c) => c.action === selectedAction);
    if (curCap && curCap.needParam === "tap_position") {
      paramGroup.style.display = "flex";
      $("#op-param-label").textContent = "设定目标分接档位 (1-9档)";
    } else {
      paramGroup.style.display = "none";
    }
  };

  actionSelect.onchange = checkParamNeed;
  checkParamNeed();
}

/* 提交业务操作 */
async function submitOperation(event) {
  event.preventDefault();
  if (!state.runId) return;
  const targetInput = $("#op-target");
  const target = targetInput ? targetInput.dataset.realId : "";
  const actionSelect = $("#op-action-select");
  const action = actionSelect ? actionSelect.value : "";
  if (!target || !action) return;

  const opPayload = {
    request_id: `op-${Date.now()}`,
    target_asset_id: target,
    action_type: action,
  };

  if (action === "set_tap") {
    const paramVal = parseInt($("#op-param-val").value || "5", 10);
    opPayload.parameters = { tap_position: paramVal };
  }

  const payload = await api("POST", `/api/runs/${state.runId}/operations`, opPayload);
  if (payload.error) {
    setStatus(`下发失败: ${payload.error}`, true);
  } else {
    setStatus(`已下发指令: ${action}@${target} (${payload.status || "ok"})`);
  }
  await refreshViews();
}

/* 回放定位 */
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
    updateEnvironmentBar(data);
    renderTopology(data);
    updatePacketFlow(data);
    updateAlertFeed(data);
    if (state.selectedDeviceId) {
      inspectDevice(state.selectedDeviceId, false);
    }
  } else {
    showSubView();
    renderOtherView(state.currentView, data);
    // 异步拉取 system 数据仅用于更新顶部统计条与全站电压电流，绝不触发拓扑重绘显示
    api("GET", `/api/runs/${state.runId}/views?view=system`).then((res) => {
      if (res.data) {
        state.systemData = res.data;
        updateMetricsRibbon(res.data);
        updateEnvironmentBar(res.data);
      }
    });
  }
}

/* 更新全站环境指标条 */
function updateEnvironmentBar(data) {
  const env = data.environment || {};
  const busV = env.bus_voltage_kv != null ? env.bus_voltage_kv.toFixed(2) : "10.00";
  const lineI = env.line_current_a != null ? env.line_current_a.toFixed(2) : "0.00";
  const inFlight = (data.in_flight_messages || []).length;

  const vElem = $("#env-bus-v");
  const iElem = $("#env-line-i");
  const fElem = $("#env-in-flight");
  if (vElem) vElem.textContent = busV;
  if (iElem) iElem.textContent = lineI;
  if (fElem) fElem.textContent = inFlight;
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

/* 更新右下角事件告警流 */
function updateAlertFeed(data) {
  const feed = $("#alert-feed");
  const items = [];

  (data.recognition || []).forEach((r) => {
    items.push({
      time: r.timestamp_us || 0,
      type: "danger",
      title: `[威胁告警] ${esc(r.detected_type || "异常")}`,
      desc: `目标: ${esc((r.target_asset_ids || []).join(","))} | 置信度: ${r.confidence ?? "-"}`,
    });
  });

  (data.defense || []).forEach((d) => {
    items.push({
      time: d.timestamp_us || 0,
      type: "normal",
      title: `[防御执行] ${esc(d.strategy_id || "策略响应")}`,
      desc: `目标: ${esc(d.target_asset_id)} | 动作: ${esc(d.action)}`,
    });
  });

  const obs = data.observations || {};
  (obs.missing || []).forEach((m) => {
    items.push({
      time: m.window_end_us || data.time_us || 0,
      type: "warning",
      title: `[数据缺失] ${esc(m.provider_id || "监测通道")}`,
      desc: `源未按时递交证据数据`,
    });
  });
  (obs.late || []).forEach((l) => {
    items.push({
      time: l.event?.timestamp_us || data.time_us || 0,
      type: "warning",
      title: `[数据迟到] ${esc(l.event?.provider_id || "监测通道")}`,
      desc: `时延超出允许时窗`,
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

/* 拓扑坐标表（工业逻辑优化：授时系统-合并单元-电流互感器严格垂直对齐；无功补偿-电压互感器垂直对齐） */
const NODE_COORDINATES = {
  // === 站控层 (Y: 55) ===
  station:    { x: 280, y: 55 },   // 站端监控 (对接 prot & mc)
  time_svc:   { x: 650, y: 55 },   // 授时系统 (X:650，垂直向下对齐 mu 和 ct_current)

  // === 间隔层 (Y: 190) ===
  prot:       { x: 180, y: 190 },  // 保护装置 (直通 ctl_brk)
  mc:         { x: 400, y: 190 },  // 测控装置 (接收 mu 遥测，下发三路控制)

  // === 过程层 - 二次采集/控制层 (Y: 330) ===
  ctl_brk:    { x: 80,  y: 330 },  // 断路器智能终端 (X:80，正下方垂直引至 brk)
  ctl_tap:    { x: 230, y: 330 },  // 分接开关接口 (X:230，正下方垂直引至 tap)
  ctl_cool:   { x: 380, y: 330 },  // 冷却系统接口 (X:380，正下方垂直引至 cool)
  mu:         { x: 650, y: 330 },  // 合并单元 (X:650，正上方 time_svc，正下方 ct_current)
  comp:       { x: 790, y: 330 },  // 无功补偿支路 (X:790，垂直对齐下方 vt_voltage)

  // === 过程层 - 一次主设备与传感器本体 (Y: 480) ===
  brk:        { x: 80,  y: 480 },  // 10kV断路器 (垂直对齐 ctl_brk)
  tap:        { x: 230, y: 480 },  // 主变压器 (垂直对齐 ctl_tap)
  cool:       { x: 380, y: 480 },  // 冷却风机 (垂直对齐 ctl_cool)
  oil_temp:   { x: 510, y: 480 },  // 油温传感器 (贴近变压器测温，斜向送往 mu)
  ct_current: { x: 650, y: 480 },  // 电流互感器 (X:650，正上方垂直汇入 mu)
  vt_voltage: { x: 790, y: 480 },  // 电压互感器 (X:790，垂直对齐上方 comp)
};

/* 提取设备即时动态物理指标 */
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

  // 传感器读数
  if (devId === "ct_current") {
    const i = env?.line_current_a != null ? env.line_current_a.toFixed(1) : "0.0";
    return `[${i} A]`;
  }
  if (devId === "vt_voltage") {
    const v = env?.bus_voltage_kv != null ? env.bus_voltage_kv.toFixed(1) : "10.0";
    return `[${v} kV]`;
  }
  if (devId === "oil_temp") {
    const t = mgmt["tap"]?.state?.oil_temp_c != null ? mgmt["tap"].state.oil_temp_c.toFixed(1) : "40.0";
    return `[${t} ℃]`;
  }
  if (devId === "mu") {
    return "[采集合并]";
  }
  if (devId === "prot") {
    return "[定值 50A]";
  }
  if (devId === "mc") {
    return "[远控闭环]";
  }
  if (devId === "station") {
    return "[SCADA监视]";
  }
  if (devId === "time_svc") {
    return "[GNSS授时]";
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
      const dynamicText = getDeviceDynamicText(id, mgmt, env);

      const g = document.createElementNS("http://www.w3.org/2000/svg", "g");
      g.setAttribute("class", `topo-node ${statusClass} ${isSelected ? "selected" : ""}`);
      g.setAttribute("transform", `translate(${coord.x}, ${coord.y})`);
      g.setAttribute("data-device-id", id);

      g.innerHTML = `
        <rect width="90" height="48" rx="6"></rect>
        <text x="45" y="18" text-anchor="middle" class="node-title">${esc(dev.name || id)}</text>
        <text x="45" y="32" text-anchor="middle" class="node-sub">${esc(id)}</text>
        <text x="45" y="44" text-anchor="middle" class="node-status-text" id="node-text-${esc(id)}">${esc(dynamicText)}</text>
      `;

      g.addEventListener("click", () => {
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
      g.setAttribute("class", `topo-node ${statusClass} ${isSelected ? "selected" : ""}`);

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
let animFrameId = null;

function animateParticles(now) {
  const packetLayer = $("#topo-packets");
  if (packetLayer) {
    // 渲染存活的粒子
    const toDelete = [];
    let frag = document.createDocumentFragment();

    particleMap.forEach((p, id) => {
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

      const circle = document.createElementNS("http://www.w3.org/2000/svg", "circle");
      circle.setAttribute("cx", curX.toFixed(1));
      circle.setAttribute("cy", curY.toFixed(1));
      circle.setAttribute("r", "4.5");
      circle.setAttribute("class", `msg-particle ${p.businessType}`);
      circle.setAttribute("opacity", opacity.toFixed(2));
      frag.appendChild(circle);
    });

    toDelete.forEach((id) => particleMap.delete(id));
    packetLayer.innerHTML = "";
    packetLayer.appendChild(frag);
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
    const packetLayer = $("#topo-packets");
    if (packetLayer) packetLayer.innerHTML = "";
    return;
  }

  const inFlights = data.in_flight_messages || [];
  const incomingIds = new Set();
  const now = performance.now();

  inFlights.slice(0, 24).forEach((msg, idx) => {
    const id = msg.message_id || `${msg.sender_id}->${msg.receiver_id}`;
    incomingIds.add(id);

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
      x1: src.x + 45,
      y1: src.y + 24,
      x2: dst.x + 45,
      y2: dst.y + 24,
      businessType: msg.business_type || "sampling",
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

/* 检查并展示选中的设备详细信息 */
function inspectDevice(deviceId, fillTarget = false) {
  state.selectedDeviceId = deviceId;
  const inspector = $("#device-inspector");
  if (!state.systemData) return;

  const topo = state.systemData.topology || {};
  const dev = (topo.devices || []).find((d) => d.device_id === deviceId);
  const mgmt = (state.systemData.management || {})[deviceId] || {};
  const env = state.systemData.environment || {};
  const devState = mgmt.state || {};
  const devEffects = mgmt.effects || {};

  if (!dev) {
    inspector.innerHTML = `<div class="inspector-placeholder">未找到设备 ${esc(deviceId)} 信息</div>`;
    return;
  }

  const hasEffects = Object.keys(devEffects).length > 0;
  let effectsHtml = '<span style="color:#10b981;">无异常作用</span>';
  if (hasEffects) {
    effectsHtml = Object.entries(devEffects)
      .map(([k, v]) => `<div class="effects-badge">⚡ ${esc(k)}: ${JSON.stringify(v)}</div>`)
      .join("");
  }

  const mergedState = { ...devState };
  if (deviceId === "ct_current") mergedState["measured_current_a"] = env.line_current_a ?? 0;
  if (deviceId === "vt_voltage") mergedState["measured_voltage_kv"] = env.bus_voltage_kv ?? 10.0;
  if (deviceId === "oil_temp") mergedState["oil_temp_c"] = (state.systemData.management["tap"]?.state?.oil_temp_c) ?? 40.0;

  const stateRows = Object.entries(mergedState)
    .map(
      ([k, v]) => `
      <div class="attr-row">
        <span class="attr-key">${esc(k)}</span>
        <span class="attr-val">${esc(typeof v === "number" ? v.toFixed(3) : (typeof v === "object" ? JSON.stringify(v) : v))}</span>
      </div>`
    )
    .join("");

  inspector.innerHTML = `
    <div class="device-card-header">
      <div class="device-card-title">${esc(dev.name || dev.device_id)}</div>
      <div class="device-card-id">${esc(dev.device_id)} · ${esc(dev.device_type)} (${esc(dev.layer)})</div>
    </div>
    <div class="device-attr-list">
      <div class="attr-row">
        <span class="attr-key">所属分层</span>
        <span class="attr-val">${esc(dev.layer)}</span>
      </div>
      <div class="attr-row">
        <span class="attr-key">设备类型</span>
        <span class="attr-val">${esc(dev.device_type)}</span>
      </div>
      <div class="attr-row">
        <span class="attr-key">受影响状态</span>
        <span class="attr-val">${effectsHtml}</span>
      </div>
      <div class="section-title" style="margin-top:8px;">实时物理量与状态参数</div>
      ${stateRows || '<div style="color:#64748b;font-size:11px;">无直接物理状态量</div>'}
    </div>
  `;

  if (fillTarget) {
    updateOperationForm(deviceId);
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

function renderSnapshot(data) {
  const content = $("#view-content");
  const snapshots = data.snapshots || {};
  content.innerHTML = `
    <h3 style="color:#38bdf8;margin-bottom:10px;">时刻历史快照回放</h3>
    <pre>${esc(JSON.stringify(snapshots, null, 2))}</pre>`;
}

function renderOtherView(view, data) {
  const content = $("#view-content");

  if (view === "timeline") {
    const attacks = data.attacks || [];
    const submissions = data.attack_submissions || [];
    const recognitions = data.recognitions || data.recognition || [];
    const defenses = data.defenses || data.defense || [];
    content.innerHTML = `
      <h3 style="color:#38bdf8;margin-bottom:12px;">⏳ 攻防全时序过程推进</h3>
      <h4 style="color:#f59e0b;margin:10px 0 6px;">攻击计划队列 (${attacks.length})</h4>
      <pre>${esc(JSON.stringify(attacks, null, 2))}</pre>
      <h4 style="color:#ef4444;margin:10px 0 6px;">攻击生效提交 (${submissions.length})</h4>
      <pre>${esc(JSON.stringify(submissions, null, 2))}</pre>
      <h4 style="color:#06b6d4;margin:10px 0 6px;">智能攻击识别记录 (${recognitions.length})</h4>
      <pre>${esc(JSON.stringify(recognitions, null, 2))}</pre>
      <h4 style="color:#10b981;margin:10px 0 6px;">主动防御动作记录 (${defenses.length})</h4>
      <pre>${esc(JSON.stringify(defenses, null, 2))}</pre>
    `;
    return;
  }

  if (view === "evaluation") {
    const comparison = data.comparison || {};
    content.innerHTML = `
      <h3 style="color:#38bdf8;margin-bottom:12px;">📊 课题攻防效能综合评估对比</h3>
      <div style="background:#132238;border:1px solid #1e3a5f;padding:12px;border-radius:6px;margin-bottom:12px;">
        <div style="font-size:14px;color:#f8fafc;font-weight:600;margin-bottom:6px;">
          攻击影响降低率 (指标≥90%): <span style="color:#10b981;font-size:18px;">${comparison.impact_reduction_rate != null ? (comparison.impact_reduction_rate * 100).toFixed(2) + "%" : "未完成/无攻击"}</span>
        </div>
        <div style="font-size:14px;color:#f8fafc;font-weight:600;margin-bottom:6px;">
          综合效能得分: <span style="color:#38bdf8;font-size:18px;">${comparison.overall_score != null ? comparison.overall_score : "-"}</span>
        </div>
        <div style="font-size:14px;color:#f8fafc;font-weight:600;">
          识别与检出判定: <span style="color:#f59e0b;">${esc(comparison.detection_result ?? "无异常")}</span>
        </div>
      </div>
      <h4 style="color:#94a3b8;margin:8px 0;">原始评估比对数据</h4>
      <pre>${esc(JSON.stringify(data, null, 2))}</pre>
    `;
    return;
  }

  content.innerHTML = `
    <h3 style="color:#38bdf8;margin-bottom:10px;">${esc(view.toUpperCase())} 视图详情</h3>
    <pre>${esc(JSON.stringify(data, null, 2))}</pre>`;
}

function bindEvents() {
  $("#create-run").addEventListener("click", createRun);

  document.querySelectorAll("[data-action]").forEach((button) => {
    button.addEventListener("click", () => control(button.dataset.action));
  });

  $("#btn-single-step").addEventListener("click", stepSimulation);
  $("#btn-refresh").addEventListener("click", refreshViews);

  $("#auto-step-toggle").addEventListener("change", (e) => {
    updateAutoStep(e.target.checked);
  });

  $("#operation-form").addEventListener("submit", submitOperation);
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










