/* Pure schedule replay helpers; time is minutes after start_hour. */
(function(root) {
  function depotsFor(sim) {
    return Object.hasOwn(sim, 'depots') ? sim.depots : [{id: 'P1', position: sim.depot}];
  }
  function depotFor(sim, van) {
    return Object.hasOwn(sim, 'depots') ? sim.depots.find(depot => depot.id === van.depot_id)?.position : sim.depot;
  }
  const lengthCache = new WeakMap();
  function geographicPointAt(path, fraction) {
    if (fraction <= 0) return [...path[0]];
    if (fraction >= 1) return [...path.at(-1)];
    let lengths = lengthCache.get(path);
    if (!lengths) {
      lengths = [0];
      for (let i = 1; i < path.length; i++) {
        const [x, y] = path[i - 1], [nx, ny] = path[i], rad = Math.PI / 180;
        const a = Math.sin((ny - y) * rad / 2) ** 2 + Math.cos(y * rad) * Math.cos(ny * rad) * Math.sin((nx - x) * rad / 2) ** 2;
        lengths.push(lengths.at(-1) + 6371008.8 * 2 * Math.atan2(Math.sqrt(a), Math.sqrt(Math.max(0, 1 - a))));
      }
      lengthCache.set(path, lengths);
    }
    const target = Math.max(0, Math.min(1, fraction)) * lengths.at(-1);
    let index = 1;
    while (index < lengths.length - 1 && lengths[index] < target) index++;
    const span = lengths[index] - lengths[index - 1];
    const part = span ? (target - lengths[index - 1]) / span : 0;
    return path[index - 1].map((value, axis) => value + (path[index][axis] - value) * part);
  }
  function stateAt(van, minute, depot, geographic = false) {
    let position=depot, battery=van.initial_battery_kwh, status='At depot';
    for (const e of van.events) {
      if(minute<e.start_min) break;
      const fraction=e.end_min===e.start_min?1:Math.max(0,Math.min(1,(minute-e.start_min)/(e.end_min-e.start_min)));
      battery=e.battery_start_kwh+(e.battery_end_kwh-e.battery_start_kwh)*fraction;
      position=e.position;
      if(e.type==='drive') {
        if (geographic) position = geographicPointAt(e.path, fraction);
        else {
          const segment=fraction*(e.path.length-1), i=Math.min(Math.floor(segment),e.path.length-2);
          const a=e.path[i],b=e.path[i+1],t=segment-i;
          position=[a[0]+(b[0]-a[0])*t,a[1]+(b[1]-a[1])*t];
        }
      }
      status={drive:'Driving',wait:'Waiting',charge:`Charging ${e.charger_id}`,deliver:`Delivering ${e.delivery_id}`}[e.type]||e.type;
      if(minute<e.end_min) return {position,battery,status};
      status='Idle';
    }
    if(position[0]===depot[0]&&position[1]===depot[1])status='At depot';
    return {position,battery,status};
  }
  function deliveredAt(sim,minute) {
    return new Set(sim.vans.flatMap(v=>v.events.filter(e=>e.type==='deliver'&&e.end_min<=minute&&e.end_min<=sim.duration_min).map(e=>e.delivery_id)));
  }
  function roadItinerary(sim, van) {
    if (sim?.map?.kind !== 'geographic') return null;
    const legs = van.events.filter(event => event.type === 'drive');
    let kilometers = 0, minutes = 0;
    for (const leg of legs) {
      const duration = leg.end_min - leg.start_min;
      if (!Number.isFinite(leg.distance_km) || leg.distance_km < 0 || !Number.isFinite(duration) || duration < 0) return null;
      kilometers += leg.distance_km;
      minutes += duration;
    }
    return {legs, kilometers, minutes};
  }
  function validate(sim) {
    if(!sim||sim.kind!=='ev_fleet'||!Array.isArray(sim.vans)||!sim.vans.length||sim.vans.length>6||!Array.isArray(sim.chargers)||!Array.isArray(sim.deliveries)) return false;
    const finite=x=>typeof x==='number'&&Number.isFinite(x);
    const geographic=sim.map?.kind==='geographic';
    if(geographic ? sim.grid!==undefined||sim.map.profile!=='driving'||!/^[a-f0-9]{64}$/.test(sim.map.routing_digest)||!Array.isArray(sim.depots)
      : !sim.grid||!Number.isInteger(sim.grid.width)||!Number.isInteger(sim.grid.height)||sim.grid.width<2||sim.grid.height<2||sim.grid.width>50||sim.grid.height>50) return false;
    const point=p=>Array.isArray(p)&&p.length===2&&(geographic
      ? p.every(finite)&&p[0]>=-180&&p[0]<=180&&p[1]>=-85&&p[1]<=85
      : p.every(Number.isInteger)&&p[0]>=0&&p[1]>=0&&p[0]<sim.grid.width&&p[1]<sim.grid.height);
    if(!finite(sim.duration_min)||sim.duration_min<=0||!finite(sim.start_hour)||!finite(sim.battery_capacity_kwh)||sim.battery_capacity_kwh<=0||!finite(sim.reserve_pct)) return false;
    const depots=depotsFor(sim);
    if(Object.hasOwn(sim,'depots')&&Object.hasOwn(sim,'depot')) return false;
    if(!Array.isArray(depots)||!depots.length||depots.length>6||depots.some(d=>!d||typeof d.id!=='string'||!d.id.trim()||!point(d.position))||new Set(depots.map(d=>d.id)).size!==depots.length) return false;
    if(!sim.vans.every(v=>point(depotFor(sim,v)))) return false;
    if(!sim.chargers.every(c=>point(c.position))||!sim.deliveries.every(d=>point(d.position))) return false;
    return sim.vans.every(v=>finite(v.initial_battery_kwh)&&Array.isArray(v.events)&&v.events.every(e=>{
      if(!['drive','wait','charge','deliver'].includes(e.type)||!finite(e.start_min)||!finite(e.end_min)||e.start_min<0||e.end_min<e.start_min||!finite(e.battery_start_kwh)||!finite(e.battery_end_kwh)||!point(e.position)) return false;
      return e.type!=='drive'||(Array.isArray(e.path)&&e.path.length>=2&&e.path.length<=20000&&e.path.every(point)&&(geographic
        ? typeof e.from_id==='string'&&typeof e.to_id==='string'&&finite(e.distance_km)&&e.distance_km>=0
        : e.path.every((p,i)=>i===0||Math.abs(p[0]-e.path[i-1][0])+Math.abs(p[1]-e.path[i-1][1])===1)));
    }));
  }
  const api={stateAt,deliveredAt,validate,depotsFor,depotFor,geographicPointAt,roadItinerary};
  if(typeof module!=='undefined')module.exports=api;else root.FleetReplay=api;
})(typeof globalThis!=='undefined'?globalThis:this);
