import { act, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { EvaluationWorkspace } from '../src/EvaluationWorkspace';
import { createApiClient, type EvaluationRequest, type Transport } from '../src/api/client';
import { rememberAttempt, restoreAttempt } from '../src/api/evaluation';
import { checklistList, demo, evaluation, historicalChecklist, historicalConversation, liveRequest } from './evaluationFixtures';

const response = (data: unknown, status = 200) => new Response(JSON.stringify(data), { status, headers: { 'Content-Type': 'application/json' } });
function deferred<T>() {
  let resolve!: (value: T) => void;
  const promise = new Promise<T>(done => { resolve = done; });
  return { promise, resolve };
}
function install(overrides?: (url: string, init?: RequestInit) => Promise<Response> | Response | undefined) {
  const transport = vi.fn<Transport>().mockImplementation(async (url, init) => {
    if (url.endsWith('/appeals')) return response({ items: [] });
    const override = overrides?.(url, init);
    if (override) return override;
    if (url === '/api/v1/checklists') return response(checklistList);
    if (url === '/api/v1/demo') return response(demo);
    if (url === '/api/v1/evaluations') return response(evaluation('succeeded', JSON.parse(init!.body as string) as EvaluationRequest), 202);
    if (url.startsWith('/api/v1/evaluations/')) return response(evaluation());
    if (url.endsWith(historicalConversation.id)) return response(historicalConversation);
    if (url.endsWith(historicalChecklist.id)) return response(historicalChecklist);
    if (url.endsWith(demo.conversation.id)) return response(demo.conversation);
    if (url.endsWith(demo.checklist.id)) return response(demo.checklist);
    throw new Error(`Unexpected test URL: ${url}`);
  });
  vi.stubGlobal('fetch', transport);
  return transport;
}
async function configureLive() {
  await screen.findByRole('option', { name: /Исторический чек-лист/ });
  await userEvent.selectOptions(screen.getByLabelText('Версия чек-листа'), historicalChecklist.id);
  await userEvent.selectOptions(screen.getByLabelText('Режим оценки'), 'live');
}
const postCalls = (transport: ReturnType<typeof install>) => transport.mock.calls.filter(call => call[1]?.method === 'POST');

beforeEach(() => sessionStorage.clear());

describe('Запуск и результат оценки', () => {
  it('404 оценки предлагает очистить только устаревшую попытку без автоматического POST', async () => {
    const stale = { body: liveRequest, key: 'stale-key', evaluationId: 'missing-evaluation' };
    const other = { body: { ...liveRequest, conversation_version_id: 'other-version' }, key: 'other-key', evaluationId: 'other-evaluation' };
    rememberAttempt(historicalConversation.id, stale); rememberAttempt('other-version', other);
    sessionStorage.setItem('cqa:decision:unrelated', 'keep-me');
    const transport = install(url => url === '/api/v1/evaluations/missing-evaluation' ? response({}, 404) : undefined);
    render(<EvaluationWorkspace conversationVersionId={historicalConversation.id} />);
    expect(await screen.findByRole('alert')).toHaveTextContent('Сохранённая оценка больше недоступна');
    expect(restoreAttempt(historicalConversation.id)).toEqual(stale);
    expect(postCalls(transport)).toHaveLength(0);
    await userEvent.click(screen.getByRole('button', { name: 'Начать новую оценку' }));
    expect(restoreAttempt(historicalConversation.id)).toBeNull();
    expect(restoreAttempt('other-version')).toEqual(other);
    expect(sessionStorage.getItem('cqa:decision:unrelated')).toBe('keep-me');
    expect(screen.getByRole('button', { name: 'Запустить оценку' })).toBeDisabled();
    expect(postCalls(transport)).toHaveLength(0);
    await configureLive();
    await userEvent.click(screen.getByRole('button', { name: 'Запустить оценку' }));
    await screen.findByTestId('total-score');
    expect(postCalls(transport)).toHaveLength(1);
    expect((postCalls(transport)[0]![1]!.headers as Record<string, string>)['Idempotency-Key']).not.toBe('stale-key');
  });

  it.each(['network', '500'])('%s при чтении сохранённой оценки сохраняет ID и ключ для безопасного повтора', async failure => {
    const attempt = { body: liveRequest, key: 'keep-key', evaluationId: evaluation().id };
    rememberAttempt(historicalConversation.id, attempt);
    let unavailable = true;
    const transport = install(url => url === `/api/v1/evaluations/${evaluation().id}` && unavailable
      ? failure === 'network' ? Promise.reject(new TypeError('offline')) : response({}, 500) : undefined);
    render(<EvaluationWorkspace conversationVersionId={historicalConversation.id} />);
    await screen.findByRole('alert');
    expect(screen.queryByRole('button', { name: 'Начать новую оценку' })).not.toBeInTheDocument();
    expect(restoreAttempt(historicalConversation.id)).toEqual(attempt);
    unavailable = false;
    await userEvent.click(screen.getByRole('button', { name: 'Повторить загрузку' }));
    await screen.findByTestId('total-score');
    expect(restoreAttempt(historicalConversation.id)).toEqual(attempt);
    expect(postCalls(transport)).toHaveLength(0);
    expect(transport.mock.calls.filter(call => call[0] === `/api/v1/evaluations/${evaluation().id}`)).toHaveLength(2);
  });
  it('повторяет ошибку загрузки чек-листов и показывает пустой список без запуска', async () => {
    let reads = 0;
    const transport = install(url => url === '/api/v1/checklists' ? (++reads === 1 ? response({}, 503) : response({ items: [] })) : undefined);
    render(<EvaluationWorkspace conversationVersionId={historicalConversation.id} />);
    expect(screen.getByText('Загружаем чек-листы…')).toBeVisible();
    expect(await screen.findByRole('alert')).toHaveTextContent('HTTP 503');
    await userEvent.click(screen.getByRole('button', { name: 'Повторить загрузку' }));
    expect(await screen.findByText('Нет доступных чек-листов.')).toBeVisible();
    await userEvent.selectOptions(screen.getByLabelText('Режим оценки'), 'live');
    expect(screen.getByRole('button', { name: 'Запустить оценку' })).toBeDisabled();
    expect(postCalls(transport)).toHaveLength(0);
  });

  it('не показывает неполный succeeded как готовый результат', async () => {
    install(url => url === '/api/v1/evaluations' ? response({ ...evaluation(), effective_score: null }, 202) : undefined);
    render(<EvaluationWorkspace conversationVersionId={historicalConversation.id} />);
    await configureLive();
    await userEvent.click(screen.getByRole('button', { name: 'Запустить оценку' }));
    expect(await screen.findByRole('alert')).toHaveTextContent('не содержит полного результата');
    expect(screen.queryByTestId('total-score')).not.toBeInTheDocument();
  });

  it('отображает null-баллы как отсутствие значения, а не нули', async () => {
    const result = evaluation();
    result.original_score = { ...result.original_score!, total: null, applicable_weight: '0', reason: 'Нет применимых критериев.' };
    result.effective_score = { ...result.effective_score!, total: null, applicable_weight: '0', reason: 'Нет применимых критериев.' };
    install(url => url === '/api/v1/evaluations' ? response(result, 202) : undefined);
    render(<EvaluationWorkspace conversationVersionId={historicalConversation.id} />);
    await configureLive();
    await userEvent.click(screen.getByRole('button', { name: 'Запустить оценку' }));
    expect(await screen.findByTestId('total-score')).toHaveTextContent('—');
    expect(screen.getByTestId('effective-score')).toHaveTextContent('—');
  });

  it('требует явный режим, загружает snapshot и показывает оба серверных балла', async () => {
    const transport = install();
    render(<EvaluationWorkspace conversationVersionId={historicalConversation.id} />);
    expect(screen.getByRole('button', { name: 'Запустить оценку' })).toBeDisabled();
    await configureLive();
    await userEvent.click(screen.getByRole('button', { name: 'Запустить оценку' }));
    expect(await screen.findByTestId('total-score')).toHaveTextContent('85,38');
    expect(screen.getByTestId('effective-score')).toHaveTextContent('70,00');
    expect(screen.getByText(/Версия чек-листа: history-checklist-v1/)).toBeVisible();
    const [url, init] = postCalls(transport)[0]!;
    expect(url).toBe('/api/v1/evaluations');
    expect(JSON.parse(init!.body as string)).toEqual(liveRequest);
    expect((init!.headers as Record<string, string>)['Idempotency-Key']).toMatch(/^[\da-f-]{36}$/);
    expect(transport.mock.calls.some(call => call[0] === '/api/v1/demo')).toBe(false);
    expect(transport.mock.calls.map(call => call[0])).toContain('/api/v1/checklist-versions/history-checklist-v1');
    await userEvent.click(screen.getByRole('button', { name: 'Показать доказательство C01' }));
    expect(document.querySelector('.message--highlighted')).toHaveFocus();
    expect(within(screen.getByRole('region', { name: 'Оценка по критериям' })).getAllByText('N/A')).toHaveLength(2);
  });

  it('показывает queued → running → succeeded и прекращает polling', async () => {
    let reads = 0;
    const transport = install(url => url === '/api/v1/evaluations' ? response(evaluation('queued'), 202)
      : url.startsWith('/api/v1/evaluations/') ? response(evaluation(++reads === 1 ? 'running' : 'succeeded')) : undefined);
    render(<EvaluationWorkspace conversationVersionId={historicalConversation.id} />);
    await configureLive();
    await userEvent.click(screen.getByRole('button', { name: 'Запустить оценку' }));
    expect(await screen.findByText('queued')).toBeVisible();
    expect(await screen.findByText('running', {}, { timeout: 2500 })).toBeVisible();
    expect(await screen.findByTestId('total-score', {}, { timeout: 2500 })).toBeVisible();
    await act(async () => { await new Promise(resolve => setTimeout(resolve, 1100)); });
    expect(reads).toBe(2);
    expect(postCalls(transport)).toHaveLength(1);
  });

  it('показывает failed и причину без финального балла или fallback', async () => {
    const transport = install(url => url === '/api/v1/evaluations' ? response(evaluation('failed'), 202) : undefined);
    render(<EvaluationWorkspace conversationVersionId={historicalConversation.id} />);
    await configureLive();
    await userEvent.click(screen.getByRole('button', { name: 'Запустить оценку' }));
    expect(await screen.findByRole('alert')).toHaveTextContent('PROVIDER_TIMEOUT');
    expect(screen.getByText('failed')).toBeVisible();
    expect(screen.queryByTestId('total-score')).not.toBeInTheDocument();
    expect(transport.mock.calls.some(call => call[0] === '/api/v1/demo')).toBe(false);
    expect(transport.mock.calls.filter(call => call[0].startsWith('/api/v1/conversation-versions/'))).toHaveLength(0);
  });

  it('блокирует двойную отправку и повторяет потерянный POST с тем же ключом и телом', async () => {
    const pending = deferred<Response>();
    let posts = 0;
    const transport = install(url => url === '/api/v1/evaluations' ? (++posts === 1 ? pending.promise : response(evaluation(), 202)) : undefined);
    render(<EvaluationWorkspace conversationVersionId={historicalConversation.id} />);
    await configureLive();
    const form = screen.getByRole('button', { name: 'Запустить оценку' }).closest('form')!;
    fireEvent.submit(form); fireEvent.submit(form);
    expect(postCalls(transport)).toHaveLength(1);
    expect(screen.getByRole('button', { name: 'Отправляем…' })).toBeDisabled();
    await act(async () => { pending.resolve(response({}, 503)); });
    expect(await screen.findByRole('alert')).toHaveTextContent('HTTP 503');
    expect(screen.getByLabelText('Режим оценки')).toBeDisabled();
    await userEvent.click(screen.getByRole('button', { name: 'Повторить отправку' }));
    await screen.findByTestId('total-score');
    const [first, second] = postCalls(transport);
    expect(second![1]!.headers).toEqual(first![1]!.headers);
    expect(second![1]!.body).toEqual(first![1]!.body);
    await userEvent.click(screen.getByRole('button', { name: 'Новая оценка' }));
    await configureLive();
    await userEvent.click(screen.getByRole('button', { name: 'Запустить оценку' }));
    await screen.findByTestId('total-score');
    expect(postCalls(transport)[2]![1]!.headers).not.toEqual(first![1]!.headers);
  });

  it('сохраняет ключ после ухода со страницы с неопределённым результатом POST', async () => {
    let posts = 0;
    const transport = install(url => url === '/api/v1/evaluations' && ++posts === 1 ? Promise.reject(new TypeError('connection lost')) : undefined);
    const view = render(<EvaluationWorkspace conversationVersionId={historicalConversation.id} />);
    await configureLive();
    await userEvent.click(screen.getByRole('button', { name: 'Запустить оценку' }));
    await screen.findByRole('alert');
    view.unmount();
    render(<EvaluationWorkspace conversationVersionId={historicalConversation.id} />);
    await userEvent.click(screen.getByRole('button', { name: 'Повторить отправку' }));
    await screen.findByTestId('total-score');
    expect(postCalls(transport)[1]![1]!.headers).toEqual(postCalls(transport)[0]![1]!.headers);
  });

  it('возобновляет известное задание по ID и повторяет GET после ошибки без нового POST', async () => {
    rememberAttempt(historicalConversation.id, { body: liveRequest, key: 'saved-key', evaluationId: evaluation().id });
    let reads = 0;
    const transport = install(url => url.startsWith('/api/v1/evaluations/') && ++reads === 1 ? response({}, 502) : undefined);
    render(<EvaluationWorkspace conversationVersionId={historicalConversation.id} />);
    expect(await screen.findByRole('alert')).toHaveTextContent('HTTP 502');
    await userEvent.click(screen.getByRole('button', { name: 'Повторить загрузку' }));
    await screen.findByTestId('total-score');
    expect(postCalls(transport)).toHaveLength(0);
    expect(reads).toBe(2);
  });

  it('использует исторические ID оценки, даже когда последняя версия чек-листа другая', async () => {
    rememberAttempt(historicalConversation.id, { body: liveRequest, key: 'historical-key', evaluationId: evaluation().id });
    const transport = install(url => url === '/api/v1/checklists' ? response({ items: [{ ...checklistList.items[0], latest_version_id: 'latest-v2', version: 2 }] }) : undefined);
    render(<EvaluationWorkspace conversationVersionId={historicalConversation.id} />);
    await screen.findByTestId('total-score');
    expect(transport.mock.calls.map(call => call[0])).toEqual(expect.arrayContaining([
      '/api/v1/evaluations/synthetic-evaluation-1', '/api/v1/conversation-versions/history-conversation-v1', '/api/v1/checklist-versions/history-checklist-v1',
    ]));
    expect(transport.mock.calls.some(call => call[0].includes('latest-v2') || call[0] === '/api/v1/checklists')).toBe(false);
  });

  it.each(['conversation', 'checklist', 'evidence'])('не отображает результат при несовпадении %s snapshot', async mismatch => {
    install(url => {
      if (mismatch === 'conversation' && url.endsWith(historicalConversation.id)) return response({ ...historicalConversation, id: 'wrong-id' });
      if (mismatch === 'checklist' && url.endsWith(historicalChecklist.id)) return response({ ...historicalChecklist, id: 'wrong-id' });
      if (mismatch === 'evidence' && url === '/api/v1/evaluations') {
        const broken = evaluation();
        broken.output!.answers[0]!.evidence[0]!.conversation_version_id = 'wrong-id';
        return response(broken, 202);
      }
    });
    render(<EvaluationWorkspace conversationVersionId={historicalConversation.id} />);
    await configureLive();
    await userEvent.click(screen.getByRole('button', { name: 'Запустить оценку' }));
    await screen.findByRole('alert');
    expect(screen.queryByTestId('total-score')).not.toBeInTheDocument();
  });

  it('повторяет загрузку snapshot после 404 без повторного запуска', async () => {
    let reads = 0;
    const transport = install(url => url.endsWith(historicalChecklist.id) && ++reads === 1 ? response({}, 404) : undefined);
    render(<EvaluationWorkspace conversationVersionId={historicalConversation.id} />);
    await configureLive();
    await userEvent.click(screen.getByRole('button', { name: 'Запустить оценку' }));
    expect(await screen.findByRole('alert')).toHaveTextContent('HTTP 404');
    expect(screen.queryByRole('button', { name: 'Начать новую оценку' })).not.toBeInTheDocument();
    expect(restoreAttempt(historicalConversation.id)?.evaluationId).toBe(evaluation().id);
    await userEvent.click(screen.getByRole('button', { name: 'Повторить загрузку' }));
    await screen.findByTestId('total-score');
    expect(postCalls(transport)).toHaveLength(1);
  });

  it('блокирует mock для вручную импортированного диалога и другой версии чек-листа', async () => {
    const transport = install();
    const view = render(<EvaluationWorkspace conversationVersionId={historicalConversation.id} />);
    await configureLive();
    await userEvent.selectOptions(screen.getByLabelText('Режим оценки'), 'mock');
    await screen.findByText(/Ручной импорт не делает/);
    await userEvent.selectOptions(screen.getByLabelText('Версия чек-листа'), demo.checklist.id);
    expect(screen.getByRole('button', { name: 'Запустить оценку' })).toBeDisabled();
    view.unmount();
    render(<EvaluationWorkspace conversationVersionId={demo.conversation.id} />);
    await configureLive();
    await userEvent.selectOptions(screen.getByLabelText('Режим оценки'), 'mock');
    await screen.findByText(/Ручной импорт не делает/);
    expect(screen.getByRole('button', { name: 'Запустить оценку' })).toBeDisabled();
    expect(postCalls(transport)).toHaveLength(0);
  });

  it('разрешает mock только для пары, полученной от demo API', async () => {
    const transport = install();
    render(<EvaluationWorkspace conversationVersionId={demo.conversation.id} />);
    await userEvent.selectOptions(screen.getByLabelText('Режим оценки'), 'mock');
    await screen.findByRole('option', { name: /встроенный mock/ });
    await userEvent.selectOptions(screen.getByLabelText('Версия чек-листа'), demo.checklist.id);
    await userEvent.click(screen.getByRole('button', { name: 'Запустить оценку' }));
    expect(await screen.findByTestId('total-score')).toBeVisible();
    expect(JSON.parse(postCalls(transport)[0]![1]!.body as string)).toEqual({ conversation_version_id: demo.conversation.id, checklist_version_id: demo.checklist.id, mode: 'mock' });
    expect(screen.getByText('Тестовые данные — не AI-оценка')).toBeVisible();
  });

  it('не подменяет ошибку проверки mock встроенной fixture', async () => {
    const transport = install(url => url === '/api/v1/demo' ? response({}, 503) : undefined);
    render(<EvaluationWorkspace conversationVersionId={demo.conversation.id} />);
    await userEvent.selectOptions(screen.getByLabelText('Режим оценки'), 'mock');
    expect(await screen.findByRole('alert')).toHaveTextContent('HTTP 503');
    expect(screen.getByRole('button', { name: 'Запустить оценку' })).toBeDisabled();
    expect(postCalls(transport)).toHaveLength(0);
  });

  it('показывает отказ live и не переключает режим на mock', async () => {
    const transport = install(url => url === '/api/v1/evaluations' ? response({ error: { code: 'LIVE_NOT_AUTHORIZED', message: 'Живой запуск не согласован.', details: [] } }, 409) : undefined);
    render(<EvaluationWorkspace conversationVersionId={historicalConversation.id} />);
    await configureLive();
    await userEvent.click(screen.getByRole('button', { name: 'Запустить оценку' }));
    expect(await screen.findByRole('alert')).toHaveTextContent('Живой запуск не согласован');
    expect(screen.getByLabelText('Режим оценки')).toHaveValue('live');
    expect(transport.mock.calls.some(call => call[0] === '/api/v1/demo')).toBe(false);
  });

  it('отменяет polling при уходе со страницы', async () => {
    rememberAttempt(historicalConversation.id, { body: liveRequest, key: 'saved-key', evaluationId: evaluation().id });
    const pending = deferred<Response>();
    const transport = install(url => url.startsWith('/api/v1/evaluations/') ? pending.promise : undefined);
    const view = render(<EvaluationWorkspace conversationVersionId={historicalConversation.id} />);
    await waitFor(() => expect(transport).toHaveBeenCalled());
    const signal = transport.mock.calls[0]![1]!.signal;
    view.unmount();
    expect(signal?.aborted).toBe(true);
    await act(async () => { pending.resolve(response(evaluation('running'))); });
    expect(transport).toHaveBeenCalledTimes(1);
  });

  it('клиент кодирует ID оценки и чек-листа', async () => {
    const transport = vi.fn<Transport>().mockImplementation(async () => response({}));
    const client = createApiClient(transport);
    await client.getEvaluation('id/with space');
    await client.getChecklistVersion('version/#');
    expect(transport.mock.calls.map(call => call[0])).toEqual(['/api/v1/evaluations/id%2Fwith%20space', '/api/v1/checklist-versions/version%2F%23']);
  });
});
