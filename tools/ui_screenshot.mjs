#!/usr/bin/env node
/**
 * 演示平面无头截图工具（自检用，不参与仿真运行）。
 *
 * 流程：启动 Edge/Chrome 无头实例（CDP）→ 打开演示平面 → 在页面内创建运行、
 * 启动、步进、（可选）触发手动攻击 → 截图落盘。
 *
 * 依赖：Node 18+（内置 fetch / WebSocket）与 Windows 上的 Edge 或 Chrome，无需第三方包。
 *
 * 用法（先另开一个终端跑 `python main.py --ui ... --port 8765`）：
 *   node tools/ui_screenshot.mjs --url http://127.0.0.1:8765/ \
 *        --scenario substation-em-attack --mode attack --out shot.png
 *
 * 参数：--url 演示平面地址；--scenario 场景 ID（默认取列表第一项）；
 *       --mode none|attack；--steps 步进次数；--out 输出 PNG；--port CDP 端口。
 */
import { spawn } from "node:child_process";
import { existsSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";

const BROWSER_CANDIDATES = [
  "C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe",
  "C:/Program Files/Microsoft/Edge/Application/msedge.exe",
  "C:/Program Files/Google/Chrome/Application/chrome.exe",
  "C:/Program Files (x86)/Google/Chrome/Application/chrome.exe",
];

function parseArgs(argv) {
  const args = { url: "http://127.0.0.1:8765/", mode: "none", steps: 16, port: 9333, scenario: "", out: "ui.png" };
  for (let i = 0; i < argv.length; i += 2) {
    const key = String(argv[i] || "").replace(/^--/, "");
    if (!key) continue;
    const value = argv[i + 1];
    if (key === "steps" || key === "port") args[key] = Number(value);
    else args[key] = value;
  }
  return args;
}

const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));

async function waitForDebugger(port, timeoutMs = 20000) {
  const deadline = Date.now() + timeoutMs;
  while (Date.now() < deadline) {
    try {
      const res = await fetch(`http://127.0.0.1:${port}/json/version`);
      if (res.ok) return;
    } catch {
      /* 尚未就绪 */
    }
    await sleep(300);
  }
  throw new Error(`浏览器调试端口 ${port} 未就绪`);
}

async function connect(port) {
  const targets = await (await fetch(`http://127.0.0.1:${port}/json/list`)).json();
  const page = targets.find((t) => t.type === "page");
  if (!page) throw new Error("未找到页面目标");

  const ws = new WebSocket(page.webSocketDebuggerUrl);
  let nextId = 1;
  const pending = new Map();
  const logs = [];
  ws.addEventListener("message", (event) => {
    const msg = JSON.parse(event.data);
    if (msg.id && pending.has(msg.id)) {
      pending.get(msg.id)(msg);
      pending.delete(msg.id);
    } else if (msg.method === "Log.entryAdded" && msg.params.entry.level === "error") {
      logs.push(msg.params.entry.text);
    } else if (msg.method === "Runtime.exceptionThrown") {
      logs.push(`exception: ${msg.params.exceptionDetails.text}`);
    }
  });
  await new Promise((resolve, reject) => {
    ws.addEventListener("open", resolve);
    ws.addEventListener("error", reject);
  });

  const send = (method, params) => new Promise((resolve, reject) => {
    const id = nextId++;
    pending.set(id, (msg) => (msg.error ? reject(new Error(`${method}: ${JSON.stringify(msg.error)}`)) : resolve(msg.result)));
    ws.send(JSON.stringify({ id, method, params: params || {} }));
  });
  return { send, logs, close: () => ws.close() };
}

async function main() {
  const args = parseArgs(process.argv.slice(2));
  const browser = process.env.UI_SCREENSHOT_BROWSER || BROWSER_CANDIDATES.find((p) => existsSync(p));
  if (!browser) throw new Error("未找到 Edge/Chrome，可用环境变量 UI_SCREENSHOT_BROWSER 指定");

  const profile = join(tmpdir(), `ui-shot-${Date.now()}`);
  const proc = spawn(browser, [
    "--headless=new", "--no-sandbox", "--disable-gpu", "--disable-software-rasterizer",
    "--disable-dev-shm-usage", "--in-process-gpu", "--no-first-run", "--no-default-browser-check",
    `--remote-debugging-port=${args.port}`, `--user-data-dir=${profile}`, "about:blank",
  ], { stdio: "ignore" });

  try {
    await waitForDebugger(args.port);
    const { send, logs, close } = await connect(args.port);
    await send("Page.enable");
    await send("Runtime.enable");
    await send("Log.enable");
    await send("Emulation.setDeviceMetricsOverride", { width: 1600, height: 1000, deviceScaleFactor: 1, mobile: false });
    await send("Page.navigate", { url: args.url });
    await sleep(2200);

    const drive = `(async () => {
      const wait = (ms) => new Promise((r) => setTimeout(r, ms));
      let li = ${JSON.stringify(args.scenario)} ? document.querySelector('#scenario-list li[data-scenario-id="' + ${JSON.stringify(args.scenario)} + '"]') : null;
      if (!li) li = document.querySelector('#scenario-list li');
      if (!li) return 'no scenario';
      li.click();
      await createRun();
      await control('start');
      for (let i = 0; i < ${args.steps}; i++) { await control('step'); await wait(25); }
      if (${JSON.stringify(args.mode)} === 'attack') {
        await api('POST', '/api/runs/' + state.runId + '/operations',
          { request_id: 'shot-atk-' + Date.now(), target_asset_id: 'em_attack_node', action_type: 'start_attack' });
        for (let i = 0; i < 5; i++) { await control('step'); await wait(25); }
      }
      await refreshViews();
      return JSON.stringify({
        scenario: state.selectedScenario, runId: state.runId,
        nodes: document.querySelectorAll('.topo-node').length,
        links: document.querySelectorAll('.topo-link').length,
        particles: document.querySelectorAll('#topo-packets circle').length,
        viewBox: document.querySelector('#topo-svg').getAttribute('viewBox'),
        legendLinks: [...document.querySelectorAll('#legend-links .legend-item')].map((n) => n.textContent.trim()),
        ctText: (document.querySelector('#node-text-ct_current') || {}).textContent,
      });
    })()`;
    const result = await send("Runtime.evaluate", { expression: drive, awaitPromise: true, returnByValue: true });
    await sleep(1500); // 等粒子动画推进几帧
    const shot = await send("Page.captureScreenshot", { format: "png" });
    writeFileSync(args.out, Buffer.from(shot.data, "base64"));
    close();
    console.log(result.result && result.result.value);
    console.log(`saved: ${args.out}`);
    if (logs.length) console.log(`page errors: ${logs.join(" | ")}`);
  } finally {
    proc.kill();
  }
}

main().catch((error) => {
  console.error(`ERR ${error.message}`);
  process.exit(1);
});
