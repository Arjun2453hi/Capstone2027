// Browser regressions for review navigation and live Activity details.
// Start serve.py first. API fixtures exercise active jobs without model calls.
import assert from 'node:assert/strict';
import fs from 'node:fs/promises';
import { fileURLToPath } from 'node:url';
import { chromium } from 'playwright';

const url = process.env.EDITOR_URL || 'http://127.0.0.1:8000';
const browser = await chromium.launch({ channel: process.env.EDITOR_BROWSER || 'msedge', headless: true });
const page = await browser.newPage({ viewport: { width: 1440, height: 1080 } });
const errors = [];
page.on('pageerror', error => errors.push(error.message));
const screenshots = new URL('../../.cache/editor-ui/', import.meta.url);
await fs.mkdir(screenshots, { recursive: true });
const screenshot = (name, fullPage = true) => page.screenshot({ path: fileURLToPath(new URL(name, screenshots)), fullPage, animations: 'disabled' });
try {
  // Real server: every source slide must be available, with its API question links.
  await page.goto(url);
  await page.getByRole('heading', { name: 'Review changes', level: 1 }).waitFor();
  await page.locator('.slide-choice').first().waitFor();
  const workspace = await (await page.request.get(`${url}/api/workspace`)).json();
  const topicId = workspace.topics.find(t => t.eligible)?.id ?? workspace.topics[0].id;
  const topic = await (await page.request.get(`${url}/api/topics/${topicId}`)).json();
  const proposals = await (await page.request.get(`${url}/api/proposals`)).json();
  const candidates = proposals.filter(p => p.topic_id === topicId && p.operation !== 'repair_slide');
  assert.equal(await page.locator('.slide-choice:not(.proposed)').count(), topic.slides.length);
  assert.equal(await page.locator('.slide-choice.proposed').count(), candidates.length);
  assert.equal(await page.getByRole('button', { name: 'Layout repairs', exact: true }).count(), 0);
  assert.ok(Array.isArray(topic.question_links), 'Restarted API must expose slide-level question links');
  await page.getByRole('region', { name: 'Question to slide changes' }).waitFor();
  await page.locator('.comparison-grid .slide-image.loaded').first().waitFor({ timeout: 30000 });
  await page.locator('.slide-choice:not(.proposed)').last().click();
  await page.getByRole('tab', { name: 'Slide text', exact: true }).click();
  if (topic.slides.at(-1).text) assert.equal((await page.getByRole('tabpanel').innerText()).trim(), topic.slides.at(-1).text.trim());
  await page.locator('.comparison-grid').getByRole('button', { name: 'Enlarge Original slide', exact: false }).click();
  await page.getByRole('dialog').waitFor();
  await page.keyboard.press('Escape');
  await page.getByRole('dialog').waitFor({ state: 'hidden' });
  await page.getByRole('tab', { name: 'Gap report', exact: true }).click();
  await screenshot('review-desktop.png');

  // Stable two-slide/two-proposal fixture, including multiple questions per slide.
  const q1 = 'How does the first mechanism work?';
  const q2 = 'Why is the first mechanism useful?';
  const q3 = 'How does the second mechanism differ?';
  const q4 = 'Which explanation is missing entirely?';
  const q5 = 'Which question is already covered?';
  const q6 = 'Which question has no individual assessment?';
  const fixtureTopic = { slides: [{ id: 0, title: 'First source', text: 'First source text' }, { id: 1, title: 'Second source', text: 'Second source text' }], questions: [q1, q2, q3, q4, q5, q6], question_links: [{ question: q1, slide_id: 0 }, { question: q2, slide_id: 0 }, { question: q3, slide_id: 1 }], report: { report_text: 'Fixture report', question_verdicts: [{ question: q4, verdict: 'complete_omission' }, { question: q5, verdict: 'covered' }] } };
  const fixtureProposals = [
    { id: 'fixture-a', topic_id: 0, after_slide_id: 0, operation: 'insert_slide_after', title: 'New first explanation', bullets: ['First explanation.'], addresses_questions: [q1, q2], accepted: true, decision: 'approved', revisions: 0, problems: [] },
    { id: 'fixture-b', topic_id: 0, after_slide_id: 1, operation: 'append_bullets', title: 'Second explanation', bullets: ['Second explanation.'], addresses_questions: [q3], accepted: true, decision: 'pending', revisions: 1, problems: [] },
  ];
  let detailRequests = 0;
  let generatedQuestion = null;
  const run = { id: 'fixture-run', kind: 'gap', target_id: 0, status: 'running', message: 'Checking initial proposal', created_at: Date.now() / 1000 };
  await page.route('**/api/**', async route => {
    const path = new URL(route.request().url()).pathname;
    let data;
    if (path === '/api/workspace') data = { ...workspace, topics: [{ id: 0, title: 'Fixture topic', slide_ids: [0, 1], eligible: true, gap_type: 'shallow_coverage', confidence: .9, question_count: 3 }] };
    else if (path === '/api/topics/0') data = fixtureTopic;
    else if (path === '/api/proposals') data = fixtureProposals;
    else if (path === '/api/runs' && route.request().method() === 'POST') { generatedQuestion = route.request().postDataJSON().question; data = run; }
    else if (path === '/api/runs') data = [run, { ...run, id: 'hidden-repair', kind: 'repair' }];
    else if (path === '/api/runs/fixture-run') {
      detailRequests++;
      data = { ...run, status: detailRequests > 1 ? 'completed' : 'running', message: detailRequests > 1 ? 'Both proposals checked' : 'Checking initial proposal', events: [{ at: run.created_at, event: 'stage', message: detailRequests > 1 ? 'Live update received' : 'Initial event' }] };
    } else return route.continue();
    await route.fulfill({ json: data });
  });
  await page.reload();
  await page.locator('.slide-choice').first().waitFor();
  assert.equal(await page.locator('.slide-choice').count(), 4);
  const mapping = page.getByRole('region', { name: 'Question to slide changes' });
  assert.equal(await mapping.locator('.question-change-card').count(), 6, 'All questions are visible by default');
  await mapping.getByRole('button', { name: 'Needs changes (4)', exact: true }).click();
  assert.equal(await mapping.locator('.question-change-card').count(), 4);
  const firstQuestion = mapping.getByRole('article', { name: q1, exact: true });
  assert.ok((await firstQuestion.innerText()).includes('New slide after original slide 1'));
  assert.ok((await firstQuestion.innerText()).includes('First explanation.'));
  assert.equal(await firstQuestion.getByText('Approved for export', { exact: true }).count(), 1);
  const thirdQuestion = mapping.getByRole('article', { name: q3, exact: true });
  assert.ok((await thirdQuestion.innerText()).includes('Changes on original slide 2'));
  assert.equal(await thirdQuestion.getByText('Needs approval', { exact: true }).count(), 1);
  assert.equal(await mapping.locator('.addressed').count(), 0, 'Selection must not mark question coverage');
  await firstQuestion.getByRole('button', { name: 'View in topic slide order' }).click();
  assert.equal(await page.locator('.comparison-grid .slide-panel').count(), 2);
  await page.locator('.slide-choice:not(.proposed)').last().click();
  assert.equal(await page.locator('.comparison-grid .slide-panel').count(), 1);
  assert.equal(await firstQuestion.getByText('Approved for export', { exact: true }).count(), 1);
  await mapping.getByRole('button', { name: 'All questions (6)', exact: true }).click();
  assert.equal(await mapping.locator('.question-change-card').count(), 6);
  assert.ok((await mapping.getByRole('article', { name: q5, exact: true }).innerText()).includes('Change needed: No'));
  assert.ok((await mapping.getByRole('article', { name: q6, exact: true }).innerText()).includes('Change needed: Not assessed'));
  const deckPreview = page.getByRole('region', { name: 'Updated slide deck', exact: true });
  assert.equal(await deckPreview.locator('.deck-thumbnails button').count(), 3);
  await deckPreview.getByRole('button', { name: 'Next deck slide', exact: true }).click();
  assert.ok((await deckPreview.locator('.deck-page-controls').innerText()).includes('New slide after original slide 1'));
  await deckPreview.getByRole('button', { name: 'Next deck slide', exact: true }).click();
  assert.ok((await deckPreview.locator('.deck-page-controls').innerText()).includes('Updated original slide 2'));
  await screenshot('question-change-mapping.png');
  // A gap without a proposal must remain visible, rather than being shown as covered.
  assert.ok((await mapping.getByRole('article', { name: q4, exact: true }).innerText()).includes('no slide change has been generated'));
  await page.getByRole('button', { name: 'Next slide', exact: true }).click();
  assert.equal(await page.getByRole('button', { name: 'Next slide', exact: true }).isEnabled(), false);
  await page.getByRole('button', { name: 'Activity', exact: true }).click();
  assert.equal(await page.locator('.run-row').count(), 1);
  const details = page.getByRole('button', { name: 'Details', exact: true });
  await details.click();
  const detailPanel = page.getByRole('region', { name: 'Run details' });
  await detailPanel.getByText('Initial event', { exact: false }).waitFor();
  assert.equal(await details.getAttribute('aria-expanded'), 'true');
  await detailPanel.getByText('Live update received', { exact: false }).waitFor({ timeout: 10000 });
  assert.ok(detailRequests >= 2);
  await screenshot('activity-details.png');
  await page.getByRole('button', { name: 'Close run details', exact: true }).click();
  assert.equal(await detailPanel.count(), 0);
  await details.click();
  await detailPanel.waitFor();
  await details.click();
  assert.equal(await detailPanel.count(), 0);
  await page.getByRole('button', { name: 'Review changes', exact: true }).click();
  await page.setViewportSize({ width: 390, height: 844 });
  await page.locator('.slide-choice').first().waitFor();
  assert.ok(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth), 'Mobile review must not overflow horizontally');
  await screenshot('review-mobile.png');
  // Complete the fixture's visible run before starting another question-specific task.
  await page.route('**/api/runs', route => route.request().method() === 'GET' ? route.fulfill({ json: [] }) : route.fallback());
  await page.waitForResponse(response => new URL(response.url()).pathname === '/api/runs' && response.request().method() === 'GET', { timeout: 6000 });
  await Promise.all([page.waitForResponse(response => new URL(response.url()).pathname === '/api/runs' && response.request().method() === 'POST'), mapping.getByRole('button', { name: 'Generate change for this question', exact: true }).click()]);
  assert.equal(generatedQuestion, q4);

  assert.deepEqual(errors, []);
  console.log('Browser regressions passed: all source/new slides, dynamic multi-question links, live inline run details, removed repairs, real previews, mobile overflow. No model calls or approval changes.');
} finally { await browser.close(); }
