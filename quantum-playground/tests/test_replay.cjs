process.chdir(require('node:path').resolve(__dirname, '..'));
const assert=require('node:assert/strict');
const {execFileSync}=require('node:child_process');
const {stateAt,deliveredAt,validate}=require('../web/fleet.js');
const sim=JSON.parse(execFileSync(process.env.PYTHON||'python',['-c','import json; from ev_reference import reference_handoff; print(json.dumps(reference_handoff({})["result"]["simulation"]))'],{encoding:'utf8'}));
assert.equal(validate(sim),true);
assert.equal(deliveredAt(sim,0).size,0);
assert.equal(deliveredAt(sim,sim.duration_min).size,2);
for(const van of sim.vans){
  assert.deepEqual(stateAt(van,0,sim.depot).position,sim.depot);
  const drive=van.events.find(e=>e.type==='drive'), at=stateAt(van,(drive.start_min+drive.end_min)/2,sim.depot);
  assert.equal(at.status,'Driving');
  assert.equal(at.battery,(drive.battery_start_kwh+drive.battery_end_kwh)/2);
  const end=stateAt(van,sim.duration_min,sim.depot);
  assert.deepEqual(end.position,sim.depot);
  assert.equal(end.status,'At depot');
  for(const e of van.events){
    assert.equal(stateAt(van,e.start_min,sim.depot).battery,e.battery_start_kwh);
  }
}
const broken=structuredClone(sim);
broken.vans[0].events[0].path=[[2,2],[4,4]];
assert.equal(validate(broken),false);
assert.equal(validate({}),false);
console.log('PASS: EV replay validation, street paths, time boundaries, interpolation, delivery progress, depot return');
