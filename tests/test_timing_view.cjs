const {test} = require('node:test');
const assert = require('node:assert/strict');
const {waveform, gateRows} = require('../sim2030/presentation/static/timing-view.js');

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
