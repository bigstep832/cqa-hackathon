import { expect, test } from '@playwright/test';
import { extendedChartFixture } from '../criteriaChartFixtures';

test('390 px: дробные полосы, C09, N/A, flag и клавиатурное переключение', async ({ page }, info) => {
  const data = extendedChartFixture();
  await page.route('**/api/v1/**', route => route.fulfill({ json: new URL(route.request().url()).pathname === '/api/v1/demo'
    ? data : { items: [], offset: 0, limit: 25, total: 0 } }));
  await page.setViewportSize({ width: 390, height: 844 });
  await page.goto('/'); await page.getByRole('button', { name: 'Демо · mock', exact: true }).click();
  const chart = page.getByRole('region', { name: 'Баллы по критериям' });
  await expect(chart.getByRole('meter', { name: /^C09/ })).toBeVisible();
  await expect(chart.getByText('0,125 / 0,25')).toBeVisible();
  const meter = chart.getByRole('meter', { name: /^C09/ });
  const ratio = await meter.evaluate(element => element.firstElementChild!.getBoundingClientRect().width / element.getBoundingClientRect().width);
  expect(ratio).toBeCloseTo(0.5, 2);
  await expect(chart.getByText('N/A', { exact: true })).toHaveCount(2);
  await expect(chart.getByText('Обнаружен', { exact: true })).toBeVisible();
  await expect(chart.getByRole('meter', { name: /^C08/ })).toHaveCount(0);
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
  await chart.scrollIntoViewIfNeeded(); await page.screenshot({ path: info.outputPath('chart-mobile.png') });
  const toggle = chart.getByRole('button', { name: 'Эффективные баллы' });
  await toggle.focus(); await page.keyboard.press('Enter');
  await expect(toggle).toHaveAttribute('aria-pressed', 'true');
  await expect(chart.getByRole('meter', { name: /^C01/ })).toHaveAttribute('aria-valuenow', '0');
  await expect(page.getByTestId('total-score')).toHaveText('85,38');
  await expect(page.getByTestId('effective-score')).toHaveText('70,00');
  await page.getByRole('button', { name: 'Показать доказательство C01' }).click();
  await expect(page.locator('.message--highlighted').first()).toBeFocused();
});
