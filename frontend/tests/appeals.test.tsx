import { act, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { AppealWorkspace } from '../src/AppealWorkspace';
import { AppealsScreen } from '../src/AppealsScreen';
import { AppealRequestForm, DecisionForm } from '../src/AppealForms';
import { createApiClient, type Appeal, type DecisionRequest, type Transport } from '../src/api/client';
import { replacementAnswer, rememberDecision, restoreDecision, validateAppeal } from '../src/api/appeals';
import { historicalChecklist, historicalConversation } from './evaluationFixtures';
import { decisionResponse, openAppeal, originalEvaluation, originalResult } from './appealFixtures';

const response = (data: unknown, status = 200) => new Response(JSON.stringify(data), { status, headers: { 'Content-Type': 'application/json' } });
function install(override?: (url: string, init?: RequestInit) => Response | Promise<Response> | undefined) {
  const transport = vi.fn<Transport>().mockImplementation(async (url, init) => {
    const replacement = override?.(url, init); if (replacement) return replacement;
    if (url.endsWith('/appeals')) return response({ items: [] });
    if (url.startsWith('/api/v1/evaluations/')) return response(originalEvaluation());
    if (url.endsWith(historicalChecklist.id)) return response(historicalChecklist);
    if (url.endsWith(historicalConversation.id)) return response(historicalConversation);
    throw new Error(`Unexpected URL ${url}`);
  });
  vi.stubGlobal('fetch', transport); return transport;
}
const first = historicalChecklist.criteria[0]!;
const initialAnswer = originalEvaluation().output!.answers[0]!.answer;
function renderDecision(onSaved = vi.fn(), onConflict = vi.fn()) {
  return render(<DecisionForm appeal={openAppeal()} criterion={first} original={initialAnswer} evaluation={originalEvaluation()} disabled={false} onSaved={onSaved} onConflict={onConflict} />);
}
async function configureOverride() {
  await userEvent.selectOptions(screen.getByLabelText('Действие проверяющего C01'), 'override');
  await userEvent.selectOptions(screen.getByLabelText('Новый ответ C01'), '1');
  fireEvent.change(screen.getByLabelText('Причина решения C01'), { target: { value: 'Синтетическая поправка' } });
}
beforeEach(() => sessionStorage.clear());

describe('Апелляции и решения', () => {
  it('кодирует URL и передаёт generated request, ключ находится в теле решения', async () => {
    const transport = vi.fn<Transport>().mockImplementation(async () => response({}));
    const client = createApiClient(transport), controller = new AbortController();
    await client.listAppeals('eval /#', controller.signal);
    await client.createAppeal('eval /#', { criterion_id: 'C01', comment: 'Комментарий' }, controller.signal);
    const body: DecisionRequest = { action: 'confirm', reason: 'Причина', idempotency_key: 'key' };
    await client.decideAppeal('appeal /#', body, controller.signal);
    expect(transport.mock.calls.map(call => call[0])).toEqual(['/api/v1/evaluations/eval%20%2F%23/appeals', '/api/v1/evaluations/eval%20%2F%23/appeals', '/api/v1/appeals/appeal%20%2F%23/decision']);
    expect(transport.mock.calls[2]![1]).toMatchObject({ method: 'POST', body: JSON.stringify(body), signal: controller.signal });
  });

  it('создаёт обращение с обязательным комментарием и показывает серверный статус', async () => {
    let saved: Appeal | null = null;
    const transport = install((url, init) => {
      if (!url.endsWith('/appeals')) return;
      if (init?.method === 'POST') { saved = { ...openAppeal(), ...JSON.parse(init.body as string) }; return response(saved, 201); }
      return response({ items: saved ? [saved] : [] });
    });
    render(<AppealWorkspace initial={originalResult()} />);
    expect(screen.getByText('Загружаем апелляции и актуальный результат…')).toBeVisible();
    await screen.findByText('Апелляций пока нет.');
    await userEvent.click(screen.getByRole('button', { name: 'Подать апелляцию C01' }));
    expect(screen.getByRole('button', { name: 'Отправить апелляцию' })).toBeDisabled();
    fireEvent.change(screen.getByLabelText('Комментарий к апелляции C01'), { target: { value: ' Проверить приветствие ' } });
    await userEvent.click(screen.getByRole('button', { name: 'Отправить апелляцию' }));
    expect(await screen.findByText('Комментарий: Проверить приветствие')).toBeVisible();
    expect(screen.getByText('Открыта · open')).toBeVisible();
    expect(JSON.parse(transport.mock.calls.find(call => call[1]?.method === 'POST')![1]!.body as string)).toEqual({ criterion_id: 'C01', comment: 'Проверить приветствие' });
  });

  it('override обновляет оба ресурса, сохраняет original и историческое evidence', async () => {
    const before = originalEvaluation(); let current = before; let appeal = openAppeal();
    const transport = install((url, init) => {
      if (url.endsWith('/decision')) { const saved = decisionResponse(appeal, JSON.parse(init!.body as string)); current = saved.evaluation; appeal = { ...appeal, status: 'resolved', decision: saved.decision }; return response(saved); }
      if (url.endsWith('/appeals')) return response({ items: [appeal] });
      if (url.startsWith('/api/v1/evaluations/')) return response(current);
    });
    render(<AppealWorkspace initial={originalResult()} reviewer />);
    await screen.findByLabelText('Действие проверяющего C01'); await configureOverride();
    await userEvent.click(screen.getByRole('button', { name: 'Сохранить решение' }));
    expect(await screen.findByText('Причина: Синтетическая поправка')).toBeVisible();
    expect(screen.getByTestId('effective-score')).toHaveTextContent('70,00');
    expect(screen.getByTestId('total-score')).toHaveTextContent('85,38');
    expect(current.output).toEqual(before.output); expect(current.original_score).toEqual(before.original_score);
    const card = within(screen.getByLabelText('Обращение C01'));
    expect(card.getByText('Исходный ответ:', { exact: false }).closest('p')).toHaveTextContent('true');
    expect(card.getByText('Ответ проверяющего:', { exact: false })).toHaveTextContent('false');
    await userEvent.click(screen.getByRole('button', { name: 'Показать доказательство C01' }));
    expect(document.querySelector('.message--highlighted')).toHaveFocus();
    expect(transport.mock.calls.map(call => call[0])).toContain(`/api/v1/conversation-versions/${historicalConversation.id}`);
    expect(transport.mock.calls.filter(call => call[0].endsWith('/appeals'))).toHaveLength(2);
  });

  it('confirm требует причину и не отправляет replacement answer', async () => {
    const saved = vi.fn(); const transport = install((_url, init) => response(decisionResponse(openAppeal(), JSON.parse(init!.body as string))));
    renderDecision(saved);
    await userEvent.selectOptions(screen.getByLabelText('Действие проверяющего C01'), 'confirm');
    expect(screen.getByRole('button', { name: 'Сохранить решение' })).toBeDisabled();
    fireEvent.change(screen.getByLabelText('Причина решения C01'), { target: { value: 'Подтверждено по реплике' } });
    await userEvent.click(screen.getByRole('button', { name: 'Сохранить решение' }));
    await waitFor(() => expect(saved).toHaveBeenCalledOnce());
    expect(JSON.parse(transport.mock.calls[0]![1]!.body as string)).toMatchObject({ action: 'confirm', override_answer: null, reason: 'Подтверждено по реплике' });
  });

  it('двойная отправка и потерянный ответ: после remount повторяются ключ и тело', async () => {
    let resolve!: (value: Response) => void;
    const transport = install((_url, init) => transport.mock.calls.length === 1 ? new Promise(done => { resolve = done; })
      : response(decisionResponse(openAppeal(), JSON.parse(init!.body as string))));
    const saved = vi.fn(); const view = renderDecision(saved); await configureOverride();
    const form = screen.getByRole('form', { name: 'Решение C01' });
    fireEvent.submit(form); fireEvent.submit(form); expect(transport).toHaveBeenCalledTimes(1);
    await act(async () => resolve(response({}, 500)));
    expect(await screen.findByRole('alert')).toHaveTextContent('HTTP 500');
    expect(screen.getByLabelText('Причина решения C01')).toBeDisabled();
    view.unmount(); renderDecision(saved);
    await userEvent.click(screen.getByRole('button', { name: 'Повторить решение' }));
    await waitFor(() => expect(saved).toHaveBeenCalledOnce());
    expect(transport.mock.calls[1]![1]!.body).toBe(transport.mock.calls[0]![1]!.body);
    expect(restoreDecision(openAppeal().id, first)).toBeNull();
  });

  it.each([404, 409, 422, 500])('HTTP %s при решении сохраняет причину и не изображает успех', async status => {
    const saved = vi.fn(), conflict = vi.fn(); install(() => response({}, status));
    renderDecision(saved, conflict); await configureOverride();
    await userEvent.click(screen.getByRole('button', { name: 'Сохранить решение' }));
    expect(await screen.findByRole('alert')).toHaveTextContent(`HTTP ${status}`);
    expect(screen.getByLabelText('Причина решения C01')).toHaveValue('Синтетическая поправка');
    expect(saved).not.toHaveBeenCalled(); expect(conflict).toHaveBeenCalledTimes(status === 409 ? 1 : 0);
    if (status === 422) {
      await userEvent.click(screen.getByRole('button', { name: 'Изменить решение' }));
      expect(screen.getByLabelText('Причина решения C01')).toBeEnabled();
    }
  });

  it('при конфликте решения загружает решение другого проверяющего и новый балл', async () => {
    let appeal = openAppeal(); const result = originalResult();
    install((url, init) => {
      if (url.endsWith('/decision')) { const saved = decisionResponse(appeal, { ...JSON.parse(init!.body as string), reason: 'Решение другого проверяющего' });
        appeal = { ...appeal, status: 'resolved', decision: saved.decision }; result.evaluation = saved.evaluation; return response({}, 409); }
      if (url.endsWith('/appeals')) return response({ items: [appeal] });
      if (url.startsWith('/api/v1/evaluations/')) return response(result.evaluation);
    });
    render(<AppealWorkspace initial={originalResult()} reviewer />);
    await screen.findByLabelText('Действие проверяющего C01'); await configureOverride();
    await userEvent.click(screen.getByRole('button', { name: 'Сохранить решение' }));
    expect(await screen.findByText('Причина: Решение другого проверяющего')).toBeVisible();
    expect(screen.getByText(/Конфликт HTTP 409/)).toBeVisible();
    expect(screen.queryByRole('button', { name: 'Сохранить решение' })).not.toBeInTheDocument();
  });

  it('потерянная апелляция повторяется с тем же комментарием, двойной POST исключён', async () => {
    let resolve!: (value: Response) => void;
    const saved = vi.fn(); const transport = install((_url, init) => transport.mock.calls.length === 1
      ? new Promise(done => { resolve = done; }) : response({ ...openAppeal(), ...JSON.parse(init!.body as string) }, 201));
    render(<AppealRequestForm evaluationId={originalEvaluation().id} criterionId="C01" disabled={false} onSaved={saved} onConflict={vi.fn()} />);
    fireEvent.change(screen.getByLabelText('Комментарий к апелляции C01'), { target: { value: 'Синтетический комментарий' } });
    const form = screen.getByRole('form', { name: 'Апелляция C01' }); fireEvent.submit(form); fireEvent.submit(form);
    expect(transport).toHaveBeenCalledTimes(1);
    await act(async () => resolve(response({}, 500)));
    await screen.findByRole('alert'); expect(saved).not.toHaveBeenCalled();
    await userEvent.click(screen.getByRole('button', { name: 'Повторить апелляцию' }));
    await waitFor(() => expect(saved).toHaveBeenCalledOnce());
    expect(transport.mock.calls[1]![1]!.body).toBe(transport.mock.calls[0]![1]!.body);
  });

  it('подтверждённый POST и ошибка GET не показывают устаревший балл как обновлённый', async () => {
    let decided = false, failRead = true; const appeal = openAppeal(); let saved: ReturnType<typeof decisionResponse>;
    install((url, init) => {
      if (url.endsWith('/decision')) { decided = true; saved = decisionResponse(appeal, JSON.parse(init!.body as string)); return response(saved); }
      if (url.endsWith('/appeals')) return response({ items: [decided ? { ...appeal, status: 'resolved', decision: saved.decision } : appeal] });
      if (url.startsWith('/api/v1/evaluations/')) return failRead ? response({}, 500) : response(saved.evaluation);
    });
    render(<AppealWorkspace initial={originalResult()} reviewer />);
    await screen.findByLabelText('Действие проверяющего C01'); await configureOverride();
    await userEvent.click(screen.getByRole('button', { name: 'Сохранить решение' }));
    expect(await screen.findByRole('alert')).toHaveTextContent('HTTP 500');
    expect(screen.getByText(/Обновление ещё не подтверждено/)).toBeVisible();
    expect(screen.getByTestId('effective-score')).toHaveTextContent('85,38');
    failRead = false; await userEvent.click(screen.getByRole('button', { name: 'Повторить загрузку' }));
    await waitFor(() => expect(screen.getByTestId('effective-score')).toHaveTextContent('70,00'));
  });

  it.each(['failed', 'queued', 'running'] as const)('состояние %s не допускает рассмотрение', async status => {
    install(() => response({ ...originalEvaluation(), status, output: null, failure: status === 'failed' ? { code: 'TEST_FAILURE', message: 'Ошибка оценки' } : null }));
    render(<AppealsScreen evaluationId={originalEvaluation().id} />);
    expect(await screen.findByText(`Статус: ${status}`)).toBeVisible();
    expect(screen.queryByTestId('total-score')).not.toBeInTheDocument();
  });

  it('ошибка списка, повтор и пустое состояние не подменяются demo', async () => {
    let count = 0; const transport = install(url => url.endsWith('/appeals') ? ++count === 1 ? response({}, 404) : response({ items: [] }) : undefined);
    render(<AppealWorkspace initial={originalResult()} reviewer />);
    expect(await screen.findByRole('alert')).toHaveTextContent('HTTP 404');
    await userEvent.click(screen.getByRole('button', { name: 'Повторить загрузку' }));
    expect(await screen.findByText('Апелляций пока нет.')).toBeVisible();
    expect(transport.mock.calls.some(call => call[0].includes('/demo'))).toBe(false);
  });

  it('варианты boolean/scale/choice и N/A берутся только из исторического критерия', () => {
    expect(replacementAnswer(first, '1', '')).toEqual({ type: 'boolean', value: false });
    expect(replacementAnswer(first, 'na', 'Нет ситуации')).toBeNull();
    expect(replacementAnswer({ ...first, allow_na: true }, 'na', '  Нет ситуации  ')).toEqual({ type: 'na', reason: 'Нет ситуации' });
    expect(replacementAnswer({ ...first, allow_na: true }, 'na', ' ')).toBeNull();
    expect(replacementAnswer(historicalChecklist.criteria[3]!, '1', '')).toEqual({ type: 'scale', value: 5 });
    const choice = { ...first, answer_type: 'choice' as const, options: [{ ...first.options[0]!, value: 'custom' }] };
    expect(replacementAnswer(choice, '0', '')).toEqual({ type: 'choice', value: 'custom' });
    expect(replacementAnswer(choice, '1', '')).toBeNull();
    rememberDecision('bad', { action: 'override', reason: 'Причина', idempotency_key: 'key', override_answer: { type: 'choice', value: 'new-latest-value' } });
    expect(restoreDecision('bad', choice)).toBeNull();
    expect(() => validateAppeal({ ...openAppeal(), evaluation_id: 'other' }, originalEvaluation(), historicalChecklist)).toThrow();
  });
});
