import { useEffect, useState } from 'react';
import { api, type Evaluation } from './api/client';
import { appealsHref } from './api/appeals';
import { loadEvaluationResult, type EvaluationResult } from './api/evaluation';
import { ApiFailure } from './ApiFailure';
import { AppealWorkspace } from './AppealWorkspace';

export function AppealsScreen({ evaluationId }: { evaluationId: string }) {
  const [input, setInput] = useState(evaluationId);
  const [retry, setRetry] = useState(0);
  return <>
    <form className="panel appeal-lookup" onSubmit={event => { event.preventDefault(); if (input.trim()) { location.hash = appealsHref(input.trim()); setRetry(value => value + 1); } }}>
      <label htmlFor="appeal-evaluation-id">ID оценки</label>
      <input id="appeal-evaluation-id" value={input} onChange={event => setInput(event.target.value)} />
      <button className="primary-button" disabled={!input.trim()}>Открыть апелляции</button>
      <p className="muted">Откройте оценку по ID или перейдите сюда из её результата.</p>
    </form>
    {evaluationId ? <AppealEvaluation key={`${evaluationId}:${retry}`} id={evaluationId} retry={() => setRetry(value => value + 1)} />
      : <section className="panel state-panel"><h2>Выберите оценку для рассмотрения</h2><p>Обращения и решения загружаются из серверного хранилища.</p></section>}
  </>;
}

function AppealEvaluation({ id, retry }: { id: string; retry: () => void }) {
  const [state, setState] = useState<{ evaluation?: Evaluation; result?: EvaluationResult; error?: unknown }>({});
  useEffect(() => {
    const controller = new AbortController();
    async function load() {
      try {
        const evaluation = await api.getEvaluation(id, controller.signal);
        if (evaluation.id !== id || !['succeeded', 'failed', 'queued', 'running'].includes(evaluation.status)) throw new Error('Сервер вернул другую оценку или некорректный статус.');
        const result = evaluation.status === 'succeeded' ? await loadEvaluationResult(evaluation, controller.signal) : undefined;
        if (!controller.signal.aborted) setState({ evaluation, result });
      } catch (error) { if (!controller.signal.aborted) setState({ error }); }
    }
    void load(); return () => controller.abort();
  }, [id]);
  if (state.error) return <ApiFailure error={state.error} retry={retry} />;
  if (state.result) return <>{state.evaluation?.output?.metadata.mode === 'mock' && <p className="synthetic-note">Тестовые данные — не AI-оценка</p>}<AppealWorkspace initial={state.result} reviewer /></>;
  if (state.evaluation) return <div className="panel panel-notice" role={state.evaluation.status === 'failed' ? 'alert' : 'status'}>
    <h2>Апелляции доступны после успешной оценки</h2><p>Статус: {state.evaluation.status}</p>
    {state.evaluation.failure && <p>{state.evaluation.failure.code}: {state.evaluation.failure.message}</p>}
    <button className="secondary-button" onClick={retry}>Повторить загрузку</button>
  </div>;
  return <p role="status">Загружаем оценку и исторические версии…</p>;
}
