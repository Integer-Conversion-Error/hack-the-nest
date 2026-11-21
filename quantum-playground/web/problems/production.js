(function (host) {
  'use strict';
  const machines = [
    { id: 'M1', name: 'Cutting' },
    { id: 'M2', name: 'Assembly' },
  ];
  function seed() {
    return {
      horizon: 12,
      jobs: [
        {
          id: 'J1',
          name: 'Frame',
          release: 0,
          operations: [
            { machine: 'M1', duration: 3 },
            { machine: 'M2', duration: 2 },
          ],
        },
        {
          id: 'J2',
          name: 'Panel',
          release: 0,
          operations: [
            { machine: 'M2', duration: 2 },
            { machine: 'M1', duration: 4 },
          ],
        },
        {
          id: 'J3',
          name: 'Bracket',
          release: 0,
          operations: [
            { machine: 'M1', duration: 2 },
            { machine: 'M2', duration: 3 },
          ],
        },
      ],
    };
  }
  function solve(input) {
    if (
      !input ||
      !Number.isInteger(input.horizon) ||
      input.horizon < 1 ||
      input.horizon > 32 ||
      !Array.isArray(input.jobs) ||
      input.jobs.length < 1 ||
      input.jobs.length > 4
    )
      throw new Error('Use 1–4 jobs and a planning window of 1–32 hours.');
    const ids = new Set();
    for (const job of input.jobs) {
      if (
        typeof job.id !== 'string' ||
        ids.has(job.id) ||
        !Number.isInteger(job.release) ||
        job.release < 0 ||
        job.release > 8 ||
        !Array.isArray(job.operations) ||
        job.operations.length < 1 ||
        job.operations.length > 2 ||
        job.operations.some(
          (op) =>
            !op ||
            !machines.some((m) => m.id === op.machine) ||
            !Number.isInteger(op.duration) ||
            op.duration < 1 ||
            op.duration > 8,
        )
      ) {
        throw new Error(
          'Each job needs 1–2 operations of 1–8 hours, valid machines, and a release time from 0–8.',
        );
      }
      ids.add(job.id);
    }
    let best = null,
      examined = 0;
    function search(next, jobReady, machineReady, schedule) {
      if (next.every((n, i) => n === input.jobs[i].operations.length)) {
        examined++;
        const makespan = Math.max(...jobReady);
        if (!best || makespan < best.makespan) best = { makespan, schedule, completions: [...jobReady] };
        return;
      }
      // Enumerating all job-precedence-preserving dispatch orders covers every
      // machine ordering. Earliest starts are optimal for a fixed ordering.
      for (let i = 0; i < input.jobs.length; i++) {
        const job = input.jobs[i],
          index = next[i];
        if (index >= job.operations.length) continue;
        const operation = job.operations[index];
        const start = Math.max(jobReady[i], machineReady[operation.machine]);
        const end = start + operation.duration;
        const remaining = [...next];
        remaining[i]++;
        const ready = [...jobReady];
        ready[i] = end;
        search(remaining, ready, { ...machineReady, [operation.machine]: end }, [
          ...schedule,
          {
            jobId: job.id,
            operation: index,
            machine: operation.machine,
            start,
            end,
            duration: operation.duration,
          },
        ]);
      }
    }
    search(
      input.jobs.map(() => 0),
      input.jobs.map((job) => job.release),
      { M1: 0, M2: 0 },
      [],
    );
    const feasible = best.makespan <= input.horizon;
    return {
      feasible,
      objective: best.makespan,
      schedule: best.schedule,
      completions: best.completions,
      examined,
      exact: true,
      reason: feasible
        ? ''
        : 'The shortest possible schedule needs ' +
          best.makespan +
          ' hours, exceeding the ' +
          input.horizon +
          '-hour planning window.',
    };
  }
  function el(tag, cls, text) {
    const node = document.createElement(tag);
    if (cls) node.className = cls;
    if (text != null) node.textContent = text;
    return node;
  }
  function numeric(value, min, max, label) {
    const node = el('input');
    Object.assign(node, { type: 'number', value, min, max, step: 1 });
    node.setAttribute('aria-label', label);
    return node;
  }
  function metric(label, value) {
    const node = el('div', 'schedule-metric');
    node.append(el('span', '', label), el('strong', '', value));
    return node;
  }
  function mount(root) {
    const state = seed();
    const frame = el('div', 'scheduling-workbench');
    const controls = el('section', 'problem-card schedule-inputs');
    controls.append(el('h2', '', 'Set the production plan'));
    controls.append(
      el(
        'p',
        'schedule-note',
        'Each job follows operation 1, then operation 2. A machine can process one operation at a time; operations run without interruption.',
      ),
    );
    const fields = el('div', 'problem-controls schedule-fields');
    const horizonLabel = el('label', 'problem-field', 'Planning window, hours');
    const horizon = numeric(state.horizon, 1, 32, 'Production planning window in hours');
    horizonLabel.append(horizon);
    fields.append(horizonLabel);
    controls.append(fields);
    const scroll = el('div', 'schedule-table-scroll');
    const table = el('table', 'problem-table schedule-input-table');
    table.append(el('caption', 'sr-only', 'Job release times and ordered machine operations'));
    const head = el('tr');
    for (const title of ['Job', 'Release, h', 'Operation 1 machine', 'Hours', 'Operation 2 machine', 'Hours'])
      head.append(el('th', '', title));
    const thead = el('thead');
    thead.append(head);
    table.append(thead);
    const tbody = el('tbody');
    const editors = state.jobs.map((job) => {
      const row = el('tr');
      const name = el('th', '', job.id + ' · ' + job.name);
      name.scope = 'row';
      row.append(name);
      const release = numeric(job.release, 0, 8, job.name + ' release hour');
      const releaseCell = el('td');
      releaseCell.append(release);
      row.append(releaseCell);
      const operations = job.operations.map((operation, i) => {
        const machine = el('select');
        machine.setAttribute('aria-label', job.name + ' operation ' + (i + 1) + ' machine');
        for (const item of machines) {
          const option = el('option', '', item.name);
          option.value = item.id;
          machine.append(option);
        }
        machine.value = operation.machine;
        const duration = numeric(operation.duration, 1, 8, job.name + ' operation ' + (i + 1) + ' hours');
        const machineCell = el('td');
        machineCell.append(machine);
        const durationCell = el('td');
        durationCell.append(duration);
        row.append(machineCell, durationCell);
        return { machine, duration };
      });
      tbody.append(row);
      return { release, operations };
    });
    table.append(tbody);
    scroll.append(table);
    controls.append(scroll);
    const output = el('section', 'problem-card schedule-output');
    output.setAttribute('aria-label', 'Optimized production schedule');
    const status = el('p', 'schedule-status');
    status.setAttribute('role', 'status');
    status.setAttribute('aria-live', 'polite');
    const metrics = el('div', 'problem-metrics schedule-metrics');
    const figure = el('div', 'problem-figure schedule-table-scroll');
    const details = el('div', 'schedule-table-scroll');
    output.append(el('h2', '', 'Shortest production schedule'), status, metrics, figure, details);
    frame.append(output, controls);
    root.replaceChildren(frame);
    function read(node) {
      return node.value === '' ? NaN : Number(node.value);
    }
    function update() {
      figure.replaceChildren();
      metrics.replaceChildren();
      details.replaceChildren();
      try {
        const input = {
          horizon: read(horizon),
          jobs: state.jobs.map((job, i) => ({
            ...job,
            release: read(editors[i].release),
            operations: editors[i].operations.map((op) => ({
              machine: op.machine.value,
              duration: read(op.duration),
            })),
          })),
        };
        const result = solve(input);
        status.className = 'schedule-status' + (result.feasible ? '' : ' schedule-error');
        status.textContent = result.feasible
          ? 'Every job fits. Exact minimum makespan across all ' + result.examined + ' dispatch orders.'
          : result.reason + ' The Gantt chart shows that shortest schedule for comparison.';
        const busy = result.schedule.reduce((sum, op) => sum + op.duration, 0);
        metrics.append(
          metric('Minimum makespan', result.objective + ' h'),
          metric('Machine utilization', Math.round((busy / (2 * result.objective)) * 100) + '%'),
          metric('Window margin', input.horizon - result.objective + ' h'),
        );
        const gantt = el('div', 'schedule-gantt');
        gantt.setAttribute('role', 'img');
        gantt.setAttribute(
          'aria-label',
          'Gantt chart of ' +
            machines
              .map(
                (machine) =>
                  machine.name +
                  ': ' +
                  result.schedule
                    .filter((op) => op.machine === machine.id)
                    .map(
                      (op) =>
                        op.jobId + ' operation ' + (op.operation + 1) + ' from hour ' + op.start + ' to ' + op.end,
                    )
                    .join(', '),
              )
              .join('; '),
        );
        const axisMax = Math.max(input.horizon, result.objective);
        const axis = el('div', 'schedule-axis');
        axis.append(el('span', '', 'Hour'));
        const ticks = el('div', 'schedule-axis-ticks');
        const increment = axisMax > 20 ? 4 : axisMax > 12 ? 2 : 1;
        for (let t = 0; t <= axisMax; t += increment) {
          const tick = el('span', '', t);
          tick.style.left = (t / axisMax) * 100 + '%';
          ticks.append(tick);
        }
        axis.append(ticks);
        gantt.append(axis);
        for (const machine of machines) {
          const row = el('div', 'schedule-machine');
          row.append(el('span', 'schedule-machine-label', machine.name));
          const track = el('div', 'schedule-machine-track');
          result.schedule
            .filter((op) => op.machine === machine.id)
            .forEach((op) => {
              const index = input.jobs.findIndex((job) => job.id === op.jobId);
              const block = el('div', 'schedule-operation schedule-job-' + index);
              block.style.left = (op.start / axisMax) * 100 + '%';
              block.style.width = (op.duration / axisMax) * 100 + '%';
              block.append(
                el('strong', 'schedule-op-full', op.jobId + ' · Op ' + (op.operation + 1)),
                el('strong', 'schedule-op-short', op.jobId),
                el('small', '', op.start + '–' + op.end + ' h'),
              );
              block.title =
                input.jobs[index].name +
                ', operation ' +
                (op.operation + 1) +
                ': ' +
                op.start +
                '–' +
                op.end +
                ' h';
              track.append(block);
            });
          if (input.horizon < axisMax) {
            const boundary = el('span', 'schedule-deadline');
            boundary.style.left = (input.horizon / axisMax) * 100 + '%';
            track.append(boundary);
          }
          row.append(track);
          gantt.append(row);
        }
        figure.append(gantt);
        const legend = el('div', 'schedule-legend');
        state.jobs.forEach((job, i) => {
          const item = el('span', 'schedule-job-' + i);
          const swatch = el('i', 'schedule-swatch');
          swatch.style.background = 'var(--job-color)';
          item.append(swatch, document.createTextNode(job.id + ' · ' + job.name));
          legend.append(item);
        });
        figure.append(legend);
        figure.append(
          el('p', 'schedule-chart-note', 'Each bar is one operation. Full operation times are listed below.'),
        );
        const ledger = el('table', 'problem-table schedule-operation-ledger');
        ledger.append(el('caption', '', 'Operation times · hours from shift start'));
        const ledgerHead = el('tr');
        for (const title of ['Job / op', 'Machine', 'Start', 'End']) ledgerHead.append(el('th', '', title));
        const ledgerThead = el('thead');
        ledgerThead.append(ledgerHead);
        ledger.append(ledgerThead);
        const ledgerBody = el('tbody');
        [...result.schedule]
          .sort((a, b) => a.jobId.localeCompare(b.jobId) || a.operation - b.operation)
          .forEach((op) => {
            const row = el('tr');
            row.append(
              el('th', '', op.jobId + ' / ' + (op.operation + 1)),
              el('td', '', machines.find((m) => m.id === op.machine).name),
              el('td', '', op.start),
              el('td', '', op.end),
            );
            ledgerBody.append(row);
          });
        ledger.append(ledgerBody);
        details.append(ledger);
        const summary = el('table', 'problem-table');
        summary.append(el('caption', 'sr-only', 'Job completion times'));
        const header = el('tr');
        for (const label of ['Job', 'Released', 'Complete', 'Flow time']) header.append(el('th', '', label));
        const h = el('thead');
        h.append(header);
        summary.append(h);
        const body = el('tbody');
        input.jobs.forEach((job, i) => {
          const row = el('tr');
          const name = el('th', '', job.id + ' · ' + job.name);
          name.scope = 'row';
          row.append(
            name,
            el('td', '', job.release + ' h'),
            el('td', '', result.completions[i] + ' h'),
            el('td', '', result.completions[i] - job.release + ' h'),
          );
          body.append(row);
        });
        summary.append(body);
        details.append(summary);
      } catch (error) {
        status.className = 'schedule-status schedule-error';
        status.textContent = error.message;
      }
    }
    frame.addEventListener('input', update);
    frame.addEventListener('change', update);
    update();
    return () => {
      frame.removeEventListener('input', update);
      frame.removeEventListener('change', update);
    };
  }
  host.OptimizationViews = host.OptimizationViews || {};
  host.OptimizationViews.production = { mount };
  if (typeof module !== 'undefined' && module.exports) module.exports = { solve, seed, machines };
})(typeof window !== 'undefined' ? window : globalThis);
