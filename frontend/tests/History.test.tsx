import { act, render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it, vi } from 'vitest';
import { App } from '../src/App';
import { ConversationHistory } from '../src/ConversationHistory';
import { SavedEvaluation } from '../src/SavedEvaluation';
import { createApiClient, type Transport } from '../src/api/client';
import { evaluationItems, historyConversationList, historyEvaluation, historyResult, newVersion, oldVersion, versionItems } from './historyFixtures';

const response = (data: unknown, status = 200) => new Response(JSON.stringify(data), { status });
function fixtureTransport(url: string) {
  const parsed = new URL(url, 'http://localhost'), path = parsed.pathname;
  const offset = Number(parsed.searchParams.get('offset') ?? 0), limit = Number(parsed.searchParams.get('limit') ?? 5);
  if (path.endsWith('/history/versions')) return response({ items: versionItems.slice(offset, offset + limit), total: versionItems.length, offset, limit });
  if (path.endsWith('/history/evaluations')) return response({ items: evaluationItems.slice(offset, offset + limit), total: evaluationItems.length, offset, limit });
  if (path === '/api/v1/conversations') return response(historyConversationList);
  if (path === '/api/v1/conversation-versions/' + newVersion.id) return response(newVersion);
  if (path === '/api/v1/conversation-versions/' + oldVersion.id) return response(oldVersion);
  if (path === '/api/v1/checklist-versions/' + historyResult.checklist.id) return response(historyResult.checklist);
  if (path.endsWith('/appeals')) return response({ items: [] });
  if (path === '/api/v1/evaluations/' + historyEvaluation.id) return response(historyResult.evaluation);
  throw new Error('Unexpected test URL: ' + url);
}
function install(transport: Transport = async url => fixtureTransport(url)) {
  const mocked = vi.fn<Transport>(transport); vi.stubGlobal('fetch', mocked); return mocked;
}
async function openHistory() {
  await userEvent.click(screen.getByText('История диалога', { selector: 'summary' }));
}
function renderHistory() { render(<ConversationHistory anchorId={newVersion.id} currentVersionId={newVersion.id} onVersion={vi.fn()} onEvaluation={vi.fn()} />); }

describe('История B-04', () => {
  it('кодирует внутренний version ID и передаёт offset/limit через generated-клиент', async () => {
    const transport = vi.fn<Transport>(async () => response({ items: [], total: 0, offset: 5, limit: 5 }));
    const client = createApiClient(transport), id = 'version/Қазақ ?&';
    await client.listVersionHistory(id, { offset: 5, limit: 5 });
    await client.listEvaluationHistory(id, { offset: 5, limit: 5 });
    expect(transport.mock.calls.map(call => call[0])).toEqual([
      '/api/v1/conversation-versions/' + encodeURIComponent(id) + '/history/versions?offset=5&limit=5',
      '/api/v1/conversation-versions/' + encodeURIComponent(id) + '/history/evaluations?offset=5&limit=5',
    ]);
    expect(transport.mock.calls.every(([, init]) => !init?.method)).toBe(true);
  });

  it('загружает историю только после раскрытия, открывает старый snapshot и результат без POST', async () => {
    const transport = install(); render(<App />);
    await userEvent.click(await screen.findByRole('button', { name: new RegExp(newVersion.conversation_id) }));
    expect(transport.mock.calls.some(([url]) => url.includes('/history/'))).toBe(false);
    await openHistory();
    await userEvent.click(await screen.findByRole('button', { name: new RegExp('Историческая версия.*' + oldVersion.id) }));
    expect(await screen.findByText('Версия диалога: ' + oldVersion.id)).toBeVisible();
    await userEvent.click(screen.getByRole('button', { name: /Открыть оценку/ }));
    expect(await screen.findByTestId('total-score')).toHaveTextContent('85,38');
    await userEvent.click(screen.getByRole('button', { name: 'Показать доказательство C01' }));
    expect(document.querySelector('.message--highlighted')).toHaveFocus();
    expect(document.querySelector('.message--highlighted')).toHaveTextContent(oldVersion.messages[2]!.text);
    expect(screen.queryByText(newVersion.messages[2]!.text)).not.toBeInTheDocument();
    expect(transport.mock.calls.every(([, init]) => !init?.method)).toBe(true);
    expect(transport.mock.calls.some(([url]) => url.includes('/api/v1/demo'))).toBe(false);
    expect(sessionStorage.length).toBe(0);
  });

  it('пагинация версий не меняет страницу оценок и использует тот же anchor', async () => {
    const transport = install(async url => {
      if (!url.includes('/history/versions')) return fixtureTransport(url);
      const offset = Number(new URL(url, 'http://localhost').searchParams.get('offset'));
      return response({ items: offset === 0 ? versionItems : [{ id: 'older-6', imported_at: oldVersion.imported_at }], total: 6, offset, limit: 5 });
    });
    renderHistory(); await openHistory();
    const versions = screen.getByRole('region', { name: 'Версии диалога' });
    await within(versions).findByText('Последняя версия');
    await userEvent.click(within(versions).getByRole('button', { name: 'Далее' }));
    expect(await within(versions).findByText('older-6')).toBeVisible();
    expect(within(versions).getByRole('button', { name: 'Далее' })).toBeDisabled();
    expect(within(screen.getByRole('region', { name: 'Оценки диалога' })).getByText('Страница 1')).toBeVisible();
    expect(transport.mock.calls.filter(([url]) => url.includes('/history/evaluations'))).toHaveLength(1);
    expect(transport.mock.calls.at(-1)![0]).toContain(newVersion.id + '/history/versions?offset=5&limit=5');
    await userEvent.click(within(versions).getByRole('button', { name: 'К началу истории' }));
    expect(await within(versions).findByText('Последняя версия')).toBeVisible();
  });

  for (const failure of [404, 422, 500, 'network'] as const) it('ошибка истории ' + failure + ': независимые коллекции и безопасный retry', async () => {
    let fail = true;
    const transport = install(async url => {
      if (url.includes('/history/evaluations') && fail) {
        if (failure === 'network') throw new Error('offline');
        return response({}, failure);
      }
      return fixtureTransport(url);
    });
    renderHistory(); await openHistory();
    const jobs = screen.getByRole('region', { name: 'Оценки диалога' });
    expect(await within(jobs).findByRole('alert')).toBeVisible();
    expect(await screen.findByText('Последняя версия')).toBeVisible();
    fail = false; await userEvent.click(within(jobs).getByRole('button', { name: 'Повторить загрузку' }));
    expect(await within(jobs).findByRole('button', { name: /Открыть оценку/ })).toBeVisible();
    expect(transport.mock.calls.every(([, init]) => !init?.method)).toBe(true);
  });

  it('пустой список оценок и выход за total остаются отличимыми от ошибки', async () => {
    let changed = false;
    install(async url => {
      const parsed = new URL(url, 'http://localhost'), offset = Number(parsed.searchParams.get('offset'));
      if (url.includes('/history/evaluations')) return response({ items: [], total: 0, offset, limit: 5 });
      if (changed) return response({ items: [], total: 1, offset, limit: 5 });
      return response({ items: versionItems, total: 6, offset, limit: 5 });
    });
    renderHistory(); await openHistory();
    expect(await screen.findByText(/Оценок пока нет/)).toBeVisible();
    changed = true;
    await userEvent.click(within(screen.getByRole('region', { name: 'Версии диалога' })).getByRole('button', { name: 'Далее' }));
    expect(await screen.findByText(/На этой странице записей нет/)).toBeVisible();
    expect(screen.queryByRole('alert')).not.toBeInTheDocument();
  });

  it('не подмешивает поздний ответ другого диалога с тем же внешним ID', async () => {
    let resolve!: (value: Response) => void;
    const pending = new Promise<Response>(done => { resolve = done; });
    const transport = install(async url => url.includes('first-anchor') ? pending : fixtureTransport(url));
    const { rerender } = render(<ConversationHistory key="first" anchorId="first-anchor" currentVersionId="first-anchor" onVersion={vi.fn()} onEvaluation={vi.fn()} />);
    await openHistory();
    rerender(<ConversationHistory key="second" anchorId={newVersion.id} currentVersionId={newVersion.id} onVersion={vi.fn()} onEvaluation={vi.fn()} />);
    await openHistory(); await screen.findByText('Последняя версия');
    expect(transport.mock.calls.find(([url]) => url.includes('first-anchor'))![1]!.signal!.aborted).toBe(true);
    await act(async () => resolve(response({ items: [{ id: 'foreign-late', imported_at: oldVersion.imported_at }], total: 1, offset: 0, limit: 5 })));
    expect(screen.queryByText('foreign-late')).not.toBeInTheDocument();
  });

  it('открывает queued/running по GET и продолжает polling до исторического результата', async () => {
    let reads = 0;
    const transport = install(async url => {
      if (url === '/api/v1/evaluations/' + historyEvaluation.id && ++reads <= 2) {
        return response({ ...historyResult.evaluation, status: reads === 1 ? 'queued' : 'running', output: null, original_score: null, effective_score: null });
      }
      return fixtureTransport(url);
    });
    render(<SavedEvaluation item={{ ...historyEvaluation, status: 'queued' }} />);
    expect(await screen.findByText('В очереди')).toBeVisible();
    expect(await screen.findByText('Оценка выполняется', {}, { timeout: 2000 })).toBeVisible();
    expect(await screen.findByTestId('total-score', {}, { timeout: 3000 })).toHaveTextContent('85,38');
    expect(transport.mock.calls.every(([, init]) => !init?.method)).toBe(true);
  });

  for (const status of [404, 500]) it('GET оценки ' + status + ': повторяет ID без попытки создания', async () => {
    let fail = true;
    const transport = install(async url => url === '/api/v1/evaluations/' + historyEvaluation.id && fail ? response({}, status) : fixtureTransport(url));
    render(<SavedEvaluation item={historyEvaluation} />);
    expect(await screen.findByRole('alert')).toHaveTextContent('HTTP ' + status);
    expect(screen.queryByRole('button', { name: 'Начать новую оценку' })).not.toBeInTheDocument();
    fail = false; await userEvent.click(screen.getByRole('button', { name: 'Повторить загрузку' }));
    expect(await screen.findByTestId('total-score')).toBeVisible();
    expect(transport.mock.calls.every(([, init]) => !init?.method)).toBe(true);
  });

  it('чужой snapshot отклоняется, failed остаётся серверной ошибкой', async () => {
    install(async url => url.startsWith('/api/v1/evaluations/') ? response({ ...historyResult.evaluation, conversation_version_id: 'other' }) : fixtureTransport(url));
    const { unmount } = render(<SavedEvaluation item={historyEvaluation} />);
    expect(await screen.findByRole('alert')).toHaveTextContent('другого запроса');
    unmount();
    install(async () => response({ ...historyResult.evaluation, status: 'failed', output: null, original_score: null, effective_score: null,
      failure: { code: 'SYNTHETIC_FAILURE', message: 'Синтетическая ошибка оценщика', details: [] } }));
    render(<SavedEvaluation item={historyEvaluation} />);
    expect(await screen.findByRole('alert')).toHaveTextContent('Синтетическая ошибка оценщика');
    await waitFor(() => expect(screen.queryByTestId('total-score')).not.toBeInTheDocument());
  });
});
