import { expect, test } from '@playwright/test';
import fixture from '../../../fixtures/synthetic/demo.json' with { type: 'json' };

// Browser smoke uses only synthetic responses; it never writes to backend storage.
test('список, фильтры, импорт и выбранный транскрипт на узком экране', async ({ page }) => {
  let imported = false;
  const conversation = { ...fixture.conversation, conversation_id: 'synthetic-browser-001' };
  await page.route('**/api/v1/**', async route => {
    const url = new URL(route.request().url());
    if (route.request().method() === 'POST' && url.pathname === '/api/v1/conversations/import') {
      expect(route.request().postDataJSON().dataset_group_id).toBe('MANUAL_SAMPLE');
      imported = true;
      return route.fulfill({ json: { dataset_group_id: 'MANUAL_SAMPLE', imported_count: 1, unchanged_count: 0, conversation_version_ids: [conversation.id] } });
    }
    if (url.pathname === '/api/v1/conversations') return route.fulfill({ json: {
      items: imported ? [{ conversation_id: conversation.conversation_id, latest_version_id: conversation.id, language: 'RU', channel: 'agent chat', message_count: conversation.messages.length, has_audio: false }] : [],
      total: imported ? 1 : 0, offset: 0, limit: 25,
    } });
    if (url.pathname === `/api/v1/conversation-versions/${conversation.id}`) return route.fulfill({ json: conversation });
    return route.fulfill({ status: 404, json: { error: { code: 'TEST_ROUTE_NOT_FOUND', message: 'Неожиданный тестовый запрос.', details: [] } } });
  });
  await page.setViewportSize({ width: 390, height: 844 });
  await page.goto('/');
  await expect(page.getByText('Диалоги не найдены')).toBeVisible();
  await page.getByLabel('Язык', { exact: true }).selectOption('KZ');
  await page.getByLabel('Канал', { exact: true }).selectOption('voice call');
  await page.getByText('Ручной импорт синтетического JSON', { exact: true }).click();
  await page.getByRole('button', { name: 'Вставить синтетический пример' }).click();
  await page.getByRole('checkbox').check();
  await page.getByRole('button', { name: 'Импортировать', exact: true }).click();
  await expect(page.getByText('Версия диалога: ' + conversation.id)).toBeVisible();
  await expect(page.getByText(conversation.messages[0]!.text, { exact: true })).toBeVisible();
  await expect(page.getByLabel('Язык', { exact: true })).toHaveValue('');
  await expect(page.getByRole('button', { name: /synthetic-browser-001/ })).toHaveAttribute('aria-pressed', 'true');
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);
});

test('ошибка списка остаётся ошибкой, retry возвращает пустой список', async ({ page }) => {
  let unavailable = true;
  await page.route('**/api/v1/conversations?*', route => unavailable
    ? route.fulfill({ status: 503, json: { error: { code: 'UNAVAILABLE', message: 'Сервер временно недоступен.', details: [] } } })
    : route.fulfill({ json: { items: [], total: 0, offset: 0, limit: 25 } }));
  await page.goto('/');
  await expect(page.getByRole('alert')).toContainText('HTTP 503');
  await expect(page.getByTestId('total-score')).toHaveCount(0);
  unavailable = false;
  await page.getByRole('button', { name: 'Повторить загрузку' }).click();
  await expect(page.getByText('Диалоги не найдены')).toBeVisible();
});
