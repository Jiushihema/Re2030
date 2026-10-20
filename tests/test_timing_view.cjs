const {test} = require('node:test');
const assert = require('node:assert/strict');
const {waveform, gateRows} = require('../sim2030/presentation/static/timing-view.js');
const {SampleScroller} = require('../sim2030/presentation/static/timing-view.js');
const {protectionStatus} = require('../sim2030/presentation/static/timing-view.js');

test('reclosed breaker and cleared differential restore monitoring status and color', () => {
  // A completed trip may still exist in history; current state is healthy again.
  const state = {position:'closed', tripped:false};
  const status = protectionStatus(state, {valid:true, operate:false, id_a:0});
  assert.equal(status.text, '差动保护监测中');
  assert.equal(status.operated, false);
  // Even an inconsistent old trip marker cannot override a closed position.
  assert.equal(protectionStatus({...state, tripped:true}, {valid:true, operate:false}).text, status.text);
});

test('pending differential operation and completed trip remain distinguishable', () => {
  const pending = protectionStatus({position:'closed'}, {valid:true, operate:true});
  assert.match(pending.text, /等待断路器动作/);
  assert.equal(pending.operated,true);
  const tripped = protectionStatus({position:'open', tripped:true}, {valid:true, operate:false});
  assert.match(tripped.text, /断路器已跳闸/);
  assert.equal(tripped.operated,true);
  assert.equal(protectionStatus({position:'open'}, {valid:true, operate:false}).text, '差动保护监测中');
});

test('waveform preserves opposite polarity and displays the instantaneous sum', () => {
  const rows = [
    {local_time_us:0, i1:100, i2:-100, id:0},
    {local_time_us:250, i1:-100, i2:100, id:0},
  ];
  const svg = waveform(rows);
  assert.match(svg, /data-trace="i1" d="M48.0,24.0 L868.0,128.0"/);
  assert.match(svg, /data-trace="i2" d="M48.0,128.0 L868.0,24.0"/);
  assert.match(svg, /data-trace="id" d="M48.0,76.0 L868.0,76.0"/);
  assert.match(waveform([]), /推进仿真/);
});

test('gate display distinguishes bypassed, passed, failed and unimplemented', () => {
  const html = gateRows([
    {label:'minimum', enabled:true, implemented:true, passed:false, threshold:10},
    {label:'restraint', enabled:true, implemented:true, passed:true},
    {label:'disabled', enabled:false, implemented:true, passed:true},
    {label:'<reserved>', enabled:true, implemented:false, passed:null},
  ]);
  assert.match(html, /未满足/);
  assert.match(html, /class="timing-gate met"/);
  assert.match(html, /未启用，旁路/);
  assert.match(html, /预留，暂不参与判定/);
  assert.match(html, /&lt;reserved&gt;/);
});

test('live samples move left between model steps without predicting future data', () => {
  const rows = Array.from({length:800}, (_,i) => ({local_time_us:i*250, i1:100*Math.sin(i/80*2*Math.PI), i2:-100*Math.sin(i/80*2*Math.PI), id:0}));
  const player = new SampleScroller();
  player.update(rows);
  const before = waveform(player.visible(), {start:player.end-60000, end:player.end});
  player.tick(100, true, 1, 100000);
  const after = waveform(player.visible(), {start:player.end-60000, end:player.end});
  assert.notEqual(before.match(/data-trace="i1" d="([^"]+)/)[1], after.match(/data-trace="i1" d="([^"]+)/)[1]);
  const pausedAt = player.end;
  player.tick(100, false, 5, 100000);
  assert.equal(player.end, pausedAt);
  for(let i=0;i<30;i++) player.tick(200,true,5,100000);
  assert.equal(player.end, rows.at(-1).local_time_us);
  assert.ok(player.visible().every(row=>row.local_time_us <= player.target));
});

test('manual step displays the newest samples and a new scroller resets history', () => {
  const rows = [{local_time_us:100000},{local_time_us:200000}];
  const player = new SampleScroller();
  player.update(rows,true);
  assert.equal(player.end,200000);
  const next = new SampleScroller();
  assert.equal(next.end,null);
  assert.deepEqual(next.rows,[]);
});
