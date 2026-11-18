process.chdir(require('node:path').resolve(__dirname, '..'));
const assert=require('node:assert/strict');
const {execFileSync}=require('node:child_process');
const {stateAt,deliveredAt,validate,depotsFor,depotFor}=require('../web/fleet.js');
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
const multi=structuredClone(sim);
multi.depots=[{id:'P1',position:multi.depot},{id:'P2',position:[5,5]}];
delete multi.depot;
multi.vans.forEach((van,index)=>{van.depot_id=`P${index+1}`;});
multi.vans[1].events=[];
assert.equal(validate(multi),true);
assert.equal(depotsFor(multi).length,2);
assert.deepEqual(depotsFor(sim),[{id:'P1',position:sim.depot}]);
assert.deepEqual(depotFor(multi,multi.vans[1]),[5,5]);
assert.deepEqual(stateAt(multi.vans[1],0,depotFor(multi,multi.vans[1])).position,[5,5]);
assert.equal(stateAt(multi.vans[1],120,depotFor(multi,multi.vans[1])).status,'At depot');
multi.vans[1].depot_id='unknown';
assert.equal(validate(multi),false);
multi.vans[1].depot_id='P2';
multi.depots[1].id='P1';
assert.equal(validate(multi),false);
console.log('PASS: EV replay validation, street paths, time boundaries, interpolation, delivery progress, depot return');
