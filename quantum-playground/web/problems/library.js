/* Each domain owns its inputs, objective and visualization. */
(() => {
  const problems = [
    {
      id: 'facility',
      name: 'Facility placement',
      group: 'Location',
      objective: 'Choose which hubs to open and which demand zones each hub can serve.',
    },
    {
      id: 'workforce',
      name: 'Workforce scheduling',
      group: 'Scheduling',
      objective: 'Cover each shift with available staff at the lowest wage cost.',
    },
    {
      id: 'production',
      name: 'Production scheduling',
      group: 'Scheduling',
      objective: 'Sequence operations on shared machines to finish the last job as early as possible.',
    },
    {
      id: 'timetable',
      name: 'Timetabling',
      group: 'Scheduling',
      objective: 'Place classes in rooms and time slots that satisfy their constraints.',
    },
    {
      id: 'energy',
      name: 'Energy scheduling',
      group: 'Scheduling',
      objective: 'Choose when a battery charges and discharges to reduce the electricity bill.',
    },
    {
      id: 'projects',
      name: 'Project selection',
      group: 'Allocation',
      objective: 'Choose the combination of projects that delivers the most value within budget.',
    },
    {
      id: 'teams',
      name: 'Team formation',
      group: 'Allocation',
      objective: 'Build balanced teams with compatible members and enough coverage for each skill.',
    },
  ];

  window.OptimizationViews = Object.create(null);
  const describe = {id: 'describe', name: 'Describe a problem', objective: 'Describe the decision in your own words. Review the proposed inputs and assumptions before opening the solver.'};
  const scenarios = new Map();
  const scripts = new Map();
  const panels = new Map();
  const links = new Map();
  const select = document.querySelector('#problem-select');
  const navigation = document.querySelector('#problem-links');
  const container = document.querySelector('#problem-views');

  document.querySelector('.skip-link').addEventListener('click', (event) => {
    event.preventDefault();
    document.querySelector('#problem-content').focus();
  });

  select.add(new Option(describe.name, describe.id));
  links.set(describe.id, document.querySelector('#describe-link'));
  const formulation = window.Formulation.create({
    domainName: id => problems.find(problem => problem.id === id)?.name,
    accept(draft, description, clarifications) {
      const domain = problems.find(problem => problem.id === draft.domain);
      if (!domain) throw new Error('This domain is unavailable.');
      const id = 'scenario-' + (scenarios.size + 1);
      const scenario = { ...domain, id, domain: domain.id, inputs: draft.inputs, description, clarifications, assumptions: draft.assumptions };
      scenarios.set(id, scenario);
      const nav = document.querySelector('#scenario-links');
      nav.hidden = false;
      const link = document.createElement('a');
      link.href = '#' + id;
      link.textContent = domain.name + ' (' + scenarios.size + ')';
      nav.append(link);
      links.set(id, link);
      select.add(new Option('Your ' + link.textContent, id));
      location.hash = id;
    }
  });

  for (const group of new Set(problems.map((problem) => problem.group))) {
    const heading = document.createElement('h3');
    heading.textContent = group;
    navigation.append(heading);
    for (const { id, name } of problems.filter((problem) => problem.group === group)) {
      const link = document.createElement('a');
      link.href = '#' + id;
      link.textContent = name;
      navigation.append(link);
      links.set(id, link);
      select.add(new Option(name, id));
    }
  }

  function currentProblem() {
    const id = location.hash.slice(1);
    if (/^scenario-\d+$/.test(id) && !scenarios.has(id)) {
      history.replaceState(null, '', '#describe');
      document.querySelector('#expired-scenario').hidden = false;
      return describe;
    }
    if (id === describe.id) return describe;
    return scenarios.get(id) || problems.find(problem => problem.id === id) || problems[0];
  }

  function load(problem) {
    const moduleId = problem.domain || problem.id;
    if (!scripts.has(moduleId)) {
      const loaded = new Promise((resolve, reject) => {
        const script = document.createElement('script');
        script.src = moduleId + '.js';
        script.onload = resolve;
        script.onerror = () => {
          scripts.delete(moduleId);
          script.remove();
          reject(new Error('Could not load ' + problem.name));
        };
        document.head.append(script);
      });
      scripts.set(moduleId, loaded);
    }
    return scripts.get(moduleId);
  }

  async function display() {
    const problem = currentProblem();
    select.value = problem.id;
    document.title = problem.name + ' · QuantumLoop';
    document.querySelector('#problem-title').textContent = problem.name;
    document.querySelector('#problem-objective').textContent = problem.objective;
    const describing = problem === describe;
    document.querySelector('#describe-panel').hidden = !describing;
    document.querySelector('.solver-note').hidden = describing;
    if (describing) formulation.show();
    for (const [id, link] of links) {
      if (id === problem.id) link.setAttribute('aria-current', 'page');
      else link.removeAttribute('aria-current');
    }
    for (const [id, panel] of panels) panel.hidden = id !== problem.id;
    if (describing) return;
    if (panels.has(problem.id)) {
      window.dispatchEvent(new Event('resize'));
      return;
    }
    const panel = document.createElement('div');
    if (!problem.domain) panel.className = 'domain-view';
    panel.dataset.problem = problem.id;
    panel.setAttribute('aria-busy', 'true');
    panels.set(problem.id, panel);
    container.append(panel);
    try {
      await load(problem);
      const moduleId = problem.domain || problem.id;
      let editor = panel;
      if (problem.domain) {
        editor = document.createElement('div');
        editor.id = problem.id + '-editor';
        editor.className = 'domain-view';
        panel.append(editor);
        const source = document.createElement('details');
        source.className = 'scenario-source';
        const label = document.createElement('summary');
        label.textContent = 'Original scenario description';
        source.append(label);
        for (const line of [problem.description, ...problem.clarifications.map(pair => pair.question + ' — ' + pair.answer), ...problem.assumptions]) {
          const paragraph = document.createElement('p');
          paragraph.textContent = line;
          source.append(paragraph);
        }
        panel.prepend(source);
      }
      window.OptimizationViews[moduleId].mount(editor, problem.inputs);
      if (problem.domain && currentProblem() === problem) {
        window.scrollTo(0, 0);
        document.querySelector('#problem-title').focus({preventScroll: true});
      }
    } catch (error) {
      panels.delete(problem.id);
      panel.remove();
      if (currentProblem() === problem) {
        const status = document.createElement('p');
        status.setAttribute('role', 'alert');
        status.textContent = 'This view could not load. ';
        const retry = document.createElement('button');
        retry.className = 'secondary';
        retry.textContent = 'Try again';
        retry.onclick = () => {
          status.remove();
          display();
        };
        status.append(retry);
        container.append(status);
      }
      console.error(error);
    } finally {
      panel.removeAttribute('aria-busy');
    }
  }

  select.onchange = () => {
    location.hash = select.value;
  };
  window.addEventListener('hashchange', () => {
    container.querySelector(':scope > [role="alert"]')?.remove();
    display();
  });
  display();
})();
