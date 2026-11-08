import { randomUUID } from 'node:crypto';
import { expect, test } from '@playwright/test';

// Exercises the product forms against the configured FastAPI + SQLite using synthetic data.
test('назначение оператора из карточки и профиль с известными и неизвестными исходами', async ({ page }) => {
  const tag = randomUUID().slice(0, 8);
  const operator = `agent-${tag}`;
  const records = [
    { conversation_id: `op-call-${tag}`, channel: 'voice call', language: 'RU' },
    { conversation_id: `op-chat-${tag}`, channel: 'agent chat', language: 'KZ' },
    { conversation_id: `op-unknown-${tag}`, channel: 'voice call', language: 'EN', operator: { operator_id: operator } },
    { conversation_id: `op-other-${tag}`, channel: 'agent chat', language: 'EN', operator: { operator_id: `aa-first-${tag}` } },
  ].map(record => ({ ...record, transcript: [
    { speaker: 'client', text: 'Test question', timestamp: '2026-11-07T10:00:00Z' },
    { speaker: 'operator', text: 'Test answer', timestamp: '2026-11-07T10:00:06Z' },
  ] }));
  await page.goto('/');
  await page.getByText('Ручной импорт синтетического JSON', { exact: true }).click();
  await page.getByLabel('JSON для импорта').fill(JSON.stringify({ dataset_group_id: 'TEST-OPERATORS', records }));
  await page.getByRole('checkbox', { name: 'Это синтетические данные, созданные командой' }).check();
  await page.getByRole('button', { name: 'Импортировать', exact: true }).click();
  await expect(page.getByRole('heading', { name: records[0]!.conversation_id })).toBeVisible();

  await page.getByRole('button', { name: 'Данные оператора' }).click();
  await page.getByLabel('Псевдоним оператора').fill(operator);
  await page.getByLabel('Исход звонка').selectOption('successful');
  await page.getByLabel('Длительность звонка, секунды').fill('185');
  await page.getByRole('button', { name: 'Сохранить данные оператора' }).click();
  await expect(page.getByText('Данные оператора сохранены.')).toBeVisible();

  // A second import selects the chat by its returned version even when the list has many pages.
  await page.getByLabel('JSON для импорта').fill(JSON.stringify({ dataset_group_id: 'TEST-OPERATORS', records: [records[1]] }));
  await page.getByRole('button', { name: 'Импортировать', exact: true }).click();
  await expect(page.getByRole('heading', { name: records[1]!.conversation_id })).toBeVisible();
  await page.getByRole('button', { name: 'Данные оператора' }).click();
  await expect(page.getByLabel('Исход звонка')).toBeDisabled();
  await expect(page.getByLabel('Длительность звонка, секунды')).toBeDisabled();
  await page.getByLabel('Псевдоним оператора').fill(operator);
  await page.getByRole('button', { name: 'Сохранить данные оператора' }).click();
  await expect(page.getByText('Данные оператора сохранены.')).toBeVisible();

  await page.getByRole('button', { name: 'Операторы', exact: true }).click();
  const picker = page.getByLabel('Оператор', { exact: true });
  await expect(picker).toBeVisible();
  await expect(picker).not.toHaveValue(operator);
  await picker.selectOption(operator);
  await expect(page).toHaveURL(new RegExp('#operators/' + operator + '$'));
  await page.reload();
  await expect(picker).toHaveValue(operator);
  await expect(page.getByRole('heading', { name: `Профиль: ${operator}` })).toBeVisible();
  await expect(page.getByText('100 % (1 из 1)', { exact: true })).toBeVisible();
  await expect(page.getByText('1 из 2', { exact: true })).toBeVisible();
  await expect(page.getByText('3 мин 5 с', { exact: true }).first()).toBeVisible();
  await expect(page.getByText('0 из 3', { exact: true })).toBeVisible();
  await expect(page.getByRole('link', { name: /Открыть оценку/ })).toHaveCount(0);

  await page.getByRole('button', { name: `Открыть диалог ${records[0]!.conversation_id}`, exact: true }).click();
  await page.goBack();
  await expect(picker).toHaveValue(operator);
  await page.goForward();
  await page.getByRole('button', { name: 'Данные оператора' }).click();
  await expect(page.getByLabel('Псевдоним оператора')).toHaveValue(operator);
  await expect(page.getByLabel('Длительность звонка, секунды')).toHaveValue('185');

  await page.getByText('Ручной импорт синтетического JSON', { exact: true }).click();
  const previous = records[0]!;
  const revised = { ...previous, transcript: [...previous.transcript, { speaker: 'client', text: 'Updated synthetic transcript', timestamp: '2026-11-07T10:01:00Z' }] };
  await page.getByLabel('JSON для импорта').fill(JSON.stringify({ dataset_group_id: 'TEST-OPERATORS', records: [revised] }));
  await page.getByRole('checkbox', { name: 'Это синтетические данные, созданные командой' }).check();
  await page.getByRole('button', { name: 'Импортировать', exact: true }).click();
  await expect(page.getByText('Updated synthetic transcript', { exact: true })).toBeVisible();
  await page.getByRole('button', { name: 'Данные оператора' }).click();
  await expect(page.getByLabel('Псевдоним оператора')).toHaveValue(operator);
  await expect(page.getByLabel('Исход звонка')).toHaveValue('successful');
  await expect(page.getByLabel('Длительность звонка, секунды')).toHaveValue('185');
});
