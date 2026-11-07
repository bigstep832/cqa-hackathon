import { useEffect, useState } from 'react';
import { api, type Evaluation } from './api/client';
import { loadEvaluationResult, validateEvaluation, type EvaluationAttempt, type EvaluationResult } from './api/evaluation';
import { ApiFailure } from './ApiFailure';
import { ReviewWorkspace } from './ReviewWorkspace';

const labels: Record<Evaluation['status'], string> = {
  queued: 'В очереди', running: 'Оценка выполняется', succeeded: 'Оценка завершена', failed: 'Оценка не выполнена',
};

export function EvaluationMonitor({ attempt, initial, onNew }: {
  attempt: EvaluationAttempt & { evaluationId: string }; initial?: Evaluation; onNew: () => void;
}) {
  const [evaluation, setEvaluation] = useState<Evaluation | null>(initial ?? null);
  const [result, setResult] = useState<EvaluationResult | null>(null);
  const [error, setError] = useState<unknown>(null);
  const [retry, setRetry] = useState(0);

  useEffect(() => {
    const controller = new AbortController();
    let timer: ReturnType<typeof setTimeout> | undefined;
    async function update(current?: Evaluation) {
      try {
        const next = current ?? await api.getEvaluation(attempt.evaluationId, controller.signal);
        if (controller.signal.aborted) return;
        validateEvaluation(next, attempt.body, attempt.evaluationId);
        setEvaluation(next);
        if (next.status === 'succeeded') {
          const snapshot = await loadEvaluationResult(next, controller.signal);
          if (!controller.signal.aborted) setResult(snapshot);
        } else if (next.status !== 'failed') {
          timer = setTimeout(() => { void update(); }, 1000);
        }
      } catch (failure) {
        if (!controller.signal.aborted) setError(failure);
      }
    }
    void update(retry === 0 ? initial : undefined);
    return () => { controller.abort(); clearTimeout(timer); };
  }, [attempt, initial, retry]);

  return <section className="evaluation-run" aria-label="Задание оценки">
    <div className="evaluation-status" role="status">
      <strong>{evaluation ? labels[evaluation.status] : 'Получаем статус оценки…'}</strong>
      {evaluation && <span className={`tag evaluation-status--${evaluation.status}`}>{evaluation.status}</span>}
    </div>
    <p className="version-label">Оценка: {attempt.evaluationId}</p>
    <p className="version-label">Диалог: {attempt.body.conversation_version_id} · Чек-лист: {attempt.body.checklist_version_id}</p>
    {attempt.body.mode === 'mock' && <p className="synthetic-note">Тестовые данные — не AI-оценка</p>}
    {error != null && <><ApiFailure error={error} retry={() => { setError(null); setRetry(value => value + 1); }} />
      <p className="muted">Повтор загружает это же задание и его версии. Новая оценка не создаётся.</p></>}
    {evaluation?.status === 'failed' && <div className="request-error" role="alert">
      <p>{evaluation.failure?.message ?? 'Не удалось получить оценку.'}</p>
      {evaluation.failure?.code && <p>Код: {evaluation.failure.code}</p>}
    </div>}
    {evaluation?.status === 'succeeded' && !result && !error && <p role="status">Загружаем сохранённые версии результата…</p>}
    {result && <ReviewWorkspace key={result.evaluation.id} data={result} />}
    {(evaluation?.status === 'failed' || result) && <button className="secondary-button" onClick={onNew}>Новая оценка</button>}
  </section>;
}
