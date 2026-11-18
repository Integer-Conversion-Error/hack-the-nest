// Requires a running local dashboard; exercises actual recorded and classical data.
const assert = require('node:assert/strict');
const fs = require('node:fs/promises');
const path = require('node:path');
const { chromium } = require(process.env.PLAYWRIGHT_MODULE || 'playwright');
const base = process.env.DASHBOARD_URL || 'http://127.0.0.1:8000';
const evidence = process.env.EVIDENCE_DIR;

async function seek(page, minute) {
  await page.locator('#scrub').evaluate((element, value) => {
    element.value = String(value);
    element.dispatchEvent(new Event('input', { bubbles: true }));
  }, minute);
}

async function check(browser, platform, width, height, reducedMotion = 'no-preference') {
  const context = await browser.newContext({ viewport: { width, height }, reducedMotion,
    ...(evidence && platform === 'desktop' ? { recordVideo: { dir: evidence, size: { width: 1440, height: 1080 } } } : {}),
  });
  const page = await context.newPage();
  const video = page.video();
  const errors = [];
  page.on('pageerror', error => errors.push(error.message));
  try {
    await page.goto(base);
    await page.locator('#recorded-example').click();
    await page.locator('#results-screen').waitFor();
    await page.locator('#classical-map-view').waitFor();
    assert.equal(await page.locator('#fleet-map').isVisible(), true);
    assert.equal(await page.locator('#classical-map').isVisible(), true);
    assert.match(await page.locator('#reference-check').textContent(), /Exact optimum/);
    assert.equal(await page.locator('#gap-note').textContent(), 'Same cost');
    assert.equal(await page.locator('#map-grid').getAttribute('data-playing'), 'false', 'results never autoplay');
    await seek(page, 35);
    assert.equal(await page.locator('#quantum-delivered').textContent(), '1 / 2 delivered');
    assert.equal(await page.locator('#classical-delivered').textContent(), '1 / 2 delivered');
    await page.waitForTimeout(350); // Capture the settled layout after the entry/value transitions.
    assert.equal(await page.evaluate(() => document.documentElement.scrollWidth > innerWidth), false);
    const boxes = await Promise.all(['#fleet-map', '#classical-map'].map(selector => page.locator(selector).boundingBox()));
    if (width > 1100) assert.ok(Math.abs(boxes[0].y - boxes[1].y) < 1, 'desktop maps share a row');
    else assert.ok(boxes[1].y >= boxes[0].y + boxes[0].height, 'narrow maps stack');
    if (evidence && platform !== 'small-mobile' && platform !== 'reduced-motion') {
      await page.screenshot({ path: path.join(evidence, `after-${platform}.png`), fullPage: true });
    }

    // Both canvases use the same seek/play state and can be inspected separately.
    await page.locator('#classical-map').focus();
    await page.keyboard.press('v');
    assert.match(await page.locator('#pygame-inspector').textContent(), /Classical/);
    await page.locator('#fleet-map').focus();
    await page.keyboard.press('v');
    assert.match(await page.locator('#pygame-inspector').textContent(), /Quantum/);
    await page.keyboard.press('Escape');
    await seek(page, 0);
    await page.locator('#speed').selectOption('1');
    const before = await page.locator('#fleet-map').evaluate(canvas => canvas.toDataURL());
    const classicalBefore = await page.locator('#classical-map').evaluate(canvas => canvas.toDataURL());
    await page.locator('#play').click();
    await page.waitForTimeout(platform === 'desktop' ? 4200 : 1100);
    assert.ok(Number(await page.locator('#scrub').inputValue()) > 0, 'manual playback advances even in reduced-motion mode');
    assert.notEqual(await page.locator('#fleet-map').evaluate(canvas => canvas.toDataURL()), before);
    assert.notEqual(await page.locator('#classical-map').evaluate(canvas => canvas.toDataURL()), classicalBefore);
    if (reducedMotion === 'reduce') {
      const animation = await page.locator('.quantum-map-view .solver-dot').evaluate(element => getComputedStyle(element).animationName);
      assert.equal(animation, 'none', 'reduced-motion mode suppresses decorative pulses');
    }
    await page.locator('#play').click();
    const paused = await page.locator('#scrub').inputValue();
    await page.waitForTimeout(250);
    assert.equal(await page.locator('#scrub').inputValue(), paused, 'pause stops the shared playhead');
    await seek(page, 120);
    assert.equal(await page.locator('#quantum-delivered').textContent(), '2 / 2 delivered');
    assert.equal(await page.locator('#classical-delivered').textContent(), '2 / 2 delivered');

    // Changing mode removes the second replay when only a classical reference is shown.
    await page.locator('#mode').selectOption('reference');
    await page.waitForFunction(() => document.querySelector('#run-badge').textContent === 'Exact reference');
    assert.equal(await page.locator('#classical-map-view').isVisible(), false);
    assert.equal(await page.locator('#comparison-note').isVisible(), false);
    const config = await (await page.request.get(base + '/api/config')).json();
    if (platform === 'desktop' && config.local_backend_configured && !config.pipeline_backend_configured) {
      // Exercise real local solves and the verified cache without calling a model service.
      await page.locator('#new-problem').click();
      await page.locator('#load-example').click();
      await page.locator('#generate').click();
      await page.locator('#results-screen').waitFor();
      const slider = page.locator('#variable-van_2_initial_charge_pct-slider');
      for (const charge of [22, 35]) {
        const response = page.waitForResponse(value => value.url().endsWith('/api/run') && value.request().method() === 'POST');
        await slider.evaluate((element, value) => {
          element.value = String(value);
          element.dispatchEvent(new Event('input', { bubbles: true }));
          element.dispatchEvent(new Event('change', { bubbles: true }));
        }, charge);
        const data = await (await response).json();
        assert.equal(data.effective_values.van_2_initial_charge_pct, charge);
        await page.waitForFunction(() => !document.querySelector('#new-problem').disabled);
        if (charge === 35) {
          assert.equal(data.cached, true, 'returning to verified settings reuses the actual result cache');
          assert.equal(await page.locator('#status').textContent(), 'Reused verified result');
        }
      }
    }
    assert.deepEqual(errors, []);
    console.log(`PASS: ${platform}, recorded comparison, synchronized playback, independent inspection, mode fallback, no overflow/errors`);
  } finally {
    await context.close();
    if (video && evidence) {
      const original = await video.path();
      const destination = path.join(evidence, 'synchronized-playback.webm');
      await video.saveAs(destination);
      if (original !== destination) await fs.unlink(original);
    }
  }
}

(async () => {
  if (evidence) await fs.mkdir(evidence, { recursive: true });
  const browser = await chromium.launch({ headless: true,
    ...(process.env.BROWSER_EXECUTABLE ? { executablePath: process.env.BROWSER_EXECUTABLE } : {}), args: ['--no-sandbox'],
  });
  try {
    for (const viewport of [['desktop', 1440, 1080], ['mobile', 390, 844], ['small-mobile', 320, 740], ['reduced-motion', 1280, 900, 'reduce']]) {
      await check(browser, ...viewport);
    }
  } finally { await browser.close(); }
})().catch(error => { console.error(error); process.exitCode = 1; });
