import { expect, test, type Page } from '@playwright/test';
import type { ChecklistCreateRequest, ConversationVersion } from '../../src/api/client';
import { demo } from '../evaluationFixtures';
import { listOf, savedChecklist } from '../checklistFixtures';

test('390 px: дробные веса в результате, snapshot и редакторе без изменения значений API', async ({ page }) => {
  const weights = ['0', '0.25', '1.5', '5', '20'], labels = ['0', '0,25', '1,5', '5', '20'];
  const data = structuredClone(demo);
  weights.forEach((value, index) => { data.checklist.criteria[index]!.weight = value; });
  data.evaluation.original_score!.applicable_weight = '0.25';
  data.evaluation.effective_score!.applicable_weight = '1.5';
  let current = data.checklist;
  const submitted: ChecklistCreateRequest[] = [];
  await page.route('**/api/v1/**', async route => {
    const path = new URL(route.request().url()).pathname;
    if (route.request().method() === 'POST') {
      const body = route.request().postDataJSON() as ChecklistCreateRequest; submitted.push(body); current = savedChecklist(body);
      await route.fulfill({ status: 201, json: current }); return;
    }
    const result = path === '/api/v1/demo' ? data : path === '/api/v1/checklists' ? listOf(current)
      : path.startsWith('/api/v1/checklist-versions/') ? current : { items: [], total: 0, limit: 25, offset: 0 };
    await route.fulfill({ json: result });
  });
  await page.setViewportSize({ width: 390, height: 844 });
  await page.goto('/'); await page.getByRole('button', { name: 'Демо · mock', exact: true }).click();
  await expect(page.getByTestId('total-score')).toHaveText('85,38');
  for (const [index, label] of labels.entries()) await expect(page.locator('.criteria-list .criterion').nth(index).getByText(`Вес ${label}`, { exact: true })).toBeVisible();
  await expect(page.getByText('N/A исключены. Применимый вес: 0,25.')).toBeVisible();
  await expect(page.getByText('Применимый вес: 1,5.')).toBeVisible();
  await page.getByRole('button', { name: 'Чек-листы', exact: true }).click();
  await page.locator('.conversation-item').click();
  await expect(page.getByText('boolean · scored · Вес: 0,25', { exact: true })).toBeVisible();
  await page.getByRole('button', { name: 'Редактировать — новая версия' }).click();
  for (const [index, label] of labels.entries()) await expect(page.getByLabel('Вес', { exact: true }).nth(index)).toHaveValue(label);
  await page.getByLabel('Вес', { exact: true }).first().fill('0,25');
  await expect(page.getByLabel('Вес', { exact: true }).first()).toHaveValue('0,25');
  await page.getByRole('button', { name: 'Сохранить новую версию' }).click();
  await expect(page.getByText(/Сохранена версия v2/)).toBeVisible();
  expect(submitted[0]!.criteria.slice(0, 5).map(item => item.weight)).toEqual(['0.25', '0.25', '1.5', '5', '20']);
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
});

// Six seconds of silent PCM, created entirely in memory for transport tests.
// No real audio is read, committed or sent to an external service.
function syntheticWav() {
  const sampleRate = 8000, bytes = sampleRate * 6 * 2, wav = Buffer.alloc(44 + bytes);
  wav.write('RIFF', 0); wav.writeUInt32LE(36 + bytes, 4); wav.write('WAVEfmt ', 8);
  wav.writeUInt32LE(16, 16); wav.writeUInt16LE(1, 20); wav.writeUInt16LE(1, 22);
  wav.writeUInt32LE(sampleRate, 24); wav.writeUInt32LE(sampleRate * 2, 28);
  wav.writeUInt16LE(2, 32); wav.writeUInt16LE(16, 34); wav.write('data', 36); wav.writeUInt32LE(bytes, 40);
  return wav;
}
async function audioApi(page: Page, initialFailure?: number | 'network') {
  let failure = initialFailure, reads = 0;
  const conversation: ConversationVersion = { ...demo.conversation, id: 'synthetic-old-audio-version', channel: 'voice call',
    audio_url: 'https://upstream.invalid/private-synthetic-url', messages: demo.conversation.messages.map(item => ({ ...item, timestamp_raw: '2026-11-08T05:00:05Z', audio_offset_seconds: null })) };
  const audioPath = `/api/v1/conversation-versions/${conversation.id}/audio`, wav = syntheticWav();
  const payload = { ...demo, conversation, evaluation: { ...demo.evaluation, conversation_version_id: conversation.id,
    output: { ...demo.evaluation.output!, answers: demo.evaluation.output!.answers.map(answer => ({ ...answer,
      evidence: answer.evidence.map(ref => ({ ...ref, conversation_version_id: conversation.id })),
    })) },
  } };
  await page.route('**/api/v1/**', async route => {
    const path = new URL(route.request().url()).pathname;
    if (path === audioPath) {
      reads++;
      if (failure === 'network') { await route.abort('failed'); return; }
      if (failure) { await route.fulfill({ status: failure, json: { error: { code: 'AUDIO_UNAVAILABLE', message: 'Technical synthetic response' } } }); return; }
      const range = /^bytes=(\d+)-(\d*)$/.exec(route.request().headers().range ?? '');
      const start = range ? Number(range[1]) : 0, end = range?.[2] ? Math.min(Number(range[2]), wav.length - 1) : wav.length - 1;
      await route.fulfill({ status: range ? 206 : 200, contentType: 'audio/wav', headers: {
        'Accept-Ranges': 'bytes', ...(range ? { 'Content-Range': `bytes ${start}-${end}/${wav.length}` } : {}),
      }, body: wav.subarray(start, end + 1) }); return;
    }
    const value = path === '/api/v1/demo' ? payload : path === `/api/v1/conversation-versions/${conversation.id}` ? conversation
      : path === '/api/v1/conversations' ? { items: [{ conversation_id: conversation.conversation_id, latest_version_id: conversation.id,
        language: conversation.language, channel: conversation.channel, has_audio: true, message_count: conversation.messages.length }], total: 1, offset: 0, limit: 25 } : null;
    await route.fulfill(value ? { json: value } : { status: 404, json: {} });
  });
  return { conversation, audioPath, reads: () => reads, recover: () => { failure = undefined; } };
}

test('390 px: аудио воспроизводится внутри результата, evidence не перематывает по ISO-времени', async ({ page }) => {
  const api = await audioApi(page);
  await page.setViewportSize({ width: 390, height: 844 });
  await page.goto('/'); await page.getByRole('button', { name: 'Демо · mock', exact: true }).click();
  await expect(page.getByTestId('total-score')).toBeVisible(); expect(api.reads()).toBe(0);
  await page.getByRole('button', { name: 'Загрузить аудиозапись' }).click();
  const audio = page.getByLabel('Проигрыватель записи звонка');
  await expect(audio).toHaveAttribute('src', api.audioPath);
  await expect(page.getByText('Аудиозапись доступна.', { exact: true })).toBeVisible();
  await audio.evaluate(async (element: HTMLAudioElement) => { element.muted = true; await element.play(); });
  await expect(page.getByText('Воспроизводится аудиозапись.', { exact: true })).toBeVisible();
  await expect.poll(() => audio.evaluate((element: HTMLAudioElement) => element.currentTime)).toBeGreaterThan(0);
  const time = await audio.evaluate((element: HTMLAudioElement) => { element.pause(); return element.currentTime; });
  await page.getByRole('button', { name: 'Показать доказательство C01' }).click();
  await expect(page.locator('.message--highlighted').first()).toBeFocused();
  expect(await audio.evaluate((element: HTMLAudioElement) => element.currentTime)).toBe(time);
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
});

for (const status of [404, 500, 'network'] as const) test(`аудио ${status}: ошибка в карточке, транскрипт доступен, повтор успешен`, async ({ page, context }) => {
  const api = await audioApi(page, status);
  await page.setViewportSize({ width: 390, height: 844 }); await page.goto('/');
  await page.getByRole('button', { name: new RegExp(api.conversation.conversation_id) }).click();
  const transcript = page.getByText(api.conversation.messages[0]!.text, { exact: true });
  await expect(transcript).toBeVisible(); expect(api.reads()).toBe(0);
  await page.getByRole('button', { name: 'Загрузить аудиозапись' }).click();
  await expect(page.getByRole('alert')).toContainText('Не удалось загрузить или воспроизвести аудиозапись');
  await expect(transcript).toBeVisible(); expect(context.pages()).toHaveLength(1);
  await expect(page.getByText('Technical synthetic response')).toHaveCount(0);
  api.recover(); await page.getByRole('button', { name: 'Повторить загрузку аудио' }).click();
  await expect(page.getByText('Аудиозапись доступна.', { exact: true })).toBeVisible();
  const audio = page.getByLabel('Проигрыватель записи звонка');
  await audio.evaluate(async (element: HTMLAudioElement) => { element.muted = true; await element.play(); });
  await expect.poll(() => audio.evaluate((element: HTMLAudioElement) => element.currentTime)).toBeGreaterThan(0);
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
});
