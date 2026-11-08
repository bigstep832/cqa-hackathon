import { act, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it, vi } from 'vitest';
import { ChecklistEditor } from '../src/checklists/ChecklistEditor';
import { ChecklistsScreen } from '../src/checklists/ChecklistsScreen';
import { createApiClient, type ChecklistCreateRequest, type Transport } from '../src/api/client';
import { serializeDraft, toDraft } from '../src/checklists/draft';
import { historicalChecklist } from './evaluationFixtures';
import { listOf, savedChecklist } from './checklistFixtures';

const response = (data: unknown, status = 200) => new Response(JSON.stringify(data), { status, headers: { 'Content-Type': 'application/json' } });
function install(handler: Transport) { const transport = vi.fn<Transport>(handler); vi.stubGlobal('fetch', transport); return transport; }
function editBase() { const onSaved = vi.fn(); render(<ChecklistEditor base={historicalChecklist} onSaved={onSaved} onCancel={vi.fn()} />); return onSaved; }

describe('Редактор и API чек-листов', () => {
  it('отправляет typed body без метаданных, кодирует ID и передаёт отмену', async () => {
    const transport = vi.fn<Transport>().mockImplementation(async () => response(historicalChecklist, 201));
    const client = createApiClient(transport), controller = new AbortController();
    const body = serializeDraft(toDraft(historicalChecklist));
    await client.createChecklist(body, controller.signal);
    await client.createChecklistVersion('list /#', body, controller.signal);
    expect(transport.mock.calls.map(call => call[0])).toEqual(['/api/v1/checklists', '/api/v1/checklists/list%20%2F%23/versions']);
    expect(transport.mock.calls[1]![1]).toMatchObject({ method: 'POST', signal: controller.signal, body: JSON.stringify(body), headers: { 'Content-Type': 'application/json' } });
  });

  it('добавляет девятый критерий и сохраняет immutable версию', async () => {
    const original = structuredClone(historicalChecklist);
    const transport = install(async (_url, init) => response(savedChecklist(JSON.parse(init!.body as string) as ChecklistCreateRequest), 201));
    const saved = editBase();
    await userEvent.click(screen.getByRole('button', { name: 'Добавить критерий' }));
    const criterion = within(document.querySelectorAll('.criterion-editor')[8]! as HTMLElement);
    expect(criterion.getByLabelText('ID критерия')).toHaveValue('C09');
    fireEvent.change(criterion.getByLabelText('Название критерия'), { target: { value: 'Новый критерий' } });
    fireEvent.change(criterion.getByLabelText('Описание критерия'), { target: { value: 'Синтетическая инструкция' } });
    criterion.getAllByLabelText('Описание варианта').forEach(input => fireEvent.change(input, { target: { value: 'Описание синтетического ответа' } }));
    await userEvent.click(screen.getByRole('button', { name: 'Сохранить новую версию' }));
    await waitFor(() => expect(saved).toHaveBeenCalled());
    const body = JSON.parse(transport.mock.calls[0]![1]!.body as string) as ChecklistCreateRequest;
    expect(body.criteria).toHaveLength(9);
    expect(body.criteria[8]).toMatchObject({ id: 'C09', answer_type: 'boolean', options: [{ value: true }, { value: false }] });
    expect(historicalChecklist).toEqual(original);
  });

  it('создаёт новый чек-лист и привязывает критерий к переименованной секции', async () => {
    const transport = install(async (_url, init) => response(savedChecklist(JSON.parse(init!.body as string) as ChecklistCreateRequest, 1), 201));
    const onSaved = vi.fn(); render(<ChecklistEditor onSaved={onSaved} onCancel={vi.fn()} />);
    for (const [label, value] of [['Название чек-листа', 'Синтетический новый'], ['ID секции', 'opening-new'], ['Название секции', 'Открытие'], ['Название критерия', 'Приветствие'], ['Описание критерия', 'Проверяем приветствие оператора']]) {
      fireEvent.change(screen.getByLabelText(label!), { target: { value } });
    }
    screen.getAllByLabelText('Описание варианта').forEach(input => fireEvent.change(input, { target: { value: 'Описание' } }));
    await userEvent.click(screen.getByRole('button', { name: 'Создать чек-лист' }));
    await waitFor(() => expect(onSaved).toHaveBeenCalled());
    expect(transport.mock.calls[0]![0]).toBe('/api/v1/checklists');
    expect(JSON.parse(transport.mock.calls[0]![1]!.body as string).criteria[0].section_id).toBe('opening-new');
  });

  it('меняет boolean → scale → choice, требует новые описания, поддерживает flag и N/A', async () => {
    const transport = install(async (_url, init) => response(savedChecklist(JSON.parse(init!.body as string) as ChecklistCreateRequest), 201));
    editBase();
    const first = within(document.querySelector('.criterion-editor')! as HTMLElement);
    await userEvent.selectOptions(first.getByLabelText('Тип ответа'), 'scale');
    expect(first.getAllByLabelText('Значение').map(input => (input as HTMLInputElement).value)).toEqual(['0', '1']);
    await userEvent.selectOptions(first.getByLabelText('Тип ответа'), 'choice');
    await userEvent.click(screen.getByRole('button', { name: 'Сохранить новую версию' }));
    expect(transport).not.toHaveBeenCalled();
    expect(first.getAllByLabelText('Описание варианта')[0]).toHaveFocus();
    first.getAllByLabelText('Описание варианта').forEach(input => fireEvent.change(input, { target: { value: 'Описание' } }));
    await userEvent.selectOptions(first.getByLabelText('Назначение критерия'), 'flag');
    expect(first.queryByLabelText('Вес')).not.toBeInTheDocument();
    await userEvent.click(first.getAllByLabelText('Этот вариант означает нарушение')[1]!);
    await userEvent.click(first.getByLabelText('Разрешить N/A'));
    fireEvent.change(first.getByLabelText('Условие N/A'), { target: { value: 'Ситуация отсутствует' } });
    await userEvent.click(screen.getByRole('button', { name: 'Сохранить новую версию' }));
    await waitFor(() => expect(transport).toHaveBeenCalledTimes(1));
    expect(JSON.parse(transport.mock.calls[0]![1]!.body as string).criteria[0]).toMatchObject({ kind: 'flag', answer_type: 'choice', weight: null,
      allow_na: true, na_condition: 'Ситуация отсутствует', options: [{ value: 'option_1', quality: null, flagged: false }, { value: 'option_2', quality: null, flagged: true }] });
  });

  it.each([400, 409, 422, 500])('сохраняет поля после HTTP %s и позволяет исправить и повторить', async status => {
    let posts = 0;
    const transport = install(async (_url, init) => ++posts === 1 ? response({ error: { code: 'TEST_ERROR', message: 'Синтетическая ошибка',
      details: status === 422 ? [{ field: 'body.name', message: 'Уточните название' }] : [],
    } }, status) : response(savedChecklist(JSON.parse(init!.body as string) as ChecklistCreateRequest), 201));
    const saved = editBase();
    fireEvent.change(screen.getByLabelText('Название чек-листа'), { target: { value: 'Сохранить этот черновик' } });
    await userEvent.click(screen.getByRole('button', { name: 'Сохранить новую версию' }));
    await screen.findByText(`Синтетическая ошибка (HTTP ${status})`);
    expect(screen.getByLabelText('Название чек-листа')).toHaveValue('Сохранить этот черновик');
    expect(saved).not.toHaveBeenCalled(); expect(transport).toHaveBeenCalledTimes(1);
    if (status === 422) expect(screen.getByLabelText('Название чек-листа')).toHaveAttribute('aria-invalid', 'true');
    await userEvent.click(screen.getByRole('button', { name: 'Сохранить новую версию' }));
    await waitFor(() => expect(saved).toHaveBeenCalled());
    expect(transport.mock.calls[1]![1]!.body).toBe(transport.mock.calls[0]![1]!.body);
  });

  it('блокирует двойное сохранение и отменяет ожидание при размонтировании', async () => {
    let resolve!: (response: Response) => void;
    const transport = install(() => new Promise(done => { resolve = done; }));
    const saved = vi.fn(); const view = render(<ChecklistEditor base={historicalChecklist} onSaved={saved} onCancel={vi.fn()} />);
    const form = screen.getByRole('button', { name: 'Сохранить новую версию' }).closest('form')!;
    fireEvent.submit(form); fireEvent.submit(form);
    expect(transport).toHaveBeenCalledTimes(1);
    expect(screen.getByRole('button', { name: 'Сохраняем…' })).toBeDisabled();
    expect(screen.getByLabelText('Название чек-листа')).toBeDisabled();
    view.unmount(); expect(transport.mock.calls[0]![1]!.signal?.aborted).toBe(true);
    await act(async () => resolve(response(historicalChecklist, 201)));
    expect(saved).not.toHaveBeenCalled();
  });

  it('показывает loading, ошибку и пустой список без demo fallback', async () => {
    let calls = 0;
    const transport = install(async () => ++calls === 1 ? response({}, 500) : response({ items: [] }));
    render(<ChecklistsScreen />);
    expect(screen.getByText('Загружаем чек-листы…')).toBeVisible();
    expect(await screen.findByRole('alert')).toHaveTextContent('HTTP 500');
    await userEvent.click(screen.getByRole('button', { name: 'Повторить загрузку' }));
    expect(await screen.findByText('Чек-листов пока нет. Создайте первый.')).toBeVisible();
    expect(transport.mock.calls.every(call => call[0] === '/api/v1/checklists')).toBe(true);
  });

  it('загружает точную версию, повторяет GET после ошибки и отклоняет чужой snapshot', async () => {
    let reads = 0;
    install(async url => url === '/api/v1/checklists' ? response(listOf(historicalChecklist))
      : response(++reads === 1 ? { ...historicalChecklist, id: 'wrong' } : historicalChecklist));
    render(<ChecklistsScreen />);
    await userEvent.click(await screen.findByRole('button', { name: new RegExp(historicalChecklist.name) }));
    expect(await screen.findByRole('alert')).toHaveTextContent('другую версию');
    expect(screen.queryByRole('button', { name: 'Редактировать — новая версия' })).not.toBeInTheDocument();
    await userEvent.click(screen.getByRole('button', { name: 'Повторить загрузку' }));
    expect(await screen.findByRole('button', { name: 'Редактировать — новая версия' })).toBeVisible();
    expect(screen.getByText('Критериев: 8', { exact: false })).toBeVisible();
  });
});
