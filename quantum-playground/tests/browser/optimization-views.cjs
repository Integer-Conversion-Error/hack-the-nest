// Run against the dashboard server. Set EVIDENCE_DIR to save the rendered views.
const assert = require('node:assert/strict');
const path = require('node:path');
const fs = require('node:fs/promises');
const { chromium } = require(process.env.PLAYWRIGHT_MODULE || 'playwright');

const base = process.env.DASHBOARD_URL || 'http://127.0.0.1:8000';
const evidence = process.env.EVIDENCE_DIR;
const ids = ['facility', 'workforce', 'production', 'timetable', 'energy', 'projects', 'teams'];

async function fill(view, label, value) {
  const input = view.getByRole('spinbutton', { name: label, exact: true });
  await input.fill(String(value));
  await input.press('Tab');
}

async function edit(id, view) {
  switch (id) {
    case 'facility':
      await fill(view, 'Maximum open hubs', 1);
      assert.match(await view.locator('.problem-metrics').innerText(), /30 \/ 44/);
      assert.equal(await view.locator('.facility-pin.open').count(), 1);
      break;
    case 'workforce':
      await fill(view, 'Early required employees', 0);
      assert.match(await view.locator('.problem-metrics').innerText(), /Staffed shift places\s*3/);
      break;
    case 'production':
      await fill(view, 'Frame operation 1 hours', 8);
      assert.equal(await view.locator('.schedule-operation').count(), 6);
      assert.match(await view.locator('[role="status"]').innerText(), /shortest possible schedule needs/i);
      break;
    case 'timetable':
      await view.getByLabel('Number of available class periods').selectOption('2');
      assert.equal(await view.locator('.schedule-class').count(), 0);
      assert.match(await view.locator('[role="status"]').innerText(), /no|infeasible/i);
      await view.getByLabel('Number of available class periods').selectOption('3');
      assert.equal(await view.locator('.schedule-class').count(), 5);
      await fill(view, 'Lecture room capacity', 40);
      break;
    case 'energy':
      await fill(view, 'Battery power (kW)', 0);
      assert.match(await view.locator('.problem-metrics').innerText(), /Battery delivered\s*0(?:\.0)? kWh/);
      assert.equal(await view.locator('.problem-figure svg').count(), 2);
      break;
    case 'teams':
      await view.getByLabel('Number of teams').selectOption('3');
      assert.equal(
        await view
          .locator('.teams-results h3')
          .filter({ hasText: /^Team [123]$/ })
          .count(),
        3,
      );
      break;
    case 'projects':
      await fill(view, 'Project budget', 0);
      assert.equal(await view.locator('.projects-chart-row.funded').count(), 0);
      assert.match(await view.locator('[role="status"]').innerText(), /required|budget/i);
      await fill(view, 'Project budget', 50000);
      assert.ok((await view.locator('.projects-chart-row.funded').count()) > 0);
      assert.ok((await view.locator('.projects-chart-row.deferred').count()) > 0);
      break;
  }
}

async function verify(browser, platform, width, height) {
  const page = await browser.newPage({ viewport: { width, height } });
  const errors = [];
  page.on('pageerror', (error) => errors.push(error.message));
  page.on('response', (response) => {
    if (new URL(response.url()).origin === new URL(base).origin && response.status() >= 400) {
      errors.push(response.status() + ' ' + response.url());
    }
  });
  try {
    await page.goto(base);
    await page.getByRole('link', { name: 'Optimization problems' }).click();
    const select = page.getByLabel('Problem type', { exact: true });
    await page.locator('#problem-links a').first().waitFor({ state: 'attached' });
    assert.equal(await page.locator('#problem-links a').count(), ids.length);
    for (const id of ids) {
      if (width <= 820) await select.selectOption(id);
      else await page.locator('#problem-links a[href="#' + id + '"]').click();
      const view = page.locator('[data-problem="' + id + '"]:visible');
      await view.locator('.problem-figure').first().waitFor();
      const heading = await page.locator('#problem-title').innerText();
      assert.ok((await page.title()).includes(heading));
      assert.equal(await page.locator('.domain-view:visible').count(), 1);
      assert.ok(
        await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1),
        id + ' has horizontal page overflow at ' + width,
      );
      const result = view.locator('.problem-metrics').first();
      const initial = await result.innerText();
      assert.ok(initial.trim(), id + ' has no initial result');
      if (evidence && platform !== 'small-mobile') {
        if (id === 'facility') await view.locator('.leaflet-tile-loaded').first().waitFor();
        await page.evaluate(() => window.scrollTo(0, 0));
        const figureBottom = await view
          .locator('.problem-figure:visible')
          .last()
          .evaluate((node) => Math.ceil(node.getBoundingClientRect().bottom + window.scrollY));
        // Include the whole visualization even when the viewport ends halfway through it.
        await page.screenshot({
          path: path.join(evidence, id + '-' + platform + '.png'),
          animations: 'disabled',
          fullPage: true,
          clip: { x: 0, y: 0, width, height: Math.max(height, figureBottom) },
        });
      }
      await edit(id, view);
      assert.notEqual(await result.innerText(), initial, id + ' result did not respond to an edit');
      console.log(platform, heading, 'render and edit passed');
    }
    await page.goBack();
    assert.equal(await page.locator('[data-problem]:visible').getAttribute('data-problem'), 'projects');
    assert.equal(await page.getByRole('spinbutton', { name: 'Project budget' }).inputValue(), '50000');
    await page.getByRole('link', { name: 'Fleet dispatch', exact: true }).click();
    await page.locator('#scenario-map-type').waitFor();
    assert.deepEqual(errors, []);
  } finally {
    await page.close();
  }
}

(async () => {
  if (evidence) await fs.mkdir(evidence, { recursive: true });
  const browser = await chromium.launch({
    headless: true,
    args: ['--no-sandbox'],
    ...(process.env.BROWSER_EXECUTABLE ? { executablePath: process.env.BROWSER_EXECUTABLE } : {}),
  });
  try {
    for (const viewport of [
      ['desktop', 1440, 1000],
      ['mobile', 390, 844],
      ['small-mobile', 320, 740],
    ]) {
      await verify(browser, ...viewport);
    }
  } finally {
    await browser.close();
  }
})().catch((error) => {
  console.error(error);
  process.exitCode = 1;
});
