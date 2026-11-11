// Browser regression smoke test. Start serve.py before running npm run test:e2e.
import assert from 'node:assert/strict';
import fs from 'node:fs/promises';
import { fileURLToPath } from 'node:url';
import { chromium } from 'playwright';

const url = process.env.EDITOR_URL || 'http://127.0.0.1:8000';
const browser = await chromium.launch({ channel: process.env.EDITOR_BROWSER || 'msedge', headless: true });
const page = await browser.newPage({ viewport: { width: 1440, height: 1080 }, deviceScaleFactor: 1 });
const errors = [];
page.on('pageerror', error => errors.push(error.message));
const screenshots = new URL('../../.cache/editor-ui/', import.meta.url);
await fs.mkdir(screenshots, { recursive: true });
try {
  await page.goto(url);
  await page.getByRole('heading', { name: 'Review changes', level: 1 }).waitFor();
  await page.locator('.comparison-grid .slide-image.loaded').first().waitFor({ timeout: 30000 });
  await page.locator('.comparison-grid .slide-image.loaded').nth(1).waitFor({ timeout: 30000 });
  assert.equal(await page.locator('[role="alert"]').count(), 0);
  await page.locator('.comparison-grid .slide-image').first().hover();
  await page.getByRole('button', { name: /^Enlarge Original slide/ }).click();
  await page.getByRole('dialog').waitFor();
  await page.keyboard.press('Escape');
  await page.getByRole('dialog').waitFor({ state: 'hidden' });
  assert.ok(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth));
  await page.screenshot({ path: fileURLToPath(new URL('review-desktop.png', screenshots)), animations: 'disabled' });
  const approve = page.getByRole('button', { name: 'Approve change', exact: true });
  if (await approve.isEnabled()) {
    assert.ok((await page.locator('.approval-bar').innerText()).includes('Pending your approval'));
    await approve.click();
    await page.getByRole('button', { name: 'Approved', exact: true }).waitFor();
    await page.getByRole('button', { name: 'Clear decision', exact: true }).click();
    await approve.waitFor();
    await page.reload();
    await page.getByText('Pending your approval', { exact: true }).waitFor();
  }
  const blockedChoice = page.locator('.proposal-switcher button').filter({ has: page.locator('.mini-dot.blocked') }).first();
  if (await blockedChoice.count()) {
    await blockedChoice.click();
    await page.getByText('Why approval is blocked', { exact: true }).waitFor();
    assert.equal(await approve.isEnabled(), false);
    assert.ok((await page.locator('.findings li').first().innerText()).length > 20);
    await page.locator('.proposal-switcher button').filter({ has: page.locator('.mini-dot.pass') }).first().click();
  }
  for (const name of ['Gap report', 'Slide text', 'Suggested text', 'Student questions']) {
    await page.getByRole('tab', { name, exact: name !== 'Student questions' }).click();
  }
  await page.getByRole('button', { name: 'Evaluation', exact: true }).click();
  await page.getByRole('heading', { name: 'Evaluation', level: 1 }).waitFor();
  const downloadWait = page.waitForEvent('download');
  await page.getByRole('button', { name: 'Review form', exact: true }).click();
  const download = await downloadWait;
  const stream = await download.createReadStream();
  const chunks = []; for await (const chunk of stream) chunks.push(chunk);
  const form = JSON.parse(Buffer.concat(chunks).toString());
  assert.equal(form.questions.length, 140);
  await page.locator('input[type=file]').setInputFiles({ name: 'blank-review.json', mimeType: 'application/json', buffer: Buffer.from(JSON.stringify(form)) });
  await page.getByRole('heading', { name: 'Review completeness & outcomes' }).waitFor();
  assert.ok((await page.locator('.stats-grid').first().innerText()).includes('Not measured'));
  await page.screenshot({ path: fileURLToPath(new URL('evaluation-desktop.png', screenshots)), fullPage: true });
  await page.getByRole('button', { name: 'Activity', exact: true }).click();
  await page.getByRole('heading', { name: 'Activity', level: 1 }).waitFor();
  await page.locator('summary').click();
  assert.ok((await page.locator('.policy-grid').innerText()).includes('Groq'));
  await page.getByRole('button', { name: 'Evaluation', exact: true }).click();
  await page.getByRole('heading', { name: 'Review completeness & outcomes' }).waitFor();
  await page.getByRole('button', { name: 'Layout repairs', exact: true }).click();
  await page.getByRole('button', { name: 'Scan layouts', exact: true }).click();
  await page.locator('.layout-card').first().waitFor({ timeout: 30000 });
  assert.ok(await page.locator('.layout-card').count() > 0);
  await page.getByRole('button', { name: 'Review changes', exact: true }).click();
  await page.setViewportSize({ width: 390, height: 844 });
  await page.getByRole('heading', { name: 'Review changes', level: 1 }).waitFor();
  await page.locator('.comparison-grid .slide-image.loaded').nth(1).waitFor({ timeout: 30000 });
  await page.locator('[role="status"]').waitFor({ state: 'hidden', timeout: 8000 });
  assert.ok(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth), 'Mobile page must not overflow horizontally');
  await page.screenshot({ path: fileURLToPath(new URL('review-mobile.png', screenshots)), fullPage: true, animations: 'disabled' });
  assert.deepEqual(errors, []);
  console.log('Browser smoke passed: real previews, approval/reset, tabs, review download/upload, activity, layout scan, desktop/mobile layouts, no page errors.');
} finally { await browser.close(); }
