/* 2030 仿真系统演示平面前端脚本。只消费 /api 摘要接口，不重新执行仿真算法。 */
"use strict";

const state = {
  scenarios: [],
  selectedScenario: null,
  runId: null,
  currentView: "system",
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
  const response = await fetch(path, options);
  return response.json();
}

function setStatus(text) {
  $("#status-bar").textContent = text;
}

async function loadScenarios() {
  const payload = await api("GET", "/api/scenarios");
  state.scenarios = payload.scenarios || [];
  const list = $("#scenario-list");
  list.innerHTML = "";
  state.scenarios.forEach((scenario) => {
    const li = document.createElement("li");
    li.textContent = `${scenario.scenario_id} · ${scenario.name || ""}（设备 ${scenario.device_count}，攻击 ${scenario.attack_count}）`;
    li.dataset.scenarioId = scenario.scenario_id;
    li.addEventListener("click", () => {
      state.selectedScenario = scenario.scenario_id;
      list.querySelectorAll("li").forEach((node) => node.classList.remove("selected"));
      li.classList.add("selected");
      $("#create-run").disabled = false;
    });
    list.appendChild(li);
  });
  setStatus(`已加载 ${state.scenarios.length} 个场景`);
}

async function createRun() {
  if (!state.selectedScenario) return;
  const payload = await api("POST", "/api/runs", { scenario_id: state.selectedScenario });
  if (payload.error) {
    setStatus(payload.error);
    return;
  }
  state.runId = payload.run_id;
  $("#run-id").textContent = payload.run_id;
  setStatus(`运行已创建：${payload.run_id}`);
  await refreshViews();
}

async function control(action) {
  if (!state.runId) return;
  const payload = await api("POST", `/api/runs/${state.runId}/operations`, {
    request_id: `ctrl-${Date.now()}`,
    target_asset_id: "run",
    operation_type: action,
  });
  setStatus(`控制 ${action}：${payload.status || payload.error || ""}`);
  await refreshViews();
}

async function submitOperation(event) {
  event.preventDefault();
  if (!state.runId) return;
  const target = $("#op-target").value.trim();
  const action = $("#op-action").value.trim();
  const payload = await api("POST", `/api/runs/${state.runId}/operations`, {
    request_id: `op-${Date.now()}`,
    target_asset_id: target,
    action_type: action,
  });
  setStatus(`操作 ${action}@${target}：${payload.status || payload.error || ""}（${payload.reason || ""}）`);
  await refreshViews();
}

async function seekReplay() {
  if (!state.runId) return;
  const timeUs = $("#replay-time").value || "0";
  const payload = await api("GET", `/api/runs/${state.runId}/views?view=snapshot&time_us=${timeUs}`);
  renderSnapshot(payload.data || {});
}

async function refreshViews() {
  if (!state.runId) return;
  const payload = await api("GET", `/api/runs/${state.runId}/views?view=${state.currentView}`);
  renderView(state.currentView, payload.data || {});
}

function renderSnapshot(data) {
  const content = $("#view-content");
  const snapshots = data.snapshots || {};
  content.innerHTML = `<h3>回放快照</h3><pre>${esc(JSON.stringify(snapshots, null, 2))}</pre>`;
}

function renderTopology(data) {
  const topology = data.topology || {};
  const devices = topology.devices || [];
  const links = topology.links || [];
  const observations = data.observations || {};
  const html = `
    <h3>设备拓扑（${devices.length} 台设备，${links.length} 条连接）</h3>
    <table>
      <thead><tr><th>设备</th><th>类型</th><th>层级</th></tr></thead>
      <tbody>${devices.map((d) => `<tr><td>${esc(d.device_id)}</td><td>${esc(d.device_type)}</td><td>${esc(d.layer)}</td></tr>`).join("")}</tbody>
    </table>
    <h4>观测摘要</h4>
    <p>事件 ${observations.event_count ?? 0} 条，缺失 ${(observations.missing || []).length} 项，迟到 ${(observations.late || []).length} 项。</p>`;
  $("#view-content").innerHTML = html;
}

function renderTimeline(data) {
  const attacks = data.attacks || [];
  const submissions = data.attack_submissions || [];
  const recognitions = data.recognitions || [];
  const defenses = data.defenses || [];
  const html = `
    <h3>时间线</h3>
    <h4>攻击计划（${attacks.length}）</h4><pre>${esc(JSON.stringify(attacks, null, 2))}</pre>
    <h4>攻击提交（${submissions.length}）</h4><pre>${esc(JSON.stringify(submissions, null, 2))}</pre>
    <h4>识别（${recognitions.length}）</h4><pre>${esc(JSON.stringify(recognitions, null, 2))}</pre>
    <h4>防御（${defenses.length}）</h4><pre>${esc(JSON.stringify(defenses, null, 2))}</pre>`;
  $("#view-content").innerHTML = html;
}

function renderComparison(data) {
  const rows = data.runs || [];
  const comparison = data.comparison || {};
  const html = `
    <h3>评估</h3>
    <h4>影响降低率</h4><pre>${esc(JSON.stringify(comparison.impact_reduction_rate ?? null, null, 2))}</pre>
    <h4>综合分</h4><pre>${esc(JSON.stringify(comparison.overall_score ?? null, null, 2))}</pre>
    <h4>识别结果</h4><pre>${esc(JSON.stringify(comparison.detection_result ?? null, null, 2))}</pre>`;
  $("#view-content").innerHTML = html;
}

function renderView(view, data) {
  if (view === "system") return renderTopology(data);
  if (view === "timeline") return renderTimeline(data);
  if (view === "evaluation") return renderComparison(data);
  $("#view-content").innerHTML = `<h3>${esc(view)}</h3><pre>${esc(JSON.stringify(data, null, 2))}</pre>`;
}

function bindEvents() {
  $("#create-run").addEventListener("click", createRun);
  document.querySelectorAll("[data-action]").forEach((button) => {
    button.addEventListener("click", () => control(button.dataset.action));
  });
  $("#operation-form").addEventListener("submit", submitOperation);
  $("#seek-replay").addEventListener("click", seekReplay);
  document.querySelectorAll(".tab").forEach((tab) => {
    tab.addEventListener("click", () => {
      document.querySelectorAll(".tab").forEach((node) => node.classList.remove("active"));
      tab.classList.add("active");
      state.currentView = tab.dataset.view;
      refreshViews();
    });
  });
}

(async function init() {
  bindEvents();
  await loadScenarios();
})();
