import { expect, test } from '@playwright/test';
import type { AppealList, Evaluation } from '../../src/api/client';
import type { DemoPayload } from '../../src/api/demo';

// Explicit integration scenario. Creates a fresh persisted synthetic mock job
// and its appeals through the UI. No live evaluation or provider request.
test('FastAPI + SQLite: C01 override 85.38 → 70.00, C02 confirm, reload и evidence', async ({ page, request }) => {
  const demoResponse = await request.get('/api/v1/demo');
  expect(demoResponse.ok()).toBe(true);
  const demo = await demoResponse.json() as DemoPayload;
  expect(demo.conversation.synthetic).toBe(true);
  expect(demo.evaluation.original_score?.total).toBe('85.38');
  await page.goto('/');
  await page.getByRole('button', { name: 'Демо · mock', exact: true }).click();
  await page.getByRole('button', { name: 'Оценить встроенный пример' }).click();
  await page.getByLabel('Режим оценки', { exact: true }).selectOption('mock');
  await page.getByLabel('Версия чек-листа', { exact: true }).selectOption(demo.checklist.id);
  await page.getByRole('button', { name: 'Запустить оценку' }).click();
  await expect(page.getByTestId('total-score')).toHaveText('85,38');
  await expect(page.getByTestId('effective-score')).toHaveText('85,38');
  for (const criterion of ['C01', 'C02']) {
    await page.getByRole('button', { name: `Подать апелляцию ${criterion}`, exact: true }).click();
    await page.getByLabel(`Комментарий к апелляции ${criterion}`).fill(`DT20: синтетическая UI-проверка ${criterion}`);
    await page.getByRole('button', { name: 'Отправить апелляцию', exact: true }).click();
    await expect(page.getByText(`Комментарий: DT20: синтетическая UI-проверка ${criterion}`, { exact: true })).toBeVisible();
  }
  await page.getByRole('link', { name: 'Рассмотреть апелляции' }).click();
  await page.getByLabel('Действие проверяющего C01').selectOption('override');
  await page.getByLabel('Новый ответ C01').selectOption('1');
  await page.getByLabel('Причина решения C01').fill('Синтетическая учебная поправка: C01 = false');
  await page.getByRole('form', { name: 'Решение C01' }).getByRole('button', { name: 'Сохранить решение' }).click();
  await expect(page.getByTestId('effective-score')).toHaveText('70,00');
  await expect(page.getByTestId('total-score')).toHaveText('85,38');
  await page.getByLabel('Действие проверяющего C02').selectOption('confirm');
  await page.getByLabel('Причина решения C02').fill('Синтетическая проверка: исходный ответ C02 подтверждён');
  await page.getByRole('form', { name: 'Решение C02' }).getByRole('button', { name: 'Сохранить решение' }).click();
  await expect(page.getByLabel('Обращение C02', { exact: true })).toContainText('confirm — исходный ответ подтверждён');
  const evaluationId = decodeURIComponent(new URL(page.url()).hash.slice('#appeals/'.length));
  await page.reload();
  await expect(page.getByTestId('effective-score')).toHaveText('70,00');
  await expect(page.getByTestId('total-score')).toHaveText('85,38');
  await expect(page.getByLabel('Обращение C01', { exact: true })).toContainText('Синтетическая учебная поправка');
  await expect(page.getByLabel('Обращение C02', { exact: true })).toContainText('confirm — исходный ответ подтверждён');
  await page.getByRole('button', { name: 'Показать доказательство C01' }).click();
  await expect(page.locator('.message--highlighted').first()).toBeFocused();
  const evaluationResponse = await request.get(`/api/v1/evaluations/${encodeURIComponent(evaluationId)}`);
  expect(evaluationResponse.ok()).toBe(true);
  const saved = await evaluationResponse.json() as Evaluation;
  expect(saved.conversation_version_id).toBe(demo.conversation.id);
  expect(saved.checklist_version_id).toBe(demo.checklist.id);
  expect(saved.original_score?.total).toBe('85.38'); expect(saved.effective_score?.total).toBe('70.00');
  expect(saved.output?.answers).toEqual(demo.evaluation.output!.answers);
  const appealResponse = await request.get(`/api/v1/evaluations/${encodeURIComponent(evaluationId)}/appeals`);
  expect(appealResponse.ok()).toBe(true);
  const list = await appealResponse.json() as AppealList;
  expect(list.items.map(item => [item.criterion_id, item.status, item.decision?.action])).toEqual([
    ['C01', 'resolved', 'override'], ['C02', 'resolved', 'confirm'],
  ]);
});
