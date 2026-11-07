import type { components } from './generated/schema';

export type DemoPayload = components['schemas']['DemoPayload'];

export async function fetchDemo(signal: AbortSignal): Promise<DemoPayload | null> {
  const response = await fetch('/api/v1/demo', { signal, headers: { Accept: 'application/json' } });
  if (!response.ok) throw new Error(`Не удалось загрузить пример. Сервер вернул HTTP ${response.status}.`);
  if (response.status === 204) return null;
  const data = await response.json() as DemoPayload | null;
  if (data === null) return null;
  if (data.mode !== 'mock' || data.evaluation.output?.metadata.mode !== 'mock') {
    throw new Error('Получен неподходящий ответ: этот экран предназначен для тестового примера.');
  }
  if (data.evaluation.status !== 'succeeded' || !data.evaluation.original_score) {
    throw new Error('Оценка не завершена. Итог нельзя отобразить.');
  }
  return data;
}
