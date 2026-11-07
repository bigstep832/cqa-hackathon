import { useEffect, useState } from 'react';
import { api, type ConversationList, type ConversationQuery, type ConversationVersion } from './api/client';
import { ApiFailure } from './ApiFailure';
import { ConversationImport } from './ConversationImport';
import { EvaluationWorkspace } from './EvaluationWorkspace';
import { ConversationAudio } from './ConversationAudio';

type Resource<T> = { status: 'loading' } | { status: 'error'; error: unknown } | { status: 'ready'; data: T };
const pageSize = 25;
const speakerLabels: Record<string, string> = { client: 'Клиент', operator: 'Оператор', bot: 'Бот', system: 'Система' };

export function ConversationsScreen() {
  const [query, setQuery] = useState<ConversationQuery>({ offset: 0, limit: pageSize });
  const [revision, setRevision] = useState(0);
  const [selected, setSelected] = useState<string | null>(null);
  const [detailAttempt, setDetailAttempt] = useState(0);
  const [evaluating, setEvaluating] = useState(false);

  function changeQuery(next: ConversationQuery) {
    setQuery(next); setSelected(null); setRevision(value => value + 1);
  }
  const reload = () => { setRevision(value => value + 1); };
  if (evaluating && selected) return <>
    <button className="secondary-button evaluation-back" onClick={() => setEvaluating(false)}>К списку диалогов</button>
    <EvaluationWorkspace key={selected} conversationVersionId={selected} />
  </>;
  return <>
    <ConversationImport onImported={result => {
      setQuery({ offset: 0, limit: pageSize });
      setSelected(result.conversation_version_ids[0] ?? null);
      setDetailAttempt(value => value + 1);
      reload();
    }} />
    <section className="conversation-filters" aria-label="Фильтры диалогов">
      <label><span id="language-label">Язык</span><select aria-labelledby="language-label" value={query.language ?? ''} onChange={event => changeQuery({ ...query, offset: 0,
        language: (event.target.value || undefined) as ConversationQuery['language'] })}>
        <option value="">Все языки</option><option value="RU">Русский (RU)</option><option value="KZ">Казахский (KZ)</option><option value="EN">Английский (EN)</option>
      </select></label>
      <label><span id="channel-label">Канал</span><select aria-labelledby="channel-label" value={query.channel ?? ''} onChange={event => changeQuery({ ...query, offset: 0,
        channel: (event.target.value || undefined) as ConversationQuery['channel'] })}>
        <option value="">Все каналы</option><option value="agent chat">Чат</option><option value="voice call">Звонок</option>
      </select></label>
      <button className="secondary-button" onClick={() => changeQuery({ offset: 0, limit: pageSize })}>Сбросить фильтры</button>
      <button className="secondary-button" onClick={reload}>Обновить список</button>
    </section>
    <div className="conversation-grid">
      <ConversationListPanel key={`${JSON.stringify(query)}:${revision}`} query={query} selected={selected}
        onSelect={setSelected} retry={reload} onPage={offset => changeQuery({ ...query, offset })} />
      {selected ? <ConversationDetail key={`${selected}:${detailAttempt}`} id={selected}
        retry={() => setDetailAttempt(value => value + 1)} onEvaluate={() => setEvaluating(true)} />
        : <section className="panel state-panel"><h2>Выберите диалог</h2><p>Откройте запись из списка, чтобы прочитать её транскрипт.</p></section>}
    </div>
  </>;
}

function ConversationListPanel({ query, selected, onSelect, retry, onPage }: {
  query: ConversationQuery; selected: string | null; onSelect: (id: string) => void;
  retry: () => void; onPage: (offset: number) => void;
}) {
  const [state, setState] = useState<Resource<ConversationList>>({ status: 'loading' });
  useEffect(() => {
    const controller = new AbortController();
    api.listConversations(query, controller.signal).then(data => {
      if (!controller.signal.aborted) setState({ status: 'ready', data });
    }).catch((error: unknown) => {
      if (!controller.signal.aborted) setState({ status: 'error', error });
    });
    return () => controller.abort();
  }, [query]);
  return <section className="panel conversation-list" aria-labelledby="list-heading">
    <div className="panel-header"><h2 id="list-heading">Диалоги</h2>
      {state.status === 'ready' && <span>Всего: {state.data.total}</span>}</div>
    {state.status === 'loading' && <p className="panel-notice" role="status">Загружаем диалоги…</p>}
    {state.status === 'error' && <ApiFailure error={state.error} retry={retry} />}
    {state.status === 'ready' && <>
      {state.data.items.length === 0 ? <div className="panel-notice"><h3>Диалоги не найдены</h3><p>Измените фильтры или импортируйте синтетический JSON.</p></div>
        : <ul className="conversation-items">{state.data.items.map(item => <li key={item.latest_version_id}>
          <button className="conversation-item" aria-pressed={selected === item.latest_version_id}
            onClick={() => onSelect(item.latest_version_id)}>
            <strong>{item.conversation_id}</strong><span>{item.language} · {item.channel === 'voice call' ? 'Звонок' : 'Чат'} · {item.message_count} сообщений{item.has_audio ? ' · Есть аудио' : ''}</span>
          </button>
        </li>)}</ul>}
      <div className="pagination" aria-label="Страницы диалогов">
        <button className="secondary-button" disabled={state.data.offset === 0}
          onClick={() => onPage(Math.max(0, state.data.offset - state.data.limit))}>Назад</button>
        <span>Страница {Math.floor(state.data.offset / state.data.limit) + 1}</span>
        <button className="secondary-button" disabled={state.data.offset + state.data.limit >= state.data.total}
          onClick={() => onPage(state.data.offset + state.data.limit)}>Далее</button>
      </div>
    </>}
  </section>;
}

function ConversationDetail({ id, retry, onEvaluate }: { id: string; retry: () => void; onEvaluate: () => void }) {
  const [state, setState] = useState<Resource<ConversationVersion>>({ status: 'loading' });
  useEffect(() => {
    const controller = new AbortController();
    api.getConversationVersion(id, controller.signal).then(data => {
      if (!controller.signal.aborted) setState({ status: 'ready', data });
    }).catch((error: unknown) => {
      if (!controller.signal.aborted) setState({ status: 'error', error });
    });
    return () => controller.abort();
  }, [id]);
  return <section className="panel transcript-panel" aria-labelledby="transcript-heading">
    <div className="panel-header"><h2 id="transcript-heading">Транскрипт</h2></div>
    {state.status === 'loading' && <p className="panel-notice" role="status">Загружаем транскрипт…</p>}
    {state.status === 'error' && <ApiFailure error={state.error} retry={retry} />}
    {state.status === 'ready' && <>
      <div className="panel-notice"><h3>{state.data.conversation_id}</h3>
        <p>{state.data.language} · {state.data.channel === 'voice call' ? 'Звонок' : 'Чат'} · {state.data.messages.length} сообщений</p>
        <p>Оцениваются действия оператора.</p>
        <button className="primary-button" onClick={onEvaluate}>Оценить диалог</button>
        {state.data.synthetic && <p className="synthetic-note">Тестовые данные — не AI-оценка</p>}
      </div>
      <div className="transcript">
        {state.data.messages.length === 0 && <p>В этой версии нет сообщений.</p>}
        {state.data.messages.map(message => <article className={`message message--${message.speaker}`} key={message.message_id}>
          <div className="message-meta"><span className="speaker">{speakerLabels[message.speaker]}</span><time>{message.timestamp_raw}</time></div>
          <p>{message.text}</p>
        </article>)}
      </div>
      <div className="panel-bottom">
        <ConversationAudio conversation={state.data} />
        <span className="version-label">Версия диалога: {state.data.id}</span>
      </div>
    </>}
  </section>;
}
