// Optional real-browser check. The normal Node suite does not require Playwright.
const assert = require('node:assert/strict');
const fs = require('node:fs/promises');
const http = require('node:http');
const path = require('node:path');
const { chromium } = require(process.env.PLAYWRIGHT_MODULE || 'playwright');
const web = path.resolve(__dirname, '../../web');
const evidence = process.env.EVIDENCE_DIR;
const history = [
  { id: 1, stage: 'init', attempt: 1, status: 'completed', steps: [
    { id: 1, label: 'Read the fleet problem', status: 'completed', detail: 'Two vans, four deliveries, one shared charger.' },
    { id: 2, label: 'Check constraints and assumptions', status: 'completed', detail: 'Delivery windows and battery reserve are included.' },
  ] },
  { id: 2, stage: 'plan', attempt: 1, status: 'completed', steps: [
    { id: 3, label: 'Research a suitable solver', status: 'completed', detail: 'Grounded the QAOA approach in three sources.' },
    { id: 4, label: 'Review the proposed plan', status: 'completed', detail: 'Plan approved within the qubit and worker-time limits.' },
  ] },
  { id: 3, stage: 'build', attempt: 1, status: 'completed', steps: [
    { id: 5, label: 'Build the dispatch solver', status: 'completed', detail: 'Created the solver, verifier, and timed replay.' },
  ] },
  { id: 4, stage: 'test', attempt: 1, status: 'revising', steps: [
    { id: 6, label: 'Check delivery windows', status: 'failed', detail: 'Van 2 arrives after its delivery window. Include service time before the return trip.' },
    { id: 7, label: 'Return to Build', status: 'info', detail: 'Repair the time-window calculation and test the schedule again.' },
  ] },
  { id: 5, stage: 'build', attempt: 2, status: 'running', steps: [
    { id: 8, label: 'Repair the time-window calculation', status: 'running', detail: 'Updating the affected constraint while keeping the approved plan.' },
  ] },
];

async function check(browser, url, platform, width, height) {
  const page = await browser.newPage({ viewport: { width, height }, deviceScaleFactor: 1 });
  const errors = [];
  let snapshot = { id: 'visual-check', status: 'running', stage: 'build', attempt: 2, history };
  page.on('pageerror', error => errors.push(error.message));
  await page.route('**/api/**', route => {
    const endpoint = new URL(route.request().url()).pathname;
    const body = endpoint === '/api/config' ? { pipeline_backend_configured: true }
      : endpoint === '/api/example'
        ? { prompt: 'Dispatch two electric vans to four deliveries.', live_prompt: 'Dispatch two electric vans to four deliveries.' }
        : snapshot;
    return route.fulfill({ json: body });
  });
  try {
    await page.goto(url);
    await page.locator('#problem-prompt').fill('Dispatch two electric vans to four deliveries. Respect delivery windows and battery reserve.');
    await page.locator('#generate').click();
    await page.locator('#pipeline-visits li.pipeline-visit').last().waitFor();
    assert.equal(await page.locator('#pipeline-stages').isVisible(), true);
    assert.equal(await page.locator('#pipeline-count').textContent(), '2 of 5 stages complete');
    assert.equal(await page.locator('#pipeline-current-label').textContent(), 'Repair the time-window calculation');
    assert.equal(await page.locator('#pipeline-repeat-count').textContent(), '1 repair loop');
    assert.equal(await page.locator('#pipeline-repair').isVisible(), true);
    assert.equal(await page.evaluate(() => document.documentElement.scrollWidth > innerWidth), false);
    const dotAnimation = () => page.locator('#pipeline-current-state').evaluate(element =>
      getComputedStyle(element, '::before').animationName);
    assert.notEqual(await dotAnimation(), 'none', 'the active step has a status animation');
    await page.emulateMedia({ reducedMotion: 'reduce' });
    assert.equal(await dotAnimation(), 'none', 'reduced motion suppresses the status animation');
    await page.emulateMedia({ reducedMotion: 'no-preference' });
    await page.waitForTimeout(350);
    if (evidence && platform !== 'small-mobile') {
      await page.screenshot({ path: path.join(evidence, `after-${platform}.png`), fullPage: true });
    }

    // A disclosure opened with the keyboard stays open and focused through polling.
    const first = page.locator('#pipeline-visits details').first();
    assert.equal(await first.evaluate(element => element.open), false);
    await first.locator('summary').focus();
    await page.keyboard.press('Enter');
    await page.waitForTimeout(1500);
    assert.equal(await first.evaluate(element => element.open), true);
    assert.equal(await first.locator('summary').evaluate(element => document.activeElement === element), true);
    await page.locator('#pipeline-detail-toggle').click();
    assert.equal(await page.locator('#pipeline-visits details[open]').count(), 0);
    await page.waitForTimeout(1300);
    assert.equal(await page.locator('#pipeline-visits details[open]').count(), 0);
    await page.locator('#pipeline-detail-toggle').click();
    assert.equal(await page.locator('#pipeline-visits details[open]').count(), history.length);

    // Start a fresh run, then reopen its failed planning attempt from the problem screen.
    snapshot = { id: 'failed-check', status: 'failed', stage: 'plan', attempt: 1,
      error: 'Where are the delivery locations?', history: [
        { id: 1, stage: 'plan', attempt: 1, status: 'failed', steps: [
          { id: 1, label: 'Clarify delivery locations', status: 'failed', detail: 'Provide the depot and delivery locations so the routes can be planned.' },
        ] },
      ] };
    await page.evaluate(() => sessionStorage.clear());
    await page.reload();
    await page.locator('#problem-prompt').fill('Dispatch our vans to the deliveries.');
    await page.locator('#generate').click();
    await page.locator('#failed-pipeline-progress').waitFor();
    await page.locator('#failed-pipeline-progress').click();
    assert.equal(await page.locator('#pipeline-current-state').textContent(), 'Needs your attention');
    assert.equal(await page.locator('#pipeline-stages [data-state="failed"]').count(), 1);
    assert.equal(await dotAnimation(), 'none', 'a failed step no longer pulses as active');
    assert.equal(await page.locator('#pipeline-visits li.pipeline-visit').count(), 1);
    assert.equal(await page.locator('#pipeline-visits strong').first().textContent(), 'Plan solution');
    if (evidence && platform === 'mobile') {
      await page.waitForTimeout(350);
      await page.screenshot({ path: path.join(evidence, 'after-failed-mobile.png'), fullPage: true });
    }
    await page.locator('#progress-return').click();
    assert.equal(await page.locator('#prompt-screen').isVisible(), true);
    assert.deepEqual(errors, []);
    console.log(`PASS: ${platform} (${width}×${height}), repair state, keyboard, polling, failure recovery, no overflow/errors`);
  } finally {
    await page.close();
  }
}

(async () => {
  const server = http.createServer(async (request, response) => {
    const filename = new URL(request.url, 'http://localhost').pathname.slice(1) || 'index.html';
    if (!/^[a-z0-9.-]+$/i.test(filename)) { response.writeHead(404).end(); return; }
    try {
      const content = await fs.readFile(path.join(web, filename));
      response.setHeader('Content-Type', filename.endsWith('.css') ? 'text/css' : filename.endsWith('.js') ? 'text/javascript' : 'text/html');
      response.end(content);
    } catch { response.writeHead(404).end(); }
  });
  await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
  let browser;
  try {
    if (evidence) await fs.mkdir(evidence, { recursive: true });
    browser = await chromium.launch({ headless: true,
      ...(process.env.BROWSER_EXECUTABLE ? { executablePath: process.env.BROWSER_EXECUTABLE } : {}),
      args: ['--no-sandbox'],
    });
    const url = `http://127.0.0.1:${server.address().port}`;
    for (const viewport of [['desktop', 1360, 1080], ['mobile', 390, 844], ['small-mobile', 320, 740]]) {
      await check(browser, url, ...viewport);
    }
  } finally {
    if (browser) await browser.close();
    await new Promise(resolve => server.close(resolve));
  }
})().catch(error => { console.error(error); process.exitCode = 1; });
