import fixture from '../../fixtures/synthetic/demo.json' with { type: 'json' };
import type { ChecklistVersion, ConversationVersion, Evaluation, EvaluationRequest } from '../src/api/client';
import type { DemoPayload } from '../src/api/demo';

// Explicit synthetic responses shared by unit/browser tests, never production fallbacks.
export const demo = fixture as DemoPayload;
export const historicalConversation: ConversationVersion = { ...demo.conversation, id: 'history-conversation-v1' };
export const historicalChecklist: ChecklistVersion = { ...demo.checklist, id: 'history-checklist-v1', name: 'Исторический чек-лист' };
export const liveRequest: EvaluationRequest = {
  conversation_version_id: historicalConversation.id, checklist_version_id: historicalChecklist.id, mode: 'live',
};
export const checklistList = { items: [{ checklist_id: historicalChecklist.checklist_id,
  latest_version_id: historicalChecklist.id, name: historicalChecklist.name, version: historicalChecklist.version }] };
export const conversationList = { items: [{ conversation_id: historicalConversation.conversation_id,
  latest_version_id: historicalConversation.id, language: 'RU', channel: 'agent chat', message_count: historicalConversation.messages.length, has_audio: false }], total: 1, offset: 0, limit: 25 };

export function evaluation(status: Evaluation['status'] = 'succeeded', body = liveRequest): Evaluation {
  return {
    id: 'synthetic-evaluation-1', created_at: demo.evaluation.created_at, status,
    conversation_version_id: body.conversation_version_id, checklist_version_id: body.checklist_version_id,
    output: status === 'succeeded' ? {
      ...demo.evaluation.output!, metadata: { ...demo.evaluation.output!.metadata, mode: body.mode },
      answers: demo.evaluation.output!.answers.map(answer => ({ ...answer, evidence: answer.evidence.map(ref => ({ ...ref, conversation_version_id: body.conversation_version_id })) })),
    } : null,
    original_score: status === 'succeeded' ? demo.evaluation.original_score : null,
    effective_score: status === 'succeeded' ? { ...demo.evaluation.original_score!, total: '70.00' } : null,
    failure: status === 'failed' ? { code: 'PROVIDER_TIMEOUT', message: 'Истекло время ожидания оценщика.', details: [] } : null,
  };
}
