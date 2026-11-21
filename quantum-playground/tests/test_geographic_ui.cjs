const assert = require('node:assert/strict');
const test = require('node:test');
const D = require('../web/scenario-draft.js');
const Geo = require('../web/geo-map.js');
const { isValidValue } = require('../web/scenario-controls.js');

function storage(initial) {
  const values = new Map(initial || []);
  return { getItem: key => values.get(key), setItem: (key, value) => values.set(key, value) };
}

test('geographic example uses lon/lat, road timing, and the same fleet settings', () => {
  const scenario = D.geographicSeed();
  assert.deepEqual(D.validate(scenario), []);
  assert.equal(scenario.schema_version, 2);
  assert.deepEqual(scenario.map, { kind: 'geographic', profile: 'driving' });
  assert.equal(Object.hasOwn(scenario, 'grid'), false);
  assert.equal(Object.hasOwn(scenario.timing, 'speed_kmh'), false);
  assert.deepEqual(scenario.depots[0].position, [-79.3832, 43.6532]);
  assert.deepEqual(scenario.fleet, D.seed().fleet);
});

test('grid and real-map drafts survive switching with incomplete fields intact', () => {
  const saved = storage(), grid = D.seed(), geographic = D.geographicSeed();
  grid.title = 'Unfinished grid'; grid.grid.width = '';
  geographic.title = 'Unfinished real map'; geographic.depots[0].position[1] = '';
  D.save(saved, grid); D.save(saved, geographic);
  const bank = D.loadState(saved);
  assert.equal(bank.version, 2); assert.equal(bank.active, 'geographic');
  assert.equal(bank.drafts.grid.grid.width, '');
  assert.equal(bank.drafts.geographic.depots[0].position[1], '');
  bank.active = 'grid'; D.saveState(saved, bank);
  assert.equal(D.load(saved).title, 'Unfinished grid');
  assert.ok(D.validate(D.load(saved)).some(message => message.includes('Grid width')));
});

test('legacy grid persistence migrates without turning its coordinates into geographic positions', () => {
  const old = D.seed(); old.depots[0].position = [5, 3];
  const saved = storage([[D.storageKey, JSON.stringify({ version: 1, draft: old })]]);
  const bank = D.loadState(saved);
  assert.equal(bank.active, 'grid'); assert.deepEqual(bank.drafts.grid.depots[0].position, [5, 3]);
  assert.deepEqual(bank.drafts.geographic.depots[0].position, [-79.3832, 43.6532]);
});

test('geographic validation bounds lat/lon and rounds at submission without erasing invalid text', () => {
  const raw = D.geographicSeed(); raw.depots[0].position = ['-79.383212345', '43.653287654'];
  assert.deepEqual(D.normalize(raw).depots[0].position, [-79.383212, 43.653288]);
  assert.equal(raw.depots[0].position[0], '-79.383212345');
  raw.depots[0].position = [-181, 86];
  assert.ok(D.validate(raw).some(message => message.includes('P1 longitude')));
  assert.ok(D.validate(raw).some(message => message.includes('P1 latitude')));
  raw.depots[0].position = ['', '43'];
  assert.ok(D.validate(raw).some(message => message.includes('P1 longitude')));
  assert.equal(Geo.validPosition([null, 43]), false);
  assert.equal(Geo.validPosition([' ', 43]), false);
});

test('only approved scalar geographic edits can reuse an existing solver', () => {
  const scenario = D.geographicSeed();
  const view = { view_id: 'geographic-view', variables: [{ id: 'charge', type: 'number', minimum: 0, maximum: 100 }],
    scenario_context: { effective_scenario: D.geographicSeed(), bindings: { charge: ['vehicles', 'V1', 'initial_charge_pct'] } } };
  scenario.vehicles[0].initial_charge_pct = 50;
  assert.deepEqual(D.reuse(view, scenario, isValidValue), { view_id: 'geographic-view', variables: { charge: 50 } });
  scenario.depots[0].position[0] += 0.0001;
  assert.equal(D.reuse(view, scenario, isValidValue), null);
});

class Element {
  constructor() { this.children = []; this.style = { setProperty() {} }; this.classList = { add() {}, toggle() {} }; }
  append(...children) { this.children.push(...children); }
  replaceChildren(...children) { this.children = children; }
  setAttribute(name, value) { this[name] = value; }
  addEventListener(name, listener) { (this.listeners ||= {})[name] = listener; }
  querySelector() { return null; }
}
function leafletFixture() {
  const created = { maps: [], markers: [], polylines: [], tiles: [] };
  class Layer {
    constructor(position, options = {}) { this.position = position; this.options = options; this.handlers = {}; this.element = new Element(); }
    addTo(parent) { this.parent = parent; return this; }
    on(name, listener) { this.handlers[name] = listener; return this; }
    getElement() { return this.element; }
    getLatLng() { return { lat: this.position[0], lng: this.position[1] }; }
    setLatLng(position) { this.position = position; return this; }
    setIcon(value) { this.icon = value; return this; }
    setStyle(value) { this.style = value; return this; }
    bindPopup() { return this; }
    openPopup() { return this; }
  }
  global.L = {
    map() {
      const map = { handlers: {}, fits: [], removed: [],
        setView(center) { this.center = center; return this; },
        getCenter() { return { lat: this.center[0], lng: this.center[1] }; },
        fitBounds(bounds) { this.fits.push(bounds); },
        on(name, listener) { this.handlers[name] = listener; return this; },
        invalidateSize() {}, removeLayer(layer) { this.removed.push(layer); }, remove() { this.destroyed = true; }, closePopup() {},
      };
      created.maps.push(map); return map;
    },
    control() { return { addTo() { return this; } }; },
    tileLayer(url, options) { const layer = new Layer(null, options); created.tiles.push({ url, options, layer }); return layer; },
    layerGroup() { const layer = new Layer(); layer.clearLayers = () => {}; return layer; },
    marker(position, options) { const layer = new Layer(position, options); created.markers.push(layer); return layer; },
    polyline(points, options) { const layer = new Layer(points, options); created.polylines.push(layer); return layer; },
    divIcon(options) { return options; }, DomEvent: { stopPropagation() {} },
  };
  const container = new Element(); container.ownerDocument = { createElement: () => new Element() };
  Geo.configure({ tile_url: 'https://tiles.example/{z}/{x}/{y}.png', tile_attribution: 'Example attribution' });
  return { container, created };
}

test('editor retains its map while updating markers and emits correctly ordered coordinates', async () => {
  const { container, created } = leafletFixture(), movements = [], placements = [];
  const editor = Geo.createEditor(container, { onMove: (...args) => movements.push(args), onPlace: value => placements.push(value) });
  editor.setScenario(D.geographicSeed(), { selected: { kind: 'depots', id: 'P1' } });
  const marker = created.markers.find(item => item.options.title === 'P1');
  assert.deepEqual(marker.position, [43.6532, -79.3832]);
  marker.position = [43.65432123, -79.38212345]; marker.handlers.dragend();
  assert.deepEqual(movements[0], ['depots', 'P1', [-79.382123, 43.654321]]);
  editor.setScenario(D.geographicSeed(), { placing: true });
  created.maps[0].handlers.click({ latlng: { lat: 43.65, lng: -79.39 } });
  assert.deepEqual(placements, [[-79.39, 43.65]]);
  editor.setScenario(D.geographicSeed(), { placing: true, busy: true });
  created.maps[0].handlers.click({ latlng: { lat: 43.66, lng: -79.4 } });
  assert.equal(placements.length, 1);
  assert.equal(created.maps.length, 1); assert.equal(created.maps[0].fits.length, 1);
  await Promise.resolve(); await Promise.resolve();
  assert.equal(created.tiles[0].options.attribution, 'Example attribution');
  editor.destroy(); assert.equal(created.maps[0].destroyed, true);
});

test('replay uses its supplied clock and geographic interpolation without recentering every frame', () => {
  const { container, created } = leafletFixture(), calls = [];
  global.FleetReplay = { depotsFor: simulation => simulation.depots,
    depotFor: (simulation, van) => simulation.depots.find(depot => depot.id === van.depot_id).position,
    stateAt(...args) { calls.push(args); return { position: [-79.38, 43.652] }; }, deliveredAt: () => new Set(),
  };
  const replay = Geo.createReplay(container);
  replay.setSimulation({ map: { kind: 'geographic' }, depots: [{ id: 'P1', position: [-79.3832, 43.6532] }], chargers: [], deliveries: [],
    vans: [{ id: 'V1', depot_id: 'P1', events: [{ type: 'drive', path: [[-79.3832, 43.6532], [-79.38, 43.65]] }] }],
  });
  replay.draw(12.5, 'V1', true); replay.draw(15, 'V1', true);
  assert.deepEqual(calls.map(args => [args[1], args[3]]), [[12.5, true], [15, true]]);
  assert.deepEqual(created.markers.find(marker => marker.options.title === 'V1').position, [43.652, -79.38]);
  assert.deepEqual(created.polylines[0].position, [[43.6532, -79.3832], [43.65, -79.38]]);
  assert.equal(created.maps[0].fits.length, 1);
  replay.setSimulation(null); replay.destroy();
});
