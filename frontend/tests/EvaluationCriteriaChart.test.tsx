import { render, screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it, vi } from 'vitest';
import { EvaluationCriteriaChart } from '../src/EvaluationCriteriaChart';
import { SavedEvaluation } from '../src/SavedEvaluation';
import { ReviewWorkspace } from '../src/ReviewWorkspace';
import { chartFixture, extendedChartFixture } from './criteriaChartFixtures';

const region = () => screen.getByRole('region', { name: 'Баллы по критериям' });
describe('Диаграмма серверных баллов', () => {
  it('связывает баллы по ID, сохраняет порядок snapshot и отделяет N/A и flag', () => {
    const data = chartFixture(); data.evaluation.original_score!.criteria.reverse();
    render(<EvaluationCriteriaChart {...data} />);
    const chart = region();
    expect(within(chart).getAllByRole('meter')).toHaveLength(5);
    const c04 = within(chart).getByRole('meter', { name: /^C04/ });
    expect(c04).toHaveAttribute('aria-valuenow', '15');
    expect(c04).toHaveAttribute('aria-valuemax', '20');
    expect(c04.firstElementChild).toHaveStyle({ width: '75%' });
    expect(within(chart).getAllByText('N/A')).toHaveLength(2);
    expect(within(chart).queryByRole('meter', { name: /^C02|^C03|^C08/ })).not.toBeInTheDocument();
    expect(within(chart).getByText('Не обнаружен')).toBeVisible();
    expect(within(chart).getByText('10,5 / 15')).toBeVisible();
  });

  it.each([
    ['0', '5', '0%', '0 / 5'],
    ['0.125', '0.25', '50%', '0,125 / 0,25'],
    ['1.5', '1.5', '100%', '1,5 / 1,5'],
    ['2.5', '5', '50%', '2,5 / 5'],
    ['10', '20', '50%', '10 / 20'],
  ])('показывает %s из %s без округления дробных значений', (earned, weight, width, label) => {
    const data = chartFixture();
    data.checklist.criteria[0]!.weight = weight;
    Object.assign(data.evaluation.original_score!.criteria[0]!, { earned_points: earned, weight });
    render(<EvaluationCriteriaChart {...data} />);
    const meter = within(region()).getByRole('meter', { name: /^C01/ });
    expect(meter.firstElementChild).toHaveStyle({ width });
    expect(within(region()).getByText(label)).toBeVisible();
  });

  it('нулевой вес не делит на ноль и не создаёт meter с пустым диапазоном', () => {
    const data = chartFixture(); Object.assign(data.evaluation.original_score!.criteria[0]!, { earned_points: '0', weight: '0' });
    render(<EvaluationCriteriaChart {...data} />);
    expect(within(region()).queryByRole('meter', { name: /^C01/ })).not.toBeInTheDocument();
    expect(screen.getByText('Нулевой вес · без доли в итоге')).toBeVisible();
    expect(screen.getByText('0 / 0')).toBeVisible();
  });

  it('все N/A остаются нейтральными, а не нулевыми полосами', () => {
    const data = chartFixture();
    data.evaluation.original_score!.criteria.forEach(item => { item.applicable = false; item.earned_points = null; });
    render(<EvaluationCriteriaChart {...data} />);
    expect(screen.queryAllByRole('meter')).toHaveLength(0);
    expect(screen.getByText('Все балльные критерии отмечены N/A.')).toBeVisible();
    expect(screen.getAllByText('N/A')).toHaveLength(8);
    expect(screen.queryByText('Не обнаружен')).not.toBeInTheDocument();
  });

  it('поддерживает C09+ и отдельный сработавший флаг', () => {
    const data = extendedChartFixture();
    render(<EvaluationCriteriaChart {...data} />);
    expect(screen.getByRole('meter', { name: /^C09/ }).firstElementChild).toHaveStyle({ width: '50%' });
    expect(screen.getByText('Обнаружен')).toBeVisible();
    expect(screen.queryByRole('meter', { name: /^C08/ })).not.toBeInTheDocument();
  });

  it('показывает пустой список без подстановки критериев', () => {
    const data = chartFixture(); data.checklist.criteria = [];
    render(<EvaluationCriteriaChart {...data} />);
    expect(screen.getByText('В этой версии чек-листа нет критериев.')).toBeVisible();
    expect(screen.queryAllByRole('meter')).toHaveLength(0);
  });

  it('не превращает отсутствующие или некорректные баллы в ноль', () => {
    const data = chartFixture();
    data.evaluation.original_score!.criteria = data.evaluation.original_score!.criteria.filter(item => item.criterion_id !== 'C01');
    data.evaluation.original_score!.criteria.find(item => item.criterion_id === 'C04')!.earned_points = null;
    render(<EvaluationCriteriaChart {...data} />);
    expect(screen.getByText('Нет данных по критерию')).toBeVisible();
    expect(screen.getByText('Нет корректных данных для диаграммы')).toBeVisible();
    expect(screen.queryByRole('meter', { name: /^C01|^C04/ })).not.toBeInTheDocument();
  });

  it('переключает готовые серверные результаты, сохраняя исходные ответы и total', async () => {
    const data = chartFixture(), before = structuredClone(data);
    render(<ReviewWorkspace data={data} />);
    const chart = region();
    await userEvent.click(within(chart).getByRole('button', { name: 'Эффективные баллы' }));
    expect(within(chart).getByRole('meter', { name: /^C01/ })).toHaveAttribute('aria-valuenow', '0');
    expect(screen.getByTestId('total-score')).toHaveTextContent('85,38');
    expect(screen.getByTestId('effective-score')).toHaveTextContent('70,00');
    expect(screen.getByRole('heading', { name: 'Оценка по критериям' })).toBeVisible();
    await userEvent.click(within(chart).getByRole('button', { name: 'Исходные баллы' }));
    expect(within(chart).getByRole('meter', { name: /^C01/ })).toHaveAttribute('aria-valuenow', '10');
    expect(data).toEqual(before);
  });


  it('сохранённая оценка использует название и дробные баллы исторического snapshot', async () => {
    const data = chartFixture();
    data.checklist.id = 'checklist-old-v1';
    data.checklist.criteria[0]!.title = 'Историческое название v1';
    data.checklist.criteria[0]!.weight = '0.25';
    data.evaluation.checklist_version_id = data.checklist.id;
    Object.assign(data.evaluation.original_score!.criteria[0]!, { weight: '0.25', earned_points: '0.125' });
    const transport = vi.fn(async (url: string) => {
      const value = url === '/api/v1/evaluations/' + data.evaluation.id ? data.evaluation
        : url === '/api/v1/checklist-versions/' + data.checklist.id ? data.checklist
        : url === '/api/v1/conversation-versions/' + data.conversation.id ? data.conversation
        : url.endsWith('/appeals') ? { items: [] } : null;
      if (!value) throw new Error('Unexpected test URL: ' + url);
      return new Response(JSON.stringify(value), { status: 200 });
    });
    vi.stubGlobal('fetch', transport);
    render(<SavedEvaluation item={{ id: data.evaluation.id, conversation_version_id: data.conversation.id,
      checklist_version_id: data.checklist.id, mode: 'mock', status: 'succeeded', created_at: data.evaluation.created_at }} />);
    const chart = await screen.findByRole('region', { name: 'Баллы по критериям' });
    expect(within(chart).getByRole('meter', { name: 'C01 · Историческое название v1' })).toHaveAttribute('aria-valuemax', '0.25');
    expect(within(chart).getByText('0,125 / 0,25')).toBeVisible();
    expect(transport.mock.calls.some(([url]) => url === '/api/v1/checklist-versions/checklist-old-v1')).toBe(true);
    expect(transport.mock.calls.some(([url]) => url === '/api/v1/checklists' || url === '/api/v1/evaluations')).toBe(false);
  });

  it.each(['queued', 'running', 'failed'] as const)('не добавляет результат для %s', status => {
    const data = chartFixture(); data.evaluation.status = status;
    render(<EvaluationCriteriaChart {...data} />);
    expect(screen.queryByRole('region')).not.toBeInTheDocument();
  });
});
