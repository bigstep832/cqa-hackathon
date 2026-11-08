import type { ChecklistCreateRequest, ChecklistVersion } from '../api/client';

type Criterion = ChecklistCreateRequest['criteria'][number];
type Option = Criterion['options'][number];
// Text buffers allow incomplete numeric input. Decimal weights/quality remain
// strings for the backend; this module validates configuration, never scores.
export type OptionDraft = Omit<Option, 'value' | 'quality'> & { value: string; quality: string };
export type CriterionDraft = Omit<Criterion, 'weight' | 'options' | 'na_condition'> & {
  weight: string; na_condition: string; options: OptionDraft[];
};
export type ChecklistDraft = Omit<ChecklistCreateRequest, 'criteria'> & { criteria: CriterionDraft[] };
export type FieldIssue = { field: string; message: string };
export const fieldId = (path: string) => `checklist-${path.replaceAll('.', '-')}`;

export function nextId(prefix: string, ids: string[]) {
  let index = 1;
  while (ids.includes(`${prefix}${String(index).padStart(2, '0')}`)) index++;
  return `${prefix}${String(index).padStart(2, '0')}`;
}
export function defaultOptions(type: Criterion['answer_type'], kind: Criterion['kind']): OptionDraft[] {
  const values = type === 'boolean' ? ['true', 'false'] : type === 'scale' ? ['0', '1'] : ['option_1', 'option_2'];
  return values.map((value, index) => ({ value,
    label: type === 'boolean' ? (index === 0 ? 'Да' : 'Нет') : `Вариант ${index + 1}`,
    description: '', quality: kind === 'flag' ? '' : type === 'boolean' ? String(1 - index) : String(index), flagged: false,
  }));
}
export function newCriterion(id: string, sectionId: string): CriterionDraft {
  return { id, section_id: sectionId, title: '', description: '', kind: 'scored', answer_type: 'boolean',
    weight: '1', allow_na: false, na_condition: '', options: defaultOptions('boolean', 'scored') };
}
export function toDraft(version?: ChecklistVersion): ChecklistDraft {
  if (!version) return { name: '', sections: [{ id: 'S01', title: '' }], criteria: [newCriterion('C01', 'S01')] };
  return { name: version.name, sections: version.sections.map(section => ({ ...section })),
    criteria: version.criteria.map(criterion => ({ ...criterion, weight: criterion.weight ?? '', na_condition: criterion.na_condition ?? '',
      options: criterion.options.map(option => ({ ...option, value: String(option.value), quality: option.quality ?? '' })),
    })),
  };
}
export function changeAnswerType(criterion: CriterionDraft, type: Criterion['answer_type']): CriterionDraft {
  return { ...criterion, answer_type: type, options: defaultOptions(type, criterion.kind) };
}
export function changeKind(criterion: CriterionDraft, kind: Criterion['kind']): CriterionDraft {
  return { ...criterion, kind, weight: kind === 'flag' ? '' : '1',
    options: criterion.options.map(option => ({ ...option, quality: '', flagged: false })),
  };
}

const decimalPattern = /^[+-]?(?:\d+\.?\d*|\.\d+)(?:[eE][+-]?\d+)?$/;
function finiteNumber(value: string) { return decimalPattern.test(value.trim()) && Number.isFinite(Number(value)); }
// Decimal configuration uses strings in the API. Compare bounds without Number
// rounding (e.g. 1.0000000000000000001) or underflow (e.g. -1e-400).
function decimalBounds(value: string) {
  const text = value.trim();
  if (!decimalPattern.test(text)) return null;
  const [coefficient, exponent = '0'] = text.replace(/^[+-]/, '').split(/[eE]/);
  const significant = coefficient!.replace('.', '').replace(/^0+/, '');
  if (!significant) return { negative: false, aboveOne: false };
  const fractionLength = (coefficient!.split('.')[1] ?? '').length;
  const position = BigInt(significant.length - fractionLength) + BigInt(exponent);
  return { negative: text.startsWith('-'), aboveOne: position > 1n || (position === 1n && significant.replace(/0+$/, '') !== '1') };
}

export function validateDraft(draft: ChecklistDraft): FieldIssue[] {
  const issues: FieldIssue[] = [];
  const issue = (field: string, message: string) => issues.push({ field, message });
  const required = (value: string, field: string) => { if (!value.trim()) issue(field, 'Заполните это поле.'); };
  required(draft.name, 'name');
  if (!draft.sections.length) issue('sections', 'Добавьте хотя бы одну секцию.');
  if (!draft.criteria.length) issue('criteria', 'Добавьте хотя бы один критерий.');
  const sectionIds = draft.sections.map(section => section.id.trim());
  const criterionIds = draft.criteria.map(criterion => criterion.id.trim());
  draft.sections.forEach((section, index) => {
    required(section.id, `sections.${index}.id`); required(section.title, `sections.${index}.title`);
    if (sectionIds.indexOf(section.id.trim()) !== index) issue(`sections.${index}.id`, 'ID секции должен быть уникальным.');
  });
  draft.criteria.forEach((criterion, index) => {
    const path = `criteria.${index}`;
    required(criterion.id, `${path}.id`); required(criterion.title, `${path}.title`); required(criterion.description, `${path}.description`);
    if (criterionIds.indexOf(criterion.id.trim()) !== index) issue(`${path}.id`, 'ID критерия должен быть уникальным.');
    if (!sectionIds.includes(criterion.section_id.trim())) issue(`${path}.section_id`, 'Выберите существующую секцию.');
    if (criterion.allow_na) required(criterion.na_condition, `${path}.na_condition`);
    if (!criterion.allow_na && criterion.na_condition) issue(`${path}.na_condition`, 'Условие N/A допустимо только при включённом N/A.');
    const weight = decimalBounds(criterion.weight);
    if (criterion.kind === 'scored' && (!weight || weight.negative)) issue(`${path}.weight`, 'Вес должен быть конечным числом не меньше 0.');
    if (criterion.kind === 'flag' && criterion.weight !== '') issue(`${path}.weight`, 'У флага не должно быть веса.');
    if (!criterion.options.length) issue(`${path}.options`, 'Добавьте хотя бы один вариант ответа.');
    const keys = criterion.options.map(option => criterion.answer_type === 'scale' && finiteNumber(option.value) ? String(Number(option.value)) : option.value);
    if (criterion.answer_type === 'boolean' && (keys.length !== 2 || !keys.includes('true') || !keys.includes('false'))) issue(`${path}.answer_type`, 'Boolean требует ровно два варианта: true и false.');
    if (criterion.answer_type === 'scale' && !criterion.options.some(option => finiteNumber(option.value) && Number(option.value) > 0)) issue(`${path}.answer_type`, 'У шкалы должна быть хотя бы одна положительная ступень.');
    criterion.options.forEach((option, optionIndex) => {
      const optionPath = `${path}.options.${optionIndex}`;
      required(option.label, `${optionPath}.label`); required(option.description, `${optionPath}.description`);
      if (criterion.answer_type === 'choice') required(option.value, `${optionPath}.value`);
      if (criterion.answer_type === 'scale' && !finiteNumber(option.value)) issue(`${optionPath}.value`, 'Значение шкалы должно быть конечным числом.');
      if (keys.indexOf(keys[optionIndex]!) !== optionIndex) issue(`${optionPath}.value`, 'Значения вариантов не должны повторяться.');
      const quality = decimalBounds(option.quality);
      if (criterion.kind === 'scored' && (!quality || quality.negative || quality.aboveOne)) issue(`${optionPath}.quality`, 'Quality — число от 0 до 1 включительно.');
      if (criterion.kind === 'scored' && option.flagged) issue(`${optionPath}.flagged`, 'У scored-варианта не может быть флага нарушения.');
      if (criterion.kind === 'flag' && option.quality !== '') issue(`${optionPath}.quality`, 'У флага не должно быть quality.');
    });
  });
  return issues;
}

export function serializeDraft(draft: ChecklistDraft): ChecklistCreateRequest {
  if (validateDraft(draft).length) throw new Error('Чек-лист содержит ошибки заполнения.');
  return { name: draft.name.trim(), sections: draft.sections.map(section => ({ id: section.id.trim(), title: section.title.trim() })),
    criteria: draft.criteria.map(criterion => ({
      id: criterion.id.trim(), title: criterion.title.trim(), description: criterion.description.trim(),
      section_id: criterion.section_id.trim(), kind: criterion.kind, answer_type: criterion.answer_type,
      weight: criterion.kind === 'flag' ? null : criterion.weight.trim(),
      allow_na: criterion.allow_na, na_condition: criterion.allow_na ? criterion.na_condition.trim() : null,
      options: criterion.options.map(option => ({
        value: criterion.answer_type === 'boolean' ? option.value === 'true' : criterion.answer_type === 'scale' ? Number(option.value) : option.value,
        label: option.label.trim(), description: option.description.trim(),
        quality: criterion.kind === 'flag' ? null : option.quality.trim(), flagged: criterion.kind === 'flag' && option.flagged,
      })),
    })),
  };
}
