/* 仅拦截新增标签页；公共 app.js 和原拓扑的源文件保持原样。 */
"use strict";
(() => {
  const originalRender = renderOtherView;
  let axis = 0, busy = false, latest = null, message = "", showCurrent = false;
  const colors = ["#45d4c2", "#64a8ff", "#eda7f1"];
  const assetNames = {station:'站控',mc:'测控',ctl_cool:'冷却控制',cool:'风机'};
  const fmt = (v, n=2) => Number.isFinite(v) ? v.toFixed(n) : "—";
  const safe = value => String(value ?? "").replace(/[&<>"']/g, c => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]));

  function chart(series, xLabel, yLabel, threshold=null) {
    const points = series.flatMap(s => s.points).filter(p => Number.isFinite(p[0]) && Number.isFinite(p[1]));
    if (!points.length) return '<p class="acoustic-note">推进至少 1 秒后显示曲线。</p>';
    let xmin=Math.min(...points.map(p=>p[0])), xmax=Math.max(...points.map(p=>p[0]));
    let ymin=Math.min(...points.map(p=>p[1])), ymax=Math.max(...points.map(p=>p[1]));
    if(threshold!==null){ymin=Math.min(ymin,threshold);ymax=Math.max(ymax,threshold);}
    if(xmax===xmin)xmax=xmin+1;
    const pad=Math.max((ymax-ymin)*.12, Math.abs(ymax)*.001, .001);
    ymin-=pad; ymax+=pad;
    const span=ymax-ymin;
    const tick=v=>span<.001?(Math.abs(v)<1e-12?'0':v.toExponential(1)):fmt(v,span<1?3:span<10?2:0);
    const x=v=>55+485*(v-xmin)/(xmax-xmin), y=v=>148-125*(v-ymin)/(ymax-ymin);
    const path=s=>s.points.filter(p=>Number.isFinite(p[1])).map((p,i)=>`${i?'L':'M'}${x(p[0]).toFixed(2)},${y(p[1]).toFixed(2)}`).join(' ');
    let svg='<svg class="acoustic-chart" viewBox="0 0 560 190" role="img" aria-label="'+safe(yLabel)+'">';
    for(let i=0;i<4;i++){const v=ymin+span*i/3;svg+=`<line x1="55" x2="540" y1="${y(v)}" y2="${y(v)}" stroke="#263b4d"/><text x="49" y="${y(v)+4}" fill="#93acc2" font-size="10" text-anchor="end">${tick(v)}</text>`;}
    if(threshold!==null)svg+=`<line x1="55" x2="540" y1="${y(threshold)}" y2="${y(threshold)}" stroke="#f3bd62" stroke-dasharray="5 4"/><text x="538" y="${y(threshold)-5}" fill="#f3bd62" font-size="10" text-anchor="end">阈值 ${threshold}</text>`;
    svg+=series.map(s=>`<path d="${path(s)}" fill="none" stroke="${s.color}" stroke-width="1.6"${s.dash?' stroke-dasharray="5 4"':''}/>`).join('');
    svg+=`<text x="55" y="166" fill="#93acc2" font-size="10">${fmt(xmin,xmax-xmin<1?3:1)}</text><text x="540" y="166" text-anchor="end" fill="#93acc2" font-size="10">${fmt(xmax,xmax-xmin<1?3:1)}</text><text x="290" y="184" text-anchor="middle" fill="#b9cada" font-size="11">${safe(xLabel)}</text><text x="55" y="12" fill="#b9cada" font-size="11">${safe(yLabel)}</text></svg>`;
    return svg+'<div class="acoustic-legend">'+series.map(s=>`<span><i style="background:${s.color}"></i>${safe(s.name)}</span>`).join('')+'</div>';
  }

  const labels = {start_weak:"开启原样本幅度",start_strong:"开启增强仿真",stop_attack:"关闭虚拟声波",reset_protection:"复位保护",threshold_trip:"连续超限，锁定保护",stop_command_issued:"站控发出停机指令",measurement_received:"站控收到窗口读数",message_delivered:"业务消息送达"};
  function render(data) {
    latest = data;
    const root = document.querySelector('#view-content');
    if(!data?.available){root.innerHTML='<div class="acoustic-page"><h2>声波干扰联动演示</h2><p>先载入声波场景，再观察传感器和控制路径。</p><button class="primary" id="acoustic-load">载入声波场景</button></div>';document.querySelector('#acoustic-load').onclick=loadCase;return;}
    const preview = !showCurrent && !data.source_gain && data.attack_preview;
    const h=data.history||[], wave=(preview||data).wave||[];
    const sourceWave=(preview||data).source_wave||[], sourceStart=sourceWave[0]?.[0]||0;
    const start=wave[0]?.[0]||0;
    // 只减去当前展示窗口的基线均值，保留蓝/橙曲线之间的真实差值。
    const mean = wave.length?wave.reduce((sum,r)=>sum+r[2+axis],0)/wave.length:0;
    const waveSeries=[{name:'机械基线（无声波，去窗口均值）',color:'#64a8ff',points:wave.map(r=>[(r[0]-start)*1000,r[2+axis]-mean])},
      {name:'受扰读数（同一个均值）',color:'#ffbc74',points:wave.map(r=>[(r[0]-start)*1000,r[8+axis]-mean])}];
    const events=(data.events||[]).filter(e=>e.kind!=='measurement_received').slice(-30);
    root.innerHTML=`<div class="acoustic-page">
      <h2>声波干扰 → 读数超限 → 停机 → 散热减弱</h2>
      <p class="acoustic-note">本地软件联动 / 读数截至 ${fmt(data.model_time_s,1)} 秒</p>
      <div class="acoustic-warning">软件仿真，尚未验证真实设备。原样本幅度通常不触发停机；增强档用于演示控制联动。日志来自本次仿真消息投递，并非真实 IDS 抓包。</div>
      <div class="acoustic-warning" id="acoustic-thermal-mode">油温为加速演示：热惯性压缩 ${fmt(data.thermal_time_scale,0)} 倍，自然散热时间常数 ${fmt(data.natural_tau_s,0)} 秒；原尺度为 ${fmt(data.original_natural_tau_s,0)} 秒。风机、声波和通信时间不加速，不代表真实升温速度。</div>
      <div class="acoustic-actions"><button id="acoustic-new">新建正常对照</button><button data-acoustic="start_weak">原样本幅度 κ=0.30</button><button class="danger" data-acoustic="start_strong">增强仿真 κ=0.90</button><button data-acoustic="stop_attack">关闭干扰</button><button data-acoustic="reset_protection">复位保护</button><button id="acoustic-restart">启动风机</button><button class="primary" data-advance="1">推进 1 秒</button><button class="primary" data-advance="10">推进 10 秒</button><button data-advance="60">推进 60 秒</button></div>
      <div id="acoustic-message" role="status">${safe(message)}</div>
      <div class="acoustic-stats">
       <div class="acoustic-stat">虚拟声波<strong>${data.source_gain?'κ='+fmt(data.source_gain):'关闭'}</strong>三轴权重 1 / 0.6 / 0.3（假设）</div>
       <div class="acoustic-stat">电流 / 转速<strong>${fmt(data.current_a)} A</strong>${fmt(data.speed_rpm,0)} rpm · ${data.powered?'仍通电':'已断电，可能仍惯性转动'}</div>
       <div class="acoustic-stat">油温读数（加速演示）<strong>${fmt(data.oil_c,3)} ℃</strong>原尺度对照：${fmt(data.oil_original_c,3)} ℃<br>传感器采样时刻：${fmt(data.oil_sample_time_s,1)} s</div>
       <div class="acoustic-stat">保护判据<strong>${data.trip_latched?'已锁定':data.consecutive+' / 3 窗口'}</strong>${data.cutoff_s===null?'尚未断电':'实际执行断电：'+fmt(data.cutoff_s,1)+' s'}</div>
      </div>
      <div class="acoustic-flow"><span>XYZ 读数</span>→<span>采集装置</span>→<span>测控转报状态</span>→<span>站控设备内判定并发令</span>→<span>测控 → 冷却控制 → 风机</span></div>
      <div class="acoustic-grid">
       <section class="acoustic-card"><h3>1. 虚拟声波输入（4 ms）</h3><p class="acoustic-note">${preview?'保存的受扰片段：'+fmt(preview.time_s,1)+' 秒，κ='+fmt(preview.gain):'当前输入片段'}</p>${chart([{name:'归一化等效激励 u(t)，不是 Pa / dB',color:'#45d4c2',points:sourceWave.map(r=>[(r[0]-sourceStart)*1000,r[1]])}],'窗口内时间 / ms','归一化幅值')}<p class="acoustic-note">载波 5500 Hz，此图以 88000 Hz 展示数学输入；模型输出才以 4000 Hz 读取。没有真实播放声音。</p></section>
       <section class="acoustic-card"><h3>2. 正常 / 受扰振动 · ${'XYZ'[axis]} 轴</h3><div class="acoustic-axis">${[0,1,2].map(i=>`<button data-axis="${i}" class="${axis===i?'active':''}">${'XYZ'[i]}</button>`).join('')}<button id="acoustic-wave-toggle">${showCurrent?'显示最近受扰片段':'显示当前片段'}</button></div>${chart(waveSeries,'窗口内时间 / ms','原始计数 count')}<p class="acoustic-note">${preview?'保存的受扰片段：'+fmt(preview.time_s,1)+' 秒。':'当前片段。'}读数 = 机械基线 + 三轴增量。降速时基线幅值按转速比例衰减（假设）。</p></section>
       <section class="acoustic-card"><h3>3. 三轴异常判据（每个窗口 1 秒）</h3>${chart([0,1,2].map(i=>({name:'XYZ'[i]+' 轴',color:colors[i],points:h.map(r=>[r.time_s,r.rms_ratio_xyz[i]])})),'仿真时间 / s','去均值 RMS / 正常基准',data.rule.threshold_ratio)}<p class="acoustic-note">任一轴超过 ${data.rule.threshold_ratio} 倍，连续 ${data.rule.consecutive_windows} 个完整窗口，站控设备等待 ${data.rule.delay_s} 秒后在自身 step() 中发命令。规则与锁定状态归属于站控节点，不由应用层代发。</p></section>
       <section class="acoustic-card"><h3>4. 电流与惯性转速（各除以额定值）</h3>${chart([{name:'电流 / 2 A',color:'#45d4c2',points:h.map(r=>[r.time_s,r.current_a/2])},{name:'转速 / 1500 rpm',color:'#ffbc74',points:h.map(r=>[r.time_s,r.speed_rpm/1500])}],'仿真时间 / s','额定值比例')}<p class="acoustic-note">电流读数时间常数 0.25 秒，转速时间常数 5 秒，均为演示假设。停机不会让叶片瞬间静止。</p></section>
       <section class="acoustic-card full"><h3>5. 油温 · 加速演示 / 原尺度对照</h3>${chart([{name:'加速油温读数（5 秒滞后）',color:'#ffbc74',points:h.map(r=>[r.time_s,r.oil_c])},{name:'加速模型油温',color:'#64a8ff',points:h.map(r=>[r.time_s,r.oil_reference_c])},{name:'原尺度模型读数（未实测标定）',color:'#b8c6d4',dash:true,points:h.map(r=>[r.time_s,r.oil_original_c])}],'仿真时间 / s（仅热模型加速）','油温 / ℃')}<p class="acoustic-note">C有效=C参考/${fmt(data.thermal_time_scale,0)}；自然散热时间常数从 ${fmt(data.original_natural_tau_s,0)} 秒压缩到 ${fmt(data.natural_tau_s,0)} 秒，便于现场展示。灰色虚线使用原热容、同一停机过程作对照。${fmt(data.thermal_time_scale,0)} 倍只改变热惯性，不是把整个系统的时钟放快。</p><p class="acoustic-note">C·dT/dt = P损耗 − (G自然 + G风冷·转速比例)·(T−环境温度)。风机停下后自然散热仍存在。快慢两套参数都未实测标定，曲线不能用于推断真实设备升温速度。</p></section>
       <section class="acoustic-card full"><h3>6. 本次运行的控制路径日志</h3><p class="acoustic-note">按时间先后排列。接收时间来自 0.1 秒步长的仿真时钟；不是现场网络延迟。相同 request_id 串起命令和回执。“accepted”是受理，“completed”才是执行完成。</p><div class="acoustic-table-wrap"><table><thead><tr><th>接收/事件时间 s</th><th>事件 / 路径</th><th>动作 / 结果</th><th>关联编号 request_id</th></tr></thead><tbody>${events.map(e=>`<tr><td>${fmt(e.observed_time_s,1)}</td><td>${safe(labels[e.kind]||e.kind)}${e.src?' · '+safe(assetNames[e.src]||e.src)+' → '+safe(assetNames[e.dst]||e.dst):''}</td><td>${safe(e.payload?.action_type||e.payload?.status||'')}${e.payload?.status&&e.payload.action_type?' / '+safe(e.payload.status):''}</td><td>${safe(e.request_id||'—')}</td></tr>`).join('')}</tbody></table></div></section>
      </div><details><summary>来源、数学依据和使用边界</summary><p>拓扑基于 main 的 ${safe(data.template?.git_commit?.slice(0,7)||'未记录')}；保留两套合并单元、差动保护和协议分组，另加风机状态上送链路。两套业务量测与风机状态分开转报。</p><p>正常片段：TI 风机数据集 Normal，第一个原始文件的前 4 秒，循环回放；不是变电站风机现场数据。原始传感器为 ADXL355，数值为 count。完整文件名和 SHA256 保存在 acoustic_demo/data/provenance.json。</p><p>响应参考：ADI ADXL356 X 轴示例模型，f₀=5500 Hz、Q=17、低通=1500 Hz。采用连续域解析解，再按 4000 Hz 读取，开关使用 20 ms 平滑包络。XYZ 使用同一个响应和不同权重，尚未标定三轴真实耦合。</p><p>κ=0.30 复用旧 S 样本的幅度设定，κ=0.90 是新增的仿真放大，并非真实声压。数学一致性可以检查；设备易感性和真实停机必须另做台架验证。新建正常对照会新建运行，不会删除上一轮日志。</p><p>记录目录：runs/${safe(data.run_id)}/，acoustic_meta.jsonl 为来源、参数和热加速系数，acoustic_series.jsonl 保存加速油温与原尺度对照，acoustic_path.jsonl 保存节点判据与消息投递；原底座 business.jsonl 继续保留。</p></details>
    </div>`;
    root.querySelector('#acoustic-new').onclick=loadCase;
    root.querySelectorAll('[data-axis]').forEach(b=>b.onclick=()=>{axis=Number(b.dataset.axis);render(latest);});
    root.querySelectorAll('[data-acoustic]').forEach(b=>b.onclick=()=>operate(b.dataset.acoustic));
    root.querySelectorAll('[data-advance]').forEach(b=>b.onclick=()=>advance(Number(b.dataset.advance)));
    root.querySelector('#acoustic-restart').onclick=()=>operate('start','cool');
    root.querySelector('#acoustic-wave-toggle').onclick=()=>{showCurrent=!showCurrent;render(latest);};
    root.querySelectorAll('button').forEach(b=>{b.disabled=busy;});
  }
  async function loadCase(){
    const scenario=state.scenarios.find(s=>s.scenario_id==='substation-acoustic-demo');
    if(!scenario)return;
    state.selectedScenario=scenario.scenario_id;
    document.querySelector(`[data-scenario-id="${scenario.scenario_id}"]`)?.click();
    message=''; showCurrent=false;
    await createRun();
    await refreshViews();
  }
  async function operate(action,target='sensor_attack_node'){
    try{const r=await api('POST',`/api/runs/${state.runId}/operations`,{target_asset_id:target,action_type:action});message=r.reason||`${labels[action]||action}：${r.status}`;await refreshViews();}
    catch(e){message=e.message;if(latest)render(latest);}
  }
  async function advance(seconds){
    if(busy)return;
    busy=true; message=`正在推进 ${seconds} 秒…`;render(latest);
    const runId=state.runId;
    try{
      if(simulationPlayer.status==='running'){
        if(!await simulationPlayer.command('pause'))throw new Error('请先暂停自动推演');
      }
      for(let elapsed=0;elapsed<seconds;elapsed+=5){
        if(state.runId!==runId)break;
        const r=await api('POST','/api/acoustic/advance',{run_id:runId,seconds:Math.min(5,seconds-elapsed)});
        if(state.runId!==runId)break;
        message=`已推进 ${Math.min(elapsed+5,seconds)} / ${seconds} 秒`;
        if(state.currentView==='acoustic')render(r.data);
      }
      await refreshViews();
    }catch(e){message=e.message;}
    finally{busy=false;if(state.currentView==='acoustic'&&latest)render(latest);}
  }
  renderOtherView=function(view,data){if(view==='acoustic')return render(data);return originalRender(view,data);};
  const originalKind=nodeKindClass, originalText=getDeviceDynamicText;
  const originalEmitting=isAttackNodeEmitting, originalTampered=isSensorTampered;
  nodeKindClass=function(type){return type==='acoustic_source'?'node-kind-attack':originalKind(type);};
  isAttackNodeEmitting=function(mgmt,id){return mgmt[id]?.device_type==='acoustic_source'?!!mgmt[id].state.emitting:originalEmitting(mgmt,id);};
  getDeviceDynamicText=function(id,mgmt,env){
    if(mgmt.fan_vibration?.device_type==='acoustic_sensor'){
      const s=mgmt[id]?.state||{};
      if(id==='fan_vibration')return s.last_value==null?'[待满 1 秒窗口]':`[RMS ${fmt(s.last_value)} 倍]`;
      if(id==='fan_current')return `[${fmt(s.last_value)} A]`;
      if(id==='fan_speed')return `[${fmt(s.last_value,0)} rpm]`;
      if(id==='sensor_attack_node')return s.emitting?`[虚拟声波 κ=${fmt(s.normalized_gain)}]`:'[虚拟声波关闭]';
      if(id==='cool')return s.running?'[风机通电]':`[已断电 ${fmt(s.rotation_speed_rpm,0)}rpm]`;
    }
    return originalText(id,mgmt,env);
  };
  isSensorTampered=function(id,mgmt,env){
    if(id==='oil_temp'&&mgmt.tap?.device_type==='acoustic_transformer')return false; // 正常测量滞后不能当作攻击。
    return originalTampered(id,mgmt,env);
  };
  document.querySelector('[data-view="acoustic"]').addEventListener('click',()=>{
    if(!state.runId)render({available:false});
  });
  document.querySelector('[data-view="acoustic"]').click();
})();
