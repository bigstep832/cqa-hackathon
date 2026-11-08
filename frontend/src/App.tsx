import { useEffect, useId, useState } from 'react';
import { fetchDemo, type DemoPayload } from './api/demo';
import { ReviewWorkspace } from './ReviewWorkspace';
import { ConversationsScreen } from './ConversationsScreen';
import { EvaluationWorkspace } from './EvaluationWorkspace';
import { ChecklistsScreen } from './checklists/ChecklistsScreen';
import { AppealsScreen } from './AppealsScreen';
import { appealRoute } from './api/appeals';
import { OperatorsScreen } from './operators/OperatorsScreen';
import { operatorHref, operatorRoute } from './operators/route';

type LoadState =
  | { status: 'loading' }
  | { status: 'error'; message: string }
  | { status: 'empty' }
  | { status: 'ready'; data: DemoPayload };

const viewDescriptions = {
  conversations: 'Диалоги, транскрипты и оценка качества работы оператора.',
  operators: 'Нагрузка, результаты звонков и качество обслуживания по операторам.',
  checklists: 'Критерии качества и сохранённые версии правил оценки.',
  appeals: 'Рассмотрение обращений с сохранением исходной оценки и доказательств.',
  demo: 'Синтетический сценарий для проверки интерфейса без AI-запросов.',
};
const viewTitles: Record<keyof typeof viewDescriptions, string> = {
  conversations: 'Диалоги', operators: 'Операторы', checklists: 'Чек-листы', appeals: 'Апелляции', demo: 'Проверка диалога',
};
function NavIcon({ name }: { name: keyof typeof viewDescriptions }) {
  const paths = {
    conversations: 'M4 4h16v12H9l-5 4V4Z',
    operators: 'M3 20v-2a5 5 0 0 1 5-5h2a5 5 0 0 1 5 5v2M9 10a3 3 0 1 0 0-6 3 3 0 0 0 0 6Zm7-5a3 3 0 0 1 0 6m1 3a5 5 0 0 1 4 5',
    checklists: 'M9 6h11M9 12h11M9 18h11M3 5l1 1 2-2M3 11l1 1 2-2M3 17l1 1 2-2',
    appeals: 'M12 3v18M4 7h16M4 7l-3 6h6L4 7Zm16 0-3 6h6l-3-6ZM7 21h10',
    demo: 'm8 5 11 7-11 7V5Z',
  };
  return <svg className="nav-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinejoin="round" strokeLinecap="round" aria-hidden="true"><path d={paths[name]} /></svg>;
}

function Mark({ small = false }: { small?: boolean }) {
  const gradientId = useId();
  return <svg className={small ? 'brand-mark brand-mark--small' : 'brand-mark'} viewBox="0 0 32 32" aria-hidden="true">
    <defs><linearGradient id={gradientId} x1="0" x2="1" y1="0" y2="1"><stop stopColor="var(--brand-mint)" /><stop offset="1" stopColor="var(--brand-lime)" /></linearGradient></defs>
    <path d="M6 5h20v17H14l-8 6V5Z" fill={`url(#${gradientId})`} />
    <path d="m11 13 4 4 7-8" fill="none" stroke="var(--heading)" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round" />
  </svg>;
}

export function App() {
  const [view, selectView] = useState<keyof typeof viewDescriptions>(() =>
    operatorRoute() !== null ? 'operators' : appealRoute() !== null ? 'appeals' : 'conversations');
  const [conversationId, setConversationId] = useState<string | null>(null);
  const [operatorId, setOperatorId] = useState(() => operatorRoute() ?? '');
  const [appealId, setAppealId] = useState(() => appealRoute() ?? '');
  function setView(next: typeof view, selectedConversation: string | null = null) {
    selectView(next); setAppealId(''); setConversationId(selectedConversation); setOperatorId('');
    const url = next === 'appeals' ? '#appeals' : next === 'operators' ? operatorHref('') : `${location.pathname}${location.search}`;
    if ((view === 'operators' || next === 'operators') && new URL(url, location.href).href !== location.href) {
      history.pushState({ cqaView: next, conversationId: selectedConversation }, '', url);
    } else {
      history.replaceState({ cqaView: next, conversationId: selectedConversation }, '', url);
    }
  }
  useEffect(() => {
    const change = () => {
      const operator = operatorRoute();
      if (operator !== null) { setOperatorId(operator); selectView('operators'); return; }
      const id = appealRoute();
      if (id !== null) { setAppealId(id); selectView('appeals'); return; }
      if (!location.hash) {
        const prior = history.state?.cqaView;
        setConversationId(typeof history.state?.conversationId === 'string' ? history.state.conversationId : null);
        selectView(prior === 'demo' || prior === 'checklists' ? prior : 'conversations');
      }
    };
    window.addEventListener('hashchange', change);
    window.addEventListener('popstate', change);
    return () => { window.removeEventListener('hashchange', change); window.removeEventListener('popstate', change); };
  }, []);
  return <div className="app-shell">
    <a className="skip-link" href="#main">Перейти к содержимому</a>
    <aside className="sidebar" aria-label="Разделы CQA">
      <a className="brand" href="#main" aria-label="CQA — проверка качества"><Mark /><span>CQA<span className="brand-caption">Freedom Broker</span></span></a>
      <nav className="sidebar-section" aria-label="Рабочее пространство">
        <span className="sidebar-label">Рабочее пространство</span>
        <button className={`nav-link${view === 'conversations' ? ' nav-link--active' : ''}`} aria-pressed={view === 'conversations'} onClick={() => setView('conversations')}><NavIcon name="conversations" />Диалоги</button>
        <button className={`nav-link${view === 'operators' ? ' nav-link--active' : ''}`} aria-pressed={view === 'operators'} onClick={() => setView('operators')}><NavIcon name="operators" />Операторы</button>
        <button className={`nav-link${view === 'checklists' ? ' nav-link--active' : ''}`} aria-pressed={view === 'checklists'} onClick={() => setView('checklists')}><NavIcon name="checklists" />Чек-листы</button>
        <button className={`nav-link${view === 'appeals' ? ' nav-link--active' : ''}`} aria-pressed={view === 'appeals'} onClick={() => setView('appeals')}><NavIcon name="appeals" />Апелляции</button>
        <button className={`nav-link${view === 'demo' ? ' nav-link--active' : ''}`} aria-pressed={view === 'demo'} onClick={() => setView('demo')}><NavIcon name="demo" />Демо · mock</button>
      </nav>
      <div className="sidebar-bottom"><p>DT20 · CQA</p><span className="muted">Диалоги и версии из серверного хранилища.</span></div>
    </aside>
    <main id="main" className="main" tabIndex={-1}>
      <header className="page-header"><div><p className="breadcrumb">Рабочее пространство / {view === 'demo' ? 'Тестовый пример' : viewTitles[view]}</p>
        <h1>{viewTitles[view]}</h1><p className="page-description">{viewDescriptions[view]}</p></div><span className="page-context"><NavIcon name={view} />DT20 · CQA</span></header>
      {view === 'demo' ? <DemoScreen /> : view === 'checklists' ? <ChecklistsScreen /> : view === 'appeals' ? <AppealsScreen key={appealId} evaluationId={appealId} />
        : view === 'operators' ? <OperatorsScreen operatorId={operatorId} openConversation={id => setView('conversations', id)} /> : <ConversationsScreen initialVersionId={conversationId} />}
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
