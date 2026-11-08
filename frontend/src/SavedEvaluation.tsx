import { useMemo } from 'react';
import type { EvaluationHistoryItem } from './api/client';
import { EvaluationMonitor } from './EvaluationMonitor';

// History is a read-only entry point. It neither restores nor creates a submission attempt.
export function SavedEvaluation({ item }: { item: EvaluationHistoryItem }) {
  const reference = useMemo(() => ({
    evaluationId: item.id,
    body: { conversation_version_id: item.conversation_version_id, checklist_version_id: item.checklist_version_id, mode: item.mode },
  }), [item]);
  return <section aria-labelledby="saved-evaluation-heading">
    <h2 id="saved-evaluation-heading">Сохранённая оценка</h2>
    <EvaluationMonitor attempt={reference} />
  </section>;
}
