(() => {
  const path = '/api/pipeline-runs';
  const stages = [
    { id: 'init', label: 'Parse prompt', activity: 'Reading your problem…' },
    { id: 'plan', label: 'Plan solution', activity: 'Planning a solution…' },
    { id: 'build', label: 'Build solver', activity: 'Building the solver…' },
    { id: 'test', label: 'Run solver', activity: 'Running and testing the solver…' },
    { id: 'verify', label: 'Verify result', activity: 'Verifying the result…' },
  ];
  const visitStates = {
    running: 'In progress', completed: 'Complete', revising: 'Revision needed', failed: 'Failed',
  };
  const stepStates = { running: 'In progress', completed: 'Done', failed: 'Failed', info: '' };

  function validEntries(entries, validateEntry) {
    return Array.isArray(entries) && entries.every((entry, index) =>
      Number.isSafeInteger(entry?.id) && entry.id > (entries[index - 1]?.id ?? 0) && validateEntry(entry));
  }

  function validHistory(history) {
    return validEntries(history, visit =>
      stages.some(stage => stage.id === visit.stage) &&
      Number.isSafeInteger(visit.attempt) && visit.attempt > 0 &&
      Object.hasOwn(visitStates, visit.status) &&
      validEntries(visit.steps, step => typeof step.label === 'string' && step.label.trim() &&
        Object.hasOwn(stepStates, step.status) && (step.detail === undefined || typeof step.detail === 'string')));
  }

  function validate(snapshot, expectedId) {
    if (typeof snapshot?.id !== 'string' || !snapshot.id || (expectedId && snapshot.id !== expectedId) ||
        !['queued', 'running', 'completed', 'failed'].includes(snapshot.status) ||
        (snapshot.stage !== null && !stages.some(stage => stage.id === snapshot.stage)) ||
        !Number.isInteger(snapshot.attempt) || snapshot.attempt < 0 ||
        (snapshot.history !== undefined && !validHistory(snapshot.history))) {
      throw Error('The backend returned invalid pipeline progress.');
    }
    return snapshot;
  }

  function describe(snapshot) {
    if (snapshot == null) {
      snapshot = { id: 'pending', status: 'queued', stage: null, attempt: 0 };
    }
    validate(snapshot);
    const history = snapshot.history ?? [];
    const latest = new Map();
    for (const [index, visit] of history.entries()) {
      const targetIndex = stages.findIndex(stage => stage.id === visit.stage);
      const source = history[index - 1];
      if (source && targetIndex < stages.findIndex(stage => stage.id === source.stage)) {
        // Returning to an earlier stage invalidates its downstream approvals.
        for (const stage of stages.slice(targetIndex + 1)) latest.delete(stage.id);
        if (source.status === 'revising' || source.status === 'failed') latest.set(source.stage, source);
      }
      latest.set(visit.stage, visit);
    }
    const activeIndex = stages.findIndex(stage => stage.id === snapshot.stage);
    const stageDescriptions = stages.map((stage, index) => {
      let state = latest.get(stage.id)?.status ?? 'pending';
      if (snapshot.status === 'completed') state = 'completed';
      else if (!history.length && activeIndex >= 0) {
        state = index < activeIndex ? 'completed' : index === activeIndex
          ? snapshot.status === 'failed' ? 'failed' : 'running' : 'pending';
      }
      const attempt = !history.length && state === 'running' && snapshot.attempt > 1
        ? ' · Attempt ' + snapshot.attempt : '';
      return { id: stage.id, label: stage.label, state, status: (visitStates[state] ?? 'Pending') + attempt };
    });

    const visit = (snapshot.status === 'running'
      ? history.findLast(item => item.status === 'running') : undefined) ?? history.at(-1);
    const stage = stages.find(item => item.id === (visit?.stage ?? snapshot.stage));
    const step = (snapshot.status === 'running'
      ? visit?.steps.findLast(item => item.status === 'running') : undefined) ?? visit?.steps.at(-1);
    let current = {
      label: step?.label || stage?.label || 'Waiting to start',
      detail: step?.detail || stage?.activity || 'Waiting for the pipeline to start…',
    };
    if (snapshot.status === 'completed') {
      current = { label: 'Pipeline complete', detail: 'Your verified result is ready.' };
    } else if (snapshot.status === 'failed') {
      current.detail = typeof snapshot.error === 'string' && snapshot.error
        ? snapshot.error : step?.detail || 'The pipeline could not produce a verified result.';
    }

    let repair = null;
    if (snapshot.status !== 'completed') {
      for (let index = history.length - 1; index > 0; index--) {
        const source = history[index - 1];
        const target = history[index];
        const sourceIndex = stages.findIndex(item => item.id === source.stage);
        const targetIndex = stages.findIndex(item => item.id === target.stage);
        if (targetIndex >= sourceIndex) continue;
        const finding = source.steps.findLast(item => item.status === 'failed')
          ?? source.steps.findLast(item => item.detail) ?? source.steps.at(-1);
        const reason = finding?.detail || finding?.label || 'The previous check requested a revision.';
        repair = {
          label: 'Returned to ' + stages[targetIndex].label,
          detail: stages[sourceIndex].label + ': ' + reason + ' · Attempt ' + target.attempt,
        };
        break;
      }
    }

    return {
      stages: stageDescriptions,
      completed: stageDescriptions.filter(item => item.state === 'completed').length,
      total: stages.length,
      // A backward transition starts one repair loop; its following re-test is part of that loop.
      revisionCount: history.filter((item, index) => index > 0 &&
        stages.findIndex(stage => stage.id === item.stage) <
        stages.findIndex(stage => stage.id === history[index - 1].stage)).length,
      current, repair,
    };
  }

  function createHistoryView(list, toggle, node, writeText) {
    const visits = new Map();
    // null follows stage status; explicit show/hide-all choices apply to new visits too.
    let expandAll = null;

    function updateToggle() {
      const anyOpen = [...visits.values()].some(({ disclosure }) => disclosure.open);
      writeText(toggle, anyOpen ? 'Hide all steps' : 'Show all steps');
    }

    function setOpen(item, open) {
      item.expectedOpen = open;
      if (item.disclosure.open !== open) item.disclosure.open = open;
    }

    toggle.onclick = () => {
      expandAll = ![...visits.values()].some(({ disclosure }) => disclosure.open);
      for (const item of visits.values()) {
        item.manualOpen = expandAll;
        setOpen(item, expandAll);
      }
      updateToggle();
    };

    function reset() {
      expandAll = null;
      visits.clear();
      list.replaceChildren();
      render([]);
    }

    function addVisit(visit) {
      const row = node('li', undefined, 'pipeline-visit');
      const disclosure = node('details');
      const summary = node('summary');
      const title = node('span', undefined, 'pipeline-visit-title');
      title.append(node('strong', stages.find(stage => stage.id === visit.stage).label),
        node('span', 'Attempt ' + visit.attempt, 'pipeline-attempt'));
      const state = node('span', '', 'stage-state');
      const steps = node('ol', undefined, 'pipeline-steps');
      steps.setAttribute('role', 'list');
      summary.append(title, state);
      disclosure.append(summary, steps);
      row.append(disclosure);
      list.append(row);
      const item = { disclosure, summary, state, steps, stepRows: new Map(),
        expectedOpen: disclosure.open, manualOpen: expandAll };
      disclosure.ontoggle = () => {
        if (disclosure.open !== item.expectedOpen) {
          item.manualOpen = disclosure.open;
          item.expectedOpen = disclosure.open;
        }
        updateToggle();
      };
      visits.set(visit.id, item);
      return item;
    }

    function render(history) {
      for (const visit of history) {
        const item = visits.get(visit.id) ?? addVisit(visit);
        // A user may toggle immediately before the next poll, before ontoggle fires.
        if (item.disclosure.open !== item.expectedOpen) item.manualOpen = item.disclosure.open;
        setOpen(item, item.manualOpen ?? visit.status !== 'completed');
        item.summary.setAttribute('data-state', visit.status);
        item.summary.setAttribute('aria-current', visit.status === 'running' ? 'step' : 'false');
        writeText(item.state, visitStates[visit.status]);
        for (const step of visit.steps) {
          let parts = item.stepRows.get(step.id);
          if (!parts) {
            const row = node('li', undefined, 'pipeline-step');
            parts = { row, label: node('span', '', 'pipeline-step-label'),
              state: node('span', '', 'stage-state'), detail: node('p', '', 'pipeline-step-detail') };
            row.append(parts.label, parts.state, parts.detail);
            item.steps.append(row);
            item.stepRows.set(step.id, parts);
          }
          parts.row.setAttribute('data-state', step.status);
          writeText(parts.label, step.label);
          writeText(parts.state, stepStates[step.status]);
          writeText(parts.detail, step.detail || '');
          parts.detail.hidden = !step.detail;
        }
      }
      list.hidden = !history.length;
      toggle.hidden = !history.some(visit => visit.steps.length);
      updateToggle();
    }

    return { render, reset };
  }

  async function follow({ request, prompt, scenario, id, onSnapshot, onDisconnect }) {
    let snapshot;
    if (!id) {
      snapshot = validate(await request(path, scenario ? { scenario } : { prompt }));
      id = snapshot.id;
      onSnapshot(snapshot);
    }

    let delay = snapshot ? 1200 : 0;
    while (!snapshot || snapshot.status === 'queued' || snapshot.status === 'running') {
      if (delay) await new Promise(resolve => setTimeout(resolve, delay));
      let response;
      try {
        response = await request(path + '/' + encodeURIComponent(id));
      } catch (error) {
        if (error.status >= 400 && error.status < 500 && ![408, 429].includes(error.status)) throw error;
        onDisconnect();
        delay = Math.min(10000, Math.max(2400, delay * 2));
        continue;
      }
      snapshot = validate(response, id);
      onSnapshot(snapshot);
      delay = 1200;
    }
    return snapshot;
  }

  globalThis.PipelineProgress = { createHistoryView, describe, follow, stages };
})();
