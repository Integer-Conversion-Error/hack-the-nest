// Real frontend with synthetic road/search responses and local test tiles; no providers/models.
const assert = require('node:assert/strict');
const fs = require('node:fs/promises');
const path = require('node:path');
const http = require('node:http');
const { execFileSync } = require('node:child_process');
const { chromium } = require(process.env.PLAYWRIGHT_MODULE || 'playwright');
const web = path.resolve(__dirname, '../../web');
const python = process.env.PYTHON || 'python3';
const fixture = path.resolve(__dirname, '../geographic_browser_fixture.py');
const tile = Buffer.from('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVQIHWP4z8DwHwAFgAI/ScLbtAAAAABJRU5ErkJggg==','base64');
const state = { serial:0, posts:[], jobs:new Map(), views:new Map(), failNext:false };
function viewFor(source, plan, values) {
  const attempt='geo-attempt-'+ ++state.serial;
  const view=JSON.parse(execFileSync(python,[fixture],{input:JSON.stringify({source,plan,attempt,values}),encoding:'utf8',maxBuffer:2_000_000}));
  state.views.set(view.view_id,{view,source}); return view;
}
async function check(browser,url,label,width,height) {
  const context=await browser.newContext({viewport:{width,height},hasTouch:label!=='desktop'});
  const page=await context.newPage(), errors=[];
  page.on('pageerror',e=>errors.push(e.message));
  const draft=()=>page.evaluate(()=>{const s=JSON.parse(localStorage.getItem('quantumloop-scenario-draft-v1'));return s.drafts[s.active];});
  try {
    await page.goto(url);
    await page.locator('#scenario-run:not(:disabled)').waitFor();
    await page.locator('#scenario-map-type').selectOption('geographic');
    await page.locator('[data-geo-marker="D1"]').waitFor();
    await page.locator('[data-geo-marker="D1"]').click();
    assert.equal(await page.getByLabel('Latitude',{exact:true}).inputValue(),'43.65');
    await page.locator('[data-geo-marker="D1"]').focus(); await page.keyboard.press('ArrowRight');
    assert.equal(Number(await page.getByLabel('Longitude',{exact:true}).inputValue()),-79.3789);
    await page.locator('#scenario-address-search').fill('Toronto City Hall');
    await page.locator('#scenario-search-form').getByRole('button',{name:'Search',exact:true}).click();
    await page.getByRole('button',{name:'Place D1 here',exact:true}).click();
    assert.deepEqual((await draft()).deliveries[0].position,[-79.38,43.65]);
    await page.locator('#scenario-map-type').selectOption('grid');
    await page.getByLabel('Starting charge (%)',{exact:true}).fill('45');
    await page.locator('#scenario-map-type').selectOption('geographic');
    assert.deepEqual((await draft()).deliveries[0].position,[-79.38,43.65]);
    await page.reload();
    assert.equal(await page.locator('#scenario-map-type').inputValue(),'geographic');
    await page.locator('[data-geo-marker="D1"]').click();
    await page.locator('#scenario-address-search').fill('unavailable');
    await page.locator('#scenario-search-form').getByRole('button',{name:'Search',exact:true}).click();
    await page.getByText('Search is temporarily unavailable. You can still place markers or enter coordinates.').waitFor();
    assert.equal(await page.getByLabel('Latitude',{exact:true}).isEnabled(),true);
    await page.getByRole('button',{name:'Settings',exact:true}).click();
    assert.equal(await page.getByLabel('Driving speed (km/h)',{exact:true}).count(),0);
    assert.equal(await page.getByLabel('Width (intersections)',{exact:true}).count(),0);
    const plans=state.posts.filter(p=>p.path==='/api/pipeline-runs').length;
    await page.locator('#scenario-run').click();
    await page.locator('#results-screen').waitFor();
    assert.equal(await page.locator('#geographic-map').isVisible(),true);
    assert.equal(await page.locator('#classical-geographic-map').isVisible(),true);
    assert.equal(await page.locator('#fleet-map').isVisible(),false);
    assert.ok(await page.locator('#geographic-map .leaflet-overlay-pane path').count()>0);
    const firstVan = page.locator('#fleet .van-card').first();
    assert.equal(await firstVan.locator('.van-road-total').innerText(), '2.91 km by road · 7.2 min est. driving');
    await firstVan.click();
    const itinerary = page.locator('#road-itinerary');
    assert.match(await itinerary.locator('caption').innerText(), /Quantum.*V1/);
    assert.equal(await itinerary.locator('tbody tr').count(), 3);
    assert.deepEqual(await itinerary.locator('tbody tr').first().locator('th,td').allTextContents(),
      ['P1 → D1', '0.84 km', '1.9 min', '08:02']);
    assert.deepEqual(await itinerary.locator('tfoot td').allTextContents(), ['2.91 km', '7.2 min', '']);
    await page.locator('#classical-geographic-map .geo-marker.van').last().click();
    assert.match(await itinerary.locator('caption').innerText(), /Classical.*V2/);
    assert.equal(await itinerary.locator('tbody tr').count(), 2);
    const before=await page.locator('#geographic-map .geo-marker.van').first().evaluate(e=>e.closest('.leaflet-marker-icon').style.transform);
    await page.locator('#scrub').fill('1'); await page.locator('#scrub').dispatchEvent('input');
    const after=await page.locator('#geographic-map .geo-marker.van').first().evaluate(e=>e.closest('.leaflet-marker-icon').style.transform);
    assert.notEqual(before,after);
    await page.locator('#scrub').fill('120'); await page.locator('#scrub').dispatchEvent('input');
    assert.equal(await page.locator('#quantum-delivered').textContent(),'3 / 3 delivered');
    assert.equal(await page.locator('#classical-delivered').textContent(),'3 / 3 delivered');
    assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth),false);
    await page.locator('#edit-scenario').click();
    await page.getByRole('button',{name:'Settings',exact:true}).click();
    await page.getByLabel('Driving ($/km)',{exact:true}).fill('0.5');
    assert.equal(await page.locator('#scenario-run').textContent(),'Recalculate settings');
    await page.locator('#scenario-run').click(); await page.locator('#results-screen').waitFor();
    assert.equal(state.posts.at(-1).path,'/api/run');
    assert.equal(state.posts.filter(p=>p.path==='/api/pipeline-runs').length,plans+1);
    await page.locator('#edit-scenario').click();
    await page.locator('[data-geo-marker="D1"]').focus(); await page.keyboard.press('ArrowRight');
    assert.equal(await page.locator('#scenario-run').textContent(),'Plan new scenario');
    state.failNext=true;
    await page.locator('#scenario-run').click(); await page.locator('#scenario-error').waitFor();
    assert.match(await page.locator('#scenario-error').textContent(),/Road routing is temporarily unavailable/);
    assert.equal((await draft()).schema_version,2);
    assert.equal(Number((await draft()).costs.driving_per_km),0.5);
    await page.locator('#scenario-run').click(); await page.locator('#results-screen').waitFor();
    assert.equal(state.posts.at(-1).path,'/api/pipeline-runs');
    assert.deepEqual(errors,[]);
    console.log(`PASS: ${label} ${width}×${height}, geographic search/edit/persistence, road replay, settings reuse, rerouting failure recovery, no overflow/errors`);
  } finally { await context.close(); }
}
(async()=>{
  const server=http.createServer(async(req,res)=>{
    const endpoint=new URL(req.url,'http://localhost'), name=endpoint.pathname;
    const json=(body,status=200)=>{res.writeHead(status,{'Content-Type':'application/json'});res.end(JSON.stringify(body));};
    const config={tile_url:`http://${req.headers.host}/tiles/{z}/{x}/{y}.png`,tile_attribution:'OpenStreetMap test tiles',routing_attribution:'OSRM test routes'};
    try {
      if(name.startsWith('/tiles/')){res.writeHead(200,{'Content-Type':'image/png'});return res.end(tile);}
      if(name==='/api/config')return json({pipeline_backend_configured:true,scenario_backend_configured:true,maps:config});
      if(name==='/api/maps/config')return json(config);
      if(name==='/api/example')return json({prompt:'Dispatch the fleet.',live_prompt:'Dispatch the fleet.'});
      if(name==='/api/maps/search')return endpoint.searchParams.get('q')==='unavailable'
        ?json({error:'Search is temporarily unavailable.'},503):json({results:[{label:'Toronto City Hall',position:[-79.38,43.65]}]});
      if(req.method==='POST'){
        let raw='';for await(const chunk of req)raw+=chunk;const body=JSON.parse(raw);state.posts.push({path:name,body});
        if(name==='/api/pipeline-runs'){
          const id='geo-plan-'+ ++state.serial, failure=state.failNext;state.failNext=false;
          const view=failure?null:viewFor(body.scenario,id);
          const job={id,status:failure?'failed':'completed',stage:'init',attempt:1,history:[],
            ...(failure?{error:'Road routing is temporarily unavailable. Your draft is preserved.'}:{result:view,prompt:view.problem.original_prompt})};
          state.jobs.set(id,job);return json(job,202);
        }
        if(name==='/api/run'){
          const previous=state.views.get(body.view_id);
          return json(viewFor(previous.source,previous.view.plan_id,{...previous.view.effective_values,...body.variables}));
        }
      }
      if(name.startsWith('/api/pipeline-runs/'))return json(state.jobs.get(name.split('/').at(-1)));
      if(name==='/favicon.ico'){res.writeHead(204).end();return;}
      const file=path.resolve(web,'.'+name+(name==='/'?'index.html':''));
      if(!file.startsWith(web+path.sep))return json({error:'Not found'},404);
      const content=await fs.readFile(file);
      res.writeHead(200,{'Content-Type':file.endsWith('.css')?'text/css':file.endsWith('.js')?'text/javascript':file.endsWith('.png')?'image/png':'text/html'});res.end(content);
    }catch(error){console.error(error);json({error:error.message},500);}
  });
  await new Promise(resolve=>server.listen(0,'127.0.0.1',resolve));
  const browser=await chromium.launch({headless:true,...(process.env.BROWSER_EXECUTABLE?{executablePath:process.env.BROWSER_EXECUTABLE}:{}),args:['--no-sandbox']});
  try {for(const viewport of [['desktop',1440,1080],['mobile',390,844],['small-mobile',320,740]])await check(browser,`http://127.0.0.1:${server.address().port}`,...viewport);}
  finally {await browser.close();await new Promise(resolve=>server.close(resolve));}
})().catch(error=>{console.error(error);process.exitCode=1;});
