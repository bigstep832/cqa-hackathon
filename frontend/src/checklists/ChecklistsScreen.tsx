import { useCallback, useEffect, useState } from 'react';
import { api, type ChecklistList, type ChecklistVersion } from '../api/client';
import { ApiFailure } from '../ApiFailure';
import { ChecklistEditor } from './ChecklistEditor';
import { formatWeight } from '../format';

type Resource<T> = { status: 'loading' } | { status: 'error'; error: unknown } | { status: 'ready'; data: T };

export function ChecklistsScreen() {
  const [editor, setEditor] = useState<{ base?: ChecklistVersion } | null>(null);
  const [selected, setSelected] = useState<string | null>(null);
  const [revision, setRevision] = useState(0);
  const [detailRetry, setDetailRetry] = useState(0);
  const [versionId, setVersionId] = useState('');
  const [visited, setVisited] = useState<ChecklistVersion[]>([]);
  const [notice, setNotice] = useState('');
  const remember = useCallback((version: ChecklistVersion) => {
    setVisited(items => items.some(item => item.id === version.id) ? items : [...items, version]);
  }, []);

  if (editor) return <ChecklistEditor base={editor.base} onCancel={() => setEditor(null)} onSaved={version => {
    setNotice(version.id === editor.base?.id
      ? `Изменений нет: сервер вернул существующую версию v${version.version}.`
      : `Сохранена версия v${version.version}. Она доступна при запуске новой оценки.`);
    remember(version); setSelected(version.id); setEditor(null);
    setRevision(value => value + 1); setDetailRetry(value => value + 1);
  }} />;

  return <>
    <div className="checklist-toolbar">
      <button className="primary-button" onClick={() => { setNotice(''); setEditor({}); }}>Создать чек-лист</button>
      <button className="secondary-button" onClick={() => setRevision(value => value + 1)}>Обновить чек-листы</button>
    </div>
    {notice && <p className="checklist-notice" role="status">{notice}</p>}
    <div className="checklist-grid">
      <div className="checklist-catalog">
        <ChecklistListPanel key={revision} selected={selected} onSelect={setSelected} retry={() => setRevision(value => value + 1)} />
        <form className="panel version-lookup" onSubmit={event => { event.preventDefault(); if (versionId.trim()) { setSelected(versionId.trim()); setDetailRetry(value => value + 1); } }}>
          <label htmlFor="checklist-version-lookup">Открыть версию по ID</label>
          <input id="checklist-version-lookup" value={versionId} onChange={event => setVersionId(event.target.value)} />
          <button className="secondary-button" disabled={!versionId.trim()}>Открыть версию</button>
          <p className="muted">В списке показаны последние версии. ID исторической версии можно взять из оценки.</p>
        </form>
        {visited.length > 0 && <section className="panel visited-versions" aria-label="Открытые версии">
          <h3>Открытые версии</h3>
          <p className="muted">Просмотренные в этом разделе, не полная история.</p>
          <ul>{visited.map(item => <li key={item.id}><button className="text-link" aria-pressed={item.id === selected} onClick={() => setSelected(item.id)}>{item.name} · v{item.version} · {item.id}</button></li>)}</ul>
        </section>}
      </div>
      {selected ? <ChecklistDetail key={`${selected}:${detailRetry}`} id={selected} onLoaded={remember}
        onEdit={base => { setNotice(''); setEditor({ base }); }} retry={() => setDetailRetry(value => value + 1)} />
        : <section className="panel state-panel"><h2>Выберите чек-лист</h2><p>Откройте сохранённую версию или создайте новый чек-лист.</p></section>}
    </div>
  </>;
}

function ChecklistListPanel({ selected, onSelect, retry }: {
  selected: string | null; onSelect: (id: string) => void; retry: () => void;
}) {
  const [state, setState] = useState<Resource<ChecklistList>>({ status: 'loading' });
  useEffect(() => {
    const controller = new AbortController();
    api.listChecklists(controller.signal).then(data => {
      if (!controller.signal.aborted) setState({ status: 'ready', data });
    }).catch((error: unknown) => {
      if (!controller.signal.aborted) setState({ status: 'error', error });
    });
    return () => controller.abort();
  }, []);
  return <section className="panel conversation-list" aria-labelledby="checklist-list-heading">
    <div className="panel-header"><h2 id="checklist-list-heading">Сохранённые чек-листы</h2></div>
    {state.status === 'loading' && <p className="panel-notice" role="status">Загружаем чек-листы…</p>}
    {state.status === 'error' && <ApiFailure error={state.error} retry={retry} />}
    {state.status === 'ready' && (state.data.items.length === 0 ? <p className="panel-notice">Чек-листов пока нет. Создайте первый.</p>
      : <ul className="conversation-items">{state.data.items.map(item => <li key={item.checklist_id}>
        <button className="conversation-item" aria-pressed={selected === item.latest_version_id} onClick={() => onSelect(item.latest_version_id)}>
          <strong>{item.name}</strong><span>Версия {item.version}</span><span className="version-label">{item.latest_version_id}</span>
        </button>
      </li>)}</ul>)}
  </section>;
}

function ChecklistDetail({ id, onLoaded, onEdit, retry }: {
  id: string; onLoaded: (version: ChecklistVersion) => void; onEdit: (version: ChecklistVersion) => void; retry: () => void;
}) {
  const [state, setState] = useState<Resource<ChecklistVersion>>({ status: 'loading' });
  useEffect(() => {
    const controller = new AbortController();
    api.getChecklistVersion(id, controller.signal).then(data => {
      if (data.id !== id) throw new Error('Сервер вернул другую версию чек-листа. Повторите загрузку.');
      if (!controller.signal.aborted) { setState({ status: 'ready', data }); onLoaded(data); }
    }).catch((error: unknown) => {
      if (!controller.signal.aborted) setState({ status: 'error', error });
    });
    return () => controller.abort();
  }, [id, onLoaded]);
  return <section className="panel checklist-detail" aria-label="Версия чек-листа">
    {state.status === 'loading' && <p role="status">Загружаем версию чек-листа…</p>}
    {state.status === 'error' && <ApiFailure error={state.error} retry={retry} />}
    {state.status === 'ready' && <>
      <h2>{state.data.name} · v{state.data.version}</h2>
      <p className="version-label">ID версии: {state.data.id}</p>
      <p className="muted">Критериев: {state.data.criteria.length} · Создана: {state.data.created_at}</p>
      <button className="primary-button" onClick={() => onEdit(state.data)}>Редактировать — новая версия</button>
      {state.data.sections.map(section => <section className="checklist-section" key={section.id} aria-label={`Секция ${section.title}`}>
        <h3>{section.title} <span className="muted">· {section.id}</span></h3>
        {state.data.criteria.filter(criterion => criterion.section_id === section.id).map(criterion => <article className="criterion-snapshot" key={criterion.id}>
          <h4>{criterion.id} · {criterion.title}</h4><p className="preserve-lines">{criterion.description}</p>
          <p className="criterion-meta">{criterion.answer_type} · {criterion.kind}{criterion.kind === 'scored' ? ` · Вес: ${formatWeight(criterion.weight)}` : ' · Без веса, отдельный флаг'}</p>
          <p>{criterion.allow_na ? `N/A разрешён: ${criterion.na_condition}` : 'N/A не разрешён'}</p>
          <ul className="snapshot-options">{criterion.options.map((option, index) => <li key={index}>
            <strong>{option.label}</strong> <span className="muted">· значение: {String(option.value)} · {criterion.kind === 'scored' ? `quality: ${option.quality}` : option.flagged ? 'нарушение' : 'без нарушения'}</span>
            <p className="preserve-lines">{option.description}</p>
          </li>)}</ul>
        </article>)}
      </section>)}
    </>}
  </section>;
}
