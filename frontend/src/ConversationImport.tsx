import { useEffect, useRef, useState } from 'react';
import { api, type ImportRequest, type ImportResponse } from './api/client';
import { ApiFailure } from './ApiFailure';

const sample: ImportRequest = {
  dataset_group_id: 'MANUAL_SAMPLE',
  records: [{
    conversation_id: 'synthetic-b-001', channel: 'agent chat', language: 'RU', audio_url: null,
    transcript: [
      { timestamp: '00:01', speaker: 'client', text: 'Здравствуйте! Это учебный диалог.' },
      { timestamp: '00:02', speaker: 'operator', text: 'Здравствуйте! Чем могу помочь?' },
    ],
  }],
};

export function ConversationImport({ onImported }: { onImported: (result: ImportResponse) => void }) {
  const [json, setJson] = useState('');
  const [confirmed, setConfirmed] = useState(false);
  const [pending, setPending] = useState(false);
  const [error, setError] = useState<unknown>(null);
  const [result, setResult] = useState<ImportResponse | null>(null);
  const request = useRef<AbortController | null>(null);
  const input = useRef<HTMLTextAreaElement>(null);
  useEffect(() => () => request.current?.abort(), []);

  async function submit(event: React.SubmitEvent<HTMLFormElement>) {
    event.preventDefault();
    if (request.current || !confirmed) return;
    setError(null);
    setResult(null);
    try { JSON.parse(json); } catch {
      setError(new Error('Некорректный JSON. Проверьте кавычки, скобки и запятые.'));
      input.current?.focus();
      return;
    }
    const controller = new AbortController();
    request.current = controller;
    setPending(true);
    try {
      const imported = await api.importConversations(json, controller.signal);
      if (!controller.signal.aborted) {
        setResult(imported);
        onImported(imported);
      }
    } catch (failure) {
      if (!controller.signal.aborted) setError(failure);
    } finally {
      if (!controller.signal.aborted) {
        request.current = null;
        setPending(false);
      }
    }
  }

  return <details className="panel import-panel">
    <summary>Ручной импорт синтетического JSON</summary>
    <form onSubmit={event => { void submit(event); }} aria-busy={pending}>
      <p id="import-help">Для учебных диалогов, созданных командой. Импорт сохраняет текст на сервере и не запускает AI-оценку.</p>
      <button type="button" className="secondary-button" disabled={pending} onClick={() => {
        setJson(JSON.stringify(sample, null, 2)); setError(null); setResult(null);
      }}>Вставить синтетический пример</button>
      <label htmlFor="import-json">JSON для импорта</label>
      <textarea id="import-json" ref={input} value={json} rows={10} required disabled={pending}
        aria-describedby={error ? 'import-help import-error' : 'import-help'} aria-invalid={Boolean(error)}
        spellCheck={false} onChange={event => { setJson(event.target.value); setError(null); setResult(null); }} />
      <label className="checkbox-label"><input type="checkbox" checked={confirmed} disabled={pending}
        onChange={event => setConfirmed(event.target.checked)} />Это синтетические данные, созданные командой</label>
      {error != null && <div id="import-error"><ApiFailure error={error} /></div>}
      <button className="primary-button" disabled={pending || !confirmed || !json.trim()} type="submit">
        {pending ? 'Импортируем…' : error ? 'Повторить импорт' : 'Импортировать'}
      </button>
      {pending && <p role="status">Ожидаем подтверждение импорта от сервера.</p>}
      {result && <p role="status">Группа {result.dataset_group_id}: новых версий — {result.imported_count}, без изменений — {result.unchanged_count}. Фильтры сброшены, список запрошен повторно.</p>}
    </form>
  </details>;
}
