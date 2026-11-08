import { useState, type ReactNode } from 'react';
import type { components } from './api/generated/schema';
import type { EvaluationResult } from './api/evaluation';
import { formatDecimal, formatWeight } from './format';
import { ConversationAudio } from './ConversationAudio';

type Evidence = components['schemas']['EvidenceRef'];
const speakerLabels: Record<string, string> = {
  client: 'Клиент', operator: 'Оператор', bot: 'Бот', system: 'Система',
};

export function ReviewWorkspace({ data, renderAppeal }: { data: EvaluationResult; renderAppeal?: (criterionId: string) => ReactNode }) {
  const [highlightedIds, setHighlightedIds] = useState<string[]>([]);
  const [selectedCriterion, setSelectedCriterion] = useState<string | null>(null);
  const { conversation, checklist, evaluation } = data;
  const score = evaluation.original_score;
  const answers = evaluation.output?.answers ?? [];
  if (evaluation.status !== 'succeeded' || !score || !evaluation.output || !evaluation.effective_score) return null;

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
      <div><span className="sidebar-label">Выбранный диалог</span><a className="selected-dialogue" href="#transcript-heading">{conversation.conversation_id} <span aria-hidden="true">↗</span></a></div>
      <div className="dialogue-meta"><span className="tag">{conversation.language}</span><span>{conversation.channel === 'voice call' ? 'Звонок' : 'Чат'}</span><span>{conversation.messages.length} сообщений</span>{conversation.synthetic && <span className="tag tag--green">Синтетический</span>}</div>
    </section>

    <div className="score-overview">
    <section className="summary" aria-label="Исходный результат">
      <div className="score-total">
        <span className="summary-label">Исходный балл</span>
        <div><strong data-testid="total-score">{formatDecimal(score.total)}</strong><span className="score-scale">/ 100</span></div>
        <span className="score-note">{evaluation.output.metadata.mode === 'mock' ? 'Исходный тестовый результат' : 'Исходная AI-оценка'}</span>
        {score.reason && <span className="score-note">{score.reason}</span>}
      </div>
      <div className="section-scores">
        <p className="summary-label">Вклад секций в исходный балл</p>
        {score.sections.filter(section => Number(section.applicable_weight) > 0).map(section => <div className="section-score" key={section.section_id}>
          <span>{section.title}</span><strong>{formatDecimal(section.normalized_points)} <span>/ {formatDecimal(section.normalized_maximum)}</span></strong>
        </div>)}
        <p className="section-note">N/A исключены. Применимый вес: {formatWeight(score.applicable_weight)}.</p>
      </div>
      <div className="violations-summary">
        <span className="summary-label">Грубые нарушения</span>
        <strong className={score.flags.length ? 'violation-label' : 'clear-label'}>{score.flags.length ? 'Есть нарушение' : 'Не обнаружены'}</strong>
        <p>Отдельный флаг. Не изменяет общий балл.</p>
      </div>
    </section>

    <section className="effective-summary panel" aria-label="Эффективный результат">
      <div><span className="summary-label">Эффективный балл</span><strong data-testid="effective-score">{formatDecimal(evaluation.effective_score.total)}</strong><span> / 100</span>
        <p className="muted">Серверный результат с учётом решений по апелляциям.</p>
        {evaluation.effective_score.reason && <p>{evaluation.effective_score.reason}</p>}</div>
      <div className="effective-sections">
        <p className="summary-label">Вклад секций в эффективный балл</p>
        {evaluation.effective_score.sections.filter(section => Number(section.applicable_weight) > 0).map(section => <div className="section-score" key={section.section_id}>
          <span>{section.title}</span><strong>{formatDecimal(section.normalized_points)} <span>/ {formatDecimal(section.normalized_maximum)}</span></strong>
        </div>)}
        <p className="section-note">Применимый вес: {formatWeight(evaluation.effective_score.applicable_weight)}.</p>
      </div>
      <div className="violations-summary">
        <span className="summary-label">Грубые нарушения</span>
        <strong className={evaluation.effective_score.flags.length ? 'violation-label' : 'clear-label'}>{evaluation.effective_score.flags.length ? 'Есть нарушение' : 'Не обнаружены'}</strong>
        <p>Отдельный флаг. Не изменяет общий балл.</p>
      </div>
    </section>
    </div>
    <p className="result-provenance">Ниже — исходные ответы, пояснения и доказательства. Версия чек-листа: {checklist.id}.</p>
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
          <ConversationAudio conversation={conversation} />
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
              <div className="criterion-points">{criterion.kind === 'flag' ? 'Без веса и баллов' : <><span>Вес {formatWeight(criterion.weight)}</span><span>{answer.type === 'na' ? 'Исключён из расчёта' : `Начислено ${formatDecimal(points?.earned_points)}`}</span></>}</div>
              <p className="explanation">{result.explanation}</p>
              {answer.type === 'na' && <p className="na-reason"><strong>Почему N/A:</strong> {answer.reason}</p>}
              <div className="evidence-actions">{result.evidence.map((evidence, index) => <button key={`${evidence.start_message_id}-${index}`} className="evidence-button" onClick={() => revealEvidence(evidence, criterion.id)} aria-label={`Показать доказательство ${criterion.id}${result.evidence.length > 1 ? `, фрагмент ${index + 1}` : ''}`}><svg viewBox="0 0 16 16" aria-hidden="true"><path d="M2 3h12v8H7l-4 3v-3H2V3Z" fill="none" stroke="currentColor" strokeWidth="1.4" /></svg>{evidence.start_message_id === evidence.end_message_id ? 'Показать сообщение' : 'Показать фрагмент'}</button>)}</div>
              {renderAppeal?.(criterion.id)}
            </article>;
          })}
        </div>
        <div className="panel-bottom"><span>Переход открывает исходный текст доказательства</span></div>
      </section>
    </div>
    <div className="sr-only" role="status" aria-live="polite">{selectedCriterion ? `Показано доказательство для ${selectedCriterion}. Сообщений: ${highlightedIds.length}.` : ''}</div>
  </>;
}
