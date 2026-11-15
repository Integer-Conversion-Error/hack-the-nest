(() => {
  const path = '/api/pipeline-runs';
  const stages = [
    { id: 'init', label: 'Parse prompt', activity: 'Reading your problem…' },
    { id: 'plan', label: 'Plan solution', activity: 'Planning a solution…' },
    { id: 'build', label: 'Build solver', activity: 'Building the solver…' },
    { id: 'test', label: 'Run solver', activity: 'Running and testing the solver…' },
    { id: 'verify', label: 'Verify result', activity: 'Verifying the result…' },
  ];

  function validate(snapshot, expectedId) {
    if (typeof snapshot?.id !== 'string' || !snapshot.id || (expectedId && snapshot.id !== expectedId) ||
        !['queued', 'running', 'completed', 'failed'].includes(snapshot.status) ||
        (snapshot.stage !== null && !stages.some(stage => stage.id === snapshot.stage)) ||
        !Number.isInteger(snapshot.attempt) || snapshot.attempt < 0) {
      throw Error('The backend returned invalid pipeline progress.');
    }
    return snapshot;
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

  globalThis.PipelineProgress = { follow, stages };
})();
