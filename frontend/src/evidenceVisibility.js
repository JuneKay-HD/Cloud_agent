// Temporary presentation default. Actual locations/errors remain untouched.
export const DEFAULT_SHOW_EVIDENCE_FAILURES = false;

export function visibleLocationExcerpts(excerpts, showFailures) {
  return (excerpts || []).filter(excerpt => showFailures !== false || excerpt.status === 'located');
}

export function visibleLocationChoices(choices, location, showFailures) {
  if (showFailures !== false || !location) return choices;
  const located = new Set(visibleLocationExcerpts(location.excerpts, false).map(excerpt => excerpt.excerptId));
  return choices.filter(choice => located.has(choice.excerptId));
}

export function visibleLocationNote(location, showFailures) {
  return !!location && (showFailures !== false || location.status === 'located');
}
