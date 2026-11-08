import { expect, test, type Page, type Route } from '@playwright/test';
import type { Evaluation } from '../../src/api/client';
import { decisionResponse, openAppeal } from '../appealFixtures';
import { historyConversationList, historyEvaluation, historyResult, newVersion, oldVersion, versionItems } from '../historyFixtures';

// Transport-intercepted UI regressions. Separate from tests/integration/history-real.spec.ts.
async function install(page: Page, override?: (route: Route, path: string) => Promise<boolean>) {
  const calls: { method: string; path: string }[] = [];
  await page.route('**/api/v1/**', async route => {
    const url = new URL(route.request().url()), path = url.pathname;
    calls.push({ method: route.request().method(), path });
    if (await override?.(route, path)) return;
    const offset = Number(url.searchParams.get('offset') ?? 0), limit = 5;
    const data: unknown = path.endsWith('/history/versions') ? { items: versionItems.slice(offset, offset + limit), total: 2, offset, limit }
      : path.endsWith('/history/evaluations') ? { items: [historyEvaluation], total: 1, offset, limit }
      : path === '/api/v1/conversations' ? historyConversationList
      : path === '/api/v1/conversation-versions/' + newVersion.id ? newVersion
      : path === '/api/v1/conversation-versions/' + oldVersion.id ? oldVersion
      : path === '/api/v1/checklist-versions/' + historyResult.checklist.id ? historyResult.checklist
      : path.endsWith('/appeals') ? { items: [] }
      : path === '/api/v1/evaluations/' + historyEvaluation.id ? historyResult.evaluation : null;
    await route.fulfill(data ? { json: data } : { status: 404, json: {} });
  });
  return calls;
}
async function open(page: Page) {
  await page.goto('/');
  await page.getByRole('button', { name: new RegExp(newVersion.conversation_id) }).click();
  const summary = page.getByText('История диалога', { exact: true });
  await summary.focus(); await page.keyboard.press('Enter');
}

test('историческая оценка и апелляция открываются только GET; evidence v1 на 390 px', async ({ page }) => {
  const appeal = openAppeal();
  const decided = decisionResponse(appeal, { action: 'override', reason: 'Синтетическое решение v1', override_answer: { type: 'boolean', value: false }, idempotency_key: 'history-decision' });
  const calls = await install(page, async (route, path) => {
    if (path.endsWith('/appeals')) { await route.fulfill({ json: { items: [{ ...appeal, status: 'resolved', decision: decided.decision }] } }); return true; }
    if (path === '/api/v1/evaluations/' + historyEvaluation.id) { await route.fulfill({ json: decided.evaluation }); return true; }
    return false;
  });
  await page.setViewportSize({ width: 390, height: 844 });
  await open(page);
  const versions = page.getByRole('region', { name: 'Версии диалога' });
  await versions.getByRole('button', { name: /Историческая версия/ }).click();
  await expect(page.getByText('Открыта историческая версия диалога.')).toBeVisible();
  await page.getByRole('button', { name: /Открыть оценку/ }).click();
  await expect(page.getByTestId('total-score')).toHaveText('85,38');
  await expect(page.getByRole('region', { name: 'Баллы по критериям' }).getByRole('meter', { name: /^C01/ })).toHaveAttribute('aria-valuenow', '10');
  await expect(page.getByTestId('effective-score')).toHaveText('70,00');
  await expect(page.getByText(/Синтетическое решение v1/)).toBeVisible();
  const evidence = page.getByRole('button', { name: 'Показать доказательство C01' });
  await evidence.focus(); await page.keyboard.press('Enter');
  await expect(page.locator('.message--highlighted').first()).toBeFocused();
  await expect(page.locator('.message--highlighted').first()).toContainText(oldVersion.messages[2]!.text);
  await expect(page.getByText(newVersion.messages[2]!.text, { exact: true })).toHaveCount(0);
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
  expect(calls.every(call => call.method === 'GET')).toBe(true);
  expect(calls.some(call => call.path.endsWith('/demo'))).toBe(false);
  await page.getByRole('button', { name: 'К истории диалога' }).click();
  await expect(page.getByRole('region', { name: 'Оценки диалога' })).toBeVisible();
});

test('страницы двух списков независимы; retry сохраняет offset; отображаются все статусы и режимы', async ({ page }) => {
  let failure = true;
  const offsets: number[] = [];
  const statuses: Evaluation['status'][] = ['queued', 'running', 'succeeded', 'failed'];
  await install(page, async (route, path) => {
    if (!path.endsWith('/history/evaluations')) return false;
    const offset = Number(new URL(route.request().url()).searchParams.get('offset'));
    offsets.push(offset);
    if (offset === 5 && failure) { await route.fulfill({ status: 500, json: {} }); return true; }
    const items = offset === 0 ? statuses.map((status, i) => ({ ...historyEvaluation, id: 'job-' + i, status, mode: i % 2 ? 'live' : 'mock' }))
      : [{ ...historyEvaluation, id: 'last-job' }];
    await route.fulfill({ json: { items, total: 6, offset, limit: 5 } }); return true;
  });
  await open(page);
  const jobs = page.getByRole('region', { name: 'Оценки диалога' });
  for (const status of statuses) await expect(jobs).toContainText(status);
  await expect(jobs).toContainText('mock'); await expect(jobs).toContainText('live');
  await jobs.getByRole('button', { name: 'Далее' }).click();
  await expect(jobs.getByRole('alert')).toBeVisible();
  failure = false; await jobs.getByRole('button', { name: 'Повторить загрузку' }).click();
  await expect(jobs).toContainText('last-job');
  await expect(jobs.getByRole('button', { name: 'Далее' })).toBeDisabled();
  await expect(page.getByRole('region', { name: 'Версии диалога' })).toContainText('Страница 1');
  expect(offsets[0]).toBe(0);
  expect(offsets.filter(offset => offset !== 0)).toEqual([5, 5]);
  await jobs.getByRole('button', { name: 'К началу истории' }).click();
  await expect(jobs).toContainText('Страница 1');
});

for (const failure of [404, 422, 'network'] as const) test('ошибка истории ' + failure + ' и повтор без нового задания', async ({ page }) => {
  let fail = true;
  const calls = await install(page, async (route, path) => {
    if (!path.endsWith('/history/versions') || !fail) return false;
    if (failure === 'network') await route.abort(); else await route.fulfill({ status: failure, json: {} });
    return true;
  });
  await open(page);
  const versions = page.getByRole('region', { name: 'Версии диалога' });
  await expect(versions.getByRole('alert')).toBeVisible();
  await expect(page.getByRole('button', { name: /Открыть оценку/ })).toBeVisible();
  fail = false; await versions.getByRole('button', { name: 'Повторить загрузку' }).click();
  await expect(versions.getByRole('button', { name: /Последняя версия/ })).toBeVisible();
  expect(calls.every(call => call.method === 'GET')).toBe(true);
});

test('пустая история оценок отличима от ошибки', async ({ page }) => {
  await install(page, async (route, path) => {
    if (!path.endsWith('/history/evaluations')) return false;
    await route.fulfill({ json: { items: [], total: 0, offset: 0, limit: 5 } }); return true;
  });
  await open(page);
  await expect(page.getByText(/Оценок пока нет/)).toBeVisible();
  await expect(page.getByRole('alert')).toHaveCount(0);
});

test('открытая queued оценка опрашивается до succeeded без POST', async ({ page }) => {
  let status: Evaluation['status'] = 'queued';
  const calls = await install(page, async (route, path) => {
    if (path !== '/api/v1/evaluations/' + historyEvaluation.id) return false;
    await route.fulfill({ json: status === 'succeeded' ? historyResult.evaluation
      : { ...historyResult.evaluation, status, output: null, original_score: null, effective_score: null } }); return true;
  });
  await open(page); await page.getByRole('button', { name: /Открыть оценку/ }).click();
  await expect(page.getByText('queued', { exact: true })).toBeVisible();
  status = 'running'; await expect(page.getByText('running', { exact: true })).toBeVisible();
  status = 'succeeded'; await expect(page.getByTestId('total-score')).toHaveText('85,38');
  expect(calls.every(call => call.method === 'GET')).toBe(true);
});

test('404 сохранённой оценки сохраняет путь к истории и GET retry', async ({ page }) => {
  let missing = true;
  const calls = await install(page, async (route, path) => {
    if (path !== '/api/v1/evaluations/' + historyEvaluation.id || !missing) return false;
    await route.fulfill({ status: 404, json: {} }); return true;
  });
  await open(page); await page.getByRole('button', { name: /Открыть оценку/ }).click();
  await expect(page.getByRole('alert')).toBeVisible();
  await expect(page.getByRole('button', { name: 'Начать новую оценку' })).toHaveCount(0);
  missing = false; await page.getByRole('button', { name: 'Повторить загрузку' }).click();
  await expect(page.getByTestId('total-score')).toHaveText('85,38');
  expect(calls.every(call => call.method === 'GET')).toBe(true);
});
