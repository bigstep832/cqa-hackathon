import { useEffect, useRef, useState } from 'react';
import { api, ApiError, type ChecklistList, type Evaluation, type EvaluationRequest } from './api/client';
import { fetchDemo, type DemoPayload } from './api/demo';
import { rememberAttempt, restoreAttempt, validateEvaluation, type EvaluationAttempt } from './api/evaluation';
import { ApiFailure } from './ApiFailure';
import { EvaluationMonitor } from './EvaluationMonitor';

type Resource<T> = { status: 'loading' } | { status: 'error'; error: unknown } | { status: 'ready'; data: T };

export function EvaluationWorkspace({ conversationVersionId }: { conversationVersionId: string }) {
  const [attempt, setAttempt] = useState<EvaluationAttempt | null>(() => restoreAttempt(conversationVersionId));
  const [initial, setInitial] = useState<Evaluation>();
  const [generation, setGeneration] = useState(0);
  function save(next: EvaluationAttempt | null) { rememberAttempt(conversationVersionId, next); setAttempt(next); }
  return <section aria-labelledby="evaluation-heading">
    <h2 id="evaluation-heading">Оценка диалога</h2>
    {attempt?.evaluationId ? <EvaluationMonitor attempt={attempt as EvaluationAttempt & { evaluationId: string }} initial={initial} onNew={() => {
      save(null); setInitial(undefined); setGeneration(value => value + 1);
    }} /> : <EvaluationForm key={generation} conversationVersionId={conversationVersionId} attempt={attempt} save={save}
      onCreated={(created, pending) => { setInitial(created); save({ ...pending, evaluationId: created.id }); }} />}
  </section>;
}

function EvaluationForm({ conversationVersionId, attempt, save, onCreated }: {
  conversationVersionId: string; attempt: EvaluationAttempt | null; save: (attempt: EvaluationAttempt | null) => void;
  onCreated: (evaluation: Evaluation, attempt: EvaluationAttempt) => void;
}) {
  const [checklists, setChecklists] = useState<Resource<ChecklistList>>({ status: 'loading' });
  const [listRetry, setListRetry] = useState(0);
  const [mode, setMode] = useState<EvaluationRequest['mode'] | ''>(attempt?.body.mode ?? '');
  const [checklistId, setChecklistId] = useState(attempt?.body.checklist_version_id ?? '');
  const [mock, setMock] = useState<Resource<DemoPayload> | null>(null);
  const [mockRetry, setMockRetry] = useState(0);
  const [pending, setPending] = useState(false);
  const [error, setError] = useState<unknown>(null);
  const active = useRef<AbortController | null>(null);
  const retained = useRef(attempt);

  useEffect(() => {
    const controller = new AbortController();
    api.listChecklists(controller.signal).then(data => {
      if (!controller.signal.aborted) setChecklists({ status: 'ready', data });
    }).catch((failure: unknown) => {
      if (!controller.signal.aborted) setChecklists({ status: 'error', error: failure });
    });
    return () => controller.abort();
  }, [listRetry]);
  useEffect(() => {
    if (mode !== 'mock') return;
    const controller = new AbortController();
    fetchDemo(controller.signal).then(data => {
      if (!data) throw new Error('Сервер не вернул поддерживаемый mock-пример.');
      if (!controller.signal.aborted) setMock({ status: 'ready', data });
    }).catch((failure: unknown) => {
      if (!controller.signal.aborted) setMock({ status: 'error', error: failure });
    });
    return () => controller.abort();
  }, [mode, mockRetry]);
  useEffect(() => () => active.current?.abort(), []);

  const mockAllowed = mode === 'mock' && mock?.status === 'ready'
    && mock.data.conversation.id === conversationVersionId && mock.data.checklist.id === checklistId;
  const options = checklists.status === 'ready' ? checklists.data.items : [];
  const knownChecklist = options.some(item => item.latest_version_id === checklistId)
    || (mode === 'mock' && mock?.status === 'ready' && mock.data.checklist.id === checklistId);
  const canSubmit = Boolean(mode && checklistId && (attempt || knownChecklist) && (mode === 'live' || mockAllowed));

  async function submit(event: React.SubmitEvent<HTMLFormElement>) {
    event.preventDefault();
    if (active.current || !canSubmit || !mode) return;
    const controller = new AbortController();
    active.current = controller;
    setPending(true); setError(null);
    try {
      const next = retained.current ?? { key: crypto.randomUUID(), body: {
        conversation_version_id: conversationVersionId, checklist_version_id: checklistId, mode,
      } };
      retained.current = next;
      save(next);
      const created = await api.createEvaluation(next.body, next.key, controller.signal);
      if (controller.signal.aborted) return;
      validateEvaluation(created, next.body);
      onCreated(created, next);
    } catch (failure) {
      if (!controller.signal.aborted) setError(failure);
    } finally {
      if (!controller.signal.aborted) { active.current = null; setPending(false); }
    }
  }

  // Only a definite request rejection allows changing the body. A lost response
  // must be retried with its original key, even after navigating away/reloading.
  const rejected = error instanceof ApiError && [400, 403, 404, 409, 413, 422].includes(error.status);
  return <form className="panel evaluation-form" onSubmit={event => { void submit(event); }} aria-busy={pending}>
    <p className="version-label">Версия диалога: {conversationVersionId}</p>
    {checklists.status === 'loading' && <p role="status">Загружаем чек-листы…</p>}
    {checklists.status === 'error' && <ApiFailure error={checklists.error} retry={() => {
      setChecklists({ status: 'loading' }); setListRetry(value => value + 1);
    }} />}
    {checklists.status === 'ready' && options.length === 0 && <p>Нет доступных чек-листов.</p>}
    <label htmlFor="evaluation-checklist">Версия чек-листа</label>
    <select id="evaluation-checklist" value={checklistId} disabled={pending || Boolean(attempt)} onChange={event => setChecklistId(event.target.value)}>
      <option value="">Выберите версию</option>
      {options.map(item => <option key={item.latest_version_id} value={item.latest_version_id}>{item.name} · v{item.version} · {item.latest_version_id}</option>)}
      {mode === 'mock' && mock?.status === 'ready' && !options.some(item => item.latest_version_id === mock.data.checklist.id)
        && <option value={mock.data.checklist.id}>{mock.data.checklist.name} · v{mock.data.checklist.version} · встроенный mock</option>}
      {attempt && !knownChecklist && <option value={attempt.body.checklist_version_id}>{attempt.body.checklist_version_id} · сохранённый запрос</option>}
    </select>
    <label htmlFor="evaluation-mode">Режим оценки</label>
    <select id="evaluation-mode" value={mode} disabled={pending || Boolean(attempt)} onChange={event => {
      setMode(event.target.value as EvaluationRequest['mode'] | ''); setMock(null);
    }}>
      <option value="">Выберите режим</option><option value="mock">Mock — встроенный тестовый пример</option><option value="live">Live — настроенный оценщик</option>
    </select>
    {mode === 'live' && <p className="muted">Используется настроенный на сервере оценщик. Сервер проверит разрешение на запуск для выбранных данных.</p>}
    {mode === 'mock' && <>
      <p className="synthetic-note">Тестовые данные — не AI-оценка</p>
      {(!mock || mock.status === 'loading') && <p role="status">Проверяем поддержку встроенного mock…</p>}
      {mock?.status === 'error' && <ApiFailure error={mock.error} retry={() => { setMock({ status: 'loading' }); setMockRetry(value => value + 1); }} />}
      {mock?.status === 'ready' && !mockAllowed && <p role="note">Mock доступен только для встроенной пары: диалог {mock.data.conversation.id}, чек-лист {mock.data.checklist.id}. Ручной импорт не делает диалог поддерживаемым mock-примером.</p>}
    </>}
    {attempt && !pending && <p>Повтор будет отправлен с прежними параметрами и ключом, чтобы не создать второе задание.</p>}
    {error != null && <ApiFailure error={error} />}
    <button type="submit" className="primary-button" disabled={pending || !canSubmit}>{pending ? 'Отправляем…' : attempt ? 'Повторить отправку' : 'Запустить оценку'}</button>
    {rejected && <button type="button" className="secondary-button" onClick={() => {
      retained.current = null; save(null); setError(null);
    }}>Изменить параметры</button>}
  </form>;
}
