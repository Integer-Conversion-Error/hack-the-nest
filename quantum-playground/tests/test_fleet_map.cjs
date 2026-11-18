const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const FleetReplay = require('../web/fleet.js');

class CanvasPath {
  segments = 0;
  moveTo() {}
  lineTo() { this.segments += 1; }
}

function createContext(overrides) {
  const methods = 'setTransform drawImage setLineDash fillRect beginPath roundRect stroke fill arc moveTo lineTo save restore translate rotate fillText';
  return Object.assign(Object.fromEntries(methods.split(' ').map(name => [name, () => {}])), {
    measureText: text => ({ width: text.length * 5 }),
  }, overrides);
}

function testMap(grid, viewport, depot, count, chargers = [{ id: 'C3', position: [grid.width - 2, grid.height - 2] }],
  motionAllowed = () => true, depots = null) {
  const vans = Array.from({ length: count }, (_, index) => {
    const home = depots?.[index % depots.length] ?? { position: depot };
    return {
    id: `V${index + 1}`,
    ...(depots ? { depot_id: home.id } : {}),
    initial_battery_kwh: 4,
    events: [{
      type: 'drive', position: home.position, path: [home.position, [home.position[0] + 1, home.position[1]]],
      start_min: index, end_min: index + 2, battery_start_kwh: 4, battery_end_kwh: 3.8,
    }],
    };
  });
  const simulation = {
    kind: 'ev_fleet', grid, ...(depots ? { depots } : { depot }), vans,
    deliveries: [{ id: 'D1', position: [0, grid.height - 1] }],
    chargers,
    start_hour: 8, duration_min: 120, battery_capacity_kwh: 20, reserve_pct: 15,
  };
  assert.equal(FleetReplay.validate(simulation), true);

  const bodies = [];
  const labels = new Map();
  const routeStrokes = [];
  const streetPaths = [];
  const listeners = new Map();
  const inspected = [];
  const circles = [];
  const context = createContext({
    translate(x, y) { bodies.push([x, y]); },
    fillText(text, x, y) { labels.set(text, [x, y]); },
    stroke(route) { if (route instanceof CanvasPath) routeStrokes.push(this.strokeStyle); },
    arc(...args) { circles.push(args); },
  });
  const canvas = {
    getContext: () => context,
    getBoundingClientRect: () => viewport,
    ownerDocument: { createElement: () => ({ getContext: () => createContext({
      stroke(path) { if (path instanceof CanvasPath) streetPaths.push(path.segments); },
    }) }) },
    addEventListener: (type, listener) => listeners.set(type, listener),
  };
  const browser = vm.createContext({ Path2D: CanvasPath, FleetReplay });
  vm.runInContext(fs.readFileSync(path.resolve(__dirname, '../web/fleet-map.js'), 'utf8'), browser);
  const colors = Array.from({ length: count }, (_, index) => `#00000${index}`);
  const map = browser.FleetMap.create(canvas, { colors, onInspect: hit => inspected.push(hit), motionAllowed });
  map.setSimulation(simulation);
  map.draw(0, vans[0].id);
  if (depots) depots.forEach(home => assert.ok(labels.has(home.id), 'draw every home-depot label'));

  assert.equal(streetPaths[0], grid.width + grid.height, 'draw every street for this grid');
  assert.equal(new Set(routeStrokes).size, count, 'all routes use distinct colors');
  assert.equal(routeStrokes.at(-1), colors[0], 'keep the selected route on top');
  const click = ([x, y], kind, id) => {
    assert.ok(x >= 0 && x <= viewport.width && y >= 0 && y <= viewport.height);
    const previous = inspected.length;
    listeners.get('click')({ clientX: viewport.left + x, clientY: viewport.top + y });
    assert.equal(inspected.length, previous + 1);
    assert.equal(inspected.at(-1).kind, kind);
    assert.equal(inspected.at(-1).id, id);
  };
  vans.forEach(({ id }, index) => {
    click(bodies[index], 'van', id);
    click(labels.get(id), 'van', id);
  });
  chargers.forEach(({ id }) => click(labels.get(id), 'charger', id));
  click(labels.get('D1'), 'delivery', 'D1');
  return {
    draw(minute, playing) { circles.length = 0; map.draw(minute, null, playing); return circles.length; },
    clear() { map.setSimulation(null); map.draw(0); },
  };
}

const chargers = [{ id: 'C1', position: [5, 4] }, { id: 'C2', position: [6, 5] }, { id: 'C3', position: [5, 6] }];
testMap({ width: 12, height: 12 }, { width: 840, height: 540, left: 110, top: 45 }, [5, 5], 6, chargers);
testMap({ width: 12, height: 12 }, { width: 300, height: 225, left: 16, top: 48 }, [5, 5], 6, chargers);
testMap({ width: 12, height: 12 }, { width: 300, height: 225, left: 16, top: 48 }, [0, 0], 6);
testMap({ width: 6, height: 6 }, { width: 840, height: 540, left: 0, top: 0 }, [2, 2], 2);
testMap({ width: 20, height: 14 }, { width: 460, height: 250, left: 16, top: 48 }, [3, 3], 5);
testMap({ width: 12, height: 12 }, { width: 300, height: 225, left: 16, top: 48 }, null, 2,
  undefined, undefined, [{ id: 'P1', position: [1, 1] }, { id: 'P2', position: [8, 8] }]);
const motion = testMap({ width: 6, height: 6 }, { width: 840, height: 540, left: 0, top: 0 }, [2, 2], 2);
const paused = motion.draw(0.5, false);
assert.ok(motion.draw(0.5, true) > paused, 'moving vans get a short trail based on recent positions');
assert.equal(motion.draw(0.5, false), paused, 'pause and scrub frames have no motion trail');
assert.doesNotThrow(() => motion.clear(), 'an absent classical replay can clear its map');
const reduced = testMap({ width: 6, height: 6 }, { width: 840, height: 540, left: 0, top: 0 }, [2, 2], 2,
  undefined, () => false);
assert.equal(reduced.draw(0.5, true), reduced.draw(0.5, false), 'reduced motion suppresses decorative vehicle trails');
console.log('PASS: responsive map, selected routes, accessible inspection targets, and reduced-motion-aware vehicle trails');
