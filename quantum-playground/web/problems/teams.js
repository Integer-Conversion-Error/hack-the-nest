(function (global) {
  'use strict';
  const skills = ['Engineering', 'Design', 'Presentation'];
  const number = (value) =>
    value === null || typeof value === 'boolean' || String(value).trim() === '' ? NaN : Number(value);
  const key = (a, b) => [a, b].sort().join('|');
  function seed() {
    const people = [
      ['Ava', 3, 1, 1],
      ['Ben', 2, 0, 3],
      ['Cara', 0, 3, 1],
      ['Dev', 3, 1, 0],
      ['Eli', 1, 2, 3],
      ['Fay', 1, 3, 2],
    ].map(([name, ...levels], index) => ({ id: 'U' + (index + 1), name, skills: levels }));
    const compatibility = {};
    for (let a = 0; a < people.length; a++)
      for (let b = a + 1; b < people.length; b++) compatibility[key(people[a].id, people[b].id)] = 10;
    for (const [a, b, friction] of [
      [0, 1, 8],
      [0, 2, 3],
      [0, 5, 1],
      [1, 3, 6],
      [1, 4, 4],
      [2, 3, 7],
      [2, 4, 2],
      [3, 4, 3],
      [3, 5, 5],
      [4, 5, 9],
    ]) {
      compatibility[key(people[a].id, people[b].id)] = 10 - friction;
    }
    return { people, compatibility, team_count: 2, minimum_skill: 2 };
  }
  function validate(input) {
    const errors = [],
      count = number(input?.team_count),
      minimum = number(input?.minimum_skill);
    if (![2, 3].includes(count)) errors.push('Choose two or three teams.');
    if (!Number.isInteger(minimum) || minimum < 0 || minimum > 15)
      errors.push('Required skill coverage must be a whole number from 0 to 15.');
    if (!Array.isArray(input?.people) || input.people.length < 2 || input.people.length > 10)
      return [...errors, 'Use 2–10 people.'];
    if (input.people.length < count) errors.push('Each team needs at least one person.');
    const ids = new Set();
    for (const person of input.people) {
      if (!person || typeof person.id !== 'string' || !person.id || ids.has(person.id)) {
        errors.push('People need unique IDs.');
        continue;
      }
      ids.add(person.id);
      if (typeof person.name !== 'string' || !person.name.trim() || person.name.length > 40)
        errors.push(person.id + ' needs a name of 1–40 characters.');
      if (
        !Array.isArray(person.skills) ||
        person.skills.length !== 3 ||
        person.skills.some((value) => !Number.isInteger(number(value)) || number(value) < 0 || number(value) > 3)
      )
        errors.push(person.id + ' skill levels must be 0–3.');
    }
    if (!input.compatibility || typeof input.compatibility !== 'object')
      errors.push('Add pair compatibility scores.');
    else
      for (const value of Object.values(input.compatibility))
        if (!Number.isInteger(number(value)) || number(value) < 0 || number(value) > 10) {
          errors.push('Pair compatibility must be a whole number from 0 to 10.');
          break;
        }
    return errors;
  }
  function solve(input) {
    const errors = validate(input);
    if (errors.length) return { status: 'invalid', errors };
    const people = input.people,
      count = number(input.team_count),
      minimum = number(input.minimum_skill);
    const small = Math.floor(people.length / count),
      large = Math.ceil(people.length / count),
      teams = [],
      totals = [],
      totalSkills = [0, 0, 0];
    people.forEach((person) =>
      person.skills.forEach((value, index) => {
        totalSkills[index] += number(value);
      }),
    );
    let best = null,
      evaluated = 0,
      feasible = 0;
    function visit(index, score) {
      if (index === people.length) {
        if (teams.length !== count || teams.some((team) => team.length < small)) return;
        evaluated++;
        if (totals.some((total) => total.some((value) => value < minimum))) return;
        feasible++;
        const weakest = Math.min(...totals.flat());
        const imbalance = totals.reduce(
          (sum, total) =>
            sum +
            total.reduce((subtotal, value, skill) => subtotal + (value * count - totalSkills[skill]) ** 2, 0),
          0,
        );
        if (
          !best ||
          score > best.score ||
          (score === best.score &&
            (weakest > best.weakest || (weakest === best.weakest && imbalance < best.imbalance)))
        ) {
          best = {
            score,
            weakest,
            imbalance,
            teams: teams.map((team) => team.map((member) => people[member].id)),
            coverage: totals.map((total) => [...total]),
          };
        }
        return;
      }
      const person = people[index],
        existing = teams.length;
      for (let team = 0; team <= Math.min(existing, count - 1); team++) {
        if (team === existing) {
          teams.push([]);
          totals.push([0, 0, 0]);
        }
        if (teams[team].length < large) {
          const affinity = teams[team].reduce(
            (sum, other) => sum + number(input.compatibility[key(person.id, people[other].id)] ?? 5),
            0,
          );
          teams[team].push(index);
          person.skills.forEach((value, skill) => {
            totals[team][skill] += number(value);
          });
          visit(index + 1, score + affinity);
          teams[team].pop();
          person.skills.forEach((value, skill) => {
            totals[team][skill] -= number(value);
          });
        }
        if (team === existing) {
          teams.pop();
          totals.pop();
        }
      }
    }
    visit(0, 0);
    if (!best)
      return {
        status: 'infeasible',
        evaluated,
        message:
          'No balanced split meets every skill requirement. Lower required coverage or increase people’s skill levels.',
      };
    const pairCount = best.teams.reduce((sum, team) => sum + (team.length * (team.length - 1)) / 2, 0);
    return { status: 'ok', ...best, evaluated, feasible, maximum_score: pairCount * 10 };
  }

  function mount(container) {
    const doc = container.ownerDocument,
      state = seed();
    let selectedPair = [state.people[4].id, state.people[5].id];
    container.classList.add('teams-view');
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
    const field = (label, input) => {
      const wrap = el('label', undefined, 'problem-field');
      wrap.append(el('span', label), input);
      return wrap;
    };
    const select = (options, value, change, label) => {
      const input = el('select');
      input.setAttribute('aria-label', label);
      for (const [value, text] of options) {
        const option = el('option', text);
        option.value = value;
        input.append(option);
      }
      input.value = String(value);
      input.onchange = () => change(input.value);
      return input;
    };
    const controls = el('section', undefined, 'problem-controls');
    controls.setAttribute('aria-label', 'Team settings');
    const settings = el('div', undefined, 'teams-settings');
    const editors = el('div', undefined, 'teams-people');
    const add = button('+ Add person', () => {
      let id = 1;
      while (state.people.some((person) => person.id === 'U' + id)) id++;
      state.people.push({ id: 'U' + id, name: 'New member', skills: [1, 1, 1] });
      renderEditors();
      renderPairEditor();
      update();
    });
    add.id = 'teams-add';
    const pairEditor = el('div', undefined, 'teams-pair-editor');
    const status = el('p', undefined, 'teams-status');
    status.id = 'teams-status';
    status.setAttribute('role', 'status');
    const metrics = el('div', undefined, 'problem-metrics');
    const figure = el('section', undefined, 'problem-figure teams-results');
    figure.setAttribute('aria-label', 'Team assignments and skill coverage');
    const matrix = el('section', undefined, 'problem-figure teams-compatibility');
    matrix.setAttribute('aria-label', 'Pair compatibility');
    controls.append(
      settings,
      el(
        'p',
        'Build balanced teams that cover every skill and maximize compatibility. Skill levels: 0 = none, 3 = strong.',
        'hint',
      ),
    );
    const peopleDetails = el('details', undefined, 'teams-editor-details problem-controls'),
      peopleSummary = el('summary');
    peopleDetails.append(peopleSummary, editors, add);
    const pairDetails = el('details', undefined, 'teams-editor-details problem-controls');
    pairDetails.append(el('summary', 'Edit pair compatibility'), pairEditor);
    container.replaceChildren(controls, status, metrics, figure, matrix, peopleDetails, pairDetails);
    function renderSettings() {
      settings.replaceChildren();
      const count = select(
        [
          [2, '2 teams'],
          [3, '3 teams'],
        ],
        state.team_count,
        (value) => {
          state.team_count = value;
          update();
        },
        'Number of teams',
      );
      const minimum = el('input');
      minimum.type = 'number';
      minimum.min = '0';
      minimum.max = '15';
      minimum.step = '1';
      minimum.value = state.minimum_skill;
      minimum.setAttribute('aria-label', 'Required coverage per skill');
      minimum.oninput = () => {
        state.minimum_skill = minimum.value;
        update();
      };
      settings.append(
        field('Teams', count),
        field('Minimum per skill, per team', minimum),
        button('Reset example', () => {
          Object.assign(state, seed());
          selectedPair = ['U5', 'U6'];
          renderSettings();
          renderEditors();
          renderPairEditor();
          update();
        }),
      );
    }
    function renderEditors() {
      editors.replaceChildren();
      for (const person of state.people) {
        const row = el('div', undefined, 'teams-person');
        row.dataset.person = person.id;
        const name = el('input');
        name.type = 'text';
        name.maxLength = 40;
        name.value = person.name;
        name.setAttribute('aria-label', person.id + ' name');
        name.oninput = () => {
          person.name = name.value;
          update();
          refreshPairNames();
        };
        row.append(field('Person', name));
        skills.forEach((skill, index) =>
          row.append(
            field(
              skill,
              select(
                [0, 1, 2, 3].map((value) => [value, String(value)]),
                person.skills[index],
                (value) => {
                  person.skills[index] = Number(value);
                  update();
                },
                person.id + ' ' + skill,
              ),
            ),
          ),
        );
        const remove = button('Remove', () => {
          state.people = state.people.filter((member) => member !== person);
          for (const pair of Object.keys(state.compatibility))
            if (pair.split('|').includes(person.id)) delete state.compatibility[pair];
          if (selectedPair.includes(person.id)) selectedPair = [state.people[0].id, state.people[1].id];
          renderEditors();
          renderPairEditor();
          update();
        });
        remove.disabled = state.people.length <= 2;
        remove.setAttribute('aria-label', 'Remove ' + person.name);
        row.append(remove);
        editors.append(row);
      }
      add.disabled = state.people.length >= 10;
      peopleSummary.textContent = 'Edit people and skills (' + state.people.length + ')';
    }
    function refreshPairNames() {
      for (const input of pairEditor.querySelectorAll('select[data-pair-person]'))
        for (const option of input.options) {
          option.textContent = state.people.find((person) => person.id === option.value)?.name || option.value;
        }
    }
    function renderPairEditor() {
      pairEditor.replaceChildren(
        el('h3', 'Edit pair compatibility'),
        el('p', '0 = difficult pairing, 10 = works very well together. Unrated pairs start at 5.', 'hint'),
      );
      const row = el('div', undefined, 'teams-pair-fields');
      selectedPair.forEach((person, index) => {
        const input = select(
          state.people.map((person) => [person.id, person.name]),
          person,
          (value) => {
            selectedPair[index] = value;
            renderPairEditor();
          },
          index ? 'Second person' : 'First person',
        );
        input.dataset.pairPerson = String(index);
        row.append(field(index ? 'Second person' : 'First person', input));
      });
      const same = selectedPair[0] === selectedPair[1];
      const rating = el('input');
      rating.type = 'range';
      rating.min = '0';
      rating.max = '10';
      rating.step = '1';
      rating.value = state.compatibility[key(...selectedPair)] ?? 5;
      rating.disabled = same;
      rating.id = 'teams-pair-rating';
      rating.setAttribute('aria-label', 'Pair compatibility rating');
      const output = el('output', String(rating.value) + ' / 10');
      output.htmlFor = rating.id;
      rating.oninput = () => {
        state.compatibility[key(...selectedPair)] = Number(rating.value);
        output.textContent = rating.value + ' / 10';
        update();
      };
      const ratingField = field('Compatibility', rating);
      ratingField.append(output);
      row.append(ratingField);
      pairEditor.append(row);
      if (same) pairEditor.append(el('p', 'Choose two different people to edit their compatibility.', 'hint'));
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
      renderMatrix(result);
      status.classList.toggle('request-error', result.status !== 'ok');
      if (result.status !== 'ok') {
        status.textContent = result.status === 'invalid' ? result.errors.join(' ') : result.message;
        return;
      }
      status.textContent =
        'Exact classical search · ' +
        result.evaluated.toLocaleString() +
        ' balanced partitions checked. Compatibility ties favor stronger, more even skill coverage.';
      metric('Compatibility', result.score + ' / ' + result.maximum_score);
      metric('Teams', result.teams.length);
      metric('Weakest skill coverage', result.weakest);
      metric('Feasible splits', result.feasible.toLocaleString());
      const cards = el('div', undefined, 'teams-assignment');
      result.teams.forEach((team, index) => {
        const card = el('section', undefined, 'problem-card team-card');
        card.dataset.team = String(index + 1);
        card.style.setProperty('--team-color', ['#185f55', '#315b8f', '#8a4d27'][index]);
        card.append(el('h3', 'Team ' + (index + 1)));
        const list = el('ul', undefined, 'teams-members');
        for (const id of team) {
          const person = state.people.find((member) => member.id === id);
          const member = el('li');
          member.append(
            el('strong', person.name),
            el('span', person.skills.map((value, skill) => skills[skill] + ' ' + value).join(' · '), 'hint'),
          );
          list.append(member);
        }
        card.append(list, el('h4', 'Skill coverage'));
        result.coverage[index].forEach((value, skill) => {
          const row = el('div', undefined, 'teams-coverage-row');
          row.append(el('span', skills[skill]), el('strong', value + ' / ' + state.minimum_skill + ' required'));
          const bar = el('meter');
          bar.min = 0;
          bar.max = Math.max(3, team.length * 3);
          bar.value = value;
          bar.setAttribute(
            'aria-label',
            'Team ' +
              (index + 1) +
              ' ' +
              skills[skill] +
              ' coverage ' +
              value +
              ', required ' +
              state.minimum_skill,
          );
          row.append(bar);
          card.append(row);
        });
        cards.append(card);
      });
      figure.append(cards);
    }
    function renderMatrix(result) {
      matrix.replaceChildren(
        el('h3', 'Compatibility map'),
        el(
          'p',
          'Tap a pair to edit it. Outlined scores are pairs placed on the same team. Higher scores are better.',
          'hint',
        ),
      );
      const scroll = el('div', undefined, 'teams-matrix-scroll'),
        table = el('table', undefined, 'problem-table');
      const caption = el('caption', 'Pair compatibility scores from 0 to 10');
      caption.className = 'sr-only';
      table.append(caption);
      const head = el('thead'),
        heading = el('tr');
      heading.append(el('th', 'Person'));
      for (const person of state.people) {
        const th = el('th', person.name);
        th.scope = 'col';
        heading.append(th);
      }
      head.append(heading);
      table.append(head);
      const body = el('tbody');
      for (const person of state.people) {
        const row = el('tr'),
          name = el('th', person.name);
        name.scope = 'row';
        row.append(name);
        for (const other of state.people) {
          const cell = el('td');
          if (person.id === other.id) cell.textContent = '—';
          else {
            const score = number(state.compatibility[key(person.id, other.id)] ?? 5);
            const edit = button(String(score), () => {
              selectedPair = [person.id, other.id];
              pairDetails.open = true;
              renderPairEditor();
              pairEditor.querySelector('input[type=range]').focus();
            });
            edit.setAttribute(
              'aria-label',
              person.name + ' and ' + other.name + ': compatibility ' + score + ' out of 10',
            );
            edit.className = 'teams-pair-score ' + (score >= 7 ? 'strong' : score <= 3 ? 'weak' : 'neutral');
            if (
              result.status === 'ok' &&
              result.teams.some((team) => team.includes(person.id) && team.includes(other.id))
            )
              edit.classList.add('together');
            cell.append(edit);
          }
          row.append(cell);
        }
        body.append(row);
      }
      table.append(body);
      scroll.append(table);
      matrix.append(scroll);
    }
    renderSettings();
    renderEditors();
    renderPairEditor();
    update();
    return () => {
      container.classList.remove('teams-view');
      container.replaceChildren();
    };
  }
  global.OptimizationViews ||= {};
  global.OptimizationViews.teams = { mount };
  if (typeof module !== 'undefined') module.exports = { seed, validate, solve, key };
})(typeof window === 'undefined' ? globalThis : window);
