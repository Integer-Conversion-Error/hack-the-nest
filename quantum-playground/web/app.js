const $=id=>document.getElementById(id), colors=['#8af1d0','#a0abff','#ffc177','#fa8faf'];
const changed=new Set();
let inspected=null,pygameScene=null,pygameSceneRun=null,scrubbing=null;
let result=null,sim=null,busy=false,playing=false,minute=0,last=0,frameTimer=null,specs=[],defaults=[],examplePrompt='',selectedVan=null;
const node=(tag,text,cls)=>{const n=document.createElement(tag);if(text!==undefined)n.textContent=text;if(cls)n.className=cls;return n;};
function error(message){$('error').textContent=message;$('error').hidden=false;}
function controls(definitions){specs=definitions;changed.clear();for(const id of ['variables','battery-variables','extra-variables'])$(id).replaceChildren();for(const v of definitions){const wrap=node('div',undefined,'control'),label=node('label',v.label||v.id),out=node('output',`${v.default} ${v.unit||''}`),input=node('input');input.type='range';input.id=`variable-${v.id}`;input.dataset.variable=v.id;input.min=v.min;input.max=v.max;input.step=v.step||1;input.value=v.default;label.htmlFor=input.id;input.oninput=()=>{changed.add(v.id);out.textContent=`${input.value} ${v.unit||''}`;queueRun(true);};label.append(out);const bounds=node('div',undefined,'bounds');bounds.append(node('span',v.min),node('span',v.max));wrap.append(label,input,bounds);const group=v.id.startsWith('initial_')?'battery-variables':['battery_kwh','charging_kw','reserve_pct'].includes(v.id)?'variables':'extra-variables';$(group).append(wrap);}}
function values(){return Object.fromEntries([...$('all-variables').querySelectorAll('input')].filter(x=>$('mode').value==='demo'||(result?.mode==='live'&&result?.status!=='needs_input')||changed.has(x.dataset.variable)).map(x=>[x.dataset.variable,Number(x.value)]));}
async function request(path,data){const r=await fetch(path,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(data)}),body=await r.json();if(!r.ok)throw Error(body.error||'Request failed');return body;}
function clock(m){const total=Math.round((sim?.start_hour??8)*60+m);return `${String(Math.floor(total/60)).padStart(2,'0')}:${String(total%60).padStart(2,'0')}`;}
function setPlaying(value){playing=value;$('play').textContent=value?'Ⅱ Pause':'▶ Play';}
const updater=createLatestUpdater({
  execute:payload=>request('/api/run',payload),
  onBusy:value=>{busy=value;$('generate').disabled=value;$('rerun').disabled=value||!result;},
  onResult:(data,meta)=>{const previous=result;render(data,{preserve:meta.automatic});result=data;if(meta.automatic&&!previous&&sim)setPlaying(true);
    const delta=previous?.mode===data.mode&&typeof previous.evaluation?.objective_value==='number'&&typeof data.evaluation?.objective_value==='number'?data.evaluation.objective_value-previous.evaluation.objective_value:null;
    $('change-summary').textContent=delta===null?'Drag a slider to explore a different scenario.':`Cost ${delta===0?'unchanged':`${delta>0?'+':'-'}$${Math.abs(delta).toFixed(2)}`} vs previous plan.`;
    $('update-state').textContent='Updated';$('update-state').dataset.state='ready';},
  onError:e=>{error(e.message);$('status').textContent='Update failed. The displayed plan is the last successful result.';$('update-state').textContent='Update failed - retry';$('update-state').dataset.state='error';}
});
function queueRun(automatic=false){
  if(!$('prompt').value.trim()){if(!automatic)$('prompt').reportValidity();return;}
  if(automatic&&!$('auto-update').checked)return;
  if(automatic&&$('mode').value==='live'&&(result?.mode!=='live'||result?.status==='needs_input')){
    $('update-state').textContent='Build a plan to apply your changes';return;
  }
  $('error').hidden=true;
  $('update-state').textContent=automatic?'Updating...':'Building plan...';$('update-state').dataset.state='pending';
  $('status').textContent=$('mode').value==='demo'?'Updating the example...':'Building your plan...';
  updater.schedule({prompt:$('prompt').value,mode:$('mode').value,variables:values()},{automatic},automatic?($('mode').value==='demo'?180:900):0);
}
function run(){queueRun(false);}
$('prompt-form').onsubmit=e=>{e.preventDefault();run();};$('rerun').onclick=run;
$('reset').onclick=()=>{controls(defaults.length?defaults:specs);specs.forEach(v=>changed.add(v.id));queueRun(true);};
$('auto-update').onchange=()=>{if($('auto-update').checked)queueRun(true);else{updater.cancel();$('update-state').textContent='Manual updates';}};
$('mode').onchange=()=>{updater.cancel();const live=$('mode').value==='live';$('agent-label').textContent=live?'Live agent - solver and evaluator tools':'Demo - no API calls';$('mode-note').textContent=live?'Gemini reads your prompt. Sliders update the plan.':'Example mode uses the sample map, not prompt edits.';$('update-state').textContent=live?'Generate a live plan to begin':'Updating preview...';if(!live)queueRun(true);};
$('load-example').onclick=()=>{if(examplePrompt){$('prompt').value=examplePrompt;controls(defaults);queueRun(true);}};
function render(data,{preserve=false}={}){const saved={minute,playing,selectedVan};if(!preserve)inspected=null;sim=FleetReplay.validate(data.simulation)?data.simulation:null;selectedVan=null;minute=0;setPlaying(false);$('problem-title').textContent=data.problem?.title||'Dispatch request';$('statement').textContent=data.problem?.statement||'';$('data-tag').textContent=data.mode==='demo'?'EXAMPLE':'YOUR PLAN';$('statement-label').textContent=data.mode==='demo'?'Example problem statement':'Problem statement';$('submitted-prompt').textContent=data.problem?.original_prompt||'';$('run-badge').textContent=data.mode==='demo'?'EXAMPLE':'YOUR PLAN';$('circuit-tag').textContent=data.mode==='demo'?'WAITING':'READY';
if(!preserve)controls(data.variables||[]);else if(data.variables?.length)specs=data.variables;$('circuits').replaceChildren();
const drawings=data.mode==='demo'?[]:(data.circuits||[]).filter(c=>c.drawing);
$('circuit-tag').textContent=drawings.length?'READY':'WAITING';
for(const c of drawings){const card=node('article',undefined,'circuit-card');card.append(node('h3',c.title||'Circuit drawing'),node('pre',c.drawing));$('circuits').append(card);}
if(!drawings.length){for(let i=1;i<=3;i++){const slot=node('article',undefined,'circuit-placeholder');slot.append(node('span',String(i).padStart(2,'0')),node('p','Waiting for circuit drawing'),node('code','qc.draw()'));$('circuits').append(slot);}}
const q=data.quantum||{};$('quantum-summary').textContent=`${q.binary_variables??'—'} binary decisions · actual qubits: ${q.actual_qubits??'not supplied'}${q.qubit_target?` · target < ${q.qubit_target}`:''}`;
$('histogram').replaceChildren();const counts=Object.entries(data.simulation?.counts||{}),total=counts.reduce((s,[,c])=>s+Number(c),0);for(const [bits,count]of counts){const row=node('div',undefined,'bar-row'),track=node('div',undefined,'bar-track'),bar=node('div',undefined,'bar');bar.style.width=`${total?Number(count)/total*100:0}%`;track.append(bar);row.append(node('span',bits),track,node('span',count));$('histogram').append(row);}if(!counts.length)$('histogram').append(node('p','No quantum measurement counts supplied.','hint'));
$('assumptions').replaceChildren(...(data.assumptions?.length?data.assumptions:['No assumptions supplied. Check backend inputs before interpreting feasibility.']).map(x=>node('li',x)));
const ev=data.evaluation||{};$('score').textContent=typeof ev.objective_value==='number'?`$${ev.objective_value.toFixed(2)}`:'—';$('feasible').textContent=ev.feasible===true?'Checks passed':ev.feasible===false?'Needs changes':'Not checked';$('feasible').style.color=ev.feasible===false?'#ff9eaa':'';
$('costs').replaceChildren(...Object.entries(ev.costs||{}).map(([k,v])=>node('span',`${k}: $${Number(v).toFixed(2)}`)));$('violations').replaceChildren();$('violations').hidden=!ev.violations?.length;if(ev.violations?.length){$('violations').append(node('strong','Needs attention'));const list=node('ul');list.append(...ev.violations.map(x=>node('li',x)));$('violations').append(list);}
$('explanation').textContent=data.explanation||'No explanation returned.';$('events').replaceChildren(...(data.events||[]).map(x=>node('li',x)));$('status').textContent=data.status==='needs_input'?'Add the missing details to your prompt':data.status==='no_go'?'Some parts of this plan need attention':data.mode==='demo'?'Example plan ready':'Your plan is ready';
$('scrub').max=sim?.duration_min||240;$('end-time').textContent=clock(sim?.duration_min||240);for(const id of ['play','restart','scrub'])$(id).disabled=!sim;$('export').disabled=false;$('feedback').disabled=false;buildFleet();buildTimeline();if(!sim&&data.status!=='needs_input')error('No compatible EV replay data returned. The result and explanation remain available; see docs/BACKEND_CONTRACT.md.');if(preserve&&sim){minute=Math.min(saved.minute,sim.duration_min);selectedVan=saved.selectedVan;setPlaying(saved.playing&&minute<sim.duration_min);[...$('fleet').children].forEach((card,i)=>{const selected=selectedVan===sim.vans[i].id;card.classList.toggle('selected',selected);card.setAttribute('aria-pressed',String(selected));});}updatePlayback();}
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
    let delay=150;const frameStarted=Date.now();
    try{
      if(!result||!sim){$('frame-status').textContent='Build a plan to see the map.';return;}
      const resolution=PygameInput.resolutionFor($('pygame-view').getBoundingClientRect(),window.devicePixelRatio||1);
      const runId=result.run_id,key=`${runId}:${minute.toFixed(2)}:${selectedVan||''}:${playing}:${$('speed').value}:${resolution}`;
      if(key===lastKey)return;
      const query=new URLSearchParams({run_id:runId,minute:minute.toFixed(2),selected:selectedVan||'',playing:String(playing),speed:$('speed').value,resolution:String(resolution)});
      const response=await fetch('/api/pygame-frame?'+query);
      if(!response.ok){const message=(await response.json()).error||'Unable to render Pygame frame.';
        if(revision!==viewRevision)return;
        $('frame-status').textContent=message;$('frame-status').hidden=false;$('pygame-frame').hidden=true;
        delay=response.status===503?3000:1500;return;
      }
      const metadata=response.headers?.get('X-Pygame-Scene');const scene=metadata?JSON.parse(metadata):null;
      const blob=await response.blob();
      if(revision!==viewRevision||runId!==result?.run_id)return;
      const url=URL.createObjectURL(blob),image=$('pygame-frame');
      const previousUrl=frameUrl;frameUrl=url;image.src=url;
      image.onload=()=>{if(revision===viewRevision){image.hidden=false;$('frame-status').hidden=true;pygameScene=scene;pygameSceneRun=runId;}if(previousUrl)URL.revokeObjectURL(previousUrl);};
      image.onerror=()=>{$('frame-status').textContent='The browser could not display the Pygame frame.';$('frame-status').hidden=false;};
      lastKey=key;delay=playing?Math.max(16,50-(Date.now()-frameStarted)):150;
    }catch(e){if(revision===viewRevision){$('frame-status').textContent='Could not reach the Pygame renderer. Check that server.py is running.';$('frame-status').hidden=false;}delay=1500;}
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
    panel.textContent=`${delivery.id} at (${delivery.position.join(', ')}) | ${complete?'Delivered':'Pending'} | ${van?.id||'Unassigned'}${event?` | Service completes ${clock(event.end_min)}`:''}${Number.isFinite(delivery.deadline_min)?` | Deadline ${clock(delivery.deadline_min)}`:''}`;
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
  if(pygameSceneRun!==result?.run_id||event.button>0)return;
  const point=imagePoint(event),target=PygameInput.hitAt(pygameScene,point);if(!target)return;
  event.preventDefault();pygameImage.focus();
  if(target.kind==='scrub'){scrubbing={target,pointer:event.pointerId};pygameImage.setPointerCapture(event.pointerId);}
  handleFrameTarget(target,point);
};
pygameImage.onpointermove=event=>{
  const point=imagePoint(event),target=PygameInput.hitAt(pygameScene,point);
  pygameImage.style.cursor=target?'pointer':'default';pygameImage.title=target?`${target.id||target.kind} - click to interact`:'';
  if(scrubbing&&event.pointerId===scrubbing.pointer&&point&&pygameSceneRun===result?.run_id)handleFrameTarget(scrubbing.target,point);
};
function releaseScrub(){scrubbing=null;}
pygameImage.onpointerup=releaseScrub;pygameImage.onpointercancel=releaseScrub;pygameImage.onlostpointercapture=releaseScrub;
pygameImage.onkeydown=event=>{
  if(!sim)return;
  const entityKinds={v:['van',sim.vans],c:['charger',sim.chargers],d:['delivery',sim.deliveries]};
  const group=entityKinds[event.key.toLowerCase()];
  if(group&&group[1].length){event.preventDefault();const index=inspected?.kind===group[0]?group[1].findIndex(item=>item.id===inspected.id):-1;const next=group[1][(index+1)%group[1].length];inspect({kind:group[0],id:next.id});return;}
  const actions={' ':()=> $('play').onclick(),ArrowLeft:()=>seek(minute-(event.shiftKey?15:5)),ArrowRight:()=>seek(minute+(event.shiftKey?15:5)),Home:()=>seek(0),End:()=>seek(sim.duration_min),Escape:()=>handleFrameTarget({kind:'clear'})};
  if(actions[event.key]){event.preventDefault();actions[event.key]();}
};
function stopVisualizer(){pygameView=false;viewRevision++;clearTimeout(frameTimer);if(frameUrl){URL.revokeObjectURL(frameUrl);frameUrl=null;}}
window.addEventListener('pagehide',stopVisualizer);
$('export').onclick=()=>{if(!result)return;const url=URL.createObjectURL(new Blob([JSON.stringify(result,null,2)],{type:'application/json'})),a=node('a');a.href=url;a.download=`ev-dispatch-${result.run_id}.json`;a.click();setTimeout(()=>URL.revokeObjectURL(url),1000);};
$('feedback').onclick=async()=>{if(!result)return;const feedback=prompt('Feedback is saved for this server session. What should change?');if(!feedback)return;try{await request('/api/feedback',{run_id:result.run_id,feedback});$('status').textContent='Feedback saved locally for this server session';}catch(e){error(e.message);}};
async function init(){try{const [config,example]=await Promise.all([fetch('/api/config').then(r=>r.json()),fetch('/api/example').then(r=>r.json())]);examplePrompt=example.prompt;defaults=example.variables;$('prompt').value=examplePrompt;controls(defaults);$('generate').disabled=false;startVisualizer();queueRun(true);$('connection').textContent=config.gemini_configured&&config.backend_configured?'Gemini connected':'Local workspace';}catch(e){error('Unable to load the local service. Start python3 server.py and refresh.');$('connection').textContent='Service unavailable';}}init();
