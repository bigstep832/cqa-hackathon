import type { components, paths } from './generated/schema';

export type VersionHistoryList = components['schemas']['VersionHistoryList'];
export type EvaluationHistoryList = components['schemas']['EvaluationHistoryList'];
export type EvaluationHistoryItem = components['schemas']['EvaluationHistoryItem'];
export type HistoryQuery = NonNullable<paths['/api/v1/conversation-versions/{version_id}/history/versions']['get']['parameters']['query']>;
export type ConversationVersion = components['schemas']['ConversationVersion'];
export type ConversationList = components['schemas']['ConversationList'];
export type ImportRequest = components['schemas']['ConversationImportRequest'];
export type ImportResponse = components['schemas']['ImportResponse'];
export type ConversationQuery = NonNullable<paths['/api/v1/conversations']['get']['parameters']['query']>;
export type ChecklistList = components['schemas']['ChecklistList'];
export type ChecklistVersion = components['schemas']['ChecklistVersion'];
export type ChecklistCreateRequest = components['schemas']['ChecklistCreateRequest'];
export type ChecklistVersionCreateRequest = components['schemas']['ChecklistVersionCreateRequest'];
export type Evaluation = components['schemas']['Evaluation'];
export type EvaluationRequest = components['schemas']['EvaluationCreateRequest'];
export type Appeal = components['schemas']['Appeal'];
export type AppealList = components['schemas']['AppealList'];
export type AppealRequest = components['schemas']['AppealCreateRequest'];
export type DecisionRequest = components['schemas']['DecisionCreateRequest'];
export type DecisionResponse = components['schemas']['DecisionResponse'];
type Json200<T extends { responses: { 200: { content: { 'application/json': unknown } } } }> = T['responses'][200]['content']['application/json'];
export type OperatorList = Json200<paths['/api/v1/operators']['get']>;
export type OperatorProfile = Json200<paths['/api/v1/operators/{operator_id}/profile']['get']>;
type AssignmentPath = paths['/api/v1/conversation-versions/{version_id}/operator'];
export type OperatorAssignment = Json200<AssignmentPath['get']>;
export type OperatorAssignmentInput = AssignmentPath['put']['requestBody']['content']['application/json'];
export type Answer = components['schemas']['CriterionAnswer']['answer'];
type ErrorField = components['schemas']['ErrorField'];
export type Transport = (input: string, init?: RequestInit) => Promise<Response>;

export class ApiError extends Error {
  constructor(
    message: string,
    readonly status: number,
    readonly code: string,
    readonly details: ErrorField[] = [],
  ) {
    super(message);
    this.name = 'ApiError';
  }
}

function object(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value);
}

export function createApiClient(transport: Transport = (url, init) => fetch(url, init)) {
  async function request<T>(url: string, init: RequestInit = {}): Promise<T> {
    let response: Response;
    try {
      response = await transport(url, {
        ...init,
        headers: { Accept: 'application/json', ...init.headers },
      });
    } catch (error) {
      if (init.signal?.aborted) throw error;
      throw new ApiError('Не удалось связаться с сервером. Проверьте подключение и повторите запрос.', 0, 'NETWORK_ERROR');
    }
    let data: unknown;
    try { data = await response.json(); } catch { /* A proxy may return HTML or an empty body. */ }
    if (!response.ok) {
      const error = object(data) && object(data.error) ? data.error : undefined;
      const details = Array.isArray(error?.details) ? error.details.filter((item): item is ErrorField =>
        object(item) && typeof item.field === 'string' && typeof item.message === 'string') : [];
      const message = response.status === 501 ? 'Эта функция ещё не реализована на сервере.'
        : typeof error?.message === 'string' ? error.message : 'Сервер не выполнил запрос.';
      throw new ApiError(`${message} (HTTP ${response.status})`, response.status,
        typeof error?.code === 'string' ? error.code : 'HTTP_ERROR', details);
    }
    if (!object(data)) throw new ApiError('Сервер вернул некорректный JSON-ответ.', response.status, 'INVALID_RESPONSE');
    return data as T;
  }

  return {
    listOperators(signal?: AbortSignal) {
      return request<OperatorList>('/api/v1/operators', { signal });
    },
    operatorProfile(operatorId: string, signal?: AbortSignal) {
      return request<OperatorProfile>(`/api/v1/operators/${encodeURIComponent(operatorId)}/profile`, { signal });
    },
    getOperatorAssignment(versionId: string, signal?: AbortSignal) {
      return request<OperatorAssignment>(`/api/v1/conversation-versions/${encodeURIComponent(versionId)}/operator`, { signal });
    },
    saveOperatorAssignment(versionId: string, body: OperatorAssignmentInput, signal?: AbortSignal) {
      return request<OperatorAssignment>(`/api/v1/conversation-versions/${encodeURIComponent(versionId)}/operator`, {
        method: 'PUT', signal, headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body),
      });
    },
    listConversations(query: ConversationQuery = {}, signal?: AbortSignal) {
      const params = new URLSearchParams();
      for (const [key, value] of Object.entries(query)) {
        if (value !== undefined && value !== null) params.set(key, String(value));
      }
      return request<ConversationList>(`/api/v1/conversations?${params}`, { signal });
    },
    getConversationVersion(id: string, signal?: AbortSignal) {
      return request<ConversationVersion>(`/api/v1/conversation-versions/${encodeURIComponent(id)}`, { signal });
    },
    listVersionHistory(versionId: string, query: HistoryQuery = {}, signal?: AbortSignal) {
      const params = new URLSearchParams();
      for (const [key, value] of Object.entries(query)) if (value != null) params.set(key, String(value));
      return request<VersionHistoryList>(`/api/v1/conversation-versions/${encodeURIComponent(versionId)}/history/versions?${params}`, { signal });
    },
    listEvaluationHistory(versionId: string, query: HistoryQuery = {}, signal?: AbortSignal) {
      const params = new URLSearchParams();
      for (const [key, value] of Object.entries(query)) if (value != null) params.set(key, String(value));
      return request<EvaluationHistoryList>(`/api/v1/conversation-versions/${encodeURIComponent(versionId)}/history/evaluations?${params}`, { signal });
    },
    listChecklists(signal?: AbortSignal) {
      return request<ChecklistList>('/api/v1/checklists', { signal });
    },
    getChecklistVersion(id: string, signal?: AbortSignal) {
      return request<ChecklistVersion>(`/api/v1/checklist-versions/${encodeURIComponent(id)}`, { signal });
    },
    createChecklist(body: ChecklistCreateRequest, signal?: AbortSignal) {
      return request<ChecklistVersion>('/api/v1/checklists', {
        method: 'POST', signal, headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body),
      });
    },
    createChecklistVersion(checklistId: string, body: ChecklistVersionCreateRequest, signal?: AbortSignal) {
      return request<ChecklistVersion>(`/api/v1/checklists/${encodeURIComponent(checklistId)}/versions`, {
        method: 'POST', signal, headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body),
      });
    },
    createEvaluation(body: EvaluationRequest, idempotencyKey: string, signal?: AbortSignal) {
      return request<Evaluation>('/api/v1/evaluations', {
        method: 'POST', signal,
        headers: { 'Content-Type': 'application/json', 'Idempotency-Key': idempotencyKey },
        body: JSON.stringify(body),
      });
    },
    getEvaluation(id: string, signal?: AbortSignal) {
      return request<Evaluation>(`/api/v1/evaluations/${encodeURIComponent(id)}`, { signal });
    },
    listAppeals(evaluationId: string, signal?: AbortSignal) {
      return request<AppealList>(`/api/v1/evaluations/${encodeURIComponent(evaluationId)}/appeals`, { signal });
    },
    createAppeal(evaluationId: string, body: AppealRequest, signal?: AbortSignal) {
      return request<Appeal>(`/api/v1/evaluations/${encodeURIComponent(evaluationId)}/appeals`, {
        method: 'POST', signal, headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body),
      });
    },
    decideAppeal(appealId: string, body: DecisionRequest, signal?: AbortSignal) {
      return request<DecisionResponse>(`/api/v1/appeals/${encodeURIComponent(appealId)}/decision`, {
        method: 'POST', signal, headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body),
      });
    },
    // Raw JSON is preserved so backend validation can detect duplicate properties.
    importConversations(body: ImportRequest | string, signal?: AbortSignal) {
      return request<ImportResponse>('/api/v1/conversations/import', {
        method: 'POST', signal, headers: { 'Content-Type': 'application/json' },
        body: typeof body === 'string' ? body : JSON.stringify(body),
      });
    },
  };
}

export const api = createApiClient();
