// Real frontend, deterministic independently verified schedules. No model or quantum calls.
const assert = require('node:assert/strict');
const fs = require('node:fs/promises');
const http = require('node:http');
const path = require('node:path');
const { execFileSync } = require('node:child_process');
const { chromium } = require(process.env.PLAYWRIGHT_MODULE || 'playwright');
const web = path.resolve(__dirname, '../../web');
const evidence = process.env.EVIDENCE_DIR;
const python = process.env.PYTHON || 'python3';
const project = `
import json, sys
from pathlib import Path
root = Path.cwd()
if root.name == 'quantum-playground': root = root.parent
sys.path[:0] = [str(root), str(root / 'quantum-playground'), str(root / 'quantum-playground/tests')]
from scenario import normalize_scenario, scenario_prompt, effective_scenario
from pipeline_live import project_result
from fleet_handoff import normalize_handoff
from test_scenario import direct_result
data = json.load(sys.stdin)
source = normalize_scenario(data['source'])
controls = [{'key':'driving_cost_per_km','type':'number','label':'Driving cost','default':source['costs']['driving_per_km'],
             'minimum':0,'maximum':2,'step':0.1,'rerun_mode':'rebuild'}]
values = data.get('values') or {'driving_cost_per_km':source['costs']['driving_per_km']}
effective = effective_scenario(source, controls, values)
output = direct_result(effective)
output['controls'] = values
artifact = {'controls':controls,'problem':{'title':source['title'],'summary':'Browser test fixture; no quantum execution.'}}
identity = {'run_id':data['plan'],'plan_id':data['plan'],'attempt_id':data['attempt']}
prompt = scenario_prompt(source)
handoff = project_result(artifact,output,prompt,identity,source_scenario=source)
view = normalize_handoff(handoff,'live',prompt,expected_values=values)
view.update(view_id=data['attempt'],scenario_context=handoff['scenario_context'])
print(json.dumps(view))
`;

function makeView(source, plan, attempt, values) {
  return JSON.parse(execFileSync(python, ['-c', project], {
    input: JSON.stringify({ source, plan, attempt, values }), encoding: 'utf8',
    env: { ...process.env, PYGAME_HIDE_SUPPORT_PROMPT: '1' }, maxBuffer: 2_000_000,
  }));
}

async function check(browser, url, platform, width, height, state) {
  const context = await browser.newContext({ viewport: { width, height }, hasTouch: platform !== 'desktop' });
  const page = await context.newPage();
  const errors = [];
  page.on('pageerror', error => errors.push(error.message));
  const snapshot = () => page.evaluate(() => JSON.parse(localStorage.getItem('quantumloop-scenario-draft-v1')).draft);
  const capture = async name => {
    if (evidence && platform !== 'small-mobile') {
      await page.screenshot({ path: path.join(evidence, name + '-' + platform + '.png'), fullPage: true });
    }
  };
  try {
    await page.goto(url);
    assert.equal(await page.title(), 'QuantumLoop');
    assert.equal(await page.locator('.brand').textContent(), 'QuantumLoop');
    await page.locator('#scenario-run:not(:disabled)').waitFor();
    assert.equal(await page.locator('#scenario-builder').isVisible(), true);
    assert.equal(await page.locator('.builder-stop').count(), 2);

    // Invalid edits survive refresh and reveal inline errors without submitting.
    await page.locator('#scenario-title').fill('');
    await page.reload();
    assert.equal(await page.locator('#scenario-title').inputValue(), '');
    await page.locator('#scenario-run').click();
    await page.locator('#scenario-error').waitFor();
    assert.match(await page.locator('#scenario-error').textContent(), /title/);
    await page.locator('#scenario-title').fill('Fleet authoring browser check');

    // A valid smaller dimension is rejected until existing locations are moved.
    await page.getByRole('button', { name: 'Settings', exact: true }).click();
    await page.getByLabel('Width (intersections)', { exact: true }).fill('2');
    await page.getByLabel('Width (intersections)', { exact: true }).dispatchEvent('change');
    assert.equal(await page.getByLabel('Width (intersections)', { exact: true }).inputValue(), '6');
    assert.match(await page.locator('#scenario-error').textContent(), /inside the smaller grid/);
    await page.locator('#scenario-title').fill('Fleet authoring browser check');

    // Keyboard movement has a precise coordinate alternative and persists.
    await page.locator('[data-marker="C1"]').focus();
    await page.keyboard.press('ArrowRight');
    assert.equal((await snapshot()).chargers[0].position[0], 3);
    assert.equal(await page.getByLabel('X', { exact: true }).inputValue(), '3');
    await page.reload();
    assert.equal((await snapshot()).chargers[0].position[0], 3);
    await page.getByRole('button', { name: 'Vehicles 2', exact: true }).click();
    await page.getByRole('button', { name: '+ Add delivery for V1', exact: true }).click();
    assert.equal((await snapshot()).deliveries.filter(stop => stop.vehicle_id === 'V1').length, 3);

    // Touch-friendly move mode uses the same intersection snapping.
    await page.locator('[data-marker="C2"]').click();
    await page.locator('#scenario-move').click();
    await page.locator('[data-marker="D3"]').click();
    assert.deepEqual((await snapshot()).chargers.find(charger => charger.id === 'C2').position, [5, 0]);
    await page.locator('[data-marker="C2"]').click();
    assert.match(await page.locator('.builder-overlap').textContent(), /Choose an object/);
    await page.getByRole('button', { name: 'C2 · chargers', exact: true }).click();
    assert.equal(await page.locator('.builder-object-heading h2').textContent(), 'C2');
    await page.getByRole('button', { name: 'Vehicles 2', exact: true }).click();
    assert.equal(await page.evaluate(() => document.documentElement.scrollWidth > innerWidth), false);
    await capture('after-builder');

    const plansBefore = state.posts.filter(post => post.path === '/api/pipeline-runs').length;
    await page.locator('#scenario-run').click();
    await page.locator('#results-screen').waitFor();
    assert.equal(state.posts.filter(post => post.path === '/api/pipeline-runs').length, plansBefore + 1);

    assert.equal(await page.locator('#classical-map-view').isVisible(), true);
    await page.locator('#scrub').fill('120');
    await page.locator('#scrub').dispatchEvent('input');
    assert.equal(await page.locator('#quantum-delivered').textContent(), '4 / 4 delivered');
    assert.equal(await page.locator('#classical-delivered').textContent(), '4 / 4 delivered');
    assert.equal(await page.evaluate(() => document.documentElement.scrollWidth > innerWidth), false);
    await capture('after-result');

    // A supported numeric change reuses the existing approved plan.
    await page.locator('#edit-scenario').click();
    await page.getByRole('button', { name: 'Settings', exact: true }).click();
    await page.getByLabel('Driving ($/km)', { exact: true }).fill('0.5');
    assert.equal(await page.locator('#scenario-run').textContent(), 'Recalculate settings');
    await page.locator('#scenario-run').click();
    await page.locator('#results-screen').waitFor();
    assert.equal(state.posts.at(-1).path, '/api/run');
    assert.equal(state.posts.at(-1).body.variables.driving_cost_per_km, 0.5);
    assert.equal(state.posts.filter(post => post.path === '/api/pipeline-runs').length, plansBefore + 1);

    // Returning to the retained draft advances its source to the latest attempt.
    await page.locator('#new-problem').click();
    await page.getByRole('button', { name: 'Settings', exact: true }).click();
    await page.getByLabel('Driving ($/km)', { exact: true }).fill('0.6');
    assert.equal(await page.locator('#scenario-run').textContent(), 'Recalculate settings');
    await page.locator('#scenario-run').click();
    await page.locator('#results-screen').waitFor();
    assert.equal(state.posts.at(-1).path, '/api/run');

    // Editing this attempt restores its effective values. Geometry forces planning.
    await page.locator('#edit-scenario').click();
    await page.getByRole('button', { name: 'Settings', exact: true }).click();
    assert.equal(await page.getByLabel('Driving ($/km)', { exact: true }).inputValue(), '0.6');
    await page.locator('[data-marker="D1"]').focus();
    await page.keyboard.press('ArrowRight');
    assert.equal(await page.locator('#scenario-run').textContent(), 'Plan new scenario');
    state.failNext = true;
    await page.locator('#scenario-run').click();
    await page.locator('#scenario-error').waitFor();
    assert.match(await page.locator('#scenario-error').textContent(), /No feasible/);
    assert.equal((await snapshot()).deliveries.find(stop => stop.id === 'D1').position[0], 1);
    assert.equal(Number((await snapshot()).costs.driving_per_km), 0.6);
    assert.equal(await page.locator('#failed-pipeline-progress').isVisible(), true);
    await capture('after-error');
    await page.locator('#scenario-run').click();
    await page.locator('#results-screen').waitFor();
    assert.equal(state.posts.at(-1).path, '/api/pipeline-runs');
    assert.equal(state.posts.at(-1).body.scenario.deliveries.find(stop => stop.id === 'D1').position[0], 1);

    // Builder-created history restores source inputs; prompt mode remains accessible.
    await page.locator('#run-history').click();
    await page.locator('.history-item').first().click();
    await page.locator('#history-edit-scenario').click();
    assert.equal(await page.locator('#scenario-title').inputValue(), state.saved.source.title);
    await page.locator('#prompt-mode').focus();
    await page.keyboard.press('Enter');
    assert.equal(await page.locator('#problem-prompt').isVisible(), true);
    await page.keyboard.press('ArrowLeft');
    assert.equal(await page.locator('#scenario-builder').isVisible(), true);
    assert.deepEqual(errors, []);
    console.log(`PASS: ${platform} ${width}×${height}, multi-stop editing, keyboard/touch, persistence, reuse/replan, failure recovery, history, no overflow/errors`);
  } finally {
    await context.close();
  }
}

(async () => {
  const state = { posts: [], views: new Map(), jobs: new Map(), serial: 0, failNext: false, saved: null };
  const server = http.createServer(async (request, response) => {
    const endpoint = new URL(request.url, 'http://localhost').pathname;
    const json = (body, status = 200) => {
      response.writeHead(status, { 'Content-Type': 'application/json' }); response.end(JSON.stringify(body));
    };
    try {
      if (request.method === 'POST') {
        let raw = ''; for await (const chunk of request) raw += chunk;
        const body = JSON.parse(raw); state.posts.push({ path: endpoint, body });
        const attempt = 'browser-attempt-' + ++state.serial;
        if (endpoint === '/api/pipeline-runs') {
          const id = 'browser-plan-' + state.serial;
          const history = [{ id: 1, stage: 'init', attempt: 1, status: state.failNext ? 'failed' : 'completed',
            steps: [{ id: 1, label: 'Check authored scenario', status: state.failNext ? 'failed' : 'completed' }] }];
          const view = state.failNext ? null : makeView(body.scenario, id, attempt);
          const job = { id, stage: 'init', attempt: 1, history,
            ...(state.failNext ? { status: 'failed', error: 'No feasible schedule for the selected settings.' }
              : { status: 'completed', result: view }) };
          state.failNext = false;
          if (view) {
            job.prompt = view.problem.original_prompt;
            state.views.set(attempt, { view, source: body.scenario });
            state.saved = { view, source: body.scenario };
          }
          state.jobs.set(id, job);
          return json({ id, stage: null, attempt: 0, history: [], status: 'queued', ...(job.prompt ? { prompt: job.prompt } : {}) }, 202);
        }
        if (endpoint === '/api/run') {
          const previous = state.views.get(body.view_id);
          const view = makeView(previous.source, previous.view.plan_id, attempt,
            { ...previous.view.effective_values, ...body.variables });
          state.views.set(attempt, { view, source: previous.source }); return json(view);
        }
        return json({ error: 'Unexpected browser test request.' }, 404);
      }
      if (endpoint === '/api/config') return json({ pipeline_backend_configured: true, scenario_backend_configured: true });
      if (endpoint === '/favicon.ico') { response.writeHead(204).end(); return; }
      if (endpoint === '/api/example') return json({ prompt: 'Dispatch the fleet.', live_prompt: 'Dispatch the fleet.' });
      if (endpoint.startsWith('/api/pipeline-runs/')) return json(state.jobs.get(endpoint.split('/').at(-1)));
      if (endpoint === '/api/workspaces') return json({ workspaces: [{ id: 'saved-builder', title: state.saved.source.title,
        status: 'verified', can_open: true, can_edit: true }] });
      if (endpoint === '/api/workspaces/saved-builder') return json({ id: 'saved-builder', title: state.saved.source.title,
        prompt: state.saved.view.problem.original_prompt, scenario: state.saved.source, artifacts: [], can_open: true, can_edit: true });
      const filename = endpoint.slice(1) || 'index.html';
      if (!/^[a-z0-9.-]+$/i.test(filename)) return json({ error: 'Not found.' }, 404);
      const content = await fs.readFile(path.join(web, filename));
      response.writeHead(200, { 'Content-Type': filename.endsWith('.css') ? 'text/css' : filename.endsWith('.js') ? 'text/javascript' : 'text/html' });
      response.end(content);
    } catch (error) {
      console.error(error); json({ error: String(error.message) }, 500);
    }
  });
  await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
  let browser;
  try {
    if (evidence) await fs.mkdir(evidence, { recursive: true });
    browser = await chromium.launch({ headless: true,
      ...(process.env.BROWSER_EXECUTABLE ? { executablePath: process.env.BROWSER_EXECUTABLE } : {}), args: ['--no-sandbox'] });
    const url = `http://127.0.0.1:${server.address().port}`;
    for (const viewport of [['desktop', 1440, 1080], ['mobile', 390, 844], ['small-mobile', 320, 740]]) {
      await check(browser, url, ...viewport, state);
    }
  } finally {
    if (browser) await browser.close();
    await new Promise(resolve => server.close(resolve));
  }
})().catch(error => { console.error(error); process.exitCode = 1; });
