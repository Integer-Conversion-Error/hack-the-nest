(function (global) {
  'use strict';
  const number = (value) =>
    value === null || typeof value === 'boolean' || String(value).trim() === '' ? NaN : Number(value);
  const money = (value) =>
    new Intl.NumberFormat('en-CA', { style: 'currency', currency: 'CAD', maximumFractionDigits: 2 }).format(value);
  const EPS = 1e-8;
  const twoDecimals = new Intl.NumberFormat('en-CA', { minimumFractionDigits: 2, maximumFractionDigits: 2 });
  // Directed road distances and driving durations from tests/fixtures/geographic-road-replay.json.
  // This saved OSRM snapshot is usable without contacting a routing provider.
  const SNAPSHOT = {
    created: '2026-11-20',
    positions: [
      [-79.3832, 43.6532],
      [-79.395, 43.653],
      [-79.386, 43.65],
      [-79.389, 43.655],
      [-79.379, 43.65],
      [-79.392, 43.65],
      [-79.398, 43.655],
    ],
    distances_m: [
      [0, 1363.7, 911.9, 768.8, 597, 1027.5, 1645.5],
      [1363.7, 0, 1519.6, 652.8, 1963.4, 684.5, 570.9],
      [687.4, 1119.6, 0, 827.4, 767.9, 617.6, 1583.9],
      [1060.1, 1055.2, 1205.7, 0, 1649.5, 1321.4, 1337],
      [1002.7, 2225.5, 1095, 1771.5, 0, 1723.5, 2648.2],
      [1096.7, 502, 884.7, 864.1, 1317.8, 0, 966.2],
      [1631, 452.6, 1787, 920.1, 2230.7, 951.8, 0],
    ],
    durations_s: [
      [0, 197.1, 138, 104.7, 86.1, 123, 246.5],
      [187.3, 0, 231, 101.8, 270.1, 113.9, 112.9],
      [89, 191.8, 0, 121.5, 117.9, 93.1, 259],
      [153.5, 164.1, 184.1, 0, 223.2, 176.6, 213.5],
      [133.4, 317.4, 137.6, 238.1, 0, 218.7, 379.9],
      [143.3, 98.7, 126.3, 128.9, 174, 0, 165.9],
      [237.1, 106, 280.8, 151.6, 319.9, 163.7, 0],
    ],
  };
  function seed() {
    return {
      budget: 120,
      maxSites: 2,
      minutes: 3,
      hourlyCost: 24,
      requireFullCoverage: false,
      sites: ['Central', 'West', 'South', 'North'].map((name, i) => ({
        id: 'H' + (i + 1),
        name,
        enabled: true,
        cost: [70, 55, 45, 60][i],
        capacity: [30, 32, 24, 24][i],
        position: [...SNAPSHOT.positions[i]],
      })),
      demand: ['East', 'South-west', 'North-west'].map((name, i) => ({
        id: 'D' + (i + 1),
        name,
        weight: [14, 18, 12][i],
        position: [...SNAPSHOT.positions[i + 4]],
      })),
      ...snapshotMetrics(),
    };
  }
  function snapshotMetrics() {
    return {
      distances: SNAPSHOT.distances_m.map(row => [...row]),
      durations: SNAPSHOT.durations_s.map(row => [...row]),
    };
  }
  function driveMetrics(metres, seconds) {
    return twoDecimals.format(metres / 1000) + ' km · ' + twoDecimals.format(seconds / 60) + ' min';
  }
  function positions(input) {
    return [...input.sites, ...input.demand].map((node) => node.position.map(number));
  }
  function signature(input) {
    return JSON.stringify(positions(input));
  }
  function prepareInputs(input) {
    if (input == null) return seed();
    // Only authored fields cross this boundary. Provider matrices and routing
    // metadata must come from a matching saved snapshot or the road endpoint.
    const source = JSON.parse(JSON.stringify(input));
    const state = Object.fromEntries(['budget', 'maxSites', 'minutes', 'hourlyCost', 'sites', 'demand'].map(key => [key, source[key]]));
    state.requireFullCoverage = source.requireFullCoverage ?? false;
    return Object.assign(state, signature(state) === JSON.stringify(SNAPSHOT.positions)
      ? snapshotMetrics() : { distances: null, durations: null });
  }
  function validPosition(position) {
    return (
      Array.isArray(position) &&
      position.length === 2 &&
      Number.isFinite(number(position[0])) &&
      Number.isFinite(number(position[1])) &&
      Math.abs(number(position[0])) <= 180 &&
      Math.abs(number(position[1])) <= 85
    );
  }
  function validateMatrix(matrix, count) {
    if (!Array.isArray(matrix) || matrix.length !== count) return false;
    for (let i = 0; i < count; i++) {
      if (!Array.isArray(matrix[i]) || matrix[i].length !== count) return false;
      for (let j = 0; j < count; j++) {
        const value = matrix[i][j];
        if (value !== null && (typeof value !== 'number' || !Number.isFinite(value) || value < 0)) return false;
        if (i === j && value !== 0) return false;
      }
    }
    return true;
  }
  function validRoadMetrics(distances, durations, count) {
    return validateMatrix(distances, count) && validateMatrix(durations, count) &&
      distances.every((row, i) => row.every((metres, j) => (metres === null) === (durations[i][j] === null)));
  }
  function validate(input) {
    const errors = [],
      limit = (key, min, max, integer = false) => {
        const v = number(input?.[key]);
        if (!Number.isFinite(v) || v < min || v > max || (integer && !Number.isInteger(v)))
          errors.push(
            key + ' must be ' + (integer ? 'a whole number ' : '') + 'between ' + min + ' and ' + max + '.',
          );
      };
    limit('budget', 0, 10000);
    limit('minutes', 0.1, 120);
    limit('hourlyCost', 0, 200);
    if (input?.requireFullCoverage !== undefined && typeof input.requireFullCoverage !== 'boolean')
      errors.push('Full coverage must be selected or cleared.');
    if (
      !Array.isArray(input?.sites) ||
      input.sites.length < 1 ||
      input.sites.length > 10 ||
      !Array.isArray(input?.demand) ||
      input.demand.length !== 3
    )
      return [...errors, 'Use 1–10 candidate hubs and three demand zones.'];
    limit('maxSites', 1, input.sites.length, true);
    const ids = new Set();
    for (const node of [...input.sites, ...input.demand]) {
      if (!node || typeof node.id !== 'string' || ids.has(node.id)) {
        errors.push('Locations need unique IDs.');
        continue;
      }
      ids.add(node.id);
      if (!validPosition(node.position))
        errors.push(node.id + ' needs a longitude from −180 to 180 and latitude from −85 to 85.');
    }
    input.sites.forEach((site) => {
      if (!site) return;
      const cost = number(site.cost),
        capacity = number(site.capacity);
      if (!Number.isFinite(cost) || cost < 0 || cost > 10000)
        errors.push(site.id + ' opening cost must be $0–$10,000.');
      if (!Number.isInteger(capacity) || capacity < 0 || capacity > 1000)
        errors.push(site.id + ' capacity must be 0–1,000 whole visits/day.');
      if (typeof site.enabled !== 'boolean') errors.push(site.id + ' availability must be selected or cleared.');
    });
    input.demand.forEach((zone) => {
      if (!zone) return;
      const weight = number(zone.weight);
      if (!Number.isInteger(weight) || weight < 0 || weight > 1000)
        errors.push(zone.id + ' demand must be 0–1,000 whole visits/day.');
    });
    if (!validRoadMetrics(input.distances, input.durations, input.sites.length + input.demand.length))
      errors.push('Recalculate road times to get matching driving distances and travel times for these locations.');
    return errors;
  }
  function evaluate(input, assignment) {
    const loads = input.sites.map(() => 0),
      opened = new Set();
    let covered = 0,
      metres = 0,
      seconds = 0;
    for (let d = 0; d < input.demand.length; d++) {
      const s = assignment[d],
        weight = number(input.demand[d].weight);
      if (s < 0 || weight === 0) continue;
      const out = input.durations[s][d + input.sites.length],
        back = input.durations[d + input.sites.length][s];
      if (!input.sites[s]?.enabled || out === null || back === null || out > number(input.minutes) * 60 + EPS)
        return null;
      opened.add(s);
      loads[s] += weight;
      covered += weight;
      seconds += (out + back) * weight;
      const zone = d + input.sites.length;
      metres += (input.distances[s][zone] + input.distances[zone][s]) * weight;
    }
    const opening = [...opened].reduce((sum, i) => sum + number(input.sites[i].cost), 0);
    if (
      opened.size > number(input.maxSites) ||
      opening > number(input.budget) + EPS ||
      loads.some((load, i) => load > number(input.sites[i].capacity))
    )
      return null;
    return {
      assignment: [...assignment],
      opened: [...opened],
      loads,
      covered,
      opening,
      roadKm: metres / 1000,
      roadHours: seconds / 3600,
      cost: opening + (seconds / 3600) * number(input.hourlyCost),
    };
  }
  function solve(input) {
    const errors = validate(input);
    if (errors.length) return { status: 'invalid', errors };
    const choices = input.demand.map((zone, d) => {
      const eligible = [-1];
      if (number(zone.weight) === 0) return eligible;
      input.sites.forEach((site, s) => {
        const out = input.durations[s][d + input.sites.length],
          back = input.durations[d + input.sites.length][s];
        if (site.enabled && out !== null && back !== null && out <= number(input.minutes) * 60 + EPS)
          eligible.push(s);
      });
      return eligible;
    });
    let best = null,
      evaluated = 0;
    function visit(assignment) {
      if (assignment.length < input.demand.length) {
        for (const choice of choices[assignment.length]) visit([...assignment, choice]);
        return;
      }
      evaluated++;
      const plan = evaluate(input, assignment);
      if (!plan) return;
      if (
        !best ||
        plan.covered > best.covered ||
        (plan.covered === best.covered &&
          (plan.cost < best.cost - EPS ||
            (Math.abs(plan.cost - best.cost) <= EPS && plan.opened.length < best.opened.length)))
      )
        best = plan;
    }
    visit([]);
    const nearest = choices.map(
      (options, d) =>
        options
          .filter((s) => s >= 0)
          .sort(
            (a, b) => input.durations[a][d + input.sites.length] - input.durations[b][d + input.sites.length],
          )[0] ?? -1,
    );
    const result = {
      status: 'ok',
      ...best,
      totalDemand: input.demand.reduce((sum, zone) => sum + number(zone.weight), 0),
      evaluated,
      baseline: evaluate(input, nearest),
      eligible: choices.map((row) => row.filter((i) => i >= 0)),
    };
    if (input.requireFullCoverage && result.covered < result.totalDemand) return {
      ...result, status: 'infeasible',
      message: 'Full coverage is infeasible under these limits. Best achievable coverage is ' + result.covered +
        ' of ' + result.totalDemand + ' visits/day. Increase the budget, capacities, hub limit or drive-time limit.',
    };
    return result;
  }
  function validateResponse(response, requested) {
    if (
      !response ||
      !validRoadMetrics(response.distances_m, response.durations_s, requested.length) ||
      !Array.isArray(response.positions) ||
      response.positions.length !== requested.length
    )
      throw new Error('The road service returned an incomplete matrix. Retry recalculation.');
    response.positions.forEach((point, i) => {
      if (!validPosition(point) || point.some((value, j) => Math.abs(number(value) - requested[i][j]) > 1e-7))
        throw new Error('The road response did not match the requested locations. Retry recalculation.');
    });
    return {
      distances: response.distances_m.map(row => [...row]),
      durations: response.durations_s.map(row => [...row]),
    };
  }

  function mount(container, inputs) {
    const doc = container.ownerDocument,
      state = prepareInputs(inputs);
    const namespace = container.dataset?.problem && container.dataset.problem !== 'facility' ? container.dataset.problem
      : container.id?.startsWith('scenario-') ? container.id : '';
    const elementId = value => namespace ? namespace + '-' + value : value;
    let matrixSignature = state.durations ? signature(state) : null,
      sourceLabel = state.durations ? 'Saved OSRM road snapshot · ' + SNAPSHOT.created : 'Road times have not been calculated for these locations.',
      map,
      tileLayer,
      layers,
      pathRequest,
      routingDigest,
      request,
      requestId = 0,
      disposed = false,
      configRequest;
    const markers = new Map(),
      coordinateInputs = new Map();
    container.classList.add('facility-view', 'domain-view');
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
      input.type = 'number';
      input.value = value;
      for (const [key, value] of Object.entries({ min: 0, max: 10000, step: 1, ...options }))
        input.setAttribute(key, value);
      input.setAttribute('aria-label', label);
      input.oninput = () => {
        change(input.value);
        update();
      };
      wrap.append(input);
      return wrap;
    };
    const controls = el('section', undefined, 'problem-card');
    controls.setAttribute('aria-label', 'Facility coverage controls');
    const fields = el('div', undefined, 'problem-controls');
    controls.append(
      fields,
      el(
        'p',
        'Maximize visits served within the outbound driving-time limit. Each demand zone stays together. Opening budget, site count and daily capacities are hard limits; operating cost breaks equal-coverage ties.',
        'hint',
      ),
    );
    const reset = button('Reset Toronto example', () => {
      if (request) request.abort();
      request = null;
      requestId++;
      clearPath();
      routingDigest = null;
      Object.assign(state, seed());
      matrixSignature = signature(state);
      sourceLabel = 'Saved OSRM road snapshot · ' + SNAPSHOT.created;
      renderControls();
      renderEditors();
      update();
      fitMap();
    });
    controls.append(reset);
    const status = el('p', undefined, 'facility-status');
    status.setAttribute('role', 'status');
    const metrics = el('div', undefined, 'problem-metrics facility-metrics');
    const figure = el('figure', undefined, 'problem-figure');
    figure.append(el('h2', 'Candidate hubs and demand'));
    const mapNode = el('div', undefined, 'facility-map');
    mapNode.setAttribute('aria-label', 'Candidate hubs and demand locations');
    const mapStatus = el('p', undefined, 'hint');
    mapStatus.setAttribute('role', 'status');
    const legend = el('div', undefined, 'facility-legend');
    for (const [cls, text] of [
      ['open', 'Open hub'],
      ['closed', 'Unused candidate'],
      ['served', 'Served demand'],
      ['unserved', 'Unserved demand'],
    ])
      legend.append(el('span', text, cls));
    const recalc = button('Recalculate road times', recalculate);
    recalc.id = elementId('facility-recalculate');
    const mapActions = el('div', undefined, 'facility-map-actions');
    mapActions.append(recalc, button('Fit locations', fitMap));
    const tripControls = el('div', undefined, 'facility-trip-controls');
    const tripLabel = el('label', undefined, 'problem-field');
    const tripSelect = el('select');
    tripSelect.setAttribute('aria-label', 'Road trip to display');
    tripSelect.onchange = () => { clearPath(); updateTripSummary(); };
    tripLabel.append(el('span', 'Road trip to display'), tripSelect);
    const showTrip = button('Show road route', showRoadPath);
    const tripSummary = el('strong', undefined, 'facility-trip-summary');
    const tripStatus = el('p', undefined, 'facility-route-status');
    tripStatus.setAttribute('role', 'status');
    tripControls.append(tripLabel, showTrip, tripSummary, tripStatus);
    figure.append(
      mapNode,
      legend,
      tripControls,
      mapActions,
      mapStatus,
      el(
        'figcaption',
        'Drag a pin or edit its coordinates, then recalculate road times. Each served visit makes one outbound and one return trip. Driving estimates follow the road network and exclude live traffic.',
      ),
    );
    const editors = el('div', undefined, 'facility-editors');
    const assignments = el('section', undefined, 'problem-card');
    const assignmentHeading = el('h2', 'Service plan'); assignments.append(assignmentHeading);
    const assignmentContent = el('div', undefined, 'facility-table-wrap');
    assignments.append(assignmentContent);
    const method = el('details', undefined, 'problem-card');
    method.append(
      el('summary', 'Search, data and assumptions'),
      el(
        'p',
        'A local classical search checks every eligible assignment, including leaving a zone unserved: at most 1,331 combinations for ten candidate hubs and three demand zones. The objective first maximizes daily visits covered, then minimizes opening plus roundtrip driving cost. A zone cannot be split between hubs. Locations snap to nearby driving roads; an assignment requires an outbound and a return route. Road times have no live traffic. Moving, adding or removing a pin invalidates the saved matrix until recalculation succeeds. The initial Toronto example embeds the repository’s actual OSRM snapshot, so its calculation works offline.',
      ),
    );
    container.replaceChildren(status, metrics, figure, assignments, controls, editors, method);
    function heading(table, labels) {
      const head = el('thead'),
        row = el('tr');
      for (const label of labels) {
        const cell = el('th', label);
        cell.scope = 'col';
        row.append(cell);
      }
      head.append(row);
      table.append(head);
    }
    function serviceCell(label, text) {
      const cell = el('td');
      const mobileLabel = el('span', label, 'facility-mobile-label');
      mobileLabel.setAttribute('aria-hidden', 'true');
      cell.append(mobileLabel, el('span', text));
      return cell;
    }
    function renderControls() {
      fields.replaceChildren(
        field('Opening budget ($/day)', state.budget, (value) => {
          state.budget = value;
        }),
        field(
          'Maximum open hubs',
          state.maxSites,
          (value) => {
            state.maxSites = value;
          },
          { min: 1, max: state.sites.length },
        ),
        field(
          'Drive-time limit (min)',
          state.minutes,
          (value) => {
            state.minutes = value;
          },
          { min: 0.1, max: 120, step: 0.1 },
        ),
        field(
          'Driving cost ($/hour)',
          state.hourlyCost,
          (value) => {
            state.hourlyCost = value;
          },
          { max: 200, step: 1 },
        ),
      );
      const fullCoverageLabel = el('label', undefined, 'facility-availability');
      const fullCoverage = el('input'); fullCoverage.type = 'checkbox';
      fullCoverage.checked = Boolean(state.requireFullCoverage);
      fullCoverage.setAttribute('aria-label', 'Require full demand coverage');
      fullCoverage.onchange = () => { state.requireFullCoverage = fullCoverage.checked; update(); };
      fullCoverageLabel.append(fullCoverage, el('span', 'Require every demand zone to be served'));
      fields.append(fullCoverageLabel);
    }
    function topologyChanged() {
      if (request) {
        request.abort();
        request = null;
        requestId++;
      }
      state.maxSites = Math.min(number(state.maxSites) || 1, state.sites.length);
      renderControls();
      renderEditors();
      update();
    }
    function positionChanged(node) {
      if (request) {
        request.abort();
        request = null;
        requestId++;
      }
      const marker = markers.get(node.id);
      if (marker && validPosition(node.position))
        marker.setLatLng([number(node.position[1]), number(node.position[0])]);
      update();
    }
    function coordinates(node) {
      const wrap = el('div', undefined, 'facility-coordinates');
      for (const [i, label, max] of [
        [0, 'Longitude', 180],
        [1, 'Latitude', 85],
      ]) {
        const group = el('label', undefined, 'problem-field');
        group.append(el('span', label));
        const input = el('input');
        input.type = 'number';
        input.min = -max;
        input.max = max;
        input.step = '.00001';
        input.value = node.position[i];
        input.setAttribute('aria-label', node.id + ' ' + label.toLowerCase());
        input.oninput = () => {
          node.position[i] = input.value;
          positionChanged(node);
        };
        group.append(input);
        wrap.append(group);
        coordinateInputs.set(node.id + ':' + i, input);
      }
      return wrap;
    }
    function renderEditors() {
      editors.replaceChildren();
      coordinateInputs.clear();
      const siteCard = el('section', undefined, 'problem-card'),
        zoneCard = el('section', undefined, 'problem-card');
      siteCard.append(el('h2', 'Candidate hubs'));
      zoneCard.append(el('h2', 'Demand zones'));
      for (const site of state.sites) {
        const row = el('div', undefined, 'facility-site-row'),
          name = el('label', undefined, 'facility-availability'),
          checkbox = el('input');
        checkbox.type = 'checkbox';
        checkbox.checked = site.enabled;
        checkbox.setAttribute('aria-label', site.id + ' available');
        checkbox.onchange = () => {
          site.enabled = checkbox.checked;
          update();
        };
        name.append(checkbox, el('strong', site.id + ' · ' + site.name));
        const remove = button('Remove ' + site.id, () => {
          state.sites = state.sites.filter((candidate) => candidate !== site);
          topologyChanged();
        });
        remove.disabled = state.sites.length === 1;
        row.append(
          name,
          field(site.id + ' opening ($/day)', site.cost, (value) => {
            site.cost = value;
          }),
          field(
            site.id + ' capacity (visits/day)',
            site.capacity,
            (value) => {
              site.capacity = value;
            },
            { max: 1000 },
          ),
          coordinates(site),
          remove,
        );
        siteCard.append(row);
      }
      const add = button('+ Add candidate hub', () => {
        let id = 1;
        while (state.sites.some((site) => site.id === 'H' + id)) id++;
        const anchor = [...state.sites, ...state.demand].find((node) => validPosition(node.position));
        const [lon, lat] = anchor ? anchor.position.map(number) : SNAPSHOT.positions[0];
        state.sites.push({
          id: 'H' + id,
          name: 'New candidate',
          enabled: true,
          cost: 50,
          capacity: 30,
          position: [
            Number(Math.max(-180, lon - id * 0.0006).toFixed(6)),
            Number(Math.min(85, lat + id * 0.0004).toFixed(6)),
          ],
        });
        topologyChanged();
      });
      add.disabled = state.sites.length >= 10;
      siteCard.append(add);
      for (const zone of state.demand) {
        const row = el('div', undefined, 'facility-zone-row');
        row.append(
          el('strong', zone.id + ' · ' + zone.name),
          field(
            zone.id + ' demand (visits/day)',
            zone.weight,
            (value) => {
              zone.weight = value;
            },
            { max: 1000 },
          ),
          coordinates(zone),
        );
        zoneCard.append(row);
      }
      editors.append(siteCard, zoneCard);
    }
    function fitMap() {
      if (!map) return;
      const points = [...state.sites, ...state.demand]
        .filter((node) => validPosition(node.position))
        .map((node) => [number(node.position[1]), number(node.position[0])]);
      if (points.length) map.fitBounds(points, { padding: [35, 35], maxZoom: 15, animate: false });
    }
    function initMap() {
      if (!global.L) {
        mapNode.replaceChildren(
          el('p', 'The map library is unavailable. Edit coordinates and inspect the service table below.'),
        );
        return;
      }
      map = global.L.map(mapNode, { scrollWheelZoom: false });
      layers = global.L.layerGroup().addTo(map);
      syncMarkers();
      fitMap();
      configRequest = new AbortController();
      global
        .fetch('/api/maps/config', { signal: configRequest.signal })
        .then((response) => {
          if (!response.ok) throw new Error('Map tiles unavailable.');
          return response.json();
        })
        .then((config) => {
          if (disposed || !map) return;
          if (typeof config.tile_url === 'string')
            tileLayer = global.L.tileLayer(config.tile_url, {
              attribution: config.tile_attribution || '',
              maxZoom: 19,
            }).addTo(map);
          if (typeof config.routing_attribution === 'string')
            map.attributionControl.addAttribution(config.routing_attribution);
        })
        .catch((error) => {
          if (!disposed && error.name !== 'AbortError')
            mapStatus.textContent =
              sourceLabel + ' · Basemap unavailable; road calculations and coordinates remain usable.';
        });
    }
    function syncMarkers() {
      if (!map) return;
      const nodes = [...state.sites, ...state.demand],
        ids = new Set(nodes.map((node) => node.id));
      for (const [id, marker] of markers)
        if (!ids.has(id)) {
          marker.remove();
          markers.delete(id);
        }
      for (const node of nodes) {
        if (markers.has(node.id)) {
          markers.get(node.id).bindTooltip(el('span', node.id + ' · ' + node.name));
          continue;
        }
        const id = node.id,
          point = validPosition(node.position)
            ? [number(node.position[1]), number(node.position[0])]
            : [43.6532, -79.3832];
        const marker = global.L.marker(point, {
          draggable: true,
          keyboard: true,
          title: id + ' ' + node.name + '. Drag to move; coordinate fields provide keyboard editing.',
        }).addTo(map);
        marker.bindTooltip(el('span', id + ' · ' + node.name));
        marker.on('dragend', () => {
          // Resolve the live node by ID: reset may replace the scenario objects.
          const live = [...state.sites, ...state.demand].find((item) => item.id === id);
          if (!live) return;
          const point = marker.getLatLng();
          live.position = [Number(point.lng.toFixed(6)), Number(point.lat.toFixed(6))];
          for (let i = 0; i < 2; i++) coordinateInputs.get(id + ':' + i).value = live.position[i];
          positionChanged(live);
        });
        markers.set(id, marker);
      }
    }
    function drawMap(result) {
      if (!map) { tripControls.hidden = true; return; }
      syncMarkers();
      const opened = new Set(result?.opened || []),
        served = new Set();
      if (result)
        result.assignment.forEach((site, d) => {
          if (site >= 0 && number(state.demand[d].weight) > 0) served.add(d);
        });
      [...state.sites, ...state.demand].forEach((node, i) => {
        const isSite = i < state.sites.length,
          active = isSite ? opened.has(i) : served.has(i - state.sites.length),
          cls = isSite ? (active ? 'open' : 'closed') : active ? 'served' : 'unserved';
        if (validPosition(node.position))
          markers.get(node.id).setLatLng([number(node.position[1]), number(node.position[0])]);
        markers.get(node.id).setIcon(
          global.L.divIcon({
            className: 'facility-pin ' + cls + (!isSite ? ' demand' : ''),
            html: '<span>' + node.id + '</span>',
            iconSize: [34, 34],
            iconAnchor: [17, 17],
          }),
        );
      });
      setTripOptions(result);
    }
    function clearPath() {
      pathRequest?.abort();
      pathRequest = null;
      layers?.clearLayers();
      tripStatus.textContent = '';
      showTrip.disabled = false;
    }
    function setTripOptions(result) {
      const options = [];
      result?.assignment.forEach((site, d) => {
        if (site < 0 || number(state.demand[d].weight) === 0) return;
        const zone = d + state.sites.length;
        const hubId = state.sites[site].id, zoneId = state.demand[d].id;
        for (const [from, to, label] of [
          [site, zone, hubId + ' → ' + zoneId + ' · Outbound'],
          [zone, site, zoneId + ' → ' + hubId + ' · Return'],
        ]) {
          const option = el('option', label);
          option.value = from + '-' + to;
          options.push(option);
        }
      });
      const previous = tripSelect.value;
      tripSelect.replaceChildren(...options);
      const preferred = options.find(option => option.value === previous) || options[0];
      tripSelect.value = preferred?.value || '';
      tripControls.hidden = !preferred;
      if (!preferred || previous !== preferred.value) clearPath();
      updateTripSummary();
    }
    function updateTripSummary() {
      if (!tripSelect.value) { tripSummary.textContent = ''; return; }
      const [from, to] = tripSelect.value.split('-').map(Number);
      tripSummary.textContent = driveMetrics(state.distances[from][to], state.durations[from][to]) + ' estimated';
    }
    async function showRoadPath() {
      if (!tripSelect.value || !map || request || matrixSignature !== signature(state)) return;
      clearPath();
      const [from, to] = tripSelect.value.split('-').map(Number);
      const controller = new AbortController();
      pathRequest = controller;
      showTrip.disabled = true;
      tripStatus.textContent = 'Loading the driving route…';
      try {
        const response = await global.fetch('/api/maps/road-path', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ positions: positions(state), from_index: from, to_index: to,
            ...(routingDigest ? {routing_digest: routingDigest} : {}) }),
          signal: controller.signal,
        });
        const payload = await response.json();
        if (pathRequest !== controller) return;
        if (!response.ok) throw new Error(payload.error || 'Road geometry is unavailable. Try again.');
        if (payload.from_index !== from || payload.to_index !== to ||
            (routingDigest && payload.routing_digest !== routingDigest) ||
            !Number.isFinite(payload.distance_m) || !Number.isFinite(payload.duration_s) ||
            Math.abs(payload.distance_m - state.distances[from][to]) > 1 ||
            Math.abs(payload.duration_s - state.durations[from][to]) > 1 ||
            !Array.isArray(payload.coordinates) || payload.coordinates.length < 2 ||
            !payload.coordinates.every(point => validPosition(point) && point.every(Number.isFinite)))
          throw new Error('The road path does not match the trip estimates. Recalculate road times and try again.');
        const points = payload.coordinates.map(([lon, lat]) => [lat, lon]);
        global.L.polyline(points, { color: '#1a5f88', weight: 5, opacity: 0.9,
          className: 'facility-road-path', interactive: false }).addTo(layers);
        map.fitBounds(points, {padding: [45, 45], maxZoom: 16, animate: false});
        tripStatus.textContent = 'Driving route shown on map.';
      } catch (error) {
        if (pathRequest === controller) tripStatus.textContent = error.message || 'Could not load this road route.';
      } finally {
        if (pathRequest === controller) { pathRequest = null; showTrip.disabled = false; }
      }
    }
    async function recalculate() {
      const nodes = [...state.sites, ...state.demand];
      if (nodes.some((node) => !validPosition(node.position))) {
        mapStatus.textContent = 'Enter valid longitude and latitude for all locations before recalculating.';
        return;
      }
      if (request) request.abort();
      const id = ++requestId,
        requested = positions(state),
        requestedSignature = signature(state);
      request = new AbortController();
      update();
      mapStatus.textContent = 'Requesting directed road times for ' + nodes.length + ' locations…';
      try {
        const response = await global.fetch('/api/maps/travel-times', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ positions: requested }),
          signal: request.signal,
        });
        const payload = await response.json();
        if (!response.ok)
          throw new Error(payload.error || payload.message || 'Road service is unavailable. Try again.');
        const matrix = validateResponse(payload, requested);
        if (disposed || id !== requestId || requestedSignature !== signature(state)) return;
        Object.assign(state, matrix);
        routingDigest = payload.routing_digest;
        matrixSignature = requestedSignature;
        sourceLabel =
          'Refreshed OSRM road snapshot · ' +
          new Date().toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
        request = null;
        update();
      } catch (error) {
        if (disposed || id !== requestId || error.name === 'AbortError') return;
        request = null;
        update();
        mapStatus.textContent = 'Road times were not updated. ' + error.message;
      }
    }
    function metric(label, value, note) {
      const box = el('div', undefined, 'problem-card');
      box.append(el('span', label), el('strong', value));
      if (note) box.append(el('small', note));
      metrics.append(box);
    }
    function update() {
      const stale = matrixSignature !== signature(state),
        pending = Boolean(request);
      recalc.disabled = pending;
      recalc.textContent = pending ? 'Calculating road times…' : 'Recalculate road times';
      metrics.replaceChildren();
      assignmentContent.replaceChildren();
      status.classList.remove('request-error');
      if (stale || pending) {
        status.textContent = pending
          ? 'Road times are being recalculated. Coverage results will appear when the matching matrix is ready.'
          : matrixSignature === null ? 'Recalculate road times to solve these locations.' : 'Locations changed. Recalculate road times to solve this scenario.';
        mapStatus.textContent = stale ? 'Road matrix is out of date. Previous results are hidden.' : sourceLabel;
        drawMap(null);
        return;
      }
      const result = solve(state);
      if (result.status !== 'ok' && result.status !== 'infeasible') {
        status.classList.add('request-error');
        status.textContent = result.errors.join(' ');
        mapStatus.textContent = sourceLabel;
        drawMap(null);
        return;
      }
      const diagnostic = result.status === 'infeasible';
      assignmentHeading.textContent = diagnostic ? 'Best achievable partial plan · full coverage infeasible' : 'Service plan';
      const percent = result.totalDemand === 0 ? 100 : (result.covered / result.totalDemand) * 100;
      if (diagnostic) status.classList.add('request-error');
      status.textContent = diagnostic ? result.message :
        'Exact classical search · ' +
        result.evaluated +
        ' assignments checked · ' +
        (result.totalDemand === 0
          ? 'No visits requested.'
          : result.covered < result.totalDemand
            ? result.totalDemand - result.covered + ' visits/day remain unserved under these limits.'
            : 'All requested visits are covered.');
      mapStatus.textContent = sourceLabel + (diagnostic ? ' · Diagnostic partial plan; the full-coverage requirement is not satisfied.' : ' · Driving profile, without live traffic.');
      metric(
        diagnostic ? 'Best achievable visits covered' : 'Daily visits covered',
        result.covered + ' / ' + result.totalDemand,
        percent.toFixed(0) + '% coverage',
      );
      metric(
        'Open hubs',
        result.opened.length + ' / ' + state.maxSites,
        money(result.opening) + ' opening cost/day',
      );
      metric(
        'Daily road travel',
        result.roadKm.toFixed(2) + ' km',
        (result.roadHours * 60).toFixed(1) + ' min estimated (' + result.roadHours.toFixed(2) + ' h)',
      );
      metric(
        'Driving + opening',
        money(result.cost) + '/day',
        money(number(state.budget) - result.opening) + ' opening budget left',
      );
      const table = el('table', undefined, 'problem-table');
      heading(table, ['Demand zone', 'Visits/day', 'Assigned hub', 'Outbound · road km & est. time', 'Return · road km & est. time', 'Why unserved']);
      const body = el('tbody');
      state.demand.forEach((zone, d) => {
        const s = result.assignment[d],
          zoneIndex = d + state.sites.length,
          weight = number(zone.weight),
          row = el('tr'),
          th = el('th', zone.id + ' · ' + zone.name);
        th.scope = 'row';
        row.append(
          th,
          serviceCell('Visits/day', String(weight)),
          serviceCell('Assigned hub', s >= 0 ? state.sites[s].id : '—'),
          serviceCell('Outbound', s >= 0 ? driveMetrics(state.distances[s][zoneIndex], state.durations[s][zoneIndex]) : '—'),
          serviceCell('Return', s >= 0 ? driveMetrics(state.distances[zoneIndex][s], state.durations[zoneIndex][s]) : '—'),
        );
        const reason = weight === 0 ? 'No demand' : result.eligible[d].length === 0
          ? 'No eligible hub within road-time limit' : 'Budget, capacity or site-count tradeoff';
        const reasonCell = serviceCell('Why unserved', s >= 0 ? '—' : reason);
        if (s >= 0) reasonCell.className = 'facility-served-reason';
        row.append(reasonCell);
        if (s < 0 && weight > 0) row.classList.add('facility-unserved-row');
        body.append(row);
      });
      table.append(body);
      assignmentContent.append(table);
      const utilization = el('div', undefined, 'facility-utilization');
      state.sites.forEach((site, i) => {
        const row = el('div', undefined, 'facility-utilization-row'),
          fill = el('span');
        fill.style.width = Math.min(100, (result.loads[i] / Math.max(1, number(site.capacity))) * 100) + '%';
        const track = el('div', undefined, 'facility-capacity-track');
        track.append(fill);
        row.append(
          el('span', site.id + (result.opened.includes(i) ? ' open' : ' unused')),
          track,
          el('span', result.loads[i] + ' / ' + site.capacity + ' visits'),
        );
        utilization.append(row);
      });
      assignmentContent.append(el('h3', 'Hub capacity'), utilization);
      assignmentContent.append(
        el(
          'p',
          result.baseline
            ? 'Nearest-hub comparison: ' +
                result.baseline.covered +
                ' visits covered; ' +
                money(result.baseline.cost) +
                '/day under the same limits.'
            : 'Assigning every zone to its nearest eligible hub violates capacity, budget or site-count limits. The optimized plan checks those limits explicitly.',
          'hint',
        ),
      );
      drawMap(result);
    }
    renderControls();
    renderEditors();
    initMap();
    update();
    return () => {
      disposed = true;
      requestId++;
      request?.abort();
      configRequest?.abort();
      clearPath();
      if (map) {
        map.remove();
        map = null;
      }
      container.classList.remove('facility-view', 'domain-view');
      container.replaceChildren();
    };
  }
  global.OptimizationViews ||= {};
  global.OptimizationViews.facility = { mount };
  if (typeof module !== 'undefined')
    module.exports = { seed, prepareInputs, validate, validateMatrix, validateResponse, solve, evaluate, SNAPSHOT };
})(typeof window === 'undefined' ? globalThis : window);
