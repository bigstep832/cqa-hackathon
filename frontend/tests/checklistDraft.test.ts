import { describe, expect, it } from 'vitest';
import { changeAnswerType, changeKind, newCriterion, nextId, serializeDraft, toDraft, validateDraft } from '../src/checklists/draft';
import { historicalChecklist } from './evaluationFixtures';

describe('Конфигурация чек-листа без расчёта баллов', () => {
  it('копирует snapshot и сериализует только request-поля, сохраняя Decimal', () => {
    const original = structuredClone(historicalChecklist);
    const draft = toDraft(historicalChecklist);
    draft.name = ' Новое имя ';
    draft.criteria[0]!.weight = '0.12345678901234567890123456789';
    draft.criteria[0]!.options[0]!.quality = '0.999999999999999999999999';
    const body = serializeDraft(draft);
    expect(historicalChecklist).toEqual(original);
    expect(body).not.toHaveProperty('id');
    expect(body).not.toHaveProperty('version');
    expect(body.name).toBe('Новое имя');
    expect(body.criteria[0]!.weight).toBe(draft.criteria[0]!.weight);
    expect(body.criteria[0]!.options[0]!.quality).toBe(draft.criteria[0]!.options[0]!.quality);
    expect(body.criteria[0]!.options.map(option => option.value)).toEqual([true, false]);
  });

  it('поддерживает C09 и последующие критерии без фиксированного лимита', () => {
    const draft = toDraft(historicalChecklist);
    for (let index = 9; index <= 12; index++) {
      const id = nextId('C', draft.criteria.map(item => item.id));
      expect(id).toBe(`C${String(index).padStart(2, '0')}`);
      const criterion = newCriterion(id, draft.sections[0]!.id);
      criterion.title = 'Синтетический критерий'; criterion.description = 'Проверка оператора';
      criterion.options.forEach(option => { option.description = 'Синтетическое описание'; });
      draft.criteria.push(criterion);
    }
    expect(validateDraft(draft)).toEqual([]);
    expect(serializeDraft(draft).criteria).toHaveLength(12);
  });

  it.each(['boolean', 'scale', 'choice'] as const)('смена типа на %s задаёт совместимые варианты и требует описания', type => {
    const draft = toDraft(historicalChecklist);
    draft.criteria[0] = changeAnswerType(draft.criteria[0]!, type);
    expect(validateDraft(draft).some(issue => issue.field.endsWith('description'))).toBe(true);
    draft.criteria[0].options.forEach(option => { option.description = 'Новое описание'; });
    const values = serializeDraft(draft).criteria[0]!.options.map(option => option.value);
    expect(values.every(value => typeof value === (type === 'boolean' ? 'boolean' : type === 'scale' ? 'number' : 'string'))).toBe(true);
  });

  it('смена scored/flag сбрасывает несовместимые поля и поддерживает flagged', () => {
    const draft = toDraft(historicalChecklist);
    draft.criteria[0] = changeKind(draft.criteria[0]!, 'flag');
    draft.criteria[0].options[0]!.flagged = true;
    expect(serializeDraft(draft).criteria[0]).toMatchObject({ weight: null, kind: 'flag', options: [
      { quality: null, flagged: true }, { quality: null, flagged: false },
    ] });
    draft.criteria[0] = changeKind(draft.criteria[0], 'scored');
    expect(validateDraft(draft).map(issue => issue.field)).toContain('criteria.0.options.0.quality');
    expect(draft.criteria[0].options[0]!.flagged).toBe(false);
  });

  it('проверяет уникальность ID, ссылки на секции и обязательное условие N/A', () => {
    const draft = toDraft(historicalChecklist);
    draft.criteria[1]!.id = ` ${draft.criteria[0]!.id} `;
    draft.sections[1]!.id = draft.sections[0]!.id;
    draft.criteria[0]!.section_id = 'missing';
    draft.criteria[0]!.allow_na = true; draft.criteria[0]!.na_condition = ' ';
    expect(validateDraft(draft).map(issue => issue.field)).toEqual(expect.arrayContaining([
      'criteria.1.id', 'sections.1.id', 'criteria.0.section_id', 'criteria.0.na_condition',
    ]));
    expect(() => serializeDraft(draft)).toThrow();
  });

  it('N/A является настройкой критерия, а не добавленным вариантом', () => {
    const draft = toDraft(historicalChecklist);
    draft.criteria[0]!.allow_na = true; draft.criteria[0]!.na_condition = '  Не требуется проверка  ';
    expect(serializeDraft(draft).criteria[0]).toMatchObject({ allow_na: true, na_condition: 'Не требуется проверка' });
    draft.criteria[0]!.allow_na = false; draft.criteria[0]!.na_condition = '';
    expect(serializeDraft(draft).criteria[0]!.na_condition).toBeNull();
  });

  it.each(['NaN', 'Infinity', '-1', '-1e-400', 'abc', ''])('отклоняет недопустимый вес %s', weight => {
    const draft = toDraft(historicalChecklist); draft.criteria[0]!.weight = weight;
    expect(validateDraft(draft).map(issue => issue.field)).toContain('criteria.0.weight');
  });

  it.each(['-0.1', '1.01', '1.0000000000000000001', '-1e-400', 'Infinity', ''])('отклоняет недопустимый quality %s', quality => {
    const draft = toDraft(historicalChecklist); draft.criteria[0]!.options[0]!.quality = quality;
    expect(validateDraft(draft).map(issue => issue.field)).toContain('criteria.0.options.0.quality');
  });

  it('проверяет Decimal вне диапазона Number без округления и underflow', () => {
    const draft = toDraft(historicalChecklist);
    draft.criteria[0]!.weight = '1e400';
    draft.criteria[0]!.options[0]!.quality = '1e-400';
    draft.criteria[0]!.options[1]!.quality = '+0.1000e1';
    expect(validateDraft(draft)).toEqual([]);
    expect(serializeDraft(draft).criteria[0]).toMatchObject({ weight: '1e400', options: [{ quality: '1e-400' }, { quality: '+0.1000e1' }] });
    draft.criteria[0]!.options[1]!.quality = '0.10000000000000000001e1';
    expect(validateDraft(draft).map(issue => issue.field)).toContain('criteria.0.options.1.quality');
  });

  it('проверяет scale: числовые дубликаты 7/7.0, конечность и положительный максимум', () => {
    const draft = toDraft(historicalChecklist);
    const criterion = changeAnswerType(draft.criteria[0]!, 'scale'); draft.criteria[0] = criterion;
    criterion.options[0]!.value = '7'; criterion.options[1]!.value = '7.0';
    expect(validateDraft(draft).map(issue => issue.field)).toContain('criteria.0.options.1.value');
    criterion.options[0]!.value = '-1'; criterion.options[1]!.value = '0';
    expect(validateDraft(draft).map(issue => issue.field)).toContain('criteria.0.answer_type');
    criterion.options[1]!.value = 'Infinity';
    expect(validateDraft(draft).map(issue => issue.field)).toContain('criteria.0.options.1.value');
  });

  it('проверяет boolean и choice без смешивания типов и пустых вариантов', () => {
    const draft = toDraft(historicalChecklist);
    draft.criteria[0]!.options[0]!.value = 'yes';
    expect(validateDraft(draft).map(issue => issue.field)).toContain('criteria.0.answer_type');
    draft.criteria[0] = changeAnswerType(draft.criteria[0]!, 'choice');
    draft.criteria[0].options[1]!.value = draft.criteria[0].options[0]!.value;
    expect(validateDraft(draft).map(issue => issue.field)).toContain('criteria.0.options.1.value');
    draft.criteria[0].options[1]!.value = '';
    expect(validateDraft(draft).map(issue => issue.field)).toContain('criteria.0.options.1.value');
  });
});
