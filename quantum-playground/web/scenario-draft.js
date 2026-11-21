/* Structured authoring state, validation, persistence, and approved-control reuse. */
(function (root) {
  'use strict';
  const storageKey = 'quantumloop-scenario-draft-v1';
  const legacyStorageKey = 'fleet-lab-scenario-draft-v1';
  const clone = value => JSON.parse(JSON.stringify(value));
  const collections = ['depots', 'chargers', 'deliveries', 'vehicles'];
  const limits = { depots: 6, chargers: 6, vehicles: 6, deliveries: 60, stops: 10, slots: 4 };
  const typeOf = scenario => scenario?.schema_version === 2 && scenario?.map?.kind === 'geographic' ? 'geographic' : 'grid';
  const roundPosition = position => position.map(number => Math.round(Number(number) * 1e6) / 1e6);

  function seed() {
    return {
      schema_version: 1, title: 'Two depots, three deliveries',
      grid: { width: 6, height: 6, block_km: 0.5 },
      depots: [{ id: 'P1', position: [2, 2] }, { id: 'P2', position: [4, 4] }],
      chargers: [{ id: 'C1', position: [2, 1], power_kw: 10,
        slots: [{ id: 'S1', start_min: 30, end_min: 60 }, { id: 'S2', start_min: 60, end_min: 90 }] },
      { id: 'C2', position: [4, 3], power_kw: 10,
        slots: [{ id: 'S1', start_min: 30, end_min: 60 }, { id: 'S2', start_min: 60, end_min: 90 }] }],
      deliveries: [{ id: 'D1', position: [0, 0], vehicle_id: 'V1', deadline_min: 60, service_min: 5 },
        { id: 'D2', position: [1, 4], vehicle_id: 'V1', deadline_min: 90, service_min: 5 },
        { id: 'D3', position: [5, 0], vehicle_id: 'V2', deadline_min: 90, service_min: 5 }],
      vehicles: [{ id: 'V1', depot_id: 'P1', initial_charge_pct: 40 },
        { id: 'V2', depot_id: 'P2', initial_charge_pct: 35 }],
      fleet: { battery_capacity_kwh: 20, reserve_pct: 15, energy_use_kwh_per_km: 0.3 },
      timing: { start_hour: 8, duration_min: 120, speed_kmh: 30 },
      costs: { driving_per_km: 0.3, electricity_per_kwh: 0.2, waiting_per_hour: 25, late_per_hour: 60 },
    };
  }

  function geographicSeed() {
    const scenario = seed();
    scenario.schema_version = 2;
    scenario.title = 'Toronto delivery routes';
    scenario.map = { kind: 'geographic', profile: 'driving' };
    delete scenario.grid; delete scenario.timing.speed_kmh;
    scenario.depots[0].position = [-79.3832, 43.6532]; scenario.depots[1].position = [-79.395, 43.653];
    scenario.chargers[0].position = [-79.386, 43.65]; scenario.chargers[1].position = [-79.389, 43.655];
    scenario.deliveries[0].position = [-79.379, 43.65]; scenario.deliveries[1].position = [-79.392, 43.65];
    scenario.deliveries[2].position = [-79.398, 43.655];
    return scenario;
  }

  // Keep incomplete text in the draft; convert only at the submission boundary.
  function normalize(draft) {
    const value = clone(draft);
    const number = raw => raw === null || typeof raw === 'boolean' || String(raw).trim() === '' ? NaN : Number(raw);
    for (const section of ['grid', 'fleet', 'timing', 'costs']) {
      for (const key of Object.keys(value[section] || {})) value[section][key] = number(value[section][key]);
    }
    for (const section of collections) for (const item of value[section] || []) {
      if (item.position) {
        item.position = item.position.map(number);
        if (typeOf(value) === 'geographic') item.position = roundPosition(item.position);
      }
      for (const key of ['power_kw', 'deadline_min', 'service_min', 'initial_charge_pct']) {
        if (key in item) item[key] = number(item[key]);
      }
      for (const slot of item.slots || []) {
        slot.start_min = number(slot.start_min); slot.end_min = number(slot.end_min);
      }
    }
    value.title = typeof value.title === 'string' ? value.title.trim() : '';
    return value;
  }

  function validate(draft) {
    let value;
    try { value = normalize(draft); } catch { return ['This draft could not be read. Reset to the example to start again.']; }
    const errors = [];
    const positive = (number, label) => {
      if (!Number.isFinite(number) || number <= 0) errors.push(label + ' must be greater than zero.');
    };
    const numeric = (number, label, min, max = Infinity, integer = false) => {
      if (!Number.isFinite(number) || number < min || number > max || (integer && !Number.isInteger(number))) {
        errors.push(label + ' must be ' + (integer ? 'a whole number ' : '') +
          (max === Infinity ? 'at least ' + min : 'between ' + min + ' and ' + max) + '.');
      }
    };
    const geographic = typeOf(value) === 'geographic';
    if (value.schema_version !== 1 && !geographic) errors.push('This scenario version is not supported.');
    if (!value.title || value.title.length > 120) errors.push('Enter a scenario title of 1–120 characters.');
    if (/[\u0000-\u001f]/.test(value.title)) errors.push('The title cannot contain control characters.');
    if (geographic) {
      if (value.map.profile !== 'driving') errors.push('Real maps currently support driving routes.');
    } else {
      numeric(value.grid?.width, 'Grid width', 2, 50, true);
      numeric(value.grid?.height, 'Grid height', 2, 50, true);
      positive(value.grid?.block_km, 'Distance per block');
    }
    positive(value.fleet?.battery_capacity_kwh, 'Battery capacity');
    numeric(value.fleet?.reserve_pct, 'Battery reserve', 0, 100);
    positive(value.fleet?.energy_use_kwh_per_km, 'Energy use');
    numeric(value.timing?.start_hour, 'Shift start hour', 0, 24);
    if (value.timing?.start_hour === 24) errors.push('Shift start must be before 24:00.');
    numeric(value.timing?.duration_min, 'Shift length', 1, 1440);
    if (!geographic) positive(value.timing?.speed_kmh, 'Driving speed');
    for (const key of ['driving_per_km', 'electricity_per_kwh', 'waiting_per_hour', 'late_per_hour']) {
      numeric(value.costs?.[key], key.replaceAll('_', ' '), 0);
    }
    for (const section of collections) {
      const rows = value[section];
      if (!Array.isArray(rows) || rows.length > limits[section] || (!rows.length && section !== 'chargers')) {
        errors.push('Use ' + (section === 'chargers' ? '0' : '1') + '–' + limits[section] + ' ' + section + '.');
        continue;
      }
      const seen = new Set();
      for (const row of rows) {
        const prefix = { depots: 'P', vehicles: 'V', deliveries: 'D', chargers: 'C' }[section];
        if (!row || typeof row.id !== 'string' || !new RegExp('^' + prefix + '[1-9][0-9]{0,5}$').test(row.id) || seen.has(row.id)) {
          errors.push('Every ' + section.slice(0, -1) + ' needs a unique ID.'); continue;
        }
        seen.add(row.id);
        if (section !== 'vehicles') {
          if (!Array.isArray(row.position) || row.position.length !== 2) errors.push(row.id + ' needs ' + (geographic ? 'longitude and latitude' : 'X and Y coordinates') + '.');
          else {
            numeric(row.position[0], row.id + (geographic ? ' longitude' : ' X'), geographic ? -180 : 0, geographic ? 180 : value.grid.width - 1, !geographic);
            numeric(row.position[1], row.id + (geographic ? ' latitude' : ' Y'), geographic ? -85 : 0, geographic ? 85 : value.grid.height - 1, !geographic);
          }
        }
      }
    }
    if (collections.some(section => !Array.isArray(value[section]) || value[section].some(row => !row || typeof row !== 'object'))) return errors;
    for (const vehicle of value.vehicles) {
      if (!value.depots.some(depot => depot.id === vehicle.depot_id)) errors.push(vehicle.id + ' needs a starting depot.');
      numeric(vehicle.initial_charge_pct, vehicle.id + ' starting charge', 0, 100);
      if (value.deliveries.filter(delivery => delivery.vehicle_id === vehicle.id).length > limits.stops) {
        errors.push(vehicle.id + ' can have at most 10 delivery stops.');
      }
    }
    for (const delivery of value.deliveries) {
      if (!value.vehicles.some(vehicle => vehicle.id === delivery.vehicle_id)) errors.push(delivery.id + ' needs a vehicle assignment.');
      numeric(delivery.deadline_min, delivery.id + ' due time', 0);
      numeric(delivery.service_min, delivery.id + ' service time', 0);
    }
    for (const charger of value.chargers) {
      positive(charger.power_kw, charger.id + ' charging power');
      if (!Array.isArray(charger.slots) || charger.slots.length > limits.slots || charger.slots.some(slot => !slot || typeof slot !== 'object')) {
        errors.push(charger.id + ' can have at most four charging slots.'); continue;
      }
      const seen = new Set();
      for (const slot of charger.slots) {
        if (!/^S[1-9][0-9]{0,5}$/.test(slot.id) || seen.has(slot.id)) errors.push(charger.id + ' needs unique slot IDs.');
        seen.add(slot.id);
        numeric(slot.start_min, charger.id + ' slot start', 0, value.timing.duration_min);
        numeric(slot.end_min, charger.id + ' slot end', 0, value.timing.duration_min);
        if (slot.end_min <= slot.start_min) errors.push(charger.id + ' slots must end after they start.');
      }
      const ordered = [...charger.slots].sort((a, b) => a.start_min - b.start_min);
      if (ordered.some((slot, index) => index && slot.start_min < ordered[index - 1].end_min)) {
        errors.push(charger.id + ' charging slots must not overlap.');
      }
    }
    return [...new Set(errors)];
  }

  function readable(draft) {
    return draft && collections.every(key => Array.isArray(draft[key]) &&
      draft[key].every(row => row && typeof row.id === 'string' &&
        new RegExp('^' + ({ depots: 'P', vehicles: 'V', deliveries: 'D', chargers: 'C' }[key]) + '[1-9][0-9]{0,5}$').test(row.id) &&
        (key === 'vehicles' || (Array.isArray(row.position) && row.position.length === 2)) &&
        (key !== 'chargers' || (Array.isArray(row.slots) && row.slots.every(slot => slot && /^S[1-9][0-9]{0,5}$/.test(slot.id)))))) &&
      ['fleet', 'timing', 'costs'].every(key => draft[key] && typeof draft[key] === 'object') &&
      (typeOf(draft) === 'geographic' || (draft.grid && typeof draft.grid === 'object'));
  }
  function loadState(storage) {
    const initial = { version: 2, active: 'grid', drafts: { grid: seed(), geographic: geographicSeed() } };
    try {
      const saved = JSON.parse(storage?.getItem(storageKey) || storage?.getItem(legacyStorageKey));
      if (saved?.version === 2 && saved.drafts) {
        for (const kind of ['grid', 'geographic']) if (readable(saved.drafts[kind]) && typeOf(saved.drafts[kind]) === kind) initial.drafts[kind] = clone(saved.drafts[kind]);
        if (saved.active === 'geographic') initial.active = 'geographic';
      } else if (saved?.version === 1 && readable(saved.draft)) {
        initial.active = typeOf(saved.draft); initial.drafts[initial.active] = clone(saved.draft);
      }
    } catch { /* Storage can be disabled or contain an older draft. */ }
    return initial;
  }
  function load(storage) { const state = loadState(storage); return state.drafts[state.active]; }
  function saveState(storage, state) {
    try { storage?.setItem(storageKey, JSON.stringify(state)); return Boolean(storage); }
    catch { return false; }
  }
  function save(storage, draft) {
    const state = loadState(storage); state.active = typeOf(draft); state.drafts[state.active] = draft;
    return saveState(storage, state);
  }
  function nextId(rows, prefix) {
    let id = 1;
    while (rows.some(row => row.id === prefix + id)) id++;
    return prefix + id;
  }
  const stable = value => JSON.stringify(value, function (key, item) {
    if (Array.isArray(item) && item.every(row => row && typeof row === 'object' && typeof row.id === 'string')) {
      return [...item].sort((a, b) => a.id.localeCompare(b.id, undefined, { numeric: true }));
    }
    return item && !Array.isArray(item) && typeof item === 'object'
      ? Object.fromEntries(Object.keys(item).sort().map(name => [name, item[name]])) : item;
  });
  function bindingTarget(scenario, path) {
    if (!Array.isArray(path) || path.length < 2) return null;
    let object = scenario;
    for (const key of path.slice(0, -1)) {
      if (key === null) continue;
      object = Array.isArray(object) ? object.find(row => row.id === key) : object?.[key];
      if (!object) return null;
    }
    return { object, field: path.at(-1) };
  }
  function reuse(source, scenario, validValue) {
    const context = source?.scenario_context;
    if (!source?.view_id || !context?.effective_scenario || !context.bindings) return null;
    const patched = clone(context.effective_scenario), variables = {};
    for (const [id, path] of Object.entries(context.bindings)) {
      const before = bindingTarget(patched, path), after = bindingTarget(scenario, path);
      if (!before?.object || !after?.object || !Object.hasOwn(before.object, before.field)) return null;
      if (before.object[before.field] === after.object[after.field]) continue;
      const definition = source.variables?.find(control => control.id === id);
      if (!definition || !validValue(definition, after.object[after.field])) return null;
      variables[id] = after.object[after.field];
      before.object[before.field] = after.object[after.field];
    }
    return stable(patched) === stable(scenario) ? { view_id: source.view_id, variables } : null;
  }
  const same = (a, b) => stable(a) === stable(b);
  const api = { seed, geographicSeed, typeOf, roundPosition, normalize, validate, load, save, loadState, saveState, nextId, reuse, same, limits, storageKey, clone };
  if (typeof module !== 'undefined') module.exports = api; else root.ScenarioDraft = api;
})(typeof globalThis === 'undefined' ? this : globalThis);
