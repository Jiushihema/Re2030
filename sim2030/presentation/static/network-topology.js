/* 场景声明的交换式网络部署。业务关系、物理接入和旁路采集分别建模。 */
"use strict";

const NetworkTopology = (() => {
  const endpointId = (endpoint) => Array.isArray(endpoint) ? endpoint[0] : endpoint;
  const center = (p) => ({ x: p.x + 45, y: p.y + 24 });
  const pathData = (points) => points.map((p, i) => `${i ? "L" : "M"}${p.x},${p.y}`).join(" ");
  // 设备节点尺寸（与前端 NODE_SIZE 一致）：检测节点上报线的起点按它取右侧边中点。
  const NODE_WIDTH = 90;
  const NODE_HEIGHT = 48;
  /* 交换机接入端口：左侧边排列，上方设备与下方设备各最多分开显示三条线，
     装不下的接入并入该侧最下面那条（与相邻设备共用一条线）。 */
  const PORT_MARGIN = 12;
  const PORT_SPACING = 12;
  const MAX_LANES_PER_SIDE = 3;
  const PROTOCOL_ORDER = ["mms", "sv", "goose"];
  const ACCESS_STRAND_OFFSET = 1.8;

  function protocolsForConnection(network, connection, linkMap) {
    const found = new Set();
    for (const id of network.link_ids || []) {
      const link = linkMap.get(id);
      if (!link) continue;
      if (endpointId(link.endpoint_a) !== connection.device_id &&
          endpointId(link.endpoint_b) !== connection.device_id) continue;
      const protocol = String(link.parameters?.protocol || "").toLowerCase();
      if (protocol) found.add(protocol);
    }
    return [...PROTOCOL_ORDER.filter(protocol => found.has(protocol)),
      ...found].filter((protocol, index, all) => all.indexOf(protocol) === index);
  }

  function offsetAccessLine(points, offset) {
    if (points.length < 3) return points.map(point => ({ x: point.x + offset, y: point.y + offset }));
    // 接入线固定为“设备锚点 → 竖直走线 → 水平进交换机端口”，按协议并行平移成线束。
    const [start, bend, end] = points;
    return [
      { x: start.x + offset, y: start.y },
      { x: bend.x + offset, y: bend.y + offset },
      { x: end.x, y: end.y + offset },
    ];
  }

  function strandsFor(points, protocols) {
    if (protocols.length < 2) return null;
    const step = ACCESS_STRAND_OFFSET * 2;
    return protocols.map((protocol, index) => ({
      protocol,
      points: offsetAccessLine(points, -ACCESS_STRAND_OFFSET * (protocols.length - 1) + step * index),
    }));
  }

  function pointAt(points, progress) {
    if (!points.length) return { x: 0, y: 0 };
    const lengths = points.slice(1).map((p, i) => Math.hypot(p.x - points[i].x, p.y - points[i].y));
    let distance = lengths.reduce((sum, value) => sum + value, 0) * Math.max(0, Math.min(1, progress));
    for (let i = 0; i < lengths.length; i++) {
      if (distance <= lengths[i] && lengths[i] > 0) {
        const ratio = distance / lengths[i];
        return { x: points[i].x + (points[i + 1].x - points[i].x) * ratio,
          y: points[i].y + (points[i + 1].y - points[i].y) * ratio };
      }
      distance -= lengths[i];
    }
    return points[points.length - 1];
  }

  function build(topology, coordinates) {
    const deployment = topology.layout?.deployment;
    const networks = deployment?.networks || [];
    const routes = new Map();
    const cables = [];
    const assets = [];
    const covered = new Set();
    const interfaces = [];
    const links = topology.links || [];
    const linkMap = new Map(links.map(link => [link.link_id, link]));

    for (const network of networks) {
      const sw = { ...network.switch, kind: "switch", network_id: network.id };
      const probe = { ...network.probe, kind: "probe", network_id: network.id };
      assets.push(sw, probe);
      const access = new Map();
      // 上方设备与下方设备各最多分开显示 MAX_LANES_PER_SIDE 条线，且线条总数不超过左侧边容量。
      const capacity = Math.max(1, Math.floor((sw.height - 2 * PORT_MARGIN) / PORT_SPACING) + 1);
      const centerY = sw.y + sw.height / 2;
      const entries = network.connections.map((connection) => {
        const coord = coordinates[connection.device_id];
        if (!coord) return null;
        const anchor = { x: coord.x + connection.anchor_x, y: coord.y + connection.anchor_y };
        return { connection, anchor, side: anchor.y < centerY ? "above" : "below" };
      }).filter(Boolean);

      // 每侧按 x 升序：index 0 为离交换机最远的设备。
      const sides = {
        above: entries.filter((item) => item.side === "above").sort((a, b) => a.anchor.x - b.anchor.x),
        below: entries.filter((item) => item.side === "below").sort((a, b) => a.anchor.x - b.anchor.x),
      };
      const lanes = {
        above: Math.min(sides.above.length, MAX_LANES_PER_SIDE),
        below: Math.min(sides.below.length, MAX_LANES_PER_SIDE),
      };
      while (lanes.above + lanes.below > capacity) {
        if (lanes.above > lanes.below) lanes.above -= 1;
        else if (lanes.below > 0) lanes.below -= 1;
        else if (lanes.above > 0) lanes.above -= 1;
        else break;
      }

      const placements = [];
      for (const side of ["above", "below"]) {
        const group = sides[side];
        if (!group.length) continue;
        const laneCount = Math.max(1, Math.min(lanes[side], group.length));
        group.forEach((item, index) => {
          // 离交换机最远的设备优先独占一条线；靠得近的溢出接入并入该侧最外侧
          // 那条线（lane 0：上方为最上、下方为最下），与相邻设备共用同一条。
          const lane = index < laneCount ? laneCount - 1 - index : 0;
          placements.push({
            connection: item.connection,
            anchor: item.anchor,
            port: {
              x: sw.x,
              y: side === "above"
                ? sw.y + PORT_MARGIN + lane * PORT_SPACING
                : sw.y + sw.height - PORT_MARGIN - lane * PORT_SPACING,
            },
          });
        });
      }

      placements.forEach(({ connection, anchor, port }) => {
        const points = [anchor, { x: anchor.x, y: port.y }, port];
        const protocols = protocolsForConnection(network, connection, linkMap);
        const strands = strandsFor(points, protocols);
        access.set(connection.device_id, points);
        interfaces.push({ ...connection, network_id: network.id, anchor, port, protocols });
        cables.push({
          id: `${network.id}:${connection.switch_port}`,
          kind: network.id,
          points,
          protocols,
          ...(strands ? { strands } : {}),
          title: `${connection.device_id} / ${connection.interface_id} ↔ ${sw.name} / ${connection.switch_port}`
            + (protocols.length ? ` · ${protocols.map(protocol => protocol.toUpperCase()).join("+")}` : ""),
        });
      });
      for (const id of network.link_ids) {
        const link = linkMap.get(id);
        if (!link || link.link_type !== "wired") continue;
        const a = access.get(endpointId(link.endpoint_a));
        const b = access.get(endpointId(link.endpoint_b));
        if (!a || !b) continue;
        // 进入同一交换机的不同端口；不经另一张网，也不经 IDS。
        const middleX = sw.x + sw.width / 2;
        const points = [...a, { x: middleX, y: a[a.length - 1].y },
          { x: middleX, y: b[b.length - 1].y }, ...[...b].reverse()];
        routes.set(id, { points, network_id: network.id, link });
        covered.add(id);
      }
      const mirrorStart = { x: sw.x + sw.width, y: sw.y + sw.height / 2 };
      const mirrorEnd = { x: probe.x, y: probe.y + probe.height / 2 };
      cables.push({ id: `${network.id}:mirror`, kind: "mirror",
        points: [mirrorStart, { x: mirrorEnd.x - 20, y: mirrorStart.y },
          { x: mirrorEnd.x - 20, y: mirrorEnd.y }, mirrorEnd],
        title: `${sw.name} / ${network.mirror_port} → ${probe.name} / ${probe.capture_interface}（只接收镜像）` });
    }

    if (deployment?.manager) {
      const manager = { ...deployment.manager, kind: "manager" };
      assets.push(manager);
      networks.forEach((network, index) => {
        const probe = network.probe;
        const outsideX = manager.x + manager.width + 18 + index * 14;
        cables.push({ id: `${network.id}:management`, kind: "management",
          points: [{ x: probe.x + probe.width, y: probe.y + probe.height / 2 },
            { x: outsideX, y: probe.y + probe.height / 2 },
            { x: outsideX, y: manager.y + 20 + index * 18 },
            { x: manager.x + manager.width, y: manager.y + 20 + index * 18 }],
          title: `${probe.name} / ${probe.management_interface} → ${manager.name}（监测结果上报）` });
      });
      // 检测节点（如频谱/授时信号检测）的检测结论同样上报安全监测平台：
      // 与 IDS 管理口一样画成监测上报线。检测节点与平台同处上方监测区时，从节点顶边先
      // 上行再右接平台左侧边；位于过程层等下层区域时，先从节点顶边走到过程层上方的
      // 层间空档，再右移到设备区之外，最后上行进入平台，避免横穿其它设备。
      const clampToManager = (value) => Math.min(Math.max(value, manager.y + 12), manager.y + manager.height - 12);
      const processBand = (topology.layout?.layers || []).find(layer => layer.key === "process");
      const lowerCorridorY = processBand ? processBand.y - 30 : manager.y + manager.height + 40;
      for (const device of topology.devices || []) {
        if (device.device_type !== "spectrum_detector") continue;
        const coord = coordinates[device.device_id];
        if (!coord) continue;
        const inUpperRegion = coord.y <= manager.y + manager.height;
        const start = { x: coord.x + NODE_WIDTH / 2, y: coord.y };
        const end = { x: manager.x, y: clampToManager(start.y) };
        // 场景可用 layout.report_points 指定“从 IDS 连线右侧绕到平台”的走线；
        // 未声明时沿用通用路径，保证旧场景不受影响。
        const explicit = Array.isArray(device.layout?.report_points) ? device.layout.report_points : null;
        const points = explicit?.length
          ? explicit
          : (inUpperRegion
            ? [start, { x: start.x, y: end.y }, end]
            : [start, { x: start.x, y: lowerCorridorY },
              { x: end.x - 35, y: lowerCorridorY }, { x: end.x - 35, y: end.y }, end]);
        cables.push({ id: `${device.device_id}:report`, kind: "management", points,
          title: `${device.name || device.device_id} → ${manager.name}（检测结论上报）` });
      }
    }

    for (const link of links) {
      if (covered.has(link.link_id)) continue;
      const a = coordinates[endpointId(link.endpoint_a)];
      const b = coordinates[endpointId(link.endpoint_b)];
      if (!a || !b) continue;
      const direct = deployment?.direct_links?.[link.link_id];
      const points = direct?.points || [center(a), center(b)];
      const kind = direct?.role || link.link_type || "wired";
      routes.set(link.link_id, { points, link, network_id: null });
      cables.push({ id: link.link_id, kind, points,
        title: `${endpointId(link.endpoint_a)} ↔ ${endpointId(link.endpoint_b)} · ${direct?.label || kind}` });
    }

    // 网络攻击节点是设备而不是部署资产：用红色虚线标出它接入的交换机端口。
    const originId = topology.layout?.goose_lab?.attack_origin?.asset_id;
    const origin = originId ? coordinates[originId] : null;
    if (origin && !assets.some(asset => asset.id === originId)) {
      const target = networks.find(network =>
        (network.connections || []).some(connection => connection.device_id === originId))
        || networks.find(network => network.switch?.id === topology.layout?.goose_lab?.attack_origin?.switch_id);
      if (target) {
        const sw = target.switch;
        // 攻击节点排在交换机正下方：接线从节点顶边垂直上联到交换机底边。
        const x = origin.x + 45;
        const points = [{ x, y: origin.y }, { x, y: sw.y + sw.height }];
        cables.push({ id: `${target.id}:attack`, kind: "attack-access", points,
          title: `${originId} ↔ ${sw.name}（场景假定攻击者已接入过程网，注入攻击报文）` });
      }
    }

    return { networks, routes, cables, assets, interfaces, deployment,
      width: Math.max(0, ...assets.map(a => a.x + a.width + 55)),
      height: Math.max(0, ...assets.map(a => a.y + a.height + 25)) };
  }

  function messageRoute(model, message) {
    // 同一对设备可能有多个接口；首先按端口匹配，禁止跨网猜测路径。
    const matches = [...model.routes.values()].filter(({ link }) => {
      const a = link.endpoint_a, b = link.endpoint_b;
      return (a[0] === message.sender_id && b[0] === message.receiver_id) ||
        (b[0] === message.sender_id && a[0] === message.receiver_id);
    });
    const candidates = matches.filter(({ link }) => {
      const forward = link.endpoint_a[0] === message.sender_id;
      const source = forward ? link.endpoint_a : link.endpoint_b;
      const target = forward ? link.endpoint_b : link.endpoint_a;
      return (!message.source_port || source[1] === message.source_port) &&
        (!message.target_port || target[1] === message.target_port);
    });
    if (candidates.length !== 1) return null;
    const route = candidates[0];
    return route.link.endpoint_a[0] === message.sender_id ? route.points : [...route.points].reverse();
  }

  function eventMatches(event, probe) {
    const raw = event.raw || event;
    if ((event.source_type || raw.source_type) !== "ids") return false;
    if (probe.provider_id && (event.provider_id || raw.provider_id) !== probe.provider_id) return false;
    const interfaceId = raw.metadata?.interface_id;
    return interfaceId ? interfaceId === probe.interface_id : raw.source_id === probe.source_id;
  }

  function summarize(data, probe) {
    const events = (data.observations?.ids_events || data.observations?.events || []).filter(event => eventMatches(event, probe));
    const alerts = events.filter(event => {
      const raw = event.raw || event;
      return raw.data?.event_type === "alert" || raw.status === "anomaly";
    });
    return { events, alerts, status: alerts.length ? "alert" : events.length ? "receiving" : "unknown",
      label: alerts.length ? `${alerts.length} 条告警` : events.length ? "已收到事件" : "暂无采集数据" };
  }

  return { build, messageRoute, pathData, pointAt, eventMatches, summarize };
})();

if (typeof module !== "undefined") module.exports = NetworkTopology;

/* 网络攻击情景的只读展示模型。阶段选择不调用业务、攻击或观测接口。 */
"use strict";

class CyberDemo {
  constructor() {
    this.key = "";
    this.config = null;
    this.phase = 0;
  }

  sync(data) {
    const config = data.topology?.layout?.cyber_demo;
    const key = `${data.run_id || ""}:${JSON.stringify(config || null)}`;
    if (key === this.key) return false;
    this.key = key;
    this.config = config?.mode === "presentation_only" && Array.isArray(config.stages) && config.stages.length ? config : null;
    this.phase = 0;
    return true;
  }

  get active() { return Boolean(this.config); }
  get stage() { return this.config?.stages[this.phase] || null; }

  select(index) {
    if (!this.active || !Number.isInteger(index)) return;
    this.phase = Math.max(0, Math.min(this.config.stages.length - 1, index));
  }

  plan(network, coordinates) {
    if (!this.stage || !network) return { paths: [], marks: [] };
    const paths = [];
    for (const id of this.stage.routes || []) {
      const route = network.routes.get(id);
      if (route) paths.push({ id, kind: "command", points: route.points });
    }
    for (const id of this.stage.cables || []) {
      const cable = network.cables.find(item => item.id === id);
      if (cable) paths.push({ id, kind: "observation", points: cable.points });
    }
    const marks = (this.stage.marks || []).flatMap(mark => {
      const asset = network.assets.find(item => item.id === mark.id);
      const device = coordinates[mark.id];
      const box = asset || (device ? { ...device, width: 90, height: 48 } : null);
      return box ? [{ ...mark, x: box.x, y: box.y, width: box.width, height: box.height }] : [];
    });
    return { paths, marks };
  }
}

if (typeof module !== "undefined") module.exports.CyberDemo = CyberDemo;
