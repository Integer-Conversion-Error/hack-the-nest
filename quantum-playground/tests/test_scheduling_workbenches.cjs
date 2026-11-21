const test = require('node:test');
const assert = require('node:assert/strict');
const workforce = require('../web/problems/workforce.js');
const production = require('../web/problems/production.js');
const timetable = require('../web/problems/timetable.js');

function checkRoster(input, result) {
  assert.equal(result.feasible, true);
  const coverage = [0, 0, 0];
  let cost = 0;
  for (const assignment of result.assignments) {
    const worker = input.workers.find((w) => w.id === assignment.workerId);
    assert.ok(worker);
    assert.ok(assignment.shifts.length * 8 <= input.maxHours);
    for (const i of assignment.shifts) {
      assert.equal(worker.available[i], true);
      coverage[i]++;
      cost += 8 * worker.rate;
    }
    for (const i of assignment.shifts)
      for (const j of assignment.shifts)
        if (i < j) {
          assert.ok(
            workforce.shifts[i].end <= workforce.shifts[j].start ||
              workforce.shifts[j].end <= workforce.shifts[i].start,
          );
        }
  }
  assert.deepEqual(coverage, input.coverage);
  assert.equal(result.objective, cost);
}

function bruteRoster(input) {
  let best = Infinity;
  function visit(i, coverage, cost) {
    if (i === input.workers.length) {
      if (coverage.every((n, j) => n === input.coverage[j])) best = Math.min(best, cost);
      return;
    }
    const worker = input.workers[i];
    // Independent enumeration of the only five non-overlapping shift patterns.
    for (const shifts of [[], [0], [1], [2], [0, 2]]) {
      if (shifts.length * 8 > input.maxHours || shifts.some((s) => !worker.available[s])) continue;
      const next = coverage.map((n, s) => n + Number(shifts.includes(s)));
      if (next.some((n, s) => n > input.coverage[s])) continue;
      visit(i + 1, next, cost + shifts.length * worker.rate * 8);
    }
  }
  visit(0, [0, 0, 0], 0);
  return best;
}

test('workforce roster has exact minimum wages and respects availability, coverage and overlap', () => {
  const input = workforce.seed();
  const original = structuredClone(input);
  const result = workforce.solve(input);
  checkRoster(input, result);
  assert.equal(result.objective, 896);
  assert.deepEqual(input, original);
  for (const maxHours of [8, 16])
    for (const coverage of [
      [1, 1, 1],
      [2, 2, 1],
      [3, 3, 3],
    ]) {
      const scenario = { ...input, maxHours, coverage };
      const expected = bruteRoster(scenario);
      const actual = workforce.solve(scenario);
      assert.equal(actual.feasible, Number.isFinite(expected));
      if (actual.feasible) {
        assert.equal(actual.objective, expected);
        checkRoster(scenario, actual);
      }
    }
});

test('workforce edits change the optimum and unavailable coverage is explicitly infeasible', () => {
  const input = workforce.seed();
  const before = workforce.solve(input);
  input.maxHours = 16;
  const flexible = workforce.solve(input);
  checkRoster(input, flexible);
  assert.ok(flexible.objective < before.objective);
  for (const worker of input.workers) worker.available[2] = false;
  const blocked = workforce.solve(input);
  assert.equal(blocked.feasible, false);
  assert.equal(blocked.objective, null);
  assert.deepEqual(blocked.assignments, []);
  input.coverage[2] = 0;
  checkRoster(input, workforce.solve(input));
  assert.throws(() => workforce.solve({ ...input, coverage: [4, 1, 1] }));
});

function checkProduction(input, result) {
  assert.equal(
    result.schedule.length,
    input.jobs.reduce((sum, job) => sum + job.operations.length, 0),
  );
  let makespan = 0;
  for (const job of input.jobs) {
    let ready = job.release;
    job.operations.forEach((operation, i) => {
      const matches = result.schedule.filter((op) => op.jobId === job.id && op.operation === i);
      assert.equal(matches.length, 1);
      const scheduled = matches[0];
      assert.equal(scheduled.machine, operation.machine);
      assert.equal(scheduled.end - scheduled.start, operation.duration);
      assert.ok(scheduled.start >= ready);
      ready = scheduled.end;
      makespan = Math.max(makespan, ready);
    });
  }
  for (const machine of production.machines) {
    const ops = result.schedule.filter((op) => op.machine === machine.id).sort((a, b) => a.start - b.start);
    for (let i = 1; i < ops.length; i++) assert.ok(ops[i - 1].end <= ops[i].start);
  }
  assert.equal(result.objective, makespan);
  assert.equal(result.feasible, makespan <= input.horizon);
}

test('job shop achieves the machine workload lower bound with valid precedence and releases', () => {
  const input = production.seed();
  const original = structuredClone(input);
  const result = production.solve(input);
  checkProduction(input, result);
  assert.equal(result.objective, 9);
  assert.equal(result.examined, 90);
  assert.equal(result.feasible, true);
  assert.deepEqual(input, original);
  input.jobs[0].release = 4;
  checkProduction(input, production.solve(input));
});

test('job shop reoptimizes duration and machine edits and reports a too-short window', () => {
  const input = production.seed();
  input.jobs[1].operations[1].duration = 8;
  const longer = production.solve(input);
  checkProduction(input, longer);
  assert.equal(longer.objective, 13);
  assert.equal(longer.feasible, false);
  assert.match(longer.reason, /13 hours/);
  input.horizon = 16;
  assert.equal(production.solve(input).feasible, true);
  for (const job of input.jobs) for (const operation of job.operations) operation.machine = 'M1';
  const serial = production.solve(input);
  checkProduction(input, serial);
  assert.equal(
    serial.objective,
    input.jobs.flatMap((job) => job.operations).reduce((sum, op) => sum + op.duration, 0),
  );
  assert.throws(() => production.solve({ ...input, horizon: Infinity }));
});

test('two opposing machine routes run in parallel to a known exact optimum', () => {
  const input = {
    horizon: 6,
    jobs: [
      {
        id: 'A',
        release: 0,
        operations: [
          { machine: 'M1', duration: 3 },
          { machine: 'M2', duration: 3 },
        ],
      },
      {
        id: 'B',
        release: 0,
        operations: [
          { machine: 'M2', duration: 3 },
          { machine: 'M1', duration: 3 },
        ],
      },
    ],
  };
  const result = production.solve(input);
  checkProduction(input, result);
  assert.equal(result.objective, 6);
});

function checkTimetable(input, result) {
  assert.equal(result.feasible, true);
  assert.equal(result.assignments.length, input.courses.length);
  let penalty = 0,
    waste = 0;
  for (const course of input.courses) {
    const entries = result.assignments.filter((a) => a.courseId === course.id);
    assert.equal(entries.length, 1);
    const entry = entries[0],
      room = input.rooms.find((r) => r.id === entry.roomId);
    assert.ok(room && room.open && room.capacity >= course.students);
    assert.ok(entry.slot < input.slotCount);
    assert.equal(course.available[entry.slot], true);
    penalty += Math.abs(entry.slot - course.preferred);
    waste += room.capacity - course.students;
  }
  result.assignments.forEach((a, i) =>
    result.assignments.slice(i + 1).forEach((b) => {
      if (a.slot !== b.slot) return;
      const first = input.courses.find((c) => c.id === a.courseId),
        second = input.courses.find((c) => c.id === b.courseId);
      assert.notEqual(a.roomId, b.roomId);
      assert.notEqual(first.teacher, second.teacher);
      assert.notEqual(first.cohort, second.cohort);
    }),
  );
  assert.equal(result.objective, penalty);
  assert.equal(result.seatWaste, waste);
}

test('timetable satisfies capacity, teacher and cohort rules at the known minimum penalty', () => {
  const input = timetable.seed();
  const original = structuredClone(input);
  const result = timetable.solve(input);
  checkTimetable(input, result);
  assert.equal(result.objective, 1);
  assert.equal(result.seatWaste, 32);
  assert.deepEqual(input, original);
});

test('timetable responds to teacher and room edits with a better feasible solution', () => {
  const input = timetable.seed();
  input.rooms[1].capacity = 32;
  input.courses[2].teacher = 'Prof. Lee';
  const result = timetable.solve(input);
  checkTimetable(input, result);
  assert.equal(result.objective, 0);
});

test('timetable proves infeasible room, period and restricted teacher combinations', () => {
  const closed = timetable.seed();
  closed.rooms[1].open = false;
  assert.equal(timetable.solve(closed).feasible, false);
  const short = timetable.seed();
  short.slotCount = 2;
  assert.equal(timetable.solve(short).feasible, false);
  const small = timetable.seed();
  small.rooms.forEach((room) => (room.capacity = 20));
  assert.equal(timetable.solve(small).feasible, false);
  const clashes = timetable.seed();
  clashes.rooms[1].capacity = 36;
  clashes.courses[0].available = [true, false, false];
  clashes.courses[2].available = [true, false, false];
  const result = timetable.solve(clashes);
  assert.equal(result.feasible, false);
  assert.equal(result.objective, null);
  assert.deepEqual(result.assignments, []);
  assert.throws(() => timetable.solve({ ...clashes, slotCount: 4 }));
});

test('scheduling modules register the parent mount contract', () => {
  for (const id of ['workforce', 'production', 'timetable'])
    assert.equal(typeof globalThis.OptimizationViews[id].mount, 'function');
});
