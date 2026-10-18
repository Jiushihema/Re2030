/* Timing-scene presentation only. All electrical quantities come from the model. */
(function (root) {
  'use strict';
  const esc = value => String(value ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  const fixed = (n, digits = 3) => Number.isFinite(n) ? n.toFixed(digits) : '—';
  const time = us => `${fixed(us / 1e6, 6)} s`;
  let latest, selected, callbacks = {}, runId, lastTime, lastPaint = 0;

  // A moving viewport over received samples, never synthetic future points.
  class SampleScroller {
    constructor() { this.rows = []; this.end = null; this.target = null; }
    update(rows, snap = false) {
      if (!rows?.length) return;
      this.rows = rows;
      this.target = rows.at(-1).local_time_us;
      if (this.end == null) this.end = Math.min(this.target, rows[0].local_time_us + 60000);
      this.end = Math.max(this.end, rows[0].local_time_us);
      if (snap) this.end = this.target;
    }
    tick(delta, running, rate, dtUs) {
      if (running && this.end != null) this.end = Math.min(this.target, this.end + Math.min(delta, 200) * rate * dtUs / 1000);
    }
    visible() { return this.rows.filter(r => r.local_time_us >= this.end - 60000 && r.local_time_us <= this.end); }
  }
  let scroller = new SampleScroller();

  function paintWave() {
    if (!latest || document.getElementById('timing-workbench').hidden) return;
    const trip = document.getElementById('timing-wave-mode').value === 'trip';
    const rows = trip ? latest.timing_lab?.trip_waveform : scroller.visible();
    document.getElementById('timing-wave').innerHTML = waveform(rows, !trip && scroller.end != null ? {start:scroller.end - 60000, end:scroller.end} : null);
  }

  function animate(delta, running, rate) {
    scroller.tick(delta, running && latest?.status === 'running', rate, latest?.dt_us || 100000);
    lastPaint += delta;
    if (running && lastPaint >= 32 && document.getElementById('timing-wave-mode')?.value === 'live') {
      lastPaint = 0; paintWave();
    }
  }

  function open() {
    if (!latest?.topology?.layout?.timing_lab) return;
    const panel = document.getElementById('timing-workbench');
    panel.hidden = false;
    if (!panel.dataset.placed) {
      panel.style.width = `${Math.min(820, window.innerWidth - 32)}px`;
      panel.style.height = `${Math.min(420, window.innerHeight - 60)}px`;
      panel.style.left = `${Math.max(16, (window.innerWidth - Math.min(820, window.innerWidth - 32)) / 2)}px`;
      panel.style.top = `${Math.max(16, window.innerHeight - Math.min(420, window.innerHeight - 60) - 24)}px`;
      panel.dataset.placed = '1';
    }
    paintWave();
  }

  function waveform(rows, bounds = null) {
    if (!rows?.length) return '<p class="timing-empty">推进仿真以获取两路有效采样；跳闸后可切换到“最近跳闸证据”。</p>';
    const amplitude = Math.max(1, ...rows.flatMap(r => [Math.abs(r.i1), Math.abs(r.i2), Math.abs(r.id)]));
    const start = bounds?.start ?? rows[0].local_time_us, span = Math.max(250, (bounds?.end ?? rows.at(-1).local_time_us) - start);
    const x = r => 48 + (r.local_time_us - start) / span * 820;
    const y = value => 76 - value / amplitude * 52;
    const paths = [['i1','#67e8f9'],['i2','#c4b5fd'],['id','#fb7185']].map(([field,color]) =>
      `<path data-trace="${field}" d="${rows.map((r,i) => `${i?'L':'M'}${fixed(x(r),1)},${fixed(y(r[field]),1)}`).join(' ')}" fill="none" stroke="${color}" stroke-width="1.8"/>`).join('');
    const ticks = [0,.25,.5,.75,1].map(p => `<text x="${48+820*p}" y="147" text-anchor="middle">${fixed((start+span*p)/1000,2)}</text>`).join('');
    return `<div class="timing-wave-legend"><span style="color:#67e8f9">━ MU1 进线 i₁</span><span style="color:#c4b5fd">━ MU2 出线 i₂</span><span style="color:#fb7185">━ 瞬时差流 i₁+i₂</span></div>
      <svg class="timing-wave-svg" viewBox="0 0 920 166" preserveAspectRatio="none" role="img" aria-label="两路原始方向电流与瞬时差流，按本地采样编号对齐">
        <path d="M48 22H868 M48 76H868 M48 130H868" stroke="#26374e" fill="none"/>
        <text x="3" y="25">${fixed(amplitude,0)}</text><text x="20" y="80">0</text><text x="0" y="132">−${fixed(amplitude,0)}</text>
        <text x="4" y="12">A</text>${paths}${ticks}<text x="890" y="147">ms</text>
      </svg><p class="timing-caption">横轴：本地编号对应时刻；两路物理方向相反。i₁+i₂ 为瞬时差流，Iᵈ 是单周波相量和的有效值，二者含义不同。</p>`;
  }

  function gateRows(gates) {
    return (gates || []).map(g => {
      const status = !g.implemented ? '预留，暂不参与判定' : !g.enabled ? '未启用，旁路' : g.passed ? '满足' : '未满足';
      return `<div class="timing-gate ${g.implemented && g.enabled && g.passed ? 'met' : ''}"><span>${esc(g.label)}${g.threshold != null ? `<small>门槛 ${fixed(g.threshold,2)} A RMS</small>`:''}</span><b>${status}</b></div>`;
    }).join('');
  }

  function render(data, deviceId) {
    latest = data; selected = deviceId;
    const lab = data.timing_lab, enabled = !!data.topology?.layout?.timing_lab;
    const $ = id => document.getElementById(id);
    document.body.classList.toggle('timing-mode', enabled);
    ['timing-result','timing-device-info'].forEach(id => { $(id).hidden = !enabled; });
    if (!enabled) { $('timing-workbench').hidden = true; return; }
    if (runId !== data.run_id) {
      runId = data.run_id; lastTime = null;
      scroller = new SampleScroller();
      $('timing-wave-mode').value = 'live';
      $('timing-workbench').hidden = true;
    }
    scroller.update(lab?.waveform, data.status !== 'running' && data.time_us !== lastTime);
    lastTime = data.time_us;
    const mgmt = data.management || {}, report = lab?.report || {}, lastTrip = lab?.last_trip;
    const attack = mgmt.time_attack_node?.state?.emitting;
    const breaker = mgmt.brk?.state || {};
    const clocks = [['time_svc','授时系统'],['time_svc_2','授时系统-2'],['mu','合并单元'],['mu_2','合并单元-2']];
    const clockCards = clocks.map(([id,name]) => {
      const s = mgmt[id]?.state || {}, offset = s.clock_offset_us ?? s.timing_offset_us ?? 0;
      return `<button type="button" class="timing-clock ${offset ? 'delayed' : ''}" data-timing-device="${id}"><span>${name}</span><strong>${time(s.local_time_us || 0)}</strong><small>${s.synced === false ? '等待首次对时' : offset ? `落后 ${fixed(Math.abs(offset)/1000)} ms` : '正常 · 偏移 0 ms'}</small></button>`;
    }).join('');
    const stateText = breaker.tripped ? '差动保护已出口 · 断路器已跳闸' : lab?.trip_count ? '差动保护已出口 · 等待动作／人工操作后状态' : report.operate ? '差动判据满足' : '差动保护监测中';
    $('timing-result').innerHTML = `<h2>授时链路与差动保护</h2>
      <div class="timing-reference">模型参考时刻 ${time(mgmt.time_svc?.state?.reference_time_us || 0)}<br>攻击节点：${attack ? '<b class="timing-danger">正在干扰</b>' : '未发射'} · 50 Hz / 4 kHz</div>
      <div class="timing-clock-grid">${clockCards}</div>
      <div class="timing-path">授时延迟 → MU 编号错位 → 差流 → 保护出口 → 智能终端 → 断路器</div>
      <div class="timing-outcome ${lab?.trip_count ? 'operated' : ''}">${stateText}</div>
      <div class="timing-quantities"><span>Iᵈ 差动量<strong>${fixed(report.id_a,2)} A</strong></span><span>Iʳ 制动量<strong>${fixed(report.ir_a,2)} A</strong></span></div>
      <div class="timing-gates">${gateRows(lab?.gates)}</div>
      <p class="timing-caption">判据：Iᵈ = |I₁ + I₂|；Iʳ = |I₁| + |I₂|。启用的实际门槛须同时满足。预留开关不参与计算。</p>
      ${lastTrip ? `<details open><summary>最近出口证据 · ${time(lastTrip.time_us)}</summary><p>差流 ${fixed(lastTrip.id_a,2)} A · 制动 ${fixed(lastTrip.ir_a,2)} A<br>采样错位 ${fixed(lastTrip.pair.skew_us/1000)} ms · 出口共 ${lab.trip_count} 次</p><button class="btn btn-secondary" type="button" data-timing-trip>查看跳闸波形</button></details>` : ''}
      <details><summary>模型条件与整定值</summary><p>单相正弦教学模型；CT 有效值在 100 ms 调度块内保持，内部按 250 μs 采样。按本地秒序号+smpCnt 配对，并非现场装置完整 SV 实现。真实采样时刻只作证据，不参与配对与保护判定。</p><p>默认 Imin=10 A、k=0.2，属于演示整定值；完整连续单周波数据后判断，动作展示受全站调度步长影响。结束攻击不自动合闸；人工合闸前建议先结束攻击。</p><p>本场景停用独立过流出口，仅验证授时错位引起的差动出口。方向许可与 CT 闭锁尚未建模，也不将攻击真值伪装成检测告警。</p></details>
      <h3>授时与动作事件</h3><div class="timing-event-list">${(lab?.events || []).slice(-12).reverse().map(e => `<div><time>${time(e.time_us)}</time><span>${esc(e.text)}</span></div>`).join('') || '<p>尚无事件，点击开始或单步推进。</p>'}</div>`;

    const mode = $('timing-wave-mode').value;
    const rows = mode === 'trip' ? lab?.trip_waveform : scroller.visible();
    paintWave();
    const pairs = (rows || []).slice(-6);
    $('timing-pair-table').innerHTML = pairs.length ? `<p class="timing-caption">同一编号配对，但真实采样时刻可能不同。真实时刻属于模型证据。</p><table><thead><tr><th>本地秒 / smpCnt</th><th>MU1 真实时刻</th><th>MU2 真实时刻</th><th>错位</th><th>i₁ / i₂ (A)</th></tr></thead><tbody>${pairs.map(r => `<tr class="${r.skew_us ? 'mismatch':''}"><td>${r.epoch} / ${r.smpCnt}</td><td>${time(r.true_1_us)}</td><td>${time(r.true_2_us)}</td><td>${fixed(r.skew_us/1000)} ms</td><td>${fixed(r.i1,2)} / ${fixed(r.i2,2)}</td></tr>`).join('')}</tbody></table>` : '<p>等待有效配对采样。</p>';
    const selectedState = mgmt[deviceId]?.state || {};
    let info = '点选授时系统、合并单元、差动保护、智能终端或断路器查看对应信息。';
    if (clocks.some(([id]) => id === deviceId)) {
      const offset = selectedState.clock_offset_us ?? selectedState.timing_offset_us ?? 0;
      const sample = selectedState.last_sample;
      info = `参考时间：${time(selectedState.reference_time_us || 0)}<br>本地时间：${time(selectedState.local_time_us || 0)}<br>落后量：${fixed(Math.abs(offset)/1000)} ms${sample ? `<br>最近编号：${sample.epoch} / ${sample.smpCnt}<br>真实采样时刻：${time(sample.true_time_us)}` : ''}`;
    } else if (deviceId === 'diff_prot') info = `两路配对 → 80 点相量窗 → 启用门槛取 AND → 出口。<br>当前 Iᵈ：${fixed(report.id_a,2)} A；Iʳ：${fixed(report.ir_a,2)} A。<br>四个开关分别设置；后两项仅保留接口。`;
    else if (deviceId === 'time_attack_node') info = '只攻击授时系统-2；正数参数表示时钟落后。先开始推演建立正常对照，再点击开始攻击。调整延迟前须结束攻击，重新开始后生效。';
    else if (deviceId === 'ctl_brk') info = '接收差动保护的真实跳闸消息，再沿控制硬接线发送给断路器。接收、转发与动作可在右侧事件及报文光点中查看。';
    else if (deviceId === 'brk') info = `当前位置：${breaker.position === 'closed' ? '合闸' : '分闸'}。结束攻击不会自动合闸。`;
    $('timing-device-info').innerHTML = `<h3>授时场景 · 设备说明</h3><p>${info}</p>`;
    ['mu_2','time_svc_2'].forEach(id => {
      const s = mgmt[id]?.state || {};
      document.querySelector(`[data-device-id="${id}"].topo-node`)?.classList.toggle('timing-delayed', !!(s.clock_offset_us ?? s.timing_offset_us));
    });
  }

  function bind(options) {
    callbacks = options;
    const panel = document.getElementById('timing-workbench');
    const handle = document.getElementById('timing-window-handle');
    document.getElementById('timing-window-close').addEventListener('click', () => { panel.hidden = true; });
    document.addEventListener('keydown', event => { if (event.key === 'Escape') panel.hidden = true; });
    const fit = () => {
      const box = panel.getBoundingClientRect();
      panel.style.width = `${Math.min(box.width, window.innerWidth - 16)}px`;
      panel.style.height = `${Math.min(box.height, window.innerHeight - 16)}px`;
      panel.style.left = `${Math.max(8, Math.min(box.left, window.innerWidth - Math.min(box.width, window.innerWidth - 16) - 8))}px`;
      panel.style.top = `${Math.max(8, Math.min(box.top, window.innerHeight - Math.min(box.height, window.innerHeight - 16) - 8))}px`;
    };
    ['smaller','larger'].forEach(action => document.getElementById(`timing-window-${action}`).addEventListener('click', () => {
      const box = panel.getBoundingClientRect(), factor = action === 'larger' ? 1.2 : 1 / 1.2;
      panel.style.width = `${Math.max(360, box.width * factor)}px`;
      panel.style.height = `${Math.max(260, box.height * factor)}px`;
      fit();
    }));
    let drag;
    handle.addEventListener('pointerdown', event => {
      if (event.target.closest('button') || event.button !== 0) return;
      const box = panel.getBoundingClientRect();
      drag = {x:event.clientX, y:event.clientY, left:box.left, top:box.top};
      handle.setPointerCapture(event.pointerId);
    });
    handle.addEventListener('pointermove', event => {
      if (!drag) return;
      const box = panel.getBoundingClientRect();
      panel.style.left = `${Math.max(0, Math.min(window.innerWidth - box.width, drag.left + event.clientX - drag.x))}px`;
      panel.style.top = `${Math.max(0, Math.min(window.innerHeight - box.height, drag.top + event.clientY - drag.y))}px`;
    });
    const endDrag = () => { drag = null; };
    handle.addEventListener('pointerup', endDrag);
    handle.addEventListener('pointercancel', endDrag);
    window.addEventListener('resize', () => { if (!panel.hidden) fit(); });
    document.getElementById('timing-wave-mode').addEventListener('change', () => render(latest, selected));
    document.getElementById('timing-result').addEventListener('click', event => {
      const device = event.target.closest('[data-timing-device]');
      if (device) callbacks.inspect(device.dataset.timingDevice);
      if (event.target.closest('[data-timing-trip]')) {
        document.getElementById('timing-wave-mode').value = 'trip';
        open();
        render(latest, selected);
      }
    });
  }
  root.TimingView = {render, bind, waveform, gateRows, open, animate, SampleScroller};
  if (typeof module !== 'undefined') module.exports = root.TimingView;
})(typeof window !== 'undefined' ? window : globalThis);
