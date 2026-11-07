import type { Appeal, DecisionRequest, DecisionResponse, Evaluation } from '../src/api/client';
import { evaluation, historicalChecklist, historicalConversation } from './evaluationFixtures';

// Explicit synthetic values: no local scoring, real data or provider calls.
export function originalEvaluation(): Evaluation { const value = evaluation(); return { ...value, effective_score: value.original_score }; }
export const originalResult = () => ({ evaluation: originalEvaluation(), conversation: historicalConversation, checklist: historicalChecklist });
export function openAppeal(criterionId = 'C01'): Appeal {
  return { id: `appeal-${criterionId}`, evaluation_id: originalEvaluation().id, criterion_id: criterionId,
    comment: `Синтетическая апелляция ${criterionId}`, status: 'open', created_at: '2026-11-08T00:00:00Z', decision: null };
}
export function decisionResponse(appeal: Appeal, body: DecisionRequest, current = originalEvaluation()): DecisionResponse {
  return { decision: { ...body, id: `decision-${appeal.id}`, appeal_id: appeal.id, created_at: '2026-11-08T00:01:00Z', actor_label: 'Тестовый проверяющий' },
    evaluation: body.action === 'override' ? { ...current, effective_score: { ...current.original_score!, total: '70.00' } } : current };
}
