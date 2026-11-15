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

  function createHistoryView(list, toggle, node, writeText) {
    const visits = new Map();
    let expandAll = true;

    function updateToggle() {
      const anyOpen = [...visits.values()].some(({ disclosure }) => disclosure.open);
      if (!anyOpen && visits.size) expandAll = false;
      writeText(toggle, anyOpen || expandAll ? 'Hide all steps' : 'Show all steps');
    }

    toggle.onclick = () => {
      expandAll = ![...visits.values()].some(({ disclosure }) => disclosure.open);
      for (const { disclosure } of visits.values()) disclosure.open = expandAll;
      updateToggle();
    };

    function reset() {
      expandAll = true;
      visits.clear();
      list.replaceChildren();
      render([]);
    }

    function addVisit(visit) {
      const row = node('li', undefined, 'pipeline-visit');
      const disclosure = node('details');
      disclosure.open = expandAll;
      disclosure.ontoggle = updateToggle;
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
      const item = { disclosure, summary, state, steps, stepRows: new Map() };
      visits.set(visit.id, item);
      return item;
    }

    function render(history) {
      for (const visit of history) {
        const item = visits.get(visit.id) ?? addVisit(visit);
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

  async function follow({ request, prompt, id, onSnapshot, onDisconnect }) {
    let snapshot;
    if (!id) {
      snapshot = validate(await request(path, { prompt }));
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

  globalThis.PipelineProgress = { createHistoryView, follow, stages };
})();
