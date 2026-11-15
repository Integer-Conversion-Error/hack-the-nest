process.chdir(require('node:path').resolve(__dirname, '..'));

const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const prompt = 'EV fleet dispatch example\n';
const variables = [{
  id: 'van_2_initial_charge_pct',
  label: 'Van 2 starting charge',
  allowed_values: [35, 20],
  default: 35,
  unit: '%',
  rerun_mode: 'rebuild',
}];
const depot = [2, 2];
const delivery = {id: 'D1', position: [2, 1], deadline_min: 80};
const drive = {
  type: 'drive', position: depot, path: [depot, delivery.position],
  start_min: 0, end_min: 1, battery_start_kwh: 4, battery_end_kwh: 3.85,
};
const deliver = {
  type: 'deliver', position: delivery.position, delivery_id: delivery.id,
  start_min: 90, end_min: 95, battery_start_kwh: 3.85, battery_end_kwh: 3.85,
};

function fixture(charge = 35, attempt = '1') {
  const score = charge === 35 ? 947 / 60 : 3502 / 60;
  return {
    mode: 'reference', view_id: 'view-' + attempt, run_id: 'run', plan_id: 'plan',
    attempt_id: attempt, status: 'completed', provenance: 'classical_reference',
    problem: {title: 'Fleet dispatch', statement: 'Backend summary.', original_prompt: prompt},
    variables, effective_values: {van_2_initial_charge_pct: charge},
    simulation: {
      kind: 'ev_fleet', grid: {width: 6, height: 6}, depot,
      chargers: [{id: 'C1', position: [2, 1]}], deliveries: [delivery],
      start_hour: 8, duration_min: 120, battery_capacity_kwh: 20, reserve_pct: 15,
      vans: [
        {id: 'V1', initial_battery_kwh: 4, delivery_ids: [delivery.id], events: [drive, deliver]},
        {id: 'V2', initial_battery_kwh: charge / 5, events: []},
      ],
    },
    evaluation: {feasible: true, objective_value: score, reference_value: score, gap: 0},
  };
}

const scene = {
  width: 840, height: 620,
  targets: [
    {kind: 'charger', id: 'C1', rect: [100, 100, 50, 50]},
    {kind: 'van', id: 'V2', rect: [230, 440, 188, 68]},
    {kind: 'play', rect: [24, 530, 100, 34]},
    {kind: 'scrub', rect: [24, 580, 792, 24]},
  ],
};

const registry = new Map();
class Element {
  constructor(tag) {
    this.tag = tag;
    this.children = [];
    this.style = {setProperty() {}};
    this.value = '';
    this.attrs = {};
    this.classList = {toggle() {}};
  }
  set id(value) { this._id = value; registry.set(value, this); }
  get id() { return this._id; }
  append(...items) { this.children.push(...items); }
  replaceChildren(...items) { this.children = items; }
  setAttribute(key, value) { this.attrs[key] = value; }
  querySelectorAll(selector) {
    const matches = child => selector.startsWith('.')
      ? child.className?.split(' ').includes(selector.slice(1))
      : child.tag === selector;
    return this.children.flatMap(child => [
      ...(matches(child) ? [child] : []),
      ...child.querySelectorAll(selector),
    ]);
  }
  querySelector(selector) { return this.querySelectorAll(selector)[0]; }
  reportValidity() { return Boolean(this.value.trim()); }
  getBoundingClientRect() { return {left: 0, top: 0, width: 840, height: 620}; }
  focus() {}
  setPointerCapture() {}
  set src(value) { this._src = value; setTimeout(() => this.onload?.(), 0); }
}

const get = id => registry.get(id);
for (const [, id] of fs.readFileSync('web/index.html', 'utf8').matchAll(/id="([^"]+)"/g)) {
  const element = new Element('div');
  element.id = id;
}
get('mode').value = 'reference';
get('speed').value = '4';
const posts = [];
const frames = [];
const pageEvents = new Map();
const json = (body, ok = true) => ({ok, json: async () => body});
const context = vm.createContext({
  console, setTimeout, clearTimeout, URLSearchParams,
  URL: {createObjectURL: () => 'blob:test-frame', revokeObjectURL() {}},
  requestAnimationFrame() {},
  window: {addEventListener: (event, listener) => pageEvents.set(event, listener)},
  document: {
    getElementById: get,
    createElement: tag => new Element(tag),
    querySelectorAll: selector => [...registry.values()].flatMap(element => element.querySelectorAll(selector)),
  },
  fetch: async (path, options) => {
    if (path.startsWith('/api/pygame-frame?')) {
      frames.push(new URLSearchParams(path.split('?')[1]));
      return {ok: true, headers: {get: () => JSON.stringify(scene)}, blob: async () => ({})};
    }
    if (path === '/api/config') return json({backend_configured: true});
    if (path === '/api/example') return json({prompt, variables});
    assert.equal(path, '/api/run');
    return new Promise(resolve => posts.push({payload: JSON.parse(options.body), respond: resolve}));
  },
});
for (const name of ['fleet.js', 'live-updates.js', 'pygame-input.js', 'app.js']) {
  vm.runInContext(fs.readFileSync('web/' + name, 'utf8'), context);
}

const wait = ms => new Promise(resolve => setTimeout(resolve, ms));
async function waitFor(predicate, label) {
  const deadline = Date.now() + 2500;
  while (!predicate()) {
    if (Date.now() > deadline) throw Error('Timed out waiting for ' + label);
    await wait(10);
  }
}
const flush = () => new Promise(resolve => setImmediate(resolve));

async function main() {
  await waitFor(() => posts.length === 1, 'initial request');
  assert.deepEqual(posts[0].payload, {mode: 'reference', prompt});
  posts[0].respond(json(fixture()));
  await waitFor(() => get('pygame-frame').hidden === false, 'initial Pygame frame');

  const charge = get('variable-van_2_initial_charge_pct');
  assert.equal(charge.tag, 'select');
  assert.deepEqual(charge.children.map(option => option.value), ['35', '20']);
  assert.equal(charge.value, '35');
  assert.match(get('applied-settings').textContent, /35%/);
  assert.equal(get('run-badge').textContent, 'Exact reference');
  assert.equal(get('score').textContent, '$15.78');
  assert.equal(get('end-time').textContent, '10:00');
  assert.equal(get('circuit-panel').hidden, true);

  pageEvents.get('pagehide')({persisted: true});
  const framesBeforeRestore = frames.length;
  pageEvents.get('pageshow')({persisted: true});
  await waitFor(() => frames.length > framesBeforeRestore && !get('pygame-frame').hidden, 'restored Pygame frame');
  assert.equal(frames.at(-1).get('view_id'), 'view-1');

  get('scrub').value = '60';
  get('scrub').oninput();
  get('fleet').children[1].onclick();
  charge.value = '20';
  charge.onchange();
  assert.equal(posts.length, 1, 'editing a control waits for Apply');
  get('rerun').onclick();
  assert.deepEqual(posts[1].payload, {
    mode: 'reference', view_id: 'view-1', variables: {van_2_initial_charge_pct: 20},
  });
  assert.match(get('status').textContent, /Recalculating/);
  assert.equal(get('score').textContent, '$15.78');
  assert.match(get('applied-settings').textContent, /35%/);
  assert.equal(get('clock').textContent, '09:00');
  assert.equal(get('pygame-frame').hidden, false, 'the previous map stays visible while pending');

  posts[1].respond(json({error: 'Worker timed out.'}, false));
  await flush();
  assert.equal(get('error').textContent, 'Worker timed out.');
  assert.equal(get('score').textContent, '$15.78');
  assert.equal(charge.value, '20');
  assert.equal(get('rerun').disabled, false, 'failed Apply keeps the draft for retry');

  get('rerun').onclick();
  posts[2].respond(json({...fixture(20, '2'), plan_id: 'another-plan'}));
  await flush();
  assert.match(get('error').textContent, /did not match/);
  assert.equal(get('score').textContent, '$15.78');

  get('rerun').onclick();
  posts[3].respond(json(fixture(20, '2')));
  await waitFor(() => frames.at(-1)?.get('view_id') === 'view-2' && !get('pygame-frame').hidden, 'new Pygame frame');
  assert.equal(get('score').textContent, '$58.37');
  assert.match(get('applied-settings').textContent, /20%/);
  assert.equal(get('clock').textContent, '09:00');
  assert.equal(vm.runInContext('selectedVan', context), 'V2');
  assert.equal(vm.runInContext('playing', context), false);
  assert.equal(get('error').hidden, true);
  assert.equal(get('rerun').disabled, true);
  assert.equal(frames.at(-1).get('minute'), '60');

  const image = get('pygame-frame');
  const click = kind => {
    const [left, top, width, height] = scene.targets.find(target => target.kind === kind).rect;
    image.onpointerdown({
      clientX: left + width / 2, clientY: top + height / 2,
      button: 0, pointerId: 1, preventDefault() {},
    });
  };
  click('charger');
  assert.equal(get('pygame-inspector').textContent, 'C1 | No charging scheduled at this time | Reservations: none');
  click('play');
  assert.equal(vm.runInContext('playing', context), true);
  click('scrub');
  assert.equal(get('clock').textContent, '09:00');
  assert.equal(vm.runInContext('playing', context), false);
  image.onpointerup();
  image.onkeydown({key: 'd', preventDefault() {}});
  assert.match(get('pygame-inspector').textContent, /Deadline 09:20.*15 min late/);

  get('mode').value = 'live';
  get('mode').onchange();
  get('prompt').value = 'A new draft.\n';
  assert.equal(get('submitted-prompt').textContent, prompt, 'draft and applied prompts stay separate');
  get('prompt-form').onsubmit({preventDefault() {}});
  assert.deepEqual(posts[4].payload, {mode: 'live', prompt: 'A new draft.\n'});
  const live = fixture(35, '3');
  live.simulation.duration_min = 120.009;
  posts[4].respond(json({
    ...live, mode: 'live', provenance: 'recorded',
    circuits: [{title: 'Solver circuit', drawing: 'q0: --H--M--'}],
  }));
  await flush();
  assert.equal(get('run-badge').textContent, 'Recorded run');
  assert.equal(get('circuit-panel').hidden, false);
  assert.equal(get('circuits').children.length, 1);
  assert.equal(get('reference-score').textContent, '$15.78');
  assert.equal(get('gap-score').textContent, '$0.00');

  await waitFor(() => frames.at(-1)?.get('view_id') === 'view-3' && !image.hidden, 'live Pygame frame');
  image.onkeydown({key: 'End', preventDefault() {}});
  const framesBeforeEnd = frames.length;
  await waitFor(() => frames.length > framesBeforeEnd, 'end of the fractional replay');
  assert.equal(frames.at(-1).get('minute'), '120.009');
  console.log('PASS: explicit recalculation, failed and mismatched results, replay controls, and provenance');
}

main().catch(error => {
  console.error(error);
  process.exitCode = 1;
}).finally(() => vm.runInContext('stopVisualizer()', context));
