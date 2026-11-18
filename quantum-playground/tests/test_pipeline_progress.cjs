const assert = require('node:assert/strict');
require('../web/pipeline-progress.js');
const { describe, createHistoryView, follow } = globalThis.PipelineProgress;

const snapshot = (history = [], extra = {}) => ({
  id: 'progress-test', status: 'running', stage: history.at(-1)?.stage ?? null,
  attempt: history.at(-1)?.attempt ?? 0, history, ...extra,
});
const visit = (id, stage, status, attempt = 1, steps = []) => ({ id, stage, status, attempt, steps });
const step = (id, label, status, detail) => ({ id, label, status, ...(detail === undefined ? {} : { detail }) });

function testQueuedProgress() {
  const result = describe(null);
  assert.equal(result.total, 5);
  assert.equal(result.completed, 0);
  assert.equal(result.revisionCount, 0);
  assert.ok(result.stages.every(stage => stage.state === 'pending' && stage.status === 'Pending'));
  assert.equal(result.current.label, 'Waiting to start');
  assert.equal(result.repair, null);
}

function testRepairProgressUsesLatestStageVisits() {
  const reason = 'Van 4 missed its delivery deadline.\nRecheck the service time.';
  const history = [
    visit(1, 'init', 'completed'), visit(2, 'plan', 'completed'), visit(3, 'build', 'completed'),
    visit(4, 'test', 'revising', 1, [step(4, 'Check schedule constraints', 'failed', reason)]),
    visit(5, 'build', 'running', 2, [step(5, 'Update service times', 'running', 'Apply the timing correction.')]),
  ];
  const result = describe(snapshot(history));
  assert.deepEqual(result.stages.map(stage => stage.state), ['completed', 'completed', 'running', 'revising', 'pending']);
  assert.equal(result.stages[3].status, 'Revision needed');
  assert.equal(result.completed, 2, 'revisiting Build removes its old completed state');
  assert.equal(result.revisionCount, 1);
  assert.equal(result.current.label, 'Update service times');
  assert.equal(result.current.detail, 'Apply the timing correction.');
  assert.equal(result.repair.label, 'Returned to Build solver');
  assert.ok(result.repair.detail.includes(reason));
  assert.ok(result.repair.detail.includes('Attempt 2'));

  history[4] = { ...history[4], status: 'completed', steps: [step(5, 'Update service times', 'completed')] };
  history.push(visit(6, 'test', 'running', 2));
  const retest = describe(snapshot(history));
  assert.equal(retest.revisionCount, 1, 're-testing after a repair does not count as another repair loop');
  assert.equal(retest.completed, 3);
  assert.equal(retest.repair.label, 'Returned to Build solver');
  assert.equal(retest.current.detail, 'Running and testing the solver…');
}

function testHistoryNeverInventsEarlierCompletions() {
  const result = describe(snapshot([visit(1, 'build', 'running')]));
  assert.deepEqual(result.stages.map(stage => stage.state), ['pending', 'pending', 'running', 'pending', 'pending']);
  assert.equal(result.completed, 0);
}

function testReinterpretationInvalidatesDownstreamApprovals() {
  const reason = 'The objective must minimize missed deliveries before driving cost.';
  const history = [
    visit(1, 'init', 'completed'), visit(2, 'plan', 'completed'), visit(3, 'build', 'completed'),
    visit(4, 'test', 'completed'),
    visit(5, 'verify', 'revising', 1, [step(5, 'Compare the problem with the original prompt', 'failed', reason)]),
    visit(6, 'init', 'running', 2, [step(6, 'Correct the objective', 'running')]),
  ];
  const result = describe(snapshot(history));
  assert.deepEqual(result.stages.map(stage => stage.state), ['running', 'pending', 'pending', 'pending', 'revising']);
  assert.equal(result.completed, 0, 'approvals after the returned stage must be earned again');
  assert.equal(result.revisionCount, 1);
  assert.equal(result.repair.label, 'Returned to Parse prompt');
  assert.ok(result.repair.detail.includes(reason));
  assert.ok(result.repair.detail.includes('Attempt 2'));
  assert.equal(history.length, 6, 'the complete visit history remains available');
}

function testLegacySnapshotsInferPreviousStages() {
  const result = describe({ id: 'legacy', status: 'running', stage: 'test', attempt: 2 });
  assert.deepEqual(result.stages.map(stage => stage.state), ['completed', 'completed', 'completed', 'running', 'pending']);
  assert.equal(result.completed, 3);
  assert.equal(result.stages[3].status, 'In progress · Attempt 2');
  assert.equal(result.current.label, 'Run solver');
  assert.equal(result.repair, null);
}

function testCurrentStepAndFailedProgress() {
  const history = [visit(1, 'plan', 'failed', 1, [
    step(1, 'Research solver', 'completed'),
    step(2, 'Review plan', 'failed', 'The qubit estimate exceeds the limit.'),
  ])];
  const result = describe(snapshot(history, { status: 'failed', error: 'A smaller problem is required.' }));
  assert.equal(result.current.label, 'Review plan');
  assert.equal(result.current.detail, 'A smaller problem is required.');
  assert.equal(result.stages[1].state, 'failed');
  assert.equal(result.stages[1].status, 'Failed');
  assert.equal(result.completed, 0);

  const running = describe(snapshot([visit(1, 'build', 'running', 1, [
    step(1, 'Write solver', 'running', 'Writing main.py'), step(2, 'Worker limit is 120 seconds', 'info'),
  ])]));
  assert.equal(running.current.label, 'Write solver', 'the running step takes precedence over later informational steps');
  assert.equal(running.current.detail, 'Writing main.py');

  const failed = describe(snapshot([visit(1, 'build', 'failed', 1, [
    step(1, 'Write solver', 'running'), step(2, 'Worker could not start', 'failed'),
  ])], { status: 'failed' }));
  assert.equal(failed.current.label, 'Worker could not start', 'a failed snapshot reports its last step');
}

function testCompletedSnapshotFinishesEveryStage() {
  const history = [visit(1, 'test', 'revising'), visit(2, 'build', 'completed', 2), visit(3, 'verify', 'completed')];
  const result = describe(snapshot(history, { status: 'completed' }));
  assert.ok(result.stages.every(stage => stage.state === 'completed' && stage.status === 'Complete'));
  assert.equal(result.completed, 5);
  assert.equal(result.revisionCount, 1);
  assert.equal(result.current.label, 'Pipeline complete');
  assert.equal(result.repair, null);
}

function testMalformedProgressIsRejected() {
  for (const value of [
    {}, snapshot([], { status: 'unknown' }), snapshot([], { stage: 'unknown' }),
    snapshot([], { attempt: -1 }), snapshot([], { history: {} }),
    snapshot([visit(1, 'plan', 'running'), visit(1, 'build', 'running')]),
    snapshot([visit(Number.MAX_SAFE_INTEGER + 1, 'plan', 'running')]),
    snapshot([visit(1, 'plan', 'unknown')]),
    snapshot([visit(1, 'plan', 'running', 0)]),
    snapshot([visit(1, 'plan', 'running', 1, [step(1, '', 'running')])]),
    snapshot([visit(1, 'plan', 'running', 1, [step(1, 'Plan', 'unknown')])]),
    snapshot([visit(1, 'plan', 'running', 1, [step(1, 'Plan', 'running', {})])]),
  ]) {
    assert.throws(() => describe(value), /invalid pipeline progress/);
  }
}

class Node {
  constructor(tag, text = '', className = '') {
    this.tagName = tag;
    this.textContent = text;
    this.className = className;
    this.children = [];
    this.attributes = {};
    this.open = false;
  }
  append(...children) { this.children.push(...children); }
  replaceChildren(...children) { this.children = children; }
  setAttribute(name, value) { this.attributes[name] = value; }
}

function historyView() {
  const list = new Node('ol');
  const toggle = new Node('button');
  const view = createHistoryView(list, toggle,
    (tag, text, className) => new Node(tag, text, className),
    (node, text) => { node.textContent = text; });
  return { list, toggle, ...view };
}

function testFocusedDisclosureDefaultsAndManualChoices() {
  const ui = historyView();
  const history = [visit(1, 'init', 'completed'), visit(2, 'build', 'running')];
  ui.render(history);
  const completed = ui.list.children[0].children[0];
  const active = ui.list.children[1].children[0];
  assert.equal(completed.open, false, 'completed attempts start collapsed');
  assert.equal(active.open, true, 'the active attempt starts open');
  active.open = false;
  active.ontoggle();
  completed.open = true;
  completed.ontoggle();
  ui.render([history[0], { ...history[1], status: 'revising' }]);
  assert.equal(ui.list.children[0].children[0], completed, 'polling keeps disclosure nodes');
  assert.equal(completed.open, true, 'a manually inspected completed attempt stays open');
  assert.equal(active.open, false, 'a manually closed attempt stays closed during revision');

  const fresh = historyView();
  fresh.render([visit(1, 'build', 'running')]);
  const original = fresh.list.children[0].children[0];
  fresh.render([visit(1, 'build', 'completed')]);
  assert.equal(original.open, false, 'unmodified details collapse automatically on completion');
}

function testShowHideAllAndTextOnlyDetails() {
  const ui = historyView();
  const history = [visit(1, 'plan', 'completed'), visit(2, 'test', 'revising', 1, [
    step(1, '<img src=x onerror=alert(1)>', 'failed', '<script>alert(1)</script>'),
  ])];
  ui.render(history);
  assert.equal(ui.list.children[1].children[0].open, true, 'revision findings start open');
  ui.toggle.onclick();
  history.push(visit(3, 'build', 'running', 2));
  ui.render(history);
  assert.ok(ui.list.children.every(row => !row.children[0].open), 'new attempts inherit hide-all preference');
  assert.equal(ui.toggle.textContent, 'Show all steps');
  ui.toggle.onclick();
  assert.ok(ui.list.children.every(row => row.children[0].open));
  const findings = ui.list.children[1].children[0].children[1].children[0];
  assert.equal(findings.children[0].textContent, '<img src=x onerror=alert(1)>');
  assert.equal(findings.children[2].textContent, '<script>alert(1)</script>');
  ui.reset();
  assert.equal(ui.list.children.length, 0);
  assert.equal(ui.list.hidden, true);
}

async function testPollingValidatesRunIdentity() {
  await assert.rejects(follow({
    id: 'run/with spaces', prompt: 'prompt',
    request: async path => {
      assert.equal(path, '/api/pipeline-runs/run%2Fwith%20spaces');
      return snapshot([], { id: 'different-run', status: 'completed' });
    },
    onSnapshot() { throw Error('A mismatched run must not reach rendering.'); },
    onDisconnect() { throw Error('A malformed response is not a temporary disconnect.'); },
  }), /invalid pipeline progress/);
}

(async () => {
  for (const test of [testQueuedProgress, testRepairProgressUsesLatestStageVisits, testHistoryNeverInventsEarlierCompletions,
    testReinterpretationInvalidatesDownstreamApprovals,
    testLegacySnapshotsInferPreviousStages, testCurrentStepAndFailedProgress, testCompletedSnapshotFinishesEveryStage,
    testMalformedProgressIsRejected, testFocusedDisclosureDefaultsAndManualChoices, testShowHideAllAndTextOnlyDetails]) test();
  await testPollingValidatesRunIdentity();
  console.log('PASS: pipeline stage overview, repair loops, current steps, validation, and focused history disclosures');
})().catch(error => { console.error(error); process.exitCode = 1; });
