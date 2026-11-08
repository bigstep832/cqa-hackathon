import { describe, expect, it, vi } from 'vitest';
import { ApiError, createApiClient, type ImportRequest, type Transport } from '../src/api/client';

const response = (data: unknown, status = 200) => new Response(JSON.stringify(data), {
  status, headers: { 'Content-Type': 'application/json' },
});

describe('Типизированный API client', () => {
  it('кодирует фильтры и передаёт сигнал отмены', async () => {
    const transport = vi.fn<Transport>().mockResolvedValue(response({ items: [], total: 0, limit: 25, offset: 25 }));
    const controller = new AbortController();
    await createApiClient(transport).listConversations({ language: 'KZ', channel: 'voice call', limit: 25, offset: 25 }, controller.signal);
    const [url, init] = transport.mock.calls[0]!;
    expect(new URL(url, 'http://localhost').searchParams.get('channel')).toBe('voice call');
    expect(url).toContain('language=KZ');
    expect(url).toContain('offset=25');
    expect(init?.signal).toBe(controller.signal);
  });

  it('открывает конкретную версию и кодирует ID', async () => {
    const transport = vi.fn<Transport>().mockResolvedValue(response({ id: 'version/one ?' }));
    await createApiClient(transport).getConversationVersion('version/one ?');
    expect(transport.mock.calls[0]![0]).toBe('/api/v1/conversation-versions/version%2Fone%20%3F');
  });

  it('передаёт typed JSON и исходный текст JSON без потери повторяющихся полей', async () => {
    const transport = vi.fn<Transport>().mockImplementation(async () => response({ imported_count: 0 }));
    const client = createApiClient(transport);
    const body: ImportRequest = { dataset_group_id: 'SYNTHETIC_TEST', records: [] };
    await client.importConversations(body);
    expect(transport.mock.calls[0]![1]).toMatchObject({ method: 'POST', body: JSON.stringify(body), headers: { 'Content-Type': 'application/json' } });
    const raw = '{"dataset_group_id":"ONE","dataset_group_id":"TWO","records":[]}';
    await client.importConversations(raw);
    expect(transport.mock.calls[1]![1]?.body).toBe(raw);
  });

  it('сохраняет status, code и ошибки полей из backend envelope', async () => {
    const transport = vi.fn<Transport>().mockResolvedValue(response({ error: {
      code: 'VALIDATION_ERROR', message: 'Проверьте поля.', details: [{ field: 'body.records.0.language', message: 'Недопустимое значение.' }],
    } }, 422));
    await expect(createApiClient(transport).importConversations('{}')).rejects.toMatchObject({
      status: 422, code: 'VALIDATION_ERROR', details: [{ field: 'body.records.0.language', message: 'Недопустимое значение.' }],
    });
    expect(transport).toHaveBeenCalledTimes(1);
  });

  it('объясняет 501 и не пытается получить mock', async () => {
    const transport = vi.fn<Transport>().mockResolvedValue(response({}, 501));
    await expect(createApiClient(transport).listConversations()).rejects.toThrow('ещё не реализована');
    expect(transport).toHaveBeenCalledTimes(1);
  });

  it('не показывает HTML ошибки прокси пользователю', async () => {
    const transport = vi.fn<Transport>().mockResolvedValue(new Response('<html>private upstream</html>', { status: 502 }));
    await expect(createApiClient(transport).listConversations()).rejects.toMatchObject({ status: 502, message: 'Сервер не выполнил запрос. (HTTP 502)' });
  });

  it('отклоняет некорректный успешный JSON', async () => {
    const transport = vi.fn<Transport>().mockResolvedValue(new Response('not json'));
    await expect(createApiClient(transport).listConversations()).rejects.toMatchObject({ code: 'INVALID_RESPONSE' });
  });

  it('сообщает сетевую ошибку без автоматического повтора POST', async () => {
    const transport = vi.fn<Transport>().mockRejectedValue(new TypeError('fetch failed'));
    await expect(createApiClient(transport).importConversations('{}')).rejects.toBeInstanceOf(ApiError);
    expect(transport).toHaveBeenCalledTimes(1);
  });

  it('сохраняет отмену запроса', async () => {
    const controller = new AbortController();
    controller.abort();
    const transport = vi.fn<Transport>().mockRejectedValue(controller.signal.reason);
    await expect(createApiClient(transport).listConversations({}, controller.signal)).rejects.toBe(controller.signal.reason);
  });
});
