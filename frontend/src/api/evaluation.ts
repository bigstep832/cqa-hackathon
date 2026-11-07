import { api, type ChecklistVersion, type ConversationVersion, type Evaluation, type EvaluationRequest } from './client';

export const evaluationStatusLabels: Record<Evaluation['status'], string> = {
  queued: 'В очереди', running: 'Оценка выполняется', succeeded: 'Оценка завершена', failed: 'Оценка не выполнена',
};

export type EvaluationResult = { conversation: ConversationVersion; checklist: ChecklistVersion; evaluation: Evaluation };
export type EvaluationAttempt = { body: EvaluationRequest; key: string; evaluationId?: string };

// Only technical IDs, mode and the retry key are stored for the current browser tab.
// No transcripts, answers, provider credentials or scores are stored here.
const storageKey = (conversationId: string) => `cqa:evaluation:${conversationId}`;
export function rememberAttempt(conversationId: string, attempt: EvaluationAttempt | null) {
  try {
    if (attempt) sessionStorage.setItem(storageKey(conversationId), JSON.stringify(attempt));
    else sessionStorage.removeItem(storageKey(conversationId));
  } catch { /* Private browsing can disable storage; the mounted form still retains its key. */ }
}

export function restoreAttempt(conversationId: string): EvaluationAttempt | null {
  try {
    const value = JSON.parse(sessionStorage.getItem(storageKey(conversationId)) ?? 'null') as EvaluationAttempt | null;
    if (value && typeof value.key === 'string' && value.key.length > 0 && value.key.length <= 200
      && value.body?.conversation_version_id === conversationId && typeof value.body.checklist_version_id === 'string'
      && ['mock', 'live'].includes(value.body.mode)
      && (value.evaluationId === undefined || typeof value.evaluationId === 'string')) return value;
  } catch { /* Ignore damaged or unavailable session storage. Never auto-submit it. */ }
  return null;
}

export function validateEvaluation(evaluation: Evaluation, body: EvaluationRequest, expectedId?: string) {
  if (!evaluation.id || (expectedId && evaluation.id !== expectedId)
    || !['queued', 'running', 'succeeded', 'failed'].includes(evaluation.status)
    || evaluation.conversation_version_id !== body.conversation_version_id
    || evaluation.checklist_version_id !== body.checklist_version_id
    || (evaluation.output && evaluation.output.metadata.mode !== body.mode)) {
    throw new Error('Сервер вернул оценку для другого запроса или некорректный статус.');
  }
}

export async function loadEvaluationResult(evaluation: Evaluation, signal: AbortSignal): Promise<EvaluationResult> {
  if (evaluation.status !== 'succeeded' || !evaluation.output || !evaluation.original_score || !evaluation.effective_score) {
    throw new Error('Завершённая оценка не содержит полного результата.');
  }
  const [conversation, checklist] = await Promise.all([
    api.getConversationVersion(evaluation.conversation_version_id, signal),
    api.getChecklistVersion(evaluation.checklist_version_id, signal),
  ]);
  if (conversation.id !== evaluation.conversation_version_id || checklist.id !== evaluation.checklist_version_id) {
    throw new Error('Полученные версии не совпадают со снимками оценки.');
  }
  for (const answer of evaluation.output.answers) {
    for (const evidence of answer.evidence) {
      const start = conversation.messages.findIndex(message => message.message_id === evidence.start_message_id);
      const end = conversation.messages.findIndex(message => message.message_id === evidence.end_message_id);
      if (evidence.conversation_version_id !== conversation.id || start < 0 || end < start) {
        throw new Error('Доказательство не относится к сохранённой версии диалога.');
      }
    }
  }
  return { conversation, checklist, evaluation };
}
