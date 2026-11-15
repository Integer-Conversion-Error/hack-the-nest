process.chdir(require('node:path').resolve(__dirname, '..'));

const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');

const chargeKey = 'van_2_initial_charge_pct';
const chargeSliderId = 'variable-' + chargeKey + '-slider';
const examplePrompt = 'EV fleet dispatch example\n';
const liveExamplePrompt = 'EV fleet dispatch example with a charge range\n';
const pricePrompt = examplePrompt + 'Vary the electricity price.\n';
const sampleCatalog = [
  { id: 'ev-charge-range', label: 'Van 2 charge range', prompt: liveExamplePrompt },
  { id: 'ev-charge-options', label: 'Two charge options', prompt: examplePrompt },
  { id: 'ev-electricity-price', label: 'Electricity price', prompt: pricePrompt },
];
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
const json = (body, ok = true, status = ok ? 200 : 400) => ({ ok, status, json: async () => body });
const flush = () => new Promise(resolve => setImmediate(resolve));
const preventDefault = () => {};

function boot(config, liveExample = liveExamplePrompt, storage = new Map(), samples = sampleCatalog) {
  const registry = new Map();
  const posts = [];
  const polls = [];
  const historyReads = [];
  const timers = [];
  const drawings = [];
  const listeners = new Map();
  let animationFrame, inspect, displayedSimulation;

  class Element {
    constructor(tag) {
      this.tag = tag;
      this.children = [];
      this.style = { setProperty(name, value) { this[name] = value; } };
      this.value = '';
      this.attrs = {};
      this.classList = { toggle() {} };
    }
    set id(value) { this._id = value; registry.set(value, this); }
    get id() { return this._id; }
    append(...items) { this.children.push(...items); }
    replaceChildren(...items) { this.children = items; }
    setAttribute(key, value) { this.attrs[key] = value; }
    setCustomValidity(message) { this.validationMessage = message; }
    reportValidity() { return Boolean(this.value.trim()); }
    focus() {}
  }

  const get = id => registry.get(id);
  for (const [, id] of html.matchAll(/id="([^"]+)"/g)) new Element('div').id = id;
  get('mode').value = 'recorded';
  get('speed').value = '4';
  get('fleet-map').hidden = true;
  get('original-prompt').open = false;
  get('progress-screen').hidden = true;
  get('history-screen').hidden = true;
  get('results-screen').hidden = true;

  const context = vm.createContext({
    console,
    setTimeout: (callback, delay) => { assert.ok(delay > 0); timers.push(callback); },
    clearTimeout,
    requestAnimationFrame: draw => { animationFrame = draw; },
    window: {
      addEventListener: (event, listener) => listeners.set(event, listener),
      sessionStorage: {
        getItem: key => storage.get(key),
        setItem: (key, value) => storage.set(key, value),
        removeItem: key => storage.delete(key),
      },
    },
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
      if (path === '/api/example') return json({
        prompt: examplePrompt, live_prompt: liveExample, variables, ...(samples ? { samples } : {}),
      });
      if (path.startsWith('/api/pipeline-runs/')) {
        assert.equal(options, undefined, 'progress is requested with GET');
        return new Promise(resolve => polls.push({ path, respond: resolve }));
      }
      if (path.startsWith('/api/workspaces') && options === undefined) {
        return new Promise(resolve => historyReads.push({ path, respond: resolve }));
      }
      assert.ok(['/api/run', '/api/pipeline-runs'].includes(path) || /^\/api\/workspaces\/[^/]+\/open$/.test(path),
        'browser playback sends no frame requests');
      return new Promise(resolve => posts.push({ path, payload: JSON.parse(options.body), respond: resolve }));
    },
  });
  for (const name of ['fleet.js', 'live-updates.js', 'pipeline-progress.js', 'scenario-controls.js', 'app.js']) {
    vm.runInContext(fs.readFileSync('web/' + name, 'utf8'), context);
  }
  return {
    get, posts, polls, historyReads, drawings, listeners, inspect, storage,
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
    async readHistory(body, index = historyReads.length - 1) {
      historyReads[index].respond(json(body));
      await flush();
    },
    async poll(body, ok, status) {
      if (timers.length) {
        timers.shift()();
        await flush();
      }
      polls.at(-1).respond(json(body, ok, status));
      await flush();
    },
    submit() { get('prompt-form').onsubmit({ preventDefault }); },
    mode(value) {
      get('mode').value = value;
      get('mode').onchange();
    },
  };
}

async function testReferenceAndRecorded() {
  const ui = boot({ backend_configured: true });
  const { get, posts, drawings } = ui;
  await flush();
  assert.equal(posts.length, 0, 'the composer loads without automatically starting a replay');
  get('sample-prompt').value = 'ev-electricity-price';
  get('sample-prompt').onchange();
  get('load-example').onclick();
  assert.equal(posts.length, 0, 'loading sample text does not start a run');
  get('recorded-example').onclick();
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
  assert.equal(posts.length, 0);
  assert.equal(get('generate').disabled, true);
  assert.deepEqual(get('sample-prompt').children.map(option => option.value), ['ev-charge-range']);
  get('load-example').onclick();
  ui.submit();
  assert.deepEqual(posts[0].payload, { mode: 'live', prompt: liveExamplePrompt });
  assert.equal(get('problem-prompt').readOnly, true);
  assert.equal(get('pipeline-stages').hidden, true);
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
  assert.equal(posts.length, 0, 'planning requires explicit submission');
  assert.equal(get('problem-prompt').value, '');
  assert.equal(get('problem-prompt').readOnly, false);
  assert.equal(get('generate').textContent, 'Run pipeline');
  const picker = get('sample-prompt');
  assert.deepEqual(picker.children.map(option => [option.value, option.textContent]),
    sampleCatalog.map(sample => [sample.id, sample.label]));
  assert.equal(picker.value, 'ev-charge-range');
  get('load-example').onclick();
  assert.equal(get('problem-prompt').value, liveExamplePrompt);

  picker.value = 'ev-electricity-price';
  picker.onchange();
  assert.equal(get('problem-prompt').value, liveExamplePrompt, 'selecting a sample leaves the draft intact');
  get('load-example').onclick();
  assert.equal(get('problem-prompt').value, pricePrompt);
  assert.equal(posts.length, 0);

  const prompt = pricePrompt + 'Keep the existing delivery windows.';
  get('problem-prompt').value = prompt;
  ui.submit();
  assert.deepEqual([posts[0].path, posts[0].payload], ['/api/pipeline-runs', { prompt }]);
  assert.equal(get('progress-screen').hidden, false);
  assert.equal(get('results-screen').hidden, true);
  await ui.respond({ id: 'pipeline-test', status: 'queued', stage: null, attempt: 0 });
  assert.match([...ui.storage.values()][0], /pipeline-test/);
  await ui.poll({ id: 'pipeline-test', status: 'running', stage: 'build', attempt: 1 });
  assert.equal(ui.polls[0].path, '/api/pipeline-runs/pipeline-test');
  const stages = get('pipeline-stages').children;
  assert.equal(stages[2].attrs['aria-current'], 'step');
  assert.equal(stages[1].attrs['data-state'], 'complete');
  await ui.poll({ error: 'Try later.' }, false, 503);
  assert.match(get('pipeline-status').textContent, /Reconnecting/);
  await ui.poll({ id: 'pipeline-test', status: 'running', stage: 'plan', attempt: 2 });
  assert.equal(stages[1].children[1].textContent, 'In progress · Attempt 2');
  assert.equal(stages[2].attrs['data-state'], 'pending', 'a revision resets downstream stages');
  await ui.poll({ id: 'pipeline-test', status: 'running', stage: 'verify', attempt: 2 });
  assert.equal(get('pipeline-status').textContent, 'Verifying the result…');

  const planned = liveFixture(35, 'planned', prompt);
  planned.variables = [{ ...chargeRange, group: 'Battery' },
    { id: 'price', label: 'Charging price', type: 'number', minimum: 0, maximum: 2,
      step: 0.05, default: 0.2, unit: '$/kWh', group: 'Costs', rerun_mode: 'rebuild' },
    { id: 'automation', label: 'Allow automation', type: 'boolean', default: false, rerun_mode: 'bind' },
  ];
  planned.effective_values = { [chargeKey]: 35, price: 0.2, automation: false };
  await ui.poll({ id: 'pipeline-test', status: 'completed', stage: 'verify', attempt: 2, result: planned });
  assert.equal(get('progress-screen').hidden, true);
  assert.equal(get('results-screen').hidden, false);
  assert.equal(get('original-prompt').open, false);
  assert.equal(get('prompt').textContent, prompt);
  assert.equal(get('score').textContent, '$15.78');
  assert.equal(ui.storage.size, 0);
  const [battery, costs] = get('variables').children;
  assert.equal(battery.children[0].textContent, 'Battery');
  assert.equal(battery.open, true);
  assert.equal(costs.children[0].textContent, 'Costs');
  assert.equal(costs.open, false);
  ui.chooseCharge(28);
  assert.deepEqual(posts[1].payload, {
    mode: 'live', view_id: 'view-planned', variables: { [chargeKey]: 28, price: 0.2, automation: false },
  });
  const recalculated = liveFixture(28, 'range-rerun', prompt);
  recalculated.variables = planned.variables;
  recalculated.effective_values = posts[1].payload.variables;
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
  assert.equal(posts.length, 2, 'other draft values wait for Apply even after a charge commit');
  get('rerun').onclick();
  assert.deepEqual(posts[2].payload, {
    mode: 'live', view_id: 'view-range-rerun', variables: { [chargeKey]: 24, price: 0.25, automation: true },
  });
}

async function testResumedPipelineClarification() {
  const prompt = 'Deliver packages with our electric vans.';
  const storage = new Map([['fleet-lab-pending-pipeline', JSON.stringify({ id: 'resume-me', prompt })]]);
  const ui = boot({ pipeline_backend_configured: true }, liveExamplePrompt, storage);
  const { get, posts, polls } = ui;
  await flush();
  assert.equal(get('progress-screen').hidden, false);
  assert.equal(posts.length, 0, 'a reload polls instead of launching a duplicate run');
  assert.equal(polls[0].path, '/api/pipeline-runs/resume-me');
  await ui.poll({ id: 'resume-me', status: 'failed', stage: 'plan', attempt: 1,
    error: 'Add these details to the original prompt and run again. Where are the deliveries?',
    history: [{ id: 1, stage: 'plan', attempt: 1, status: 'failed',
      steps: [{ id: 1, label: 'Clarify delivery locations', status: 'failed', detail: 'Where are the deliveries?' }] }]
  });
  assert.equal(get('prompt-screen').hidden, false);
  assert.equal(get('progress-screen').hidden, true);
  assert.equal(get('results-screen').hidden, true);
  assert.match(get('composer-error').textContent, /Where are the deliveries\?/);
  assert.equal(get('problem-prompt').value, prompt);
  assert.equal(storage.size, 0);
  assert.equal(get('failed-pipeline-progress').hidden, false);
  get('failed-pipeline-progress').onclick();
  assert.equal(get('progress-screen').hidden, false);
  assert.equal(get('progress-return').textContent, 'Back to problem');
  assert.equal(get('pipeline-status').textContent, get('composer-error').textContent);
  get('progress-return').onclick();
  assert.equal(get('problem-prompt').value, prompt);
  assert.equal(get('prompt-screen').hidden, false);
  get('problem-prompt').value = prompt + ' The deliveries are at (1, 2) and (4, 2).';
  ui.submit();
  assert.deepEqual(posts[0].payload, { prompt: get('problem-prompt').value });
  assert.equal(get('pipeline-visits').children.length, 0, 'a fresh run starts a new stage history');
}

async function testDetailedPipelineProgress() {
  const ui = boot({ pipeline_backend_configured: true });
  const { get } = ui;
  await flush();
  const prompt = 'Schedule the electric vans and check the deadline for each delivery.';
  get('problem-prompt').value = prompt;
  ui.submit();
  const id = 'inspection';
  await ui.respond({ id, status: 'queued', stage: null, attempt: 0, history: [] });
  const history = [
    { id: 1, stage: 'init', attempt: 1, status: 'completed',
      steps: [{ id: 1, label: 'Read problem statement', status: 'completed' }] },
    { id: 2, stage: 'plan', attempt: 1, status: 'completed',
      steps: [{ id: 2, label: 'Review solver plan', status: 'completed' }] },
    { id: 3, stage: 'build', attempt: 1, status: 'running',
      steps: [{ id: 3, label: 'Write solver', status: 'running' }] },
  ];
  await ui.poll({ id, status: 'running', stage: 'build', attempt: 1, history });
  const visits = get('pipeline-visits');
  const toggle = get('pipeline-detail-toggle');
  const firstBuild = visits.children[2].children[0];
  assert.equal(get('pipeline-stages').hidden, true);
  assert.equal(firstBuild.open, true, 'fine details are visible by default');
  assert.equal(toggle.hidden, false);
  assert.equal(toggle.textContent, 'Hide all steps');
  toggle.onclick();
  assert.equal(firstBuild.open, false);
  assert.equal(toggle.textContent, 'Show all steps');

  const firstStep = firstBuild.children[1].children[0];
  const repair = 'Van 4 missed its delivery deadline.\nRecheck the service time.';
  const revisedHistory = [
    ...history.slice(0, 2),
    { ...history[2], status: 'completed', steps: [{ ...history[2].steps[0], status: 'completed' }] },
    { id: 4, stage: 'test', attempt: 1, status: 'revising',
      steps: [{ id: 4, label: 'Check schedule constraints', status: 'failed', detail: repair }] },
    { id: 5, stage: 'build', attempt: 2, status: 'completed',
      steps: [{ id: 5, label: 'Update time-window constraints', status: 'completed' }] },
    { id: 6, stage: 'test', attempt: 2, status: 'running',
      steps: [{ id: 6, label: 'Check schedule constraints', status: 'running' }] },
  ];
  await ui.poll({ id, status: 'running', stage: 'test', attempt: 2, history: revisedHistory });
  const disclosures = visits.children.map(row => row.children[0]);
  assert.deepEqual(disclosures.map(element => element.children[0].children[0].children[0].textContent),
    ['Parse prompt', 'Plan solution', 'Build solver', 'Run solver', 'Build solver', 'Run solver']);
  assert.equal(visits.children[2].children[0], firstBuild, 'polling preserves the existing disclosure and focus');
  assert.equal(firstBuild.children[1].children[0], firstStep, 'updates use the same step element');
  assert.equal(firstStep.children[1].textContent, 'Done');
  const [firstTest, retryBuild, retryTest] = disclosures.slice(3);
  assert.equal(firstTest.children[0].children[1].textContent, 'Revision needed');
  assert.equal(firstTest.children[1].children[0].children[2].textContent, repair);
  assert.equal(retryBuild.children[0].children[0].children[1].textContent, 'Attempt 2');
  assert.equal(retryTest.children[0].attrs['aria-current'], 'step');
  assert.ok(disclosures.every(element => !element.open), 'new visits inherit the hide-all preference');
  toggle.onclick();
  assert.ok(disclosures.every(element => element.open), 'the details for every attempt can be shown at once');
  toggle.onclick();
  assert.ok(disclosures.every(element => !element.open));
  assert.equal(visits.hidden, false, 'hiding details keeps all stage attempts visible');
  firstTest.open = true;
  firstTest.ontoggle();
  assert.equal(toggle.textContent, 'Hide all steps');
  assert.equal(firstBuild.open, false, 'any attempt can be inspected independently');

  const complete = [
    ...revisedHistory.slice(0, 5),
    { ...revisedHistory[5], status: 'completed', steps: [{ ...revisedHistory[5].steps[0], status: 'completed' }] },
    { id: 7, stage: 'verify', attempt: 1, status: 'completed',
      steps: [{ id: 7, label: 'Approve verified result', status: 'completed' }] },
  ];
  await ui.poll({ id, status: 'completed', stage: 'verify', attempt: 1, history: complete,
    result: liveFixture(35, 'detailed', prompt) });
  assert.equal(get('results-screen').hidden, false);
  assert.equal(get('result-pipeline-progress').hidden, false);
  get('result-pipeline-progress').onclick();
  assert.equal(get('progress-screen').hidden, false);
  assert.equal(get('progress-return').textContent, 'Back to results');
  assert.equal(get('pipeline-status').textContent, 'Pipeline complete.');
  assert.equal(firstTest.open, true);
  assert.equal(visits.children.length, 7);
  get('progress-return').onclick();
  assert.equal(get('results-screen').hidden, false);
}

async function testExternalBackend() {
  const ui = boot({ backend_configured: true });
  const { get, posts } = ui;
  await flush();
  const prompt = 'Dispatch a fleet using an external backend.';
  get('problem-prompt').value = prompt;
  ui.submit();
  assert.deepEqual([posts[0].path, posts[0].payload], ['/api/run', { mode: 'live', prompt }]);
  assert.equal(get('pipeline-stages').hidden, true);
  await ui.respond({ error: 'The external solver is unavailable.' }, false);
  assert.equal(get('prompt-screen').hidden, false);
  assert.equal(get('composer-error').textContent, 'The external solver is unavailable.');
  assert.equal(get('problem-prompt').value, prompt);
}

async function testLiveExampleFallback() {
  const ui = boot({ backend_configured: true, local_backend_configured: true }, null, undefined, null);
  await flush();
  assert.equal(ui.get('sample-prompt').children.length, 1);
  ui.get('load-example').onclick();
  ui.submit();
  assert.deepEqual(ui.posts[0].payload, { mode: 'live', prompt: examplePrompt });
  await ui.respond(fixture(35, 'legacy', 'live'));
}

async function testSavedRunHistory(canRecalculate) {
  const ui = boot(canRecalculate ? { backend_configured: true, saved_run_recalculation_configured: true }
    : { backend_configured: true, local_backend_configured: true });
  const { get, posts, historyReads } = ui;
  await flush();
  const verified = { id: 'saved-fleet', title: 'Morning parcels', status: 'verified',
    created_at: '2026-11-15T09:00:00Z', can_open: true };
  const unfinished = { id: 'draft-fleet', title: 'Evening parcels', status: 'incomplete',
    created_at: '2026-11-14T18:30:00Z', can_open: false };
  const prompt = 'Dispatch from (2, 3).\nMinimize charging cost.';
  const detail = { ...verified, prompt,
    artifacts: [{ key: 'entry point', label: 'Generated solver', filename: 'main.py' }] };

  get('run-history').onclick();
  assert.equal(get('history-screen').hidden, false);
  assert.equal(historyReads[0].path, '/api/workspaces');
  await ui.readHistory({ workspaces: [verified, unfinished] });
  const [first, second] = get('history-list').children.map(row => row.children[0]);
  assert.equal(first.children[0].textContent, verified.title);
  assert.equal(first.children[1].textContent, 'Verified');
  assert.ok(first.children[2].textContent);
  assert.equal(second.children[1].textContent, 'Unfinished');
  first.onclick();
  second.onclick();
  assert.deepEqual(historyReads.slice(1).map(call => call.path), [
    '/api/workspaces/saved-fleet', '/api/workspaces/draft-fleet',
  ]);
  await ui.readHistory({ ...unfinished, prompt: 'Pending prompt', artifacts: [] }, 2);
  await ui.readHistory(detail, 1);
  assert.equal(get('history-detail-heading').textContent, unfinished.title);
  assert.equal(get('history-open').hidden, true);
  assert.equal(get('history-artifact-section').hidden, true);

  first.onclick();
  await ui.readHistory(detail);
  assert.equal(get('history-prompt').textContent, prompt);
  const link = get('history-artifacts').children[0].children[0];
  assert.equal(link.href, '/api/workspaces/saved-fleet/artifacts/entry%20point');
  assert.equal(link.download, 'main.py');
  get('history-open').onclick();
  assert.deepEqual([posts[0].path, posts[0].payload], ['/api/workspaces/saved-fleet/open', {}]);
  const data = { ...liveFixture(35, 'reopened', prompt), plan_id: verified.id, provenance: 'recorded',
    workspace_origin: 'local', source: { workspace_id: verified.id, file: 'handoff.json' } };
  await ui.respond(data);
  assert.equal(get('results-screen').hidden, false);
  assert.equal(get('run-badge').textContent, 'Saved run');
  assert.equal(get(chargeSliderId).disabled, !canRecalculate);
  assert.equal(get('saved-run-note').hidden, canRecalculate);

  get('run-history').onclick();
  await ui.readHistory({ workspaces: [verified, unfinished] });
  assert.equal(get('history-screen').hidden, false);
  get('history-new-problem').onclick();
  assert.equal(get('prompt-screen').hidden, false);
  assert.equal(posts.length, 1, 'opening a saved run never starts a pipeline');
}

async function testSixVanResult() {
  const ui = boot({ backend_configured: true });
  await flush();
  const result = fixture();
  const { simulation } = result;
  simulation.grid = { width: 12, height: 12 };
  simulation.chargers.push({ id: 'C2', position: [6, 5] }, { id: 'C3', position: [5, 6] });
  for (const id of ['V3', 'V4', 'V5', 'V6']) {
    simulation.vans.push({ id, initial_battery_kwh: 4, events: [] });
  }
  simulation.vans.at(-1).events.push({
    type: 'charge', charger_id: 'C3', position: [5, 6],
    start_min: 30, end_min: 60, battery_start_kwh: 4, battery_end_kwh: 9,
  });

  ui.mode('reference');
  await ui.respond(result);

  const cards = ui.get('fleet').children;
  const fleetColors = cards.map(card => card.style['--van']);
  assert.equal(cards.length, 6);
  assert.equal(new Set(fleetColors).size, 6);
  const timelineRows = ui.get('timeline').children.slice(1);
  assert.deepEqual(timelineRows.map(row => row.children[0].textContent),
    ['V1', 'V2', 'V3', 'V4', 'V5', 'V6', 'C1', 'C2', 'C3']);
  const booking = timelineRows.at(-1).children[1].children[0];
  assert.match(booking.attrs['aria-label'], /C3 · V6 · Charging/);
  assert.equal(booking.style['--van'], fleetColors.at(-1));
}

async function main() {
  await testReferenceAndRecorded();
  await testLocalSolver();
  await testPlannerControls();
  await testDetailedPipelineProgress();
  await testResumedPipelineClarification();
  await testExternalBackend();
  await testLiveExampleFallback();
  await testSavedRunHistory(false);
  await testSavedRunHistory(true);
  await testSixVanResult();
  console.log('PASS: canvas playback, saved run history, verified result swaps, and typed planner controls');
}

main().catch(error => {
  console.error(error);
  process.exitCode = 1;
});
