process.chdir(require('node:path').resolve(__dirname, '..'));

const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');

const chargeKey = 'van_2_initial_charge_pct';
const chargeSliderId = 'variable-' + chargeKey + '-slider';
const examplePrompt = 'EV fleet dispatch example\n';
const liveExamplePrompt = 'EV fleet dispatch example with a charge range\n';
const variables = [{
  id: chargeKey, label: 'Van 2 starting charge', type: 'integer',
  allowed_values: [35, 20], default: 35, unit: '%', rerun_mode: 'rebuild',
}];
const chargeRange = {
  ...variables[0], allowed_values: null, minimum: 20, maximum: 35, step: 1,
};
const depot = [2, 2];
const delivery = { id: 'D1', position: [2, 1], deadline_min: 80 };
const drive = {
  type: 'drive', position: depot, path: [depot, delivery.position],
  start_min: 0, end_min: 1, battery_start_kwh: 4, battery_end_kwh: 3.85,
};
const deliver = {
  type: 'deliver', position: delivery.position, delivery_id: delivery.id,
  start_min: 90, end_min: 95, battery_start_kwh: 3.85, battery_end_kwh: 3.85,
};

function fixture(charge = 35, attempt = '1', mode = 'reference') {
  const score = charge === 35 ? 947 / 60 : 3502 / 60;
  const recorded = mode === 'recorded';
  const bits = charge === 35 ? '00101' : '0110';
  const width = charge === 35 ? 5 : 4;
  return {
    mode, view_id: 'view-' + attempt, run_id: 'run', plan_id: 'plan', attempt_id: attempt, status: 'completed',
    provenance: mode === 'reference' ? 'classical_reference' : mode,
    problem: { title: 'Fleet dispatch', original_prompt: examplePrompt },
    variables, effective_values: { [chargeKey]: charge },
    source: recorded ? { workspace_id: 'recorded-workspace', file: charge === 35 ? 'handoff.json' : 'test.json' } : undefined,
    quantum: mode !== 'reference'
      ? { binary_variables: width, actual_qubits: width, engine: 'quantum-rings', shots: 2048, best_bitstring: bits }
      : {},
    simulation: {
      kind: 'ev_fleet', grid: { width: 6, height: 6 }, depot,
      chargers: [{ id: 'C1', position: [2, 1] }], deliveries: [delivery],
      start_hour: 8, duration_min: 120, battery_capacity_kwh: 20, reserve_pct: 15,
      counts: recorded ? { [bits]: charge === 35 ? 310 : 491 } : {},
      vans: [
        { id: 'V1', initial_battery_kwh: 4, delivery_ids: [delivery.id], events: [drive, deliver] },
        { id: 'V2', initial_battery_kwh: charge / 5, events: [] },
      ],
    },
    evaluation: {
      feasible: true, objective_value: score, reference_value: score, gap: 0,
      costs: { driving: 2.7, electricity: charge === 35 ? 1 : 2,
        waiting: charge === 35 ? 725 / 60 : 2200 / 60, late: charge === 35 ? 0 : 17 },
    },
  };
}

function liveFixture(charge, attempt, prompt = liveExamplePrompt) {
  const data = fixture(charge, attempt, 'live');
  data.problem.original_prompt = prompt;
  data.variables = [chargeRange];
  return data;
}

const html = fs.readFileSync('web/index.html', 'utf8');
const json = (body, ok = true) => ({ ok, json: async () => body });
const flush = () => new Promise(resolve => setImmediate(resolve));
const preventDefault = () => {};

function boot(config, liveExample = liveExamplePrompt) {
  const registry = new Map();
  const posts = [];
  const drawings = [];
  const listeners = new Map();
  let animationFrame, inspect, displayedSimulation;

  class Element {
    constructor(tag) {
      this.tag = tag;
      this.children = [];
      this.style = { setProperty() {} };
      this.value = '';
      this.attrs = {};
      this.classList = { toggle() {} };
    }
    set id(value) { this._id = value; registry.set(value, this); }
    get id() { return this._id; }
    append(...items) { this.children.push(...items); }
    replaceChildren(...items) { this.children = items; }
    setAttribute(key, value) { this.attrs[key] = value; }
    reportValidity() { return Boolean(this.value.trim()); }
  }

  const get = id => registry.get(id);
  for (const [, id] of html.matchAll(/id="([^"]+)"/g)) new Element('div').id = id;
  get('mode').value = 'recorded';
  get('speed').value = '4';
  get('fleet-map').hidden = true;
  get('original-prompt').open = false;

  const context = vm.createContext({
    console, setTimeout, clearTimeout,
    requestAnimationFrame: draw => { animationFrame = draw; },
    window: { addEventListener: (event, listener) => listeners.set(event, listener) },
    document: { getElementById: get, createElement: tag => new Element(tag) },
    FleetMap: {
      create(_canvas, options) {
        inspect = options.onInspect;
        return {
          setSimulation: simulation => { displayedSimulation = simulation; },
          draw: (minute, selection, playing) => drawings.push({ minute, selection, playing, simulation: displayedSimulation }),
        };
      },
    },
    fetch: async (path, options) => {
      if (path === '/api/config') return json(config);
      if (path === '/api/example') return json({ prompt: examplePrompt, live_prompt: liveExample, variables });
      assert.equal(path, '/api/run', 'browser playback sends no frame requests');
      return new Promise(resolve => posts.push({ payload: JSON.parse(options.body), respond: resolve }));
    },
  });
  for (const name of ['fleet.js', 'live-updates.js', 'scenario-controls.js', 'app.js']) {
    vm.runInContext(fs.readFileSync('web/' + name, 'utf8'), context);
  }
  return {
    get, posts, drawings, listeners, inspect,
    advance: now => animationFrame(now),
    chooseCharge(value) {
      const slider = get(chargeSliderId);
      slider.value = String(value);
      slider.oninput();
      slider.onchange();
    },
    async respond(body, ok) {
      posts.at(-1).respond(json(body, ok));
      await flush();
    },
    mode(value) {
      get('mode').value = value;
      get('mode').onchange();
      get('prompt-form').onsubmit({ preventDefault });
    },
  };
}

async function testReferenceAndRecorded() {
  const ui = boot({ backend_configured: true });
  const { get, posts, drawings } = ui;
  await flush();
  assert.deepEqual(posts[0].payload, { mode: 'recorded', prompt: examplePrompt });
  await ui.respond(fixture(35, 'recorded', 'recorded'));
  assert.equal(get('run-badge').textContent, 'Recorded backend run');
  assert.equal(get('score').textContent, '$15.78');
  assert.equal(get('histogram').children[0].children.at(-1).textContent, 310);
  assert.match(get('result-source').textContent, /handoff\.json$/);

  ui.mode('reference');
  await ui.respond(fixture());
  assert.equal(get(chargeSliderId).value, '1');
  assert.equal(get(chargeSliderId).type, 'range');
  assert.equal(get(chargeSliderId).attrs['aria-valuetext'], '35%');
  assert.equal(get('fleet-map').hidden, false);
  assert.equal(get('original-prompt').open, false);
  get('scrub').value = '60';
  get('scrub').oninput();
  get('fleet').children[1].onclick();
  ui.chooseCharge(0);
  assert.equal(posts.length, 2, 'reference drafts wait for Apply');
  get('rerun').onclick();
  assert.deepEqual(posts[2].payload, { mode: 'reference', view_id: 'view-1', variables: { [chargeKey]: 20 } });
  assert.equal(get('score').textContent, '$15.78');
  assert.equal(get('fleet-map').hidden, false);
  assert.equal(drawings.at(-1).minute, 60);
  assert.equal(drawings.at(-1).selection, 'V2');
  assert.equal(drawings.at(-1).simulation.vans[1].initial_battery_kwh, 7);

  await ui.respond({ error: 'Worker timed out.' }, false);
  assert.equal(get('error').textContent, 'Worker timed out.');
  assert.equal(get(chargeSliderId).attrs['aria-valuetext'], '20%');
  get('rerun').onclick();
  await ui.respond({ ...fixture(20, '2'), plan_id: 'another-plan' });
  assert.match(get('error').textContent, /did not match/);
  assert.equal(get('score').textContent, '$15.78');

  get('rerun').onclick();
  await ui.respond(fixture(20, '2'));
  assert.equal(get('error').hidden, true);
  assert.equal(get('score').textContent, '$58.37');
  assert.equal(get('clock').textContent, '09:00');
  assert.equal(drawings.at(-1).selection, 'V2');
  assert.equal(drawings.at(-1).simulation.vans[1].initial_battery_kwh, 4);
  assert.deepEqual(get('cost-delta').children.map(item => item.textContent),
    ['Electricity +$1.00', 'Waiting +$24.58', 'Lateness +$17.00']);

  ui.inspect({ kind: 'charger', id: 'C1' });
  assert.match(get('pygame-inspector').textContent, /No charging scheduled/);
  get('fleet-map').onkeydown({ key: 'd', preventDefault });
  assert.match(get('pygame-inspector').textContent, /Deadline 09:20.*15 min late/);
  const beforePlayback = drawings.length;
  get('play').onclick();
  ui.advance(100);
  ui.advance(150);
  assert.equal(drawings.at(-1).minute, 60.2);
  assert.equal(drawings.at(-1).playing, true);
  ui.listeners.get('pageshow')({ persisted: true });
  assert.ok(drawings.length > beforePlayback);
  assert.equal(posts.length, 5);
}

async function testLocalSolver() {
  const ui = boot({ backend_configured: true, local_backend_configured: true });
  const { get, posts, drawings } = ui;
  await flush();
  assert.deepEqual(posts[0].payload, { mode: 'live', prompt: liveExamplePrompt });
  assert.equal(get('prompt').readOnly, true);
  await ui.respond(liveFixture(35, 'local-1'));
  assert.equal(get('run-badge').textContent, 'Live · Quantum Rings');
  const chargeSlider = get(chargeSliderId);
  assert.deepEqual([chargeSlider.min, chargeSlider.max, chargeSlider.step], ['20', '35', '1']);
  chargeSlider.value = '27';
  chargeSlider.oninput();
  assert.equal(get('variable-' + chargeKey).value, '27');
  assert.equal(chargeSlider.attrs['aria-valuetext'], '27%');
  assert.equal(posts.length, 1, 'previewing the slider commits no solver request');
  chargeSlider.onchange();
  assert.deepEqual(posts[1].payload, {
    mode: 'live', view_id: 'view-local-1', variables: { [chargeKey]: 27 },
  });
  assert.equal(get('score').textContent, '$15.78');
  assert.equal(get('fleet-map').hidden, false);
  await ui.respond({ error: 'Solver unavailable.' }, false);
  assert.equal(get('rerun').disabled, false, 'the failed change can be applied again');
  get('rerun').onclick();
  assert.deepEqual(posts[2].payload, posts[1].payload);
  const updated = liveFixture(27, 'local-2');
  updated.simulation.duration_min = 120.009;
  await ui.respond(updated);
  assert.equal(drawings.at(-1).simulation.vans[1].initial_battery_kwh, 5.4);
  get('fleet-map').onkeydown({ key: 'End', preventDefault });
  assert.equal(drawings.at(-1).minute, 120.009);
  assert.equal(get('clock').textContent, '10:00');
  assert.equal(get('rerun').disabled, true);
}

async function testPlannerControls() {
  const ui = boot({ backend_configured: true, pipeline_backend_configured: true });
  const { get, posts } = ui;
  await flush();
  assert.deepEqual(posts[0].payload, { mode: 'recorded', prompt: examplePrompt });
  await ui.respond(fixture(35, 'recorded', 'recorded'));

  get('mode').value = 'live';
  get('mode').onchange();
  assert.equal(posts.length, 1, 'switching modes leaves planning for an explicit run');
  assert.equal(get('original-prompt').open, false);
  assert.equal(get('prompt').readOnly, false);
  assert.equal(get('generate').textContent, 'Plan and run');
  assert.equal(get('prompt').value, liveExamplePrompt);
  get('prompt').value = 'Draft to replace';
  get('load-example').onclick();
  assert.equal(get('prompt').value, liveExamplePrompt);

  const prompt = 'Optimize fleet dispatch with a charging price control';
  get('prompt').value = prompt;
  get('prompt-form').onsubmit({ preventDefault });
  assert.deepEqual(posts[1].payload, { mode: 'live', prompt });
  assert.equal(get('status').textContent, 'Planning and running solver…');

  const planned = liveFixture(35, 'planned', prompt);
  planned.variables = [{ ...chargeRange, group: 'Battery' },
    { id: 'price', label: 'Charging price', type: 'number', minimum: 0, maximum: 2,
      step: 0.05, default: 0.2, unit: '$/kWh', group: 'Costs', rerun_mode: 'rebuild' },
    { id: 'automation', label: 'Allow automation', type: 'boolean', default: false, rerun_mode: 'bind' },
  ];
  planned.effective_values = { [chargeKey]: 35, price: 0.2, automation: false };
  await ui.respond(planned);
  const [battery, costs] = get('variables').children;
  assert.equal(battery.children[0].textContent, 'Battery');
  assert.equal(battery.open, true);
  assert.equal(costs.children[0].textContent, 'Costs');
  assert.equal(costs.open, false);
  ui.chooseCharge(28);
  assert.deepEqual(posts[2].payload, {
    mode: 'live', view_id: 'view-planned', variables: { [chargeKey]: 28, price: 0.2, automation: false },
  });
  const recalculated = liveFixture(28, 'range-rerun', prompt);
  recalculated.variables = planned.variables;
  recalculated.effective_values = posts[2].payload.variables;
  await ui.respond(recalculated);
  assert.equal(ui.drawings.at(-1).simulation.vans[1].initial_battery_kwh, 5.6);

  const slider = get('variable-price-slider');
  assert.equal(slider.type, 'range');
  assert.equal(slider.value, '0.2');
  slider.value = '0.25';
  slider.oninput();
  slider.onchange();
  ui.chooseCharge(24);
  get('variable-automation').checked = true;
  get('variable-automation').onchange();
  assert.equal(posts.length, 3, 'other draft values wait for Apply even after a charge commit');
  get('rerun').onclick();
  assert.deepEqual(posts[3].payload, {
    mode: 'live', view_id: 'view-range-rerun', variables: { [chargeKey]: 24, price: 0.25, automation: true },
  });
}

async function testLiveExampleFallback() {
  const ui = boot({ backend_configured: true, local_backend_configured: true }, null);
  await flush();
  assert.deepEqual(ui.posts[0].payload, { mode: 'live', prompt: examplePrompt });
  await ui.respond(fixture(35, 'legacy', 'live'));
}

async function main() {
  await testReferenceAndRecorded();
  await testLocalSolver();
  await testPlannerControls();
  await testLiveExampleFallback();
  console.log('PASS: canvas playback, verified result swaps, and typed planner controls');
}

main().catch(error => {
  console.error(error);
  process.exitCode = 1;
});
