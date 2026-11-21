// Real page, editors, and local solvers. Only the model formulation endpoint is replaced.
const assert = require('node:assert/strict');
const fs = require('node:fs/promises');
const path = require('node:path');
const { chromium } = require(process.env.PLAYWRIGHT_MODULE || 'playwright');
const base = process.env.DASHBOARD_URL || 'http://127.0.0.1:8000';
const evidence = process.env.EVIDENCE_DIR;

const portfolio = {
  status: 'ready', domain: 'projects', summary: 'Choose the highest-value portfolio within a USD 90 budget.',
  assumptions: ['Values are points and all costs are in USD.'],
  inputs: { budget: 90, projects: [
    { id: 'P21', name: 'Microscope', cost: 60, value: 80, required: false, requires: '' },
    { id: 'P22', name: 'Workshop', cost: 40, value: 50, required: false, requires: '' },
    { id: 'P23', name: 'Materials', cost: 20, value: 25, required: false, requires: '' },
  ] },
};
const teamDraft = {
  status: 'ready', domain: 'teams', summary: 'Form two balanced pairs with a minimum of two points in every skill.',
  assumptions: ['All skill levels are 1; pair compatibility ranges from 0 to 10.'],
  inputs: { team_count: 2, minimum_skill: 2,
    people: ['Nia', 'Omar', 'Pia', 'Rui'].map((name, index) => ({ id: 'U' + (41 + index), name, skills: [1, 1, 1] })),
    compatibility: { 'U41|U42': 2, 'U41|U43': 5, 'U41|U44': 5, 'U42|U43': 5, 'U42|U44': 5, 'U43|U44': 5 },
  },
};

async function navigate(page, id, width) {
  if (width <= 820) await page.locator('#problem-select').selectOption(id);
  else if (id === 'describe') await page.locator('#describe-link').click();
  else await page.locator((id.startsWith('scenario-') ? '#scenario-links' : '#problem-links') + ' a[href="#' + id + '"]').click();
  await page.waitForFunction(value => location.hash === '#' + value, id);
  if (id === 'describe') await page.locator('#describe-panel').waitFor({ state: 'visible' });
  else await page.locator('[data-problem="' + id + '"]:not([aria-busy]):visible').waitFor();
}
async function capture(page, name) {
  if (!evidence) return;
  await page.evaluate(() => { document.activeElement?.blur(); window.scrollTo(0, 0); });
  await page.screenshot({ path: path.join(evidence, name + '.png'), fullPage: true });
}
async function check(browser, platform, width, height) {
  const context = await browser.newContext({ viewport: { width, height }, hasTouch: width < 821 });
  const page = await context.newPage(), errors = [], requests = [];
  let releaseCanceled;
  page.on('pageerror', error => errors.push(error.message));
  await page.route('**/api/problems/formulate', async route => {
    requests.push(route.request().postDataJSON());
    switch (requests.length) {
      case 1: return route.fulfill({ status: 503, json: { error: 'Draft provider is temporarily unavailable. Try again.' } });
      case 2: return route.fulfill({ json: { status: 'clarify', question: 'What is your total budget in USD?' } });
      case 3:
      case 4: return route.fulfill({ json: portfolio });
      case 5: return route.fulfill({ json: teamDraft });
      case 6: return route.fulfill({ json: { status: 'unsupported', message: 'This editor does not support nonlinear production rewards.' } });
      case 7:
        await new Promise(resolve => { releaseCanceled = resolve; });
        return route.fulfill({ json: portfolio }).catch(() => {});
      default: throw Error('Unexpected extra formulation request.');
    }
  });
  try {
    await page.goto(base + '/problems/#projects');
    const manual = page.locator('[data-problem="projects"]');
    await manual.locator('.projects-chart-row').first().waitFor();
    await manual.getByRole('spinbutton', { name: 'Project budget', exact: true }).fill('65000');
    const manualResult = await manual.locator('.projects-summary').textContent();
    await navigate(page, 'describe', width);
    const description = 'Choose among these illustrative projects: Microscope costs USD 60 and is worth 80 points; Workshop costs USD 40 for 50 points; Materials costs USD 20 for 25 points. None is required or has a prerequisite.';
    await page.locator('#description').fill(description);
    await page.locator('#draft-button').click();
    await page.waitForFunction(() => document.querySelector('#formulation-status').textContent.includes('temporarily unavailable'));
    assert.equal(await page.locator('#description').inputValue(), description);
    assert.equal(await page.locator('#description').evaluate(input => input.readOnly), false);
    assert.equal(await manual.getByRole('spinbutton', { name: 'Project budget', exact: true, includeHidden: true }).inputValue(), '65000');
    assert.equal(await manual.locator('.projects-summary').textContent(), manualResult);

    // Retry, clarify, and review without replacing the existing manual scenario.
    await page.locator('#draft-button').click();
    await page.locator('#clarification-form').waitFor({ state: 'visible' });
    assert.equal(await page.locator('#clarification-question').textContent(), 'What is your total budget in USD?');
    assert.equal(await page.locator('#description').evaluate(input => input.readOnly), true);
    await page.locator('#clarification-answer').fill('USD 90.');
    await page.locator('#clarification-form button').click();
    await page.locator('#draft-review').waitFor({ state: 'visible' });
    assert.match(await page.locator('#review-body').textContent(), /Microscope/);
    assert.match(await page.locator('#review-body').textContent(), /Values are points/);
    assert.equal(await page.locator('[data-problem^="scenario-"]').count(), 0, 'ready drafts require explicit acceptance');
    assert.equal(requests[2].description, description);
    assert.deepEqual(requests[2].clarifications, [{ question: 'What is your total budget in USD?', answer: 'USD 90.' }]);
    await capture(page, 'describe-review-' + platform);

    // Correcting a reviewed draft retains the answered facts in the description.
    await page.locator('#edit-description').click();
    assert.equal(await page.locator('#draft-review').isVisible(), false);
    assert.match(await page.locator('#description').inputValue(), /USD 90\./);
    assert.equal(await page.locator('#description').evaluate(input => input.readOnly), false);
    await page.locator('#draft-button').click();
    await page.locator('#draft-review').waitFor({ state: 'visible' });
    assert.match(requests[3].description, /USD 90\./);
    await page.locator('#open-draft').click();
    await page.locator('[data-problem="scenario-1"]:visible .projects-chart-row').first().waitFor();
    const approved = page.locator('[data-problem="scenario-1"]');
    assert.equal(await approved.getByRole('spinbutton', { name: 'Project budget', exact: true }).inputValue(), '90');
    assert.equal(await approved.locator('.projects-chart-row.funded').count(), 2);
    assert.match(await approved.locator('.projects-chart').innerText(), /Microscope/);
    assert.doesNotMatch(await approved.locator('.projects-chart').innerText(), /Solar canopy/);
    await approved.getByRole('spinbutton', { name: 'Project budget', exact: true }).fill('40');
    assert.equal(await approved.locator('.projects-chart-row.funded').count(), 1);
    assert.match(await approved.locator('.projects-chart-row.funded').innerText(), /Workshop/);
    await capture(page, 'describe-applied-' + platform);
    await navigate(page, 'projects', width);
    assert.equal(await manual.getByRole('spinbutton', { name: 'Project budget', exact: true }).inputValue(), '65000');
    assert.equal(await manual.locator('.projects-summary').textContent(), manualResult);
    await navigate(page, 'scenario-1', width);
    assert.equal(await approved.getByRole('spinbutton', { name: 'Project budget', exact: true }).inputValue(), '40');

    // A second domain uses its reviewed entity count and keeps manual state and IDs separate.
    await navigate(page, 'teams', width);
    const manualTeams = page.locator('[data-problem="teams"]');
    await manualTeams.getByRole('combobox', { name: 'Number of teams', exact: true }).selectOption('3');
    await navigate(page, 'describe', width);
    await page.locator('#edit-description').click();
    await page.locator('#description').fill('Create an illustrative two-team model for Nia, Omar, Pia and Rui. Each has level 1 in all three skills; each team needs level 2 per skill. All pair ratings are 5 except Nia and Omar at 2.');
    await page.locator('#draft-button').click();
    await page.locator('#draft-review').waitFor({ state: 'visible' });
    await page.locator('#open-draft').click();
    const approvedTeams = page.locator('[data-problem="scenario-2"]');
    await approvedTeams.locator('.team-card').first().waitFor();
    assert.equal(await approvedTeams.locator('.teams-person').count(), 4);
    assert.doesNotMatch(await approvedTeams.locator('.teams-assignment').innerText(), /Ava/);
    await approvedTeams.getByRole('button', { name: 'Nia and Omar: compatibility 2 out of 10', exact: true }).click();
    await approvedTeams.getByRole('slider', { name: 'Pair compatibility rating', exact: true }).fill('10');
    assert.equal(await approvedTeams.locator('.team-card').filter({ hasText: 'Nia' }).filter({ hasText: 'Omar' }).count(), 1);
    const ratingId = await approvedTeams.getByRole('slider', { name: 'Pair compatibility rating', exact: true }).getAttribute('id');
    assert.equal(await approvedTeams.locator('output').getAttribute('for'), ratingId);
    assert.equal(await manualTeams.getByRole('combobox', { name: 'Number of teams', exact: true, includeHidden: true }).inputValue(), '3');
    const duplicateIds = await page.evaluate(() => {
      const ids = [...document.querySelectorAll('[id]')].map(node => node.id);
      return ids.filter((id, index) => ids.indexOf(id) !== index);
    });
    assert.deepEqual(duplicateIds, [], 'manual and approved panels must not share HTML IDs');

    await navigate(page, 'describe', width);
    await page.locator('#edit-description').click();
    await page.locator('#description').fill('Optimize nonlinear production rewards.');
    await page.locator('#draft-button').click();
    await page.waitForFunction(() => document.querySelector('#formulation-status').textContent.includes('does not support'));
    assert.equal(await page.locator('#draft-review').isVisible(), false);
    assert.equal(await page.locator('[data-problem^="scenario-"]').count(), 2);
    await page.locator('#description').fill(description);
    await page.locator('#draft-button').click();
    await page.locator('#cancel-formulation').waitFor({ state: 'visible' });
    const deadline = Date.now() + 5000;
    while (!releaseCanceled) {
      if (Date.now() > deadline) throw Error('Canceled request did not reach the formulation fixture.');
      await page.waitForTimeout(10);
    }
    await page.locator('#cancel-formulation').click();
    releaseCanceled();
    await page.waitForTimeout(75);
    assert.equal(await page.locator('#draft-review').isVisible(), false);
    assert.equal(await page.locator('#description').evaluate(input => input.readOnly), false);
    assert.match(await page.locator('#formulation-status').innerText(), /canceled/);
    assert.equal(await page.evaluate(() => document.documentElement.scrollWidth > innerWidth), false);
    assert.deepEqual(errors, []);
    assert.equal(requests.length, 7);
    console.log('PASS: ' + platform + ', manual state, retry, clarification, review, explicit acceptance, editable imported inputs, unique IDs, cancellation, no overflow/errors');
  } finally { releaseCanceled?.(); await context.close(); }
}

(async () => {
  if (evidence) await fs.mkdir(evidence, { recursive: true });
  const browser = await chromium.launch({ headless: true, args: ['--no-sandbox'],
    ...(process.env.BROWSER_EXECUTABLE ? { executablePath: process.env.BROWSER_EXECUTABLE } : {}),
  });
  try {
    for (const viewport of [['desktop', 1440, 1080], ['mobile', 390, 844], ['small-mobile', 320, 740]]) await check(browser, ...viewport);
  } finally { await browser.close(); }
})().catch(error => { console.error(error); process.exitCode = 1; });
