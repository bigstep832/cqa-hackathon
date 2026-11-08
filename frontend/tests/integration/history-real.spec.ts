import { expect, test } from '@playwright/test';
import type { ConversationVersion, Evaluation, EvaluationHistoryList, ImportResponse, VersionHistoryList } from '../../src/api/client';

// No page.route, API interception, sessionStorage seeding or Evaluation ID input.
// Actual FastAPI, queue, domain scorer and SQLite; upstream transports only are synthetic.
test('FastAPI + SQLite: v1 → evaluation → v2 → reload → history → evidence v1', async ({ page, request }, testInfo) => {
  const importVersion = async () => {
    const response = await request.post('/api/v1/datasets/import', { data: { group_id: 'B04_SYNTHETIC' } });
    expect(response.status(), await response.text()).toBe(200);
    const result = await response.json() as ImportResponse;
    expect(result.imported_count).toBe(1);
    return result.conversation_version_ids[0]!;
  };
  const v1 = await importVersion();
  const oldResponse = await request.get('/api/v1/conversation-versions/' + encodeURIComponent(v1));
  expect(oldResponse.ok()).toBe(true);
  const old = await oldResponse.json() as ConversationVersion;
  let posts = 0;
  page.on('request', incoming => { if (incoming.method() === 'POST' && new URL(incoming.url()).pathname === '/api/v1/evaluations') posts++; });

  await page.goto('/');
  await page.getByRole('button', { name: /B04\/synthetic-history/ }).click();
  await page.getByRole('button', { name: 'Оценить диалог', exact: true }).click();
  await page.getByLabel('Режим оценки', { exact: true }).selectOption('live');
  const checklist = page.getByLabel('Версия чек-листа', { exact: true });
  await expect(checklist.locator('option')).not.toHaveCount(1);
  await checklist.selectOption({ index: 1 });
  const checklistId = await checklist.inputValue();
  const createdResponse = page.waitForResponse(response => response.request().method() === 'POST'
    && new URL(response.url()).pathname === '/api/v1/evaluations');
  await page.getByRole('button', { name: 'Запустить оценку' }).click();
  const created = await (await createdResponse).json() as Evaluation;
  expect(created.conversation_version_id).toBe(v1);
  await expect(page.getByTestId('total-score')).toHaveText('85,38', { timeout: 20000 });

  const v2 = await importVersion();
  expect(v2).not.toBe(v1);
  const currentResponse = await request.get('/api/v1/conversation-versions/' + encodeURIComponent(v2));
  const current = await currentResponse.json() as ConversationVersion;
  expect(current.messages[2]!.text).toBe('B04 SYNTHETIC: changed operator message in v2.');
  expect(old.messages[2]!.text).not.toBe(current.messages[2]!.text);

  // Required discoverability path after a full page refresh: latest list → history.
  await page.reload();
  await page.getByRole('button', { name: /B04\/synthetic-history/ }).click();
  await expect(page.getByText(current.messages[2]!.text, { exact: true })).toBeVisible();
  await page.getByText('История диалога', { exact: true }).click();
  const versions = page.getByRole('region', { name: 'Версии диалога' });
  await expect(versions.getByRole('button', { name: /Последняя версия/ })).toContainText(v2);
  await expect(versions.getByRole('button', { name: /Историческая версия/ })).toContainText(v1);
  const history = page.getByRole('region', { name: 'Оценки диалога' });
  await expect(history.getByRole('button', { name: /Открыть оценку/ })).toHaveCount(1);
  await expect(history).toContainText(v1);
  await expect(history).toContainText(checklistId);
  await page.screenshot({ path: testInfo.outputPath('history-desktop.png'), fullPage: true });
  await history.getByRole('button', { name: /Открыть оценку/ }).click();
  await expect(page.getByRole('heading', { name: 'Сохранённая оценка' })).toBeVisible();
  await expect(page.getByRole('region', { name: 'Баллы по критериям' }).getByRole('meter', { name: /^C01/ })).toHaveAttribute('aria-valuenow', '10');
  await expect(page.getByTestId('total-score')).toHaveText('85,38');
  await expect(page.getByTestId('effective-score')).toHaveText('85,38');
  const evidence = page.getByRole('button', { name: 'Показать доказательство C01' });
  await evidence.focus(); await page.keyboard.press('Enter');
  const highlighted = page.locator('.message--highlighted').first();
  await expect(highlighted).toBeFocused();
  await expect(highlighted).toContainText(old.messages[2]!.text);
  await expect(page.getByText(current.messages[2]!.text, { exact: true })).toHaveCount(0);
  expect(posts).toBe(1);

  const savedResponse = await request.get('/api/v1/evaluations/' + encodeURIComponent(created.id));
  const saved = await savedResponse.json() as Evaluation;
  expect(saved.status).toBe('succeeded');
  expect(saved.conversation_version_id).toBe(v1);
  expect(saved.checklist_version_id).toBe(checklistId);
  expect(saved.original_score?.total).toBe('85.38');
  expect(saved.effective_score?.total).toBe('85.38');
  expect(saved.output?.answers[0]?.evidence[0]?.conversation_version_id).toBe(v1);
  const versionsResponse = await request.get('/api/v1/conversation-versions/' + encodeURIComponent(v2) + '/history/versions');
  const serverVersions = await versionsResponse.json() as VersionHistoryList;
  expect(serverVersions.items.map(item => item.id)).toEqual([v2, v1]);
  const jobsResponse = await request.get('/api/v1/conversation-versions/' + encodeURIComponent(v2) + '/history/evaluations');
  const serverJobs = await jobsResponse.json() as EvaluationHistoryList;
  expect(serverJobs.total).toBe(1);
  expect(serverJobs.items[0]?.id).toBe(created.id);
  const retainedV1 = await request.get('/api/v1/conversation-versions/' + encodeURIComponent(v1));
  expect(await retainedV1.json()).toEqual(old);

  await page.getByRole('button', { name: 'К истории диалога' }).click();
  await page.setViewportSize({ width: 390, height: 844 });
  await expect(page.getByRole('region', { name: 'Оценки диалога' })).toBeVisible();
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
  await page.screenshot({ path: testInfo.outputPath('history-mobile.png'), fullPage: true });
});
