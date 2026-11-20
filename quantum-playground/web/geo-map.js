/* Leaflet adapters. Scenario and replay positions use [longitude, latitude]. */
(function (root) {
  'use strict';
  let configuration = null, configurationRequest = null;
  const defaults = ['#185f55', '#315b8f', '#8a4d27', '#75527c', '#a42b46', '#6c650d'];
  const round = position => position.map(value => Math.round(Number(value) * 1e6) / 1e6);
  const valid = position => Array.isArray(position) && position.length === 2 && position.every(value => value !== null && typeof value !== 'boolean' && String(value).trim() !== '' && Number.isFinite(Number(value))) &&
    Number(position[0]) >= -180 && Number(position[0]) <= 180 && Number(position[1]) >= -85 && Number(position[1]) <= 85;
  const latLng = position => [Number(position[1]), Number(position[0])];
  const positionOf = point => round([Math.min(180, Math.max(-180, point.lng)), Math.min(85, Math.max(-85, point.lat))]);

  function configure(value) { configuration = value; configurationRequest = null; }
  async function config() {
    if (configuration) return configuration;
    if (!configurationRequest) configurationRequest = root.fetch('/api/maps/config').then(async response => {
      if (!response.ok) throw Error('Map settings are unavailable.');
      configuration = await response.json(); return configuration;
    }).catch(error => { configurationRequest = null; throw error; });
    return configurationRequest;
  }
  function createSurface(container, { onStatus = () => {} } = {}) {
    const L = root.L;
    if (!L) {
      const message = container.ownerDocument.createElement('p'); message.className = 'geo-map-message';
      message.textContent = 'The map could not load. You can still enter location coordinates.'; container.replaceChildren(message);
      onStatus(message.textContent);
      return null;
    }
    container.classList.add('geo-map');
    const map = L.map(container, { zoomControl: true, attributionControl: true, scrollWheelZoom: false,
      minZoom: 2, maxZoom: 19, maxBounds: [[-85, -180], [85, 180]], maxBoundsViscosity: 1,
    }).setView([43.6532, -79.3832], 14);
    let destroyed = false;
    const messageControl = L.control({ position: 'topleft' });
    const message = container.ownerDocument.createElement('p'); message.className = 'geo-map-message'; message.setAttribute('role', 'status'); message.hidden = true;
    messageControl.onAdd = () => message; messageControl.addTo(map);
    const status = text => { if (destroyed) return; message.textContent = text; message.hidden = !text; onStatus(text); };
    config().then(settings => {
      if (destroyed) return;
      if (settings.routing_attribution) map.attributionControl?.addAttribution(settings.routing_attribution);
      if (!settings.tile_url) { status('Map tiles are unavailable. Markers and coordinate editing still work.'); return; }
      const tiles = L.tileLayer(settings.tile_url, { attribution: settings.tile_attribution || '',
        minZoom: settings.min_zoom ?? 2, maxZoom: settings.max_zoom ?? 19, crossOrigin: true,
      }).addTo(map);
      tiles.on('tileerror', () => status('Map tiles could not load. You can still use markers and location coordinates.'));
    }).catch(() => status('Map tiles are unavailable. You can still use markers and location coordinates.'));
    return {
      map, L, status,
      invalidateSize() { if (!destroyed) map.invalidateSize({ animate: false, pan: false }); },
      destroy() { destroyed = true; map.remove(); },
    };
  }

  function icon(L, document, kind, id, color, selected = false, count = 1, done = false) {
    const body = document.createElement('div'); body.className = 'geo-marker ' + kind + (selected ? ' selected' : '') + (done ? ' delivered' : '');
    body.style.setProperty('--marker-color', color);
    const shape = document.createElement('span'); shape.className = 'geo-marker-shape';
    shape.textContent = kind === 'chargers' || kind === 'charger' ? 'ϟ' : kind === 'depots' || kind === 'depot' ? 'P' : kind === 'van' ? 'V' : '•';
    const label = document.createElement('span'); label.className = 'geo-marker-label'; label.textContent = id + (count > 1 ? ' +' + (count - 1) : '');
    body.append(shape, label);
    return L.divIcon({ className: 'geo-marker-icon', html: body, iconSize: [44, 48], iconAnchor: [22, 24] });
  }

  function createEditor(container, { onSelect = () => {}, onMove = () => {}, onPlace = () => {}, onStatus = () => {} } = {}) {
    const surface = createSurface(container, { onStatus });
    if (!surface) return { setScenario() {}, getCenter: () => [-79.3832, 43.6532], setView() {}, invalidateSize() {}, destroy() {} };
    const { map, L } = surface, document = container.ownerDocument;
    const layer = L.layerGroup().addTo(map);
    let initialized = false, busy = false, placing = false;
    map.on('click', event => { if (!busy && placing) onPlace(positionOf(event.latlng)); });
    function setScenario(scenario, options = {}) {
      busy = Boolean(options.busy); placing = Boolean(options.placing);
      const selected = options.selected;
      layer.clearLayers();
      const groups = new Map(), bounds = [];
      for (const kind of ['depots', 'chargers', 'deliveries']) for (const item of scenario[kind] || []) {
        if (!valid(item.position)) continue;
        const key = round(item.position).join(',');
        if (!groups.has(key)) groups.set(key, []); groups.get(key).push({ kind, item }); bounds.push(latLng(item.position));
      }
      for (const entries of groups.values()) {
        const active = entries.find(entry => entry.kind === selected?.kind && entry.item.id === selected?.id);
        const { kind, item } = active || entries[0];
        const owner = kind === 'deliveries' ? scenario.vehicles.findIndex(vehicle => vehicle.id === item.vehicle_id) : -1;
        const color = owner >= 0 ? defaults[owner % defaults.length] : kind === 'depots' ? '#455e64' : '#1e5147';
        const marker = L.marker(latLng(item.position), { draggable: !busy && !placing, keyboard: !busy,
          title: entries.map(entry => entry.item.id).join(', '), alt: item.id,
          icon: icon(L, document, kind, item.id, color, Boolean(active), entries.length),
        }).addTo(layer);
        marker.on('click', event => {
          if (busy) return;
          if (event.originalEvent) L.DomEvent.stopPropagation(event.originalEvent);
          if (placing) { onPlace(round(item.position)); return; }
          if (entries.length === 1) { onSelect(kind, item.id); return; }
          const chooser = document.createElement('div'); chooser.className = 'geo-overlap-chooser';
          for (const entry of entries) {
            const choice = document.createElement('button'); choice.type = 'button'; choice.textContent = entry.item.id;
            choice.onclick = () => { map.closePopup(); onSelect(entry.kind, entry.item.id); }; chooser.append(choice);
          }
          marker.bindPopup(chooser).openPopup();
        });
        marker.on('dragend', () => { if (!busy) onMove(kind, item.id, positionOf(marker.getLatLng())); });
        const target = marker.getElement();
        if (target) {
          target.setAttribute('data-geo-marker', item.id);
          target.setAttribute('aria-label', entries.map(entry => entry.item.id).join(', ') + ' at latitude ' + item.position[1] + ', longitude ' + item.position[0]);
          target.addEventListener('keydown', event => {
            if (busy) return;
            const delta = { ArrowLeft: [-0.0001, 0], ArrowRight: [0.0001, 0], ArrowUp: [0, 0.0001], ArrowDown: [0, -0.0001] }[event.key];
            if (!delta) return;
            event.preventDefault(); event.stopPropagation();
            const moved = round([Math.min(180, Math.max(-180, Number(item.position[0]) + delta[0])), Math.min(85, Math.max(-85, Number(item.position[1]) + delta[1]))]);
            onMove(kind, item.id, moved);
            container.querySelector('[data-geo-marker="' + item.id + '"]')?.focus();
          });
        }
      }
      if ((!initialized || options.fit) && bounds.length) { map.fitBounds(bounds, { padding: [38, 38], maxZoom: 15, animate: false }); initialized = true; }
      container.classList.toggle('is-placing', placing);
    }
    return { setScenario, getCenter: () => positionOf(map.getCenter()),
      setView(position, zoom = 16) { if (valid(position)) map.setView(latLng(position), zoom, { animate: false }); },
      invalidateSize: surface.invalidateSize, destroy: surface.destroy,
    };
  }

  function createReplay(container, { colors = defaults, onInspect = () => {} } = {}) {
    let surface = null, simulation = null, vehicleMarkers = [], deliveryMarkers = [], routes = [], lastSelection;
    const document = container.ownerDocument;
    function ensure() { if (!surface) surface = createSurface(container); return surface; }
    function setSimulation(value) {
      simulation = value;
      if (!value && !surface) return;
      if (!ensure()) return;
      const { map, L } = surface;
      for (const layer of [...vehicleMarkers, ...deliveryMarkers, ...routes]) map.removeLayer(layer.marker || layer);
      vehicleMarkers = []; deliveryMarkers = []; routes = [];
      lastSelection = undefined;
      if (!simulation) return;
      const bounds = [], addStop = (kind, item, color) => {
        const marker = L.marker(latLng(item.position), { keyboard: true, title: item.id,
          icon: icon(L, document, kind, item.id, color),
        }).addTo(map);
        if (kind !== 'depot') marker.on('click', () => onInspect({ kind, id: item.id }));
        bounds.push(latLng(item.position));
        if (kind === 'delivery') deliveryMarkers.push({ marker, item, color }); else routes.push(marker);
      };
      root.FleetReplay.depotsFor(simulation).forEach(item => addStop('depot', item, '#455e64'));
      simulation.chargers.forEach(item => addStop('charger', item, '#1e5147'));
      simulation.deliveries.forEach(item => addStop('delivery', item, '#52636a'));
      simulation.vans.forEach((van, index) => {
        const color = colors[index % colors.length];
        for (const event of van.events) if (event.type === 'drive') {
          const line = L.polyline(event.path.map(latLng), { color, weight: 3, opacity: 0.7, interactive: false }).addTo(map);
          routes.push({ marker: line, vanId: van.id }); bounds.push(...event.path.map(latLng));
        }
        const position = root.FleetReplay.depotFor(simulation, van);
        const marker = L.marker(latLng(position), { keyboard: true, zIndexOffset: 500, title: van.id,
          icon: icon(L, document, 'van', van.id, color),
        }).addTo(map);
        marker.on('click', () => onInspect({ kind: 'van', id: van.id }));
        vehicleMarkers.push({ marker, van, color });
      });
      if (bounds.length) map.fitBounds(bounds, { padding: [35, 35], maxZoom: 16, animate: false });
      surface.invalidateSize();
    }
    function draw(minute, selection = null) {
      if (!simulation || !surface) return;
      for (const vehicle of vehicleMarkers) {
        const state = root.FleetReplay.stateAt(vehicle.van, minute, root.FleetReplay.depotFor(simulation, vehicle.van), true);
        vehicle.marker.setLatLng(latLng(state.position));
        const selected = selection === vehicle.van.id;
        if (vehicle.selected !== selected) {
          vehicle.marker.setIcon(icon(surface.L, document, 'van', vehicle.van.id, vehicle.color, selected)); vehicle.selected = selected;
        }
      }
      const delivered = root.FleetReplay.deliveredAt(simulation, minute);
      for (const stop of deliveryMarkers) {
        const done = delivered.has(stop.item.id);
        if (stop.done !== done) { stop.marker.setIcon(icon(surface.L, document, 'delivery', stop.item.id, done ? '#285941' : stop.color, false, 1, done)); stop.done = done; }
      }
      if (lastSelection !== selection) {
        for (const route of routes) if (route.vanId) route.marker.setStyle({ opacity: !selection || selection === route.vanId ? 0.85 : 0.2, weight: selection === route.vanId ? 5 : 3 });
        lastSelection = selection;
      }
    }
    return { setSimulation, draw, invalidateSize() { surface?.invalidateSize(); }, destroy() { surface?.destroy(); surface = null; } };
  }
  const api = { configure, createEditor, createReplay, validPosition: valid, toLatLng: latLng, fromLatLng: positionOf };
  if (typeof module !== 'undefined') module.exports = api; else root.GeoMap = api;
})(typeof globalThis === 'undefined' ? this : globalThis);
