export function operatorHref(operatorId: string) {
  return operatorId ? '#operators/' + encodeURIComponent(operatorId) : '#operators';
}

export function operatorRoute(hash = location.hash): string | null {
  if (hash === '#operators') return '';
  if (!hash.startsWith('#operators/')) return null;
  try { return decodeURIComponent(hash.slice('#operators/'.length)); } catch { return ''; }
}
