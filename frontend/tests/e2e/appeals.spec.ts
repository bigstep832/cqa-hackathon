import { expect, test, type Page } from '@playwright/test';
import type { Appeal, AppealRequest, DecisionRequest } from '../../src/api/client';
import { checklistList, conversationList, historicalChecklist, historicalConversation } from '../evaluationFixtures';
import { decisionResponse, openAppeal, originalEvaluation } from '../appealFixtures';

async function mockApi(page: Page, options: { decisionFailure?: number | 'network'; resolvedConflict?: boolean; existing?: boolean; listFailure?: number } = {}) {
  let current = originalEvaluation();
  const appeals = new Map<string, Appeal>();
  if (options.existing) appeals.set('C01', openAppeal());
  const posts: DecisionRequest[] = [], paths: string[] = [];
  let failure = options.decisionFailure, listFailure = options.listFailure;
  await page.route('**/api/v1/**', async route => {
    const path = new URL(route.request().url()).pathname; paths.push(path);
    if (path.endsWith('/appeals')) {
      if (route.request().method() === 'POST') {
        const body = route.request().postDataJSON() as AppealRequest;
        const appeal = appeals.get(body.criterion_id) ?? { ...openAppeal(body.criterion_id), comment: body.comment };
        appeals.set(body.criterion_id, appeal); await route.fulfill({ status: 201, json: appeal }); return;
      }
      if (listFailure) { await route.fulfill({ status: listFailure, json: {} }); return; }
      await route.fulfill({ json: { items: [...appeals.values()] } }); return;
    }
    if (path.endsWith('/decision')) {
      const body = route.request().postDataJSON() as DecisionRequest; posts.push(body);
      const appeal = [...appeals.values()].find(item => path.includes(item.id))!;
      if (failure) {
        const status = failure; failure = undefined;
        if (options.resolvedConflict) {
          const saved = decisionResponse(appeal, { ...body, reason: 'Решение другого проверяющего', idempotency_key: 'other-key' }, current);
          current = saved.evaluation; appeals.set(appeal.criterion_id, { ...appeal, status: 'resolved', decision: saved.decision });
        }
        if (status === 'network') await route.abort('failed');
        else await route.fulfill({ status, json: { error: { code: 'TEST_ERROR', message: 'Синтетическая ошибка решения', details: [] } } });
        return;
      }
      const saved = decisionResponse(appeal, body, current); current = saved.evaluation;
      appeals.set(appeal.criterion_id, { ...appeal, status: 'resolved', decision: saved.decision });
      await route.fulfill({ json: saved }); return;
    }
    const data = path === '/api/v1/conversations' ? conversationList : path === '/api/v1/checklists' ? checklistList
      : path.startsWith('/api/v1/evaluations') ? current : path.endsWith(historicalChecklist.id) ? historicalChecklist
      : path.endsWith(historicalConversation.id) ? historicalConversation : null;
    await route.fulfill(data ? { json: data, status: route.request().method() === 'POST' ? 202 : 200 } : { status: 404, json: {} });
  });
  return { posts, paths, allowList: () => { listFailure = undefined; } };
}
async function openResult(page: Page) {
  await page.goto('/');
  await page.getByRole('button', { name: new RegExp(historicalConversation.conversation_id) }).click();
  await page.getByRole('button', { name: 'Оценить диалог', exact: true }).click();
  await page.getByLabel('Версия чек-листа', { exact: true }).selectOption(historicalChecklist.id);
  await page.getByLabel('Режим оценки', { exact: true }).selectOption('live');
  await page.getByRole('button', { name: 'Запустить оценку' }).click();
  await expect(page.getByTestId('total-score')).toHaveText('85,38');
}
async function appeal(page: Page, criterion: string) {
  await page.getByRole('button', { name: `Подать апелляцию ${criterion}`, exact: true }).click();
  await page.getByLabel(`Комментарий к апелляции ${criterion}`).fill(`Синтетическое обращение ${criterion}`);
  await page.getByRole('button', { name: 'Отправить апелляцию', exact: true }).click();
  await expect(page.getByText(`Комментарий: Синтетическое обращение ${criterion}`, { exact: true })).toBeVisible();
}
async function configureOverride(page: Page) {
  await page.getByLabel('Действие проверяющего C01').selectOption('override');
  await page.getByLabel('Новый ответ C01').selectOption('1');
  await page.getByLabel('Причина решения C01').fill('Синтетическая поправка C01');
}

test('создание C01/C02, override 85.38 → 70.00, confirm, reload и историческое evidence', async ({ page }) => {
  const api = await mockApi(page);
  await openResult(page); await appeal(page, 'C01'); await appeal(page, 'C02');
  await page.getByRole('link', { name: 'Рассмотреть апелляции' }).click();
  await expect(page).toHaveURL(/#appeals\/synthetic-evaluation-1$/);
  await configureOverride(page);
  await page.getByRole('form', { name: 'Решение C01' }).getByRole('button', { name: 'Сохранить решение' }).click();
  await expect(page.getByTestId('effective-score')).toHaveText('70,00');
  await expect(page.getByTestId('total-score')).toHaveText('85,38');
  const c01 = page.getByLabel('Обращение C01', { exact: true });
  await expect(c01).toContainText('Исходный ответ: Да (true)');
  await expect(c01).toContainText('Ответ проверяющего: Нет (false)');
  await expect(c01).toContainText('Причина: Синтетическая поправка C01');
  await page.getByLabel('Действие проверяющего C02').selectOption('confirm');
  const confirm = page.getByRole('form', { name: 'Решение C02' }).getByRole('button', { name: 'Сохранить решение' });
  await expect(confirm).toBeDisabled();
  await page.getByLabel('Причина решения C02').fill('Исходный ответ подтверждён репликой');
  await confirm.click();
  await expect(page.getByLabel('Обращение C02', { exact: true })).toContainText('confirm — исходный ответ подтверждён');
  // Newer source versions exist, but result/reviewer must never query them.
  await page.route('**/api/v1/checklists', route => route.fulfill({ json: { items: [{ ...checklistList.items[0], latest_version_id: 'latest-checklist', version: 2 }] } }));
  await page.route('**/api/v1/conversations?**', route => route.fulfill({ json: { ...conversationList, items: [{ ...conversationList.items[0], latest_version_id: 'reimported-conversation' }] } }));
  await page.reload();
  await expect(page.getByTestId('effective-score')).toHaveText('70,00');
  await expect(page.getByTestId('total-score')).toHaveText('85,38');
  await expect(c01).toContainText('Синтетическая поправка C01');
  await page.getByRole('button', { name: 'Показать доказательство C01' }).click();
  await expect(page.locator('.message--highlighted').first()).toBeFocused();
  await expect(page.locator('.message--highlighted').first()).toContainText(historicalConversation.messages.find(item => item.message_id === originalEvaluation().output!.answers[0]!.evidence[0]!.start_message_id)!.text);
  expect(api.posts[0]).toMatchObject({ action: 'override', override_answer: { type: 'boolean', value: false } });
  expect(api.posts[1]).toMatchObject({ action: 'confirm', override_answer: null });
  expect(api.posts[0]!.idempotency_key).not.toBe(api.posts[1]!.idempotency_key);
  expect(api.paths.some(path => /latest-|reimported-|\/demo/.test(path))).toBe(false);
});

test('потерянный ответ и обновление страницы сохраняют ключ решения', async ({ page }) => {
  const api = await mockApi(page, { existing: true, decisionFailure: 'network' });
  await page.goto('/#appeals/synthetic-evaluation-1'); await configureOverride(page);
  await page.getByRole('button', { name: 'Сохранить решение' }).click();
  await expect(page.getByRole('alert')).toContainText('Не удалось связаться');
  await page.reload();
  await expect(page.getByLabel('Причина решения C01')).toHaveValue('Синтетическая поправка C01');
  await expect(page.getByLabel('Причина решения C01')).toBeDisabled();
  await page.getByRole('button', { name: 'Повторить решение' }).click();
  await expect(page.getByTestId('effective-score')).toHaveText('70,00');
  expect(api.posts).toHaveLength(2); expect(api.posts[1]).toEqual(api.posts[0]);
});

test('409 загружает решение другого проверяющего', async ({ page }) => {
  await mockApi(page, { existing: true, decisionFailure: 409, resolvedConflict: true });
  await page.goto('/#appeals/synthetic-evaluation-1'); await configureOverride(page);
  await page.getByRole('button', { name: 'Сохранить решение' }).click();
  await expect(page.getByText(/Конфликт HTTP 409/)).toBeVisible();
  await expect(page.getByText('Причина: Решение другого проверяющего')).toBeVisible();
  await expect(page.getByRole('button', { name: 'Сохранить решение' })).toHaveCount(0);
});

for (const status of [422, 500]) test(`HTTP ${status} не стирает причину и не показывает успех`, async ({ page }) => {
  const api = await mockApi(page, { existing: true, decisionFailure: status });
  await page.goto('/#appeals/synthetic-evaluation-1'); await configureOverride(page);
  await page.getByRole('button', { name: 'Сохранить решение' }).click();
  await expect(page.getByRole('alert')).toContainText(`HTTP ${status}`);
  await expect(page.getByLabel('Причина решения C01')).toHaveValue('Синтетическая поправка C01');
  await expect(page.getByText('Решена · resolved')).toHaveCount(0);
  await expect(page.getByTestId('effective-score')).toHaveText('85,38');
  await page.getByRole('button', { name: 'Повторить решение' }).click();
  await expect(page.getByTestId('effective-score')).toHaveText('70,00');
  expect(api.posts[1]).toEqual(api.posts[0]);
});

test('404 списка, retry, empty; мобильный экран и evidence с клавиатуры', async ({ page }) => {
  const api = await mockApi(page, { listFailure: 404 });
  await page.setViewportSize({ width: 390, height: 844 });
  await page.goto('/#appeals/synthetic-evaluation-1');
  await expect(page.getByRole('alert')).toContainText('HTTP 404');
  api.allowList();
  await page.getByRole('button', { name: 'Повторить загрузку' }).click();
  await expect(page.getByText('Апелляций пока нет.')).toBeVisible();
  const evidence = page.getByRole('button', { name: 'Показать доказательство C01' });
  await evidence.focus(); await page.keyboard.press('Enter');
  await expect(page.locator('.message--highlighted').first()).toBeFocused();
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
  expect(api.posts).toHaveLength(0);
});
