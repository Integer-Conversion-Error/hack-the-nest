/* Editable street-grid scenarios. Verified replay remains owned by app.js. */
(function (root) {
  'use strict';
  const D = root.ScenarioDraft;
  const SVG = 'http://www.w3.org/2000/svg';
  const names = { vehicles: 'Vehicles', depots: 'Depots', chargers: 'Chargers', deliveries: 'Deliveries', settings: 'Settings' };
  const colors = ['#185f55', '#315b8f', '#8a4d27', '#75527c', '#a42b46', '#6c650d'];

  function create({ onSubmit = () => {}, onModeChange = () => {} } = {}) {
    const document = root.document;
    const host = document.getElementById('scenario-builder');
    let storage;
    try { storage = root.localStorage; } catch { /* Private browsing may disable storage. */ }
    let draft = D.load(storage), source = null, configured = false, busy = false;
    let selected = { kind: 'vehicles', id: draft.vehicles[0]?.id }, moveMode = false;
    let mode = 'builder', serverError = '', saved = true, showErrors = false, overlap = null;
    const element = (tag, className, text) => {
      const node = document.createElement(tag);
      if (className) node.className = className;
      if (text !== undefined) node.textContent = text;
      return node;
    };
    const button = (label, action, className = 'secondary') => {
      const node = element('button', className, label); node.type = 'button'; node.onclick = action; return node;
    };
    const svgElement = (tag, attributes = {}) => {
      const node = document.createElementNS(SVG, tag);
      for (const [key, value] of Object.entries(attributes)) node.setAttribute(key, value);
      return node;
    };
    const toolbar = element('div', 'builder-toolbar');
    const titleLabel = element('label', 'builder-title-label', 'Scenario name');
    const title = element('input', 'builder-title'); title.id = 'scenario-title'; title.maxLength = 120;
    titleLabel.htmlFor = title.id; titleLabel.append(title); title.value = draft.title;
    title.oninput = () => { draft.title = title.value; changed(false); };
    const reset = button('Reset to example', () => {
      if (!root.confirm('Replace your draft with the example scenario?')) return;
      draft = D.seed(); source = null; selected = { kind: 'vehicles', id: 'V1' };
      title.value = draft.title; moveMode = false; showErrors = false; changed();
    }, 'text-button');
    toolbar.append(titleLabel, reset);
    const intro = element('p', 'builder-intro', 'Place your depots, chargers, and deliveries. Assign stops to vehicles; the solver chooses their visit order.');
    const layout = element('div', 'builder-layout');
    const mapPanel = element('section', 'panel builder-map-panel'); mapPanel.setAttribute('aria-label', 'Scenario map');
    const mapHeader = element('div', 'builder-map-heading');
    const mapSummary = element('p', 'builder-map-summary');
    const mapInstruction = element('p', 'hint builder-map-help', 'Drag a marker to an intersection, or select it and edit its coordinates. Arrow keys move a focused marker.');
    mapInstruction.id = 'scenario-map-help';
    const move = button('Move selected', () => { moveMode = !moveMode; renderMap(); renderStatus(); });
    move.id = 'scenario-move'; mapHeader.append(mapSummary, move);
    const mapHost = element('div', 'builder-map'); mapHost.id = 'scenario-map';
    const overlapHost = element('div', 'builder-overlap'); overlapHost.hidden = true;
    const legend = element('div', 'builder-legend');
    for (const [symbol, text] of [['■', 'Depot'], ['ϟ', 'Charger'], ['●', 'Delivery']]) {
      legend.append(element('span', null, symbol + ' ' + text));
    }
    mapPanel.append(mapHeader, mapHost, overlapHost, legend, mapInstruction);
    const inspector = element('section', 'panel builder-inspector'); inspector.setAttribute('aria-label', 'Scenario inspector');
    layout.append(mapPanel, inspector);
    const footer = element('div', 'builder-footer');
    const run = button('Run scenario', submit, 'primary'); run.id = 'scenario-run';
    const status = element('p', 'hint builder-save-status'); status.setAttribute('role', 'status');
    const error = element('div', 'request-error builder-errors'); error.id = 'scenario-error'; error.setAttribute('role', 'alert'); error.hidden = true;
    footer.append(run, status);
    host.replaceChildren(intro, toolbar, layout, error, footer);

    function selectedItem() { return draft[selected.kind]?.find(item => item.id === selected.id); }
    function select(kind, id) {
      selected = { kind, id }; moveMode = false; overlap = null; render();
    }
    function changed(full = true) {
      saved = D.save(storage, draft); serverError = '';
      if (full) render(); else { renderMap(); renderStatus(); }
    }
    function renderStatus() {
      const errors = D.validate(draft);
      run.disabled = busy || !configured;
      const eligible = !errors.length && D.reuse(source, D.normalize(draft), root.ScenarioControls?.isValidValue || (() => false));
      run.textContent = busy ? 'Solving scenario…' : eligible ? 'Recalculate settings' : source ? 'Plan new scenario' : 'Run scenario';
      reset.disabled = busy; title.disabled = busy;
      status.textContent = busy ? 'Your draft is saved while the solver runs.' : !configured
        ? 'Configure a planning backend to solve a custom scenario. You can keep editing.'
        : saved ? 'Draft saved in this browser.' : 'Draft stays open here; browser storage is unavailable.';
      error.replaceChildren();
      const messages = serverError ? [serverError] : showErrors ? errors : [];
      error.hidden = !messages.length;
      if (messages.length) {
        const list = element('ul');
        for (const message of messages) list.append(element('li', null, message));
        error.append(list);
      }
      move.disabled = busy || !selectedItem()?.position;
      move.textContent = moveMode ? 'Cancel move' : 'Move selected';
      move.setAttribute('aria-pressed', String(moveMode));
      mapInstruction.textContent = moveMode ? 'Tap an intersection to place ' + selected.id + '. Escape cancels.'
        : 'Drag a marker to an intersection, or select it and edit its coordinates. Arrow keys move a focused marker.';
    }
    function field(container, label, object, key, { min, max, step = 'any', suffix, onChange } = {}) {
      const row = element('div', 'builder-field');
      const id = 'scenario-' + selected.kind + '-' + (selected.id || 'all') + '-' + key;
      const caption = element('label', null, label); caption.htmlFor = id;
      const input = element('input'); input.id = id; input.type = 'number'; input.value = object[key] ?? '';
      input.step = String(step); if (min !== undefined) input.min = String(min); if (max !== undefined) input.max = String(max);
      input.disabled = busy;
      input.oninput = () => { object[key] = input.value; changed(false); };
      if (onChange) input.onchange = onChange;
      row.append(caption, input);
      if (suffix) row.append(element('span', 'hint', suffix));
      container.append(row); return input;
    }
    function dropdown(container, label, value, choices, change) {
      const row = element('div', 'builder-field');
      const input = element('select'); input.id = 'scenario-' + selected.kind + '-' + (selected.id || 'all') + '-' + label.toLowerCase().replaceAll(' ', '-');
      const caption = element('label', null, label); caption.htmlFor = input.id;
      for (const [id, text] of choices) { const option = element('option', null, text); option.value = id; input.append(option); }
      input.value = value; input.disabled = busy;
      input.onchange = () => { change(input.value); changed(); };
      row.append(caption, input); container.append(row);
    }
    function section(container, label, fields, object) {
      const group = element('fieldset', 'builder-fieldset'); group.append(element('legend', null, label));
      for (const [key, caption, options] of fields) field(group, caption, object, key, options || { min: 0 });
      container.append(group);
    }
    function renderInspector() {
      inspector.replaceChildren();
      const tabs = element('div', 'builder-categories'); tabs.setAttribute('role', 'group'); tabs.setAttribute('aria-label', 'Edit scenario objects');
      for (const [kind, label] of Object.entries(names)) {
        const tab = button(label + (draft[kind] ? ' ' + draft[kind].length : ''), () => select(kind, draft[kind]?.[0]?.id), 'builder-category');
        tab.setAttribute('aria-pressed', String(selected.kind === kind)); tabs.append(tab);
      }
      inspector.append(tabs);
      if (selected.kind === 'settings') { renderSettings(); return; }
      const rows = draft[selected.kind], list = element('div', 'builder-object-list'); list.setAttribute('aria-label', names[selected.kind]);
      for (const item of rows) {
        const count = draft.deliveries.filter(stop => stop.vehicle_id === item.id).length;
        const summary = selected.kind === 'vehicles' ? item.id + ' · ' + count + (count === 1 ? ' stop' : ' stops')
          : selected.kind === 'deliveries' ? item.id + ' · ' + (item.vehicle_id || 'Unassigned') : item.id;
        const entry = button(summary, () => select(selected.kind, item.id), 'builder-object');
        entry.setAttribute('aria-pressed', String(selected.id === item.id)); list.append(entry);
      }
      const add = button('+ Add ' + names[selected.kind].toLowerCase().replace(/ies$/, 'y').replace(/s$/, ''), addItem, 'text-button');
      add.id = 'scenario-add-' + selected.kind; add.disabled = busy || rows.length >= D.limits[selected.kind];
      list.append(add); inspector.append(list);
      const item = selectedItem();
      if (!item) { inspector.append(element('p', 'hint builder-empty', 'Add an object to edit its details.')); return; }
      const body = element('div', 'builder-object-fields');
      const heading = element('div', 'builder-object-heading'); heading.append(element('h2', null, item.id));
      const remove = button('Remove', removeItem, 'text-button builder-remove');
      const depotUsed = selected.kind === 'depots' && draft.vehicles.some(vehicle => vehicle.depot_id === item.id);
      const lastRequired = ['depots', 'vehicles'].includes(selected.kind) && rows.length === 1;
      remove.disabled = busy || depotUsed || lastRequired; heading.append(remove); body.append(heading);
      if (depotUsed) body.append(element('p', 'hint', 'Reassign this depot’s vehicles before removing it.'));
      if (lastRequired) body.append(element('p', 'hint', 'Keep at least one ' + (selected.kind === 'depots' ? 'depot' : 'vehicle') + '.'));
      if (item.position) {
        const coordinates = element('div', 'builder-coordinates');
        field(coordinates, 'X', item.position, 0, { min: 0, max: Number(draft.grid.width) - 1, step: 1 });
        field(coordinates, 'Y', item.position, 1, { min: 0, max: Number(draft.grid.height) - 1, step: 1 });
        body.append(coordinates);
      }
      if (selected.kind === 'vehicles') {
        dropdown(body, 'Starting depot', item.depot_id, draft.depots.map(depot => [depot.id, depot.id]), value => { item.depot_id = value; });
        field(body, 'Starting charge (%)', item, 'initial_charge_pct', { min: 0, max: 100 });
        body.append(element('h3', 'builder-stops-heading', 'Assigned deliveries'));
        body.append(element('p', 'hint', 'The solver chooses visit order. Up to 10 stops per vehicle.'));
        const stops = draft.deliveries.filter(stop => stop.vehicle_id === item.id);
        for (const stop of stops) body.append(button(stop.id + ' · (' + stop.position.join(', ') + ')', () => select('deliveries', stop.id), 'builder-stop'));
        if (!stops.length) body.append(element('p', 'hint', 'No deliveries assigned yet.'));
        const addStop = button('+ Add delivery for ' + item.id, () => addItem('deliveries', item.id), 'text-button');
        addStop.disabled = busy || stops.length >= D.limits.stops || draft.deliveries.length >= D.limits.deliveries; body.append(addStop);
      } else if (selected.kind === 'deliveries') {
        dropdown(body, 'Assigned vehicle', item.vehicle_id || '', [['', 'Choose a vehicle'], ...draft.vehicles.map(vehicle => [vehicle.id, vehicle.id])], value => { item.vehicle_id = value; });
        field(body, 'Due time (min from shift start)', item, 'deadline_min', { min: 0 });
        field(body, 'Service time (min)', item, 'service_min', { min: 0 });
      } else if (selected.kind === 'chargers') {
        field(body, 'Power (kW)', item, 'power_kw', { min: 0.001 });
        body.append(element('h3', 'builder-stops-heading', 'Charging slots'));
        body.append(element('p', 'hint', 'Minutes from shift start. One vehicle per slot. No slots means this charger is unavailable.'));
        for (const slot of item.slots) {
          const group = element('fieldset', 'builder-slot'); group.append(element('legend', null, slot.id));
          const row = element('div', 'builder-coordinates');
          const start = field(row, 'Start (min)', slot, 'start_min', { min: 0 }); start.id += '-' + slot.id;
          row.children[0].querySelector('label').htmlFor = start.id;
          const end = field(row, 'End (min)', slot, 'end_min', { min: 0 }); end.id += '-' + slot.id;
          row.children[1].querySelector('label').htmlFor = end.id;
          const removeSlot = button('Remove ' + slot.id, () => { item.slots = item.slots.filter(row => row !== slot); changed(); }, 'text-button'); removeSlot.disabled = busy;
          group.append(row, removeSlot); body.append(group);
        }
        const addSlot = button('+ Add slot', () => {
          const start = item.slots.length ? Number(item.slots.at(-1).end_min) : 30;
          item.slots.push({ id: D.nextId(item.slots, 'S'), start_min: start, end_min: start + 30 }); changed();
        }, 'text-button'); addSlot.disabled = busy || item.slots.length >= D.limits.slots; body.append(addSlot);
      }
      inspector.append(body);
    }
    function renderSettings() {
      const body = element('div', 'builder-object-fields builder-settings');
      const resize = (axis, previous) => event => {
        const size = Number(event.target.value);
        if (!Number.isInteger(size) || size < 2 || size > 50) return;
        const index = axis === 'width' ? 0 : 1;
        const stranded = ['depots', 'chargers', 'deliveries'].flatMap(kind => draft[kind])
          .filter(item => Number(item.position[index]) >= size).map(item => item.id);
        if (stranded.length) {
          draft.grid[axis] = previous;
          changed();
          serverError = 'Move ' + stranded.join(', ') + ' inside the smaller grid before changing its ' + axis + '.';
          renderStatus(); error.tabIndex = -1; error.focus();
        } else changed();
      };
      section(body, 'Street grid', [['width', 'Width (intersections)', { min: 2, max: 50, step: 1, onChange: resize('width', draft.grid.width) }], ['height', 'Height (intersections)', { min: 2, max: 50, step: 1, onChange: resize('height', draft.grid.height) }], ['block_km', 'Distance per block (km)', { min: 0.001 }]], draft.grid);
      section(body, 'Fleet limits', [['battery_capacity_kwh', 'Battery capacity (kWh)', { min: 0.001 }], ['reserve_pct', 'Minimum reserve (%)', { min: 0, max: 100 }], ['energy_use_kwh_per_km', 'Energy use (kWh/km)', { min: 0.001 }]], draft.fleet);
      section(body, 'Shift and timing', [['start_hour', 'Start hour (24-hour clock)', { min: 0, max: 23.99 }], ['duration_min', 'Shift length (min)', { min: 1, max: 1440 }], ['speed_kmh', 'Driving speed (km/h)', { min: 0.001 }]], draft.timing);
      section(body, 'Costs in USD', [['driving_per_km', 'Driving ($/km)'], ['electricity_per_kwh', 'Electricity ($/kWh)'], ['waiting_per_hour', 'Waiting ($/hour)'], ['late_per_hour', 'Lateness ($/hour)']], draft.costs);
      inspector.append(body);
    }
    function addItem(kind = selected.kind, assignedVehicle) {
      // onclick passes its event; treat that as the active collection.
      if (typeof kind !== 'string') kind = selected.kind;
      if (busy || draft[kind].length >= D.limits[kind]) return;
      const prefixes = { vehicles: 'V', depots: 'P', chargers: 'C', deliveries: 'D' };
      const item = { id: D.nextId(draft[kind], prefixes[kind]) };
      if (kind === 'vehicles') Object.assign(item, { depot_id: draft.depots[0]?.id || '', initial_charge_pct: 50 });
      else item.position = [Math.max(0, Math.floor((Number(draft.grid.width) || 6) / 2)), Math.max(0, Math.floor((Number(draft.grid.height) || 6) / 2))];
      if (kind === 'deliveries') Object.assign(item, { vehicle_id: assignedVehicle || draft.vehicles.find(vehicle => draft.deliveries.filter(stop => stop.vehicle_id === vehicle.id).length < 10)?.id || '', deadline_min: 90, service_min: 5 });
      if (kind === 'chargers') Object.assign(item, { power_kw: 10, slots: [{ id: 'S1', start_min: 30, end_min: 60 }] });
      draft[kind].push(item); selected = { kind, id: item.id }; changed();
    }
    function removeItem() {
      if (busy) return;
      const item = selectedItem(); if (!item) return;
      if (selected.kind === 'depots' && (draft.depots.length === 1 || draft.vehicles.some(vehicle => vehicle.depot_id === item.id))) return;
      if (selected.kind === 'vehicles') {
        if (draft.vehicles.length === 1) return;
        for (const delivery of draft.deliveries) if (delivery.vehicle_id === item.id) delivery.vehicle_id = '';
      }
      draft[selected.kind] = draft[selected.kind].filter(row => row !== item);
      selected.id = draft[selected.kind][0]?.id; changed();
    }
    function renderMap() {
      const width = Math.min(50, Math.max(2, Number(draft.grid.width) || 6));
      const height = Math.min(50, Math.max(2, Number(draft.grid.height) || 6));
      const size = 520, pad = 46, cell = Math.min((size - pad * 2) / (width - 1), (size - pad * 2) / (height - 1));
      const markerScale = Math.max(1, size / (mapHost.clientWidth || size));
      const left = (size - (width - 1) * cell) / 2, top = (size - (height - 1) * cell) / 2;
      const point = position => [left + Number(position[0]) * cell, top + (height - 1 - Number(position[1])) * cell];
      const svg = svgElement('svg', { viewBox: '0 0 520 520', role: 'group', 'aria-label': 'Editable scenario grid', 'aria-describedby': mapInstruction.id });
      const background = svgElement('rect', { x: 0, y: 0, width: size, height: size, fill: '#f4f5f3' }); svg.append(background);
      for (let x = 0; x < width; x++) {
        svg.append(svgElement('line', { x1: point([x, 0])[0], x2: point([x, 0])[0], y1: top, y2: size - top, class: 'builder-street' }));
        if (x % Math.ceil(width / 12) === 0 || x === width - 1) {
          const label = svgElement('text', { x: point([x, 0])[0], y: size - top + 24 * markerScale, class: 'builder-axis', style: 'font-size:' + 11 * markerScale + 'px' }); label.textContent = x; svg.append(label);
        }
      }
      for (let y = 0; y < height; y++) {
        svg.append(svgElement('line', { y1: point([0, y])[1], y2: point([0, y])[1], x1: left, x2: size - left, class: 'builder-street' }));
        if (y % Math.ceil(height / 12) === 0 || y === height - 1) {
          const label = svgElement('text', { x: left - 23 * markerScale, y: point([0, y])[1] + 4 * markerScale, class: 'builder-axis', style: 'font-size:' + 11 * markerScale + 'px' }); label.textContent = y; svg.append(label);
        }
      }
      const groups = new Map();
      for (const kind of ['depots', 'chargers', 'deliveries']) for (const item of draft[kind]) {
        if (!item.position.every(value => value !== '' && Number.isFinite(Number(value)))) continue;
        if (Number(item.position[0]) < 0 || Number(item.position[0]) >= width || Number(item.position[1]) < 0 || Number(item.position[1]) >= height) continue;
        const key = item.position.join(','); if (!groups.has(key)) groups.set(key, []); groups.get(key).push({ kind, item });
      }
      const destination = event => {
        const p = svg.createSVGPoint(); p.x = event.clientX; p.y = event.clientY;
        const at = p.matrixTransform(svg.getScreenCTM().inverse());
        return [Math.min(width - 1, Math.max(0, Math.round((at.x - left) / cell))), Math.min(height - 1, Math.max(0, Math.round(height - 1 - (at.y - top) / cell)))];
      };
      let drag = null;
      for (const entries of groups.values()) {
        const active = entries.find(entry => entry.kind === selected.kind && entry.item.id === selected.id);
        const { kind, item } = active || entries[0], [x, y] = point(item.position);
        const marker = svgElement('g', { transform: 'translate(' + x + ' ' + y + ')', tabindex: busy ? -1 : 0,
          role: 'button', 'aria-label': entries.map(entry => entry.item.id).join(', ') + ' at (' + item.position.join(', ') + ')' + (entries.length > 1 ? '. Select to choose an object.' : ''),
          'data-marker': item.id, class: 'builder-marker' + (active ? ' selected' : '') });
        const owner = kind === 'deliveries' ? draft.vehicles.findIndex(vehicle => vehicle.id === item.vehicle_id) : -1;
        const color = owner >= 0 ? colors[owner % colors.length] : kind === 'depots' ? '#455e64' : '#1e5147';
        marker.append(svgElement('circle', { r: 23 * markerScale, class: 'builder-marker-target' }));
        if (kind === 'depots') marker.append(svgElement('rect', { x: -12 * markerScale, y: -12 * markerScale, width: 24 * markerScale, height: 24 * markerScale, rx: 3, fill: color }));
        else marker.append(svgElement('circle', { r: 13 * markerScale, fill: color }));
        const symbol = svgElement('text', { x: 0, y: 4 * markerScale, class: 'builder-marker-symbol', style: 'font-size:' + 18 * markerScale + 'px' }); symbol.textContent = kind === 'chargers' ? 'ϟ' : kind === 'depots' ? 'P' : '•'; marker.append(symbol);
        const label = svgElement('text', { x: 0, y: -21 * markerScale, class: 'builder-marker-label', style: 'font-size:' + 12 * markerScale + 'px' }); label.textContent = entries.length > 1 ? item.id + ' +' + (entries.length - 1) : item.id; marker.append(label);
        marker.onclick = event => {
          event.stopPropagation(); if (busy) return;
          if (moveMode && selectedItem()?.position) { selectedItem().position = [...item.position].map(Number); moveMode = false; changed(); return; }
          if (entries.length > 1) { overlap = entries; renderOverlap(); }
          else select(kind, item.id);
        };
        marker.onkeydown = event => {
          if (busy) return;
          if (event.key === 'Enter' || event.key === ' ') { event.preventDefault(); marker.onclick(event); }
          if (event.key === 'Escape') { moveMode = false; overlap = null; renderOverlap(); renderStatus(); }
          const offset = { ArrowLeft: [-1, 0], ArrowRight: [1, 0], ArrowUp: [0, 1], ArrowDown: [0, -1] }[event.key];
          if (offset) {
            event.preventDefault();
            selected = { kind, id: item.id };
            item.position = [Math.min(width - 1, Math.max(0, Number(item.position[0]) + offset[0])), Math.min(height - 1, Math.max(0, Number(item.position[1]) + offset[1]))];
            changed(); mapHost.querySelector('[data-marker="' + item.id + '"]')?.focus();
          }
        };
        marker.onpointerdown = event => {
          if (busy || moveMode || event.button !== 0) return;
          drag = { kind, item, marker, origin: [event.clientX, event.clientY], moved: false };
        };
        svg.append(marker);
      }
      svg.onpointermove = event => {
        if (!drag) return;
        if (Math.hypot(event.clientX - drag.origin[0], event.clientY - drag.origin[1]) < 5 && !drag.moved) return;
        if (!drag.moved) svg.setPointerCapture(event.pointerId);
        drag.moved = true; drag.marker.setAttribute('transform', 'translate(' + point(destination(event)).join(' ') + ')');
      };
      svg.onpointerup = event => {
        if (!drag) return;
        const moved = drag; drag = null;
        if (svg.hasPointerCapture(event.pointerId)) svg.releasePointerCapture(event.pointerId);
        if (moved.moved) { moved.item.position = destination(event); selected = { kind: moved.kind, id: moved.item.id }; changed(); }
      };
      svg.onpointercancel = () => { drag = null; renderMap(); };
      svg.onclick = event => {
        if (!busy && moveMode && selectedItem()?.position) { selectedItem().position = destination(event); moveMode = false; changed(); }
      };
      svg.onkeydown = event => { if (event.key === 'Escape') { moveMode = false; renderStatus(); } };
      mapHost.replaceChildren(svg); renderOverlap();
      mapSummary.textContent = draft.grid.width + ' × ' + draft.grid.height + ' grid · ' + draft.grid.block_km + ' km / block';
      mapHost.classList.toggle('is-moving', moveMode);
    }
    function renderOverlap() {
      overlapHost.replaceChildren(); overlapHost.hidden = !overlap;
      if (!overlap) return;
      overlapHost.append(element('p', 'hint', 'Choose an object at this intersection:'));
      for (const entry of overlap) overlapHost.append(button(entry.item.id + ' · ' + names[entry.kind].toLowerCase(), () => select(entry.kind, entry.item.id)));
    }
    function render() { renderInspector(); renderMap(); renderStatus(); }
    function submit() {
      if (busy || !configured) return;
      showErrors = true; renderStatus();
      if (D.validate(draft).length) { error.setAttribute('tabindex', '-1'); error.focus(); return; }
      const scenario = D.normalize(draft);
      const reuse = D.reuse(source, scenario, root.ScenarioControls?.isValidValue || (() => false));
      onSubmit(scenario, reuse);
    }
    function showMode(next) {
      mode = next === 'prompt' ? 'prompt' : 'builder';
      host.hidden = mode !== 'builder'; document.getElementById('prompt-composer').hidden = mode !== 'prompt';
      document.getElementById('prompt-screen').classList.toggle('builder-entry', mode === 'builder');
      for (const name of ['builder', 'prompt']) {
        document.getElementById(name + '-mode').setAttribute('aria-selected', String(name === mode));
        document.getElementById(name + '-mode').tabIndex = name === mode ? 0 : -1;
      }
      if (mode === 'builder') renderMap();
      onModeChange(mode);
    }
    document.getElementById('builder-mode').onclick = () => showMode('builder');
    document.getElementById('prompt-mode').onclick = () => showMode('prompt');
    for (const id of ['builder-mode', 'prompt-mode']) document.getElementById(id).onkeydown = event => {
      if (!['ArrowLeft', 'ArrowRight', 'Home', 'End'].includes(event.key)) return;
      event.preventDefault(); const next = event.key === 'Home' ? 'builder' : event.key === 'End' ? 'prompt' : mode === 'builder' ? 'prompt' : 'builder';
      showMode(next); document.getElementById(next + '-mode').focus();
    };
    render(); showMode('builder');
    if (root.ResizeObserver) {
      let mapWidth = mapHost.clientWidth;
      new root.ResizeObserver(() => {
        if (mapHost.clientWidth && mapHost.clientWidth !== mapWidth) { mapWidth = mapHost.clientWidth; renderMap(); }
      }).observe(mapHost);
    }
    return {
      setConfigured(value) { configured = Boolean(value); renderStatus(); },
      setBusy(value) { if (busy === Boolean(value)) return; busy = Boolean(value); render(); },
      edit(scenario, view = null) {
        draft = D.clone(scenario); source = view; selected = { kind: 'vehicles', id: draft.vehicles[0]?.id };
        title.value = draft.title; showErrors = false; serverError = ''; moveMode = false; changed(); showMode('builder');
      },
      getScenario() { return D.validate(draft).length ? null : D.normalize(draft); },
      sourceView() { return source; },
      acceptResult(view) { source = view; renderStatus(); },
      setError(message) {
        serverError = message || ''; renderStatus();
        if (message) { error.tabIndex = -1; error.focus(); }
      },
      showMode,
      refresh: render,
    };
  }
  root.ScenarioBuilder = { create };
})(globalThis);
