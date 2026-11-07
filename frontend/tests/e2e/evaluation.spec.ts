import { expect, test, type Page, type Route } from '@playwright/test';
import { checklistList, conversationList, demo, evaluation, historicalChecklist, historicalConversation, liveRequest } from '../evaluationFixtures';
import type { Evaluation, EvaluationRequest } from '../../src/api/client';

// Every API call is intercepted. These scenarios never call a model or write SQLite.
async function mockApi(page: Page, override?: (route: Route, path: string) => Promise<boolean>) {
  await page.route('**/api/v1/**', async route => {
    const path = new URL(route.request().url()).pathname;
    if (await override?.(route, path)) return;
    const data: unknown = path === '/api/v1/conversations' ? conversationList
      : path === '/api/v1/checklists' ? checklistList
      : path === '/api/v1/demo' ? demo
      : path === '/api/v1/evaluations' ? evaluation('succeeded', route.request().postDataJSON() as EvaluationRequest)
      : path.startsWith('/api/v1/evaluations/') ? evaluation()
      : path === `/api/v1/conversation-versions/${historicalConversation.id}` ? historicalConversation
      : path === `/api/v1/checklist-versions/${historicalChecklist.id}` ? historicalChecklist
      : path === `/api/v1/conversation-versions/${demo.conversation.id}` ? demo.conversation
      : path === `/api/v1/checklist-versions/${demo.checklist.id}` ? demo.checklist : null;
    await route.fulfill(data ? { status: path === '/api/v1/evaluations' ? 202 : 200, json: data }
      : { status: 404, json: { error: { code: 'UNEXPECTED_TEST_URL', message: 'Неожиданный запрос теста.', details: [] } } });
  });
}

async function openForm(page: Page) {
  await page.goto('/');
  await page.getByRole('button', { name: new RegExp(historicalConversation.conversation_id) }).click();
  await page.getByRole('button', { name: 'Оценить диалог', exact: true }).click();
}
async function configureLive(page: Page) {
  await page.getByLabel('Версия чек-листа', { exact: true }).selectOption(historicalChecklist.id);
  await page.getByLabel('Режим оценки', { exact: true }).selectOption('live');
}

test('queued → running → succeeded, исходный snapshot и отдельные баллы', async ({ page }) => {
  let status: Evaluation['status'] = 'queued';
  let created = false;
  const paths: string[] = [];
  await mockApi(page, async (route, path) => {
    paths.push(path);
    if (path === '/api/v1/evaluations') {
      created = true;
      expect(route.request().postDataJSON()).toEqual(liveRequest);
      expect(route.request().headers()['idempotency-key']).toMatch(/^[\da-f-]{36}$/);
      await route.fulfill({ status: 202, json: evaluation('queued') }); return true;
    }
    if (path.startsWith('/api/v1/evaluations/')) {
      await route.fulfill({ json: evaluation(status) }); return true;
    }
    // The latest versions change while the job is running. The result must not use them.
    if (created && path === '/api/v1/checklists') {
      await route.fulfill({ json: { items: [{ ...checklistList.items[0], latest_version_id: 'latest-checklist-v2', version: 2 }] } }); return true;
    }
    if (created && path === '/api/v1/conversations') {
      await route.fulfill({ json: { ...conversationList, items: [{ ...conversationList.items[0], latest_version_id: 'latest-conversation-v2' }] } }); return true;
    }
    return false;
  });
  await openForm(page);
  await configureLive(page);
  await page.getByRole('button', { name: 'Запустить оценку' }).click();
  await expect(page.getByText('queued', { exact: true })).toBeVisible();
  status = 'running';
  await expect(page.getByText('running', { exact: true })).toBeVisible();
  status = 'succeeded';
  await expect(page.getByTestId('total-score')).toHaveText('85,38');
  await expect(page.getByTestId('effective-score')).toHaveText('70,00');
  await expect(page.getByText(/Версия чек-листа: history-checklist-v1/)).toBeVisible();
  await page.getByRole('button', { name: 'Показать доказательство C01' }).click();
  await expect(page.locator('.message--highlighted').first()).toBeFocused();
  expect(paths).toContain('/api/v1/conversation-versions/history-conversation-v1');
  expect(paths).toContain('/api/v1/checklist-versions/history-checklist-v1');
  expect(paths.some(path => path.includes('latest-') || path === '/api/v1/demo')).toBe(false);
});

test('failed сохраняет причину, а ошибка GET повторяет то же задание', async ({ page }) => {
  let unavailable = true;
  let posts = 0;
  await mockApi(page, async (route, path) => {
    if (path === '/api/v1/evaluations') {
      posts++; await route.fulfill({ status: 202, json: evaluation('running') }); return true;
    }
    if (path.startsWith('/api/v1/evaluations/')) {
      await route.fulfill(unavailable ? { status: 502, json: {} } : { json: evaluation('failed') }); return true;
    }
    return false;
  });
  await openForm(page); await configureLive(page);
  await page.getByRole('button', { name: 'Запустить оценку' }).click();
  await expect(page.getByText('running', { exact: true })).toBeVisible();
  await expect(page.getByRole('alert')).toContainText('HTTP 502');
  unavailable = false;
  await page.getByRole('button', { name: 'Повторить загрузку' }).click();
  await expect(page.getByText('failed', { exact: true })).toBeVisible();
  await expect(page.getByRole('alert')).toContainText('PROVIDER_TIMEOUT');
  await expect(page.getByTestId('total-score')).toHaveCount(0);
  expect(posts).toBe(1);
});

test('потерянный POST после перезагрузки повторяется с прежним Idempotency-Key', async ({ page }) => {
  const submissions: { key: string | undefined; body: unknown }[] = [];
  await mockApi(page, async (route, path) => {
    if (path !== '/api/v1/evaluations') return false;
    submissions.push({ key: route.request().headers()['idempotency-key'], body: route.request().postDataJSON() });
    if (submissions.length === 1) { await route.abort('failed'); return true; }
    await route.fulfill({ status: 202, json: evaluation() }); return true;
  });
  await openForm(page); await configureLive(page);
  await page.getByRole('button', { name: 'Запустить оценку' }).click();
  await expect(page.getByRole('alert')).toContainText('Не удалось связаться');
  await expect(page.getByLabel('Режим оценки', { exact: true })).toBeDisabled();
  await openForm(page);
  await page.getByRole('button', { name: 'Повторить отправку' }).click();
  await expect(page.getByTestId('total-score')).toBeVisible();
  expect(submissions).toHaveLength(2);
  expect(submissions[1]).toEqual(submissions[0]);
});

test('mock запрещён для другой пары; отказ live не подменяется demo', async ({ page }) => {
  let posts = 0;
  await mockApi(page, async (route, path) => {
    if (path !== '/api/v1/evaluations') return false;
    posts++;
    expect(route.request().postDataJSON().mode).toBe('live');
    await route.fulfill({ status: 409, json: { error: { code: 'LIVE_NOT_AUTHORIZED', message: 'Живой запуск не согласован.', details: [] } } }); return true;
  });
  await openForm(page);
  await page.getByLabel('Режим оценки', { exact: true }).selectOption('mock');
  await page.getByLabel('Версия чек-листа', { exact: true }).selectOption(demo.checklist.id);
  await expect(page.getByText(/Ручной импорт не делает/)).toBeVisible();
  await expect(page.getByRole('button', { name: 'Запустить оценку' })).toBeDisabled();
  expect(posts).toBe(0);
  await configureLive(page);
  await page.getByRole('button', { name: 'Запустить оценку' }).click();
  await expect(page.getByRole('alert')).toContainText('Живой запуск не согласован');
  await expect(page.getByTestId('total-score')).toHaveCount(0);
  await expect(page.getByLabel('Режим оценки', { exact: true })).toHaveValue('live');
});

test('поддерживаемый mock на 390 px: результат и evidence с клавиатуры', async ({ page }) => {
  await mockApi(page);
  await page.setViewportSize({ width: 390, height: 844 });
  await page.emulateMedia({ reducedMotion: 'reduce' });
  await page.goto('/');
  await page.getByRole('button', { name: 'Демо · mock' }).click();
  await page.getByRole('button', { name: 'Оценить встроенный пример' }).click();
  await page.getByLabel('Режим оценки', { exact: true }).selectOption('mock');
  await page.getByLabel('Версия чек-листа', { exact: true }).selectOption(demo.checklist.id);
  await page.getByRole('button', { name: 'Запустить оценку' }).click();
  await expect(page.getByTestId('total-score')).toBeVisible();
  await expect(page.getByText('Тестовые данные — не AI-оценка', { exact: true })).toBeVisible();
  const evidence = page.getByRole('button', { name: 'Показать доказательство C01' });
  await evidence.focus(); await page.keyboard.press('Enter');
  await expect(page.locator('.message--highlighted').first()).toBeFocused();
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);
});

test('чужой snapshot отклоняется, повтор не создаёт новую оценку', async ({ page }) => {
  let wrongSnapshot = true;
  let posts = 0;
  await mockApi(page, async (route, path) => {
    if (path === '/api/v1/evaluations') posts++;
    if (path === `/api/v1/checklist-versions/${historicalChecklist.id}` && wrongSnapshot) {
      await route.fulfill({ json: { ...historicalChecklist, id: 'wrong-version' } }); return true;
    }
    return false;
  });
  await openForm(page); await configureLive(page);
  await page.getByRole('button', { name: 'Запустить оценку' }).click();
  await expect(page.getByRole('alert')).toContainText('не совпадают');
  await expect(page.getByTestId('total-score')).toHaveCount(0);
  wrongSnapshot = false;
  await page.getByRole('button', { name: 'Повторить загрузку' }).click();
  await expect(page.getByTestId('total-score')).toBeVisible();
  expect(posts).toBe(1);
});
