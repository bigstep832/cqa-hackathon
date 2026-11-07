import { expect, test, type Page, type TestInfo } from '@playwright/test';
import { originalResult, openAppeal } from '../appealFixtures';
import { listOf } from '../checklistFixtures';

// Synthetic transport only: this visual suite makes no writes or AI requests.
async function syntheticScreens(page: Page) {
  const data = { ...structuredClone(originalResult()), mode: 'mock' as const };
  data.evaluation.output!.metadata.mode = 'mock';
  const text = 'Проверка качества. Клиентке көмектесу және түсінікті жауап. Clear explanation and historical evidence. ';
  data.conversation.conversation_id = 'synthetic-' + 'long-identifier-'.repeat(12);
  data.conversation.messages[0]!.text = text.repeat(5);
  data.checklist.name = 'Качество обслуживания · Қызмет сапасы · Service quality';
  data.checklist.criteria[0]!.title = text;
  data.evaluation.output!.answers[0]!.explanation = text.repeat(3);
  const appeal = { ...openAppeal(), comment: text.repeat(3) };
  await page.route('**/api/v1/**', async route => {
    expect(route.request().method()).toBe('GET');
    const path = new URL(route.request().url()).pathname;
    const value = path === '/api/v1/demo' ? data
      : path === '/api/v1/conversations' ? { items: [{ conversation_id: data.conversation.conversation_id, latest_version_id: data.conversation.id,
        language: data.conversation.language, channel: data.conversation.channel, message_count: data.conversation.messages.length, has_audio: false }], total: 1, offset: 0, limit: 25 }
      : path === '/api/v1/checklists' ? listOf(data.checklist)
      : path === '/api/v1/conversation-versions/' + data.conversation.id ? data.conversation
      : path === '/api/v1/checklist-versions/' + data.checklist.id ? data.checklist
      : path === '/api/v1/evaluations/' + data.evaluation.id ? data.evaluation
      : path === '/api/v1/evaluations/' + data.evaluation.id + '/appeals' ? { items: [appeal] } : null;
    await route.fulfill(value ? { json: value } : { status: 404, json: {} });
  });
  return data;
}
async function fits(page: Page) {
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
  const sizes = await page.locator('.breadcrumb, .page-footer, .version-label, .message p, .explanation').evaluateAll(elements =>
    elements.filter(element => element.getClientRects().length).map(element => parseFloat(getComputedStyle(element).fontSize)));
  expect(sizes.every(size => size >= 13)).toBe(true);
}
async function capture(page: Page, info: TestInfo, name: string) {
  await fits(page);
  await page.screenshot({ path: info.outputPath(name + '.png') });
}
for (const width of [390, 768, 1024, 1440]) {
  test('B-03: all screens, long RU/KZ/EN and keyboard at ' + width + 'px', async ({ page }, info) => {
    await page.setViewportSize({ width, height: 1000 });
    await page.emulateMedia({ reducedMotion: 'reduce' });
    const data = await syntheticScreens(page);
    await page.goto('/');
    await page.keyboard.press('Tab');
    await expect(page.getByRole('link', { name: 'Перейти к содержимому' })).toBeFocused();
    expect(await page.getByRole('link', { name: 'Перейти к содержимому' }).evaluate(element => getComputedStyle(element).outlineStyle)).toBe('solid');
    await page.keyboard.press('Enter');
    await page.locator('.conversation-item').click();
    await expect(page.getByRole('button', { name: 'Оценить диалог', exact: true })).toBeVisible();
    await capture(page, info, 'dialogues-' + width);
    await page.locator('.import-panel summary').click();
    await capture(page, info, 'import-' + width);
    await page.getByRole('button', { name: 'Демо · mock', exact: true }).click();
    await expect(page.getByTestId('total-score')).toHaveText('85,38');
    await capture(page, info, 'result-' + width);
    const transcript = await page.locator('.review-grid .transcript-panel').boundingBox();
    const criteria = await page.locator('.criteria-panel').boundingBox();
    expect(transcript).not.toBeNull(); expect(criteria).not.toBeNull();
    if (width === 1440) expect(transcript!.x + transcript!.width).toBeLessThan(criteria!.x);
    else expect(transcript!.y + transcript!.height).toBeLessThanOrEqual(criteria!.y);
    const evidence = page.getByRole('button', { name: 'Показать доказательство C01' });
    await evidence.focus(); await page.keyboard.press('Enter');
    await expect(page.locator('.message--highlighted').first()).toBeFocused();
    await capture(page, info, 'evidence-' + width);
    await page.getByRole('button', { name: 'Чек-листы', exact: true }).click();
    await page.locator('.conversation-item').click();
    await expect(page.getByRole('button', { name: 'Редактировать — новая версия' })).toBeVisible();
    await capture(page, info, 'checklist-' + width);
    await page.getByRole('button', { name: 'Редактировать — новая версия' }).click();
    for (const name of ['Основные сведения', 'Оценивание и секция', 'Применимость · N/A', 'Варианты ответа']) {
      await expect(page.getByRole('group', { name, exact: true }).first()).toBeVisible();
    }
    await capture(page, info, 'editor-' + width);
    await page.getByLabel('Название критерия', { exact: true }).first().scrollIntoViewIfNeeded();
    await capture(page, info, 'criterion-fields-' + width);
    await page.getByRole('button', { name: 'Апелляции', exact: true }).click();
    await page.getByLabel('ID оценки', { exact: true }).fill(data.evaluation.id);
    await page.getByRole('button', { name: 'Открыть апелляции', exact: true }).click();
    await expect(page.getByLabel('Обращение C01', { exact: true })).toContainText('Открыта · open');
    await page.getByLabel('Действие проверяющего C01').selectOption('override');
    await page.getByLabel('Новый ответ C01').selectOption('1');
    await page.getByLabel('Причина решения C01').fill('Обязательная причина — ' + 'синтетическая проверка '.repeat(20));
    await expect(page.getByRole('form', { name: 'Решение C01' }).getByRole('button', { name: 'Сохранить решение' })).toBeEnabled();
    await capture(page, info, 'appeal-' + width);
    await expect(page.getByTestId('total-score')).toHaveText('85,38');
  });
}
