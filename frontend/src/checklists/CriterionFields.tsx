import { useState } from 'react';
import { changeAnswerType, changeKind, fieldId, type ChecklistDraft, type CriterionDraft, type FieldIssue } from './draft';
import { EditorField, EditorSelect } from './EditorField';

export function CriterionFields({ criterion, index, sections, issues, onChange, onRemove, canRemove, initiallyOpen }: {
  criterion: CriterionDraft; index: number; sections: ChecklistDraft['sections']; issues: FieldIssue[];
  onChange: (criterion: CriterionDraft) => void; onRemove: () => void; canRemove: boolean; initiallyOpen: boolean;
}) {
  const [typeNotice, setTypeNotice] = useState(false);
  const path = `criteria.${index}`;
  function update(patch: Partial<CriterionDraft>) { onChange({ ...criterion, ...patch }); }
  return <details className="criterion-editor" open={initiallyOpen}>
    <summary>{criterion.id || `Критерий ${index + 1}`} — {criterion.title || 'Новый критерий'}</summary>
    <div className="criterion-editor-body">
      <div className="editor-grid">
        <EditorField path={`${path}.id`} label="ID критерия" value={criterion.id} issues={issues} onChange={id => update({ id })} />
        <EditorField path={`${path}.title`} label="Название критерия" value={criterion.title} issues={issues} onChange={title => update({ title })} />
      </div>
      <EditorField path={`${path}.description`} label="Описание критерия" value={criterion.description} issues={issues} multiline onChange={description => update({ description })} />
      <div className="editor-grid">
        <EditorSelect path={`${path}.section_id`} label="Секция критерия" value={criterion.section_id} issues={issues} onChange={section_id => update({ section_id })}>
          <option value="">Выберите секцию</option>{sections.map((section, i) => <option key={i} value={section.id}>{section.title || 'Без названия'} · {section.id}</option>)}
        </EditorSelect>
        <EditorSelect path={`${path}.kind`} label="Назначение критерия" value={criterion.kind} issues={issues} onChange={kind => onChange(changeKind(criterion, kind as CriterionDraft['kind']))}>
          <option value="scored">scored — баллы</option><option value="flag">flag — флаг нарушения</option>
        </EditorSelect>
        <EditorSelect path={`${path}.answer_type`} label="Тип ответа" value={criterion.answer_type} issues={issues} onChange={type => {
          onChange(changeAnswerType(criterion, type as CriterionDraft['answer_type'])); setTypeNotice(true);
        }}><option value="boolean">boolean — да / нет</option><option value="scale">scale — числовая шкала</option><option value="choice">choice — выбор варианта</option></EditorSelect>
        {criterion.kind === 'scored' && <EditorField path={`${path}.weight`} label="Вес" value={criterion.weight.replace('.', ',')} issues={issues} inputMode="decimal" onChange={weight => update({ weight: weight.replace(',', '.') })} />}
      </div>
      <p className="muted">Смена типа ответа заменяет варианты шаблоном выбранного типа. Проверьте значения, описания и quality.</p>
      {typeNotice && <p role="status">Варианты обновлены для типа {criterion.answer_type}. Заполните их описания.</p>}
      {criterion.kind === 'flag' && <p className="muted">Флаг не имеет веса и quality. Отметьте варианты, означающие нарушение.</p>}
      <label className="checkbox-label"><input type="checkbox" checked={criterion.allow_na} onChange={event => update({ allow_na: event.target.checked, na_condition: event.target.checked ? criterion.na_condition : '' })} />Разрешить N/A</label>
      {criterion.allow_na && <EditorField path={`${path}.na_condition`} label="Условие N/A" value={criterion.na_condition} issues={issues} multiline onChange={na_condition => update({ na_condition })} />}
      <h3>Варианты ответа</h3>
      {criterion.options.map((option, optionIndex) => {
        const optionPath = `${path}.options.${optionIndex}`;
        const change = (patch: Partial<typeof option>) => update({ options: criterion.options.map((item, i) => i === optionIndex ? { ...item, ...patch } : item) });
        return <fieldset className="option-editor" key={optionIndex}><legend>Вариант {optionIndex + 1}</legend>
          <div className="editor-grid">
            {criterion.answer_type === 'boolean' ? <div className="editor-field"><label htmlFor={fieldId(`${optionPath}.value`)}>Значение</label><input id={fieldId(`${optionPath}.value`)} value={option.value} readOnly /></div>
              : <EditorField path={`${optionPath}.value`} label="Значение" value={option.value} issues={issues} inputMode={criterion.answer_type === 'scale' ? 'decimal' : undefined} onChange={value => change({ value })} />}
            <EditorField path={`${optionPath}.label`} label="Название варианта" value={option.label} issues={issues} onChange={label => change({ label })} />
            {criterion.kind === 'scored' ? <EditorField path={`${optionPath}.quality`} label="Quality (0–1)" value={option.quality} issues={issues} inputMode="decimal" onChange={quality => change({ quality })} />
              : <label className="checkbox-label"><input type="checkbox" checked={option.flagged} onChange={event => change({ flagged: event.target.checked })} />Этот вариант означает нарушение</label>}
          </div>
          <EditorField path={`${optionPath}.description`} label="Описание варианта" value={option.description} issues={issues} multiline onChange={description => change({ description })} />
          {criterion.answer_type !== 'boolean' && <button className="secondary-button" type="button" disabled={criterion.options.length === 1} onClick={() => update({ options: criterion.options.filter((_, i) => i !== optionIndex) })}>Удалить вариант {optionIndex + 1}</button>}
        </fieldset>;
      })}
      {criterion.answer_type !== 'boolean' && <button className="secondary-button" type="button" onClick={() => update({ options: [...criterion.options, { value: '', label: '', description: '', quality: '', flagged: false }] })}>Добавить вариант</button>}
      <button className="secondary-button danger-button" type="button" disabled={!canRemove} onClick={onRemove}>Удалить критерий {criterion.id}</button>
    </div>
  </details>;
}
