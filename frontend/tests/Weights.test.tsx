import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it, vi } from 'vitest';
import { formatWeight } from '../src/format';
import { ReviewWorkspace } from '../src/ReviewWorkspace';
import { ChecklistEditor } from '../src/checklists/ChecklistEditor';
import { ChecklistsScreen } from '../src/checklists/ChecklistsScreen';
import type { ChecklistCreateRequest, Transport } from '../src/api/client';
import { originalResult } from './appealFixtures';
import { historicalChecklist } from './evaluationFixtures';
import { listOf, savedChecklist } from './checklistFixtures';

const weights = ['0', '0.25', '1.5', '5', '20'];
const displayed = ['0', '0,25', '1,5', '5', '20'];
function checklist() {
  return { ...historicalChecklist, criteria: historicalChecklist.criteria.map((item, index) => ({ ...item, weight: weights[index] ?? item.weight })) };
}
const response = (data: unknown) => new Response(JSON.stringify(data), { headers: { 'Content-Type': 'application/json' } });

describe('Отображение весов без изменения расчётов', () => {
  it.each(weights.map((value, i) => [value, displayed[i]]))('%s отображается как %s', (value, expected) => {
    expect(formatWeight(value)).toBe(expected); expect(formatWeight(Number(value))).toBe(expected);
  });
  it('не округляет Decimal и различает ноль и отсутствие веса', () => {
    expect(formatWeight('0.123456789012345678901')).toBe('0,123456789012345678901');
    expect(formatWeight('20.000')).toBe('20'); expect(formatWeight('0.000')).toBe('0');
    expect(formatWeight(null)).toBe('—'); expect(formatWeight('not a number')).toBe('—');
  });
  it('сохраняет дробные веса критериев и применимые веса обоих результатов', () => {
    const data = originalResult(); data.checklist = checklist();
    data.evaluation.original_score = { ...data.evaluation.original_score!, applicable_weight: '0.25' };
    data.evaluation.effective_score = { ...data.evaluation.effective_score!, applicable_weight: '1.5' };
    const before = structuredClone(data);
    render(<ReviewWorkspace data={data} />);
    displayed.forEach((value, i) => expect(within(document.querySelectorAll('.criteria-list .criterion')[i]! as HTMLElement).getByText(`Вес ${value}`, { exact: true })).toBeVisible());
    expect(screen.getByText('N/A исключены. Применимый вес: 0,25.')).toBeVisible();
    expect(screen.getByText('Применимый вес: 1,5.')).toBeVisible();
    expect(screen.getByTestId('total-score')).toHaveTextContent('85,38');
    expect(data).toEqual(before);
  });
  it('локализует веса в просмотре чек-листа', async () => {
    const base = checklist();
    vi.stubGlobal('fetch', vi.fn<Transport>().mockImplementation(async url => response(url === '/api/v1/checklists' ? listOf(base) : base)));
    render(<ChecklistsScreen />);
    await userEvent.click(await screen.findByRole('button', { name: new RegExp(base.name) }));
    await screen.findByRole('button', { name: 'Редактировать — новая версия' });
    const cards = [...document.querySelectorAll('.criterion-snapshot')];
    weights.forEach((_, i) => {
      const card = cards.find(item => item.querySelector('h4')?.textContent?.startsWith(base.criteria[i]!.id))!;
      expect(card.querySelector('.criterion-meta')).toHaveTextContent(`Вес: ${displayed[i]}`);
    });
  });
  it('редактор показывает запятую, принимает обе записи и отправляет точные Decimal-строки', async () => {
    const base = checklist(), before = structuredClone(base), saved = vi.fn();
    const transport = vi.fn<Transport>().mockImplementation(async (_url, init) => response(savedChecklist(JSON.parse(init!.body as string) as ChecklistCreateRequest)));
    vi.stubGlobal('fetch', transport);
    render(<ChecklistEditor base={base} onSaved={saved} onCancel={vi.fn()} />);
    const inputs = screen.getAllByLabelText('Вес', { exact: true });
    displayed.forEach((value, i) => expect(inputs[i]).toHaveValue(value));
    fireEvent.change(inputs[1]!, { target: { value: '0,25' } });
    fireEvent.change(inputs[2]!, { target: { value: '1.5' } });
    expect(inputs[2]).toHaveValue('1,5');
    await userEvent.click(screen.getByRole('button', { name: 'Сохранить новую версию' }));
    await waitFor(() => expect(saved).toHaveBeenCalledOnce());
    const body = JSON.parse(transport.mock.calls[0]![1]!.body as string) as ChecklistCreateRequest;
    expect(body.criteria.slice(0, 5).map(item => item.weight)).toEqual(weights);
    expect(base).toEqual(before);
  });
});
