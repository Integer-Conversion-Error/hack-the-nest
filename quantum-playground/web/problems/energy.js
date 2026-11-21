(function (global) {
  'use strict';
  const number = (value) =>
    value === null || typeof value === 'boolean' || String(value).trim() === '' ? NaN : Number(value);
  const money = (value) =>
    new Intl.NumberFormat('en-CA', { style: 'currency', currency: 'CAD', maximumFractionDigits: 2 }).format(value);
  const EPS = 1e-8;
  function seed() {
    const loads = [5, 5, 8, 13, 17, 18, 13, 8],
      solar = [0, 0, 2, 12, 16, 4, 0, 0],
      prices = [0.12, 0.1, 0.14, 0.22, 0.3, 0.45, 0.26, 0.16];
    return {
      capacity: 12,
      initial: 4,
      reserve: 2,
      power: 2,
      gridLimit: 6,
      efficiency: 90,
      feedIn: 0.05,
      cycling: 0.015,
      intervals: loads.map((load, i) => ({
        label: String(i * 3).padStart(2, '0') + '–' + String((i + 1) * 3).padStart(2, '0'),
        load,
        solar: solar[i],
        price: prices[i],
      })),
    };
  }
  function validate(input) {
    const errors = [],
      integer = (key, max) => {
        const value = number(input?.[key]);
        if (!Number.isInteger(value) || value < 0 || value > max)
          errors.push(key + ' must be a whole number from 0 to ' + max + '.');
      },
      bounded = (key, max, min = 0) => {
        const value = number(input?.[key]);
        if (!Number.isFinite(value) || value < min || value > max)
          errors.push(key + ' must be between ' + min + ' and ' + max + '.');
      };
    integer('capacity', 40);
    integer('initial', 40);
    integer('reserve', 40);
    bounded('power', 12);
    bounded('gridLimit', 20);
    bounded('efficiency', 100, 50);
    bounded('feedIn', 2);
    bounded('cycling', 1);
    if (number(input?.initial) > number(input?.capacity))
      errors.push('Starting charge cannot exceed battery capacity.');
    if (number(input?.reserve) > number(input?.initial))
      errors.push('Minimum reserve cannot exceed starting charge.');
    if (!Array.isArray(input?.intervals) || input.intervals.length < 1 || input.intervals.length > 12)
      return [...errors, 'Use 1–12 three-hour intervals.'];
    for (const [i, interval] of input.intervals.entries()) {
      for (const key of ['load', 'solar', 'price']) {
        const v = number(interval?.[key]),
          max = key === 'price' ? 2 : 100;
        if (!Number.isFinite(v) || v < 0 || v > max)
          errors.push('Interval ' + (i + 1) + ' ' + key + ' must be between 0 and ' + max + '.');
      }
    }
    return errors;
  }
  function transition(input, interval, before, after) {
    const eta = Math.sqrt(number(input.efficiency) / 100),
      delta = after - before;
    const charge = delta > 0 ? delta / eta : 0,
      discharge = delta < 0 ? -delta * eta : 0;
    if (Math.max(charge, discharge) > number(input.power) * 3 + EPS) return null;
    const net = number(interval.load) - number(interval.solar) + charge - discharge;
    const imports = Math.max(0, net),
      exports = Math.max(0, -net);
    if (imports > number(input.gridLimit) * 3 + EPS) return null;
    const cyclingCost = Math.abs(delta) * number(input.cycling);
    return {
      before,
      after,
      charge,
      discharge,
      imports,
      exports,
      loss: charge - discharge - delta,
      cost: imports * number(interval.price) - exports * number(input.feedIn) + cyclingCost,
      cyclingCost,
    };
  }
  function summarize(rows) {
    const totals = { cost: 0, imports: 0, exports: 0, charge: 0, discharge: 0, loss: 0, cyclingCost: 0, peak: 0 };
    for (const row of rows) {
      for (const key of ['cost', 'imports', 'exports', 'charge', 'discharge', 'loss', 'cyclingCost'])
        totals[key] += row[key];
      totals.peak = Math.max(totals.peak, row.imports / 3);
    }
    return totals;
  }
  function baseline(input) {
    const rows = input.intervals.map((interval) => {
      const net = number(interval.load) - number(interval.solar),
        imports = Math.max(0, net),
        exports = Math.max(0, -net);
      return {
        imports,
        exports,
        charge: 0,
        discharge: 0,
        loss: 0,
        cyclingCost: 0,
        cost: imports * number(interval.price) - exports * number(input.feedIn),
      };
    });
    return {
      ...summarize(rows),
      feasible: rows.every((row) => row.imports <= number(input.gridLimit) * 3 + EPS),
      rows,
    };
  }
  function solve(input) {
    const errors = validate(input);
    if (errors.length) return { status: 'invalid', errors };
    const capacity = number(input.capacity),
      reserve = number(input.reserve),
      initial = number(input.initial),
      layers = [];
    let previous = new Map([[initial, { cost: 0, throughput: 0 }]]),
      evaluated = 0;
    for (const interval of input.intervals) {
      const next = new Map();
      for (const [before, value] of previous)
        for (let after = reserve; after <= capacity; after++) {
          evaluated++;
          const row = transition(input, interval, before, after);
          if (!row) continue;
          const cost = value.cost + row.cost,
            throughput = value.throughput + Math.abs(after - before),
            current = next.get(after);
          if (
            !current ||
            cost < current.cost - EPS ||
            (Math.abs(cost - current.cost) <= EPS && throughput < current.throughput)
          )
            next.set(after, { cost, throughput, row });
        }
      layers.push(next);
      previous = next;
    }
    const reference = baseline(input);
    if (!previous.has(initial))
      return {
        status: 'infeasible',
        evaluated,
        baseline: reference,
        message:
          'No schedule meets the grid limit, reserve and ending-charge requirement at 1 kWh resolution. Increase the grid limit, battery capacity or power, or reduce demand.',
      };
    const rows = [];
    let state = initial;
    for (let i = layers.length - 1; i >= 0; i--) {
      const row = layers[i].get(state).row;
      rows.unshift(row);
      state = row.before;
    }
    return {
      status: 'ok',
      rows,
      ...summarize(rows),
      evaluated,
      baseline: reference,
      savings: reference.feasible ? reference.cost - previous.get(initial).cost : null,
    };
  }

  function mount(container) {
    const doc = container.ownerDocument,
      state = seed(),
      ns = 'http://www.w3.org/2000/svg';
    container.classList.add('energy-view', 'domain-view');
    const el = (tag, text, cls) => {
      const node = doc.createElement(tag);
      if (text !== undefined) node.textContent = text;
      if (cls) node.className = cls;
      return node;
    };
    const svgEl = (tag, attrs = {}, text) => {
      const node = doc.createElementNS(ns, tag);
      for (const [key, value] of Object.entries(attrs)) node.setAttribute(key, value);
      if (text !== undefined) node.textContent = text;
      return node;
    };
    const field = (label, key, options = {}) => {
      const wrap = el('label', undefined, 'problem-field');
      wrap.append(el('span', label));
      const input = el('input');
      input.type = 'number';
      input.value = state[key];
      for (const [key, value] of Object.entries({ min: 0, max: 40, step: 1, ...options }))
        input.setAttribute(key, value);
      input.setAttribute('aria-label', label);
      input.oninput = () => {
        state[key] = input.value;
        update();
      };
      wrap.append(input);
      return wrap;
    };
    const controls = el('section', undefined, 'problem-card');
    controls.setAttribute('aria-label', 'Battery and grid controls');
    const fields = el('div', undefined, 'problem-controls');
    controls.append(fields);
    const reset = el('button', 'Reset example', 'secondary');
    reset.type = 'button';
    reset.onclick = () => {
      Object.assign(state, seed());
      renderInputs();
      update();
    };
    controls.append(
      el(
        'p',
        'Use storage to shift electricity purchases between three-hour intervals. Prices and costs are in CAD. The battery must end with its starting charge.',
        'hint',
      ),
      reset,
    );
    const inputCard = el('section', undefined, 'problem-card');
    inputCard.append(el('h2', 'Demand, solar and tariff'));
    const inputWrap = el('div', undefined, 'energy-table-wrap'),
      inputTable = el('table', undefined, 'problem-table energy-inputs');
    inputWrap.append(inputTable);
    inputCard.append(inputWrap);
    const status = el('p', undefined, 'energy-status');
    status.setAttribute('role', 'status');
    const metrics = el('div', undefined, 'problem-metrics energy-metrics');
    const figure = el('figure', undefined, 'problem-figure');
    figure.append(el('h2', 'Daily power balance'));
    const chart = el('div', undefined, 'energy-chart'),
      caption = el(
        'figcaption',
        'Time labels mark the start of each three-hour interval. Bars show solar, grid imports and battery flow; the demand line uses the same kWh scale. Positive battery flow serves demand; negative flow charges storage.',
      );
    figure.append(chart, caption);
    const detail = el('section', undefined, 'problem-card');
    detail.append(el('h2', 'Dispatch schedule'));
    const detailWrap = el('div', undefined, 'energy-table-wrap');
    detail.append(detailWrap);
    const method = el('details', undefined, 'problem-card');
    method.append(
      el('summary', 'Model and search limits'),
      el(
        'p',
        'A local classical dynamic program finds the lowest cost over whole-kWh battery states. Charge/discharge losses split the round-trip efficiency equally. Power and grid limits apply to three-hour interval averages. Solar is used or exported; exports earn the feed-in tariff, including energy discharged from storage. Cycling cost applies per kWh added to or removed from the battery. This model has no demand charges or intrainterval peaks.',
      ),
    );
    container.replaceChildren(status, metrics, figure, controls, inputCard, detail, method);
    function heading(table, labels) {
      const head = el('thead'),
        row = el('tr');
      for (const label of labels) {
        const th = el('th', label);
        th.scope = 'col';
        row.append(th);
      }
      head.append(row);
      table.append(head);
    }
    function renderInputs() {
      fields.replaceChildren(
        field('Capacity (kWh)', 'capacity'),
        field('Starting charge (kWh)', 'initial'),
        field('Reserve (kWh)', 'reserve'),
        field('Battery power (kW)', 'power', { max: 12, step: 0.5 }),
        field('Grid import limit (kW)', 'gridLimit', { max: 20, step: 0.5 }),
        field('Round-trip efficiency (%)', 'efficiency', { min: 50, max: 100 }),
        field('Export tariff ($/kWh)', 'feedIn', { max: 2, step: 0.01 }),
        field('Cycling cost ($/kWh)', 'cycling', { max: 1, step: 0.005 }),
      );
      inputTable.replaceChildren();
      heading(inputTable, ['Time', 'Demand (kWh)', 'Solar (kWh)', 'Import ($/kWh)']);
      const body = el('tbody');
      state.intervals.forEach((interval, i) => {
        const row = el('tr'),
          th = el('th', interval.label);
        th.scope = 'row';
        row.append(th);
        for (const key of ['load', 'solar', 'price']) {
          const td = el('td'),
            input = el('input');
          input.type = 'number';
          input.min = '0';
          input.max = key === 'price' ? '2' : '100';
          input.step = key === 'price' ? '.01' : '1';
          input.value = interval[key];
          input.setAttribute(
            'aria-label',
            interval.label +
              ' ' +
              (key === 'load' ? 'demand kWh' : key === 'solar' ? 'solar kWh' : 'import price per kWh'),
          );
          input.oninput = () => {
            state.intervals[i][key] = input.value;
            update();
          };
          td.append(input);
          row.append(td);
        }
        body.append(row);
      });
      inputTable.append(body);
    }
    function metric(label, value, note) {
      const box = el('div', undefined, 'problem-card');
      box.append(el('span', label), el('strong', value));
      if (note) box.append(el('small', note));
      metrics.append(box);
    }
    const compactChart = global.matchMedia('(max-width: 650px)');
    function draw(result) {
      chart.replaceChildren();
      const compact = compactChart.matches;
      const W = compact ? 320 : 820,
        H = compact ? 210 : 310,
        left = compact ? 29 : 50;
      const right = 16,
        top = 24,
        bottom = 45,
        width = W - left - right;
      const max = Math.max(
        1,
        ...state.intervals.map((x) => Math.max(number(x.load), number(x.solar))),
        ...result.rows.map((x) => Math.max(x.imports, x.discharge, x.charge)),
      );
      const maxNegative = Math.max(1, ...result.rows.map((x) => x.charge)),
        range = max + maxNegative,
        y = (v) => top + ((max - v) / range) * (H - top - bottom),
        slot = width / result.rows.length;
      const svg = svgEl('svg', {
        viewBox: '0 0 ' + W + ' ' + H,
        role: 'img',
        'aria-label': 'Three-hour energy flow in kWh. Exact interval amounts are in the dispatch table.',
      });
      svg.append(svgEl('text', { x: left - 8, y: 12, 'text-anchor': 'end' }, 'kWh'));
      for (let i = 0; i <= 4; i++) {
        const v = (max * i) / 4;
        svg.append(
          svgEl('line', { x1: left, x2: W - right, y1: y(v), y2: y(v), class: 'energy-grid' }),
          svgEl('text', { x: left - 8, y: y(v) + 4, 'text-anchor': 'end' }, Number(v.toFixed(2))),
        );
      }
      svg.append(svgEl('line', { x1: left, x2: W - right, y1: y(0), y2: y(0), class: 'energy-axis' }));
      const demandPoints = [];
      result.rows.forEach((row, i) => {
        const center = left + slot * (i + 0.5),
          bar = Math.min(20, slot / 5),
          data = [
            [number(state.intervals[i].solar), 'energy-solar', -1.1],
            [row.imports, 'energy-import', 0],
            [row.discharge - row.charge, 'energy-battery', 1.1],
          ];
        for (const [value, cls, offset] of data) {
          const rect = svgEl('rect', {
            x: center + offset * bar - bar / 2,
            y: Math.min(y(0), y(value)),
            width: bar * 0.9,
            height: Math.abs(y(value) - y(0)),
            class: cls,
          });
          rect.append(
            svgEl(
              'title',
              {},
              state.intervals[i].label +
                ': ' +
                Math.abs(value).toFixed(2) +
                ' kWh ' +
                (cls === 'energy-battery'
                  ? value < 0
                    ? 'battery charge'
                    : 'battery discharge'
                  : cls === 'energy-solar'
                    ? 'solar'
                    : 'grid import'),
            ),
          );
          svg.append(rect);
        }
        demandPoints.push(center + ',' + y(number(state.intervals[i].load)));
        svg.append(
          svgEl(
            'text',
            { x: center, y: H - 16, 'text-anchor': 'middle' },
            compact ? state.intervals[i].label.slice(0, 2) : state.intervals[i].label,
          ),
        );
      });
      svg.append(svgEl('polyline', { points: demandPoints.join(' '), class: 'energy-demand' }));
      chart.append(svg);
      const legend = el('div', undefined, 'energy-legend');
      for (const [cls, label] of [
        ['solar', 'Solar'],
        ['import', 'Grid import'],
        ['battery', 'Battery'],
        ['demand', 'Demand'],
      ]) {
        const item = el('span', label, 'energy-key ' + cls);
        legend.append(item);
      }
      chart.append(legend);
      const soc = svgEl('svg', {
          viewBox: '0 0 ' + W + ' 155',
          role: 'img',
          'aria-label':
            'Battery charge from ' +
            number(state.initial) +
            ' kWh back to ' +
            number(state.initial) +
            ' kWh. Reserve ' +
            number(state.reserve) +
            ' kWh.',
        }),
        cap = Math.max(1, number(state.capacity)),
        sy = (v) => 115 - (v / cap) * 90;
      soc.append(
        svgEl('text', { x: left, y: 16 }, 'Battery charge (kWh)'),
        svgEl('line', {
          x1: left,
          x2: W - right,
          y1: sy(number(state.reserve)),
          y2: sy(number(state.reserve)),
          class: 'energy-reserve',
        }),
        svgEl(
          'text',
          { x: W - right, y: sy(number(state.reserve)) - 5, 'text-anchor': 'end' },
          'Reserve ' + state.reserve,
        ),
      );
      const values = [number(state.initial), ...result.rows.map((row) => row.after)],
        points = values.map((v, i) => left + (width * i) / result.rows.length + ',' + sy(v));
      soc.append(svgEl('polyline', { points: points.join(' '), class: 'energy-soc' }));
      values.forEach((v, i) => {
        const x = left + (width * i) / result.rows.length;
        soc.append(
          svgEl('circle', { cx: x, cy: sy(v), r: 3, class: 'energy-soc-dot' }),
          svgEl(
            'text',
            { x, y: 140, 'text-anchor': 'middle' },
            String(i * 3).padStart(2, '0') + (compact ? '' : ':00'),
          ),
          svgEl('text', { x, y: sy(v) - 7, 'text-anchor': 'middle' }, v),
        );
      });
      chart.append(soc);
    }
    function update() {
      const result = solve(state);
      metrics.replaceChildren();
      detailWrap.replaceChildren();
      chart.replaceChildren();
      status.classList.toggle('request-error', result.status !== 'ok');
      if (result.status !== 'ok') {
        status.textContent = result.status === 'invalid' ? result.errors.join(' ') : result.message;
        caption.hidden = true;
        return;
      }
      caption.hidden = false;
      status.textContent =
        'Exact classical dynamic program · ' +
        result.evaluated.toLocaleString() +
        ' state transitions checked · 1 kWh storage steps.';
      metric('Daily energy cost', money(result.cost), 'Includes exports and cycling');
      metric(
        'Without storage',
        result.baseline.feasible ? money(result.baseline.cost) : 'Grid limit exceeded',
        result.savings === null ? 'Baseline is infeasible' : money(result.savings) + ' saved per day',
      );
      metric(
        'Grid imports',
        result.imports.toFixed(1) + ' kWh',
        'Peak interval average ' + result.peak.toFixed(1) + ' kW',
      );
      metric(
        'Battery delivered',
        result.discharge.toFixed(1) + ' kWh',
        result.loss.toFixed(1) + ' kWh lost to inefficiency',
      );
      draw(result);
      const table = el('table', undefined, 'problem-table');
      heading(table, [
        'Time',
        'Demand',
        'Solar',
        'Import',
        'Export',
        'Charge',
        'Discharge',
        'Stored at end',
        'Cost',
      ]);
      const body = el('tbody');
      result.rows.forEach((row, i) => {
        const tr = el('tr'),
          th = el('th', state.intervals[i].label);
        th.scope = 'row';
        tr.append(th);
        for (const value of [
          number(state.intervals[i].load),
          number(state.intervals[i].solar),
          row.imports,
          row.exports,
          row.charge,
          row.discharge,
          row.after,
        ])
          tr.append(el('td', value.toFixed(1)));
        tr.append(el('td', money(row.cost)));
        body.append(tr);
      });
      table.append(body);
      detailWrap.append(
        el('p', 'Energy columns are kWh. Cost includes export credits and cycling.', 'hint'),
        table,
      );
    }
    renderInputs();
    update();
    compactChart.addEventListener('change', update);
    return () => {
      compactChart.removeEventListener('change', update);
      container.classList.remove('energy-view', 'domain-view');
      container.replaceChildren();
    };
  }
  global.OptimizationViews ||= {};
  global.OptimizationViews.energy = { mount };
  if (typeof module !== 'undefined') module.exports = { seed, validate, transition, baseline, solve };
})(typeof window === 'undefined' ? globalThis : window);
