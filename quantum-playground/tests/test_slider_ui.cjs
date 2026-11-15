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

function recordedFixture(charge, attempt) {
  const data = fixture(charge, attempt);
  const initial = charge === 35;
  const bestBitstring = initial ? '00101' : '0110';
  const width = initial ? 5 : 4;
  const options = initial ? ['Van1_Slot1', 'Van2_Direct'] : ['Van1_Slot2', 'Van2_Slot1'];
  return {
    ...data,
    mode: 'recorded', provenance: 'recorded',
    source: {workspace_id: 'recorded-workspace', file: initial ? 'handoff.json' : 'test.json'},
    quantum: {
      method: 'Post-Filtered One-Hot Penalty QAOA (QuantumRingsLib)',
      binary_variables: width, actual_qubits: width, shots: 2048, best_bitstring: bestBitstring,
    },
    simulation: {
      ...data.simulation,
      counts: {[bestBitstring]: initial ? 310 : 491},
      vans: data.simulation.vans.map((van, i) => ({...van, option_id: options[i]})),
    },
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
const posts = [];
const frames = [];
const pageEvents = new Map();
const json = (body, ok = true) => ({ok, json: async () => body});
const html = fs.readFileSync('web/index.html', 'utf8');
let context;

function boot(config) {
  registry.clear();
  posts.length = 0;
  frames.length = 0;
  pageEvents.clear();
  for (const [, id] of html.matchAll(/id="([^"]+)"/g)) {
    const element = new Element('div');
    element.id = id;
  }
  get('mode').value = 'recorded';
  get('speed').value = '4';
  context = vm.createContext({
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
      if (path === '/api/config') return json(config);
      if (path === '/api/example') return json({prompt, variables});
      assert.equal(path, '/api/run');
      return new Promise(resolve => posts.push({payload: JSON.parse(options.body), respond: resolve}));
    },
  });
  for (const name of ['fleet.js', 'live-updates.js', 'pygame-input.js', 'app.js']) {
    vm.runInContext(fs.readFileSync('web/' + name, 'utf8'), context);
  }
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

async function testRecordedAndExternalModes() {
  await waitFor(() => posts.length === 1, 'initial request');
  assert.deepEqual(posts[0].payload, {mode: 'recorded', prompt});
  posts[0].respond(json(recordedFixture(35, 'recorded-35')));
  await waitFor(() => frames.at(-1)?.get('view_id') === 'view-recorded-35' && !get('pygame-frame').hidden,
    'recorded backend frame');
  assert.equal(get('prompt').readOnly, true);
  assert.equal(get('run-badge').textContent, 'Recorded backend run');
  assert.equal(get('rerun').textContent, 'Load recorded result');
  assert.equal(get('score').textContent, '$15.78');
  assert.equal(get('reference-score').textContent, '$15.78');
  assert.equal(get('gap-score').textContent, '$0.00');
  assert.match(get('quantum-summary').textContent, /QAOA.*5 executed qubits.*2048 shots.*00101/);
  assert.equal(get('histogram').children[0].children.at(-1).textContent, 310);
  assert.match(get('result-source').textContent, /handoff\.json$/);
  get('fleet').children[1].onclick();
  assert.match(get('pygame-inspector').textContent, /Option: Van2_Direct/);

  const recordedCharge = get('variable-van_2_initial_charge_pct');
  recordedCharge.value = '20';
  recordedCharge.onchange();
  get('rerun').onclick();
  assert.deepEqual(posts[1].payload, {
    mode: 'recorded', view_id: 'view-recorded-35', variables: {van_2_initial_charge_pct: 20},
  });
  assert.match(get('status').textContent, /Loading the recorded result/);
  posts[1].respond(json(recordedFixture(20, 'recorded-20')));
  await waitFor(() => frames.at(-1)?.get('view_id') === 'view-recorded-20' && !get('pygame-frame').hidden,
    'recorded control test frame');
  assert.equal(get('score').textContent, '$58.37');
  assert.match(get('applied-settings').textContent, /20%/);
  assert.match(get('quantum-summary').textContent, /4 executed qubits.*2048 shots.*0110/);
  assert.equal(get('histogram').children[0].children.at(-1).textContent, 491);
  assert.match(get('result-source').textContent, /test\.json$/);

  get('mode').value = 'reference';
  get('mode').onchange();
  assert.equal(get('rerun').disabled, true, 'a recorded view cannot be recalculated as a classical reference');
  posts.length = 0;
  get('prompt-form').onsubmit({preventDefault() {}});
  assert.deepEqual(posts[0].payload, {mode: 'reference', prompt});
  posts[0].respond(json(fixture()));
  await waitFor(() => frames.at(-1)?.get('view_id') === 'view-1' && !get('pygame-frame').hidden,
    'classical reference frame');

  const charge = get('variable-van_2_initial_charge_pct');
  assert.equal(charge.tag, 'select');
  assert.deepEqual(charge.children.map(option => option.value), ['35', '20']);
  assert.equal(charge.value, '35');
  assert.match(get('applied-settings').textContent, /35%/);
  assert.equal(get('run-badge').textContent, 'Exact reference');
  assert.equal(get('result-source').hidden, true);
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
  get('prompt-form').onsubmit({preventDefault() {}});
  assert.deepEqual(posts[4].payload, {mode: 'live', prompt: 'A new draft.\n'});
  const live = fixture(35, '3');
  live.simulation.duration_min = 120.009;
  posts[4].respond(json({
    ...live, mode: 'live', provenance: 'recorded',
    circuits: [{title: 'Solver circuit', drawing: 'q0: --H--M--'}],
  }));
  await flush();
  assert.equal(get('run-badge').textContent, 'Recorded backend run');
  assert.equal(get('circuit-panel').hidden, false);
  assert.equal(get('circuits').children.length, 1);
  assert.equal(get('reference-score').textContent, '$15.78');
  assert.equal(get('gap-score').textContent, '$0.00');

  await waitFor(() => frames.at(-1)?.get('view_id') === 'view-3' && !image.hidden, 'live Pygame frame');
  image.onkeydown({key: 'End', preventDefault() {}});
  const framesBeforeEnd = frames.length;
  await waitFor(() => frames.length > framesBeforeEnd, 'end of the fractional replay');
  assert.equal(frames.at(-1).get('minute'), '120.009');
  get('mode').value = 'recorded';
  get('mode').onchange();
  get('mode').value = 'reference';
  get('mode').onchange();
  get('mode').value = 'live';
  get('mode').onchange();
  assert.equal(get('prompt').value, 'A new draft.\n', 'local replay modes preserve the live draft');
  console.log('PASS: recorded outcomes, explicit recalculation, failed and mismatched results, replay controls, and provenance');
}

async function testLocalSolver() {
  await waitFor(() => posts.length === 1, 'initial live request');
  assert.deepEqual(posts[0].payload, {mode: 'live', prompt});
  assert.equal(get('prompt').readOnly, true);
  assert.equal(get('mode').value, 'live');
  assert.equal(get('status').textContent, 'Running solver…');

  const live = (charge, attempt) => ({...fixture(charge, attempt), mode: 'live', provenance: 'live'});
  posts[0].respond(json(live(35, 'local-1')));
  await waitFor(() => frames.at(-1)?.get('view_id') === 'view-local-1' && !get('pygame-frame').hidden,
    'first live map');

  const control = get('variable-van_2_initial_charge_pct');
  assert.equal(get('rerun').hidden, true);
  control.value = '20';
  control.onchange();
  assert.deepEqual(posts[1].payload, {
    mode: 'live', view_id: 'view-local-1', variables: {van_2_initial_charge_pct: 20},
  });
  assert.equal(get('score').textContent, '$15.78');
  assert.equal(get('pygame-frame').hidden, false);
  assert.match(get('applied-settings').textContent, /35%/);
  assert.equal(get('applied-settings').hidden, false);

  posts[1].respond(json({error: 'Solver unavailable.'}, false));
  await flush();
  assert.equal(get('error').textContent, 'Solver unavailable.');
  assert.equal(get('score').textContent, '$15.78');
  assert.equal(control.value, '20');
  assert.equal(get('pygame-frame').hidden, false);
  assert.equal(get('rerun').hidden, false);
  assert.equal(get('rerun').textContent, 'Retry solver');

  get('rerun').onclick();
  assert.deepEqual(posts[2].payload, posts[1].payload);
  posts[2].respond(json(live(20, 'local-2')));
  await waitFor(() => frames.at(-1)?.get('view_id') === 'view-local-2' && !get('pygame-frame').hidden,
    'recalculated live map');
  assert.equal(get('score').textContent, '$58.37');
  assert.equal(get('applied-settings').hidden, true);
  assert.equal(get('error').hidden, true);
  assert.equal(get('rerun').hidden, true);
}

async function main() {
  boot({backend_configured: true});
  try {
    await testRecordedAndExternalModes();
  } finally {
    vm.runInContext('stopVisualizer()', context);
  }

  boot({backend_configured: true, local_backend_configured: true});
  try {
    await testLocalSolver();
  } finally {
    vm.runInContext('stopVisualizer()', context);
  }
}

main().catch(error => {
  console.error(error);
  process.exitCode = 1;
});
