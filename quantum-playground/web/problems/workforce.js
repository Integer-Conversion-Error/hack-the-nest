(function (host) {
  'use strict';
  const shifts = [
    { id: 'early', label: 'Early', time: '06:00–14:00', start: 6, end: 14 },
    { id: 'day', label: 'Day', time: '10:00–18:00', start: 10, end: 18 },
    { id: 'late', label: 'Late', time: '14:00–22:00', start: 14, end: 22 },
  ];
  const mounted = new WeakMap();
  function seed() {
    return {
      coverage: [2, 2, 1],
      maxHours: 8,
      workers: [
        { id: 'W1', name: 'Amara', rate: 19, available: [true, true, false] },
        { id: 'W2', name: 'Ben', rate: 21, available: [true, false, true] },
        { id: 'W3', name: 'Chloe', rate: 22, available: [false, true, true] },
        { id: 'W4', name: 'Diego', rate: 24, available: [true, true, true] },
        { id: 'W5', name: 'Eli', rate: 26, available: [true, true, true] },
        { id: 'W6', name: 'Farah', rate: 30, available: [false, true, true] },
      ],
    };
  }
  function solve(input) {
    if (
      !input ||
      !Array.isArray(input.workers) ||
      input.workers.length < 1 ||
      input.workers.length > 8 ||
      !Array.isArray(input.coverage) ||
      input.coverage.length !== 3 ||
      !input.coverage.every((n) => Number.isInteger(n) && n >= 0 && n <= 3) ||
      ![8, 16].includes(input.maxHours)
    )
      throw new Error('Use 0–3 people per shift and an 8 or 16 hour daily limit.');
    const ids = new Set();
    for (const worker of input.workers) {
      if (
        typeof worker.id !== 'string' ||
        ids.has(worker.id) ||
        !Number.isFinite(worker.rate) ||
        worker.rate < 1 ||
        worker.rate > 100 ||
        !Array.isArray(worker.available) ||
        worker.available.length !== 3 ||
        !worker.available.every((v) => typeof v === 'boolean')
      ) {
        throw new Error('Each employee needs a unique ID, availability and an hourly wage from $1 to $100.');
      }
      ids.add(worker.id);
    }
    // Dynamic programming keeps the cheapest roster for each exact coverage
    // vector. Every feasible daily pattern is considered for every employee.
    let states = new Map([['0,0,0', { coverage: [0, 0, 0], cost: 0, assignments: [] }]]);
    let examined = 0;
    for (const worker of input.workers) {
      const patterns = [];
      for (let mask = 0; mask < 8; mask++) {
        const selected = shifts.map((s, i) => i).filter((i) => mask & (1 << i));
        if (selected.length * 8 > input.maxHours || selected.some((i) => !worker.available[i])) continue;
        if (
          selected.some((i, a) =>
            selected.slice(a + 1).some((j) => shifts[i].start < shifts[j].end && shifts[j].start < shifts[i].end),
          )
        )
          continue;
        patterns.push(selected);
      }
      const next = new Map();
      for (const state of states.values())
        for (const selected of patterns) {
          examined++;
          const coverage = state.coverage.map((n, i) => n + Number(selected.includes(i)));
          if (coverage.some((n, i) => n > input.coverage[i])) continue;
          const cost = state.cost + selected.length * 8 * worker.rate;
          const key = coverage.join(',');
          if (!next.has(key) || cost < next.get(key).cost)
            next.set(key, {
              coverage,
              cost,
              assignments: [...state.assignments, { workerId: worker.id, shifts: selected }],
            });
        }
      states = next;
    }
    const best = states.get(input.coverage.join(','));
    return best
      ? {
          feasible: true,
          objective: best.cost,
          assignments: best.assignments,
          coverage: best.coverage,
          examined,
          exact: true,
        }
      : {
          feasible: false,
          objective: null,
          assignments: [],
          examined,
          exact: true,
          reason:
            'No roster covers every shift within availability, daily hours and overlapping-shift constraints.',
        };
  }
  function el(tag, cls, text) {
    const node = document.createElement(tag);
    if (cls) node.className = cls;
    if (text != null) node.textContent = text;
    return node;
  }
  function number(value, minimum, maximum, label) {
    const input = el('input');
    input.type = 'number';
    input.min = minimum;
    input.max = maximum;
    input.step = 1;
    input.value = value;
    input.setAttribute('aria-label', label);
    return input;
  }
  function metric(label, value) {
    const node = el('div', 'schedule-metric');
    node.append(el('span', '', label), el('strong', '', value));
    return node;
  }
  function mount(root, inputs) {
    mounted.get(root)?.();
    const state = inputs == null ? seed() : JSON.parse(JSON.stringify(inputs));
    const frame = el('div', 'scheduling-workbench');
    const controls = el('section', 'problem-card schedule-inputs');
    controls.append(el('h2', '', 'Staff the day'));
    const reset = el('button', 'secondary', 'Reset example');
    reset.type = 'button'; reset.onclick = () => mount(root); controls.append(reset);
    controls.append(
      el(
        'p',
        'schedule-note',
        'Choose exact coverage. Overlapping shifts cannot be assigned to the same person. Wages apply to every scheduled hour.',
      ),
    );
    const fields = el('div', 'problem-controls schedule-fields');
    const coverageInputs = shifts.map((shift, i) => {
      const label = el('label', 'problem-field', shift.label + ' coverage');
      const input = number(state.coverage[i], 0, 3, shift.label + ' required employees');
      label.append(input);
      fields.append(label);
      return input;
    });
    const hoursLabel = el('label', 'problem-field', 'Daily hours per person');
    const hours = el('select');
    hours.setAttribute('aria-label', 'Maximum daily hours per employee');
    for (const value of [8, 16]) {
      const option = el('option', '', value + ' hours');
      option.value = value;
      hours.append(option);
    }
    hours.value = String(state.maxHours);
    hoursLabel.append(hours);
    fields.append(hoursLabel);
    controls.append(fields);
    const inputScroll = el('div', 'schedule-table-scroll');
    const inputTable = el('table', 'problem-table schedule-input-table');
    const caption = el('caption', 'sr-only', 'Employee wages and availability by shift');
    inputTable.append(caption);
    const head = el('tr');
    for (const title of ['Employee', 'USD / hour', ...shifts.map((s) => s.label)])
      head.append(el('th', '', title));
    const thead = el('thead');
    thead.append(head);
    inputTable.append(thead);
    const body = el('tbody');
    const workerInputs = state.workers.map((worker) => {
      const row = el('tr');
      const name = el('th', '', worker.name);
      name.scope = 'row';
      row.append(name);
      const rateCell = el('td');
      const rate = number(worker.rate, 1, 100, worker.name + ' hourly wage in USD');
      rateCell.append(rate);
      row.append(rateCell);
      const available = shifts.map((shift, i) => {
        const cell = el('td');
        const check = el('input');
        check.type = 'checkbox';
        check.checked = worker.available[i];
        check.setAttribute('aria-label', worker.name + ' available for ' + shift.label + ' shift');
        cell.append(check);
        row.append(cell);
        return check;
      });
      body.append(row);
      return { rate, available };
    });
    inputTable.append(body);
    inputScroll.append(inputTable);
    controls.append(inputScroll);
    const output = el('section', 'problem-card schedule-output');
    output.setAttribute('aria-label', 'Optimized workforce roster');
    const status = el('p', 'schedule-status');
    status.setAttribute('role', 'status');
    status.setAttribute('aria-live', 'polite');
    const metrics = el('div', 'problem-metrics schedule-metrics');
    const figure = el('div', 'problem-figure schedule-table-scroll');
    output.append(el('h2', '', 'Minimum-cost roster'), status, metrics, figure);
    frame.append(output, controls);
    root.replaceChildren(frame);
    function update() {
      figure.replaceChildren();
      metrics.replaceChildren();
      try {
        const input = {
          coverage: coverageInputs.map((node) => (node.value === '' ? NaN : Number(node.value))),
          maxHours: Number(hours.value),
          workers: state.workers.map((worker, i) => ({
            ...worker,
            rate: workerInputs[i].rate.value === '' ? NaN : Number(workerInputs[i].rate.value),
            available: workerInputs[i].available.map((check) => check.checked),
          })),
        };
        const result = solve(input);
        status.className = 'schedule-status' + (result.feasible ? '' : ' schedule-error');
        status.textContent = result.feasible
          ? 'All required shifts covered. Exact minimum wage cost for these inputs.'
          : result.reason;
        if (!result.feasible) {
          figure.append(
            el(
              'p',
              'schedule-empty',
              'Adjust coverage, availability or the daily hours limit to build a feasible roster.',
            ),
          );
          return;
        }
        metrics.append(
          metric('Daily wage cost', '$' + result.objective.toLocaleString('en-US')),
          metric(
            'Staffed shift places',
            input.coverage.reduce((a, b) => a + b, 0),
          ),
          metric('Employees scheduled', result.assignments.filter((a) => a.shifts.length).length),
        );
        const table = el('table', 'problem-table schedule-roster');
        table.append(el('caption', 'sr-only', 'Optimized employee assignments and shift coverage'));
        const row = el('tr');
        row.append(el('th', '', 'Employee'));
        for (const shift of shifts) {
          const cell = el('th');
          cell.append(el('strong', '', shift.label), el('span', 'schedule-small', shift.time));
          row.append(cell);
        }
        row.append(el('th', '', 'Hours'));
        const h = el('thead');
        h.append(row);
        table.append(h);
        const rows = el('tbody');
        result.assignments.forEach((assignment, i) => {
          const line = el('tr');
          const name = el('th', '', state.workers[i].name);
          name.scope = 'row';
          line.append(name);
          shifts.forEach((shift, j) => {
            const cell = el('td', assignment.shifts.includes(j) ? 'schedule-assigned' : 'schedule-unassigned');
            cell.append(
              el(
                'span',
                '',
                assignment.shifts.includes(j)
                  ? 'Assigned'
                  : input.workers[i].available[j]
                    ? 'Off duty'
                    : 'Unavailable',
              ),
            );
            line.append(cell);
          });
          line.append(el('td', 'schedule-hours', assignment.shifts.length * 8));
          rows.append(line);
        });
        const total = el('tr', 'schedule-total');
        total.append(el('th', '', 'Coverage'));
        input.coverage.forEach((n) => total.append(el('td', '', n + ' / ' + n + ' covered')));
        total.append(el('td', '', ''));
        rows.append(total);
        table.append(rows);
        figure.append(table);
        const mobile = el('div', 'schedule-roster-mobile');
        shifts.forEach((shift, i) => {
          const card = el('section', 'schedule-shift-card');
          const title = el('h3', '', shift.label);
          title.append(
            el(
              'span',
              'schedule-small',
              shift.time + ' · ' + input.coverage[i] + ' / ' + input.coverage[i] + ' covered',
            ),
          );
          const names = result.assignments
            .filter((a) => a.shifts.includes(i))
            .map((a) => state.workers.find((w) => w.id === a.workerId).name);
          card.append(title, el('p', '', names.length ? names.join(' · ') : 'No coverage required'));
          mobile.append(card);
        });
        figure.append(mobile);
      } catch (error) {
        status.className = 'schedule-status schedule-error';
        status.textContent = error.message;
      }
    }
    frame.addEventListener('input', update);
    frame.addEventListener('change', update);
    update();
    mounted.set(root, () => {
      frame.removeEventListener('input', update);
      frame.removeEventListener('change', update);
    });
    return () => { mounted.get(root)?.(); mounted.delete(root); };
  }
  host.OptimizationViews = host.OptimizationViews || {};
  host.OptimizationViews.workforce = { mount };
  if (typeof module !== 'undefined' && module.exports) module.exports = { solve, seed, shifts };
})(typeof window !== 'undefined' ? window : globalThis);
