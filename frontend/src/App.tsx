import { useEffect, useState } from 'react';
import { fetchDemo, type DemoPayload } from './api/demo';
import { ReviewWorkspace } from './ReviewWorkspace';
import { ConversationsScreen } from './ConversationsScreen';
import { EvaluationWorkspace } from './EvaluationWorkspace';
import { ChecklistsScreen } from './checklists/ChecklistsScreen';

type LoadState =
  | { status: 'loading' }
  | { status: 'error'; message: string }
  | { status: 'empty' }
  | { status: 'ready'; data: DemoPayload };

function Mark({ small = false }: { small?: boolean }) {
  return <svg className={small ? 'brand-mark brand-mark--small' : 'brand-mark'} viewBox="0 0 32 32" aria-hidden="true">
    <path d="M6 5h20v17H14l-8 6V5Z" fill="currentColor" />
    <path d="m11 13 4 4 7-8" fill="none" stroke="white" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round" />
  </svg>;
}

export function App() {
  const [view, setView] = useState<'conversations' | 'checklists' | 'demo'>('conversations');
  return <div className="app-shell">
    <a className="skip-link" href="#main">Перейти к содержимому</a>
    <aside className="sidebar" aria-label="Разделы CQA">
      <a className="brand" href="#main" aria-label="CQA — проверка качества"><Mark /><span>CQA<span className="brand-caption">Качество диалогов</span></span></a>
      <nav className="sidebar-section" aria-label="Рабочее пространство">
        <span className="sidebar-label">Рабочее пространство</span>
        <button className={`nav-link${view === 'conversations' ? ' nav-link--active' : ''}`} aria-pressed={view === 'conversations'} onClick={() => setView('conversations')}>Диалоги</button>
        <button className={`nav-link${view === 'checklists' ? ' nav-link--active' : ''}`} aria-pressed={view === 'checklists'} onClick={() => setView('checklists')}>Чек-листы</button>
        <button className={`nav-link${view === 'demo' ? ' nav-link--active' : ''}`} aria-pressed={view === 'demo'} onClick={() => setView('demo')}>Демо · mock</button>
      </nav>
      <div className="sidebar-bottom"><p>DT20 · CQA</p><span className="muted">Диалоги и версии из серверного хранилища.</span></div>
    </aside>
    <main id="main" className="main" tabIndex={-1}>
      <header className="page-header"><div><p className="breadcrumb">Рабочее пространство / {view === 'demo' ? 'Тестовый пример' : view === 'checklists' ? 'Чек-листы' : 'Диалоги'}</p>
        <h1>{view === 'demo' ? 'Проверка диалога' : view === 'checklists' ? 'Чек-листы' : 'Диалоги'}</h1></div></header>
      {view === 'demo' ? <DemoScreen /> : view === 'checklists' ? <ChecklistsScreen /> : <ConversationsScreen />}
      <footer className="page-footer">CQA <span aria-hidden="true">/</span> Каждая оценка связана с конкретной версией диалога и чек-листа.</footer>
    </main>
  </div>;
}

export function DemoScreen() {
  const [evaluating, setEvaluating] = useState(false);
  const [attempt, setAttempt] = useState(0);
  const [state, setState] = useState<LoadState>({ status: 'loading' });

  useEffect(() => {
    const controller = new AbortController();
    fetchDemo(controller.signal).then(data => {
      if (!controller.signal.aborted) {
        setState(data && data.conversation.messages.length > 0 ? { status: 'ready', data } : { status: 'empty' });
      }
    }).catch((error: unknown) => {
      if (!controller.signal.aborted) {
        setState({ status: 'error', message: error instanceof Error ? error.message : 'Не удалось загрузить пример.' });
      }
    });
    return () => controller.abort();
  }, [attempt]);

  function retry() {
    setState({ status: 'loading' });
    setAttempt(previous => previous + 1);
  }

  if (evaluating && state.status === 'ready') return <>
    <button className="secondary-button evaluation-back" onClick={() => setEvaluating(false)}>К демонстрационной карточке</button>
    <EvaluationWorkspace conversationVersionId={state.data.conversation.id} />
  </>;

  return <>
      <div className="demo-banner" role="note">
        <span className="banner-icon" aria-hidden="true">i</span>
        <div><strong>Тестовые данные — не AI-оценка</strong><p>Синтетический разговор и заранее заданные ответы для проверки интерфейса и расчёта.</p></div>
      </div>

      {state.status === 'loading' && <div className="state-panel" role="status"><span className="loading-dot" aria-hidden="true" /><h2>Загружаем тестовый пример</h2><p>Получаем диалог, критерии и результат расчёта.</p></div>}
      {state.status === 'error' && <div className="state-panel state-panel--error" role="alert"><h2>Пример не загрузился</h2><p>{state.message}</p><p className="muted">Проверьте подключение к серверу и повторите загрузку.</p><button className="primary-button" onClick={retry}>Повторить загрузку</button></div>}
      {state.status === 'empty' && <div className="state-panel"><Mark small /><h2>Пока нет диалогов</h2><p>Сервер не вернул тестовый разговор.</p><button className="primary-button" onClick={retry}>Обновить</button></div>}
      {state.status === 'ready' && <>
        <button className="primary-button" onClick={() => setEvaluating(true)}>Оценить встроенный пример</button>
        <ReviewWorkspace data={state.data} />
      </>}
  </>;
}
