export function groupEvidence(evidence) {
  const groups = new Map();
  for (const item of evidence) {
    // Missing identity is never merged using a filename.
    const id = item.source_type === 'document' && item.metadata?.doc_id
      ? 'document:' + item.metadata.doc_id : item.source_type + ':' + item.evidence_id;
    if (!groups.has(id)) groups.set(id, { id, title: item.metadata?.filename || item.title, items: [] });
    groups.get(id).items.push(item);
  }
  return [...groups.values()];
}

export function sourcePageKey(groupId, item, source) {
  const candidate = source ? source.page_no : item.metadata?.page_no;
  const number = Number.isInteger(candidate) && candidate > 0 ? candidate : null;
  // Unknown page identities must not merge unrelated images.
  return JSON.stringify([groupId, number ?? item.evidence_id, source?.source_hash || null, source?.original_hash || null]);
}

export function groupSourcePages(group, sources, locations) {
  const pages = new Map();
  for (const item of group.items) {
    const source = sources[item.evidence_id];
    if (!source || !(source.page_image_url || source.source_image || source.formula_image)) continue;
    const key = sourcePageKey(group.id, item, source);
    if (!pages.has(key)) pages.set(key, { key, source, items: [], annotations: [], excerpts: [] });
    const page = pages.get(key);
    page.items.push(item);
    for (const excerpt of locations[item.evidence_id]?.excerpts || []) {
      page.excerpts.push({ ...excerpt, evidenceId: item.evidence_id, citation: item.metadata?.citation });
    }
    for (const mark of locations[item.evidence_id]?.boxes || []) {
      page.annotations.push({ ...mark, evidenceId: item.evidence_id, citation: item.metadata?.citation });
    }
  }
  const result=[...pages.values()];
  for (const page of result) {
    const peers=result.filter(other=>other!==page && Number.isInteger(page.source.page_no) && page.source.page_no>0 && other.source.page_no===page.source.page_no);
    if (peers.length) {
      page.versionConflict=true;
      page.annotations=[];
      page.excerpts=page.excerpts.map(excerpt=>({...excerpt,status:'text_only',note:'同页原件版本冲突，已阻断区域标注。'}));
    }
  }
  return result.sort((a,b) => (a.source.page_no ?? Infinity) - (b.source.page_no ?? Infinity));
}
