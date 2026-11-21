(function (global) {
  'use strict';
  const number = (value) =>
    value === null || typeof value === 'boolean' || String(value).trim() === '' ? NaN : Number(value);
  const dollars = (value) =>
    new Intl.NumberFormat('en-US', {
      style: 'currency',
      currency: 'USD',
      minimumFractionDigits: Number.isInteger(value) ? 0 : 2,
      maximumFractionDigits: 2,
    }).format(value);
  function seed() {
    const input = {
      budget: 100000,
      projects: [
        ['Solar canopy', 42000, 74],
        ['Lab equipment', 28000, 59],
        ['Accessibility upgrades', 18000, 46, true],
        ['Student app', 12000, 24],
        ['Community garden', 9000, 17],
        ['Energy sensors', 15000, 31],
        ['Shuttle pilot', 32000, 57],
        ['Library hours', 21000, 43],
      ].map(([name, cost, value, required], index) => ({
        id: 'P' + (index + 1),
        name,
        cost,
        value,
        required: Boolean(required),
        requires: '',
      })),
    };
    input.projects[0].requires = 'P6';
    input.projects[6].requires = 'P4';
    return input;
  }
  function validate(input) {
    const errors = [],
      budget = number(input?.budget);
    const money = (value) =>
      Number.isFinite(value) &&
      value >= 0 &&
      value <= 10000000 &&
      Math.abs(value * 100 - Math.round(value * 100)) < 1e-6;
    if (!money(budget)) errors.push('Budget must be $0–$10,000,000 with at most two decimal places.');
    if (!Array.isArray(input?.projects) || input.projects.length < 1 || input.projects.length > 14)
      return [...errors, 'Use 1–14 projects.'];
    const ids = new Set();
    for (const project of input.projects) {
      if (!project || typeof project.id !== 'string' || !project.id || ids.has(project.id)) {
        errors.push('Projects need unique IDs.');
        continue;
      }
      ids.add(project.id);
      if (typeof project.name !== 'string' || !project.name.trim() || project.name.length > 60)
        errors.push(project.id + ' needs a name of 1–60 characters.');
      if (!money(number(project.cost)))
        errors.push(project.id + ' cost must be $0–$10,000,000 with at most two decimal places.');
      if (!Number.isInteger(number(project.value)) || number(project.value) < 0 || number(project.value) > 1000)
        errors.push(project.id + ' value must be a whole number from 0 to 1,000.');
      if (
        project.requires &&
        (project.requires === project.id || !input.projects.some((other) => other?.id === project.requires))
      )
        errors.push(project.id + ' needs a different existing project as its prerequisite.');
    }
    return errors;
  }
  function solve(input) {
    const errors = validate(input);
    if (errors.length) return { status: 'invalid', errors };
    const projects = input.projects,
      budget = Math.round(number(input.budget) * 100),
      size = 2 ** projects.length;
    let required = 0,
      best = null,
      evaluated = 0;
    const indices = new Map(projects.map((project, index) => [project.id, index]));
    projects.forEach((project, index) => {
      if (project.required) required |= 1 << index;
    });
    for (let mask = 0; mask < size; mask++) {
      evaluated++;
      if ((mask & required) !== required) continue;
      let spend = 0,
        value = 0,
        dependencies = true;
      for (let index = 0; index < projects.length; index++)
        if (mask & (1 << index)) {
          spend += Math.round(number(projects[index].cost) * 100);
          value += number(projects[index].value);
          if (projects[index].requires && !(mask & (1 << indices.get(projects[index].requires))))
            dependencies = false;
        }
      if (spend > budget || !dependencies) continue;
      if (!best || value > best.value || (value === best.value && spend < best.spend))
        best = { mask, spend, value };
    }
    if (!best)
      return {
        status: 'infeasible',
        evaluated,
        message:
          'Required projects and their prerequisites exceed the budget. Increase the budget or clear a Required checkbox.',
      };
    return {
      status: 'ok',
      evaluated,
      value: best.value,
      spend: best.spend / 100,
      remaining: (budget - best.spend) / 100,
      selected: projects.filter((project, index) => best.mask & (1 << index)).map((project) => project.id),
      deferred: projects.filter((project, index) => !(best.mask & (1 << index))).map((project) => project.id),
    };
  }

  function mount(container, inputs) {
    const doc = container.ownerDocument,
      state = inputs == null ? seed() : JSON.parse(JSON.stringify(inputs));
    const namespace = container.dataset?.problem && container.dataset.problem !== 'projects' ? container.dataset.problem
      : container.id?.startsWith('scenario-') ? container.id : '';
    const elementId = value => namespace ? namespace + '-' + value : value;
    container.classList.add('projects-view');
    const el = (tag, text, cls) => {
      const node = doc.createElement(tag);
      if (text !== undefined) node.textContent = text;
      if (cls) node.className = cls;
      return node;
    };
    const button = (text, action) => {
      const node = el('button', text, 'secondary');
      node.type = 'button';
      node.onclick = action;
      return node;
    };
    const field = (label, value, change, options = {}) => {
      const wrap = el('label', undefined, 'problem-field');
      wrap.append(el('span', label));
      const input = el('input');
      input.type = options.type || 'number';
      input.value = value;
      input.setAttribute('aria-label', options.aria || label);
      if (input.type === 'number') {
        input.min = '0';
        input.max = String(options.max ?? 10000000);
        input.step = String(options.step ?? 0.01);
      }
      if (input.type === 'text') input.maxLength = 60;
      input.oninput = () => {
        change(input.value);
        update();
      };
      wrap.append(input);
      return wrap;
    };
    const controls = el('section', undefined, 'problem-controls');
    controls.setAttribute('aria-label', 'Project settings');
    const top = el('div', undefined, 'projects-top');
    top.append(
      field(
        'Available budget (USD)',
        state.budget,
        (value) => {
          state.budget = value;
        },
        { aria: 'Project budget' },
      ),
      button('Reset example', () => {
        Object.assign(state, seed());
        render();
      }),
    );
    controls.append(
      top,
      el(
        'p',
        'Choose projects that deliver the most value within your budget. Required projects and selected prerequisites must be funded.',
        'hint',
      ),
    );
    const editors = el('div', undefined, 'projects-editors');
    const add = button('+ Add project', () => {
      let id = 1;
      while (state.projects.some((project) => project.id === 'P' + id)) id++;
      state.projects.push({
        id: 'P' + id,
        name: 'New project',
        cost: 10000,
        value: 20,
        required: false,
        requires: '',
      });
      renderEditors();
      update();
    });
    add.id = elementId('projects-add');
    const details = el('details', undefined, 'projects-editor-details problem-controls'),
      detailsSummary = el('summary');
    details.append(detailsSummary, editors, add);
    const status = el('p', undefined, 'projects-status');
    status.id = elementId('projects-status');
    status.setAttribute('role', 'status');
    const metrics = el('div', undefined, 'problem-metrics');
    const figure = el('section', undefined, 'problem-figure projects-results');
    figure.setAttribute('aria-label', 'Project selection results');
    container.replaceChildren(controls, status, metrics, figure, details);
    function renderEditors() {
      editors.replaceChildren();
      for (const project of state.projects) {
        const row = el('div', undefined, 'project-editor-row');
        row.dataset.project = project.id;
        row.append(
          field(
            'Project',
            project.name,
            (value) => {
              project.name = value;
              refreshPrerequisiteNames();
            },
            { type: 'text', aria: project.id + ' name' },
          ),
          field(
            'Cost (USD)',
            project.cost,
            (value) => {
              project.cost = value;
            },
            { aria: project.id + ' cost' },
          ),
          field(
            'Value points',
            project.value,
            (value) => {
              project.value = value;
            },
            { max: 1000, step: 1, aria: project.id + ' value' },
          ),
        );
        const required = el('label', undefined, 'project-required'),
          checkbox = el('input');
        checkbox.type = 'checkbox';
        checkbox.checked = project.required;
        checkbox.setAttribute('aria-label', project.id + ' required');
        checkbox.onchange = () => {
          project.required = checkbox.checked;
          update();
        };
        required.append(checkbox, el('span', 'Required'));
        row.append(required);
        const dependency = el('label', undefined, 'problem-field');
        dependency.append(el('span', 'Prerequisite'));
        const input = el('select');
        input.setAttribute('aria-label', project.id + ' prerequisite');
        const none = el('option', 'No prerequisite');
        none.value = '';
        input.append(none);
        for (const other of state.projects)
          if (other !== project) {
            const option = el('option', other.name);
            option.value = other.id;
            input.append(option);
          }
        input.value = project.requires || '';
        input.onchange = () => {
          project.requires = input.value;
          renderEditors();
          update();
        };
        dependency.append(input);
        row.append(dependency);
        const remove = button('Remove', () => {
          state.projects = state.projects.filter((item) => item !== project);
          renderEditors();
          update();
        });
        const dependents = state.projects.filter((other) => other.requires === project.id);
        remove.disabled = state.projects.length === 1 || Boolean(dependents.length);
        if (dependents.length)
          remove.title =
            'Change the prerequisite of ' +
            dependents.map((other) => other.name).join(', ') +
            ' before removing this project.';
        remove.setAttribute('aria-label', 'Remove ' + project.name);
        row.append(remove);
        editors.append(row);
      }
      add.disabled = state.projects.length >= 14;
      detailsSummary.textContent = 'Edit projects, values, and prerequisites (' + state.projects.length + ')';
    }
    function refreshPrerequisiteNames() {
      for (const input of editors.querySelectorAll('select'))
        for (const option of input.options)
          if (option.value)
            option.textContent =
              state.projects.find((project) => project.id === option.value)?.name || option.value;
    }
    function metric(label, value) {
      const card = el('div', undefined, 'problem-card');
      card.append(el('span', label), el('strong', value));
      metrics.append(card);
    }
    function update() {
      const result = solve(state);
      metrics.replaceChildren();
      figure.replaceChildren();
      status.classList.toggle('request-error', result.status !== 'ok');
      if (result.status !== 'ok') {
        status.textContent = result.status === 'invalid' ? result.errors.join(' ') : result.message;
        return;
      }
      const selected = new Set(result.selected),
        deferred = state.projects.filter((project) => !selected.has(project.id));
      status.textContent =
        'Exact classical search · ' +
        result.evaluated.toLocaleString() +
        ' subsets checked. Equal-value plans use the lower spend.';
      metric('Selected value', result.value + ' points');
      metric('Budget used', dollars(result.spend));
      metric('Remaining', dollars(result.remaining));
      metric('Funded projects', result.selected.length + ' / ' + state.projects.length);
      const summary = el('div', undefined, 'projects-summary');
      for (const [label, rows, active] of [
        ['Funded', state.projects.filter((project) => selected.has(project.id)), true],
        ['Deferred', deferred, false],
      ]) {
        const card = el('div', undefined, 'problem-card' + (active ? ' funded' : ' deferred'));
        card.append(
          el('h3', label),
          el(
            'p',
            rows.reduce((sum, project) => sum + number(project.value), 0) +
              ' value points · ' +
              dollars(rows.reduce((sum, project) => sum + number(project.cost), 0)),
          ),
        );
        summary.append(card);
      }
      figure.append(
        summary,
        el('h3', 'Value and cost by project'),
        el('p', 'Each column uses its own scale. Green projects are funded; gray projects are deferred.', 'hint'),
      );
      const chart = el('div', undefined, 'projects-chart'),
        maxCost = Math.max(1, ...state.projects.map((project) => number(project.cost))),
        maxValue = Math.max(1, ...state.projects.map((project) => number(project.value)));
      const heading = el('div', undefined, 'projects-chart-heading');
      heading.append(el('span', 'Project'), el('span', 'Value points'), el('span', 'Cost (USD)'));
      chart.append(heading);
      for (const project of [...state.projects].sort(
        (a, b) => Number(selected.has(b.id)) - Number(selected.has(a.id)),
      )) {
        const row = el(
          'div',
          undefined,
          'projects-chart-row' + (selected.has(project.id) ? ' funded' : ' deferred'),
        );
        const name = el('div');
        name.append(
          el('strong', project.name),
          el(
            'span',
            selected.has(project.id) ? (project.required ? 'Funded · Required' : 'Funded') : 'Deferred',
            'hint',
          ),
        );
        if (project.requires)
          name.append(
            el('span', 'Needs ' + state.projects.find((other) => other.id === project.requires).name, 'hint'),
          );
        row.append(name);
        for (const [value, maximum, text, kind] of [
          [number(project.value), maxValue, String(project.value), 'value'],
          [number(project.cost), maxCost, dollars(number(project.cost)), 'cost'],
        ]) {
          const bar = el('div', undefined, 'project-bar-cell');
          const track = el('div', undefined, 'project-bar-track');
          const fill = el('span', undefined, 'project-bar-fill ' + kind);
          fill.style.width = (value / maximum) * 100 + '%';
          track.append(fill);
          bar.append(el('span', text), track);
          row.append(bar);
        }
        chart.append(row);
      }
      figure.append(chart);
    }
    function render() {
      top.replaceChildren(
        field(
          'Available budget (USD)',
          state.budget,
          (value) => {
            state.budget = value;
          },
          { aria: 'Project budget' },
        ),
        button('Reset example', () => {
          Object.assign(state, seed());
          render();
        }),
      );
      renderEditors();
      update();
    }
    renderEditors();
    update();
    return () => {
      container.classList.remove('projects-view');
      container.replaceChildren();
    };
  }
  global.OptimizationViews ||= {};
  global.OptimizationViews.projects = { mount };
  if (typeof module !== 'undefined') module.exports = { seed, validate, solve };
})(typeof window === 'undefined' ? globalThis : window);
