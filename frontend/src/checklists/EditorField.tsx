import type { ReactNode } from 'react';
import { fieldId, type FieldIssue } from './draft';
type Props = { path: string; label: string; issues: FieldIssue[]; children?: ReactNode };
export function EditorField({ path, label, issues, value, onChange, multiline = false, inputMode }: Props & {
  value: string; onChange: (value: string) => void; multiline?: boolean; inputMode?: 'decimal';
}) {
  const issue = issues.find(item => item.field === path);
  const props = { id: fieldId(path), value, 'aria-invalid': Boolean(issue),
    'aria-describedby': issue ? `${fieldId(path)}-error` : undefined,
    onChange: (event: React.ChangeEvent<HTMLInputElement | HTMLTextAreaElement>) => onChange(event.target.value),
  };
  return <div className="editor-field"><label htmlFor={fieldId(path)}>{label}</label>
    {multiline ? <textarea {...props} rows={3} /> : <input {...props} type="text" inputMode={inputMode} />}
    {issue && <p id={`${fieldId(path)}-error`} className="field-error">{issue.message}</p>}
  </div>;
}
export function EditorSelect({ path, label, issues, value, onChange, children }: Props & {
  value: string; onChange: (value: string) => void;
}) {
  const issue = issues.find(item => item.field === path);
  return <div className="editor-field"><label htmlFor={fieldId(path)}>{label}</label>
    <select id={fieldId(path)} value={value} onChange={event => onChange(event.target.value)}
      aria-invalid={Boolean(issue)} aria-describedby={issue ? `${fieldId(path)}-error` : undefined}>{children}</select>
    {issue && <p id={`${fieldId(path)}-error`} className="field-error">{issue.message}</p>}
  </div>;
}
