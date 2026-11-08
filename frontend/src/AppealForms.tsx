import { useEffect, useRef, useState } from 'react';
import { api, ApiError, type Answer, type Appeal, type AppealRequest, type DecisionRequest, type Evaluation } from './api/client';
import { definiteRejection, rememberDecision, replacementAnswer, restoreDecision, sameAnswer, type Criterion } from './api/appeals';
import { validateEvaluation } from './api/evaluation';
import { ApiFailure } from './ApiFailure';

export function AppealRequestForm({ evaluationId, criterionId, disabled, onSaved, onConflict }: {
  evaluationId: string; criterionId: string; disabled: boolean; onSaved: () => void; onConflict: () => void;
}) {
  const [comment, setComment] = useState('');
  const [attempt, setAttempt] = useState<AppealRequest | null>(null);
  const [pending, setPending] = useState(false);
  const [error, setError] = useState<unknown>(null);
  const active = useRef<AbortController | null>(null);
  useEffect(() => () => active.current?.abort(), []);

  async function submit(event: React.SubmitEvent<HTMLFormElement>) {
    event.preventDefault();
    if (disabled || active.current) return;
    if (!comment.trim()) { setError(new Error('Комментарий обязателен.')); return; }
    const controller = new AbortController(); active.current = controller;
    const body = attempt ?? { criterion_id: criterionId, comment: comment.trim() };
    setAttempt(body); setPending(true); setError(null);
    try {
      const saved = await api.createAppeal(evaluationId, body, controller.signal);
      if (controller.signal.aborted) return;
      if (!saved.id || saved.evaluation_id !== evaluationId || saved.criterion_id !== criterionId || saved.comment !== body.comment
        || !['open', 'resolved'].includes(saved.status)) throw new Error('Сервер не подтвердил сохранение этой апелляции.');
      onSaved();
    } catch (failure) {
      if (!controller.signal.aborted) {
        setError(failure);
        if (failure instanceof ApiError && failure.status === 409) onConflict();
      }
    } finally { if (!controller.signal.aborted) { active.current = null; setPending(false); } }
  }
  return <form className="appeal-form" aria-label={`Апелляция ${criterionId}`} noValidate onSubmit={event => { void submit(event); }} aria-busy={pending}>
    <label htmlFor={`appeal-comment-${criterionId}`}>Комментарий к апелляции {criterionId}</label>
    <p className="muted" id={`appeal-help-${criterionId}`}>Обязательное поле. Опишите, почему ответ по этому критерию требует пересмотра.</p>
    <textarea aria-describedby={`appeal-help-${criterionId}`} id={`appeal-comment-${criterionId}`} rows={3} required value={comment} disabled={disabled || pending || Boolean(attempt)} onChange={event => setComment(event.target.value)} />
    {attempt && !pending && <p className="muted">Повтор отправит тот же комментарий. Сервер сохраняет одну апелляцию на критерий.</p>}
    {error != null && <ApiFailure error={error} />}
    <button className="primary-button" disabled={disabled || pending || !comment.trim()}>{pending ? 'Отправляем апелляцию…' : attempt ? 'Повторить апелляцию' : 'Отправить апелляцию'}</button>
    {definiteRejection(error) && <button className="secondary-button" type="button" disabled={disabled || pending} onClick={() => { setAttempt(null); setError(null); }}>Исправить комментарий</button>}
  </form>;
}

export function DecisionForm({ appeal, criterion, original, evaluation, disabled, onSaved, onConflict }: {
  appeal: Appeal; criterion: Criterion; original: Answer; evaluation: Evaluation; disabled: boolean;
  onSaved: () => void; onConflict: () => void;
}) {
  const [attempt, setAttempt] = useState(() => restoreDecision(appeal.id, criterion));
  const [action, setAction] = useState<DecisionRequest['action'] | ''>(attempt?.action ?? '');
  const [reason, setReason] = useState(attempt?.reason ?? '');
  const [optionIndex, setOptionIndex] = useState(() => {
    const answer = attempt?.override_answer;
    return !answer ? '' : answer.type === 'na' ? 'na' : String(criterion.options.findIndex(option => option.value === answer.value));
  });
  const [naReason, setNaReason] = useState(attempt?.override_answer?.type === 'na' ? attempt.override_answer.reason : '');
  const [pending, setPending] = useState(false);
  const [error, setError] = useState<unknown>(null);
  const active = useRef<AbortController | null>(null);
  const retained = useRef(attempt);
  useEffect(() => () => active.current?.abort(), []);
  const replacement = replacementAnswer(criterion, optionIndex, naReason);
  const valid = Boolean(action && reason.trim() && (action === 'confirm' || (replacement && !sameAnswer(original, replacement))));
  const locked = disabled || pending || Boolean(attempt);

  async function submit(event: React.SubmitEvent<HTMLFormElement>) {
    event.preventDefault();
    if (disabled || active.current || !valid || !action) return;
    const body: DecisionRequest = retained.current ?? { action, reason: reason.trim(), override_answer: action === 'override' ? replacement : null, idempotency_key: crypto.randomUUID() };
    retained.current = body; setAttempt(body); rememberDecision(appeal.id, body);
    const controller = new AbortController(); active.current = controller;
    setPending(true); setError(null);
    try {
      const response = await api.decideAppeal(appeal.id, body, controller.signal);
      if (controller.signal.aborted) return;
      validateEvaluation(response.evaluation, { conversation_version_id: evaluation.conversation_version_id,
        checklist_version_id: evaluation.checklist_version_id, mode: evaluation.output!.metadata.mode }, evaluation.id);
      const decision = response.decision;
      if (!decision?.id || decision.appeal_id !== appeal.id || decision.idempotency_key !== body.idempotency_key
        || decision.action !== body.action || decision.reason !== body.reason
        || (body.override_answer ? !decision.override_answer || !sameAnswer(body.override_answer, decision.override_answer)
          || (body.override_answer.type === 'na' && decision.override_answer.type === 'na' && body.override_answer.reason !== decision.override_answer.reason)
          : decision.override_answer != null)) throw new Error('Сервер не подтвердил отправленное решение.');
      rememberDecision(appeal.id, null);
      onSaved();
    } catch (failure) {
      if (!controller.signal.aborted) {
        setError(failure);
        if (failure instanceof ApiError && failure.status === 409) onConflict();
      }
    } finally { if (!controller.signal.aborted) { active.current = null; setPending(false); } }
  }
  return <form className="appeal-form decision-form" aria-label={`Решение ${criterion.id}`} noValidate onSubmit={event => { void submit(event); }} aria-busy={pending}>
    <label htmlFor={`decision-action-${appeal.id}`}>Действие проверяющего {criterion.id}</label>
    <select id={`decision-action-${appeal.id}`} value={action} disabled={locked} onChange={event => setAction(event.target.value as typeof action)}>
      <option value="">Выберите действие</option><option value="confirm">confirm — подтвердить исходный ответ</option><option value="override">override — изменить ответ</option>
    </select>
    {action === 'override' && <>
      <label htmlFor={`decision-answer-${appeal.id}`}>Новый ответ {criterion.id}</label>
      <select id={`decision-answer-${appeal.id}`} value={optionIndex} disabled={locked} onChange={event => setOptionIndex(event.target.value)}>
        <option value="">Выберите ответ</option>
        {criterion.options.map((option, index) => <option key={index} value={index} disabled={original.type !== 'na' && original.value === option.value}>{option.label} ({String(option.value)})</option>)}
        {criterion.allow_na && <option value="na" disabled={original.type === 'na'}>N/A — не применимо</option>}
      </select>
      {optionIndex === 'na' && <><p>{criterion.na_condition}</p><label htmlFor={`decision-na-${appeal.id}`}>Обоснование N/A {criterion.id}</label>
        <textarea id={`decision-na-${appeal.id}`} value={naReason} disabled={locked} required onChange={event => setNaReason(event.target.value)} /></>}
      <p className="muted">Варианты из оценённой версии: {evaluation.checklist_version_id}.</p>
    </>}
    <label htmlFor={`decision-reason-${appeal.id}`}>Причина решения {criterion.id}</label>
    <p className="muted" id={`decision-help-${appeal.id}`}>Обязательная причина будет сохранена вместе с решением. Исходный ответ останется в истории.</p>
    <textarea aria-describedby={`decision-help-${appeal.id}`} id={`decision-reason-${appeal.id}`} rows={3} required value={reason} disabled={locked} onChange={event => setReason(event.target.value)} />
    {attempt && !pending && <p className="muted">Повтор использует прежний idempotency_key и те же параметры решения.</p>}
    {error != null && <ApiFailure error={error} />}
    <button className="primary-button" disabled={disabled || pending || !valid}>{pending ? 'Сохраняем решение…' : attempt ? 'Повторить решение' : 'Сохранить решение'}</button>
    {definiteRejection(error) && <button type="button" className="secondary-button" disabled={disabled || pending} onClick={() => {
      retained.current = null; setAttempt(null); rememberDecision(appeal.id, null); setError(null);
    }}>Изменить решение</button>}
  </form>;
}
