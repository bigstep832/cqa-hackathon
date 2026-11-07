import type { components, paths } from './generated/schema';

export type ConversationVersion = components['schemas']['ConversationVersion'];
export type ConversationList = components['schemas']['ConversationList'];
export type ImportRequest = components['schemas']['ConversationImportRequest'];
export type ImportResponse = components['schemas']['ImportResponse'];
export type ConversationQuery = NonNullable<paths['/api/v1/conversations']['get']['parameters']['query']>;
export type ChecklistList = components['schemas']['ChecklistList'];
export type ChecklistVersion = components['schemas']['ChecklistVersion'];
export type Evaluation = components['schemas']['Evaluation'];
export type EvaluationRequest = components['schemas']['EvaluationCreateRequest'];
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
    listChecklists(signal?: AbortSignal) {
      return request<ChecklistList>('/api/v1/checklists', { signal });
    },
    getChecklistVersion(id: string, signal?: AbortSignal) {
      return request<ChecklistVersion>(`/api/v1/checklist-versions/${encodeURIComponent(id)}`, { signal });
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
