// Excerpt identity is derived before asynchronous location requests, never from boxes.
export function citationChoices(item, assessment) {
  const values = new Map();
  const add = (quote, label) => {
    if (typeof quote === 'string' && quote && item.content?.includes(quote) && !values.has(quote)) {
      const ordinal = values.size + 1;
      values.set(quote, { quote, label, excerptId: item.evidence_id + ':quote:' + ordinal,
        subcitation: item.metadata?.citation ? item.metadata.citation + '.' + ordinal : String(ordinal) });
    }
  };
  for (const [index, formula] of (assessment?.formulas || []).entries()) {
    const matches = proof => proof && (proof.chunk_id === (item.metadata?.chunk_id || item.evidence_id) ||
      item.evidence_id.startsWith(proof.chunk_id + '-range-'));
    if (matches(formula.source)) add(formula.expression, '公式 ' + (index + 1));
    for (const parameter of formula.inputs || []) {
      if (matches(parameter.definition)) add(parameter.definition.quote, parameter.symbol + ' 的定义');
      for (const value of parameter.candidates || []) if (matches(value.source)) add(value.source.quote, parameter.symbol + ' 的取值');
    }
  }
  for (const quote of item.metadata?.quotes || []) add(quote, '引用摘录');
  if (!values.size && item.content) add(item.content, '整段引用');
  return [...values.values()];
}

export function validRegion(box) {
  return ['pdf_character_match', 'ocr_line_match', 'region_store_line'].includes(box.method) &&
    ['left', 'top', 'width', 'height'].every(key => Number.isFinite(box[key])) &&
    box.left >= 0 && box.top >= 0 && box.width > 0 && box.height > 0 &&
    box.left + box.width <= 100 && box.top + box.height <= 100;
}

export function locationSpan(source, quote) {
  const text = source.highlight || '';
  const offset = text.indexOf(quote);
  if (!quote || offset < 0 || text.indexOf(quote, offset + 1) >= 0 || !Number.isInteger(source.start_char)) return {};
  const start = source.start_char + Array.from(text.slice(0, offset)).length;
  return {start_char:start, end_char:start + Array.from(quote).length};
}

export async function locateChoices(choices, request) {
  const excerpts = await Promise.all(choices.map(async choice => {
    try {
      if (choice.quote.length > 4000) throw new Error('引用超过坐标定位长度上限。');
      const position = await request(choice.quote);
      if (position.position_status==='located' && (position.highlight_boxes || []).some(box=>!validRegion(box))) {
        throw new Error('部分区域坐标无效，无法可靠完整定位该摘录。');
      }
      const unique = new Map();
      for (const box of position.position_status === 'located' ? position.highlight_boxes || [] : []) {
        if (validRegion(box)) unique.set(JSON.stringify([box.left, box.top, box.width, box.height, box.method]), { ...box, ...choice });
      }
      const boxes = [...unique.values()];
      return { ...choice, boxes, status: boxes.length ? 'located' : 'text_only',
        reason_code:position.reason_code, text_status:position.text_status, heading_status:position.heading_status,
        method:position.method, matched_chars:position.matched_chars, total_chars:position.total_chars,
        note: boxes.length ? '' : position.position_note || '无法可靠定位原页区域。' };
    } catch (error) {
      return { ...choice, boxes: [], status: 'error', reason_code:'request_failed', note: '定位请求失败：' + (error.message || '原页坐标接口暂不可用。') };
    }
  }));
  const missing = excerpts.filter(excerpt => excerpt.status !== 'located');
  return { excerpts, boxes: excerpts.flatMap(excerpt => excerpt.boxes),
    status: missing.length || !excerpts.length ? 'text_only' : 'located',
    note: missing.length ? missing.map(excerpt => '[' + excerpt.subcitation + '] ' + excerpt.note).join(' ') : '已按真实字符或OCR行坐标标注。' };
}

// Shared by one panel, so expanding several documents cannot fan out unbounded requests.
export function createRequestQueue(limit = 4) {
  let active = 0;
  const pending = [];
  const start = () => {
    while (active < limit && pending.length) {
      const { task, resolve, reject } = pending.shift();
      active++;
      Promise.resolve().then(task).then(resolve, reject).finally(() => { active--; start(); });
    }
  };
  return task => new Promise((resolve, reject) => { pending.push({ task, resolve, reject }); start(); });
}
