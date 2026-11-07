import { useEffect, useState } from 'react';
import { api, type Appeal } from './api/client';
import { answerLabel, appealsHref, rememberDecision, validateAppeal } from './api/appeals';
import { loadEvaluationResult, validateEvaluation, type EvaluationResult } from './api/evaluation';
import { ReviewWorkspace } from './ReviewWorkspace';
import { AppealRequestForm, DecisionForm } from './AppealForms';
import { ApiFailure } from './ApiFailure';

export function AppealWorkspace({ initial, reviewer = false }: { initial: EvaluationResult; reviewer?: boolean }) {
  const [data, setData] = useState(initial);
  const [appeals, setAppeals] = useState<Appeal[]>([]);
  const [ready, setReady] = useState(false);
  const [error, setError] = useState<unknown>(null);
  const [revision, setRevision] = useState(0);
  const [selected, setSelected] = useState<string | null>(null);
  const [notice, setNotice] = useState('');
  function refresh(message = '') { setNotice(message); setReady(false); setError(null); setRevision(value => value + 1); }

  useEffect(() => {
    const controller = new AbortController();
    async function load() {
      try {
        const [evaluation, list] = await Promise.all([
          revision === 0 ? Promise.resolve(initial.evaluation) : api.getEvaluation(initial.evaluation.id, controller.signal),
          api.listAppeals(initial.evaluation.id, controller.signal),
        ]);
        validateEvaluation(evaluation, { conversation_version_id: initial.conversation.id, checklist_version_id: initial.checklist.id,
          mode: initial.evaluation.output!.metadata.mode }, initial.evaluation.id);
        if (evaluation.status !== 'succeeded' || !Array.isArray(list.items)) throw new Error('Сервер вернул неполные данные апелляций.');
        for (const appeal of list.items) validateAppeal(appeal, evaluation, initial.checklist);
        if (new Set(list.items.map(item => item.criterion_id)).size !== list.items.length) throw new Error('Сервер вернул повторные апелляции по одному критерию.');
        const next = revision === 0 ? initial : await loadEvaluationResult(evaluation, controller.signal);
        if (controller.signal.aborted) return;
        list.items.filter(item => item.status === 'resolved').forEach(item => rememberDecision(item.id, null));
        setData(next); setAppeals(list.items); setReady(true);
      } catch (failure) { if (!controller.signal.aborted) setError(failure); }
    }
    void load();
    return () => controller.abort();
  }, [initial, revision]);

  return <>
    <section className="panel appeals-toolbar" aria-label="Апелляции оценки">
      <h2>Апелляции оценки</h2><p className="version-label">Оценка: {initial.evaluation.id}</p>
      <p className="version-label">Диалог: {initial.conversation.id} · Чек-лист: {initial.checklist.id}</p>
      {!reviewer && <a className="text-link" href={appealsHref(initial.evaluation.id)}>Рассмотреть апелляции</a>}
      {notice && <p role="status">{notice}</p>}
      {!ready && !error && <p role="status">Загружаем апелляции и актуальный результат…</p>}
      {!ready && revision > 0 && <p className="muted">Ниже показан последний загруженный результат. Обновление ещё не подтверждено.</p>}
      {error != null && <ApiFailure error={error} retry={() => refresh(notice)} />}
      {ready && <><p>{appeals.length === 0 ? 'Апелляций пока нет.' : `Открыто: ${appeals.filter(item => item.status === 'open').length}. Решено: ${appeals.filter(item => item.status === 'resolved').length}.`}</p>
        <button className="secondary-button" onClick={() => refresh()}>Обновить результат и апелляции</button></>}
    </section>
    <ReviewWorkspace data={data} renderAppeal={criterionId => {
      const criterion = data.checklist.criteria.find(item => item.id === criterionId)!;
      const original = data.evaluation.output!.answers.find(item => item.criterion_id === criterionId)!.answer;
      const appeal = appeals.find(item => item.criterion_id === criterionId);
      return <div className="criterion-appeal" aria-label={`Обращение ${criterionId}`}>
        <p><strong>Исходный ответ:</strong> {answerLabel(original, criterion)}</p>
        {appeal ? <>
          <p><strong>Апелляция:</strong> <span className={`appeal-status appeal-status--${appeal.status}`}>{appeal.status === 'open' ? 'Открыта · open' : 'Решена · resolved'}</span></p>
          <p className="preserve-lines">Комментарий: {appeal.comment}</p>
          {appeal.decision ? <div className="review-decision">
            <p><strong>Решение проверяющего:</strong> {appeal.decision.action === 'confirm' ? 'confirm — исходный ответ подтверждён' : 'override — ответ изменён'}</p>
            {appeal.decision.override_answer && <p>Ответ проверяющего: {answerLabel(appeal.decision.override_answer, criterion)}</p>}
            <p className="preserve-lines">Причина: {appeal.decision.reason}</p>
            <p className="muted">{appeal.decision.actor_label} · {appeal.decision.created_at}</p>
          </div> : reviewer && <DecisionForm key={appeal.id} appeal={appeal} criterion={criterion} original={original}
            evaluation={data.evaluation} disabled={!ready} onSaved={() => refresh('Решение принято сервером. Загружаем обновлённые данные.')}
            onConflict={() => refresh('Конфликт HTTP 409: сервер не подтвердил это решение. Загружаем сохранённое состояние.')} />}
        </> : !reviewer && <>
          <button className="secondary-button" disabled={!ready} onClick={() => setSelected(criterionId)}>Подать апелляцию {criterionId}</button>
          {selected === criterionId && <AppealRequestForm key={criterionId} evaluationId={data.evaluation.id} criterionId={criterionId} disabled={!ready}
            onSaved={() => { setSelected(null); refresh('Апелляция сохранена сервером. Обновляем список.'); }}
            onConflict={() => refresh('Конфликт HTTP 409: апелляция уже может существовать. Загружаем сохранённый комментарий.')} />}
        </>}
      </div>;
    }} />
  </>;
}
