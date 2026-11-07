import type { EvaluationHistoryItem, EvaluationHistoryList, VersionHistoryList } from '../src/api/client';
import { originalResult } from './appealFixtures';

export const historyResult = originalResult();
export const oldVersion = historyResult.conversation;
export const newVersion = { ...oldVersion, id: 'history-conversation-v2', imported_at: '2026-11-08T01:00:00Z',
  messages: oldVersion.messages.map((message, index) => index === 2 ? { ...message, text: 'Синтетический изменённый текст v2 — не доказательство v1.' } : message) };
export const historyEvaluation: EvaluationHistoryItem = { id: historyResult.evaluation.id, conversation_version_id: oldVersion.id,
  checklist_version_id: historyResult.checklist.id, mode: 'live', status: 'succeeded', created_at: historyResult.evaluation.created_at };
export const versionItems: VersionHistoryList['items'] = [{ id: newVersion.id, imported_at: newVersion.imported_at },
  { id: oldVersion.id, imported_at: oldVersion.imported_at }];
export const evaluationItems: EvaluationHistoryList['items'] = [historyEvaluation];
export const historyConversationList = { items: [{ conversation_id: newVersion.conversation_id, latest_version_id: newVersion.id,
  language: newVersion.language, channel: newVersion.channel, message_count: newVersion.messages.length, has_audio: false }], offset: 0, limit: 25, total: 1 };
