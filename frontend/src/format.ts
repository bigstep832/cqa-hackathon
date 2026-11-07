export function formatDecimal(value: string | number | null | undefined, digits = 2): string {
  if (value === null || value === undefined) return '—';
  const numeric = Number(value);
  return Number.isFinite(numeric)
    ? numeric.toLocaleString('ru-RU', { maximumFractionDigits: digits, minimumFractionDigits: digits })
    : '—';
}
