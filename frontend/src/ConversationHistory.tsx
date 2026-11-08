import { useEffect, useState } from 'react';
import { api, ApiError, type EvaluationHistoryItem, type EvaluationHistoryList, type VersionHistoryList } from './api/client';
import { evaluationStatusLabels } from './api/evaluation';
import { formatDateTime } from './format';
import { ApiFailure } from './ApiFailure';

type HistoryPage = VersionHistoryList | EvaluationHistoryList;
type Resource = { status: 'loading' } | { status: 'error'; error: unknown } | { status: 'ready'; data: HistoryPage };
const pageSize = 5;

export function ConversationHistory({ anchorId, currentVersionId, onVersion, onEvaluation, initiallyOpen = false }: {
  initiallyOpen?: boolean; anchorId: string; currentVersionId: string; onVersion: (id: string) => void; onEvaluation: (item: EvaluationHistoryItem) => void;
}) {
  const [open, setOpen] = useState(initiallyOpen);
  return <details open={open} className="panel conversation-history" onToggle={event => setOpen(event.currentTarget.open)}>
    <summary>История диалога</summary>
    {open && <div className="history-content">
      <p className="muted">Версии и оценки этого диалога, включая предыдущие версии. Новые записи показаны первыми.</p>
      <HistoryCollection key={anchorId + ':versions'} kind="versions" anchorId={anchorId} currentVersionId={currentVersionId} onVersion={onVersion} onEvaluation={onEvaluation} />
      <HistoryCollection key={anchorId + ':evaluations'} kind="evaluations" anchorId={anchorId} currentVersionId={currentVersionId} onVersion={onVersion} onEvaluation={onEvaluation} />
    </div>}
  </details>;
}

function HistoryCollection({ kind, anchorId, currentVersionId, onVersion, onEvaluation }: {
  kind: 'versions' | 'evaluations'; anchorId: string; currentVersionId: string;
  onVersion: (id: string) => void; onEvaluation: (item: EvaluationHistoryItem) => void;
}) {
  const title = kind === 'versions' ? 'Версии диалога' : 'Оценки диалога';
  const [state, setState] = useState<Resource>({ status: 'loading' });
  const [offset, setOffset] = useState(0);
  const [revision, setRevision] = useState(0);
  function reload() { setState({ status: 'loading' }); setRevision(value => value + 1); }
  function goTo(next: number) { setState({ status: 'loading' }); setOffset(next); }
  useEffect(() => {
    const controller = new AbortController();
    const load = kind === 'versions' ? api.listVersionHistory : api.listEvaluationHistory;
    load(anchorId, { offset, limit: pageSize }, controller.signal).then(data => {
      if (controller.signal.aborted) return;
      if (!Array.isArray(data.items) || !Number.isInteger(data.total) || data.total < 0
        || data.offset !== offset || data.limit !== pageSize || data.items.length > data.limit
        || data.items.some(item => !item.id || (kind === 'versions' ? !('imported_at' in item)
          : !('mode' in item) || !['mock', 'live'].includes(item.mode) || !['queued', 'running', 'succeeded', 'failed'].includes(item.status)))) {
        throw new ApiError('Сервер вернул некорректную страницу истории.', 200, 'INVALID_RESPONSE');
      }
      setState({ status: 'ready', data });
    }).catch((error: unknown) => {
      if (!controller.signal.aborted) setState({ status: 'error', error });
    });
    return () => controller.abort();
  }, [anchorId, kind, offset, revision]);

  return <section className="history-collection" aria-label={title} aria-busy={state.status === 'loading'}>
    <div className="history-heading"><h3>{title}</h3><button className="secondary-button" disabled={state.status === 'loading'} onClick={reload} aria-label={'Обновить: ' + title}>Обновить</button></div>
    {state.status === 'loading' && <p role="status">Загружаем историю…</p>}
    {state.status === 'error' && <ApiFailure error={state.error} retry={reload} />}
    {state.status === 'ready' && <>
      <p className="muted">Всего: {state.data.total}</p>
      {state.data.items.length === 0 && <p>{state.data.total === 0
        ? kind === 'versions' ? 'История версий пуста.' : 'Оценок пока нет. Запустите оценку из карточки диалога.'
        : 'На этой странице записей нет. Вернитесь к началу истории.'}</p>}
      <ul className="history-items">{state.data.items.map(item => <li key={item.id}>
        {'imported_at' in item ? <button className="history-item" aria-pressed={item.id === currentVersionId} onClick={() => onVersion(item.id)}>
          <span className="history-item-heading"><strong>{item.id === anchorId ? 'Последняя версия' : 'Историческая версия'}</strong>
            {item.id === currentVersionId && <span className="tag tag--green">Открыта</span>}</span>
          <span>Импорт: <time dateTime={item.imported_at}>{formatDateTime(item.imported_at)}</time></span>
          <span className="version-label">{item.id}</span>
        </button> : <button className="history-item" onClick={() => onEvaluation(item)}>
          <span className="history-item-heading"><strong>Открыть оценку</strong><span className={'tag evaluation-status--' + item.status}>{evaluationStatusLabels[item.status]} · {item.status}</span></span>
          <span>Создана: <time dateTime={item.created_at}>{formatDateTime(item.created_at)}</time> · {item.mode}</span>
          <span className="version-label">Оценка: {item.id}</span>
          <span className="version-label">Диалог: {item.conversation_version_id} · {item.conversation_version_id === anchorId ? 'последняя версия' : 'историческая версия'}</span>
          <span className="version-label">Чек-лист: {item.checklist_version_id}</span>
        </button>}
      </li>)}</ul>
    </>}
    <nav className="pagination history-pagination" aria-label={'Страницы: ' + title}>
      <button className="secondary-button" disabled={offset === 0 || state.status === 'loading'} onClick={() => goTo(Math.max(0, offset - pageSize))}>Назад</button>
      <span>Страница {Math.floor(offset / pageSize) + 1}</span>
      <button className="secondary-button" disabled={state.status !== 'ready' || offset + pageSize >= state.data.total} onClick={() => goTo(offset + pageSize)}>Далее</button>
      {offset > 0 && <button className="text-link history-first" disabled={state.status === 'loading'} onClick={() => goTo(0)}>К началу истории</button>}
    </nav>
  </section>;
}
