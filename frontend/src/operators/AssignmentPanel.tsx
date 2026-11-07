import { useCallback, useEffect, useRef, useState } from 'react';
import { api, type ConversationVersion, type OperatorAssignment, type OperatorAssignmentInput } from '../api/client';
import { ApiFailure } from '../ApiFailure';
import { useOperatorResource } from './useOperatorResource';

export function AssignmentPanel({ conversation }: { conversation: ConversationVersion }) {
  const [open, setOpen] = useState(false);
  return <div className="operator-assignment">
    <button type="button" className="secondary-button" aria-expanded={open} onClick={() => setOpen(!open)}>Данные оператора</button>
    {open && <AssignmentLoader versionId={conversation.id} isCall={conversation.channel === 'voice call'} />}
  </div>;
}

function AssignmentLoader({ versionId, isCall }: { versionId: string; isCall: boolean }) {
  const read = useCallback((signal: AbortSignal) => api.getOperatorAssignment(versionId, signal), [versionId]);
  const { state, retry } = useOperatorResource(read);
  if (state.status === 'loading') return <p role="status">Загружаем данные оператора…</p>;
  if (state.status === 'error') return <ApiFailure error={state.error} retry={retry} />;
  return <AssignmentForm initial={state.data} versionId={versionId} isCall={isCall} />;
}

function AssignmentForm({ initial, versionId, isCall }: { initial: OperatorAssignment; versionId: string; isCall: boolean }) {
  const [alias, setAlias] = useState(initial.operator_id ?? '');
  const [outcome, setOutcome] = useState<NonNullable<OperatorAssignmentInput['outcome']> | ''>(initial.outcome ?? '');
  const [duration, setDuration] = useState(initial.duration_seconds?.toString() ?? '');
  const [pending, setPending] = useState(false);
  const [notice, setNotice] = useState('');
  const [error, setError] = useState<unknown>(null);
  const active = useRef<AbortController | null>(null);
  useEffect(() => () => active.current?.abort(), []);
  const trimmed = alias.trim();
  const invalidAlias = Boolean(trimmed) && (!/^[\p{L}\p{N}][\p{L}\p{M}\p{N}._-]*$/u.test(trimmed) || Array.from(trimmed).length > 80);
  const invalidDuration = isCall && Boolean(duration) && (!/^\d+$/.test(duration) || Number(duration) > 2_147_483_647);
  function changed() { setNotice(''); setError(null); }
  async function save(event: React.SubmitEvent<HTMLFormElement>) {
    event.preventDefault();
    if (active.current || invalidAlias || invalidDuration) return;
    const controller = new AbortController();
    active.current = controller;
    setPending(true); changed();
    try {
      const body: OperatorAssignmentInput = {
        operator_id: trimmed || null, outcome: isCall ? outcome || null : null,
        duration_seconds: isCall && duration !== '' ? Number(duration) : null,
      };
      const saved = await api.saveOperatorAssignment(versionId, body, controller.signal);
      if (controller.signal.aborted) return;
      setAlias(saved.operator_id ?? ''); setOutcome(saved.outcome ?? '');
      setDuration(saved.duration_seconds?.toString() ?? ''); setNotice('Данные оператора сохранены.');
    } catch (failure) {
      if (!controller.signal.aborted) setError(failure);
    } finally {
      if (!controller.signal.aborted) { active.current = null; setPending(false); }
    }
  }
  return <form className="operator-form" aria-label="Назначение оператора" onSubmit={event => { void save(event); }} aria-busy={pending}>
    <p className="muted">Сведения относятся к диалогу и сохраняются при переимпорте. Пустой псевдоним снимает назначение.</p>
    <fieldset disabled={pending}><legend className="sr-only">Данные выбранного диалога</legend>
      <label>Псевдоним оператора<input autoComplete="off" value={alias} placeholder="Например, agent-01"
        aria-invalid={invalidAlias || undefined} aria-describedby={invalidAlias ? 'operator-alias-error' : undefined}
        onChange={event => { setAlias(event.target.value); changed(); }} /></label>
      {invalidAlias && <p id="operator-alias-error" role="alert">Введите до 80 символов. Начните с буквы или цифры. Далее допустимы буквы, цифры, точка, дефис и подчёркивание.</p>}
      <label>Исход звонка<select value={outcome} disabled={!isCall} onChange={event => {
        setOutcome(event.target.value as typeof outcome); changed();
      }}><option value="">Нет данных</option><option value="successful">Успешный</option><option value="unsuccessful">Неуспешный</option></select></label>
      <label>Длительность звонка, секунды<input value={duration} inputMode="numeric" disabled={!isCall} placeholder="Нет данных"
        aria-invalid={invalidDuration || undefined} aria-describedby={invalidDuration ? 'operator-duration-error' : undefined}
        onChange={event => { setDuration(event.target.value); changed(); }} /></label>
      {invalidDuration && <p id="operator-duration-error" role="alert">Укажите целое число секунд от 0 до 2 147 483 647 или оставьте поле пустым.</p>}
      {!isCall && <p className="muted">Исход и длительность указывают только для звонков.</p>}
      <button className="primary-button" disabled={invalidAlias || invalidDuration}>{pending ? 'Сохраняем…' : 'Сохранить данные оператора'}</button>
    </fieldset>
    {error != null && <ApiFailure error={error} />}
    {notice && <p role="status">{notice}</p>}
  </form>;
}
