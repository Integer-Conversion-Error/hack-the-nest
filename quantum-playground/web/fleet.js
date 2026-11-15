/* Pure schedule replay helpers; time is minutes after start_hour. */
(function(root) {
  function stateAt(van, minute, depot) {
    let position=depot, battery=van.initial_battery_kwh, status='At depot';
    for (const e of van.events) {
      if(minute<e.start_min) break;
      const fraction=e.end_min===e.start_min?1:Math.max(0,Math.min(1,(minute-e.start_min)/(e.end_min-e.start_min)));
      battery=e.battery_start_kwh+(e.battery_end_kwh-e.battery_start_kwh)*fraction;
      position=e.position;
      if(e.type==='drive') {
        const segment=fraction*(e.path.length-1), i=Math.min(Math.floor(segment),e.path.length-2);
        const a=e.path[i],b=e.path[i+1],t=segment-i;
        position=[a[0]+(b[0]-a[0])*t,a[1]+(b[1]-a[1])*t];
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
  function validate(sim) {
    if(!sim||sim.kind!=='ev_fleet'||!Array.isArray(sim.vans)||!sim.vans.length||!Array.isArray(sim.chargers)||!Array.isArray(sim.deliveries)||!sim.grid) return false;
    const finite=x=>typeof x==='number'&&Number.isFinite(x);
    if(!Number.isInteger(sim.grid.width)||!Number.isInteger(sim.grid.height)||sim.grid.width<2||sim.grid.height<2||sim.grid.width>50||sim.grid.height>50) return false;
    const point=p=>Array.isArray(p)&&p.length===2&&p.every(finite)&&p[0]>=0&&p[1]>=0&&p[0]<sim.grid.width&&p[1]<sim.grid.height;
    if(!finite(sim.duration_min)||sim.duration_min<=0||!finite(sim.start_hour)||!finite(sim.battery_capacity_kwh)||sim.battery_capacity_kwh<=0||!finite(sim.reserve_pct)||!point(sim.depot)) return false;
    if(!sim.chargers.every(c=>point(c.position))||!sim.deliveries.every(d=>point(d.position))) return false;
    return sim.vans.every(v=>finite(v.initial_battery_kwh)&&Array.isArray(v.events)&&v.events.every(e=>{
      if(!['drive','wait','charge','deliver'].includes(e.type)||!finite(e.start_min)||!finite(e.end_min)||e.start_min<0||e.end_min<e.start_min||!finite(e.battery_start_kwh)||!finite(e.battery_end_kwh)||!point(e.position)) return false;
      return e.type!=='drive'||(Array.isArray(e.path)&&e.path.length>=2&&e.path.every(point)&&e.path.every((p,i)=>i===0||Math.abs(p[0]-e.path[i-1][0])+Math.abs(p[1]-e.path[i-1][1])===1));
    }));
  }
  const api={stateAt,deliveredAt,validate};
  if(typeof module!=='undefined')module.exports=api;else root.FleetReplay=api;
})(typeof globalThis!=='undefined'?globalThis:this);
