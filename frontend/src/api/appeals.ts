import { ApiError, type Answer, type Appeal, type ChecklistVersion, type DecisionRequest, type Evaluation } from './client';

export type Criterion = ChecklistVersion['criteria'][number];
export const appealsHref = (id: string) => `#appeals/${encodeURIComponent(id)}`;
export function appealRoute() {
  if (!location.hash.startsWith('#appeals')) return null;
  try { return decodeURIComponent(location.hash.slice('#appeals/'.length)); } catch { return ''; }
}

export function answerLabel(answer: Answer, criterion: Criterion) {
  return answer.type === 'na' ? `N/A: ${answer.reason}`
    : `${criterion.options.find(option => option.value === answer.value)?.label ?? answer.value} (${String(answer.value)})`;
}
export function replacementAnswer(criterion: Criterion, optionIndex: string, naReason: string): Answer | null {
  if (optionIndex === 'na') return criterion.allow_na && naReason.trim() ? { type: 'na', reason: naReason.trim() } : null;
  if (!/^\d+$/.test(optionIndex)) return null;
  const option = criterion.options[Number(optionIndex)];
  if (!option) return null;
  if (criterion.answer_type === 'boolean' && typeof option.value === 'boolean') return { type: 'boolean', value: option.value };
  if (criterion.answer_type === 'scale' && typeof option.value === 'number') return { type: 'scale', value: option.value };
  if (criterion.answer_type === 'choice' && typeof option.value === 'string') return { type: 'choice', value: option.value };
  return null;
}
export function sameAnswer(first: Answer, second: Answer) {
  return first.type === second.type && (first.type === 'na' ? second.type === 'na' : second.type !== 'na' && first.value === second.value);
}
export function validateAppeal(appeal: Appeal, evaluation: Evaluation, checklist: ChecklistVersion) {
  if (!appeal.id || appeal.evaluation_id !== evaluation.id || !checklist.criteria.some(item => item.id === appeal.criterion_id)
    || !evaluation.output?.answers.some(item => item.criterion_id === appeal.criterion_id)
    || !['open', 'resolved'].includes(appeal.status) || (appeal.status === 'resolved') !== Boolean(appeal.decision)
    || (appeal.decision && appeal.decision.appeal_id !== appeal.id)) throw new Error('Апелляция не соответствует оценке или её историческому чек-листу.');
}
export const definiteRejection = (error: unknown) => error instanceof ApiError && [400, 404, 409, 422].includes(error.status);
const storageKey = (appealId: string) => `cqa:decision:${appealId}`;
// Retain only the reviewer's pending request in this tab to replay a lost POST.
// Never store transcripts or evaluation snapshots. Clear on acknowledgement.
export function rememberDecision(appealId: string, body: DecisionRequest | null) {
  try {
    if (body) sessionStorage.setItem(storageKey(appealId), JSON.stringify(body));
    else sessionStorage.removeItem(storageKey(appealId));
  } catch { /* An open form retains the same request even if storage is disabled. */ }
}
export function restoreDecision(appealId: string, criterion: Criterion): DecisionRequest | null {
  try {
    const body = JSON.parse(sessionStorage.getItem(storageKey(appealId)) ?? 'null') as DecisionRequest | null;
    if (!body || typeof body.idempotency_key !== 'string' || !body.idempotency_key || typeof body.reason !== 'string' || !body.reason.trim()) return null;
    if (body.action === 'confirm' && body.override_answer == null) return body;
    const answer = body.override_answer;
    if (body.action === 'override' && answer && (answer.type === 'na'
      ? criterion.allow_na && typeof answer.reason === 'string' && answer.reason.trim()
      : answer.type === criterion.answer_type && criterion.options.some(option => option.value === answer.value))) return body;
  } catch { /* Ignore malformed browser state. Never submit automatically. */ }
  return null;
}
