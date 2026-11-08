import { render, screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it, vi } from 'vitest';
import fixture from '../../fixtures/synthetic/demo.json';
import { App } from '../src/App';
import type { ConversationVersion, OperatorProfile, Transport } from '../src/api/client';
import { AssignmentPanel } from '../src/operators/AssignmentPanel';
import { OperatorsScreen } from '../src/operators/OperatorsScreen';

const json = (data: unknown, status = 200) => new Response(JSON.stringify(data), { status, headers: { 'Content-Type': 'application/json' } });
const detail = { operator_id: 'agent-1', conversation_count: 5, call_count: 4, chat_count: 1,
  successful_calls: 1, unsuccessful_calls: 1, unknown_calls: 2, measured_calls: 1, total_call_seconds: 0, average_call_seconds: 0,
  operator_messages: 6, client_messages: 4, first_reply_count: 0, average_first_reply_seconds: null,
  timed_call_spans: 2, average_call_span_seconds: 185, unanswered_conversations: 1,
  flagged_conversations: 1, scored_conversations: 1, average_effective_score: 70,
  criteria: [{ checklist_version_id: 'check-v1', criterion_id: 'C05', title: 'Эмпатия', assessed_count: 1, average_quality: 100 },
    { checklist_version_id: 'check-v1', criterion_id: 'C01', title: 'Приветствие', assessed_count: 1, average_quality: 50 }],
  conversations: [{ version_id: 'version/1', conversation_id: 'case/one', channel: 'voice call', language: 'RU',
    duration_seconds: 0, outcome: null, evaluation_id: 'eval-1', effective_score: 70 }],
} satisfies OperatorProfile;
const snapshot = fixture.conversation as ConversationVersion;

describe('Профиль оператора', () => {
  it('показывает охват оценок, знаменатель исходов, измеренный ноль и ведёт к данным сохранённой версии', async () => {
    const open = vi.fn();
    vi.stubGlobal('fetch', vi.fn<Transport>(async url => {
      if (url === '/api/v1/operators') return json({ items: [{ operator_id: 'agent-1', conversation_count: 5 }], unassigned_count: 3 });
      if (url === '/api/v1/operators/agent-1/profile') return json(detail);
      throw Error(url);
    }));
    render(<OperatorsScreen openConversation={open} />);
    expect(await screen.findByRole('heading', { name: 'Профиль: agent-1' })).toBeVisible();
    expect(screen.getByText('50 % (1 из 2)')).toBeVisible();
    expect(screen.getByText('1 из 5')).toBeVisible();
    expect(screen.getByText('Среднее время первого ответа').nextElementSibling).toHaveTextContent('Нет данных');
    expect(screen.getByText('Средняя длительность').nextElementSibling).toHaveTextContent('0 с');
    expect(screen.getByText('Между первой и последней репликами звонка, в среднем').nextElementSibling).toHaveTextContent('3 мин 5 с');
    const criteria = screen.getAllByText(/^(C01 · Приветствие|C05 · Эмпатия)$/);
    expect(criteria.map(item => item.textContent)).toEqual(['C01 · Приветствие', 'C05 · Эмпатия']);
    expect(screen.getByRole('link', { name: /Открыть оценку/ })).toHaveAttribute('href', '#appeals/eval-1');
    await userEvent.click(screen.getByRole('button', { name: 'Открыть диалог case/one' }));
    expect(open).toHaveBeenCalledWith('version/1');
  });

  it('даёт повтор при сбое списка и объясняет, как назначить первого оператора', async () => {
    let reads = 0;
    vi.stubGlobal('fetch', vi.fn<Transport>(async url => {
      if (url.startsWith('/api/v1/conversations?')) return json({ items: [], total: 0, limit: 25, offset: 0 });
      if (url !== '/api/v1/operators') throw Error(url);
      return ++reads === 1 ? json({ error: { message: 'Подготовка профилей' } }, 503) : json({ items: [], unassigned_count: 7 });
    }));
    render(<App />);
    await userEvent.click(screen.getByRole('button', { name: 'Операторы' }));
    expect(await screen.findByRole('alert')).toHaveTextContent('503');
    await userEvent.click(screen.getByRole('button', { name: 'Повторить загрузку' }));
    expect(await screen.findByText('Пока нет назначенных операторов')).toBeVisible();
    expect(screen.getByText('Диалогов без назначенного оператора: 7.')).toBeVisible();
  });

  it('для чата сохраняет только псевдоним в выбранную версию, включая повтор после ошибки', async () => {
    const writes: unknown[] = [];
    vi.stubGlobal('fetch', vi.fn<Transport>(async (url, init) => {
      expect(url).toBe('/api/v1/conversation-versions/version%2Fchat/operator');
      if (init?.method !== 'PUT') return json({ operator_id: null, outcome: null, duration_seconds: null });
      const body = JSON.parse(String(init.body)); writes.push(body);
      return writes.length === 1 ? json({}, 503) : json(body);
    }));
    render(<AssignmentPanel conversation={{ ...snapshot, id: 'version/chat', channel: 'agent chat' }} />);
    await userEvent.click(screen.getByRole('button', { name: 'Данные оператора' }));
    const form = within(await screen.findByRole('form', { name: 'Назначение оператора' }));
    expect(form.getByLabelText('Исход звонка')).toBeDisabled();
    expect(form.getByLabelText('Длительность звонка, секунды')).toBeDisabled();
    const alias = form.getByLabelText('Псевдоним оператора');
    await userEvent.type(alias, '.test');
    expect(form.getByRole('button', { name: 'Сохранить данные оператора' })).toBeDisabled();
    await userEvent.clear(alias); await userEvent.type(alias, 'Агент_1');
    await userEvent.click(form.getByRole('button', { name: 'Сохранить данные оператора' }));
    expect(await form.findByRole('alert')).toHaveTextContent('503');
    expect(alias).toHaveValue('Агент_1');
    await userEvent.click(form.getByRole('button', { name: 'Сохранить данные оператора' }));
    expect(await form.findByRole('status')).toHaveTextContent('сохранены');
    expect(writes).toEqual(Array(2).fill({ operator_id: 'Агент_1', outcome: null, duration_seconds: null }));
  });
});
