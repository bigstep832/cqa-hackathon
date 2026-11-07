import { ApiError } from './api/client';

export function ApiFailure({ error, retry }: { error: unknown; retry?: () => void }) {
  return <div className="request-error" role="alert">
    <p>{error instanceof Error ? error.message : 'Не удалось выполнить запрос.'}</p>
    {error instanceof ApiError && error.details.length > 0 && <ul>{error.details.map((item, index) =>
      <li key={index}>{item.field}: {item.message}</li>)}</ul>}
    {retry && <button type="button" className="primary-button" onClick={retry}>Повторить загрузку</button>}
  </div>;
}
