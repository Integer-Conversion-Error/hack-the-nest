const assert = require('node:assert/strict');
const test = require('node:test');
const { isValidValue, render } = require('../web/scenario-controls.js');

class Element {
  constructor(tagName) {
    this.tagName = tagName;
    this.children = [];
  }
  append(...elements) { this.children.push(...elements); }
  replaceChildren(...elements) { this.children = elements; }
  setAttribute(key, value) { this[key] = value; }
}
function fixture() {
  const pane = new Element('div');
  pane.ownerDocument = { createElement: tagName => new Element(tagName) };
  const find = (id, node = pane) => {
    if (node.id === id) return node;
    for (const child of node.children) {
      const found = find(id, child);
      if (found) return found;
    }
    return undefined;
  };
  return { pane, find };
}

const charge = {
  id: 'van_2_initial_charge_pct',
  label: "Van 2 starting charge",
  allowed_values: [35, 20],
  unit: '%',
};
const tariff = {
  id: 'tariff',
  label: 'Electricity',
  type: 'number',
  minimum: 0.05,
  maximum: 1.05,
  step: 0.1,
  unit: '$/kWh',
  group: 'Costs',
};
const waiting = { id: 'waiting', label: 'Allow waiting', type: 'boolean' };
const route = {
  id: 'route',
  label: 'Route',
  type: 'string',
  allowed_values: ['A', 'B', 'C', 'D', 'E'],
};

test('charge choices use a slider that previews approved percentages and commits a number', () => {
  const { pane, find } = fixture();
  let changes = 0;
  const controls = render(pane, [charge], { [charge.id]: 35 }, { onChange: () => changes++ });
  const slider = find('variable-' + charge.id + '-slider');
  assert.equal(slider.type, 'range');
  assert.deepEqual([slider.min, slider.max, slider.step, slider.value], ['0', '1', '1', '1']);
  assert.equal(slider['aria-valuetext'], '35%');
  assert.equal(controls.automatic, true);
  assert.equal(controls.displayApplied(), '35%');

  slider.value = '0';
  slider.oninput();
  assert.equal(slider['aria-valuetext'], '20%');
  assert.deepEqual(controls.values(), { [charge.id]: 20 });
  assert.equal(changes, 0);
  slider.onchange();
  assert.equal(changes, 1);
  controls.setDisabled(true);
  assert.equal(slider.disabled, true);
});

test('nonuniform numeric choices snap to approved values in ascending order', () => {
  const { pane, find } = fixture();
  const allowed_values = [1.05, 0, 0.2, 0.35, 3.2];
  const pricedChoices = { id: 'tariff', label: 'Electricity', type: 'number', unit: '$/kWh', allowed_values };
  const controls = render(pane, [pricedChoices], { tariff: 0.2 });
  const slider = find('variable-tariff-slider');
  assert.equal(slider.type, 'range');
  assert.deepEqual([slider.min, slider.max, slider.step, slider.value], ['0', '4', '1', '1']);
  assert.deepEqual(allowed_values, [1.05, 0, 0.2, 0.35, 3.2], 'rendering leaves the planner list intact');

  const selected = allowed_values.map((_, index) => {
    slider.value = String(index);
    slider.oninput();
    return controls.values().tariff;
  });
  assert.deepEqual(selected, [0, 0.2, 0.35, 1.05, 3.2]);
  assert.equal(slider['aria-valuetext'], '3.2 $/kWh');
});

test('range, toggle, and menu use approved values and keep groups initially collapsed', () => {
  const { pane, find } = fixture();
  let changes = 0;
  const definitions = [tariff, waiting, route];
  const current = { tariff: 0.25, waiting: true, route: 'A' };
  const controls = render(pane, definitions, current, { onChange: () => changes++ });
  const disclosure = pane.children[0];
  assert.equal(disclosure.tagName, 'details');
  assert.equal(disclosure.children[0].textContent, 'Costs');
  assert.equal(disclosure.open, false);
  assert.equal(controls.automatic, false);
  const numeric = find('variable-tariff');
  const slider = find('variable-tariff-slider');
  assert.equal(numeric.type, 'number');
  assert.equal(slider.type, 'range');

  slider.value = '0.35';
  slider.oninput();
  assert.equal(numeric.value, '0.35');
  assert.equal(changes, 0);
  slider.onchange();
  assert.equal(changes, 1);

  const toggle = find('variable-waiting');
  assert.equal(toggle.type, 'checkbox');
  toggle.checked = false;
  toggle.onchange();
  const menu = find('variable-route');
  assert.equal(menu.tagName, 'select');
  menu.value = 'E';
  menu.onchange();
  assert.deepEqual(controls.values(), { tariff: 0.35, waiting: false, route: 'E' });

  numeric.value = '0.3';
  numeric.oninput();
  assert.equal(controls.values(), null);
  assert.equal(slider.value, '0.35');
  assert.equal(numeric['aria-invalid'], 'true');
  assert.match(find('variable-tariff-bounds').textContent, /in steps of 0.1/);

  disclosure.open = true;
  render(pane, definitions, current);
  assert.equal(pane.children[0].open, true);
});

test('validates decimal step offsets and rejects bool-number aliases', () => {
  assert.equal(isValidValue(tariff, 0.15), true);
  assert.equal(isValidValue(tariff, 0.35000000000000003), false);
  assert.equal(isValidValue(tariff, 0.3), false);
  assert.equal(isValidValue(tariff, Infinity), false);
  assert.equal(isValidValue(charge, '20'), false);
  assert.equal(isValidValue({ type: 'integer', allowed_values: [1, 2] }, true), false);
  assert.equal(isValidValue(waiting, false), true);
  assert.equal(isValidValue(waiting, 0), false);
});
