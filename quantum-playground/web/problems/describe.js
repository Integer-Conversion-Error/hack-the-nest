/* A description becomes editable solver input only after the user accepts the draft. */
window.Formulation = (() => {
  const element = id => document.getElementById(id);
  function node(tag, text) {
    const result = document.createElement(tag);
    if (text !== undefined) result.textContent = text;
    return result;
  }

  const fields = {
    maxSites: 'Maximum open hubs', minutes: 'Maximum outbound driving time (minutes)',
    hourlyCost: 'Driving cost (CAD/hour)', requireFullCoverage: 'Require all demand to be served',
    sites: 'Candidate hubs', demand: 'Demand zones', position: 'Longitude, latitude',
    enabled: 'Available', weight: 'Visits per day', maxHours: 'Maximum hours per employee',
    rate: 'Wage (USD/hour)', coverage: 'Staff needed by shift', available: 'Availability',
    horizon: 'Planning window (hours)', release: 'Release (hours from shift start)', operations: 'Operations in order',
    slotCount: 'Class periods', preferred: 'Preferred period', team_count: 'Teams',
    minimum_skill: 'Minimum total per skill per team', initial: 'Starting charge (kWh)',
    reserve: 'Battery reserve (kWh)', power: 'Battery power (kW)', gridLimit: 'Grid import limit (kW)',
    efficiency: 'Round-trip efficiency (%)', feedIn: 'Export tariff (CAD/kWh)', cycling: 'Cycling cost (CAD/kWh)',
    load: 'Demand per interval (kWh)', solar: 'Solar per interval (kWh)', price: 'Import tariff (CAD/kWh)', label: 'Interval',
    value: 'Benefit points',
    requires: 'Prerequisite', compatibility: 'Pair compatibility', required: 'Required', id: 'ID',
  };

  function fieldName(key, domain) {
    if (key === 'capacity') return { facility: 'Hub capacity (visits/day)', energy: 'Storage capacity (kWh)', timetable: 'Seats' }[domain] || 'Capacity';
    if (key === 'budget') return domain === 'facility' ? 'Daily opening budget (CAD)' : 'Budget (USD)';
    if (key === 'cost') return domain === 'facility' ? 'Daily opening cost (CAD)' : 'Cost (USD)';
    return fields[key] || key.charAt(0).toUpperCase() + key.slice(1);
  }

  const classPeriods = ['09:00–11:00', '11:00–13:00', '14:00–16:00'];
  const shifts = ['Early 06:00–14:00', 'Day 10:00–18:00', 'Late 14:00–22:00'];
  const rules = {
    facility: 'Each hub serves whole zones. Best effort maximizes visits served, then minimizes cost. Full coverage requires every zone to be served. Road costs include outbound and return travel. For custom locations, select Recalculate road times in the editor.',
    workforce: 'This is one day of fixed eight-hour shifts. Staffing requirements are exact. An employee can work both Early and Late with a 16-hour limit. Overlapping shifts cannot be combined.',
    production: 'Each job follows its listed operations in order. Cutting (M1) and Assembly (M2) each process one operation at a time, without interruption. The objective is the earliest last completion, and all jobs must fit the planning window.',
    timetable: 'Each class meets once. A time slot can use each teacher, cohort and room only once. The room must fit the class size. Only the offered periods can be scheduled. Minimize distance from preferred periods first, then unused seats in booked rooms.',
    energy: 'Demand and solar are the total energy in each three-hour interval. The battery uses whole-kWh states and must end at its starting charge. Power and grid limits apply to interval averages. Exports earn the feed-in tariff.',
    projects: 'Maximize total benefit points within the budget, then minimize spending. Required projects must be funded. Funding a project also funds its prerequisite.',
    teams: 'Team sizes differ by at most one. Each team must meet the summed minimum in Engineering, Design and Presentation. Higher pair scores are preferred. People with a score of zero can still be placed together.',
  };
  function showValue(value, key, domain, inputs) {
    if (typeof value === 'boolean') return value ? 'Yes' : 'No';
    if (Number.isInteger(value) && Math.abs(value) >= 1000) return value.toLocaleString('en-US');
    if (key === 'preferred') return classPeriods[value] || String(value);
    if (key === 'slotCount') return classPeriods.slice(0, value).join(', ');
    if (Array.isArray(value)) {
      if (key === 'operations') return value.map(op => (op.machine === 'M1' ? 'Cutting' : 'Assembly') + ' (' + op.duration + ' h)').join(' → ');
      if (key === 'available') return value.map((yes, i) => yes ? (domain === 'workforce' ? shifts[i] : classPeriods[i] + (i >= inputs.slotCount ? ' (not offered)' : '')) : '').filter(Boolean).join(', ') || 'None';
      if (key === 'coverage') return value.map((quantity, i) => shifts[i] + ' ' + quantity).join(', ');
      if (key === 'skills') return value.map((level, i) => ['Engineering', 'Design', 'Presentation'][i] + ' ' + level).join(', ');
      return value.join(', ');
    }
    if (key === 'requires' && !value) return 'None';
    return String(value);
  }

  function appendFacts(parent, pairs) {
    const facts = node('dl');
    facts.className = 'review-facts';
    for (const [label, value] of pairs) facts.append(node('dt', label), node('dd', value));
    parent.append(facts);
  }

  function showInputs(domain, inputs) {
    const details = node('details');
    details.className = 'review-inputs';
    details.open = true;
    details.append(node('summary', 'Solver inputs'));
    if (rules[domain]) details.append(node('p', rules[domain]));
    const pairs = Object.entries(inputs);
    appendFacts(details, pairs.filter(([, value]) => typeof value !== 'object' ||
      (Array.isArray(value) && typeof value[0] !== 'object')).map(([key, value]) =>
      [fieldName(key, domain), showValue(value, key, domain, inputs)]));
    for (const [key, value] of pairs) {
      if (!value || typeof value !== 'object') continue;
      if (!Array.isArray(value)) {
        details.append(node('h3', fieldName(key, domain)));
        const people = new Map((inputs.people || []).map(person => [person.id, person.name]));
        appendFacts(details, Object.entries(value).map(([pair, score]) => key === 'compatibility'
          ? [pair.split('|').map(id => (people.get(id) || id) + ' (' + id + ')').join(' / '), score + ' / 10']
          : [pair, score]));
      } else if (value.length && typeof value[0] === 'object') {
        const columns = Object.keys(value[0]);
        const scroll = node('div');
        scroll.className = 'review-scroll';
        const table = node('table');
        table.className = 'problem-table';
        const title = fieldName(key, domain);
        table.setAttribute('aria-label', title);
        const heading = table.createTHead().insertRow();
        for (const col of columns) {
          const th = node('th', fieldName(col, domain));
          th.scope = 'col';
          heading.append(th);
        }
        const body = table.createTBody();
        for (const record of value) {
          const row = body.insertRow();
          for (const col of columns) {
            const cell = row.insertCell();
            const label = node('span', fieldName(col, domain));
            label.className = 'review-mobile-label';
            label.setAttribute('aria-hidden', 'true');
            cell.append(label, document.createTextNode(showValue(record[col], col, domain, inputs)));
          }
        }
        scroll.append(table);
        details.append(node('h3', title), scroll);
      }
    }
    return details;
  }

  function create({domainName, accept}) {
    const description = element('description');
    const answer = element('clarification-answer');
    const status = element('formulation-status');
    const draftButton = element('draft-button');
    const editButton = element('edit-description');
    const newButton = element('new-description');
    const example = element('example-description');
    const cancel = element('cancel-formulation');
    const clarifyForm = element('clarification-form');
    const review = element('draft-review');
    const history = element('clarification-history');
    let pending = null;
    let transcript = [];
    let question = '';
    let draft = null;
    let checkedConfig = false;
    description.maxLength = 8000;
    answer.maxLength = 1000;
    function message(value, error = false) {
      status.textContent = value;
      status.toggleAttribute('data-error', error);
    }
    function renderControls() {
      const locked = Boolean(question || draft);
      description.readOnly = locked || Boolean(pending);
      draftButton.hidden = Boolean(question || draft);
      draftButton.disabled = Boolean(pending);
      example.hidden = locked || Boolean(pending) || Boolean(description.value.trim());
      editButton.hidden = !locked || Boolean(pending);
      newButton.hidden = Boolean(pending) || !(locked || transcript.length);
      cancel.hidden = !pending;
      clarifyForm.hidden = !question;
      answer.readOnly = Boolean(pending);
      clarifyForm.querySelector('button').disabled = Boolean(pending);
      review.hidden = !draft;
    }
    function focusIfVisible(control) {
      if (!element('describe-panel').hidden) control.focus();
    }
    function showHistory() {
      history.replaceChildren();
      if (!transcript.length) return;
      const details = node('details');
      details.append(node('summary', 'Clarifications (' + transcript.length + ')'));
      transcript.forEach(pair => details.append(node('p', pair.question), node('p', pair.answer)));
      history.append(details);
    }
    function renderReview() {
      const content = element('review-body');
      content.replaceChildren(node('h3', domainName(draft.domain)), node('p', draft.summary));
      if (draft.assumptions.length) {
        const list = node('ul');
        list.className = 'review-assumptions';
        draft.assumptions.forEach(value => list.append(node('li', value)));
        content.append(node('h3', 'Assumptions to check'), list);
      }
      content.append(showInputs(draft.domain, draft.inputs));
    }
    async function submit(event) {
      event.preventDefault();
      if (pending) return;
      element('expired-scenario').hidden = true;
      const original = description.value.trim();
      if (!original) { focusIfVisible(description); return; }
      const reply = answer.value.trim();
      if (question && !reply) { focusIfVisible(answer); return; }
      const clarifications = question ? [...transcript, {question, answer: reply}] : transcript;
      const controller = new AbortController();
      pending = controller;
      renderControls();
      message('Preparing your scenario…');
      try {
        const response = await fetch('/api/problems/formulate', {
          method: 'POST', headers: {'Content-Type': 'application/json'},
          body: JSON.stringify({description: original, clarifications}), signal: controller.signal,
        });
        const result = await response.json();
        if (pending !== controller) return;
        if (!response.ok) throw new Error(result.error || 'Drafting is unavailable. Try again.');
        if (result.status === 'ready') {
          if (!domainName(result.domain) || !result.inputs || !Array.isArray(result.assumptions)) throw new Error('The provider returned an incomplete draft. Try again.');
          draft = result;
          renderReview();
          message('Draft ready. Check the inputs before using this scenario.');
        } else if (result.status === 'clarify' && result.question) {
          element('clarification-question').textContent = result.question;
          message('One detail is needed to prepare your scenario.');
        } else if (result.status === 'unsupported') {
          message(result.message, true);
        } else throw new Error('The provider returned an incomplete response. Try again.');
        transcript = clarifications;
        question = result.status === 'clarify' ? result.question : '';
        answer.value = '';
        showHistory();
      } catch (error) {
        if (pending === controller) message(error.message || 'Could not reach the drafting service. Try again.', true);
      } finally {
        if (pending === controller) {
          pending = null;
          renderControls();
          if (question) focusIfVisible(answer);
          if (draft) focusIfVisible(element('review-heading'));
        }
      }
    }

    cancel.onclick = () => {
      pending?.abort();
      pending = null;
      message('Draft request canceled.');
      renderControls();
      focusIfVisible(question ? answer : draftButton);
    };
    editButton.onclick = () => {
      // Fold accepted answers into the editable description so corrections keep the user's facts.
      const combined = [description.value, ...transcript.map(pair => pair.question + '\n' + pair.answer)].join('\n\n');
      if (combined.length <= description.maxLength) {
        description.value = combined;
        transcript = [];
      }
      draft = null;
      question = '';
      answer.value = '';
      showHistory();
      message('');
      renderControls();
      description.readOnly = false;
      focusIfVisible(description);
    };
    newButton.onclick = () => {
      description.value = '';
      answer.value = '';
      question = '';
      draft = null;
      transcript = [];
      showHistory();
      message('');
      renderControls();
      focusIfVisible(description);
    };
    example.onclick = () => {
      description.value = 'I have a USD 50,000 budget. A lab upgrade costs 30,000 and earns 60 points, solar panels cost 25,000 and earn 50, and a garden costs 15,000 and earns 25. Maximize total points. None of these projects are required or depend on another.';
      renderControls();
      focusIfVisible(description);
    };
    description.addEventListener('input', () => { message(''); renderControls(); });
    element('description-form').addEventListener('submit', submit);
    clarifyForm.addEventListener('submit', submit);
    element('open-draft').onclick = () => { if (draft) accept(draft, description.value.trim(), transcript.slice()); };
    renderControls();
    return {show() {
      if (checkedConfig) return;
      checkedConfig = true;
      fetch('/api/config').then(r => r.ok && r.json()).then(config => {
        if (config && config.formulation_configured === false && !pending && !draft && !question && !status.textContent)
          message('AI drafting needs a configured model provider. You can use any problem editor directly.');
      }).catch(() => {});
    }};
  }
  return {create};
})();
