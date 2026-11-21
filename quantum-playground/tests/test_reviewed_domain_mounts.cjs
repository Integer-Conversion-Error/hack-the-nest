const assert = require('node:assert/strict');
const test = require('node:test');
const modules = Object.fromEntries(['facility', 'workforce', 'production', 'timetable', 'energy', 'projects', 'teams']
  .map(id => [id, require('../web/problems/' + id + '.js')]));

class Element {
  constructor(tag) {
    this.tagName = tag; this.children = []; this.attrs = {}; this.dataset = {}; this.listeners = {};
    this.className = ''; this.style = { setProperty(name, value) { this[name] = value; } };
    this.classList = {
      add: (...names) => { this.className = [...new Set([...this.className.split(/\s+/).filter(Boolean), ...names])].join(' '); },
      remove: (...names) => { this.className = this.className.split(/\s+/).filter(name => !names.includes(name)).join(' '); },
      toggle: (name, value) => { if (value ?? !this.className.split(/\s+/).includes(name)) this.classList.add(name); else this.classList.remove(name); },
    };
    this.value = ''; this._text = '';
  }
  set textContent(value) { this._text = String(value); this.children = []; }
  get textContent() { return this._text + this.children.map(child => child.textContent).join(''); }
  append(...children) { children.forEach(child => { child.parent = this; this.children.push(child); }); }
  replaceChildren(...children) { this.children = []; this._text = ''; this.append(...children); }
  setAttribute(key, value) { this.attrs[key] = String(value); }
  addEventListener(name, callback) { (this.listeners[name] ||= new Set()).add(callback); }
  removeEventListener(name, callback) { this.listeners[name]?.delete(callback); }
  get options() { return this.children.filter(child => child.tagName === 'option'); }
  querySelectorAll(selector) {
    return all(this).slice(1).filter(node => selector === 'select[data-pair-person]' ? node.tagName === 'select' && node.dataset.pairPerson !== undefined
      : selector === 'select' ? node.tagName === 'select' : selector === 'input[type=range]' ? node.tagName === 'input' && node.type === 'range' : false);
  }
  querySelector(selector) { return this.querySelectorAll(selector)[0] || null; }
  focus() {}
}
function all(node) { return [node, ...node.children.flatMap(all)]; }
function fixture(id, inputs, panelId) {
  const document = { createElement: tag => new Element(tag), createElementNS: (_ns, tag) => new Element(tag),
    createTextNode: text => { const node = new Element('#text'); node.textContent = text; return node; } };
  global.document = document;
  global.matchMedia = () => ({ matches: false, addEventListener() {}, removeEventListener() {} });
  const root = new Element('div'); root.ownerDocument = document; if (panelId) root.id = panelId;
  const cleanup = global.OptimizationViews[id].mount(root, inputs);
  const find = predicate => { const found = all(root).find(predicate); assert.ok(found, 'Expected rendered element'); return found; };
  return { root, cleanup, nodes: () => all(root), text: () => root.textContent,
    input: label => find(node => node.attrs['aria-label'] === label),
    button: label => find(node => node.tagName === 'button' && node.textContent === label),
    change(label, value, type = 'input') {
      const node = find(node => node.attrs['aria-label'] === label); node.value = String(value);
      node['on' + type]?.({ target: node });
      for (let parent = node; parent; parent = parent.parent) for (const callback of parent.listeners[type] || []) callback({ target: node });
    },
  };
}

test('reviewed projects replace the example, keep custom IDs and never mutate the reviewed draft', () => {
  const source = { budget: 8, projects: [{ id: 'P42', name: 'Research grant', cost: 7, value: 12, required: true, requires: '' }] };
  const before = JSON.stringify(source), ui = fixture('projects', source);
  assert.match(ui.text(), /Research grant/); assert.doesNotMatch(ui.text(), /Solar canopy/);
  assert.equal(Number(ui.input('Project budget').value), 8);
  ui.change('P42 cost', 9); assert.match(ui.text(), /prerequisites exceed the budget/);
  assert.equal(JSON.stringify(source), before);
  ui.button('Reset example').onclick(); assert.match(ui.text(), /Solar canopy/); assert.doesNotMatch(ui.text(), /Research grant/); ui.cleanup();
});

test('reviewed two-person teams retain pair scores and reset to the six-person example', () => {
  const source = { people: [{ id: 'U41', name: 'Nia', skills: [3, 3, 3] }, { id: 'U42', name: 'Omar', skills: [3, 3, 3] }],
    compatibility: { 'U41|U42': 2 }, team_count: 2, minimum_skill: 1 };
  const ui = fixture('teams', source);
  assert.match(ui.text(), /Nia/); assert.doesNotMatch(ui.text(), /Ava/);
  assert.equal(ui.nodes().filter(node => node.className === 'teams-person').length, 2);
  assert.equal(Number(ui.input('Pair compatibility rating').value), 2);
  ui.button('Reset example').onclick(); assert.equal(ui.nodes().filter(node => node.className === 'teams-person').length, 6); ui.cleanup();
});

test('reviewed workforce applies sixteen-hour limits and varying worker counts immediately', () => {
  const source = { maxHours: 16, coverage: [1, 0, 1], workers: [{ id: 'W77', name: 'Night nurse', rate: 10, available: [true, false, true] }] };
  const ui = fixture('workforce', source);
  assert.equal(Number(ui.input('Maximum daily hours per employee').value), 16);
  assert.match(ui.text(), /All required shifts covered/); assert.match(ui.text(), /\$160/); assert.match(ui.text(), /Night nurse/); assert.doesNotMatch(ui.text(), /Amara/);
  ui.button('Reset example').onclick(); assert.match(ui.text(), /Amara/); assert.equal(Number(ui.input('Maximum daily hours per employee').value), 8); ui.cleanup();
});

test('reviewed production supports one job and one operation without resurrecting example jobs', () => {
  const source = { horizon: 8, jobs: [{ id: 'J41', name: 'Kiosk', release: 2, operations: [{ machine: 'M2', duration: 3 }] }] };
  const ui = fixture('production', source);
  assert.match(ui.text(), /Kiosk/); assert.doesNotMatch(ui.text(), /Frame/); assert.match(ui.text(), /5 h/);
  assert.equal(Number(ui.input('Kiosk operation 1 hours').value), 3);
  assert.equal(ui.nodes().some(node => node.attrs['aria-label'] === 'Kiosk operation 2 hours'), false);
  ui.button('Reset example').onclick(); assert.match(ui.text(), /Frame/); ui.cleanup();
});

test('reviewed timetable retains custom teacher/cohort names, closed rooms, and one available period', () => {
  const source = { slotCount: 1, rooms: [{ id: 'R22', name: 'Vault', capacity: 20, open: false }, { id: 'R23', name: 'Studio', capacity: 20, open: true }],
    courses: [{ id: 'C44', name: 'Studio session', teacher: 'Coach Sky', cohort: 'Evening group', students: 12, preferred: 0, available: [true, true, true] }] };
  const ui = fixture('timetable', source);
  assert.equal(Number(ui.input('Number of available class periods').value), 1);
  assert.equal(ui.input('Vault availability').value, 'false');
  assert.equal(ui.input('Studio session teacher').value, 'Coach Sky'); assert.equal(ui.input('Studio session cohort').value, 'Evening group');
  assert.match(ui.text(), /All 1 classes scheduled/); assert.match(ui.text(), /Coach Sky/); assert.doesNotMatch(ui.text(), /Algorithms/);
  ui.button('Reset example').onclick(); assert.match(ui.text(), /Algorithms/); ui.cleanup();
});

test('reviewed energy uses the imported interval count and labels with an honest end boundary', () => {
  const source = { ...modules.energy.seed(), intervals: [{ label: 'Morning', load: 2, solar: 0, price: 0.1 }, { label: 'Evening', load: 3, solar: 1, price: 0.2 }] };
  const ui = fixture('energy', source);
  assert.equal(Number(ui.input('Morning demand kWh').value), 2); assert.equal(Number(ui.input('Evening solar kWh').value), 1);
  assert.match(ui.text(), /End/); assert.doesNotMatch(ui.text(), /00–03/);
  ui.button('Reset example').onclick(); assert.equal(Number(ui.input('00–03 demand kWh').value), 5); ui.cleanup();
});

test('facility imports ignore untrusted road matrices and only attach a matching recorded snapshot', () => {
  const source = modules.facility.seed(); source.durations = source.durations.map(row => row.map(() => 0)); source.distances = []; source.routing = { forged: true };
  const prepared = modules.facility.prepareInputs(source);
  assert.deepEqual(prepared.distances, modules.facility.SNAPSHOT.distances_m);
  assert.deepEqual(prepared.durations, modules.facility.SNAPSHOT.durations_s); assert.equal(Object.hasOwn(prepared, 'routing'), false);
  source.sites[0].position[0] += 0.001;
  assert.equal(modules.facility.prepareInputs(source).durations, null);
  assert.equal(modules.facility.prepareInputs(source).distances, null);
  const ui = fixture('facility', source);
  assert.match(ui.text(), /Recalculate road times to solve these locations/);
  assert.equal(ui.nodes().filter(node => node.className === 'problem-card' && node.children.some(child => child.textContent === 'Daily visits covered')).length, 0);
  ui.button('Reset Toronto example').onclick(); assert.match(ui.text(), /Exact classical search/); ui.cleanup();
});

test('full coverage is distinct from maximizing partial coverage in both the solver and the view', () => {
  const source = modules.facility.seed(); source.budget = 0;
  const partial = modules.facility.solve(source); assert.equal(partial.status, 'ok'); assert.equal(partial.covered, 0);
  source.requireFullCoverage = true;
  const required = modules.facility.solve(source); assert.equal(required.status, 'infeasible'); assert.equal(required.covered, 0); assert.equal(required.totalDemand, 44);
  const ui = fixture('facility', source);
  assert.match(ui.text(), /Full coverage is infeasible/); assert.match(ui.text(), /Best achievable visits covered/);
  assert.match(ui.text(), /Diagnostic partial plan; the full-coverage requirement is not satisfied/);
  ui.input('Require full demand coverage').checked = false; ui.input('Require full demand coverage').onchange();
  assert.doesNotMatch(ui.text(), /Full coverage is infeasible/); assert.match(ui.text(), /44 visits\/day remain unserved/); ui.cleanup();
});

test('approved child editors namespace their HTML IDs while the manual selectors stay stable', () => {
  for (const [id, control, expected] of [
    ['projects', null, 'projects-add'], ['teams', 'Pair compatibility rating', 'teams-pair-rating'],
    ['facility', null, 'facility-recalculate'],
  ]) {
    const inputs = modules[id].seed(), manual = fixture(id, inputs);
    assert.ok(manual.nodes().some(node => node.id === expected)); manual.cleanup();
    const reviewed = fixture(id, inputs, 'scenario-7-editor');
    assert.ok(reviewed.nodes().some(node => node.id === 'scenario-7-editor-' + expected));
    assert.equal(reviewed.nodes().some(node => node.id === expected), false);
    if (control) assert.equal(reviewed.nodes().find(node => node.tagName === 'output').htmlFor, reviewed.input(control).id);
    reviewed.cleanup();
  }
});
