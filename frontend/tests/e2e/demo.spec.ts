import { expect, test } from '@playwright/test';

test('реальный backend возвращает пример, evidence открывает сообщение', async ({ page }) => {
  await page.goto('/');
  await expect(page.getByText('Тестовые данные — не AI-оценка')).toBeVisible();
  await expect(page.getByTestId('total-score')).toHaveText('85,38');
  await expect(page.getByRole('heading', { name: 'Транскрипт', exact: true })).toBeVisible();
  await page.getByRole('button', { name: 'Показать доказательство C01' }).click();
  await expect(page.locator('.message--highlighted').first()).toBeFocused();
  await expect(page.getByRole('status')).toContainText('C01');
});

test('ошибка сервера не подменяется mock-ответом', async ({ page }) => {
  await page.route('**/api/v1/demo', route => route.fulfill({ status: 503, body: '{}' }));
  await page.goto('/');
  await expect(page.getByRole('alert')).toContainText('HTTP 503');
  await expect(page.getByTestId('total-score')).toHaveCount(0);
});

test('экран помещается на узком viewport и evidence доступен с клавиатуры', async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await page.emulateMedia({ reducedMotion: 'reduce' });
  await page.goto('/');
  await expect(page.getByTestId('total-score')).toBeVisible();
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);
  const evidence = page.getByRole('button', { name: 'Показать доказательство C01' });
  await evidence.focus();
  await page.keyboard.press('Enter');
  await expect(page.locator('.message--highlighted').first()).toBeFocused();
});
