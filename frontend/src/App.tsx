import { useEffect, useState } from 'react';
import type { components } from './api/generated/schema';
import { fetchDemo, type DemoPayload } from './api/demo';
import { formatDecimal } from './format';

type LoadState =
  | { status: 'loading' }
  | { status: 'error'; message: string }
  | { status: 'empty' }
  | { status: 'ready'; data: DemoPayload };
type Evidence = components['schemas']['EvidenceRef'];

const speakerLabels: Record<string, string> = {
  client: 'Клиент', operator: 'Оператор', bot: 'Бот', system: 'Система',
};

function Mark({ small = false }: { small?: boolean }) {
  return <svg className={small ? 'brand-mark brand-mark--small' : 'brand-mark'} viewBox="0 0 32 32" aria-hidden="true">
    <path d="M6 5h20v17H14l-8 6V5Z" fill="currentColor" />
    <path d="m11 13 4 4 7-8" fill="none" stroke="white" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round" />
  </svg>;
}

export function App() {
  const [attempt, setAttempt] = useState(0);
  const [state, setState] = useState<LoadState>({ status: 'loading' });

  useEffect(() => {
    const controller = new AbortController();
    fetchDemo(controller.signal).then(data => {
      if (!controller.signal.aborted) {
        setState(data && data.conversation.messages.length > 0 ? { status: 'ready', data } : { status: 'empty' });
      }
    }).catch((error: unknown) => {
      if (!controller.signal.aborted) {
        setState({ status: 'error', message: error instanceof Error ? error.message : 'Не удалось загрузить пример.' });
      }
    });
    return () => controller.abort();
  }, [attempt]);

  function retry() {
    setState({ status: 'loading' });
    setAttempt(previous => previous + 1);
  }

  return <div className="app-shell">
    <a className="skip-link" href="#main">Перейти к содержимому</a>
    <aside className="sidebar" aria-label="Разделы CQA">
      <a className="brand" href="#main" aria-label="CQA — проверка качества"><Mark /><span>CQA<span className="brand-caption">Качество диалогов</span></span></a>
      <div className="sidebar-section">
        <span className="sidebar-label">Рабочее пространство</span>
        <a className="nav-link nav-link--active" href="#main" aria-current="page"><span className="nav-symbol" aria-hidden="true">▤</span>Диалоги<span className="nav-count">{state.status === 'ready' ? '1' : '—'}</span></a>
      </div>
      <div className="sidebar-bottom">
        <span className="stage-label"><span className="status-dot" />Этап 0</span>
        <p>Общий каркас команды</p>
        <span className="muted">AI-оценщик и рабочие процессы — следующие задачи.</span>
      </div>
    </aside>

    <main id="main" className="main" tabIndex={-1}>
      <header className="page-header">
        <div><p className="breadcrumb">Диалоги <span aria-hidden="true">/</span> Тестовый пример</p><h1>Проверка диалога</h1></div>
        <span className="stage-chip">Локальный прототип</span>
      </header>
      <div className="demo-banner" role="note">
        <span className="banner-icon" aria-hidden="true">i</span>
        <div><strong>Тестовые данные — не AI-оценка</strong><p>Синтетический разговор и заранее заданные ответы для проверки интерфейса и расчёта.</p></div>
      </div>

      {state.status === 'loading' && <div className="state-panel" role="status"><span className="loading-dot" aria-hidden="true" /><h2>Загружаем тестовый пример</h2><p>Получаем диалог, критерии и результат расчёта.</p></div>}
      {state.status === 'error' && <div className="state-panel state-panel--error" role="alert"><h2>Пример не загрузился</h2><p>{state.message}</p><p className="muted">Проверьте подключение к серверу и повторите загрузку.</p><button className="primary-button" onClick={retry}>Повторить загрузку</button></div>}
      {state.status === 'empty' && <div className="state-panel"><Mark small /><h2>Пока нет диалогов</h2><p>Сервер не вернул тестовый разговор.</p><button className="primary-button" onClick={retry}>Обновить</button></div>}
      {state.status === 'ready' && <ReviewWorkspace data={state.data} />}
      <footer className="page-footer">CQA <span aria-hidden="true">/</span> Каждая оценка связана с конкретной версией диалога и чек-листа.</footer>
    </main>
  </div>;
}

function ReviewWorkspace({ data }: { data: DemoPayload }) {
  const [highlightedIds, setHighlightedIds] = useState<string[]>([]);
  const [selectedCriterion, setSelectedCriterion] = useState<string | null>(null);
  const { conversation, checklist, evaluation } = data;
  const score = evaluation.original_score;
  const answers = evaluation.output?.answers ?? [];
  if (!score) return null;

  function revealEvidence(evidence: Evidence, criterionId: string) {
    const start = conversation.messages.findIndex(message => message.message_id === evidence.start_message_id);
    const end = conversation.messages.findIndex(message => message.message_id === evidence.end_message_id);
    if (evidence.conversation_version_id !== conversation.id || start < 0 || end < start) return;
    const ids = conversation.messages.slice(start, end + 1).map(message => message.message_id);
    setHighlightedIds(ids);
    setSelectedCriterion(criterionId);
    const target = document.getElementById(`message-${evidence.start_message_id}`);
    target?.focus({ preventScroll: true });
    target?.scrollIntoView({ block: 'center', behavior: window.matchMedia('(prefers-reduced-motion: reduce)').matches ? 'instant' : 'smooth' });
  }

  return <>
    <section className="dialogue-selector" aria-label="Список диалогов">
      <div><span className="sidebar-label">Выбранный диалог</span><a className="selected-dialogue" href="#transcript-heading">Демонстрационный разговор <span aria-hidden="true">↗</span></a></div>
      <div className="dialogue-meta"><span className="tag">{conversation.language}</span><span>{conversation.channel === 'voice call' ? 'Звонок' : 'Чат'}</span><span>{conversation.messages.length} сообщений</span><span className="tag tag--green">Синтетический</span></div>
    </section>

    <section className="summary" aria-label="Результат расчёта">
      <div className="score-total">
        <span className="summary-label">Общий балл</span>
        <div><strong data-testid="total-score">{formatDecimal(score.total)}</strong><span className="score-scale">/ 100</span></div>
        <span className="score-note">Исходный тестовый результат</span>
        {score.reason && <span className="score-note">{score.reason}</span>}
      </div>
      <div className="section-scores">
        <p className="summary-label">Вклад секций в общий балл</p>
        {score.sections.filter(section => Number(section.applicable_weight) > 0).map(section => <div className="section-score" key={section.section_id}>
          <span>{section.title}</span><strong>{formatDecimal(section.normalized_points)} <span>/ {formatDecimal(section.normalized_maximum)}</span></strong>
        </div>)}
        <p className="section-note">N/A исключены. Применимый вес: {formatDecimal(score.applicable_weight, 0)}.</p>
      </div>
      <div className="violations-summary">
        <span className="summary-label">Грубые нарушения</span>
        <strong className={score.flags.length ? 'violation-label' : 'clear-label'}>{score.flags.length ? 'Есть нарушение' : 'Не обнаружены'}</strong>
        <p>Отдельный флаг.<br />Не изменяет общий балл.</p>
      </div>
    </section>

    <div className="review-grid">
      <section className="panel transcript-panel" aria-labelledby="transcript-heading">
        <div className="panel-header"><div><h2 id="transcript-heading">Транскрипт</h2><p>Оцениваются действия оператора</p></div><span className="message-counter">{conversation.messages.length}</span></div>
        <div className="transcript">
          {conversation.messages.map(message => <article
            className={`message message--${message.speaker}${highlightedIds.includes(message.message_id) ? ' message--highlighted' : ''}`}
            key={message.message_id} id={`message-${message.message_id}`} tabIndex={-1}
            aria-label={`${speakerLabels[message.speaker] ?? message.speaker}, ${message.timestamp_raw}`}
          >
            <div className="message-meta"><span className="speaker">{speakerLabels[message.speaker] ?? message.speaker}</span><time>{message.timestamp_raw}</time></div>
            <p>{message.text}</p>
            {highlightedIds.includes(message.message_id) && <span className="evidence-tag">Доказательство {selectedCriterion}</span>}
          </article>)}
        </div>
        <div className="panel-bottom">
          {conversation.audio_url ? <a className="text-link" href={conversation.audio_url} target="_blank" rel="noreferrer">Открыть запись звонка</a> : <span>Для этого примера аудиозаписи нет</span>}
          <span className="version-label">Версия диалога: {conversation.id}</span>
        </div>
      </section>

      <section className="panel criteria-panel" aria-labelledby="criteria-heading">
        <div className="panel-header"><div><h2 id="criteria-heading">Оценка по критериям</h2><p>{checklist.name} · версия {checklist.version}</p></div><span className="message-counter">{checklist.criteria.length}</span></div>
        <div className="criteria-list">
          {checklist.criteria.map(criterion => {
            const result = answers.find(answer => answer.criterion_id === criterion.id);
            const points = score.criteria.find(item => item.criterion_id === criterion.id);
            if (!result) return <div className="criterion" key={criterion.id}><h3>{criterion.title}</h3><p>Ответ отсутствует</p></div>;
            const answer = result.answer;
            const label = answer.type === 'na' ? 'N/A' : criterion.options.find(option => option.value === answer.value)?.label ?? String(answer.value);
            const tone = answer.type === 'na' ? 'na' : criterion.kind === 'flag' ? (points?.flagged ? 'danger' : 'clear') : Number(points?.quality ?? 0) === 1 ? 'clear' : 'partial';
            return <article className={`criterion${selectedCriterion === criterion.id ? ' criterion--selected' : ''}`} key={criterion.id}>
              <div className="criterion-title"><span className="criterion-id">{criterion.id}</span><h3>{criterion.title}</h3><span className={`answer-badge answer-badge--${tone}`}>{label}</span></div>
              <div className="criterion-points">{criterion.kind === 'flag' ? 'Без веса и баллов' : <><span>Вес {formatDecimal(criterion.weight, 0)}</span><span>{answer.type === 'na' ? 'Исключён из расчёта' : `Начислено ${formatDecimal(points?.earned_points)}`}</span></>}</div>
              <p className="explanation">{result.explanation}</p>
              {answer.type === 'na' && <p className="na-reason"><strong>Почему N/A:</strong> {answer.reason}</p>}
              <div className="evidence-actions">{result.evidence.map((evidence, index) => <button key={`${evidence.start_message_id}-${index}`} className="evidence-button" onClick={() => revealEvidence(evidence, criterion.id)} aria-label={`Показать доказательство ${criterion.id}${result.evidence.length > 1 ? `, фрагмент ${index + 1}` : ''}`}><svg viewBox="0 0 16 16" aria-hidden="true"><path d="M2 3h12v8H7l-4 3v-3H2V3Z" fill="none" stroke="currentColor" strokeWidth="1.4" /></svg>{evidence.start_message_id === evidence.end_message_id ? 'Показать сообщение' : 'Показать фрагмент'}</button>)}</div>
            </article>;
          })}
        </div>
        <div className="panel-bottom"><span>Переход открывает исходный текст доказательства</span></div>
      </section>
    </div>
    <div className="sr-only" role="status" aria-live="polite">{selectedCriterion ? `Показано доказательство для ${selectedCriterion}. Сообщений: ${highlightedIds.length}.` : ''}</div>
    <section className="next-steps" aria-labelledby="next-heading"><h2 id="next-heading">Дальше в работе команды</h2><p>AI-оценщик, импорт новых наборов, редактор критериев и апелляции ещё не реализованы. Этот экран проверяет общий формат результата и переход к доказательствам.</p></section>
  </>;
}
