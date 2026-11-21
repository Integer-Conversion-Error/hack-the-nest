// Run against the dashboard with its configured OSRM routing provider.
const assert = require('node:assert/strict');
const fs = require('node:fs/promises');
const path = require('node:path');
const {chromium} = require(process.env.PLAYWRIGHT_MODULE || 'playwright');

const base = process.env.DASHBOARD_URL || 'http://127.0.0.1:8000';
const evidence = process.env.EVIDENCE_DIR;

async function check(browser, platform, width, height) {
  const page = await browser.newPage({viewport: {width, height}});
  const errors = [];
  page.on('pageerror', error => errors.push(error.message));
  try {
    await page.goto(base + '/problems/#facility');
    const view = page.locator('[data-problem="facility"]');
    for (const id of ['H2', 'H3', 'H4']) await view.getByLabel(id + ' available').uncheck();
    await view.getByLabel('H1 capacity (visits/day)').fill('50');
    await view.getByLabel('Drive-time limit (min)').fill('10');
    await view.getByLabel('Drive-time limit (min)').press('Tab');

    const matrixReply = page.waitForResponse(r => r.url().endsWith('/api/maps/travel-times'));
    await view.getByRole('button', {name: 'Recalculate road times'}).click();
    const reply = await matrixReply;
    const matrix = await reply.json();
    assert.ok(reply.ok(), JSON.stringify(matrix));
    const firstRow = view.locator('.facility-table-wrap tbody tr').first();
    await firstRow.waitFor();
    assert.match(await firstRow.innerText(), /East/);
    const formatter = new Intl.NumberFormat('en-CA', {minimumFractionDigits: 2, maximumFractionDigits: 2});
    const selector = view.getByLabel('Road trip to display');
    const tripButton = view.getByRole('button', {name: 'Show road route'});
    for (const [from, to] of [[0, 4], [4, 0]]) {
      const km = matrix.distances_m[from][to] / 1000;
      const minutes = matrix.durations_s[from][to] / 60;
      const expected = formatter.format(km) + ' km · ' + formatter.format(minutes) + ' min';
      assert.ok((await firstRow.innerText()).includes(expected), expected);
      await selector.selectOption(from + '-' + to);
      const pathReply = page.waitForResponse(r => r.url().endsWith('/api/maps/road-path'));
      await tripButton.click();
      const response = await pathReply;
      const line = await response.json();
      assert.ok(response.ok(), JSON.stringify(line));
      assert.equal(line.distance_m, matrix.distances_m[from][to]);
      assert.equal(line.duration_s, matrix.durations_s[from][to]);
      assert.equal(line.routing_digest, matrix.routing_digest);
      assert.ok(line.coordinates.length > 2, 'Toronto road path has intermediate street vertices');
      await view.locator('path.facility-road-path').waitFor();
      assert.equal(await view.locator('path.facility-road-path').count(), 1);
      await page.waitForFunction(() => {
        const map = document.querySelector('.facility-view .leaflet-container');
        const route = map?.querySelector('.facility-road-path');
        if (!route || map.classList.contains('leaflet-zoom-anim')) return false;
        const frame = map.getBoundingClientRect(), line = route.getBoundingClientRect();
        return line.width > 0 && line.height > 0 && line.top >= frame.top &&
          line.bottom <= frame.bottom && line.left >= frame.left && line.right <= frame.right;
      }, null, {timeout:5000});
      console.log(platform, from + ' → ' + to, line.distance_m, 'm,', line.duration_s, 's,', line.coordinates.length, 'vertices');
    }
    function dailyRoundTrips(values) {
      return [14, 18, 12].reduce((total, visits, i) => {
        const zone = 4 + i;
        return total + visits * (values[0][zone] + values[zone][0]);
      }, 0);
    }
    const dailyKm = dailyRoundTrips(matrix.distances_m) / 1000;
    const dailyMin = dailyRoundTrips(matrix.durations_s) / 60;
    const summary = await view.locator('.facility-metrics').innerText();
    assert.ok(summary.includes(dailyKm.toFixed(2) + ' km'), summary);
    assert.ok(summary.includes(dailyMin.toFixed(1) + ' min'), summary);
    if (evidence) {
      await fs.mkdir(evidence, {recursive:true});
      await page.waitForFunction(() => [...document.querySelectorAll('img.leaflet-tile-loaded')].some(i => i.naturalWidth > 0));
      await page.waitForLoadState('networkidle');
      if (platform === 'desktop') {
        await view.locator('.facility-metrics').screenshot({path:path.join(evidence, 'facility-totals-desktop.png')});
      }
      const figure = view.locator('.problem-figure');
      await figure.screenshot({path:path.join(evidence, 'facility-map-' + platform + '.png')});
      const service = view.getByRole('heading', {name:'Service plan'}).locator('..');
      await service.screenshot({path:path.join(evidence, 'facility-plan-' + platform + '.png')});
    }
    await view.getByLabel('H1 longitude').fill('-79.382');
    await page.waitForFunction(() => !document.querySelector('path.facility-road-path'));
    assert.equal((await view.locator('.facility-metrics').innerText()).trim(), '');
    assert.deepEqual(errors, []);
  } finally { await page.close(); }
}

(async () => {
  const browser = await chromium.launch({headless:true,args:['--no-sandbox'],
    ...(process.env.BROWSER_EXECUTABLE ? {executablePath: process.env.BROWSER_EXECUTABLE} : {})});
  try {
    await check(browser, 'desktop', 1440, 1000);
    await check(browser, 'mobile', 390, 844);
  } finally { await browser.close(); }
})().catch(error => { console.error(error); process.exitCode = 1; });
