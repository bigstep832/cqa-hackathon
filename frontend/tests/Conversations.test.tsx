import { act, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it, vi } from 'vitest';
import fixture from '../../fixtures/synthetic/demo.json';
import { App } from '../src/App';
import type { ConversationList, ConversationVersion, ImportResponse, Transport } from '../src/api/client';

// Every record in these tests is explicitly synthetic; no HTTP service is contacted.
const first: ConversationVersion = { ...fixture.conversation, id: 'synthetic-version-1', conversation_id: 'synthetic-dialogue-1' } as ConversationVersion;
const second: ConversationVersion = { ...first, id: 'synthetic-version-2', conversation_id: 'synthetic-dialogue-2', language: 'KZ', messages: [
  { ...first.messages[0]!, message_id: 'synthetic-kz', text: 'Сәлеметсіз бе! Hello <script>test</script>', timestamp_raw: '00:03' },
] };
function list(items = [first, second], offset = 0, total = items.length): ConversationList {
  return { items: items.map(item => ({ conversation_id: item.conversation_id, latest_version_id: item.id,
    language: item.language, channel: item.channel, message_count: item.messages.length, has_audio: Boolean(item.audio_url) })), offset, limit: 25, total };
}
const response = (data: unknown, status = 200) => new Response(JSON.stringify(data), { status, headers: { 'Content-Type': 'application/json' } });
function deferred<T>() {
  let resolve!: (value: T) => void;
  const promise = new Promise<T>(done => { resolve = done; });
  return { promise, resolve };
}
function install(handler?: Transport) {
  const transport = vi.fn<Transport>().mockImplementation(handler ?? (async url => {
    if (url.startsWith('/api/v1/conversations?')) return response(list());
    if (url.endsWith(first.id)) return response(first);
    if (url.endsWith(second.id)) return response(second);
    if (url === '/api/v1/demo') return response(fixture);
    throw new Error(`Unexpected test URL: ${url}`);
  }));
  vi.stubGlobal('fetch', transport);
  return transport;
}
async function openImport() {
  const user = userEvent.setup();
  await user.click(screen.getByText('Ручной импорт синтетического JSON', { selector: 'summary' }));
  await user.click(screen.getByRole('button', { name: 'Вставить синтетический пример' }));
  await user.click(screen.getByRole('checkbox'));
  return user;
}

describe('Рабочие диалоги B-01', () => {
  it('загружает список и выбранный snapshot, сохраняя Unicode и текст как текст', async () => {
    const transport = install();
    render(<App />);
    expect(screen.getByRole('status')).toHaveTextContent('Загружаем диалоги');
    await userEvent.click(await screen.findByRole('button', { name: /synthetic-dialogue-2/ }));
    expect(await screen.findByText('Сәлеметсіз бе! Hello <script>test</script>')).toBeVisible();
    expect(screen.getByText('Версия диалога: synthetic-version-2')).toBeVisible();
    expect(transport.mock.calls.map(call => call[0])).toContain('/api/v1/conversation-versions/synthetic-version-2');
    expect(transport.mock.calls.some(call => call[0] === '/api/v1/demo')).toBe(false);
    expect(document.querySelector('.transcript script')).toBeNull();
    expect(screen.queryByTestId('total-score')).not.toBeInTheDocument();
  });

  it('передаёт фильтры серверу, сбрасывает страницу и выбранный диалог', async () => {
    const transport = install(async url => url.includes('conversation-versions') ? response(first)
      : response(list([first], Number(new URL(url, 'http://localhost').searchParams.get('offset')), 51)));
    render(<App />);
    await userEvent.click(await screen.findByRole('button', { name: /synthetic-dialogue-1/ }));
    await screen.findByText('Версия диалога: synthetic-version-1');
    await userEvent.click(screen.getByRole('button', { name: 'Далее' }));
    await screen.findByText('Страница 2');
    await userEvent.selectOptions(screen.getByLabelText('Язык'), 'EN');
    await userEvent.selectOptions(screen.getByLabelText('Канал'), 'voice call');
    await waitFor(() => expect(transport.mock.lastCall?.[0]).toContain('channel=voice+call'));
    expect(transport.mock.lastCall?.[0]).toContain('language=EN');
    expect(transport.mock.lastCall?.[0]).toContain('offset=0');
    expect(screen.getByText('Выберите диалог')).toBeVisible();
    await userEvent.click(screen.getByRole('button', { name: 'Сбросить фильтры' }));
    await waitFor(() => expect(transport.mock.lastCall?.[0]).not.toContain('language'));
  });

  it('показывает ошибку списка и повтор, затем пустое состояние без mock', async () => {
    let attempt = 0;
    const transport = install(async () => ++attempt === 1 ? response({}, 501) : response(list([])));
    render(<App />);
    expect(await screen.findByRole('alert')).toHaveTextContent('ещё не реализована');
    await userEvent.click(screen.getByRole('button', { name: 'Повторить загрузку' }));
    expect(await screen.findByText('Диалоги не найдены')).toBeVisible();
    expect(transport).toHaveBeenCalledTimes(2);
    expect(screen.queryByTestId('total-score')).not.toBeInTheDocument();
  });

  it('показывает 404 выбранной версии и позволяет повторить её загрузку', async () => {
    let attempts = 0;
    install(async url => url.startsWith('/api/v1/conversations?') ? response(list([first]))
      : ++attempts === 1 ? response({ error: { code: 'NOT_FOUND', message: 'Версия не найдена.' } }, 404) : response(first));
    render(<App />);
    await userEvent.click(await screen.findByRole('button', { name: /synthetic-dialogue-1/ }));
    expect(await screen.findByRole('alert')).toHaveTextContent('HTTP 404');
    await userEvent.click(screen.getByRole('button', { name: 'Повторить загрузку' }));
    expect(await screen.findByText('Версия диалога: synthetic-version-1')).toBeVisible();
  });

  it('отменяет старый запрос и не подменяет текущий диалог запоздавшим ответом', async () => {
    const slow = deferred<Response>();
    const transport = install(async url => url.startsWith('/api/v1/conversations?') ? response(list())
      : url.endsWith(first.id) ? slow.promise : response(second));
    render(<App />);
    await userEvent.click(await screen.findByRole('button', { name: /synthetic-dialogue-1/ }));
    expect(screen.getByText('Загружаем транскрипт…')).toBeVisible();
    const oldSignal = transport.mock.calls.find(call => call[0].endsWith(first.id))![1]!.signal;
    await userEvent.click(screen.getByRole('button', { name: /synthetic-dialogue-2/ }));
    expect(await screen.findByText('Версия диалога: synthetic-version-2')).toBeVisible();
    expect(oldSignal?.aborted).toBe(true);
    await act(async () => { slow.resolve(response(first)); });
    expect(screen.queryByText('Версия диалога: synthetic-version-1')).not.toBeInTheDocument();
  });

  it('не восстанавливает старый список после смены фильтра', async () => {
    const slow = deferred<Response>();
    install(async url => url.includes('language=KZ') ? response(list([second])) : slow.promise);
    render(<App />);
    await userEvent.selectOptions(screen.getByLabelText('Язык'), 'KZ');
    await screen.findByRole('button', { name: /synthetic-dialogue-2/ });
    await act(async () => { slow.resolve(response(list([first]))); });
    expect(screen.queryByRole('button', { name: /synthetic-dialogue-1/ })).not.toBeInTheDocument();
  });

  it('показывает пустой транскрипт и только существующую ссылку на запись', async () => {
    install(async url => url.startsWith('/api/v1/conversations?') ? response(list([first])) : response({ ...first, messages: [], audio_url: '/api/v1/conversation-versions/synthetic-version-1/audio' }));
    render(<App />);
    await userEvent.click(await screen.findByRole('button', { name: /synthetic-dialogue-1/ }));
    expect(await screen.findByText('В этой версии нет сообщений.')).toBeVisible();
    expect(screen.getByRole('link', { name: 'Открыть запись звонка' })).toHaveAttribute('href', '/api/v1/conversation-versions/synthetic-version-1/audio');
  });

  it('загружает mock только после явного выбора demo и возвращается к списку', async () => {
    const transport = install();
    render(<App />);
    await screen.findByRole('button', { name: /synthetic-dialogue-1/ });
    expect(screen.queryByText('Тестовые данные — не AI-оценка')).not.toBeInTheDocument();
    await userEvent.click(screen.getByRole('button', { name: 'Демо · mock' }));
    expect(await screen.findByTestId('total-score')).toHaveTextContent('85,38');
    expect(screen.getByText('Тестовые данные — не AI-оценка')).toBeVisible();
    await userEvent.click(screen.getByRole('button', { name: 'Диалоги' }));
    await screen.findByRole('button', { name: /synthetic-dialogue-1/ });
    expect(transport.mock.calls.filter(call => call[0] === '/api/v1/demo')).toHaveLength(1);
    expect(screen.queryByTestId('total-score')).not.toBeInTheDocument();
  });
});

describe('Ручной импорт', () => {
  it('не отправляет некорректный JSON и сохраняет введённый текст', async () => {
    const transport = install();
    render(<App />);
    const user = await openImport();
    fireEvent.change(screen.getByLabelText('JSON для импорта'), { target: { value: '{broken' } });
    await user.click(screen.getByRole('button', { name: 'Импортировать' }));
    expect(screen.getByRole('alert')).toHaveTextContent('Некорректный JSON');
    expect(screen.getByLabelText('JSON для импорта')).toHaveValue('{broken');
    expect(transport.mock.calls.some(call => call[1]?.method === 'POST')).toBe(false);
  });

  it('показывает backend validation и повторяет исходный JSON без ложного успеха', async () => {
    const transport = install(async (url, init) => init?.method === 'POST'
      ? response({ error: { code: 'VALIDATION_ERROR', message: 'Проверьте поля.', details: [{ field: 'body.records', message: 'Недопустимое значение.' }] } }, 422)
      : url.startsWith('/api/v1/conversations?') ? response(list([])) : response(first));
    render(<App />);
    const user = await openImport();
    const entered = (screen.getByLabelText('JSON для импорта') as HTMLTextAreaElement).value;
    await user.click(screen.getByRole('button', { name: 'Импортировать' }));
    expect(await screen.findByRole('alert')).toHaveTextContent('body.records');
    expect(screen.getByLabelText('JSON для импорта')).toHaveValue(entered);
    expect(screen.queryByText(/новых версий —/)).not.toBeInTheDocument();
    await user.click(screen.getByRole('button', { name: 'Повторить импорт' }));
    await waitFor(() => expect(transport.mock.calls.filter(call => call[1]?.method === 'POST')).toHaveLength(2));
    expect(transport.mock.calls.filter(call => call[0].startsWith('/api/v1/conversations?'))).toHaveLength(1);
  });

  it('ждёт подтверждения, блокирует повтор, обновляет список и открывает возвращённую версию', async () => {
    const pending = deferred<Response>();
    const imported: ImportResponse = { dataset_group_id: 'MANUAL_SAMPLE', imported_count: 1, unchanged_count: 0, conversation_version_ids: [first.id] };
    const transport = install(async (url, init) => init?.method === 'POST' ? pending.promise
      : url.startsWith('/api/v1/conversations?') ? response(list([first])) : response(first));
    render(<App />);
    const user = await openImport();
    await user.selectOptions(screen.getByLabelText('Язык'), 'KZ');
    await user.click(screen.getByRole('button', { name: 'Импортировать' }));
    expect(screen.getByRole('button', { name: 'Импортируем…' })).toBeDisabled();
    expect(screen.queryByText(/новых версий —/)).not.toBeInTheDocument();
    await act(async () => { pending.resolve(response(imported)); });
    expect(await screen.findByText(/новых версий — 1, без изменений — 0/)).toBeVisible();
    expect(await screen.findByText('Версия диалога: synthetic-version-1')).toBeVisible();
    expect(screen.getByLabelText('Язык')).toHaveValue('');
    expect(transport.mock.calls.filter(call => call[1]?.method === 'POST')).toHaveLength(1);
    const listCalls = transport.mock.calls.filter(call => call[0].startsWith('/api/v1/conversations?'));
    expect(listCalls.at(-1)?.[0]).not.toContain('language');
  });

  it('показывает unchanged_count при повторном импорте', async () => {
    install(async (url, init) => init?.method === 'POST' ? response({ dataset_group_id: 'MANUAL_SAMPLE', imported_count: 0, unchanged_count: 1, conversation_version_ids: [first.id] })
      : url.startsWith('/api/v1/conversations?') ? response(list([first])) : response(first));
    render(<App />);
    const user = await openImport();
    await user.click(screen.getByRole('button', { name: 'Импортировать' }));
    expect(await screen.findByText(/новых версий — 0, без изменений — 1/)).toBeVisible();
  });

  it('требует явного подтверждения синтетических данных', async () => {
    install();
    render(<App />);
    await userEvent.click(screen.getByText('Ручной импорт синтетического JSON', { selector: 'summary' }));
    await userEvent.click(screen.getByRole('button', { name: 'Вставить синтетический пример' }));
    const form = screen.getByLabelText('JSON для импорта').closest('form')!;
    expect(within(form).getByRole('button', { name: 'Импортировать' })).toBeDisabled();
  });
});
