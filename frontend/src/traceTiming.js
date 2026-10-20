// Total request processing time; absent on historical replies that were not timed.
export function formatElapsed(milliseconds) {
  if (typeof milliseconds !== 'number' || !Number.isFinite(milliseconds) || milliseconds < 0) return '';
  return milliseconds < 1000 ? `${Math.round(milliseconds)}毫秒` : `${(milliseconds / 1000).toFixed(1)}秒`;
}
