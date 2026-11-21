const assert = require('node:assert/strict');
const test = require('node:test');
const { seed, solve } = require('../web/problems/projects.js');

test('project example maximizes value while funding required projects and prerequisites', () => {
  const input = seed(),
    result = solve(input),
    chosen = new Set(result.selected);
  assert.equal(result.status, 'ok');
  assert.equal(result.value, 203);
  assert.equal(result.spend, 94000);
  assert.equal(result.evaluated, 256);
  assert.equal(chosen.has('P3'), true);
  for (const project of input.projects)
    if (chosen.has(project.id) && project.requires) assert.equal(chosen.has(project.requires), true);
  assert.equal(result.selected.length + result.deferred.length, input.projects.length);
});

test('an expensive prerequisite prevents a high-value dependent from bypassing the budget', () => {
  const input = {
    budget: 10,
    projects: [
      { id: 'base', name: 'Foundation', cost: 8, value: 1 },
      { id: 'dependent', name: 'Upgrade', cost: 4, value: 100, requires: 'base' },
      { id: 'standalone', name: 'Standalone', cost: 10, value: 20 },
    ],
  };
  assert.deepEqual(solve(input).selected, ['standalone']);
  input.projects[1].required = true;
  assert.equal(solve(input).status, 'infeasible');
});

test('transitive and mutual prerequisites are enforced by complete selection', () => {
  const input = {
    budget: 3,
    projects: [
      { id: 'A', name: 'A', cost: 1, value: 1, requires: 'B', required: true },
      { id: 'B', name: 'B', cost: 1, value: 1, requires: 'C' },
      { id: 'C', name: 'C', cost: 1, value: 1, requires: 'A' },
    ],
  };
  assert.deepEqual(solve(input).selected, ['A', 'B', 'C']);
  input.budget = 2;
  assert.equal(solve(input).status, 'infeasible');
});

test('budget comparisons use integer cents and equal-value plans use lower spend', () => {
  const input = {
    budget: 0.3,
    projects: [
      { id: 'A', name: 'A', cost: 0.1, value: 1 },
      { id: 'B', name: 'B', cost: 0.2, value: 2 },
      { id: 'C', name: 'C', cost: 0.29, value: 3 },
    ],
  };
  assert.deepEqual(solve(input).selected, ['C']);
  input.projects.pop();
  assert.equal(solve(input).spend, 0.3);
  assert.equal(solve(input).value, 3);
});

test('bounded project inputs reject blanks, missing prerequisites, and excessive rows', () => {
  const input = seed();
  input.budget = '';
  assert.equal(solve(input).status, 'invalid');
  input.budget = 100000;
  input.projects[0].requires = 'missing';
  assert.equal(solve(input).status, 'invalid');
  input.projects = Array.from({ length: 15 }, (_, index) => ({
    id: String(index),
    name: 'Project',
    cost: 1,
    value: 1,
  }));
  assert.equal(solve(input).status, 'invalid');
});

test('zero budget still funds free useful projects without mutating the input', () => {
  const input = { budget: 0, projects: [{ id: 'free', name: 'Free project', cost: 0, value: 5 }] },
    before = JSON.stringify(input);
  assert.deepEqual(solve(input).selected, ['free']);
  assert.equal(JSON.stringify(input), before);
});
