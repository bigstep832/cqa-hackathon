import { demo } from './evaluationFixtures';
import type { EvaluationResult } from '../src/api/evaluation';

// Explicit synthetic response variations. Totals are supplied fixture values, never computed here.
export function chartFixture(): EvaluationResult {
  const data = structuredClone(demo);
  data.evaluation.effective_score = structuredClone(data.evaluation.original_score!);
  data.evaluation.effective_score.total = '70.00';
  data.evaluation.effective_score.criteria.find(item => item.criterion_id === 'C01')!.earned_points = '0';
  data.evaluation.effective_score.criteria.find(item => item.criterion_id === 'C01')!.quality = '0';
  return data;
}
export function extendedChartFixture(): EvaluationResult {
  const data = chartFixture();
  data.checklist.criteria.push({ ...data.checklist.criteria[0]!, id: 'C09', title: 'Длинное название дополнительного критерия · Қызмет сапасы · Service quality '.repeat(3), weight: '0.25' });
  data.evaluation.original_score!.criteria.push({ ...data.evaluation.original_score!.criteria[0]!, criterion_id: 'C09', weight: '0.25', earned_points: '0.125', quality: '0.5' });
  data.evaluation.output!.answers.push({ ...data.evaluation.output!.answers[0]!, criterion_id: 'C09' });
  data.evaluation.original_score!.criteria.find(item => item.criterion_id === 'C08')!.flagged = true;
  return data;
}
