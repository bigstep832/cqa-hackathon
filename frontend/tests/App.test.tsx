import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it, vi } from 'vitest';
import fixture from '../../fixtures/synthetic/demo.json';
import { DemoScreen } from '../src/App';
import type { DemoPayload } from '../src/api/demo';

const demo = fixture as DemoPayload;

function respond(data: unknown, status = 200) {
  vi.stubGlobal('fetch', vi.fn().mockResolvedValue(new Response(JSON.stringify(data), {
    status, headers: { 'Content-Type': 'application/json' },
  })));
}

describe('Синтетический пример', () => {
  it('отображает серверный итог и переводит фокус на исходное доказательство', async () => {
    respond(demo);
    render(<DemoScreen />);
    expect(await screen.findByTestId('total-score')).toHaveTextContent('85,38');
    expect(screen.getByText('Тестовые данные — не AI-оценка')).toBeVisible();
    expect(screen.getAllByText('N/A')).toHaveLength(2);

    const evidence = demo.evaluation.output!.answers.find(answer => answer.criterion_id === 'C01')!.evidence[0]!;
    const message = demo.conversation.messages.find(item => item.message_id === evidence.start_message_id)!;
    await userEvent.click(screen.getByRole('button', { name: 'Показать доказательство C01' }));
    const target = document.getElementById(`message-${message.message_id}`);
    expect(target).toHaveFocus();
    expect(target).toHaveClass('message--highlighted');
    expect(target).toHaveTextContent(message.text);
    expect(screen.getByRole('status')).toHaveTextContent('Показано доказательство для C01');
  });

  it('показывает ошибку API без подстановки тестового результата и позволяет повторить', async () => {
    const fetchMock = vi.fn()
      .mockResolvedValueOnce(new Response('{}', { status: 503 }))
      .mockResolvedValueOnce(new Response(JSON.stringify(demo), { status: 200 }));
    vi.stubGlobal('fetch', fetchMock);
    render(<DemoScreen />);
    expect(await screen.findByRole('alert')).toHaveTextContent('HTTP 503');
    expect(screen.queryByTestId('total-score')).not.toBeInTheDocument();
    await userEvent.click(screen.getByRole('button', { name: 'Повторить загрузку' }));
    expect(await screen.findByTestId('total-score')).toHaveTextContent('85,38');
    expect(fetchMock).toHaveBeenCalledTimes(2);
  });

  it('показывает пустое состояние без ложного нулевого балла', async () => {
    respond(null);
    render(<DemoScreen />);
    expect(await screen.findByRole('heading', { name: 'Пока нет диалогов' })).toBeVisible();
    expect(screen.queryByTestId('total-score')).not.toBeInTheDocument();
  });

  it('не показывает итог незавершённой оценки', async () => {
    respond({ ...demo, evaluation: { ...demo.evaluation, status: 'failed', original_score: null } });
    render(<DemoScreen />);
    expect(await screen.findByRole('alert')).toHaveTextContent('Оценка не завершена');
    expect(screen.queryByTestId('total-score')).not.toBeInTheDocument();
  });
});
