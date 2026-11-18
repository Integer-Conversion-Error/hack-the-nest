/* Draw the verified replay in the browser. Only vehicles and markers change during playback. */
(function (root) {
  'use strict';

  const palette = {
    background: '#f4f5f3',
    parcel: '#eceee8',
    street: '#fcfdfb',
    curb: '#c2cbc4',
    block: '#e6e8e1',
    blockEdge: '#d5d9d0',
    building: '#d8ded5',
    park: '#e1e8da',
    trees: '#c9d8c4',
    surface: '#fff',
    labelBorder: '#bec8c2',
    depot: '#455e64',
    charger: '#1e5147',
    charge: '#42724f',
    chargeFill: '#e1ebdf',
    delivered: '#285941',
    deliveredFill: '#e7eee2',
    pending: '#52636a',
    vehicleEdge: '#303e40',
    windshield: '#526970',
  };

  const clamp = (value, min, max) => Math.max(min, Math.min(max, value));
  const smooth = fraction => fraction * fraction * (3 - 2 * fraction);

  function headingAlong(path, progress) {
    const segments = path.length - 1;
    const position = clamp(progress, 0, segments);
    const index = Math.min(Math.floor(position), segments - 1);
    const within = position - index;
    const angle = segment => {
      const [x, y] = path[segment];
      const [nextX, nextY] = path[segment + 1];
      return Math.atan2(y - nextY, nextX - x);
    };
    const interpolate = (from, to, fraction) => {
      const difference = to - from;
      const turn = Math.atan2(Math.sin(difference), Math.cos(difference));
      return from + turn * smooth(fraction);
    };
    const turnWindow = 0.22;
    if (index > 0 && within < turnWindow) {
      return interpolate(angle(index - 1), angle(index), (within + turnWindow) / (2 * turnWindow));
    }
    if (index < segments - 1 && within > 1 - turnWindow) {
      return interpolate(angle(index), angle(index + 1), (within - 1 + turnWindow) / (2 * turnWindow));
    }
    return angle(index);
  }

  function headingAt(drives, minute) {
    let drive = drives[0];
    for (const candidate of drives) {
      if (candidate.start_min > minute) break;
      drive = candidate;
    }
    if (!drive) return 0;
    const duration = drive.end_min - drive.start_min;
    const fraction = duration ? (minute - drive.start_min) / duration : 1;
    return headingAlong(drive.path, fraction * (drive.path.length - 1));
  }

  function layout(grid, width, height) {
    const margin = Math.min(68, Math.min(width, height) * 0.13);
    const columns = grid.width - 1;
    const rows = grid.height - 1;
    const cell = Math.min((width - margin * 2) / columns, (height - margin * 2) / rows);
    const left = (width - columns * cell) / 2;
    const top = (height - rows * cell) / 2;
    return {
      width, height, cell, left, top,
      right: width - left,
      bottom: height - top,
      point: ([x, y]) => [left + x * cell, top + (rows - y) * cell],
    };
  }

  function rounded(context, x, y, width, height, radius, fill, stroke) {
    context.beginPath();
    context.roundRect(x, y, width, height, radius);
    if (fill) {
      context.fillStyle = fill;
      context.fill();
    }
    if (stroke) {
      context.strokeStyle = stroke;
      context.lineWidth = 1;
      context.stroke();
    }
  }

  function circle(context, x, y, radius, fill, stroke, width = 1) {
    context.beginPath();
    context.arc(x, y, radius, 0, Math.PI * 2);
    if (fill) {
      context.fillStyle = fill;
      context.fill();
    }
    if (stroke) {
      context.strokeStyle = stroke;
      context.lineWidth = width;
      context.stroke();
    }
  }

  function line(context, points, color, width = 1) {
    context.beginPath();
    context.moveTo(...points[0]);
    for (const point of points.slice(1)) context.lineTo(...point);
    context.strokeStyle = color;
    context.lineWidth = width;
    context.stroke();
  }

  function streetPath(grid, view) {
    const path = new root.Path2D();
    for (let x = 0; x < grid.width; x += 1) {
      path.moveTo(...view.point([x, 0]));
      path.lineTo(...view.point([x, grid.height - 1]));
    }
    for (let y = 0; y < grid.height; y += 1) {
      path.moveTo(...view.point([0, y]));
      path.lineTo(...view.point([grid.width - 1, y]));
    }
    return path;
  }

  function drawBase(context, grid, view) {
    const { width, height, cell, left, top, right, bottom } = view;
    context.fillStyle = palette.background;
    context.fillRect(0, 0, width, height);

    const inset = Math.min(20, cell * 0.45);
    rounded(context, left - inset, top - inset, right - left + inset * 2,
      bottom - top + inset * 2, Math.min(3, inset), palette.parcel, palette.blockEdge);

    const pavement = clamp(cell * 0.29, 0.75, 18);
    const blockInset = pavement * 0.7;
    const blockSize = cell - blockInset * 2;
    if (blockSize >= 6) {
      for (let x = 0; x < grid.width - 1; x += 1) {
        for (let y = 0; y < grid.height - 1; y += 1) {
          const px = left + x * cell + blockInset;
          const py = top + y * cell + blockInset;
          const green = (x + y * 3) % 5 === 2;
          rounded(context, px, py, blockSize, blockSize, Math.min(2, blockSize * 0.14),
            green ? palette.park : palette.block, palette.blockEdge);
          if (blockSize > 23) {
            const unit = blockSize / 5;
            if (green) {
              circle(context, px + unit * 1.5, py + unit * 1.5, unit * 0.55, palette.trees);
              circle(context, px + unit * 3.4, py + unit * 2.7, unit * 0.7, palette.trees);
              circle(context, px + unit * 1.8, py + unit * 3.4, unit * 0.45, palette.trees);
            } else {
              rounded(context, px + unit, py + unit, unit * 3, unit * 1.1, 2, palette.building);
              rounded(context, px + unit, py + unit * 2.7, unit * 1.25, unit * 1.3, 2, palette.building);
              rounded(context, px + unit * 2.75, py + unit * 2.7, unit * 1.25, unit * 1.3, 2, palette.building);
            }
          }
        }
      }
    }

    const streets = streetPath(grid, view);
    context.lineCap = 'round';
    context.lineJoin = 'round';
    context.strokeStyle = palette.curb;
    context.lineWidth = pavement + clamp(cell * 0.1, 0.5, 1.5);
    context.stroke(streets);
    context.strokeStyle = palette.street;
    context.lineWidth = pavement;
    context.stroke(streets);
    if (cell >= 27) {
      context.setLineDash([2, 5]);
      context.strokeStyle = '#d1d9d2';
      context.lineWidth = 1;
      context.stroke(streets);
      context.setLineDash([]);
    }
  }

  function buildRoutes(vans, view, spacing) {
    return vans.map((van, index) => {
      const route = new root.Path2D();
      const offset = spacing * (index - (vans.length - 1) / 2);
      for (const drive of van.drives) {
        drive.path.forEach((position, step) => {
          const [x, y] = view.point(position);
          if (step === 0) route.moveTo(x + offset, y + offset);
          else route.lineTo(x + offset, y + offset);
        });
      }
      return route;
    });
  }

  function placeVehicles(positions, view, marker) {
    if (positions.length <= 1) return positions;
    const separationDistance = marker * 3.1;
    // Leave room for the IDs and edge padding when the whole fleet shares a stop.
    const maxOffset = Math.min(marker * 1.5 / Math.sin(Math.PI / positions.length),
      Math.max(0, Math.min(view.left, view.top) - marker * 2));
    const startAngle = -Math.PI / 2;
    return positions.map((point, index) => {
      let nearest = separationDistance;
      positions.forEach((other, otherIndex) => {
        if (index !== otherIndex) nearest = Math.min(nearest, Math.hypot(point[0] - other[0], point[1] - other[1]));
      });
      const strength = smooth(1 - nearest / separationDistance);
      const angle = startAngle + index * Math.PI * 2 / positions.length;
      const offset = strength * maxOffset;
      return [point[0] + Math.cos(angle) * offset, point[1] + Math.sin(angle) * offset];
    });
  }

  function label(context, text, x, y, color, size) {
    context.font = '600 ' + size + 'px Inter, "Segoe UI", sans-serif';
    context.textAlign = 'center';
    context.textBaseline = 'middle';
    const pad = size * 0.45;
    const width = context.measureText(text).width + pad * 2;
    const left = x - width / 2;
    const top = y - size * 0.69;
    const height = size * 1.4;
    rounded(context, left, top, width, height, 2, palette.surface, palette.labelBorder);
    context.fillStyle = color;
    context.fillText(text, x, y + 0.25);
    return { left, top, right: left + width, bottom: top + height };
  }

  function drawStop(context, item, point, marker, target, view, depot) {
    const [anchorX, anchorY] = point;
    const { kind, id, done, active } = item;
    const tint = active ? palette.charge : done ? palette.delivered
      : kind === 'charger' ? palette.charger : kind === 'depot' ? palette.depot : palette.pending;
    const fill = active ? palette.chargeFill : done ? palette.deliveredFill : palette.surface;
    const badge = Math.max(5, marker * 0.87);
    const chargingPoint = kind === 'charger';
    let side = anchorX > view.width / 2 ? -1 : 1;
    let direction = anchorY < marker * 3.9 ? 1 : -1;
    // Aim nearby charger labels away from the depot, where the vehicle markers gather.
    if (chargingPoint) {
      const dx = anchorX - depot[0];
      const dy = anchorY - depot[1];
      if (Math.hypot(dx, dy) <= Math.max(view.cell * 1.05, marker * 4)) {
        if (Math.abs(dx) > 1 && Math.min(anchorX, view.width - anchorX) > marker * 4.7) side = Math.sign(dx);
        if (Math.abs(dy) > 1 && Math.min(anchorY, view.height - anchorY) > marker * 2.5) direction = Math.sign(dy);
      }
    }
    const x = anchorX + (chargingPoint ? side * marker * 1.35 : 0);
    const y = anchorY + direction * marker * (chargingPoint ? 1.25 : 1.75);
    line(context, [[x, y], point], tint);
    circle(context, anchorX, anchorY, marker * 0.23, tint);

    circle(context, x, y, badge, fill, tint, active ? 1.8 : 1.35);

    const s = badge * 0.45;
    if (kind === 'depot') {
      line(context, [[x - s, y + s], [x - s, y - s * 0.2], [x, y - s],
        [x + s, y - s * 0.2], [x + s, y + s], [x - s, y + s]], tint, 1.4);
    } else if (kind === 'charger') {
      line(context, [[x + s * 0.3, y - s], [x - s * 0.65, y + s * 0.22], [x + s * 0.55, y + s * 0.1],
        [x - s * 0.28, y + s]], tint, 1.55);
    } else {
      if (done) {
        line(context, [[x - s, y], [x - s * 0.2, y + s * 0.62], [x + s, y - s * 0.62]], tint, 1.5);
      } else {
        rounded(context, x - s, y - s * 0.75, s * 2, s * 1.5, 1.2, null, tint);
        line(context, [[x, y - s * 0.75], [x, y + s * 0.04]], tint);
      }
    }

    const caption = kind === 'depot' ? (id || 'Depot') : id;
    const captionX = chargingPoint ? x + side * marker * 1.9 : x;
    const captionY = chargingPoint ? y : y + direction * (badge + Math.max(7, marker * 0.7));
    const captionBounds = label(context, caption, captionX, clamp(captionY, 11, view.height - 11),
      tint, clamp(marker * 0.9, 9, 13));
    if (target) {
      target.x = x;
      target.y = y;
      target.radius = Math.max(13, badge + 6);
      target.caption = captionBounds;
    }
  }

  function drawVehicle(context, vehicle, point, marker, minute, selected) {
    const [x, y] = point;
    const tint = vehicle.color;
    if (selected) {
      circle(context, x, y, marker * 1.28, palette.surface, tint, 1.4);
    }
    context.save();
    context.translate(x, y);
    context.rotate(headingAt(vehicle.drives, minute));

    const length = marker * 2.05;
    const width = marker * 1.15;
    const corner = width * 0.22;
    rounded(context, -length / 2, -width / 2, length, width, corner, palette.surface, palette.vehicleEdge);
    rounded(context, -length / 2 + 1, -width / 2 + 1,
      length * 0.63, width - 2, Math.max(1, corner - 1), tint);
    rounded(context, length * 0.14, -width / 2 + 1,
      length * 0.32, width - 2, Math.max(1, corner - 1), '#e6eae8');
    rounded(context, length * 0.30, -width * 0.26, Math.max(1, length * 0.1),
      width * 0.52, 1, palette.windshield);
    context.restore();
    return label(context, vehicle.van.id, x, y + marker * 1.25, tint, clamp(marker * 0.85, 8, 12));
  }

  function create(canvas, { colors, onInspect,
    motionAllowed = () => !root.matchMedia?.('(prefers-reduced-motion: reduce)')?.matches }) {
    const context = canvas.getContext('2d', { alpha: false });
    const background = canvas.ownerDocument.createElement('canvas');
    const baseContext = background.getContext('2d', { alpha: false });
    let simulation = null;
    let vehicles = [];
    let routePaths = [];
    let routeWidth = 1.65;
    let hitTargets = [];
    let view = null;
    let density = 0;
    let resized = true;
    let lastDraw = null;

    function setSimulation(sim) {
      simulation = sim;
      vehicles = (sim?.vans || []).map((van, index) => ({
        van,
        color: colors[index % colors.length],
        drives: van.events.filter(event => event.type === 'drive'),
      }));
      hitTargets = [];
      lastDraw = null;
      resized = true;
    }

    function resize(scale) {
      const { width, height } = canvas.getBoundingClientRect();
      if (!width || !height) return false;
      density = scale;
      resized = false;
      view = layout(simulation.grid, width, height);
      canvas.width = background.width = Math.round(width * scale);
      canvas.height = background.height = Math.round(height * scale);
      baseContext.setTransform(scale, 0, 0, scale, 0, 0);
      drawBase(baseContext, simulation.grid, view);
      // Fit all route lines into a share of the gap between street intersections.
      const spacing = Math.min(2.5, view.cell * 0.42 / Math.max(1, vehicles.length - 1));
      routePaths = buildRoutes(vehicles, view, spacing);
      routeWidth = clamp(spacing * 0.9, 0.65, 1.65);
      return true;
    }

    function draw(minute, selectedVan = null, playing = false) {
      if (!simulation) return;
      lastDraw = [minute, selectedVan, playing];
      const scale = clamp(root.devicePixelRatio || 1, 1, 2.5);
      if ((resized || density !== scale) && !resize(scale)) return;
      context.setTransform(1, 0, 0, 1, 0, 0);
      context.drawImage(background, 0, 0);
      context.setTransform(scale, 0, 0, scale, 0, 0);
      context.lineCap = 'round';
      context.lineJoin = 'round';

      const highlight = vehicles.findIndex(({ van }) => van.id === selectedVan);
      const drawRoute = (route, index) => {
        const selected = index === highlight;
        context.strokeStyle = vehicles[index].color;
        context.globalAlpha = selected ? 1 : highlight >= 0 ? 0.23 : 0.72;
        context.lineWidth = selected ? Math.max(1.6, routeWidth * 1.6) : routeWidth;
        context.stroke(route);
      };
      routePaths.forEach((route, index) => {
        if (index !== highlight) drawRoute(route, index);
      });
      if (highlight !== -1) drawRoute(routePaths[highlight], highlight);
      context.globalAlpha = 1;

      const marker = clamp(view.cell * 0.18, 7, 15);
      if (playing && motionAllowed()) {
        vehicles.forEach(vehicle => {
          const drive = vehicle.drives.find(event => event.start_min <= minute && minute < event.end_min);
          if (!drive) return;
          const lookback = Math.min(1.2, (drive.end_min - drive.start_min) * 0.18);
          for (const [index, fraction] of [1, 0.65, 0.3].entries()) {
            const at = Math.max(drive.start_min, minute - lookback * fraction);
            if (at === drive.start_min) continue;
            const point = view.point(root.FleetReplay.stateAt(vehicle.van, at, root.FleetReplay.depotFor(simulation, vehicle.van)).position);
            context.globalAlpha = selectedVan && vehicle.van.id !== selectedVan ? 0.1 : 0.13 + index * 0.08;
            circle(context, point[0], point[1], marker * (0.15 + index * 0.045), vehicle.color);
          }
        });
        context.globalAlpha = 1;
      }
      const positions = vehicles.map(({ van }) => view.point(root.FleetReplay.stateAt(van, minute, root.FleetReplay.depotFor(simulation, van)).position));
      const displayPositions = placeVehicles(positions, view, marker);
      displayPositions.forEach((point, index) => {
        const origin = positions[index];
        const distance = Math.hypot(point[0] - origin[0], point[1] - origin[1]);
        if (distance <= marker) return;
        context.globalAlpha = clamp((distance - marker) / marker, 0, 0.45);
        line(context, [origin, point], vehicles[index].color);
      });
      context.globalAlpha = 1;

      const delivered = root.FleetReplay.deliveredAt(simulation, minute);
      const activeChargers = new Set();
      for (const { van } of vehicles) {
        const active = van.events.find(event => event.start_min <= minute && minute < event.end_min);
        if (active?.type === 'charge') activeChargers.add(active.charger_id);
      }

      hitTargets = [];
      const depots = root.FleetReplay.depotsFor(simulation);
      const stop = item => {
        const target = item.kind === 'depot' ? null : { kind: item.kind, id: item.id };
        const point = view.point(item.position);
        const depot = depots.map(candidate => view.point(candidate.position)).reduce((nearest, candidate) =>
          Math.hypot(candidate[0] - point[0], candidate[1] - point[1]) < Math.hypot(nearest[0] - point[0], nearest[1] - point[1])
            ? candidate : nearest);
        drawStop(context, item, point, marker, target, view, depot);
        if (target) hitTargets.push(target);
      };
      depots.forEach(depot => stop({ ...depot, id: simulation.depots ? depot.id : undefined, kind: 'depot' }));
      simulation.chargers.forEach(charger => stop({
        ...charger, kind: 'charger', active: activeChargers.has(charger.id),
      }));
      simulation.deliveries.forEach(delivery => stop({
        ...delivery, kind: 'delivery', done: delivered.has(delivery.id),
      }));

      vehicles.forEach((vehicle, index) => {
        const [x, y] = displayPositions[index];
        const caption = drawVehicle(context, vehicle, [x, y], marker, minute, vehicle.van.id === selectedVan);
        hitTargets.push({ kind: 'van', id: vehicle.van.id, x, y, radius: Math.max(15, marker * 1.4), caption });
      });
    }

    function hitAt(event) {
      if (!view) return null;
      const bounds = canvas.getBoundingClientRect();
      if (!bounds.width || !bounds.height) return null;
      const x = (event.clientX - bounds.left) * view.width / bounds.width;
      const y = (event.clientY - bounds.top) * view.height / bounds.height;
      let nearest = null;
      let best = Infinity;
      for (const hit of hitTargets) {
        const { left, right, top, bottom } = hit.caption;
        const inCaption = x >= left && x <= right && y >= top && y <= bottom;
        const toMarker = Math.hypot(x - hit.x, y - hit.y);
        if (!inCaption && toMarker > hit.radius) continue;
        const toCaption = inCaption ? Math.hypot(x - (left + right) / 2, y - (top + bottom) / 2) : Infinity;
        const distance = Math.min(toMarker, toCaption);
        if (distance < best) {
          nearest = hit;
          best = distance;
        }
      }
      return nearest;
    }

    canvas.addEventListener('pointermove', event => {
      canvas.style.cursor = hitAt(event) ? 'pointer' : 'default';
    });
    canvas.addEventListener('click', event => {
      const hit = hitAt(event);
      if (hit) onInspect({ kind: hit.kind, id: hit.id });
    });
    if (root.ResizeObserver) {
      const observer = new root.ResizeObserver(() => {
        resized = true;
        if (lastDraw) draw(...lastDraw);
      });
      observer.observe(canvas);
    }

    return { setSimulation, draw };
  }

  root.FleetMap = { create };
})(globalThis);
