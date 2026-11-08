import { useCallback } from 'react';
import { api, type OperatorProfile } from '../api/client';
import { appealsHref } from '../api/appeals';
import { ApiFailure } from '../ApiFailure';
import { useOperatorResource } from './useOperatorResource';
import { operatorHref } from './route';

type Numeric = number | string | null | undefined;
const noData = 'Нет данных';
const number = new Intl.NumberFormat('ru-RU', { maximumFractionDigits: 2 });
function metric(value: Numeric, suffix = '') {
  return value == null ? noData : number.format(Number(value)) + suffix;
}
function duration(value: number | null) {
  if (value == null) return noData;
  const hundredths = Math.round(value * 100);
  const hours = Math.floor(hundredths / 360000);
  const minutes = Math.floor(hundredths / 6000) % 60;
  const seconds = (hundredths % 6000) / 100;
  return [hours ? `${hours} ч` : '', minutes ? `${minutes} мин` : '', seconds || !hours && !minutes ? metric(seconds, ' с') : ''].filter(Boolean).join(' ');
}

export function OperatorsScreen({ operatorId = '', openConversation }: { operatorId?: string; openConversation: (versionId: string) => void }) {
  const { state, retry } = useOperatorResource(api.listOperators);
  if (state.status === 'loading') return <p className="panel panel-notice" role="status">Загружаем операторов…</p>;
  if (state.status === 'error') return <ApiFailure error={state.error} retry={retry} />;
  const { items, unassigned_count: unassigned } = state.data;
  const active = operatorId || items[0]?.operator_id;
  const inDirectory = items.some(item => item.operator_id === active);
  return <div className="operator-workspace">
    <section className="panel operator-panel">
      {items.length > 0 ? <div className="operator-picker"><div><label htmlFor="profile-operator">Оператор</label>
        <select id="profile-operator" value={inDirectory ? active : ''} onChange={event => { location.hash = operatorHref(event.target.value); }}>
          {!inDirectory && <option value="">Выберите оператора</option>}
          {items.map(item => <option key={item.operator_id} value={item.operator_id}>{item.operator_id} · диалогов: {item.conversation_count}</option>)}
        </select></div><button className="secondary-button" onClick={retry}>Обновить профили</button></div>
        : <><h2>Пока нет назначенных операторов</h2><p>Откройте карточку диалога и укажите псевдоним оператора.</p>
          <button className="secondary-button" onClick={retry}>Обновить профили</button></>}
      <p className="muted">Диалогов без назначенного оператора: {unassigned}.</p>
    </section>
    {active && (inDirectory ? <Profile key={active} operatorId={active} openConversation={openConversation} />
      : <p className="panel panel-notice" role="status">У оператора {active} нет назначенных диалогов.</p>)}
  </div>;
}

function Metrics({ title, rows }: { title: string; rows: [string, string | number][] }) {
  return <section className="panel operator-panel">
    <h3>{title}</h3><dl className="operator-metrics">{rows.map(([name, value]) => <div key={name}>
      <dt>{name}</dt><dd>{value}</dd></div>)}</dl>
  </section>;
}

function Profile({ operatorId, openConversation }: { operatorId: string; openConversation: (id: string) => void }) {
  const read = useCallback((signal: AbortSignal) => api.operatorProfile(operatorId, signal), [operatorId]);
  const { state, retry } = useOperatorResource(read);
  if (state.status === 'loading') return <p className="panel panel-notice" role="status">Загружаем профиль…</p>;
  if (state.status === 'error') return <ApiFailure error={state.error} retry={retry} />;
  const p = state.data;
  const known = p.successful_calls + p.unsuccessful_calls;
  const rate = known ? metric(p.successful_calls / known * 100, ' %') : noData;
  const criteria = [...p.criteria].sort((a, b) => Number(a.average_quality ?? Infinity) - Number(b.average_quality ?? Infinity));
  return <section aria-label="Профиль оператора" className="operator-workspace">
    <div><h2>Профиль: {p.operator_id}</h2><a className="text-link" href={operatorHref(p.operator_id)}>Ссылка на профиль</a></div>
    <div className="operator-grid">
      <Metrics title="Нагрузка" rows={[
        ['Диалоги', p.conversation_count], ['Звонки', p.call_count], ['Чаты', p.chat_count],
        ['Реплики оператора', p.operator_messages], ['Реплики клиента', p.client_messages],
        ['Среднее время первого ответа', duration(p.average_first_reply_seconds)], ['Диалоги с измеренным первым ответом', p.first_reply_count],
        ['Диалоги без ответа клиенту', p.unanswered_conversations],
      ]} />
      <Metrics title="Результаты звонков" rows={[
        ['Успешные', p.successful_calls], ['Неуспешные', p.unsuccessful_calls], ['Исход неизвестен', p.unknown_calls],
        ['Доля успешных среди звонков с известным исходом', known ? `${rate} (${p.successful_calls} из ${known})` : noData],
        ['Звонки с указанной длительностью', `${p.measured_calls} из ${p.call_count}`], ['Общая длительность', duration(p.measured_calls ? p.total_call_seconds : null)],
        ['Средняя длительность', duration(p.average_call_seconds)],
        ['Между первой и последней репликами звонка, в среднем', duration(p.average_call_span_seconds)],
        ['Звонки с измеренным интервалом реплик', `${p.timed_call_spans} из ${p.call_count}`],
      ]} />
    </div>
    <section className="panel operator-panel">
      <h3>Качество обслуживания</h3>
      <p>Среднее по завершённым live-оценкам с учётом апелляций. Тестовые mock-оценки не входят в расчёт.</p>
      <dl className="operator-metrics"><div><dt>Диалоги с итоговым баллом</dt><dd>{p.scored_conversations} из {p.conversation_count}</dd></div>
        <div><dt>Средний эффективный балл</dt><dd>{metric(p.average_effective_score, ' / 100')}</dd></div>
        <div><dt>Диалоги с флагом нарушения</dt><dd>{p.flagged_conversations}</dd></div></dl>
      <h4>Критерии</h4>
      {criteria.length ? <><p className="muted">Сначала критерии с самым низким средним качеством. Значения показаны отдельно для каждой версии чек-листа.</p>
        <ul className="operator-rows">{criteria.map(c => <li key={`${c.checklist_version_id}:${c.criterion_id}`}>
        <strong>{c.criterion_id} · {c.title}</strong><span>Среднее качество: {metric(c.average_quality, ' %')} · оценок: {c.assessed_count}</span>
        <small className="muted">Версия чек-листа: {c.checklist_version_id}</small></li>)}</ul></>
        : <p>Нет данных по оценённым критериям.</p>}
    </section>
    <section className="panel operator-panel">
      <h3>Диалоги оператора</h3>
      {p.conversations.length ? <ul className="operator-rows">{p.conversations.map(c => <li key={c.version_id}>
        <button className="text-link operator-open" onClick={() => openConversation(c.version_id)} aria-label={`Открыть диалог ${c.conversation_id}`}>{c.conversation_id}</button>
        <span>{c.language} · {c.channel === 'voice call' ? 'Звонок' : 'Чат'}</span>
        {c.channel === 'voice call' && <span>Исход: {outcomeLabel(c.outcome)} · длительность: {duration(c.duration_seconds)}</span>}
        {c.evaluation_id ? <a className="text-link" href={appealsHref(c.evaluation_id)}>Открыть оценку · {metric(c.effective_score, ' / 100')}</a> : <span className="muted">Оценка: {noData}</span>}
      </li>)}</ul> : <p>У оператора пока нет диалогов.</p>}
    </section>
  </section>;
}

function outcomeLabel(outcome: OperatorProfile['conversations'][number]['outcome']) {
  return outcome === 'successful' ? 'успешный' : outcome === 'unsuccessful' ? 'неуспешный' : noData;
}
