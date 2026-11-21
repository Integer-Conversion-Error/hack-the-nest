// Capture actual OSM tiles and saved OSRM road geometry; no model/quantum run.
const fs=require('node:fs/promises'),path=require('node:path'),http=require('node:http');
const {chromium}=require(process.env.PLAYWRIGHT_MODULE||'playwright');
const web=path.resolve(__dirname,'../../web');
const output=process.env.EVIDENCE_DIR||path.resolve(__dirname,'../../docs/evidence/geographic-maps');
(async()=>{
 const fixture=JSON.parse(await fs.readFile(path.resolve(__dirname,'../fixtures/geographic-road-replay.json'),'utf8'));
 const server=http.createServer(async(req,res)=>{
  const name=new URL(req.url,'http://localhost').pathname;
  const json=value=>{res.writeHead(200,{'Content-Type':'application/json'});res.end(JSON.stringify(value));};
  try{
   const maps={tile_url:'https://tile.openstreetmap.org/{z}/{x}/{y}.png',tile_attribution:'<a href="https://www.openstreetmap.org/copyright">© OpenStreetMap contributors</a>',routing_attribution:'Routes: <a href="https://project-osrm.org">OSRM</a>'};
   if(name==='/api/config')return json({pipeline_backend_configured:true,scenario_backend_configured:true,maps});
   if(name==='/api/maps/config')return json(maps);
   if(name==='/api/example')return json({prompt:'Dispatch the fleet.',live_prompt:'Dispatch the fleet.'});
   if(name==='/api/pipeline-runs')return json({id:'evidence-road-fixture',stage:'verify',attempt:1,status:'completed',history:[],result:fixture.view,prompt:fixture.view.problem.original_prompt});
   if(name==='/favicon.ico'){res.writeHead(204).end();return;}
   const file=path.resolve(web,'.'+name+(name==='/'?'index.html':''));
   if(!file.startsWith(web+path.sep)){res.writeHead(404).end();return;}
   const bytes=await fs.readFile(file);res.writeHead(200,{'Content-Type':file.endsWith('.css')?'text/css':file.endsWith('.js')?'text/javascript':file.endsWith('.png')?'image/png':'text/html'});res.end(bytes);
  }catch(e){res.writeHead(500).end(String(e));}
 });
 await new Promise(r=>server.listen(0,'127.0.0.1',r));
 await fs.mkdir(output,{recursive:true});
 const browser=await chromium.launch({headless:true,...(process.env.BROWSER_EXECUTABLE?{executablePath:process.env.BROWSER_EXECUTABLE}:{}),args:['--no-sandbox']});
 const context=await browser.newContext();
 try{
  for(const [platform,width,height] of [['desktop',1440,1080],['mobile',390,844]]){
   const page=await context.newPage();await page.setViewportSize({width,height});
   await page.goto(`http://127.0.0.1:${server.address().port}`);
   await page.locator('#scenario-map-type').selectOption('geographic');
   await page.locator('[data-geo-marker="P1"]').waitFor();
   await page.waitForFunction(()=>[...document.querySelectorAll('#scenario-geographic-map img.leaflet-tile-loaded')].some(i=>i.naturalWidth>0));
   await page.waitForTimeout(750);
   await page.screenshot({path:path.join(output,`after-editor-${platform}.png`),fullPage:true});
   await page.locator('#scenario-run').click();await page.locator('#results-screen').waitFor();
   await page.locator('#scrub').fill('1');await page.locator('#scrub').dispatchEvent('input');
   await page.waitForFunction(()=>[...document.querySelectorAll('#geographic-map img.leaflet-tile-loaded')].some(i=>i.naturalWidth>0));
   await page.waitForTimeout(750);
   await page.screenshot({path:path.join(output,`after-replay-${platform}.png`),fullPage:true});
   console.log(`Captured ${platform} with actual map tiles and saved road geometry; no quantum execution`);
   await page.close();
  }
 }finally{await context.close();await browser.close();await new Promise(r=>server.close(r));}
})().catch(e=>{console.error(e);process.exitCode=1;});
