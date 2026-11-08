import { expect, test, type Page } from '@playwright/test';
import type { ChecklistCreateRequest, ChecklistVersion } from '../../src/api/client';
import { conversationList, historicalChecklist, historicalConversation } from '../evaluationFixtures';
import { listOf, savedChecklist } from '../checklistFixtures';

// The complete API transport is synthetic. No server writes or provider calls.
async function checklistApi(page: Page, failures: (number | 'network')[] = []) {
  let latest = historicalChecklist;
  const versions = new Map<string, ChecklistVersion>([[latest.id, latest]]);
  const posts: { path: string; body: ChecklistCreateRequest }[] = [];
  const paths: string[] = [];
  await page.route('**/api/v1/**', async route => {
    const path = new URL(route.request().url()).pathname;
    paths.push(path);
    if (route.request().method() === 'POST') {
      const body = route.request().postDataJSON() as ChecklistCreateRequest;
      posts.push({ path, body });
      const failure = failures.shift();
      if (failure === 'network') { await route.abort('failed'); return; }
      if (failure) {
        await route.fulfill({ status: failure, json: { error: { code: 'SYNTHETIC_ERROR', message: 'Синтетическая ошибка сохранения',
          details: failure === 422 ? [{ field: 'body.name', message: 'Проверьте название' }] : [],
        } } }); return;
      }
      expect(path === '/api/v1/checklists' || path === `/api/v1/checklists/${latest.checklist_id}/versions`).toBe(true);
      latest = savedChecklist(body, path === '/api/v1/checklists' ? 1 : latest.version + 1);
      versions.set(latest.id, latest);
      await route.fulfill({ status: 201, json: latest }); return;
    }
    const data = path === '/api/v1/checklists' ? listOf(latest)
      : path === '/api/v1/conversations' ? conversationList
      : path === `/api/v1/conversation-versions/${historicalConversation.id}` ? historicalConversation
      : versions.get(decodeURIComponent(path.replace('/api/v1/checklist-versions/', '')));
    await route.fulfill(data ? { json: data } : { status: 404, json: { error: { code: 'UNEXPECTED_TEST_URL', message: 'Неожиданный URL теста', details: [] } } });
  });
  return { posts, paths };
}

async function openChecklists(page: Page) {
  await page.goto('/');
  await page.getByRole('button', { name: 'Чек-листы', exact: true }).click();
}
async function editExisting(page: Page) {
  await openChecklists(page);
  await page.getByRole('button', { name: new RegExp(`${historicalChecklist.name} Версия`) }).click();
  await page.getByRole('button', { name: 'Редактировать — новая версия' }).click();
}

test('C09, смена типа, immutable snapshot и новая версия в выборе оценки', async ({ page }, testInfo) => {
  const api = await checklistApi(page);
  await editExisting(page);
  await page.getByRole('button', { name: 'Добавить критерий' }).click();
  const ninth = page.locator('.criterion-editor').nth(8);
  await expect(ninth.getByLabel('ID критерия')).toHaveValue('C09');
  await ninth.getByLabel('Название критерия').fill('Синтетический девятый критерий');
  await ninth.getByLabel('Описание критерия').fill('Проверить завершение разговора оператором.');
  await ninth.getByLabel('Тип ответа').selectOption('scale');
  await ninth.getByLabel('Значение', { exact: true }).nth(0).fill('0');
  await ninth.getByLabel('Значение', { exact: true }).nth(1).fill('10');
  await ninth.getByLabel('Описание варианта').nth(0).fill('Нет завершения.');
  await ninth.getByLabel('Описание варианта').nth(1).fill('Оператор завершил разговор.');
  await ninth.getByLabel('Вес', { exact: true }).fill('2.5');
  await page.getByRole('button', { name: 'Сохранить новую версию' }).click();
  await expect(page.getByText(/Сохранена версия v2/)).toBeVisible();
  await expect(page.getByRole('heading', { name: 'C09 · Синтетический девятый критерий' })).toBeVisible();
  expect(api.posts).toHaveLength(1);
  expect(api.posts[0]!.body.criteria).toHaveLength(9);
  expect(api.posts[0]!.body.criteria[8]).toMatchObject({ weight: '2.5', answer_type: 'scale', options: [{ value: 0 }, { value: 10 }] });
  expect(api.posts[0]!.body).not.toHaveProperty('id');
  await page.screenshot({ path: testInfo.outputPath('checklists-desktop.png'), fullPage: true });

  await page.getByLabel('Открыть версию по ID').fill(historicalChecklist.id);
  await page.getByRole('button', { name: 'Открыть версию', exact: true }).click();
  const detail = page.getByRole('region', { name: 'Версия чек-листа', exact: true });
  await expect(detail.getByText(/Критериев: 8/)).toBeVisible();
  await expect(detail.getByRole('heading', { name: /C09/ })).toHaveCount(0);
  expect(api.paths).toContain(`/api/v1/checklist-versions/${historicalChecklist.id}`);

  await page.getByRole('button', { name: 'Диалоги', exact: true }).click();
  await page.getByRole('button', { name: new RegExp(historicalConversation.conversation_id) }).click();
  await page.getByRole('button', { name: 'Оценить диалог', exact: true }).click();
  await page.getByLabel('Версия чек-листа', { exact: true }).selectOption('synthetic-checklist-v2');
  await expect(page.getByLabel('Версия чек-листа', { exact: true })).toHaveValue('synthetic-checklist-v2');
  expect(api.paths).not.toContain('/api/v1/demo');
});

test('создание чек-листа с choice/flag, новой секцией и N/A', async ({ page }) => {
  const api = await checklistApi(page);
  await openChecklists(page);
  await page.getByRole('button', { name: 'Создать чек-лист', exact: true }).click();
  await page.getByLabel('Название чек-листа').fill('Синтетический чек-лист B-02');
  await page.getByLabel('Название секции', { exact: true }).fill('Первая секция');
  await page.getByRole('button', { name: 'Добавить секцию' }).click();
  await page.getByLabel('Название секции', { exact: true }).nth(1).fill('Нарушения');
  await page.getByLabel('Название критерия', { exact: true }).fill('Тип нарушения');
  await page.getByLabel('Описание критерия', { exact: true }).fill('Проверять только действия оператора.');
  await page.getByLabel('Секция критерия').selectOption('S02');
  await page.getByLabel('Тип ответа').selectOption('choice');
  await page.getByLabel('Назначение критерия').selectOption('flag');
  await page.getByLabel('Значение', { exact: true }).nth(0).fill('clear');
  await page.getByLabel('Значение', { exact: true }).nth(1).fill('violation');
  await page.getByLabel('Описание варианта').nth(0).fill('Нарушений нет.');
  await page.getByLabel('Описание варианта').nth(1).fill('Нарушение подтверждено.');
  await page.getByLabel('Этот вариант означает нарушение').nth(1).check();
  await page.getByLabel('Разрешить N/A').check();
  await page.getByLabel('Условие N/A').fill('Нет реплик оператора.');
  await page.getByRole('button', { name: 'Создать чек-лист', exact: true }).click();
  await expect(page.getByText(/Сохранена версия v1/)).toBeVisible();
  expect(api.posts[0]!.path).toBe('/api/v1/checklists');
  expect(api.posts[0]!.body.criteria[0]).toMatchObject({ section_id: 'S02', kind: 'flag', answer_type: 'choice', weight: null,
    allow_na: true, na_condition: 'Нет реплик оператора.', options: [{ value: 'clear', quality: null, flagged: false }, { value: 'violation', quality: null, flagged: true }] });
  await expect(page.getByText('N/A разрешён: Нет реплик оператора.')).toBeVisible();
});

for (const status of [400, 409, 422, 500] as const) {
  test(`HTTP ${status}: форма сохранена, повтор отправляет прежние значения`, async ({ page }) => {
    const api = await checklistApi(page, [status]);
    await editExisting(page);
    await page.getByLabel('Название чек-листа').fill('Черновик после ошибки');
    await page.getByRole('button', { name: 'Сохранить новую версию' }).click();
    await expect(page.getByText(`Синтетическая ошибка сохранения (HTTP ${status})`)).toBeVisible();
    await expect(page.getByLabel('Название чек-листа')).toHaveValue('Черновик после ошибки');
    if (status === 422) await expect(page.getByLabel('Название чек-листа')).toHaveAttribute('aria-invalid', 'true');
    expect(api.posts).toHaveLength(1);
    await page.getByRole('button', { name: 'Сохранить новую версию' }).click();
    await expect(page.getByText(/Сохранена версия v2/)).toBeVisible();
    expect(api.posts).toHaveLength(2);
    expect(api.posts[1]!.body).toEqual(api.posts[0]!.body);
    expect(api.paths).not.toContain('/api/v1/demo');
  });
}

test('потеря сети сохраняет черновик и не повторяет POST автоматически', async ({ page }) => {
  const api = await checklistApi(page, ['network']);
  await editExisting(page);
  await page.getByLabel('Название чек-листа').fill('Черновик без сети');
  await page.getByRole('button', { name: 'Сохранить новую версию' }).click();
  await expect(page.getByText(/Ответ не подтверждает, сохранил ли сервер данные/)).toBeVisible();
  await expect(page.getByLabel('Название чек-листа')).toHaveValue('Черновик без сети');
  expect(api.posts).toHaveLength(1);
  expect(api.paths).not.toContain('/api/v1/demo');
});

test('390 px: проверка вариантов, фокус ошибки и отсутствие горизонтального скролла', async ({ page }, testInfo) => {
  const api = await checklistApi(page);
  await page.setViewportSize({ width: 390, height: 844 });
  await editExisting(page);
  const first = page.locator('.criterion-editor').first();
  await first.getByLabel('Тип ответа').selectOption('scale');
  await first.getByLabel('Значение', { exact: true }).nth(0).fill('7');
  await first.getByLabel('Значение', { exact: true }).nth(1).fill('7.0');
  await first.getByLabel('Описание варианта').nth(0).fill('Первое описание');
  await first.getByLabel('Описание варианта').nth(1).fill('Второе описание');
  await page.getByRole('button', { name: 'Сохранить новую версию' }).click();
  await expect(first.getByLabel('Значение', { exact: true }).nth(1)).toBeFocused();
  await expect(first.getByText('Значения вариантов не должны повторяться.')).toBeVisible();
  expect(api.posts).toHaveLength(0);
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);
  await page.screenshot({ path: testInfo.outputPath('checklists-mobile.png'), fullPage: true });
});
