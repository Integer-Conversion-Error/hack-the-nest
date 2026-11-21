const assert = require('node:assert/strict');
const test = require('node:test');
const E = require('../web/problems/energy.js');

const near = (actual, expected) => assert.ok(Math.abs(actual - expected) < 1e-7, `${actual} != ${expected}`);

function independentSearch(input) {
  let best = Infinity;
  const efficiency = Math.sqrt(input.efficiency / 100);
  function visit(index, stored, cost) {
    if (index === input.intervals.length) {
      if (stored === input.initial) best = Math.min(best, cost);
      return;
    }
    const interval = input.intervals[index];
    for (let next = input.reserve; next <= input.capacity; next++) {
      const delta = next - stored,
        charging = Math.max(0, delta) / efficiency,
        delivered = Math.max(0, -delta) * efficiency;
      if (Math.max(charging, delivered) > input.power * 3 + 1e-8) continue;
      const net = interval.load - interval.solar + charging - delivered;
      if (Math.max(0, net) > input.gridLimit * 3 + 1e-8) continue;
      visit(
        index + 1,
        next,
        cost +
          Math.max(0, net) * interval.price -
          Math.max(0, -net) * input.feedIn +
          Math.abs(delta) * input.cycling,
      );
    }
  }
  visit(0, input.initial, 0);
  return best;
}

test('energy dynamic program matches exhaustive complete schedules across tariffs and losses', () => {
  for (const efficiency of [75, 90, 100])
    for (const feedIn of [0, 0.15, 0.6]) {
      const input = {
        ...E.seed(),
        capacity: 3,
        reserve: 1,
        initial: 2,
        power: 0.5,
        gridLimit: 3,
        efficiency,
        feedIn,
        intervals: [
          { load: 1, solar: 4, price: 0.1 },
          { load: 4, solar: 0, price: 0.7 },
          { load: 2, solar: 1, price: 0.25 },
          { load: 0, solar: 0, price: 0.05 },
        ],
      };
      const result = E.solve(input);
      assert.equal(result.status, 'ok');
      near(result.cost, independentSearch(input));
    }
});

test('default energy dispatch conserves energy and respects every physical bound', () => {
  const input = E.seed(),
    result = E.solve(input);
  assert.equal(result.status, 'ok');
  assert.ok(result.savings > 0);
  let previous = input.initial;
  result.rows.forEach((row, i) => {
    assert.equal(row.before, previous);
    previous = row.after;
    assert.ok(Number.isInteger(row.after) && row.after >= input.reserve && row.after <= input.capacity);
    assert.ok(Math.max(row.charge, row.discharge) <= input.power * 3 + 1e-8);
    assert.ok(row.imports <= input.gridLimit * 3 + 1e-8);
    assert.ok(row.charge === 0 || row.discharge === 0);
    near(
      input.intervals[i].solar + row.imports + row.discharge,
      input.intervals[i].load + row.exports + row.charge,
    );
    near(row.charge - row.discharge, row.after - row.before + row.loss);
  });
  assert.equal(previous, input.initial);
  near(
    input.intervals.reduce((sum, interval) => sum + interval.solar + 0, 0) + result.imports,
    input.intervals.reduce((sum, interval) => sum + interval.load, 0) + result.exports + result.loss,
  );
});

test('zero battery reproduces the same feasible no-storage tariff baseline', () => {
  const input = { ...E.seed(), capacity: 0, initial: 0, reserve: 0, power: 0 };
  const result = E.solve(input);
  assert.equal(result.status, 'ok');
  near(result.cost, result.baseline.cost);
  near(result.savings, 0);
  assert.equal(result.discharge, 0);
});

test('an infeasible no-storage baseline is labeled instead of claiming savings', () => {
  const input = {
    ...E.seed(),
    capacity: 4,
    reserve: 0,
    initial: 4,
    efficiency: 100,
    power: 2,
    gridLimit: 1,
    intervals: [
      { load: 6, solar: 0, price: 1 },
      { load: 0, solar: 0, price: 0.1 },
    ],
  };
  const result = E.solve(input);
  assert.equal(result.status, 'ok');
  assert.equal(result.baseline.feasible, false);
  assert.equal(result.savings, null);
  assert.ok(result.rows[0].discharge >= 3);
  assert.equal(result.rows.at(-1).after, 4);
});

test('energy infeasibility and malformed parameters never return a dispatch', () => {
  const blocked = { ...E.seed(), capacity: 0, initial: 0, reserve: 0, power: 0, gridLimit: 0 };
  assert.equal(E.solve(blocked).status, 'infeasible');
  for (const change of [
    { capacity: 1.5 },
    { initial: 41 },
    { reserve: 5 },
    { capacity: '' },
    { efficiency: Infinity },
    { power: false },
    { intervals: [null] },
  ]) {
    const result = E.solve({ ...E.seed(), ...change });
    assert.equal(result.status, 'invalid', JSON.stringify(change));
    assert.equal(result.rows, undefined);
  }
});

test('high cycling costs and flat tariff avoid unnecessary battery activity', () => {
  const input = {
    ...E.seed(),
    efficiency: 100,
    feedIn: 0.1,
    cycling: 1,
    intervals: Array.from({ length: 8 }, () => ({ load: 5, solar: 1, price: 0.1 })),
  };
  const result = E.solve(input);
  near(result.discharge, 0);
  near(result.charge, 0);
  near(result.cost, result.baseline.cost);
});
