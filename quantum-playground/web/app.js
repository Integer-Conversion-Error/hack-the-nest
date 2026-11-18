const $ = id => document.getElementById(id);
const chargeControlId = 'van_2_initial_charge_pct';
const pendingRunKey = 'quantumloop-pending-pipeline';
const legacyPendingRunKey = 'fleet-lab-pending-pipeline';
const colors = ['#185f55', '#315b8f', '#8a4d27', '#75527c', '#a42b46', '#6c650d'];
const provenanceLabels = {
  classical_reference: 'Exact reference',
  live: 'Live run',
  recorded: 'Recorded backend run',
};
const historyStatuses = { verified: 'Verified', saved: 'Saved', incomplete: 'Unfinished' };
const historyDate = new Intl.DateTimeFormat(undefined, { dateStyle: 'medium', timeStyle: 'short' });

const runModes = {
  recorded: {
    pending: 'Loading the recorded result…',
  },
  reference: {
    pending: 'Loading the exact reference…',
  },
  live: {
    action: 'Run backend',
    pending: 'Waiting for a verified backend result…',
  },
  local: {
    action: 'Run solver',
    pending: 'Running solver…',
  },
  planner: {
    action: 'Run pipeline',
  },
};

let inspected = null;
let result = null, sim = null, classicalSim = null, busy = false, initialized = false;
let backendConfigured = false, localBackendConfigured = false, pipelineBackendConfigured = false;
let savedRunRecalculationConfigured = false;
let playing = false, minute = 0, last = null;
let examplePrompt = '', liveExamplePrompt = '';
let samplePrompts = new Map();
let screen = 'prompt';
let pipelineSnapshot = null, pipelineReturnScreen = null;
let historyRequest = 0;
let selectedRun = null, selectedRunButton = null;
let controlPanel = null;
let vanCards = [];
const motionPreference = window.matchMedia?.('(prefers-reduced-motion: reduce)');
let reducedMotion = Boolean(motionPreference?.matches);
const mapCanvas = $('fleet-map');
const classicalCanvas = $('classical-map');
const map = FleetMap.create(mapCanvas, { colors, onInspect: inspect, motionAllowed: () => !reducedMotion });
const classicalMap = FleetMap.create(classicalCanvas, { colors,
  onInspect: target => inspect(target, 'classical'), motionAllowed: () => !reducedMotion });
const builder = ScenarioBuilder.create({ onSubmit: submitScenario });

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

function showScreen(next) {
  if (screen === next) return;
  screen = next;
  for (const name of ['prompt', 'progress', 'history', 'results']) $(name + '-screen').hidden = name !== next;
  $('result-navigation').hidden = next !== 'results';
  $('run-history').hidden = next === 'history' || next === 'progress';
  $({prompt: 'problem-heading', progress: 'progress-heading', history: 'history-heading',
    results: 'results-heading'}[next]).focus();
}

function composerError(message) {
  writeText($('composer-error'), message);
  $('composer-error').hidden = !message;
}

function writeMetric(id, value) {
  const element = $(id);
  if (element.textContent === value) return;
  writeText(element, value);
  if (!reducedMotion && element.animate) {
    element.animate([{ opacity: 0.45, transform: 'translateY(3px)' }, { opacity: 1, transform: 'translateY(0)' }],
      { duration: 220, easing: 'ease-out' });
  }
}

motionPreference?.addEventListener?.('change', event => {
  reducedMotion = event.matches;
  if (reducedMotion) setPlaying(false);
  updatePlayback();
});

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
  controlPanel = ScenarioControls.render($('variables'), definitions, applied, {
    onChange: controlChanged,
    expandGroupFor: chargeControlId,
  });
  $('settings').hidden = !definitions.length;
  $('applied-settings').textContent = 'Applied · ' + controlPanel.displayApplied();
}

function usesLocalSolver() {
  return localBackendConfigured && $('mode').value === 'live';
}

function usesNativePlanner() {
  return $('mode').value === 'live' && (pipelineBackendConfigured ||
    (result?.workspace_origin === 'local' && savedRunRecalculationConfigured));
}

function liveLabels() {
  if (localBackendConfigured) return runModes.local;
  if (pipelineBackendConfigured) return runModes.planner;
  return runModes.live;
}

function usesImmediateApply() {
  return (usesLocalSolver() || usesNativePlanner()) && Boolean(controlPanel?.automatic);
}

function isSavedResult(view) {
  return view?.mode === 'live' && view.workspace_origin === 'local' && view.source?.file === 'handoff.json';
}

function updateButtons() {
  const selectedMode = $('mode').value;
  const immediate = usesImmediateApply();
  const hasCurrentResult = result?.mode === selectedMode;
  const savedWithoutPlanner = result?.workspace_origin === 'local' && !savedRunRecalculationConfigured;
  const canApply = hasCurrentResult && result.variables.length > 0 && !savedWithoutPlanner;
  const draft = canApply ? controlPanel.values() : null;
  const changes = draft ? Object.keys(draft).filter(id => result.effective_values[id] !== draft[id]) : [];
  const hasChanges = changes.length > 0;

  const prompt = $('problem-prompt');
  const picker = $('sample-prompt');
  const disabled = !initialized || busy;
  $('generate').disabled = disabled || !backendConfigured || (localBackendConfigured && prompt.value !== liveExamplePrompt);
  picker.disabled = disabled || !samplePrompts.size;
  $('load-example').disabled = picker.disabled || !samplePrompts.has(picker.value);
  $('recorded-example').disabled = disabled;
  $('failed-pipeline-progress').disabled = disabled;
  $('result-pipeline-progress').disabled = disabled;
  $('run-history').disabled = busy;
  $('new-problem').disabled = disabled;
  $('mode').disabled = disabled;
  prompt.disabled = disabled || !backendConfigured;
  prompt.readOnly = localBackendConfigured;
  $('applied-settings').hidden = !canApply || (draft !== null && !hasChanges);
  $('saved-run-note').hidden = !savedWithoutPlanner || !result.variables.length;
  $('rerun').disabled = busy || !hasChanges;
  $('rerun').hidden = !canApply || (immediate && (busy || !hasChanges || $('error').hidden));
  $('rerun').textContent = immediate ? 'Retry solver'
    : selectedMode === 'recorded' ? 'Load recorded result' : 'Apply changes';
  controlPanel?.setDisabled(busy || !canApply);
  builder.setBusy(busy);
  $('edit-scenario').disabled = busy;
  return changes;
}

function controlChanged(committedRange) {
  const changes = updateButtons();
  const chargeOnly = committedRange === chargeControlId && changes.length === 1 && changes[0] === chargeControlId;
  const automaticCharge = chargeOnly && (usesLocalSolver() || usesNativePlanner());
  if ((usesImmediateApply() || automaticCharge) && !$('rerun').disabled) submitControlChanges();
}

async function request(path, data) {
  const response = await fetch(path, data === undefined ? undefined : {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify(data),
  });
  if (!response.ok) {
    const body = await response.json().catch(() => ({}));
    throw Object.assign(Error(body?.error || 'Request failed.'), { status: response.status });
  }
  const body = await response.json();
  return body;
}

function clock(m) {
  const total = Math.round((sim?.start_hour ?? 8) * 60 + m);
  return String(Math.floor(total / 60)).padStart(2, '0') + ':' + String(total % 60).padStart(2, '0');
}

function setPlaying(value) {
  playing = value;
  $('map-grid').setAttribute('data-playing', String(value));
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
  if (payload.prompt && data.problem?.original_prompt !== payload.prompt) {
    throw Error('The verified result does not match the submitted prompt.');
  }
  if (payload.scenario && (!data.scenario_context ||
      !ScenarioDraft.same(payload.scenario, data.scenario_context.effective_scenario))) {
    throw Error('The verified result does not match the submitted scenario.');
  }
  const source = payload.sourceView || result;
  if (payload.view_id && (payload.view_id !== source?.view_id || data.run_id !== source.run_id || data.plan_id !== source.plan_id ||
      data.attempt_id === source.attempt_id || data.view_id === source.view_id ||
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
  onResult: showResult,
  onError: e => {
    if (screen === 'results' && result) {
      $('mode').value = result.mode;
      error(e.message);
      setStatus('Showing the last result');
    } else {
      showScreen('prompt');
      composerError(e.message);
    }
  }
});

function showResult(data, payload) {
  acceptResult(data, payload);
  const previous = result;
  $('mode').value = data.mode;
  $('results-heading').textContent = data.problem?.title || 'EV fleet dispatch';
  $('prompt').textContent = data.problem?.original_prompt || '';
  $('result-pipeline-progress').hidden = !(pipelineSnapshot?.status === 'completed' &&
    pipelineSnapshot.history?.length && pipelineSnapshot.result?.plan_id === data.plan_id);
  if (!payload.view_id) $('original-prompt').open = false;
  showScreen('results');
  render(data, {preserve: Boolean(payload.view_id && previous?.view_id === payload.view_id)});
  result = data;
  if (payload.scenario) builder.acceptResult(data);
  $('edit-scenario').hidden = !data.scenario_context;
  renderChanges(data.evaluation, payload.view_id ? (payload.sourceView || previous)?.evaluation : null);
  setStatus(data.cached ? 'Reused verified result' : '');
}

function queueRun(payload, status) {
  $('error').hidden = true;
  setStatus(status);
  if (screen !== 'results') {
    composerError('');
    showProgress(payload.mode === 'live' ? 'Running solver' : 'Loading result', status);
  }
  updater.schedule(payload, payload);
}

const stageRows = PipelineProgress.stages.map(stage => {
  const row = node('li');
  const state = node('span', 'Pending', 'stage-state');
  row.append(node('span', stage.label), state);
  $('pipeline-stages').append(row);
  return { row, state };
});
const pipelineHistory = PipelineProgress.createHistoryView(
  $('pipeline-visits'), $('pipeline-detail-toggle'), node, writeText);

function showProgress(title, status, native = false) {
  $('progress-heading').textContent = title;
  $('progress-return').hidden = true;
  $('pipeline-stages').hidden = !native;
  $('pipeline-visits').hidden = true;
  $('pipeline-detail-toggle').hidden = true;
  for (const id of ['pipeline-count', 'pipeline-current', 'pipeline-repair', 'pipeline-activity-heading']) $(id).hidden = true;
  $('pipeline-intro').hidden = !native;
  if (native) {
    pipelineHistory.reset();
    updatePipelineProgress();
  }
  writeText($('pipeline-status'), status);
  showScreen('progress');
}

function updatePipelineProgress(snapshot) {
  const stages = PipelineProgress.stages;
  const index = stages.findIndex(stage => stage.id === snapshot?.stage);
  const attempt = snapshot?.attempt > 1 ? ' · Attempt ' + snapshot.attempt : '';
  const history = snapshot?.history ?? [];
  const overview = PipelineProgress.describe(snapshot);
  $('pipeline-intro').hidden = false;
  $('pipeline-stages').hidden = false;
  pipelineHistory.render(history);
  for (const [i, { row, state }] of stageRows.entries()) {
    const stage = overview.stages[i];
    row.setAttribute('data-state', stage.state === 'completed' ? 'complete' : stage.state);
    row.setAttribute('aria-current', stage.state === 'running' ? 'step' : 'false');
    writeText(state, stage.status);
  }
  $('pipeline-count').hidden = false;
  writeText($('pipeline-count'), `${overview.completed} of ${overview.total} stages complete`);
  const current = $('pipeline-current');
  current.hidden = false;
  current.setAttribute('data-state', snapshot?.status || 'queued');
  writeText($('pipeline-current-state'), snapshot?.status === 'completed' ? 'Result verified'
    : snapshot?.status === 'failed' ? 'Needs your attention'
    : snapshot?.status === 'queued' || !snapshot ? 'Getting started' : 'Current step');
  writeText($('pipeline-current-label'), overview.current.label);
  writeText($('pipeline-current-detail'), overview.current.detail || '');
  $('pipeline-current-detail').hidden = !overview.current.detail;
  $('pipeline-repeat-count').hidden = !overview.revisionCount;
  writeText($('pipeline-repeat-count'), `${overview.revisionCount} repair loop${overview.revisionCount === 1 ? '' : 's'}`);
  $('pipeline-repair').hidden = !overview.repair;
  writeText($('pipeline-repair-label'), overview.repair?.label || '');
  writeText($('pipeline-repair-detail'), overview.repair?.detail || '');
  $('pipeline-activity-heading').hidden = !history.length;
  const activity = stages[index]?.activity;
  const status = snapshot?.status === 'completed' ? 'Pipeline complete.'
    : snapshot?.status === 'failed' ? snapshot.error || 'The pipeline could not produce a verified result.'
    : (history.length ? activity?.replace(/…$/, attempt + '…') : activity) || 'Waiting for the pipeline to start…';
  writeText($('pipeline-status'), status);
}

function openPipelineProgress() {
  if (busy || !pipelineSnapshot?.history?.length) return;
  setPlaying(false);
  pipelineReturnScreen = screen;
  $('progress-heading').textContent = 'Pipeline progress';
  const back = $('progress-return');
  writeText(back, screen === 'results' ? 'Back to results' : 'Back to problem');
  back.hidden = false;
  updatePipelineProgress(pipelineSnapshot);
  showScreen('progress');
}

$('failed-pipeline-progress').onclick = openPipelineProgress;
$('result-pipeline-progress').onclick = openPipelineProgress;
$('progress-return').onclick = () => {
  const previous = pipelineReturnScreen;
  if (!previous) return;
  pipelineReturnScreen = null;
  showScreen(previous);
  $(previous === 'results' ? 'result-pipeline-progress' : 'failed-pipeline-progress').focus();
};

function pendingRun() {
  try {
    const stored = JSON.parse(window.sessionStorage.getItem(pendingRunKey) ||
      window.sessionStorage.getItem(legacyPendingRunKey));
    if (typeof stored?.id === 'string' && typeof stored.prompt === 'string') return stored;
  } catch {
    // The current run still works when browser storage is unavailable.
  }
  return null;
}

function savePendingRun(run) {
  try {
    if (run) window.sessionStorage.setItem(pendingRunKey, JSON.stringify(run));
    else window.sessionStorage.removeItem(pendingRunKey);
    window.sessionStorage.removeItem(legacyPendingRunKey);
  } catch {
    // Storage only affects resuming after a reload.
  }
}

async function startPipeline(prompt, id, scenario) {
  busy = true;
  pipelineSnapshot = null;
  $('failed-pipeline-progress').hidden = true;
  composerError('');
  $('error').hidden = true;
  showProgress('Pipeline progress', id ? 'Reconnecting to your run…' : 'Submitting your problem…', true);
  updateButtons();
  try {
    const snapshot = await PipelineProgress.follow({
      request, prompt, scenario, id,
      onSnapshot(job) {
        if (typeof job.prompt === 'string') prompt = job.prompt;
        if (!id) {
          id = job.id;
          savePendingRun({ id, prompt, ...(scenario ? { scenario } : {}) });
        }
        pipelineSnapshot = job;
        updatePipelineProgress(job);
      },
      onDisconnect: () => writeText($('pipeline-status'), 'Connection interrupted. Reconnecting to your run…'),
    });
    if (snapshot.status === 'failed') throw Error(snapshot.error || 'The pipeline could not produce a verified result.');
    showResult(snapshot.result, { mode: 'live', prompt, scenario });
  } catch (e) {
    $('failed-pipeline-progress').hidden = !pipelineSnapshot?.history?.length;
    showScreen('prompt');
    composerError(e.status === 404 ? 'This pipeline run is no longer available. Submit your problem to run again.' : e.message);
    if (scenario) builder.setError(e.message);
  } finally {
    savePendingRun(null);
    busy = false;
    updateButtons();
  }
}

async function submitScenario(scenario, reuse) {
  if (busy) return;
  if (!reuse) return startPipeline('', undefined, scenario);
  const sourceView = builder.sourceView();
  busy = true;
  updateButtons();
  builder.setError('');
  showProgress('Recalculate settings', 'Running the approved solver and checking the new schedule…');
  try {
    const payload = { mode: 'live', view_id: reuse.view_id, variables: reuse.variables };
    const data = await request('/api/run', payload);
    showResult(data, { ...payload, scenario, sourceView });
  } catch (e) {
    showScreen('prompt');
    builder.setError(e.message);
  } finally {
    busy = false;
    updateButtons();
  }
}

$('prompt-form').onsubmit = event => {
  event.preventDefault();
  if ($('generate').disabled) return;
  const field = $('problem-prompt');
  const prompt = field.value;
  if (!prompt.trim()) {
    field.setCustomValidity('Enter a problem to run the pipeline.');
    field.reportValidity();
    return;
  }
  if (pipelineBackendConfigured) startPipeline(prompt);
  else queueRun({mode: 'live', prompt}, liveLabels().pending);
};

function submitControlChanges() {
  if ($('rerun').disabled) return;
  const pending = usesLocalSolver()
    ? 'Running solver…'
    : result.mode === 'recorded' ? 'Loading the recorded result…' : 'Recalculating…';
  queueRun({mode: result.mode, view_id: result.view_id, variables: controlPanel.values()}, pending);
}
$('rerun').onclick = submitControlChanges;

function returnToProblem() {
  if (busy) return;
  setPlaying(false);
  composerError('');
  showScreen('prompt');
  updateButtons();
  if (!$('problem-prompt').disabled) $('problem-prompt').focus();
}

$('new-problem').onclick = returnToProblem;
$('history-new-problem').onclick = returnToProblem;
$('mode').onchange = () => {
  const mode = $('mode').value;
  if (mode === result?.mode) return;
  if (mode === 'live') returnToProblem();
  else queueRun({mode, prompt: examplePrompt}, runModes[mode].pending);
};
$('problem-prompt').oninput = () => {
  $('problem-prompt').setCustomValidity('');
  composerError('');
};
$('sample-prompt').onchange = updateButtons;
$('load-example').onclick = () => {
  if ($('load-example').disabled) return;
  $('problem-prompt').value = samplePrompts.get($('sample-prompt').value).prompt;
  $('problem-prompt').oninput();
  updateButtons();
  if (!$('problem-prompt').disabled) $('problem-prompt').focus();
};
$('recorded-example').onclick = () => queueRun({mode: 'recorded', prompt: examplePrompt}, runModes.recorded.pending);

const workspaceUrl = id => '/api/workspaces/' + encodeURIComponent(id);

function historyMessage(status, error = '') {
  for (const [id, text] of [['history-status', status], ['history-error', error]]) {
    writeText($(id), text);
    $(id).hidden = !text;
  }
}

function isHistoryRequest(serial) {
  return screen === 'history' && serial === historyRequest;
}

function runDate(value) {
  const date = typeof value === 'string' ? new Date(value) : null;
  return date && Number.isFinite(date.valueOf()) ? historyDate.format(date) : '';
}

async function showHistory() {
  if (busy) return;
  const serial = ++historyRequest;
  setPlaying(false);
  showScreen('history');
  $('history-list').hidden = true;
  $('history-details').hidden = true;
  selectedRun = selectedRunButton = null;
  historyMessage('Loading saved runs…');
  try {
    const { workspaces } = await request('/api/workspaces');
    if (!isHistoryRequest(serial)) return;
    if (!Array.isArray(workspaces)) throw Error('The server returned an invalid run history.');
    const rows = workspaces.map(workspace => {
      const row = node('li');
      const button = node('button', undefined, 'history-item');
      button.type = 'button';
      button.setAttribute('aria-controls', 'history-details');
      const date = runDate(workspace.created_at);
      const status = node('span', historyStatuses[workspace.status] || 'Saved', 'history-badge');
      status.setAttribute('data-status', workspace.status);
      button.append(node('strong', workspace.title), status,
        node('span', date || workspace.id, 'history-item-meta'));
      button.onclick = () => selectSavedRun(workspace.id, button);
      row.append(button);
      return row;
    });
    $('history-list').replaceChildren(...rows);
    $('history-list').hidden = !rows.length;
    historyMessage(rows.length ? '' : 'No saved runs yet.');
  } catch (error) {
    if (isHistoryRequest(serial)) historyMessage('', error.message);
  }
}

async function selectSavedRun(id, button) {
  const serial = ++historyRequest;
  selectedRunButton?.setAttribute('aria-current', 'false');
  selectedRunButton = button;
  button.setAttribute('aria-current', 'true');
  selectedRun = null;
  $('history-details').hidden = true;
  historyMessage('Loading run details…');
  try {
    const run = await request(workspaceUrl(id));
    if (!isHistoryRequest(serial)) return;
    if (run.id !== id || typeof run.prompt !== 'string' || !Array.isArray(run.artifacts)) {
      throw Error('The server returned invalid run details.');
    }
    selectedRun = run;
    $('history-detail-heading').textContent = run.title;
    $('history-identity').textContent = [runDate(run.created_at), run.id].filter(Boolean).join(' · ');
    $('history-prompt').textContent = run.prompt;
    $('history-open').hidden = !run.can_open;
    $('history-edit-scenario').hidden = !run.can_edit;
    $('history-open').disabled = false;
    $('history-artifact-section').hidden = !run.artifacts.length;
    $('history-artifacts').replaceChildren(...run.artifacts.map(artifact => {
      const item = node('li');
      const link = node('a', artifact.label);
      link.href = workspaceUrl(id) + '/artifacts/' + encodeURIComponent(artifact.key);
      link.download = artifact.filename;
      item.append(link);
      return item;
    }));
    $('history-details').hidden = false;
    historyMessage('');
    $('history-detail-heading').focus();
  } catch (error) {
    if (isHistoryRequest(serial)) historyMessage('', error.message);
  }
}

async function openSavedResult() {
  const run = selectedRun;
  const serial = historyRequest;
  if (!run?.can_open || $('history-open').disabled || screen !== 'history') return;
  $('history-open').disabled = true;
  historyMessage('Opening saved results…');
  try {
    const data = await request(workspaceUrl(run.id) + '/open', {});
    if (!isHistoryRequest(serial)) return;
    if (data?.plan_id !== run.id) throw Error('The result does not match the selected run.');
    $('error').hidden = true;
    showResult(data, { mode: 'live', prompt: run.prompt });
    updateButtons();
  } catch (error) {
    if (isHistoryRequest(serial)) historyMessage('', error.message);
  } finally {
    if (isHistoryRequest(serial)) $('history-open').disabled = false;
  }
}

$('run-history').onclick = showHistory;
$('history-open').onclick = openSavedResult;
$('edit-scenario').onclick = () => {
  if (busy || !result?.scenario_context) return;
  setPlaying(false);
  builder.edit(result.scenario_context.effective_scenario, result);
  showScreen('prompt');
};
$('history-edit-scenario').onclick = () => {
  if (!selectedRun?.can_edit || !selectedRun.scenario) return;
  builder.edit(selectedRun.scenario);
  showScreen('prompt');
};

const circuitDialog = $('circuit-dialog');
function closeCircuit() {
  if (circuitDialog.open) circuitDialog.close();
}
$('circuit-dialog-close').onclick = closeCircuit;
circuitDialog.onclose = () => {
  if (circuitDialog.open) return;
  $('circuit-dialog-image').removeAttribute('src');
  $('circuit-download').removeAttribute('href');
};

function circuitImage(value) {
  return typeof value === 'string' && value.length <= 3_000_000 &&
    /^data:image\/png;base64,iVBORw0KGgo[A-Za-z0-9+/=]*$/.test(value) ? value : null;
}

function renderCircuits(circuits) {
  closeCircuit();
  const list = $('circuits');
  list.replaceChildren();
  let count = 0;
  let images = 0;
  for (const circuit of Array.isArray(circuits) ? circuits : []) {
    const image = circuitImage(circuit?.image);
    const drawing = typeof circuit?.drawing === 'string' ? circuit.drawing.trim() : '';
    if (!image && !drawing) continue;
    const title = typeof circuit.title === 'string' && circuit.title.trim() ? circuit.title : 'Quantum circuit';
    const description = typeof circuit.description === 'string' ? circuit.description : '';
    const card = node('article', undefined, 'circuit-card');
    card.append(node('h3', title));
    if (image) {
      images++;
      const preview = node('button', undefined, 'circuit-preview');
      const thumbnail = node('img');
      thumbnail.src = image;
      thumbnail.alt = title;
      thumbnail.decoding = 'async';
      preview.type = 'button';
      preview.setAttribute('aria-label', 'View ' + title + ' at full size');
      preview.setAttribute('aria-haspopup', 'dialog');
      preview.setAttribute('aria-controls', 'circuit-dialog');
      preview.append(thumbnail, node('span', 'View full size ↗'));
      preview.onclick = () => {
        setPlaying(false);
        writeText($('circuit-dialog-title'), title);
        writeText($('circuit-dialog-description'), description || 'Circuit diagram for the displayed run. Scroll to inspect all gates.');
        $('circuit-dialog-image').src = image;
        $('circuit-dialog-image').alt = title;
        $('circuit-download').href = image;
        circuitDialog.showModal();
      };
      card.append(preview);
    } else {
      const text = node('pre', drawing);
      text.tabIndex = 0;
      text.setAttribute('aria-label', title + ' text drawing');
      card.append(text);
    }
    if (description) card.append(node('p', description, 'circuit-description'));
    list.append(card);
    count++;
  }
  writeText($('circuit-help'), images ? 'Select an image to inspect the gates at full size.' : 'Circuit drawing supplied with this run.');
  $('circuit-panel').hidden = !count;
}

function render(data, {preserve = false} = {}) {
  const saved = {minute, playing};
  sim = data.simulation;
  const comparison = data.classical_simulation;
  classicalSim = data.provenance !== 'classical_reference' && data.evaluation.classical?.feasible === true &&
    Number.isFinite(data.evaluation.classical.objective_value) &&
    comparison?.start_hour === sim.start_hour && comparison.duration_min === sim.duration_min &&
    FleetReplay.validate(comparison) ? comparison : null;
  if (!preserve || (inspected && !entities(inspected.kind, replayFor(inspected.source)).some(item => item.id === inspected.id))) {
    inspected = null;
  }
  minute = preserve ? Math.min(saved.minute, sim.duration_min) : 0;
  setPlaying(preserve && saved.playing && minute < sim.duration_min);

  const engineLabel = {
    'quantum-rings': 'Quantum Rings',
    'numpy-statevector': 'Local simulator',
  }[data.quantum?.engine];
  let badge = provenanceLabels[data.provenance];
  if (isSavedResult(data)) badge = 'Saved run';
  else if (data.provenance === 'live' && engineLabel) badge = 'Live · ' + engineLabel;
  $('run-badge').textContent = badge;
  controls(data.variables || [], data.effective_values || {});

  renderCircuits(data.provenance === 'classical_reference' ? [] : data.circuits);

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
  writeMetric('score', money(evaluation.objective_value));
  $('feasible').textContent = 'Checks passed';
  renderSolverComparison(evaluation, data.provenance === 'classical_reference', Boolean(data.scenario_context));
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
  $('map-grid').classList.toggle('comparing', Boolean(classicalSim));
  $('quantum-map-heading').hidden = !classicalSim;
  $('classical-map-view').hidden = !classicalSim;
  $('comparison-note').hidden = !classicalSim;
  $('delivered-summary').hidden = Boolean(classicalSim);
  $('schedule-heading').textContent = classicalSim ? 'Quantum schedule' : 'Schedule';
  mapCanvas.setAttribute('aria-label', classicalSim ? 'Interactive quantum fleet map' : 'Interactive fleet map');
  map.setSimulation(sim);
  classicalMap.setSimulation(classicalSim);
  mapCanvas.hidden = false;
  $('frame-status').hidden = true;
  updatePlayback();
}
function renderSolverComparison(evaluation, isReference, candidateScope = false) {
  const baseline = evaluation.classical;
  const classical = !isReference && baseline?.feasible === true && Number.isFinite(baseline.objective_value);
  const reference = classical ? baseline.objective_value : evaluation.reference_value;
  const gap = Number.isFinite(reference) ? evaluation.objective_value - reference : evaluation.gap;

  $('score-label').textContent = classical ? 'Quantum cost' : 'Total cost';
  $('score-check').hidden = !classical;
  $('reference-value').hidden = isReference || !Number.isFinite(reference);
  $('reference-label').textContent = classical ? 'Classical cost' : 'Reference cost';
  writeMetric('reference-score', Number.isFinite(reference) ? money(reference) : '—');
  $('reference-check').hidden = !classical;
  $('reference-check').textContent = baseline?.optimality_proven === true
    ? candidateScope ? 'Feasible · Exact over candidates' : 'Feasible · Exact optimum' : 'Feasible';
  $('reference-gap').hidden = isReference || !Number.isFinite(gap);
  $('gap-label').textContent = classical ? 'Gap to classical' : 'Gap to reference';
  const sign = Math.abs(gap) >= 0.005 ? (gap < 0 ? '−' : '+') : '';
  writeMetric('gap-score', Number.isFinite(gap) ? sign + money(Math.abs(gap)) : '—');
  const direction = !Number.isFinite(gap) || Math.abs(gap) < 0.005 ? 'equal' : gap > 0 ? 'higher' : 'lower';
  $('reference-gap').setAttribute('data-direction', direction);
  $('gap-note').textContent = classical ? direction === 'equal' ? 'Same cost'
    : direction === 'higher' ? 'Quantum costs more' : 'Quantum costs less' : 'Difference from reference';

  $('classical-details').hidden = !classical;
  $('classical-method').textContent = classical ? baseline.method : '';
  const options = classical && Array.isArray(baseline.selected_options) ? baseline.selected_options : [];
  $('classical-options').hidden = !options.length;
  $('classical-options').textContent = options.length ? 'Route options · ' + options.join(', ') : '';
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
    if (color) row.style.setProperty('--row-color', color);
    row.append(node('span', label), track);
    for (const event of events) {
      const start = Math.max(0, event.start_min);
      const end = Math.min(sim.duration_min, event.end_min);
      if (end < start) continue;
      const block = node('button', undefined, 'event-block ' + event.type + (event.van ? ' reservation' : ''));
      const range = clock(start) + '–' + clock(end);
      block.type = 'button';
      block.style.left = start / sim.duration_min * 100 + '%';
      block.style.width = (end - start) / sim.duration_min * 100 + '%';
      block.style.setProperty('--van', color || event.color);
      block.title = [label, event.van, eventLabels[event.type], range,
        target.kind === 'van' ? event.charger_id || event.delivery_id : ''].filter(Boolean).join(' · ');
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
  map.draw(minute, inspectedVan('primary'), playing);
  writeText($('clock'), clock(minute));
  $('scrub').value = String(minute);
  $('scrub').setAttribute('aria-valuetext', clock(minute));
  const done = FleetReplay.deliveredAt(sim, minute);
  const delivered = done.size + ' / ' + sim.deliveries.length;
  writeText($('delivered'), delivered);
  if (classicalSim) {
    classicalMap.draw(minute, inspectedVan('classical'), playing);
    const classicalDone = FleetReplay.deliveredAt(classicalSim, minute);
    writeText($('quantum-delivered'), delivered + ' delivered');
    writeText($('classical-delivered'), classicalDone.size + ' / ' + classicalSim.deliveries.length + ' delivered');
  }
  sim.vans.forEach((van, i) => {
    const state = FleetReplay.stateAt(van, minute, FleetReplay.depotFor(sim, van));
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

function replayFor(source) {
  return source === 'classical' ? classicalSim : sim;
}

function entities(kind, replay) {
  return replay ? {van: replay.vans, charger: replay.chargers, delivery: replay.deliveries}[kind] : [];
}

function inspectedVan(source) {
  return inspected?.source === source && inspected.kind === 'van' ? inspected.id : null;
}

function applyVanSelection() {
  const selectedVan = inspectedVan('primary');
  vanCards.forEach(({card}, i) => {
    const active = selectedVan === sim.vans[i].id;
    card.classList.toggle('selected', active);
    card.setAttribute('aria-pressed', String(active));
  });
}

function setInspection(target, source = 'primary') {
  inspected = target ? {...target, source} : null;
  applyVanSelection();
}

function inspect(target, source = 'primary') {
  const alreadySelected = inspected?.source === source && inspected.kind === target.kind && inspected.id === target.id;
  setInspection(alreadySelected ? null : target, source);
  updatePlayback();
}

function chargerBookings(chargerId, replay = sim) {
  return replay.vans.flatMap((van, i) => van.events
    .filter(event => event.type === 'charge' && event.charger_id === chargerId)
    .map(event => ({...event, van: van.id, color: colors[i % colors.length]})))
    .sort((first, second) => first.start_min - second.start_min);
}

function updateInspector() {
  const panel = $('pygame-inspector');
  panel.hidden = !inspected;
  if (!inspected) return;
  const {kind, id, source} = inspected;
  const replay = replayFor(source);
  const item = entities(kind, replay).find(candidate => candidate.id === id);
  if (!item) {
    panel.hidden = true;
    return;
  }

  let details;
  if (kind === 'van') {
    const state = FleetReplay.stateAt(item, minute, FleetReplay.depotFor(replay, item));
    const upcoming = item.events.find(event => event.end_min > minute);
    const percent = (state.battery / replay.battery_capacity_kwh * 100).toFixed(0);
    details = [item.name || id, state.status, state.battery.toFixed(2) + ' kWh (' + percent + '%)',
      item.delivery_ids?.length ? 'Deliveries ' + item.delivery_ids.join(', ') : '',
      item.option_id ? 'Option: ' + item.option_id : '',
      upcoming ? 'Next event ends ' + clock(upcoming.end_min) : ''];
  } else if (kind === 'charger') {
    const bookings = chargerBookings(id, replay);
    const active = bookings.find(event => event.start_min <= minute && minute < event.end_min);
    const reservations = bookings.map(event => event.van + ' ' + clock(event.start_min) + '–' + clock(event.end_min));
    details = [id, active ? 'Charging ' + active.van + ' until ' + clock(active.end_min) : 'No charging scheduled at this time',
      'Reservations: ' + (reservations.join(', ') || 'none')];
  } else {
    const van = replay.vans.find(candidate => candidate.events.some(event => event.delivery_id === id));
    const event = van?.events.find(candidate => candidate.delivery_id === id);
    const done = FleetReplay.deliveredAt(replay, minute).has(id);
    const lateness = event && Math.max(0, event.end_min - item.deadline_min);
    details = [id + ' at (' + item.position.join(', ') + ')', done ? 'Delivered' : 'Pending',
      van?.id || 'Unassigned', event ? 'Service completes ' + clock(event.end_min) : '',
      Number.isFinite(item.deadline_min) ? 'Deadline ' + clock(item.deadline_min) : '',
      lateness > 0 ? lateness.toFixed(1).replace(/\.0$/, '') + ' min late' : ''];
  }
  const solver = classicalSim ? (source === 'classical' ? 'Classical' : 'Quantum') : '';
  writeText(panel, [solver, ...details].filter(Boolean).join(' | '));
}

function handleMapKeys(event, source) {
  const replay = replayFor(source);
  if (!replay) return;
  const kind = {v: 'van', c: 'charger', d: 'delivery'}[event.key.toLowerCase()];
  if (kind) {
    const items = entities(kind, replay);
    if (!items.length) return;
    const i = inspected?.source === source && inspected.kind === kind ? items.findIndex(item => item.id === inspected.id) : -1;
    event.preventDefault();
    inspect({kind, id: items[(i + 1) % items.length].id}, source);
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
}
mapCanvas.onkeydown = event => handleMapKeys(event, 'primary');
classicalCanvas.onkeydown = event => handleMapKeys(event, 'classical');
$('export').onclick=()=>{if(!result)return;const url=URL.createObjectURL(new Blob([JSON.stringify(result,null,2)],{type:'application/json'})),a=node('a');a.href=url;a.download=`ev-dispatch-${result.run_id}-${result.attempt_id}.json`;a.click();setTimeout(()=>URL.revokeObjectURL(url),1000);};
$('feedback').onclick=async()=>{if(!result)return;const feedback=prompt('Feedback is saved for this server session. What should change?');if(!feedback)return;try{await request('/api/feedback',{view_id:result.view_id,feedback});setStatus('Feedback saved locally');}catch(e){error(e.message);}};

function populateSamples(catalog) {
  const samples = (Array.isArray(catalog) ? catalog : []).filter(sample =>
    typeof sample?.id === 'string' && sample.id && typeof sample.label === 'string' && sample.label &&
    typeof sample.prompt === 'string' && sample.prompt.trim() &&
    (!localBackendConfigured || sample.prompt === liveExamplePrompt));
  if (!samples.length) samples.push({ id: 'ev-charge-range', label: 'EV fleet dispatch', prompt: liveExamplePrompt });
  samplePrompts = new Map(samples.map(sample => [sample.id, sample]));
  const picker = $('sample-prompt');
  picker.replaceChildren(...Array.from(samplePrompts.values(), sample => {
    const option = node('option', sample.label);
    option.value = sample.id;
    return option;
  }));
  picker.value = samples.find(sample => sample.prompt === liveExamplePrompt)?.id ?? samples[0].id;
}

async function init() {
  const stored = pendingRun();
  if (stored) $('problem-prompt').value = stored.prompt;
  try {
    const [config, example] = await Promise.all(
      ['/api/config', '/api/example'].map(async path => {
        const response = await fetch(path);
        if (!response.ok) throw Error('Failed to load dashboard configuration.');
        return response.json();
      })
    );
    examplePrompt = example.prompt;
    liveExamplePrompt = example.live_prompt || examplePrompt;
    localBackendConfigured = Boolean(config.local_backend_configured);
    pipelineBackendConfigured = Boolean(config.pipeline_backend_configured);
    builder.setConfigured(Boolean(config.scenario_backend_configured ?? config.pipeline_backend_configured));
    savedRunRecalculationConfigured = Boolean(config.saved_run_recalculation_configured ?? config.pipeline_backend_configured);
    backendConfigured = localBackendConfigured || pipelineBackendConfigured || Boolean(config.backend_configured);
    populateSamples(example.samples);
    $('live-mode').disabled = !backendConfigured;
    $('generate').textContent = liveLabels().action;
    const note = $('composer-note');
    note.textContent = localBackendConfigured ? 'Load the EV sample to run the built-in solver.'
      : backendConfigured ? '' : 'Start a live backend to run a custom problem.';
    note.hidden = !note.textContent;
    initialized = true;
    updateButtons();
    if (stored) {
      if (!stored.scenario) builder.showMode('prompt');
      startPipeline(stored.prompt, stored.id, stored.scenario);
    }
  } catch {
    $('composer-note').hidden = true;
    composerError('Unable to load the local service. Start the dashboard server and refresh.');
  }
}

init();
