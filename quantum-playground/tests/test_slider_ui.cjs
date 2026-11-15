process.chdir(require('node:path').resolve(__dirname, '..'));
/* Exercise app event handlers without browser dependencies; this is not visual QA. */
const assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm');
const {execFileSync}=require('node:child_process');
const fixture=JSON.parse(execFileSync(process.env.PYTHON||'python',['-c','import json; from ev_demo import demo; print(json.dumps(demo("EV demo", {})))'],{encoding:'utf8'}));
const scene=JSON.parse(execFileSync(process.env.PYTHON||'python',['-c','import json; from ev_demo import demo; from pygame_renderer import scene_layout; print(json.dumps(scene_layout(demo("EV", {})["simulation"],60)))'],{encoding:'utf8'}));
const registry=new Map();
class Element {
  constructor(tag){this.tag=tag;this.children=[];this.dataset={};this.style={setProperty(){}};this.checked=false;this.value='';this.attrs={};this.classes=new Set();this.classList={toggle:(key,on)=>on?this.classes.add(key):this.classes.delete(key)};}
  set id(value){this._id=value;registry.set(value,this);}get id(){return this._id;}
  append(...items){this.children.push(...items);}replaceChildren(...items){this.children=items;}
  setAttribute(key,value){this.attrs[key]=value;}
  querySelectorAll(selector){return this.children.flatMap(c=>[...(selector.startsWith('.')?c.className?.split(' ').includes(selector.slice(1))? [c]:[]:c.tag===selector?[c]:[]),...c.querySelectorAll(selector)]);}
  querySelector(selector){return this.querySelectorAll(selector)[0];}
  reportValidity(){return Boolean(this.value.trim());}
  getContext(){return {};}
  getBoundingClientRect(){return {left:0,top:0,width:840,height:620};}
  focus(){}
  setPointerCapture(){}
  set src(value){this._src=value;setTimeout(()=>this.onload?.(),0);}
}
for(const [,id]of fs.readFileSync('web/index.html','utf8').matchAll(/id="([^"]+)"/g)){const el=new Element('div');el.id=id;}
registry.get('mode').value='demo';registry.get('auto-update').checked=true;
registry.get('speed').value='4';
registry.get('all-variables').children=['variables','battery-variables','extra-variables'].map(id=>registry.get(id));
const posts=[],frames=[];
const context=vm.createContext({console,setTimeout,clearTimeout,setInterval,clearInterval,structuredClone,URLSearchParams,
  URL:{createObjectURL:()=> 'blob:test-frame',revokeObjectURL(){}},
  requestAnimationFrame(){},window:{addEventListener(){}},
  document:{getElementById:id=>registry.get(id),createElement:tag=>new Element(tag),querySelectorAll:selector=>[...registry.values()].flatMap(e=>e.querySelectorAll(selector))},
  fetch:async(path,options)=>{
    if(path.startsWith('/api/pygame-frame?')){frames.push(path);return {ok:true,headers:{get:()=>JSON.stringify(scene)},blob:async()=>({})};}
    return {ok:true,json:async()=>{
    if(path==='/api/config')return {};
    if(path==='/api/example')return {prompt:'EV example',variables:fixture.variables};
    const payload=JSON.parse(options.body);posts.push(payload);const next=structuredClone(fixture);next.mode=payload.mode;next.problem.original_prompt=payload.prompt;return next;
  }};}
});
for(const name of ['fleet.js','live-updates.js','pygame-input.js','app.js'])vm.runInContext(fs.readFileSync('web/'+name,'utf8'),context);
const wait=ms=>new Promise(resolve=>setTimeout(resolve,ms));
async function main(){
  await wait(240);
  assert.equal(posts.length,1,'preview loads automatically');
  assert.equal(registry.get('statement-label').textContent,'Example problem statement');
  assert.equal(registry.get('submitted-prompt').textContent,'EV example');
  assert.equal(registry.get('circuits').children.length,3);
  assert.equal(registry.get('circuit-tag').textContent,'WAITING');
  registry.get('prompt').value='An edited draft';
  assert.equal(registry.get('submitted-prompt').textContent,'EV example','last submitted prompt must remain separate from draft');
  const scrub=registry.get('scrub');scrub.value='60';scrub.oninput();
  registry.get('fleet').children[0].onclick();
  const slider=registry.get('variable-initial_1');slider.value='80';slider.oninput();
  await wait(240);
  assert.equal(posts.length,2);assert.equal(posts[1].variables.initial_1,80);
  assert.equal(registry.get('variable-initial_1'),slider,'dragged control must not be replaced');
  assert.equal(registry.get('clock').textContent,'09:00','replay time must be preserved');
  assert.equal(vm.runInContext('selectedVan',context),'V1');
  assert.equal(vm.runInContext('playing',context),false,'paused playback stays paused');
  registry.get('auto-update').checked=false;registry.get('auto-update').onchange();
  slider.value='90';slider.oninput();await wait(220);assert.equal(posts.length,2);
  registry.get('auto-update').checked=true;registry.get('auto-update').onchange();await wait(240);
  assert.equal(posts.length,3);assert.equal(posts[2].variables.initial_1,90);
  await wait(40);
  assert.ok(frames.length>=1);
  assert.ok(frames.at(-1).includes('minute=60.00'));
  assert.equal(registry.get('pygame-frame').hidden,false);
  assert.equal(registry.get('frame-status').hidden,true);
  assert.equal(registry.get('clock').textContent,'09:00');
  const image=registry.get('pygame-frame');
  const click=(target,corner=false)=>image.onpointerdown({clientX:target.rect[0]+(corner?2:target.rect[2]/2),clientY:target.rect[1]+(corner?target.rect[3]-2:target.rect[3]/2),button:0,pointerId:1,preventDefault(){}});
  click(scene.targets.find(t=>t.kind==='charger'),true);
  assert.ok(registry.get('pygame-inspector').textContent.includes('Reservations:'));
  click(scene.targets.find(t=>t.kind==='van'&&t.id==='V2'&&t.rect[1]===440));
  assert.equal(vm.runInContext('selectedVan',context),'V2');
  assert.ok(registry.get('pygame-inspector').textContent.includes('kWh'));
  click(scene.targets.find(t=>t.kind==='play'));
  assert.equal(vm.runInContext('playing',context),true);
  click(scene.targets.find(t=>t.kind==='scrub'));
  assert.equal(registry.get('clock').textContent,'10:00');
  assert.equal(vm.runInContext('playing',context),false);
  image.onpointerup();
  image.onkeydown({key:'c',preventDefault(){}});
  assert.ok(registry.get('pygame-inspector').textContent.includes('Reservations:'));
  image.onkeydown({key:'d',preventDefault(){}});
  assert.ok(registry.get('pygame-inspector').textContent.includes('Deadline'));
  image.onkeydown({key:'ArrowRight',preventDefault(){}});
  assert.equal(registry.get('clock').textContent,'10:05');
  image.onkeydown({key:'Escape',preventDefault(){}});
  assert.equal(vm.runInContext('selectedVan',context),null);
  context.window.devicePixelRatio=2;
  await wait(200);
  assert.ok(frames.at(-1).includes('resolution=2'),'a density change must refresh even a paused frame');
  assert.equal(registry.get('clock').textContent,'10:05');
  assert.equal(registry.has('graph-tab'),false);
  assert.equal(registry.has('graph'),false);
  vm.runInContext('stopVisualizer()',context);
  context.liveResult={...structuredClone(fixture),mode:'live',problem:{title:'A real plan',statement:'A short problem statement.',original_prompt:'The original request.'},circuits:[{title:'Solver circuit',drawing:'q0: --H--M--'}]};
  vm.runInContext('render(liveResult)',context);
  assert.equal(registry.get('statement-label').textContent,'Problem statement');
  assert.equal(registry.get('submitted-prompt').textContent,'The original request.');
  assert.equal(registry.get('statement').textContent,'A short problem statement.');
  assert.equal(registry.get('circuits').children.length,1);
  assert.equal(registry.get('circuit-tag').textContent,'READY');
  console.log('PASS: automatic initial preview, slider-to-request wiring, drag focus, replay position, selected van, and manual toggle');
}
main().catch(e=>{vm.runInContext('stopVisualizer()',context);console.error(e);process.exitCode=1;});
