const assert = require('node:assert/strict');
const test = require('node:test');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const F = require('../web/problems/facility.js');

test('facility seed uses actual recorded Toronto positions and directed OSRM times', () => {
  const fixture = JSON.parse(fs.readFileSync(path.join(__dirname, 'fixtures/geographic-road-replay.json')));
  assert.deepEqual(F.SNAPSHOT.durations_s, fixture.routing.durations_s);
  assert.deepEqual(
    F.SNAPSHOT.positions,
    fixture.routing.nodes.map((node) => node.position),
  );
  const result = F.solve(F.seed());
  assert.equal(result.status, 'ok');
  assert.equal(result.covered, 44);
  assert.equal(result.opening, 100);
  assert.deepEqual(result.assignment, [2, 1, 1]);
  assert.equal(result.baseline, null); // Nearest hubs violate opening budget.
});

function simple() {
  const input = F.seed();
  input.sites.forEach((site) => {
    site.capacity = 100;
    site.cost = 1;
  });
  input.budget = 4;
  input.maxSites = 4;
  input.minutes = 1;
  input.durations = Array.from({ length: 7 }, (_, i) => Array.from({ length: 7 }, (_, j) => (i === j ? 0 : 20)));
  return input;
}

test('coverage has priority over cheap opening and zones are never split', () => {
  const input = simple();
  input.budget = 10;
  input.maxSites = 1;
  input.sites.forEach((site) => {
    site.enabled = false;
  });
  input.sites[0].enabled = true;
  input.sites[0].capacity = 20;
  input.demand.forEach((zone, i) => {
    zone.weight = [14, 18, 12][i];
  });
  const result = F.solve(input);
  assert.equal(result.covered, 18);
  assert.deepEqual(result.assignment, [-1, 0, -1]);
  input.sites[1].enabled = true;
  input.sites[1].cost = 10;
  input.sites[1].capacity = 44;
  const expanded = F.solve(input);
  assert.equal(expanded.covered, 44);
  assert.deepEqual(expanded.opened, [1]);
});

test('opening budget, hub count, directed threshold and return reachability are enforced', () => {
  const input = simple();
  input.sites.forEach((site) => {
    site.enabled = false;
  });
  input.sites[0].enabled = true;
  input.durations[0][4] = 61; // A fast return cannot substitute for the outbound journey.
  input.durations[4][0] = 1;
  input.durations[5][0] = null; // Outbound route alone is insufficient for roundtrip service.
  const result = F.solve(input);
  assert.equal(result.covered, 12);
  assert.deepEqual(result.assignment, [-1, -1, 0]);
  input.budget = 0;
  assert.equal(F.solve(input).covered, 0);
  const split = simple();
  split.sites.forEach((site) => {
    site.capacity = 20;
  });
  split.maxSites = 1;
  const one = F.solve(split);
  assert.equal(one.opened.length, 1);
  assert.equal(one.covered, 18);
});

test('equal coverage uses actual directed roundtrip operating cost as tie breaker', () => {
  const input = simple();
  input.maxSites = 1;
  input.sites[0].cost = 0;
  input.sites[1].cost = 1;
  input.sites[2].enabled = input.sites[3].enabled = false;
  for (let d = 4; d < 7; d++) {
    input.durations[0][d] = 10;
    input.durations[d][0] = 600;
    input.durations[1][d] = 20;
    input.durations[d][1] = 20;
  }
  const result = F.solve(input);
  assert.equal(result.covered, 44);
  assert.deepEqual(result.opened, [1]);
  assert.ok(Math.abs(result.roadHours - (44 * 40) / 3600) < 1e-9);
});

test('facility handles no enabled hubs or no demand without inventing coverage', () => {
  const input = F.seed();
  input.sites.forEach((site) => {
    site.enabled = false;
  });
  const none = F.solve(input);
  assert.equal(none.covered, 0);
  assert.equal(none.cost, 0);
  assert.deepEqual(none.opened, []);
  input.demand.forEach((zone) => {
    zone.weight = 0;
  });
  const empty = F.solve(input);
  assert.equal(empty.totalDemand, 0);
  assert.equal(empty.covered, 0);
  assert.equal(empty.evaluated, 1);
});

test('facility solves all 1,331 bounded assignments for ten sites with dynamic demand offsets', () => {
  const input = simple();
  for (let i = 4; i < 10; i++)
    input.sites.push({ ...input.sites[0], id: 'H' + (i + 1), position: [-79.38, 43.65] });
  input.maxSites = 3;
  input.durations = Array.from({ length: 13 }, (_, i) => Array.from({ length: 13 }, (_, j) => (i === j ? 0 : 20)));
  // Only site 10 is free, so the result proves the demand offset follows site count.
  input.sites[9].cost = 0;
  const result = F.solve(input);
  assert.equal(result.status, 'ok');
  assert.equal(result.evaluated, 1331);
  assert.equal(result.covered, 44);
  assert.deepEqual(result.opened, [9]);
  assert.deepEqual(result.assignment, [9, 9, 9]);
  input.sites.push({ ...input.sites[0], id: 'H11' });
  assert.equal(F.solve(input).status, 'invalid');
});

test('facility rejects malformed matrices, coordinates and incomplete road responses', () => {
  for (const modify of [
    (input) => {
      input.durations[0][1] = NaN;
    },
    (input) => {
      input.durations[0].pop();
    },
    (input) => {
      input.durations[0][0] = null;
    },
    (input) => {
      input.demand[0].position[0] = '';
    },
    (input) => {
      input.demand[0] = null;
    },
    (input) => {
      input.budget = false;
    },
  ]) {
    const input = F.seed();
    modify(input);
    assert.equal(F.solve(input).status, 'invalid');
  }
  const requested = F.SNAPSHOT.positions.map((p) => [...p]),
    response = { positions: requested, durations_s: F.SNAPSHOT.durations_s };
  assert.deepEqual(F.validateResponse(response, requested), F.SNAPSHOT.durations_s);
  assert.throws(
    () => F.validateResponse({ ...response, positions: [...requested].reverse() }, requested),
    /match/,
  );
  assert.throws(() => F.validateResponse({ ...response, durations_s: [] }, requested), /incomplete/);
});

function ui() {
  const markers = [],
    requests = [];
  class Element {
    constructor(tag) {
      this.tagName = tag;
      this.children = [];
      this.attrs = {};
      this.style = {};
      this.classList = { add() {}, remove() {} };
    }
    append(...children) {
      this.children.push(...children);
    }
    replaceChildren(...children) {
      this.children = children;
    }
    setAttribute(key, value) {
      this.attrs[key] = value;
    }
  }
  const doc = { createElement: (tag) => new Element(tag) },
    container = new Element('div');
  container.ownerDocument = doc;
  const map = { fitBounds() {}, attributionControl: { addAttribution() {} }, remove() {} };
  const L = {
    map: () => map,
    layerGroup: () => ({
      addTo() {
        return this;
      },
      clearLayers() {},
    }),
    marker: (point, options) => {
      const marker = {
        point,
        options,
        events: {},
        addTo() {
          return this;
        },
        bindTooltip() {},
        remove() {
          this.removed = true;
        },
        on(event, callback) {
          this.events[event] = callback;
        },
        setLatLng(point) {
          this.point = point;
        },
        getLatLng() {
          return { lat: this.point[0], lng: this.point[1] };
        },
        setIcon() {},
      };
      markers.push(marker);
      return marker;
    },
    polyline: () => ({ addTo() {} }),
    divIcon: (options) => options,
    tileLayer: () => ({ addTo() {} }),
  };
  const context = vm.createContext({
    window: {
      L,
      fetch(url, options) {
        if (url === '/api/maps/config') return Promise.resolve({ ok: true, json: async () => ({}) });
        let resolve;
        const promise = new Promise((done) => {
          resolve = done;
        });
        requests.push({ options, resolve });
        return promise;
      },
    },
    Intl,
    AbortController,
    console,
  });
  vm.runInContext(fs.readFileSync(path.join(__dirname, '../web/problems/facility.js'), 'utf8'), context);
  const cleanup = context.window.OptimizationViews.facility.mount(container);
  function all(node = container) {
    return [node, ...node.children.flatMap((child) => all(child))];
  }
  const find = (predicate) => {
    const element = all().find(predicate);
    assert.ok(element);
    return element;
  };
  return {
    container,
    markers,
    requests,
    cleanup,
    find,
    status: () => find((node) => node.className === 'facility-status').textContent,
    input: (label) => find((node) => node.attrs['aria-label'] === label),
    button: (label) => find((node) => node.tagName === 'button' && node.textContent === label),
  };
}
const flush = async () => {
  await new Promise((resolve) => setImmediate(resolve));
};

test('moving a facility pin invalidates results and a late road response cannot restore stale coverage', async () => {
  const view = ui(),
    longitude = view.input('H1 longitude');
  longitude.value = '-79.382';
  longitude.oninput();
  assert.match(view.status(), /Locations changed/);
  view.button('Recalculate road times').onclick();
  assert.match(view.status(), /being recalculated/);
  const first = view.requests[0];
  longitude.value = '-79.381';
  longitude.oninput();
  assert.equal(first.options.signal.aborted, true);
  const oldPositions = JSON.parse(first.options.body).positions;
  first.resolve({
    ok: true,
    json: async () => ({ positions: oldPositions, durations_s: F.SNAPSHOT.durations_s }),
  });
  await flush();
  assert.match(view.status(), /Locations changed/);
  view.button('Recalculate road times').onclick();
  const second = view.requests[1],
    freshPositions = JSON.parse(second.options.body).positions;
  second.resolve({
    ok: true,
    json: async () => ({ positions: freshPositions, durations_s: F.SNAPSHOT.durations_s }),
  });
  await flush();
  assert.match(view.status(), /Exact classical search/);
  view.cleanup();
});

test('reset aborts pending routing, restores original pins, and dragging afterward edits the live scenario', async () => {
  const view = ui();
  view.button('Recalculate road times').onclick();
  const pending = view.requests[0];
  view.markers[0].point = [43.66, -79.38];
  view.button('Reset Toronto example').onclick();
  assert.equal(pending.options.signal.aborted, true);
  assert.deepEqual(Array.from(view.markers[0].point), [43.6532, -79.3832]);
  assert.match(view.status(), /Exact classical search/);
  assert.equal(view.button('Recalculate road times').disabled, false);
  view.markers[0].point = [43.654, -79.384];
  view.markers[0].events.dragend();
  assert.equal(view.input('H1 longitude').value, -79.384);
  assert.match(view.status(), /Locations changed/);
  pending.resolve({
    ok: true,
    json: async () => ({ positions: F.SNAPSHOT.positions, durations_s: F.SNAPSHOT.durations_s }),
  });
  await flush();
  assert.match(view.status(), /Locations changed/);
  view.cleanup();
});

test('candidate topology requires refreshed matrix, permits ten sites, and resets to the saved four', async () => {
  const view = ui();
  for (let i = 0; i < 6; i++) view.button('+ Add candidate hub').onclick();
  assert.equal(view.button('+ Add candidate hub').disabled, true);
  assert.equal(view.input('Maximum open hubs').attrs.max, 10);
  assert.match(view.status(), /Locations changed/);
  view.button('Recalculate road times').onclick();
  const request = view.requests[0],
    requested = JSON.parse(request.options.body).positions;
  assert.equal(requested.length, 13);
  const durations = requested.map((_, i) => requested.map((_, j) => (i === j ? 0 : 20)));
  request.resolve({ ok: true, json: async () => ({ positions: requested, durations_s: durations }) });
  await flush();
  assert.match(view.status(), /Exact classical search/);
  view.button('Remove H10').onclick();
  assert.match(view.status(), /Locations changed/);
  assert.equal(view.input('Maximum open hubs').attrs.max, 9);
  view.button('Reset Toronto example').onclick();
  assert.match(view.status(), /Exact classical search/);
  assert.equal(view.input('Maximum open hubs').attrs.max, 4);
  // A reused default marker still resolves the reset scenario rather than an old object.
  view.markers[0].point = [43.655, -79.385];
  view.markers[0].events.dragend();
  assert.equal(view.input('H1 longitude').value, -79.385);
  assert.match(view.status(), /Locations changed/);
  view.cleanup();
});
