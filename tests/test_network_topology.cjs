const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const topology = require('../sim2030/presentation/static/network-topology.js');
const scene = JSON.parse(fs.readFileSync(path.join(__dirname, '../scenarios/substation-live.json'), 'utf8'));
const coordinates = Object.fromEntries(scene.devices.map(d => [d.device_id, d.layout]));
const model = topology.build(scene, coordinates);

test('two isolated switches, two mirror feeds and independent reporting cables', () => {
  assert.equal(model.assets.filter(a => a.kind === 'switch').length, 2);
  assert.equal(model.cables.filter(c => c.kind === 'mirror').length, 2);
  assert.equal(model.cables.filter(c => c.kind === 'management').length, 2);
  assert.equal(model.cables.filter(c => c.kind === 'station').length, 4);
  assert.equal(model.cables.filter(c => c.kind === 'process').length, 9);
  assert.equal(model.routes.size, scene.links.length);
  assert.equal(model.cables.some(c => /gateway|bus/.test(c.id)), false);
  const rightEdge = Math.max(...scene.layout.layers.map(layer => layer.x + layer.width));
  const bands = Object.fromEntries(scene.layout.layers.map(layer => [layer.key, layer]));
  for (const network of model.networks) {
    assert.ok(network.switch.x > rightEdge, 'switch must sit outside all device layers');
    assert.ok(network.probe.x > network.switch.x + network.switch.width);
    const upper = bands[network.id === 'station' ? 'station' : 'bay'];
    const lower = bands[network.id === 'station' ? 'bay' : 'process'];
    for (const cable of model.cables.filter(c => c.kind === network.id)) {
      const [bend, port] = cable.points.slice(1);
      assert.equal(bend.y, port.y);
      assert.ok(bend.y > upper.y + upper.height && bend.y < lower.y,
        'horizontal access cables must stay between device layers');
    }
  }
});

test('live scenario access lines expose MMS/SV/GOOSE protocol sets', () => {
  const access = new Map(model.cables
    .filter(cable => cable.kind === 'station' || cable.kind === 'process')
    .map(cable => [cable.id, cable]));
  const expected = {
    'station:P1': ['mms'],
    'station:P2': ['mms'],
    'station:P3': ['mms'],
    'station:P4': ['mms'],
    'process:P1': ['sv', 'goose'],
    'process:P2': ['sv', 'goose'],
    'process:P3': ['sv', 'goose'],
    'process:P4': ['goose'],
    'process:P5': ['goose'],
    'process:P6': ['sv'],
    'process:P7': ['sv'],
    'process:P8': ['goose'],
    'process:P9': [],
  };
  assert.equal(access.size, Object.keys(expected).length);
  for (const [id, protocols] of Object.entries(expected)) {
    const cable = access.get(id);
    assert.ok(cable, `${id} 应存在`);
    assert.deepEqual(cable.protocols, protocols, `${id} 协议集合`);
    if (protocols.length > 1) {
      assert.equal(cable.strands.length, protocols.length, `${id} 应为双色线束`);
      for (const strand of cable.strands) {
        assert.ok(strand.points.some((point, index) =>
          point.x !== cable.points[index].x || point.y !== cable.points[index].y),
        `${id} 的 ${strand.protocol} 线束应有偏移`);
      }
    } else {
      assert.equal(cable.strands, undefined, `${id} 单协议不应拆线束`);
    }
  }
});

test('两套合并单元的量测都经过程网送达保护装置与测控装置', () => {
  for (const mu of ['mu', 'mu_2']) {
    for (const [receiver, targetPort] of [['mc', 'mu'], ['prot', 'in'], ['diff_prot', 'in']]) {
      const linkId = `L-${mu}-${receiver}`;
      const route = model.routes.get(linkId);
      assert.ok(route, `${linkId} 应存在`);
      assert.equal(route.network_id, 'process', `${linkId} 应经过过程网`);
      const points = topology.messageRoute(model, {
        sender_id: mu, receiver_id: receiver, source_port: 'out', target_port: targetPort,
      });
      assert.deepEqual(points, route.points, `${mu} → ${receiver} 应沿本链路唯一到达`);
    }
  }
});

test('差动保护装置经过程网收两套量测，并经站控网上报保护事件', () => {
  const inbound = ['L-mu-diff_prot', 'L-mu_2-diff_prot'];
  for (const linkId of inbound) {
    assert.equal(model.routes.get(linkId).network_id, 'process', `${linkId} 应经过过程网`);
  }
  const trip = model.routes.get('L-diff_prot-brk');
  assert.equal(trip.network_id, 'process');
  assert.deepEqual(trip.link.endpoint_b, ['ctl_brk', 'up']);
  const report = model.routes.get('L-diff_prot-station');
  assert.equal(report.network_id, 'station', '保护事件应经站控网上报');
  assert.deepEqual(report.link.endpoint_b, ['station', 'diff_prot']);
});

test('physical measurements, timing and hardwire controls never enter either switch', () => {
  const direct = ['L-ct-mu', 'L-vt-mu', 'L-oil-mu', 'L-time-mu', 'L-ct_current_2-mu_2',
    'L-time_svc_2-mu_2', 'L-ctl-brk-down', 'L-brk-tap'];
  for (const id of direct) {
    assert.equal(model.routes.get(id).network_id, null, id);
  }
  for (const [id, kind, from, to] of [
    ['L-time-mu', 'timing', 'time_svc', 'mu'],
    ['L-time_svc_2-mu_2', 'timing', 'time_svc_2', 'mu_2'],
    ['L-ct_current_2-mu_2', 'measurement', 'ct_current_2', 'mu_2'],
  ]) {
    assert.equal(model.cables.find(c => c.id === id).kind, kind, id);
    assert.deepEqual(model.routes.get(id).points, [from, to].map(device => ({
      x: coordinates[device].x + 45, y: coordinates[device].y + 24,
    })), `${id} must connect directly without intermediate bends`);
  }
});

test('packets enter only the intended switch and reverse along exactly the same cable', () => {
  const request = { sender_id: 'station', receiver_id: 'mc', source_port: 'mc_breaker', target_port: 'up' };
  const points = topology.messageRoute(model, request);
  assert.deepEqual(points, model.routes.get('L-station-mc').points);
  const station = model.networks.find(n => n.id === 'station');
  assert.ok(points.some(p => p.x === station.switch.x + station.switch.width / 2));
  assert.ok(points.every(p => p.y < 410 && p.x < station.probe.x));
  const reverse = topology.messageRoute(model, { sender_id: 'mc', receiver_id: 'station', source_port: 'up', target_port: 'mc_breaker' });
  assert.deepEqual(reverse, [...points].reverse());
  assert.equal(topology.messageRoute(model, { ...request, source_port: 'invalid' }), null);
});

test('two interfaces of the same IED have distinct physical attachment points', () => {
  for (const id of ['prot', 'mc']) {
    const ports = model.interfaces.filter(p => p.device_id === id);
    assert.equal(ports.length, 2);
    assert.notDeepEqual(ports[0].anchor, ports[1].anchor);
    assert.notEqual(ports[0].interface_id, ports[1].interface_id);
  }
});

test('same endpoints on multiple networks resolve by their ports', () => {
  const a = { endpoint_a: ['one', 'st'], endpoint_b: ['two', 'st'] };
  const b = { endpoint_a: ['one', 'pr'], endpoint_b: ['two', 'pr'] };
  const alternate = { routes: new Map([['a', { link: a, points: [{x:1,y:1}] }], ['b', {link:b,points:[{x:2,y:2}]}]]) };
  assert.deepEqual(topology.messageRoute(alternate, {sender_id:'one',receiver_id:'two',source_port:'pr',target_port:'pr'}), [{x:2,y:2}]);
  assert.equal(topology.messageRoute(alternate, {sender_id:'one',receiver_id:'two'}), null);
});

test('constant-speed interpolation follows bends and tolerates zero length segments', () => {
  const points = [{x:0,y:0},{x:30,y:0},{x:30,y:0},{x:30,y:70}];
  assert.deepEqual(topology.pointAt(points, .5), {x:30,y:20});
  assert.deepEqual(topology.pointAt(points, 0), points[0]);
  assert.deepEqual(topology.pointAt(points, 1), points[3]);
  assert.deepEqual(topology.pointAt([{x:5,y:6},{x:5,y:6}], .4), {x:5,y:6});
});

const idsEvent = (interfaceId, type = 'flow') => ({source_type:'ids', provider_id:'provider', event_id:'e1',
  raw:{source_id:'ids-station',metadata:{interface_id:interfaceId},data:{event_type:type},data_origin:'synthetic'}});

test('IDS status comes only from events on the configured capture interface', () => {
  const probe = model.networks[0].probe;
  assert.equal(topology.summarize({management:{station:{effects:{attack:true}}}},probe).status,'unknown');
  const data = {observations:{events:[idsEvent('span-station'),idsEvent('span-process','alert')]}};
  assert.equal(topology.summarize(data,probe).status,'receiving');
  assert.equal(topology.summarize(data,model.networks[1].probe).alerts.length,1);
  assert.equal(topology.eventMatches(idsEvent('unknown','alert'),probe),false);
  assert.equal(topology.eventMatches({...idsEvent('span-station'),source_type:'business'},probe),false);
});

test('replay IDS events and optional provider binding remain isolated', () => {
  const probe = {...model.networks[0].probe,provider_id:'provider'};
  assert.equal(topology.eventMatches({...idsEvent('span-station'),provider_id:'other'},probe),false);
  assert.equal(topology.summarize({observations:{ids_events:[idsEvent('span-station','alert')]}},probe).status,'alert');
  assert.equal(topology.summarize({observations:{events:[]}},probe).status,'unknown');
});

test('access ports sit on the switch left edge within its outline', () => {
  for (const filename of ['substation-live.json', 'substation-attack-demo.json',
    'substation-em-attack.json', 'substation-network-attack.json']) {
    const config = JSON.parse(fs.readFileSync(path.join(__dirname, '../scenarios', filename), 'utf8'));
    const layoutModel = topology.build(config, Object.fromEntries(config.devices.map(d => [d.device_id, d.layout])));
    for (const network of layoutModel.networks) {
      const sw = network.switch;
      const ports = layoutModel.interfaces.filter(p => p.network_id === network.id);
      assert.equal(ports.length, network.connections.length, `${filename}: ${network.id} 端口数应与接入设备一致`);
      for (const item of ports) {
        assert.equal(item.port.x, sw.x, `${filename}: ${item.device_id} 的接入端口应落在交换机左侧边`);
        assert.ok(item.port.y >= sw.y && item.port.y <= sw.y + sw.height,
          `${filename}: ${item.device_id} 的接入端口溢出了交换机轮廓`);
      }
    }
  }
});

test('上下两侧各最多三条分开的线，溢出的并入最外侧那条', () => {
  for (const network of model.networks) {
    const sw = network.switch;
    const centerY = sw.y + sw.height / 2;
    for (const [side, isAbove] of [['上方', true], ['下方', false]]) {
      const items = model.interfaces.filter(p => p.network_id === network.id
        && (p.anchor.y < centerY) === isAbove);
      const lanes = new Set(items.map(p => p.port.y));
      assert.ok(lanes.size <= 3, `${network.id} ${side}最多三条线，实际 ${lanes.size} 条`);
      // 共用同一条线的设备必须是离交换机最近的那批
      const farthest = items.reduce((max, p) => Math.max(max, Math.abs(p.anchor.x - (sw.x + sw.width / 2))), 0);
      if (lanes.size < items.length) {
        const shared = items.filter(p => items.filter(q => q.port.y === p.port.y).length > 1);
        assert.ok(shared.length >= 2, '超出线条数的接入应共用同一条线');
        for (const item of shared) {
          const distance = Math.abs(item.anchor.x - (sw.x + sw.width / 2));
          assert.ok(distance <= farthest, `${item.device_id} 不应挤占更远设备的线条`);
        }
      }
    }
  }
});

test('上方设备越靠近交换机线越靠上，下方设备越靠近交换机线越靠下', () => {
  for (const network of model.networks) {
    const sw = network.switch;
    const centerY = sw.y + sw.height / 2;
    const centerX = sw.x + sw.width / 2;
    for (const [isAbove, describe] of [[true, '上方'], [false, '下方']]) {
      const items = model.interfaces
        .filter(p => p.network_id === network.id && (p.anchor.y < centerY) === isAbove)
        .map(p => ({ ...p, distance: Math.abs(p.anchor.x - centerX) }))
        .sort((a, b) => a.distance - b.distance);
      // 独占线条的设备（同一 y 只出现一次）按距离由近到远，线位应逐条向外
      const owners = items.filter(p => items.filter(q => q.port.y === p.port.y).length === 1);
      for (let i = 1; i < owners.length; i++) {
        const nearer = owners[i - 1], farther = owners[i];
        assert.ok(nearer.distance <= farther.distance, `${describe}设备应按距离排序`);
        if (isAbove) {
          assert.ok(nearer.port.y < farther.port.y, `${network.id}: 越靠近交换机应越靠上`);
        } else {
          assert.ok(nearer.port.y > farther.port.y, `${network.id}: 越靠近交换机应越靠下`);
        }
      }
    }
  }
});

test('冷却接口与两台合并单元、风机状态采集装置共用最下面那条线', () => {
  const process = model.networks.find(n => n.id === 'process');
  const portY = id => model.interfaces.find(p => p.network_id === 'process' && p.device_id === id).port.y;
  assert.equal(portY('mu_2'), portY('mu'), '两套合并单元的接入线应共用同一条');
  assert.equal(portY('ctl_cool'), portY('mu'), '冷却接口的接入线也应并入这条线');
  assert.equal(portY('fan_condition_monitor'), portY('mu'), '风机状态采集装置的接入线也应并入这条线');
  assert.notEqual(portY('ctl_tap'), portY('ctl_cool'), '分接机构应独占一条线');
  assert.notEqual(portY('ctl_brk'), portY('ctl_tap'), '断路器终端与分接机构应各占一条线');
  assert.ok(portY('ctl_tap') > portY('ctl_brk') && portY('ctl_cool') > portY('ctl_tap'),
    '下方设备越靠近交换机线越靠下');
});

test('差动保护装置的过程网接入端口位于交换机从上往下第三条', () => {
  const process = model.networks.find(n => n.id === 'process');
  const left = model.interfaces
    .filter(p => p.network_id === 'process' && p.port.x === process.switch.x)
    .sort((a, b) => a.port.y - b.port.y);
  assert.deepEqual(left.slice(0, 3).map(p => p.device_id), ['diff_prot', 'mc', 'prot']);
});

test('接入端口号自上而下顺序编号，与画面一致', () => {
  const expected = {
    station: ['station', 'prot', 'mc', 'diff_prot'],
    process: ['diff_prot', 'mc', 'prot', 'ctl_brk', 'ctl_tap', 'mu', 'mu_2', 'ctl_cool', 'fan_condition_monitor'],
  };
  for (const [id, devices] of Object.entries(expected)) {
    const rows = model.interfaces.filter(p => p.network_id === id)
      .sort((a, b) => a.port.y - b.port.y || a.anchor.x - b.anchor.x);
    assert.deepEqual(rows.map(p => p.device_id), devices, `${id} 接入次序`);
    assert.deepEqual(rows.map(p => p.switch_port), devices.map((_, i) => `P${i + 1}`), `${id} 端口号`);
  }
});

test('接入走线不穿过其它设备的竖线', () => {
  for (const network of model.networks) {
    const items = model.interfaces.filter(p => p.network_id === network.id);
    for (const a of items) {
      for (const b of items) {
        if (b.anchor.x <= a.anchor.x) continue;         // 只看 a 右侧设备的竖线
        const low = Math.min(b.anchor.y, b.port.y), high = Math.max(b.anchor.y, b.port.y);
        assert.ok(!(a.port.y > low && a.port.y < high),
          `${network.id}: ${a.device_id} 的横线穿过了 ${b.device_id} 的竖线`);
      }
    }
  }
});

test('检测节点把检测结论上报安全监测平台', () => {
  for (const [filename, detectorId] of [['substation-time-attack.json', 'time_detect_node'],
    ['substation-em-attack.json', 'spectrum_detect_node'],
    ['substation-sensor-attack.json', 'spectrum_detect_node']]) {
    const scene = JSON.parse(fs.readFileSync(path.join(__dirname, '../scenarios', filename), 'utf8'));
    const coords = Object.fromEntries(scene.devices.map(d => [d.device_id, d.layout]));
    const model = topology.build(scene, coords);
    const detector = scene.devices.find(d => d.device_id === detectorId);
    const cable = model.cables.find(c => c.id === `${detectorId}:report`);
    assert.ok(cable, `${filename}: 检测节点应有上报线`);
    assert.equal(cable.kind, 'management');

    // 与平台同处上方监测区时从顶边中点出发，否则从右侧边中点出发。
    const start = cable.points[0];
    const fromTopEdge = start.x === detector.layout.x + 45 && start.y === detector.layout.y;
    const fromRightEdge = start.x === detector.layout.x + 90 && start.y === detector.layout.y + 24;
    assert.ok(fromTopEdge || fromRightEdge,
      `${filename}: 上报线应从检测节点顶边中点或右侧边中点出发，实际 ${JSON.stringify(start)}`);

    const manager = scene.layout.deployment.manager;
    assert.ok([manager.x, manager.x + manager.width].includes(cable.points.at(-1).x),
      `${filename}: 上报线应接到安全监测平台左侧边或右侧边`);
    assert.ok(cable.points.at(-1).y >= manager.y && cable.points.at(-1).y <= manager.y + manager.height,
      `${filename}: 上报线应落在平台轮廓内`);
    // 上报线不得穿过攻击节点或检测节点自身的方框。
    for (const other of scene.devices) {
      if (other.device_id === detectorId) continue;
      const box = { x: other.layout.x, y: other.layout.y, right: other.layout.x + 90, bottom: other.layout.y + 48 };
      for (let i = 1; i < cable.points.length; i += 1) {
        const a = cable.points[i - 1], b = cable.points[i];
        const lowX = Math.min(a.x, b.x), highX = Math.max(a.x, b.x);
        const lowY = Math.min(a.y, b.y), highY = Math.max(a.y, b.y);
        const overlaps = lowX < box.right && highX > box.x && lowY < box.bottom && highY > box.y;
        assert.ok(!overlaps, `${filename}: 上报线不应穿过 ${other.device_id} 的方框`);
      }
    }
  }
});

test('old scenarios still render original physical links with no invented IDS', () => {
  const legacy = topology.build({...scene,layout:{}},coordinates);
  assert.equal(legacy.assets.length,0);
  assert.equal(legacy.cables.length,scene.links.length);
  assert.equal(legacy.routes.size,scene.links.length);
});

test('the network attack node is a red device cabled to its access switch', () => {
  const lab = JSON.parse(fs.readFileSync(path.join(__dirname, '../scenarios/substation-goose-lab.json'), 'utf8'));
  const coords = Object.fromEntries(lab.devices.map(d => [d.device_id, d.layout]));
  const origin = lab.layout.goose_lab.attack_origin;
  const model = topology.build(lab, coords);
  const node = lab.devices.find(d => d.device_id === origin.asset_id);
  assert.equal(node.device_type, 'network_attack_source');
  assert.equal(node.parameters.access_switch_id, origin.switch_id);
  // 攻击节点是设备，不是部署资产：它不能被当成交换机渲染。
  assert.equal(model.assets.some(a => a.id === origin.asset_id), false);
  const cable = model.cables.find(c => c.id === `${origin.switch_id.replace(/^sw-/, '')}:attack`);
  assert.equal(cable.kind, 'attack-access');
  const sw = model.networks.find(n => n.switch.id === origin.switch_id).switch;
  assert.deepEqual(cable.points, [{ x: coords[origin.asset_id].x + 45, y: coords[origin.asset_id].y },
    { x: coords[origin.asset_id].x + 45, y: sw.y + sw.height }]);
  assert.ok(cable.points[1].y < cable.points[0].y, 'attack cable must lead down from the switch');
  // 攻击接入不能挤掉既有的接入线与业务路由。
  assert.equal(model.cables.filter(c => c.kind === 'process').length, 9);
  assert.equal(model.routes.size, lab.links.length);
});

test('all scenario layouts keep devices in their layers and IDS mirror ports aligned', () => {
  for (const filename of ['substation-live.json', 'substation-attack-demo.json', 'substation-em-attack.json', 'substation-network-attack.json', 'substation-sensor-attack.json']) {
    const config = JSON.parse(fs.readFileSync(path.join(__dirname, '../scenarios', filename), 'utf8'));
    const bands = new Map(config.layout.layers.map(b => [b.key, b]));
    for (const device of config.devices) {
      const band = bands.get(device.layer), p = device.layout;
      if (filename === 'substation-sensor-attack.json' && device.device_id === 'spectrum_detect_node') {
        // 传感器攻击的检测节点按需求放在攻击节点右侧，允许超过程序层右边界。
        assert.ok(p.x > band.x + band.width, `${filename}: detection node should sit outside the process band`);
        assert.ok(p.y >= band.y + 28 && p.y + 48 <= band.y + band.height, `${filename}: detection node vertical containment`);
        continue;
      }
      assert.ok(p.x >= band.x && p.x + 90 <= band.x + band.width, `${filename}: ${device.device_id} horizontal containment`);
      assert.ok(p.y >= band.y + 28 && p.y + 48 <= band.y + band.height, `${filename}: ${device.device_id} must clear the layer title`);
    }
    for (const network of config.layout.deployment.networks) {
      assert.equal(network.switch.y + network.switch.height / 2, network.probe.y + network.probe.height / 2);
      assert.ok(network.switch.x > Math.max(...config.layout.layers.map(b => b.x + b.width)));
      const manager = config.layout.deployment.manager;
      assert.ok(manager.y + manager.height < network.probe.y, 'monitoring platform must sit above both IDS probes');
    }
    const layoutModel = topology.build(config, Object.fromEntries(config.devices.map(d => [d.device_id, d.layout])));
    for (const cable of layoutModel.cables.filter(c => c.kind === 'management')) {
      assert.ok(cable.points[0].y > cable.points.at(-1).y, 'IDS reporting must lead upward to the platform');
    }
    const time = config.devices.find(d => d.device_id === 'time_svc');
    const mu = config.devices.find(d => d.device_id === 'mu');
    assert.equal(time.layout.x, mu.layout.x, 'timing cable must remain vertical');

    if (filename === 'substation-sensor-attack.json') {
      const fanSensorIds = ['fan_vibration', 'fan_current', 'fan_speed'];
      const fanSensors = fanSensorIds.map(id => config.devices.find(d => d.device_id === id));
      assert.ok(fanSensors.every(d => d.layout.x === 1005), 'fan sensors must form one vertical column');
      assert.deepEqual(fanSensors.map(d => d.layout.y), [588, 658, 728]);
      const monitor = config.devices.find(d => d.device_id === 'fan_condition_monitor');
      assert.deepEqual([monitor.layout.x, monitor.layout.y], [1140, 658],
        'fan monitor must sit beside the middle fan sensor');
      assert.equal(config.devices.find(d => d.device_id === 'mu').layout.x,
        config.devices.find(d => d.device_id === 'time_svc').layout.x,
        'merging unit must align with its timing source');
      const attack = config.devices.find(d => d.device_id === 'sensor_attack_node');
      assert.equal(attack.parameters.target_asset_id, 'fan_vibration',
        'sensor attack must target the vibration sensor');
      assert.deepEqual([attack.layout.x, attack.layout.y], [1140, 588],
        'attack source must sit right of the vibration sensor');
      const detector = config.devices.find(d => d.device_id === 'spectrum_detect_node');
      assert.deepEqual([detector.layout.x, detector.layout.y, detector.layer], [1260, 588, 'process'],
        'sensor detection node must sit right of the attack node, outside the process band');
      assert.deepEqual(config.layout.deployment.direct_links['L-sensor-fan_vibration'].points,
        [{ x: 1140, y: 612 }, { x: 1095, y: 612 }],
        'attack interference path must connect the attack node to the vibration sensor');
    }
  }
});
