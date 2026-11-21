const assert = require('node:assert/strict');
const test = require('node:test');
const { seed, solve, key } = require('../web/problems/teams.js');

test('robotics example forms two teams with verified skill coverage and exact compatibility', () => {
  const input = seed(),
    result = solve(input);
  assert.equal(result.status, 'ok');
  assert.equal(result.score, 57);
  assert.equal(result.evaluated, 10);
  assert.deepEqual(
    result.teams.map((team) => team.length),
    [3, 3],
  );
  assert.equal(new Set(result.teams.flat()).size, 6);
  let score = 0;
  result.teams.forEach((team, index) => {
    const coverage = [0, 0, 0];
    team.forEach((id, member) => {
      input.people
        .find((person) => person.id === id)
        .skills.forEach((value, skill) => {
          coverage[skill] += value;
        });
      for (const other of team.slice(member + 1)) score += input.compatibility[key(id, other)] ?? 5;
    });
    assert.deepEqual(coverage, result.coverage[index]);
    assert.ok(coverage.every((value) => value >= input.minimum_skill));
  });
  assert.equal(score, result.score);
});

test('editing pair scores changes the optimal assignment', () => {
  const input = {
    people: ['A', 'B', 'C', 'D'].map((id) => ({ id, name: id, skills: [1, 1, 1] })),
    team_count: 2,
    minimum_skill: 2,
    compatibility: { 'A|B': 10, 'C|D': 10, 'A|C': 0, 'A|D': 0, 'B|C': 0, 'B|D': 0 },
  };
  assert.deepEqual(solve(input).teams, [
    ['A', 'B'],
    ['C', 'D'],
  ]);
  Object.assign(input.compatibility, { 'A|B': 0, 'C|D': 0, 'A|C': 10, 'B|D': 10 });
  assert.deepEqual(solve(input).teams, [
    ['A', 'C'],
    ['B', 'D'],
  ]);
});

test('odd group sizes stay balanced and each person appears exactly once', () => {
  const input = seed();
  input.people.push({ id: 'U7', name: 'Gia', skills: [2, 2, 2] });
  input.team_count = 3;
  const result = solve(input);
  assert.equal(result.status, 'ok');
  assert.deepEqual(result.teams.map((team) => team.length).sort(), [2, 2, 3]);
  assert.equal(new Set(result.teams.flat()).size, 7);
});

test('skill constraints can make every balanced split infeasible', () => {
  const input = seed();
  input.minimum_skill = 15;
  const result = solve(input);
  assert.equal(result.status, 'infeasible');
  assert.equal(result.evaluated, 10);
});

test('the ten-person bound remains a complete finite search', () => {
  const input = seed();
  for (let index = 7; index <= 10; index++)
    input.people.push({ id: 'U' + index, name: 'Member ' + index, skills: [1, 1, 1] });
  input.team_count = 3;
  const result = solve(input);
  assert.equal(result.status, 'ok');
  assert.equal(result.evaluated, 2100);
  input.people.push({ id: 'U11', name: 'Extra member', skills: [1, 1, 1] });
  assert.equal(solve(input).status, 'invalid');
});

test('invalid names, blank coverage, and out-of-range pair scores do not trigger a solve', () => {
  const input = seed();
  input.people[0].name = '';
  assert.equal(solve(input).status, 'invalid');
  input.people[0].name = 'Ava';
  input.minimum_skill = '';
  assert.equal(solve(input).status, 'invalid');
  input.minimum_skill = 2;
  input.compatibility['U1|U2'] = 11;
  assert.equal(solve(input).status, 'invalid');
});
