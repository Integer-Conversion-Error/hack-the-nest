/* Each domain owns its inputs, objective and visualization. */
(() => {
  const problems = [
    {
      id: 'facility',
      name: 'Facility placement',
      group: 'Location',
      objective: 'Choose which sites to open to put the most demand within reach.',
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
    return problems.find((problem) => '#' + problem.id === location.hash) || problems[0];
  }

  function load(problem) {
    if (!scripts.has(problem.id)) {
      const loaded = new Promise((resolve, reject) => {
        const script = document.createElement('script');
        script.src = problem.id + '.js';
        script.onload = resolve;
        script.onerror = () => {
          scripts.delete(problem.id);
          script.remove();
          reject(new Error('Could not load ' + problem.name));
        };
        document.head.append(script);
      });
      scripts.set(problem.id, loaded);
    }
    return scripts.get(problem.id);
  }

  async function display() {
    const problem = currentProblem();
    select.value = problem.id;
    document.title = problem.name + ' · QuantumLoop';
    document.querySelector('#problem-title').textContent = problem.name;
    document.querySelector('#problem-objective').textContent = problem.objective;
    for (const [id, link] of links) {
      if (id === problem.id) link.setAttribute('aria-current', 'page');
      else link.removeAttribute('aria-current');
    }
    for (const [id, panel] of panels) panel.hidden = id !== problem.id;
    if (panels.has(problem.id)) {
      window.dispatchEvent(new Event('resize'));
      return;
    }
    const panel = document.createElement('div');
    panel.className = 'domain-view';
    panel.dataset.problem = problem.id;
    panel.setAttribute('aria-busy', 'true');
    panels.set(problem.id, panel);
    container.append(panel);
    try {
      await load(problem);
      window.OptimizationViews[problem.id].mount(panel);
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
