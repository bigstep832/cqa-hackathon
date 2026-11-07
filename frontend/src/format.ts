export function formatDecimal(value: string | number | null | undefined, digits = 2): string {
  if (value === null || value === undefined) return '—';
  const numeric = Number(value);
  return Number.isFinite(numeric)
    ? numeric.toLocaleString('ru-RU', { maximumFractionDigits: digits, minimumFractionDigits: digits })
    : '—';
}

// Weights are server Decimal strings. Localize without rounding through Number;
// score formatting above retains its existing precision.
export function formatWeight(value: string | number | null | undefined): string {
  if (value == null) return '—';
  const text = String(value).trim();
  if (!/^[+-]?(?:\d+\.?\d*|\.\d+)(?:[eE][+-]?\d+)?$/.test(text)) return '—';
  return text.replace(/(\.\d*?)0+(?=[eE]|$)/, '$1').replace(/\.(?=[eE]|$)/, '').replace('.', ',');
}
