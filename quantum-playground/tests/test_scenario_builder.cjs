const assert = require('node:assert/strict');
const test = require('node:test');
const D = require('../web/scenario-draft.js');
const { isValidValue } = require('../web/scenario-controls.js');

test('example supports two depots and multiple required deliveries on one vehicle', () => {
  const scenario = D.seed();
  assert.deepEqual(D.validate(scenario), []);
  assert.equal(scenario.depots.length, 2);
  assert.deepEqual(scenario.deliveries.filter(stop => stop.vehicle_id === 'V1').map(stop => stop.id), ['D1', 'D2']);
  assert.equal(scenario.vehicles[0].initial_charge_pct, 40);
});

test('temporarily invalid numeric text survives persistence and cannot become a zero on submit', () => {
  const draft = D.seed();
  draft.vehicles[0].initial_charge_pct = '';
  draft.chargers[0].slots[0].start_min = 'not a number';
  let stored;
  const storage = { setItem(key, value) { assert.equal(key, D.storageKey); stored = value; }, getItem() { return stored; } };
  assert.equal(D.save(storage, draft), true);
  const restored = D.load(storage);
  assert.equal(restored.vehicles[0].initial_charge_pct, '');
  assert.equal(restored.chargers[0].slots[0].start_min, 'not a number');
  assert.ok(D.validate(restored).some(error => error.includes('V1 starting charge')));
  assert.equal(Number.isNaN(D.normalize(restored).vehicles[0].initial_charge_pct), true);
});

test('disabled or corrupt browser storage falls back to a working example', () => {
  assert.deepEqual(D.load({ getItem() { throw Error('disabled'); } }), D.seed());
  assert.deepEqual(D.load({ getItem() { return '{'; } }), D.seed());
  assert.equal(D.save({ setItem() { throw Error('full'); } }, D.seed()), false);
});

test('QuantumLoop restores a draft saved under the former product name', () => {
  const draft = D.seed();
  draft.title = 'Retained draft';
  const legacy = JSON.stringify({ version: 1, draft });
  const restored = D.load({ getItem: key => key === 'fleet-lab-scenario-draft-v1' ? legacy : null });
  assert.equal(D.storageKey, 'quantumloop-scenario-draft-v1');
  assert.equal(restored.title, 'Retained draft');
});

test('validation catches broken depot and delivery ownership, grid bounds, and overlapping slots', () => {
  const draft = D.seed();
  draft.vehicles[0].depot_id = 'deleted';
  draft.deliveries[0].vehicle_id = '';
  draft.deliveries[1].position = [6, 1];
  draft.chargers[0].slots[1].start_min = 59;
  const errors = D.validate(draft);
  for (const text of ['V1 needs a starting depot', 'D1 needs a vehicle', 'D2 X', 'C1 charging slots must not overlap']) {
    assert.ok(errors.some(error => error.includes(text)), text);
  }
  draft.chargers[0].slots = [];
  assert.ok(!D.validate(draft).some(error => error.includes('C1 charging slots')));
});

test('validation enforces per-vehicle stop and total entity limits', () => {
  const draft = D.seed();
  draft.deliveries = Array.from({ length: 11 }, (_, index) => ({ ...draft.deliveries[0], id: 'D' + (index + 1) }));
  assert.ok(D.validate(draft).includes('V1 can have at most 10 delivery stops.'));
  draft.vehicles = Array.from({ length: 7 }, (_, index) => ({ ...draft.vehicles[0], id: 'V' + (index + 1) }));
  assert.ok(D.validate(draft).includes('Use 1–6 vehicles.'));
});

function source() {
  return {
    view_id: 'verified-view',
    variables: [{ id: 'charge', type: 'number', minimum: 20, maximum: 80, step: 5 },
      { id: 'slot', type: 'number', minimum: 0, maximum: 40, step: 5 }],
    effective_values: { charge: 40, slot: 30 },
    scenario_context: { effective_scenario: D.seed(), bindings: {
      charge: ['vehicles', 'V1', 'initial_charge_pct'],
      slot: ['chargers', 'C1', 'slots', 'S1', 'start_min'],
    } },
  };
}

test('approved scalar changes reuse the displayed solver, including charger slot bindings', () => {
  const view = source(), scenario = D.seed();
  scenario.vehicles[0].initial_charge_pct = 45;
  scenario.chargers[0].slots[0].start_min = 35;
  assert.deepEqual(D.reuse(view, scenario, isValidValue), { view_id: 'verified-view', variables: { charge: 45, slot: 35 } });
  assert.equal(view.scenario_context.effective_scenario.vehicles[0].initial_charge_pct, 40);
});

test('topology, assignments, unbound scalars, and out-of-range edits need a fresh plan', () => {
  for (const edit of [
    scenario => { scenario.depots[0].position = [3, 3]; },
    scenario => { scenario.deliveries[0].vehicle_id = 'V2'; },
    scenario => { scenario.costs.driving_per_km = 1; },
    scenario => { scenario.vehicles[0].initial_charge_pct = 90; },
  ]) {
    const scenario = D.seed(); edit(scenario);
    assert.equal(D.reuse(source(), scenario, isValidValue), null);
  }
});

test('canonical equality ignores property and stable entity order, preserving semantic changes', () => {
  const scenario = D.seed(), reversed = D.seed();
  reversed.vehicles.reverse(); reversed.deliveries.reverse(); reversed.chargers[0].slots.reverse();
  reversed.grid = { height: 6, block_km: 0.5, width: 6 };
  assert.equal(D.same(scenario, reversed), true);
  assert.deepEqual(D.reuse(source(), reversed, isValidValue), { view_id: 'verified-view', variables: {} });
  reversed.deliveries[0].position.reverse();
  assert.equal(D.same(scenario, reversed), false);
});

test('normalization converts form strings without mutating the saved draft', () => {
  const draft = D.seed(); draft.grid.width = '7'; draft.deliveries[0].position = ['1', '2']; draft.costs.driving_per_km = '0.45';
  const scenario = D.normalize(draft);
  assert.equal(scenario.grid.width, 7);
  assert.deepEqual(scenario.deliveries[0].position, [1, 2]);
  assert.equal(scenario.costs.driving_per_km, 0.45);
  assert.equal(draft.grid.width, '7');
});
