const test = require('node:test');
const assert = require('node:assert/strict');
const { geographicPointAt, stateAt, validate, roadItinerary } = require('../web/fleet.js');

test('geographic replay follows traveled distance rather than counting geometry vertices', () => {
  const path = [[-79, 43], [-78.999, 43], [-78.99, 43]];
  const halfway = geographicPointAt(path, 0.5);
  assert.ok(Math.abs(halfway[0] - -78.995) < 1e-7);
  assert.deepEqual(geographicPointAt(path, 0), path[0]);
  assert.deepEqual(geographicPointAt(path, 1), path.at(-1));
  const van = { initial_battery_kwh: 10, events: [{type:'drive',start_min:0,end_min:10,position:path[0],path,
    battery_start_kwh:10,battery_end_kwh:8}] };
  const state = stateAt(van, 5, path[0], true);
  assert.ok(Math.abs(state.position[0] - halfway[0]) < 1e-8);
  assert.equal(state.battery, 9);
});

test('geographic replay validates road paths and rejects malformed geographic metadata', () => {
  const sim = {kind:'ev_fleet',map:{kind:'geographic',profile:'driving',routing_digest:'a'.repeat(64)},
    start_hour:8,duration_min:120,battery_capacity_kwh:20,reserve_pct:15,
    depots:[{id:'P1',position:[-79,43]}],chargers:[],deliveries:[],
    vans:[{id:'V1',depot_id:'P1',initial_battery_kwh:10,delivery_ids:[],events:[]}]};
  assert.equal(validate(sim), true);
  for (const mutate of [s=>s.map.routing_digest='bad',s=>s.depots[0].position=[-79,91],s=>s.grid={width:2,height:2}]) {
    const broken=structuredClone(sim); mutate(broken); assert.equal(validate(broken),false);
  }
});

test('road totals use recorded drive metrics and exclude service, waiting and charging', () => {
  const sim = require('./fixtures/geographic-road-replay.json').view.simulation;
  const van = sim.vans[0];
  const total = roadItinerary(sim, van);
  assert.deepEqual(total.legs.map(leg => [leg.from_id, leg.to_id]), [['P1','D1'], ['D1','D2'], ['D2','P1']]);
  assert.ok(Math.abs(total.kilometers - 3.4172) < 1e-8);
  assert.ok(Math.abs(total.minutes - (86.1 + 218.7 + 143.3) / 60) < 1e-8);
  const stops = ['deliver','wait','charge'].map(type => ({type, start_min: 20, end_min: 23}));
  assert.deepEqual(roadItinerary(sim, {events: stops}), {legs: [], kilometers: 0, minutes: 0});
  assert.equal(roadItinerary({grid: {width: 5, height: 5}}, van), null);
  assert.equal(roadItinerary(sim, {events: [{type: 'drive', start_min: 0, end_min: 4}]}), null);
});
