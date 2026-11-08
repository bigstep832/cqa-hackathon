import { useId, useState } from 'react';
import type { EvaluationResult } from './api/evaluation';
import type { components } from './api/generated/schema';
import { formatWeight } from './format';

type CriterionScore = components['schemas']['CriterionScore'];

function numeric(value: string | null | undefined): number | null {
  if (formatWeight(value) === '—') return null;
  const number = Number(value);
  return Number.isFinite(number) && number >= 0 ? number : null;
}

function ScoredBar({ label, points }: { label: string; points: CriterionScore | undefined }) {
  if (!points) return <span className="criteria-chart-status muted">Нет данных по критерию</span>;
  if (!points.applicable) return <span className="criteria-chart-status muted"><span className="tag">N/A</span> Исключён из расчёта</span>;
  const earned = numeric(points.earned_points), weight = numeric(points.weight);
  if (earned === null || weight === null || earned > weight) {
    return <span className="criteria-chart-status muted">Нет корректных данных для диаграммы</span>;
  }
  const value = formatWeight(points.earned_points) + ' / ' + formatWeight(points.weight);
  if (weight === 0) return <>
    <span className="criteria-chart-status muted">Нулевой вес · без доли в итоге</span>
    <span className="criteria-chart-value">{value}</span>
  </>;
  // Only geometry: no total, normalized points, quality or appeal score is computed here.
  const width = earned / weight * 100;
  return <>
    <div className="criteria-chart-track" role="meter" aria-label={label}
      aria-valuemin={0} aria-valuemax={weight} aria-valuenow={earned}
      aria-valuetext={formatWeight(points.earned_points) + ' из ' + formatWeight(points.weight) + ' баллов'}>
      <span className="criteria-chart-fill" style={{ width: width + '%' }} aria-hidden="true" />
    </div>
    <span className="criteria-chart-value">{value}</span>
  </>;
}

export function EvaluationCriteriaChart({ checklist, evaluation }: Pick<EvaluationResult, 'checklist' | 'evaluation'>) {
  const [mode, setMode] = useState<'original' | 'effective'>('original');
  const headingId = useId();
  if (evaluation.status !== 'succeeded' || !evaluation.original_score || !evaluation.effective_score) return null;
  const score = mode === 'original' ? evaluation.original_score : evaluation.effective_score;
  const scored = checklist.criteria.filter(criterion => criterion.kind === 'scored');
  const flags = checklist.criteria.filter(criterion => criterion.kind === 'flag');
  const scores = new Map(score.criteria.map(item => [item.criterion_id, item]));
  const allNa = scored.length > 0 && scored.every(criterion => scores.get(criterion.id)?.applicable === false);
  return <section className="panel criteria-chart" aria-labelledby={headingId}>
    <div className="panel-header criteria-chart-header">
      <div><h2 id={headingId}>Баллы по критериям</h2><p>Начислено / вес критерия · до нормализации итоговой оценки</p></div>
      <div className="criteria-chart-toggle" role="group" aria-label="Результат на диаграмме">
        <button type="button" aria-pressed={mode === 'original'} onClick={() => setMode('original')}>Исходные баллы</button>
        <button type="button" aria-pressed={mode === 'effective'} onClick={() => setMode('effective')}>Эффективные баллы</button>
      </div>
    </div>
    <div className="criteria-chart-body">
      <p className="criteria-chart-caption muted">{mode === 'original'
        ? 'Исходный результат. Длина полосы показывает долю выполнения критерия.'
        : 'Результат с учётом решений по апелляциям. Исходные баллы сохранены.'}</p>
      {checklist.criteria.length === 0 ? <p>В этой версии чек-листа нет критериев.</p> : <>
        {allNa && <p className="criteria-chart-empty muted">Все балльные критерии отмечены N/A.</p>}
        {scored.length === 0 && <p className="criteria-chart-empty muted">В этой версии нет балльных критериев.</p>}
        <ul className="criteria-chart-rows">{scored.map(criterion => {
          const label = criterion.id + ' · ' + criterion.title;
          return <li className="criteria-chart-row" key={criterion.id}>
            <span className="criteria-chart-label">{label}</span>
            <ScoredBar label={label} points={scores.get(criterion.id)} />
          </li>;
        })}</ul>
        {flags.length > 0 && <div className="criteria-chart-flags">
          <h3>Флаги · без баллов</h3>
          <p className="muted">Не изменяют общий балл.</p>
          <ul>{flags.map(criterion => {
            const points = scores.get(criterion.id);
            const flagged = points?.applicable && points.flagged;
            return <li key={criterion.id}><span>{criterion.id} · {criterion.title}</span>
              <span className={flagged ? 'tag criteria-chart-flagged' : 'tag'}>
                {!points ? 'Нет данных' : !points.applicable ? 'N/A' : flagged ? 'Обнаружен' : 'Не обнаружен'}
              </span>
            </li>;
          })}</ul>
        </div>}
      </>}
    </div>
  </section>;
}
