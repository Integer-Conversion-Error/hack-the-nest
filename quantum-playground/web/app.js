const $ = id => document.getElementById(id);
const colors = ['#8af1d0','#a0abff','#ffc177','#fa8faf'];
const provenanceLabels = {
  classical_reference: 'Exact reference',
  live: 'Live run',
  recorded: 'Recorded run',
};

let inspected = null, pygameScene = null, pygameSceneView = null, scrubbing = null;
let result = null, sim = null, busy = false, initialized = false, backendConfigured = false;
let playing = false, minute = 0, last = 0, frameTimer = null, selectedVan = null;
let examplePrompt = '', livePrompt = '';
const controlInputs = new Map();

const node = (tag, text, cls) => {
  const element = document.createElement(tag);
  if (text !== undefined) element.textContent = text;
  if (cls) element.className = cls;
  return element;
};
const money = amount => Number.isFinite(amount) ? '$' + amount.toFixed(2) : '—';

function error(message) {
  $('error').textContent = message;
  $('error').hidden = false;
}

function controlLabel(control, value) {
  return value + (control.unit || '');
}

function controls(definitions, applied) {
  controlInputs.clear();
  $('variables').replaceChildren();
  const summary = [];
  for (const spec of definitions) {
    const wrap = node('div', undefined, 'control');
    const label = node('label', spec.label);
    const input = node('select');
    input.id = 'variable-' + spec.id;
    label.htmlFor = input.id;
    for (const value of spec.allowed_values) {
      const option = node('option', controlLabel(spec, value));
      option.value = String(value);
      input.append(option);
    }
    input.value = String(applied[spec.id]);
    input.onchange = updateButtons;
    controlInputs.set(spec.id, input);
    summary.push(spec.label + ' · ' + controlLabel(spec, applied[spec.id]));
    wrap.append(label, input);
    $('variables').append(wrap);
  }
  $('settings').hidden = !definitions.length;
  $('applied-settings').textContent = 'Applied settings — ' + summary.join(', ');
}

function values() {
  return Object.fromEntries(Array.from(controlInputs, ([id, input]) => [id, Number(input.value)]));
}

function updateButtons() {
  const selectedMode = $('mode').value;
  const canRun = selectedMode === 'reference' || backendConfigured;
  $('generate').disabled = !initialized || busy || !canRun;
  $('load-example').hidden = selectedMode === 'reference';
  $('load-example').disabled = !initialized || busy;
  $('mode').disabled = !initialized || busy;
  $('prompt').readOnly = selectedMode === 'reference' || busy;
  const canApply = result?.mode === selectedMode && controlInputs.size > 0;
  const hasChanges = Object.entries(values()).some(([id, value]) => result?.effective_values[id] !== value);
  $('rerun').disabled = busy || !canApply || !hasChanges;
  for (const input of controlInputs.values()) input.disabled = busy || !canApply;
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
  $('play').textContent = value ? 'Ⅱ Pause' : '▶ Play';
}

function acceptResult(data, payload) {
  if (!data) throw Error('The service did not return a verified EV fleet result.');
  const ids = [data.view_id, data.run_id, data.plan_id, data.attempt_id];
  if (data.status !== 'completed' || data.mode !== payload.mode ||
      !Object.hasOwn(provenanceLabels, data.provenance) || ids.some(id => typeof id !== 'string' || !id) ||
      data.evaluation?.feasible !== true || !Number.isFinite(data.evaluation.objective_value) ||
      !FleetReplay.validate(data.simulation)) {
    throw Error('The service did not return a verified EV fleet result.');
  }
  if (!data.effective_values || !Array.isArray(data.variables) || data.variables.some(control =>
    !Array.isArray(control.allowed_values) || !control.allowed_values.includes(data.effective_values[control.id]))) {
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
    const delta = data.evaluation.objective_value - previous?.evaluation?.objective_value;
    $('change-summary').textContent = payload.view_id && Number.isFinite(delta)
      ? (delta === 0 ? 'Cost unchanged' : 'Cost ' + (delta > 0 ? 'increased' : 'decreased') + ' by ' + money(Math.abs(delta)))
      : '';
    $('status').textContent = provenanceLabels[data.provenance] + ' ready';
  },
  onError: e => {
    error(e.message);
    $('status').textContent = result ? 'Showing the last verified result' : 'Run failed';
  }
});

function queueRun(payload, status) {
  $('error').hidden = true;
  $('status').textContent = status;
  updater.schedule(payload, payload);
}

function initialRun() {
  const mode = $('mode').value;
  const prompt = mode === 'reference' ? examplePrompt : $('prompt').value;
  if (!prompt.trim()) {
    $('prompt').reportValidity();
    return;
  }
  if ($('generate').disabled) return;
  queueRun({mode, prompt}, mode === 'reference' ? 'Loading the exact reference…' : 'Waiting for a verified backend result…');
}

$('prompt-form').onsubmit = event => {
  event.preventDefault();
  initialRun();
};

$('rerun').onclick = () => {
  if ($('rerun').disabled) return;
  queueRun({mode: result.mode, view_id: result.view_id, variables: values()}, 'Recalculating · showing the last verified result');
};

function updateMode() {
  if ($('mode').value === 'reference') {
    $('prompt').value = examplePrompt;
    $('generate').textContent = 'Load exact reference';
    $('mode-note').textContent = 'Replay the approved EV plan using the exact classical solution.';
  } else {
    $('prompt').value = livePrompt;
    $('generate').textContent = 'Run backend';
    $('mode-note').textContent = 'Submit a prompt to the backend and wait for a verified result.';
  }
  updateButtons();
}

$('mode').onchange = () => {
  if ($('mode').value === 'reference') livePrompt = $('prompt').value;
  updateMode();
};
$('load-example').onclick = () => { $('prompt').value = examplePrompt; };

function render(data, {preserve = false} = {}) {
  const saved = {minute, playing, selectedVan};
  sim = data.simulation;
  if (!preserve) inspected = null;
  selectedVan = preserve && sim.vans.some(van => van.id === saved.selectedVan) ? saved.selectedVan : null;
  minute = preserve ? Math.min(saved.minute, sim.duration_min) : 0;
  setPlaying(preserve && saved.playing && minute < sim.duration_min);

  $('problem-title').textContent = data.problem?.title || 'Dispatch request';
  $('statement').textContent = data.problem?.statement || '';
  $('submitted-prompt').textContent = data.problem?.original_prompt || '';
  $('run-badge').textContent = provenanceLabels[data.provenance];
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
    Number.isFinite(quantum.binary_variables) ? quantum.binary_variables + ' binary decisions' : null,
    Number.isFinite(quantum.actual_qubits) ? quantum.actual_qubits + ' executed qubits' : null,
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
  $('start-time').textContent = clock(0);
  $('end-time').textContent = clock(sim.duration_min);
  $('scrub').max = sim.duration_min;
  for (const id of ['play', 'restart', 'scrub', 'export', 'feedback']) $(id).disabled = false;
  buildFleet();
  buildTimeline();
  applyVanSelection();
  updatePlayback();
  pygameScene = null;
  releaseScrub();
  $('pygame-frame').hidden = true;
  $('frame-status').hidden = false;
  $('frame-status').textContent = 'Loading map…';
}
function buildFleet(){$('fleet').replaceChildren();if(!sim){$('fleet').append(node('p','No replay schedule available.','hint'));return;}sim.vans.forEach((van,i)=>{const card=node('button',undefined,'van-card');card.style.setProperty('--van',colors[i%4]);card.setAttribute('aria-pressed','false');card.append(node('strong',van.id),node('span','', 'van-state'),node('span','', 'van-energy'));const track=node('div',undefined,'battery-track');track.append(node('div',undefined,'battery-fill'));card.append(track);card.onclick=()=>inspect({kind:'van',id:van.id});$('fleet').append(card);});}
function buildTimeline(){$('timeline').replaceChildren();if(!sim)return;const axis=node('div',undefined,'time-axis');for(let i=0;i<=4;i++)axis.append(node('span',clock(sim.duration_min*i/4)));$('timeline').append(axis);
const row=(label,events,color)=>{const r=node('div',undefined,'timeline-row'),track=node('div',undefined,'timeline-track');r.append(node('span',label));for(const e of events){const start=Math.max(0,e.start_min),end=Math.min(sim.duration_min,e.end_min);if(end<=start)continue;const b=node('button',undefined,`event-block ${e.type}`);b.style.left=`${start/sim.duration_min*100}%`;b.style.width=`${(end-start)/sim.duration_min*100}%`;b.style.setProperty('--van',color);b.title=`${label}: ${e.type} ${clock(e.start_min)}–${clock(e.end_min)} ${e.charger_id||e.delivery_id||''}`;b.setAttribute('aria-label',b.title);b.onclick=()=>{minute=start;setPlaying(false);updatePlayback();};track.append(b);}track.append(node('div',undefined,'time-cursor'));r.append(track);$('timeline').append(r);};
sim.vans.forEach((v,i)=>row(v.id,v.events,colors[i%4]));sim.chargers.forEach(c=>row(c.id,sim.vans.flatMap(v=>v.events.filter(e=>e.type==='charge'&&e.charger_id===c.id)),'#8af1d0'));}
function updatePlayback(){updateInspector();$('clock').textContent=clock(minute);$('scrub').value=minute;if(!sim){$('delivered').textContent='—';return;}const done=FleetReplay.deliveredAt(sim,minute);$('delivered').textContent=`${done.size} / ${sim.deliveries.length}`;sim.vans.forEach((v,i)=>{const state=FleetReplay.stateAt(v,minute,sim.depot),card=$('fleet').children[i],pct=state.battery/sim.battery_capacity_kwh*100;card.querySelector('.van-state').textContent=state.status;card.querySelector('.van-energy').textContent=`${state.battery.toFixed(1)} kWh · ${pct.toFixed(0)}%`;const fill=card.querySelector('.battery-fill');fill.style.width=`${Math.max(0,Math.min(100,pct))}%`;fill.style.background=pct<sim.reserve_pct?'#ff7d91':colors[i%4];});document.querySelectorAll('.time-cursor').forEach(c=>c.style.left=`${minute/sim.duration_min*100}%`);}
$('play').onclick=()=>{if(!sim)return;if(minute>=sim.duration_min)minute=0;setPlaying(!playing);};$('restart').onclick=()=>{minute=0;setPlaying(false);updatePlayback();};$('scrub').oninput=()=>{minute=Number($('scrub').value);setPlaying(false);updatePlayback();};
function tick(now){const dt=last?Math.min(.1,(now-last)/1000):0;last=now;if(playing&&sim){minute=Math.min(sim.duration_min,minute+dt*Number($('speed').value));if(minute>=sim.duration_min)setPlaying(false);updatePlayback();}requestAnimationFrame(tick);}requestAnimationFrame(tick);
let pygameView=false,viewRevision=0,frameUrl=null;
function startVisualizer(){
  pygameView=true;const revision=++viewRevision;
  $('pygame-help').hidden=false;updateInspector();
  clearTimeout(frameTimer);
  pygameScene=null;$('pygame-frame').hidden=true;$('frame-status').hidden=false;$('frame-status').textContent='Loading map...';
  let lastKey='';
  async function pump(){
    if(revision!==viewRevision||!pygameView)return;
    let delay=150;const frameStarted=Date.now(),viewId=result?.view_id;
    try{
      if(!result||!sim){$('frame-status').textContent='Build a plan to see the map.';return;}
      const resolution=PygameInput.resolutionFor($('pygame-view').getBoundingClientRect(),window.devicePixelRatio||1);
      const key=`${viewId}:${minute.toFixed(2)}:${selectedVan||''}:${playing}:${$('speed').value}:${resolution}`;
      if(key===lastKey)return;
      const query=new URLSearchParams({view_id:viewId,minute:minute.toFixed(2),selected:selectedVan||'',playing:String(playing),speed:$('speed').value,resolution:String(resolution)});
      const response=await fetch('/api/pygame-frame?'+query);
      if(!response.ok){const message=(await response.json()).error||'Unable to render Pygame frame.';
        if(revision!==viewRevision||viewId!==result?.view_id)return;
        $('frame-status').textContent=message;$('frame-status').hidden=false;$('pygame-frame').hidden=true;
        delay=response.status===503?3000:1500;return;
      }
      const metadata=response.headers?.get('X-Pygame-Scene');const scene=metadata?JSON.parse(metadata):null;
      const blob=await response.blob();
      if(revision!==viewRevision||viewId!==result?.view_id)return;
      const url=URL.createObjectURL(blob),image=$('pygame-frame');
      const previousUrl=frameUrl;frameUrl=url;
      image.onload=()=>{
        if(revision===viewRevision&&viewId===result?.view_id){image.hidden=false;$('frame-status').hidden=true;pygameScene=scene;pygameSceneView=viewId;}
      };
      image.onerror=()=>{
        if(revision===viewRevision&&viewId===result?.view_id){$('frame-status').textContent='The browser could not display the Pygame frame.';$('frame-status').hidden=false;}
      };
      image.src=url;
      if(previousUrl)URL.revokeObjectURL(previousUrl);
      lastKey=key;delay=playing?Math.max(16,50-(Date.now()-frameStarted)):150;
    }catch(e){if(revision===viewRevision&&viewId===result?.view_id){$('frame-status').textContent='Could not reach the Pygame renderer. Check that server.py is running.';$('frame-status').hidden=false;}delay=1500;}
    finally{if(revision===viewRevision&&pygameView)frameTimer=setTimeout(pump,delay);}
  }
  pump();
}
function applyVanSelection(){if(!sim)return;[...$('fleet').children].forEach((card,i)=>{const active=selectedVan===sim.vans[i].id;card.classList.toggle('selected',active);card.setAttribute('aria-pressed',String(active));});}
function inspect(target){
  if(inspected?.kind===target.kind&&inspected?.id===target.id){inspected=null;if(target.kind==='van')selectedVan=null;}
  else{inspected={kind:target.kind,id:target.id};if(target.kind==='van')selectedVan=target.id;}
  applyVanSelection();updateInspector();
}
function updateInspector(){
  const panel=$('pygame-inspector');panel.hidden=!pygameView||!inspected||!sim;
  if(panel.hidden)return;
  if(inspected.kind==='van'){
    const van=sim.vans.find(v=>v.id===inspected.id);if(!van){panel.hidden=true;return;}
    const state=FleetReplay.stateAt(van,minute,sim.depot);
    const upcoming=van.events.find(e=>e.end_min>minute);
    panel.textContent=`${van.id} | ${state.status} | ${state.battery.toFixed(2)} kWh (${(state.battery/sim.battery_capacity_kwh*100).toFixed(0)}%) | Deliveries: ${(van.delivery_ids||[]).join(', ')}${upcoming?` | Current/next event ends ${clock(upcoming.end_min)}`:''}`;
  }else if(inspected.kind==='charger'){
    const reservations=sim.vans.flatMap(v=>v.events.filter(e=>e.type==='charge'&&e.charger_id===inspected.id).map(e=>({van:v.id,...e})));
    const active=reservations.find(e=>e.start_min<=minute&&minute<e.end_min);
    panel.textContent=`${inspected.id} | ${active?`Charging ${active.van} until ${clock(active.end_min)}`:'Available at this time'} | Reservations: ${reservations.map(e=>`${e.van} ${clock(e.start_min)}-${clock(e.end_min)}`).join('; ')||'none'}`;
  }else{
    const delivery=sim.deliveries.find(d=>d.id===inspected.id);if(!delivery){panel.hidden=true;return;}
    const van=sim.vans.find(v=>v.events.some(e=>e.delivery_id===delivery.id));
    const event=van?.events.find(e=>e.delivery_id===delivery.id);
    const complete=FleetReplay.deliveredAt(sim,minute).has(delivery.id);
    const lateness=event && Math.max(0,event.end_min-delivery.deadline_min);
    panel.textContent=`${delivery.id} at (${delivery.position.join(', ')}) | ${complete?'Delivered':'Pending'} | ${van?.id||'Unassigned'}${event?` | Service completes ${clock(event.end_min)}`:''}${Number.isFinite(delivery.deadline_min)?` | Deadline ${clock(delivery.deadline_min)}`:''}${lateness>0?` | ${lateness} min late`:''}`;
  }
}
function seek(to){if(!sim)return;minute=Math.max(0,Math.min(sim.duration_min,to));setPlaying(false);updatePlayback();}
function handleFrameTarget(target,point){
  if(!sim||!target)return;
  if(['van','charger','delivery'].includes(target.kind))inspect(target);
  else if(target.kind==='play')$('play').onclick();
  else if(target.kind==='restart')$('restart').onclick();
  else if(target.kind==='back')seek(minute-5);
  else if(target.kind==='forward')seek(minute+5);
  else if(target.kind==='speed'){const speeds=['1','4','12'];$('speed').value=speeds[(speeds.indexOf($('speed').value)+1)%speeds.length];}
  else if(target.kind==='clear'){inspected=null;selectedVan=null;applyVanSelection();updateInspector();}
  else if(target.kind==='scrub')seek(PygameInput.scrubMinute(target,point,sim.duration_min));
}
const pygameImage=$('pygame-frame');pygameImage.draggable=false;pygameImage.tabIndex=0;
pygameImage.setAttribute('aria-label','Interactive fleet map. Space to play or pause, arrows to seek, click objects to inspect.');
function imagePoint(event){return pygameScene&&PygameInput.framePoint(pygameImage.getBoundingClientRect(),pygameScene.width,pygameScene.height,event.clientX,event.clientY);}
pygameImage.onpointerdown=event=>{
  if(pygameSceneView!==result?.view_id||event.button>0)return;
  const point=imagePoint(event),target=PygameInput.hitAt(pygameScene,point);if(!target)return;
  event.preventDefault();pygameImage.focus();
  if(target.kind==='scrub'){scrubbing={target,pointer:event.pointerId};pygameImage.setPointerCapture(event.pointerId);}
  handleFrameTarget(target,point);
};
pygameImage.onpointermove=event=>{
  const point=imagePoint(event),target=PygameInput.hitAt(pygameScene,point);
  pygameImage.style.cursor=target?'pointer':'default';pygameImage.title=target?`${target.id||target.kind} - click to interact`:'';
  if(scrubbing&&event.pointerId===scrubbing.pointer&&point&&pygameSceneView===result?.view_id)handleFrameTarget(scrubbing.target,point);
};
function releaseScrub(){scrubbing=null;}
pygameImage.onpointerup=releaseScrub;pygameImage.onpointercancel=releaseScrub;pygameImage.onlostpointercapture=releaseScrub;
pygameImage.onkeydown=event=>{
  if(!sim||pygameSceneView!==result?.view_id)return;
  const entityKinds={v:['van',sim.vans],c:['charger',sim.chargers],d:['delivery',sim.deliveries]};
  const group=entityKinds[event.key.toLowerCase()];
  if(group&&group[1].length){event.preventDefault();const index=inspected?.kind===group[0]?group[1].findIndex(item=>item.id===inspected.id):-1;const next=group[1][(index+1)%group[1].length];inspect({kind:group[0],id:next.id});return;}
  const actions={' ':()=> $('play').onclick(),ArrowLeft:()=>seek(minute-(event.shiftKey?15:5)),ArrowRight:()=>seek(minute+(event.shiftKey?15:5)),Home:()=>seek(0),End:()=>seek(sim.duration_min),Escape:()=>handleFrameTarget({kind:'clear'})};
  if(actions[event.key]){event.preventDefault();actions[event.key]();}
};
function stopVisualizer(){pygameView=false;viewRevision++;clearTimeout(frameTimer);if(frameUrl){URL.revokeObjectURL(frameUrl);frameUrl=null;}}
window.addEventListener('pagehide',stopVisualizer);
$('export').onclick=()=>{if(!result)return;const url=URL.createObjectURL(new Blob([JSON.stringify(result,null,2)],{type:'application/json'})),a=node('a');a.href=url;a.download=`ev-dispatch-${result.run_id}-${result.attempt_id}.json`;a.click();setTimeout(()=>URL.revokeObjectURL(url),1000);};
$('feedback').onclick=async()=>{if(!result)return;const feedback=prompt('Feedback is saved for this server session. What should change?');if(!feedback)return;try{await request('/api/feedback',{view_id:result.view_id,feedback});$('status').textContent='Feedback saved locally for this server session';}catch(e){error(e.message);}};
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
    backendConfigured = config.backend_configured;
    $('live-mode').disabled = !backendConfigured;
    $('connection').textContent = backendConfigured ? 'Backend configured' : 'Local reference';
    initialized = true;
    updateMode();
    startVisualizer();
    initialRun();
  } catch (e) {
    error('Unable to load the local service. Start the dashboard server and refresh.');
    $('connection').textContent = 'Service unavailable';
  }
}

init();
