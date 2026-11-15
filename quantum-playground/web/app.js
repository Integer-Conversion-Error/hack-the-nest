const $ = id => document.getElementById(id);
const colors = ['#185f55', '#315b8f', '#8a4d27', '#75527c'];
const provenanceLabels = {
  classical_reference: 'Exact reference',
  live: 'Live run',
  recorded: 'Recorded backend run',
};

const runModes = {
  recorded: {
    action: 'Load recorded example',
    pending: 'Loading the recorded result…',
  },
  reference: {
    action: 'Load exact reference',
    pending: 'Loading the exact reference…',
  },
  live: {
    action: 'Run backend',
    pending: 'Waiting for a verified backend result…',
  },
};

let inspected = null;
let result = null, sim = null, busy = false, initialized = false;
let backendConfigured = false, localBackendConfigured = false;
let playing = false, minute = 0, last = null, selectedVan = null;
let examplePrompt = '', livePrompt = '';
let controlPanel = null;
let vanCards = [];
const mapCanvas = $('fleet-map');
const map = FleetMap.create(mapCanvas, { colors, onInspect: inspect });

const node = (tag, text, cls) => {
  const element = document.createElement(tag);
  if (text !== undefined) element.textContent = text;
  if (cls) element.className = cls;
  return element;
};
const money = amount => Number.isFinite(amount) ? '$' + amount.toFixed(2) : '—';
const costLabels = { driving: 'Driving', electricity: 'Electricity', waiting: 'Waiting', late: 'Lateness' };
const eventLabels = { drive: 'Driving', wait: 'Waiting', charge: 'Charging', deliver: 'Delivery' };

function writeText(element, text) {
  if (element.textContent !== text) element.textContent = text;
}

function error(message) {
  $('error').textContent = message;
  $('error').hidden = false;
  $('run-status').hidden = false;
}

function setStatus(message) {
  $('status').textContent = message;
  $('status').hidden = !message;
  $('run-status').hidden = !message && $('error').hidden;
}

function controls(definitions, applied) {
  controlPanel = ScenarioControls.render($('variables'), definitions, applied, {onChange: controlChanged});
  $('settings').hidden = !definitions.length;
  $('applied-settings').textContent = 'Applied · ' + controlPanel.displayApplied();
}

function usesLocalSolver() {
  return localBackendConfigured && $('mode').value === 'live';
}

function usesImmediateApply() {
  return usesLocalSolver() && Boolean(controlPanel?.automatic);
}

function updateButtons() {
  const selectedMode = $('mode').value;
  const isLive = selectedMode === 'live';
  const localLive = usesLocalSolver();
  const immediate = usesImmediateApply();
  const canRun = !isLive || backendConfigured;
  const canEditPrompt = isLive && !localLive;
  const hasCurrentResult = result?.mode === selectedMode;
  const canApply = hasCurrentResult && result.variables.length > 0;
  const draft = canApply ? controlPanel.values() : null;
  const hasChanges = draft && Object.entries(draft).some(([id, value]) => result.effective_values[id] !== value);

  $('generate').disabled = !initialized || busy || !canRun;
  $('run-action').hidden = !initialized || !canRun || busy || (hasCurrentResult && !canEditPrompt);
  $('load-example').hidden = !canEditPrompt;
  $('load-example').disabled = !initialized || busy;
  $('mode').disabled = !initialized || busy;
  $('prompt').readOnly = !canEditPrompt || busy;
  $('applied-settings').hidden = !canApply || (draft !== null && !hasChanges);
  $('rerun').disabled = busy || !hasChanges;
  $('rerun').hidden = !canApply || (immediate && (busy || !hasChanges || $('error').hidden));
  $('rerun').textContent = immediate ? 'Retry solver'
    : selectedMode === 'recorded' ? 'Load recorded result' : 'Apply changes';
  controlPanel?.setDisabled(busy || !canApply);
}

function controlChanged() {
  updateButtons();
  if (usesImmediateApply() && !$('rerun').disabled) submitControlChanges();
}

async function request(path, data) {
  const response = await fetch(path, {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify(data),
  });
  const body = await response.json();
  if (!response.ok) throw Error(body.error || 'Request failed.');
  return body;
}

function clock(m) {
  const total = Math.round((sim?.start_hour ?? 8) * 60 + m);
  return String(Math.floor(total / 60)).padStart(2, '0') + ':' + String(total % 60).padStart(2, '0');
}

function setPlaying(value) {
  playing = value;
  $('play').textContent = value ? 'Pause' : 'Play';
  $('play').setAttribute('aria-pressed', String(value));
}

function acceptResult(data, payload) {
  if (!data) throw Error('The service did not return a verified EV fleet result.');
  const ids = [data.view_id, data.run_id, data.plan_id, data.attempt_id];
  if (data.status !== 'completed' || data.mode !== payload.mode ||
      !Object.hasOwn(provenanceLabels, data.provenance) ||
      (payload.mode === 'recorded' && data.provenance !== 'recorded') ||
      (payload.mode === 'reference' && data.provenance !== 'classical_reference') ||
      ids.some(id => typeof id !== 'string' || !id) ||
      data.evaluation?.feasible !== true || !Number.isFinite(data.evaluation.objective_value) ||
      !FleetReplay.validate(data.simulation)) {
    throw Error('The service did not return a verified EV fleet result.');
  }
  if (!data.effective_values || !Array.isArray(data.variables) || data.variables.some(control =>
    !ScenarioControls.isValidValue(control, data.effective_values[control.id]))) {
    throw Error('The service returned invalid approved control values.');
  }
  if (payload.view_id && (payload.view_id !== result.view_id || data.run_id !== result.run_id || data.plan_id !== result.plan_id ||
      data.attempt_id === result.attempt_id || data.view_id === result.view_id ||
      Object.entries(payload.variables).some(([id, value]) => data.effective_values?.[id] !== value))) {
    throw Error('The recalculation did not match the requested plan and settings.');
  }
}

const updater = createLatestUpdater({
  execute: payload => request('/api/run', payload),
  onBusy: value => {
    busy = value;
    updateButtons();
  },
  onResult: (data, payload) => {
    acceptResult(data, payload);
    const previous = result;
    render(data, {preserve: !!payload.view_id});
    result = data;
    renderChanges(data.evaluation, payload.view_id ? previous?.evaluation : null);
    setStatus('');
  },
  onError: e => {
    error(e.message);
    setStatus(result ? 'Showing the last result' : '');
    if (!sim) $('frame-status').textContent = 'No verified schedule yet';
  }
});

function queueRun(payload, status) {
  $('error').hidden = true;
  setStatus(status);
  if (!sim) $('frame-status').textContent = status;
  updater.schedule(payload, payload);
}

function initialRun() {
  const mode = $('mode').value;
  const prompt = mode === 'live' ? $('prompt').value : examplePrompt;
  if (!prompt.trim()) {
    $('original-prompt').open = true;
    $('prompt').reportValidity();
    return;
  }
  if ($('generate').disabled) return;
  queueRun({mode, prompt}, usesLocalSolver() ? 'Running solver…' : runModes[mode].pending);
}

$('prompt-form').onsubmit = event => {
  event.preventDefault();
  initialRun();
};

function submitControlChanges() {
  if ($('rerun').disabled) return;
  const pending = usesLocalSolver()
    ? 'Running solver…'
    : result.mode === 'recorded' ? 'Loading the recorded result…' : 'Recalculating…';
  queueRun({mode: result.mode, view_id: result.view_id, variables: controlPanel.values()}, pending);
}
$('rerun').onclick = submitControlChanges;

function updateMode() {
  const mode = $('mode').value;
  const localLive = usesLocalSolver();
  $('prompt').value = mode === 'live' && !localLive ? livePrompt : examplePrompt;
  $('generate').textContent = localLive ? 'Run solver' : runModes[mode].action;
  updateButtons();
}

$('mode').onchange = () => {
  if (!$('prompt').readOnly) livePrompt = $('prompt').value;
  $('error').hidden = true;
  setStatus('');
  updateMode();
};
$('load-example').onclick = () => { $('prompt').value = examplePrompt; };

function render(data, {preserve = false} = {}) {
  const saved = {minute, playing, selectedVan};
  sim = data.simulation;
  if (!preserve || (inspected && !entities(inspected.kind).some(item => item.id === inspected.id))) inspected = null;
  selectedVan = preserve && sim.vans.some(van => van.id === saved.selectedVan) ? saved.selectedVan : null;
  minute = preserve ? Math.min(saved.minute, sim.duration_min) : 0;
  setPlaying(preserve && saved.playing && minute < sim.duration_min);

  const engineLabel = {
    'quantum-rings': 'Quantum Rings',
    'numpy-statevector': 'Local simulator',
  }[data.quantum?.engine];
  $('run-badge').textContent = data.provenance === 'live' && engineLabel
    ? 'Live · ' + engineLabel
    : provenanceLabels[data.provenance];
  controls(data.variables || [], data.effective_values || {});

  $('circuits').replaceChildren();
  const drawings = (data.circuits || []).filter(circuit => circuit.drawing);
  for (const circuit of drawings) {
    const card = node('article', undefined, 'circuit-card');
    card.append(node('h3', circuit.title || 'Circuit drawing'), node('pre', circuit.drawing));
    $('circuits').append(card);
  }
  $('circuit-panel').hidden = !drawings.length;

  const quantum = data.quantum || {};
  const quantumDetails = [
    quantum.method,
    Number.isFinite(quantum.binary_variables) ? quantum.binary_variables + ' binary decisions' : null,
    Number.isFinite(quantum.actual_qubits) ? quantum.actual_qubits + ' executed qubits' : null,
    Number.isFinite(quantum.shots) ? quantum.shots + ' shots' : null,
    typeof quantum.best_bitstring === 'string' ? 'Selected bitstring ' + quantum.best_bitstring : null,
  ];
  $('quantum-summary').textContent = quantumDetails.filter(Boolean).join(' · ');
  $('histogram').replaceChildren();
  const counts = Object.entries(sim.counts || {});
  const total = counts.reduce((sum, [, count]) => sum + Number(count), 0);
  for (const [bits, count] of counts) {
    const row = node('div', undefined, 'bar-row');
    const track = node('div', undefined, 'bar-track');
    const bar = node('div', undefined, 'bar');
    bar.style.width = (total ? Number(count) / total * 100 : 0) + '%';
    track.append(bar);
    row.append(node('span', bits), track, node('span', count));
    $('histogram').append(row);
  }
  $('quantum-details').hidden = !counts.length && !$('quantum-summary').textContent;

  $('assumptions').replaceChildren(...(data.assumptions || []).map(value => node('li', value)));
  const evaluation = data.evaluation;
  $('score').textContent = money(evaluation.objective_value);
  $('feasible').textContent = 'Checks passed';
  $('reference-value').hidden = data.provenance === 'classical_reference' || !Number.isFinite(evaluation.reference_value);
  $('reference-score').textContent = money(evaluation.reference_value);
  $('reference-gap').hidden = data.provenance === 'classical_reference' || !Number.isFinite(evaluation.gap);
  $('gap-score').textContent = money(evaluation.gap);
  $('costs').replaceChildren(...Object.entries(evaluation.costs || {})
    .map(([label, value]) => node('span', label + ' · ' + money(value))));
  $('explanation').textContent = data.explanation || '';
  $('result-source').hidden = !data.source?.file;
  $('result-source').textContent = data.source?.file
    ? 'Source · ' + [data.source.workspace_id, data.source.file].filter(Boolean).join(' / ')
    : '';
  $('start-time').textContent = clock(0);
  $('end-time').textContent = clock(sim.duration_min);
  $('scrub').max = sim.duration_min;
  for (const id of ['play', 'restart', 'scrub', 'export', 'feedback']) $(id).disabled = false;
  buildFleet();
  buildTimeline();
  applyVanSelection();
  map.setSimulation(sim);
  mapCanvas.hidden = false;
  $('frame-status').hidden = true;
  updatePlayback();
}
function renderChanges(current, previous) {
  const deltaList = $('cost-delta');
  deltaList.replaceChildren();
  const delta = current.objective_value - previous?.objective_value;
  $('change-summary').textContent = Number.isFinite(delta)
    ? (Math.abs(delta) < 0.005 ? 'Cost unchanged' : (delta > 0 ? '+' : '−') + money(Math.abs(delta)) + ' vs previous run')
    : '';
  if (!previous) return;

  for (const [key, value] of Object.entries(current.costs || {})) {
    const change = value - (previous.costs?.[key] ?? 0);
    if (Math.abs(change) < 0.005) continue;
    const signed = (change > 0 ? '+' : '−') + money(Math.abs(change));
    deltaList.append(node('li', (costLabels[key] || key) + ' ' + signed));
  }
}

function buildFleet() {
  $('fleet').replaceChildren();
  vanCards = sim.vans.map((van, index) => {
    const card = node('button', undefined, 'van-card');
    const status = node('span', '', 'van-state');
    const energy = node('span', '', 'van-energy');
    const track = node('div', undefined, 'battery-track');
    const fill = node('div', undefined, 'battery-fill');
    const color = colors[index % colors.length];
    card.type = 'button';
    card.style.setProperty('--van', color);
    card.append(node('strong', van.name || van.id), status, energy, track);
    track.append(fill);
    card.onclick = () => inspect({kind: 'van', id: van.id});
    $('fleet').append(card);
    return {card, status, energy, fill, color};
  });
}

function buildTimeline() {
  const timeline = $('timeline');
  const axis = node('div', undefined, 'time-axis');
  for (let i = 0; i <= 4; i++) axis.append(node('span', clock(sim.duration_min * i / 4)));
  timeline.replaceChildren(axis);

  function addRow(label, events, target, color) {
    const row = node('div', undefined, 'timeline-row');
    const track = node('div', undefined, 'timeline-track');
    row.append(node('span', label), track);
    for (const event of events) {
      const start = Math.max(0, event.start_min);
      const end = Math.min(sim.duration_min, event.end_min);
      if (end < start) continue;
      const block = node('button', undefined, 'event-block ' + event.type);
      const range = clock(start) + '–' + clock(end);
      block.type = 'button';
      block.style.left = start / sim.duration_min * 100 + '%';
      block.style.width = (end - start) / sim.duration_min * 100 + '%';
      block.style.setProperty('--van', color || event.color);
      block.title = [label, eventLabels[event.type], range, event.charger_id || event.delivery_id].filter(Boolean).join(' · ');
      block.setAttribute('aria-label', block.title);
      block.onclick = () => {
        setInspection(target);
        seek(start);
      };
      track.append(block);
    }
    track.append(node('div', undefined, 'time-cursor'));
    timeline.append(row);
  }

  sim.vans.forEach((van, i) => addRow(van.id, van.events, {kind: 'van', id: van.id}, colors[i % colors.length]));
  for (const charger of sim.chargers) {
    const bookings = chargerBookings(charger.id);
    addRow(charger.id, bookings, {kind: 'charger', id: charger.id});
  }
}

function updatePlayback() {
  if (!sim) return;
  map.draw(minute, selectedVan, playing);
  writeText($('clock'), clock(minute));
  $('scrub').value = String(minute);
  $('scrub').setAttribute('aria-valuetext', clock(minute));
  const done = FleetReplay.deliveredAt(sim, minute);
  writeText($('delivered'), done.size + ' / ' + sim.deliveries.length);
  sim.vans.forEach((van, i) => {
    const state = FleetReplay.stateAt(van, minute, sim.depot);
    const {status, energy, fill, color} = vanCards[i];
    const percent = state.battery / sim.battery_capacity_kwh * 100;
    writeText(status, state.status);
    writeText(energy, state.battery.toFixed(1) + ' kWh · ' + percent.toFixed(0) + '%');
    fill.style.width = Math.max(0, Math.min(100, percent)) + '%';
    fill.style.background = percent < sim.reserve_pct ? 'var(--danger)' : color;
  });
  $('timeline').style.setProperty('--playhead', minute / sim.duration_min * 100 + '%');
  updateInspector();
}

function seek(to) {
  if (!sim) return;
  minute = Math.max(0, Math.min(sim.duration_min, to));
  setPlaying(false);
  updatePlayback();
}

$('play').onclick = () => {
  if (!sim) return;
  if (minute >= sim.duration_min) minute = 0;
  setPlaying(!playing);
  updatePlayback();
};
$('restart').onclick = () => seek(0);
$('scrub').oninput = () => seek(Number($('scrub').value));

function tick(now) {
  if (playing && sim && last !== null) {
    const elapsed = Math.min(0.1, (now - last) / 1000);
    minute = Math.min(sim.duration_min, minute + elapsed * Number($('speed').value));
    if (minute >= sim.duration_min) setPlaying(false);
    updatePlayback();
  }
  last = now;
  requestAnimationFrame(tick);
}
requestAnimationFrame(tick);
window.addEventListener('pageshow', event => {
  if (event.persisted) {
    last = null;
    updatePlayback();
  }
});

function entities(kind) {
  return {van: sim.vans, charger: sim.chargers, delivery: sim.deliveries}[kind];
}

function applyVanSelection() {
  vanCards.forEach(({card}, i) => {
    const active = selectedVan === sim.vans[i].id;
    card.classList.toggle('selected', active);
    card.setAttribute('aria-pressed', String(active));
  });
}

function setInspection(target) {
  inspected = target;
  selectedVan = target?.kind === 'van' ? target.id : null;
  applyVanSelection();
}

function inspect(target) {
  const alreadySelected = inspected?.kind === target.kind && inspected?.id === target.id;
  setInspection(alreadySelected ? null : target);
  updatePlayback();
}

function chargerBookings(chargerId) {
  return sim.vans.flatMap((van, i) => van.events
    .filter(event => event.type === 'charge' && event.charger_id === chargerId)
    .map(event => ({...event, van: van.id, color: colors[i % colors.length]})))
    .sort((first, second) => first.start_min - second.start_min);
}

function updateInspector() {
  const panel = $('pygame-inspector');
  panel.hidden = !inspected;
  if (!inspected) return;
  const {kind, id} = inspected;
  const item = entities(kind).find(candidate => candidate.id === id);
  if (!item) {
    panel.hidden = true;
    return;
  }

  let details;
  if (kind === 'van') {
    const state = FleetReplay.stateAt(item, minute, sim.depot);
    const upcoming = item.events.find(event => event.end_min > minute);
    const percent = (state.battery / sim.battery_capacity_kwh * 100).toFixed(0);
    details = [item.name || id, state.status, state.battery.toFixed(2) + ' kWh (' + percent + '%)',
      item.delivery_ids?.length ? 'Deliveries ' + item.delivery_ids.join(', ') : '',
      item.option_id ? 'Option: ' + item.option_id : '',
      upcoming ? 'Next event ends ' + clock(upcoming.end_min) : ''];
  } else if (kind === 'charger') {
    const bookings = chargerBookings(id);
    const active = bookings.find(event => event.start_min <= minute && minute < event.end_min);
    const reservations = bookings.map(event => event.van + ' ' + clock(event.start_min) + '–' + clock(event.end_min));
    details = [id, active ? 'Charging ' + active.van + ' until ' + clock(active.end_min) : 'No charging scheduled at this time',
      'Reservations: ' + (reservations.join(', ') || 'none')];
  } else {
    const van = sim.vans.find(candidate => candidate.events.some(event => event.delivery_id === id));
    const event = van?.events.find(candidate => candidate.delivery_id === id);
    const done = FleetReplay.deliveredAt(sim, minute).has(id);
    const lateness = event && Math.max(0, event.end_min - item.deadline_min);
    details = [id + ' at (' + item.position.join(', ') + ')', done ? 'Delivered' : 'Pending',
      van?.id || 'Unassigned', event ? 'Service completes ' + clock(event.end_min) : '',
      Number.isFinite(item.deadline_min) ? 'Deadline ' + clock(item.deadline_min) : '',
      lateness > 0 ? lateness.toFixed(1).replace(/\.0$/, '') + ' min late' : ''];
  }
  writeText(panel, details.filter(Boolean).join(' | '));
}

mapCanvas.onkeydown = event => {
  if (!sim) return;
  const kind = {v: 'van', c: 'charger', d: 'delivery'}[event.key.toLowerCase()];
  if (kind) {
    const items = entities(kind);
    if (!items.length) return;
    const i = inspected?.kind === kind ? items.findIndex(item => item.id === inspected.id) : -1;
    event.preventDefault();
    inspect({kind, id: items[(i + 1) % items.length].id});
    return;
  }
  const step = event.shiftKey ? 15 : 5;
  const actions = {
    ' ': () => $('play').onclick(), ArrowLeft: () => seek(minute - step),
    ArrowRight: () => seek(minute + step), Home: () => seek(0), End: () => seek(sim.duration_min),
    Escape: () => { setInspection(null); updatePlayback(); },
  };
  if (Object.hasOwn(actions, event.key)) {
    event.preventDefault();
    actions[event.key]();
  }
};
$('export').onclick=()=>{if(!result)return;const url=URL.createObjectURL(new Blob([JSON.stringify(result,null,2)],{type:'application/json'})),a=node('a');a.href=url;a.download=`ev-dispatch-${result.run_id}-${result.attempt_id}.json`;a.click();setTimeout(()=>URL.revokeObjectURL(url),1000);};
$('feedback').onclick=async()=>{if(!result)return;const feedback=prompt('Feedback is saved for this server session. What should change?');if(!feedback)return;try{await request('/api/feedback',{view_id:result.view_id,feedback});setStatus('Feedback saved locally');}catch(e){error(e.message);}};
async function init() {
  try {
    const [config, example] = await Promise.all(
      ['/api/config', '/api/example'].map(async path => {
        const response = await fetch(path);
        if (!response.ok) throw Error('Failed to load dashboard configuration.');
        return response.json();
      })
    );
    examplePrompt = example.prompt;
    livePrompt = examplePrompt;
    localBackendConfigured = Boolean(config.local_backend_configured);
    backendConfigured = localBackendConfigured || Boolean(config.backend_configured);
    $('live-mode').disabled = !backendConfigured;
    if (localBackendConfigured) $('mode').value = 'live';
    initialized = true;
    updateMode();
    initialRun();
  } catch (e) {
    error('Unable to load the local service. Start the dashboard server and refresh.');
    setStatus('');
    $('frame-status').textContent = 'Waiting for the dashboard service';
  }
}

init();
