export function correctionCandidates(checks) {
  return (checks?.pages || []).flatMap(page => [...(page.traditional || []), ...(page.typos || [])]);
}

export function defaultCorrections(candidates) {
  return candidates.filter(item => item.kind === 'traditional' && item.suggested
    && !item.ambiguous && !item.require_review && !item.special_verify).map(item => item.candidate_id);
}

export function correctedPreview(pages, candidates, selected) {
  const chosen = new Set(selected);
  return pages.map(page => {
    const chars = Array.from(page.text || ''); // API offsets count Unicode code points.
    const edits = candidates.filter(item => chosen.has(item.candidate_id) && item.page_no === page.page_no)
      .sort((a, b) => b.start - a.start);
    let previous = chars.length;
    for (const item of edits) {
      if (item.end > previous) throw new Error('校正项重叠，请分别确认');
      if (chars.slice(item.start, item.end).join('') !== item.original) throw new Error('正文已变化，请重新读取候选');
      chars.splice(item.start, item.end - item.start, ...Array.from(item.suggested));
      previous = item.start;
    }
    return page.page_no == null || pages.length === 1 ? chars.join('') : `第${page.page_no}页\n${chars.join('')}`;
  }).join('\n\n');
}
