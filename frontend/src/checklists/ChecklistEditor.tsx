import { useEffect, useRef, useState } from 'react';
import { api, ApiError, type ChecklistVersion } from '../api/client';
import { CriterionFields } from './CriterionFields';
import { EditorField } from './EditorField';
import { fieldId, newCriterion, nextId, serializeDraft, toDraft, validateDraft, type ChecklistDraft, type FieldIssue } from './draft';

const errorHints: Record<number, string> = {
  400: 'Сервер отклонил данные чек-листа. Проверьте заполненные поля.',
  409: 'Конфликт сохранения. Проверьте актуальную версию перед повторной отправкой.',
  422: 'Данные не прошли проверку сервера. Исправьте указанные поля.',
  500: 'Ошибка сервера при сохранении.',
};

function focusField(path: string) {
  const element = document.getElementById(fieldId(path));
  const details = element?.closest('details');
  if (details) details.open = true;
  element?.focus();
}

export function ChecklistEditor({ base, onSaved, onCancel }: {
  base?: ChecklistVersion; onSaved: (version: ChecklistVersion) => void; onCancel: () => void;
}) {
  const [draft, setDraft] = useState(() => toDraft(base));
  const [issues, setIssues] = useState<FieldIssue[]>([]);
  const [error, setError] = useState<unknown>(null);
  const [pending, setPending] = useState(false);
  const [addedIndex, setAddedIndex] = useState<number | null>(null);
  const active = useRef<AbortController | null>(null);
  useEffect(() => () => active.current?.abort(), []);

  function update(next: ChecklistDraft) { setDraft(next); setIssues([]); setError(null); }
  async function submit(event: React.SubmitEvent<HTMLFormElement>) {
    event.preventDefault();
    if (active.current) return;
    const validation = validateDraft(draft);
    setIssues(validation); setError(null);
    if (validation.length) { focusField(validation[0]!.field); return; }
    const controller = new AbortController();
    active.current = controller;
    setPending(true);
    try {
      const body = serializeDraft(draft);
      const saved = base
        ? await api.createChecklistVersion(base.checklist_id, body, controller.signal)
        : await api.createChecklist(body, controller.signal);
      if (!controller.signal.aborted) onSaved(saved);
    } catch (failure) {
      if (!controller.signal.aborted) {
        setError(failure);
        if (failure instanceof ApiError) {
          const fields = failure.details.map(item => ({ ...item, field: item.field.replace(/^body\./, '') }));
          setIssues(fields);
          if (fields[0]) focusField(fields[0].field);
        }
      }
    } finally {
      if (!controller.signal.aborted) { active.current = null; setPending(false); }
    }
  }

  return <section className="panel checklist-editor" aria-labelledby="editor-heading">
    <h2 id="editor-heading">{base ? 'Новая версия чек-листа' : 'Новый чек-лист'}</h2>
    {!base && <p className="version-label">После сохранения будет создана первая версия. Следующие изменения сохраняются отдельными версиями.</p>}
    {base && <p className="version-label">Основа: v{base.version} · {base.id}. Сохранённая версия останется неизменной.</p>}
    <p className="muted">Заполните описания критериев и каждого варианта: они входят в инструкции оценщика. Quality задаётся от 0 до 1; итог рассчитывает сервер.</p>
    <form onSubmit={event => { void submit(event); }} noValidate aria-busy={pending}>
      <fieldset className="editor-fields" disabled={pending}>
        <legend className="sr-only">Настройки чек-листа</legend>
        <EditorField path="name" label="Название чек-листа" value={draft.name} issues={issues} onChange={name => update({ ...draft, name })} />
        <h3>Секции</h3>
        {draft.sections.map((section, index) => <div className="section-editor" key={index}>
          <div className="editor-grid">
            <EditorField path={`sections.${index}.id`} label="ID секции" value={section.id} issues={issues} onChange={id => update({ ...draft,
              sections: draft.sections.map((item, i) => i === index ? { ...item, id } : item),
              criteria: draft.criteria.map(item => item.section_id === section.id ? { ...item, section_id: id } : item),
            })} />
            <EditorField path={`sections.${index}.title`} label="Название секции" value={section.title} issues={issues} onChange={title => update({ ...draft,
              sections: draft.sections.map((item, i) => i === index ? { ...item, title } : item),
            })} />
          </div>
          <button type="button" className="secondary-button" disabled={draft.sections.length === 1 || draft.criteria.some(item => item.section_id === section.id)}
            onClick={() => update({ ...draft, sections: draft.sections.filter((_, i) => i !== index) })}>Удалить секцию {section.id}</button>
        </div>)}
        <p className="muted">Перед удалением секции перенесите её критерии в другую секцию.</p>
        <button type="button" className="secondary-button" onClick={() => update({ ...draft,
          sections: [...draft.sections, { id: nextId('S', draft.sections.map(item => item.id)), title: '' }],
        })}>Добавить секцию</button>
        <h3>Критерии ({draft.criteria.length})</h3>
        {draft.criteria.map((criterion, index) => <CriterionFields key={index} criterion={criterion} index={index}
          sections={draft.sections} issues={issues} initiallyOpen={index === 0 || index === addedIndex}
          canRemove={draft.criteria.length > 1} onChange={next => update({ ...draft,
            criteria: draft.criteria.map((item, i) => i === index ? next : item),
          })} onRemove={() => { update({ ...draft, criteria: draft.criteria.filter((_, i) => i !== index) }); setAddedIndex(null); }} />)}
        <button type="button" className="secondary-button" onClick={() => {
          setAddedIndex(draft.criteria.length);
          update({ ...draft, criteria: [...draft.criteria, newCriterion(nextId('C', draft.criteria.map(item => item.id)), draft.sections[0]!.id)] });
        }}>Добавить критерий</button>
      </fieldset>
      {issues.length > 0 && <div className="validation-summary" role="alert"><strong>Проверьте поля ({issues.length})</strong>
        <ul>{issues.map((issue, index) => <li key={index}><button type="button" className="text-link" onClick={() => focusField(issue.field)}>{issue.field}: {issue.message}</button></li>)}</ul>
      </div>}
      {error != null && <div className="request-error" role="alert">
        <h3>Не удалось сохранить чек-лист</h3>
        <p>{error instanceof ApiError ? errorHints[error.status] ?? 'Запрос сохранения не завершён.' : 'Запрос сохранения не завершён.'}</p>
        <p>{error instanceof Error ? error.message : 'Неизвестная ошибка.'}</p>
        {(!(error instanceof ApiError) || error.status === 0 || error.status >= 500 || error.code === 'INVALID_RESPONSE') &&
          <p>Ответ не подтверждает, сохранил ли сервер данные. Перед повторной отправкой проверьте список в другой вкладке. Поля формы сохранены.</p>}
      </div>}
      <div className="editor-actions">
        <button className="primary-button" type="submit" disabled={pending}>{pending ? 'Сохраняем…' : base ? 'Сохранить новую версию' : 'Создать чек-лист'}</button>
        <button className="secondary-button" type="button" disabled={pending} onClick={onCancel}>Отмена</button>
      </div>
      {pending && <p role="status">Отправляем чек-лист на сервер…</p>}
    </form>
  </section>;
}
