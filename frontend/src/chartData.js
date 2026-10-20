// chartData.js — pure, testable transform for 问数图表.
//
// Turns a read-only SQL execution result (`{columns, rows, truncated,
// row_count, partial, column_labels, ...}`) into a chart plan:
//   - eligibility gating (which results may enter the chart branch at all),
//   - column classification (dimension vs metric, identifier/boolean/empty
//     exclusion, full numeric-string parsing, NaN/Infinity/empty handling),
//   - bar vs pie rules (zero/negative/duplicates/units/coverage),
//   - default dimension/metric auto-selection.
//
// No Vue, no DOM. Importable from node (""*.mjs"" tests) and from the browser.
// Chart values come from the current result verbatim; the module never invents
// rows or aggregates them silently.

// Categorical slots (fixed order, from the project dataviz palette).
// Identity is carried by the slot, never by hue cycling; a fold-in slot for
// categories beyond the 8th.
export const CATEGORICAL_SLOTS = [
  '#2a78d6', '#eb6834', '#1baf7a', '#eda100',
  '#e87ba4', '#008300', '#6250d6', '#e34948',
];
export const OTHER_SLOT = '#898781';

// ---- terminal records ----------------------------------------------------

const SUCCESS_STATUSES = new Set(['completed', 'partial_answer']);

/** Chart-branch gate. Only read-only querying results reach the chart.
 *  Document summaries, formula/-symbol/raw parameter extracts, failed and
 *  clarification responses must never enter the chart branch. */
export function chartEligible(result) {
  if (!result || typeof result !== 'object') return { eligible: false, reason: '无结果' };
  if (!['sql', 'hybrid'].includes(result.task_type)) {
    return { eligible: false, reason: `非问数任务（${result.task_type || '未知'}）` };
  }
  if (!SUCCESS_STATUSES.has(result.status)) {
    return { eligible: false, reason: `任务未给出可展示结果（${result.status || '未知'}）` };
  }
  const data = result.data;
  if (!data || !Array.isArray(data.columns) || !data.columns.length || !Array.isArray(data.rows)) {
    return { eligible: false, reason: '查询未返回结构化的结果行' };
  }
  if (['formula', 'symbol', 'raw_values'].every((key) => data.columns.includes(key))) {
    return { eligible: false, reason: '参数提取结果，不属于图表范围' };
  }
  if (data.rows.length === 0) {
    return { eligible: false, reason: '查询结果为空，无数据可绘制' };
  }
  return { eligible: true, reason: '' };
}

// ---- value parsing -----------------------------------------------------

const CURRENCY_RE = /^[￥¥$€£]|[￥¥$€£]$/;
const PERCENT_RE = /([%％])$/;
const THOUSANDS_COMMA_RE = /,/g;
const BAD_VALUE_WORDS = /^(无数据|不适用|n\/a|na|--+|—|-|－|空|无)$/i;

/** Fully parse a numeric string into a finite number; null for empty/bad,
 *  NaN/Infinity excluded. Marks percent-kind when the value literally ends
 *  with `%`. Currency and thousands separators are stripped; a residual
 *  non-numeric word means the cell is not a number. */
export function parseNumeric(value) {
  if (value === null || value === undefined) return { value: null, kind: null, raw: value };
  if (typeof value === 'number') {
    return Number.isFinite(value)
      ? { value, kind: null, raw: value }
      : { value: null, kind: null, raw: value }; // NaN / Infinity excluded
  }
  if (typeof value === 'boolean') return { value: null, kind: null, raw: value };
  let text = String(value).trim();
  if (!text || BAD_VALUE_WORDS.test(text)) return { value: null, kind: null, raw: value };
  let kind = null;
  const percent = text.match(PERCENT_RE);
  if (percent) { kind = 'percent'; text = text.slice(0, -1).trim(); }
  text = text.replace(CURRENCY_RE, '').replace(THOUSANDS_COMMA_RE, '').trim();
  if (!text) return { value: null, kind: null, raw: value };
  const parsed = Number(text);
  if (!Number.isFinite(parsed)) return { value: null, kind: null, raw: value };
  return { value: parsed, kind, raw: value };
}

const IDENTIFIER_HINTS = [
  /^id$/i, /_id$/i, /^id_/i, /id\s*$/i, /^编号/, /(^-?\d+$)/
];
const DATE_HINTS = [
  /月$/, /^月份/, /日期/, /date$/i, /^年份/, /^year$/i, /年度/, /季度/, /时间/,
];

/** Classification of one column.
 *  - `identifier`   name obviously an id (CustomerId…)
 *  - `boolean`      all non-null values are booleans
 *  - `empty`        every value is null/empty/“无数据”
 *  - `metric`       parseable as numbers for enough cells; kept as number column
 *  - `dimension`    labels/categories/dates/result_status
 * Missing markers (null, empty, “无数据/不适用/—”) are recorded as missing cells
 * and never count against a metric’s numeric ratio. Returns
 * {key, label, kind, metric?, reason?, missingCells, emptyCells…}. */
export function classifyColumn(key, label, rows) {
  const labelText = label || key || '';
  const probe = String(key || '') + ' ' + labelText;
  let parsed = 0, nonNumeric = 0, missing = 0, booleanCount = 0;
  let sawPercent = false;
  let numericOnly = true;
  for (const row of rows) {
    const raw = row[key];
    const out = parseNumeric(raw);
    if (out.value !== null) {
      parsed += 1;
      if (out.kind === 'percent') sawPercent = true;
      continue;
    }
    if (raw === null || raw === undefined || raw === ''
        || (typeof raw === 'string' && BAD_VALUE_WORDS.test(raw.trim()))) {
      missing += 1;
      continue;
    }
    if (typeof raw === 'boolean') { booleanCount += 1; continue; }
    nonNumeric += 1; // unparseable content (NaN/Infinity or a real word)
    numericOnly = false;
  }
  const emptyAll = parsed === 0 && nonNumeric === 0 && booleanCount === 0;
  const isIdentifier = IDENTIFIER_HINTS.some((re) => re.test(probe));
  const isDate = DATE_HINTS.some((re) => re.test(probe));
  const isOnlyBooleans = booleanCount > 0 && parsed === 0 && nonNumeric === 0;

  if (emptyAll) return { key, label, kind: 'skip', reason: '空值', missingCells: missing };
  if (isIdentifier) return { key, label, kind: 'skip', reason: '标识符' };
  if (isOnlyBooleans) return { key, label, kind: 'skip', reason: '布尔值' };
  if (isDate) return { key, label, kind: 'dimension' };

  if (parsed > 0) {
    const usable = parsed + nonNumeric;
    const ratio = usable === 0 ? 0 : parsed / usable;
    if (ratio >= 0.8) {
      return {
        key, label,
        kind: 'metric',
        metric: { kind: sawPercent ? 'percent' : 'number', unparsedCells: nonNumeric, missingCells: missing },
      };
    }
  }
  if (numericOnly && nonNumeric > 0 && parsed === 0) {
    return { key, label, kind: 'skip', reason: 'NaN/Infinity/不可解析' };
  }
  return { key, label, kind: 'dimension' };
}

/** Full classification of a result's columns for charting. */
export function classifyColumns(data) {
  const dimensions = [], metrics = [], skipped = [];
  for (const key of data.columns || []) {
    const label = data.column_labels?.[key] || key;
    const cls = classifyColumn(key, label, data.rows || []);
    if (cls.kind === 'metric') metrics.push(cls);
    else if (cls.kind === 'dimension') dimensions.push(cls);
    else skipped.push({ key, label, reason: cls.reason });
  }
  return { dimensions, metrics, skipped };
}

// ---- chart plan ---------------------------------------------------------

let syntheticCounter = 0;

/** Build the renderable chart plan for one query result.
 *  opts: { dimension, metric } for an explicit user pick (defaults otherwise). */
export function buildChartPlan(data, opts = {}) {
  const { dimensions, metrics, skipped } = classifyColumns(data);
  if (metrics.length === 0) {
    return {
      eligible: false,
      reason: metrics.length === 0
        ? '结果中没有可量化的数值列，无法生成图表，请查看下方表格'
        : '缺少维度列',
      metrics, dimensions, skipped,
    };
  }
  const dimensionKey = opts.dimension
    && dimensions.some((d) => d.key === opts.dimension) ? opts.dimension : (dimensions[0]?.key ?? null);
  const metricKey = opts.metric
    && metrics.some((m) => m.key === opts.metric) ? opts.metric : metrics[0].key;
  const dimension = dimensions.find((d) => d.key === dimensionKey) || null;
  const metric = metrics.find((m) => m.key === metricKey) || metrics[0];
  if (!dimension) {
    // No natural category: label rows by position (a single-dimension table).
  }
  const synthetic = !dimension;
  const label = (row, index) => {
    if (!dimension) return `第 ${index + 1} 行`;
    const raw = row[dimension.key];
    if (raw === null || raw === undefined || raw === '') return '(空)';
    return String(raw);
  };

  // Prepare one record per source row — duplicate categories stay independent.
  const records = [];
  let validTotal = 0, total = 0;
  for (let i = 0; i < data.rows.length; i++) {
    const row = data.rows[i];
    const parsed = parseNumeric(row[metric.key]);
    total += 1;
    const category = label(row, i);
    if (parsed.value === null) { records.push({ index: i, category, value: null, valid: false }); continue; }
    records.push({ index: i, category, value: parsed.value, valid: true, percent: parsed.kind === 'percent' });
    validTotal += parsed.value;
  }
  const hasInvalid = records.some((r) => !r.valid);
  const values = records.filter((r) => r.valid).map((r) => r.value);
  const maxValue = values.length ? Math.max(...values) : 0;
  const minValue = values.length ? Math.min(...values) : 0;
  const anyNegative = values.some((v) => v < 0);
  const allZeroOrNegative = values.length > 0 && values.every((v) => v <= 0);

  // Units / aggregation clarity.
  const unitKnown = Boolean(opts.unit);
  const ambiguousUnit = !unitKnown && metric.metric.kind !== 'percent';

  const bar = {
    ok: true,
    why: anyNegative ? '柱状图支持负数（0 轴以下延伸）' : '',
  };

  const pie = pieCheck({ records, values, validTotal, metric, ambiguousUnit });
  const duplicates = hasDuplicateCategories(records);

  const coverage = buildCoverage(data, records, validTotal);

  return {
    eligible: true,
    dimension,
    metric,
    dimensionOptions: dimensions,
    metricOptions: metrics,
    records,
    synthetic,
    bar,
    pie,
    duplicates,
    coverage,
    unitKnown,
    ambiguousUnit,
    skipped,
    aggregate: { validTotal, count: values.length, max: maxValue, min: minValue },
    // Chart values always originate from the current read-only result:
    trace: { source: 'sql_result', rows: values.length, total },
    title: opts.title || defaultTitle(metric, data),
  };
}

function defaultTitle(metric, data) {
  return metric.label ? `${metric.label}对比` : '问数结果图表';
}

function hasDuplicateCategories(records) {
  const seen = new Map();
  const ordered = [];
  for (const r of records) {
    const count = seen.get(r.category) || 0;
    if (!seen.has(r.category)) ordered.push(r.category);
    seen.set(r.category, count + 1);
  }
  return { has: [...seen.values()].some((c) => c > 1), ordered, counts: seen };
}

function buildCoverage(data, records, validTotal) {
  const parts = [];
  if (data.truncated && data.row_count != null) {
    parts.push(`结果超过 ${data.row_count} 行被截断，仅展示前 ${data.rows.length} 行（${displayFraction(data.rows.length, data.row_count)}），不能视为完整结果。`);
  }
  if (data.partial) {
    parts.push('存在缺失或不可计算的行，图表仅包含有效数值；比例类图例标注“当前展示数据占比”。');
  }
  const invalid = records.filter((r) => !r.valid).length;
  if (invalid > 0) {
    parts.push(`${invalid} 行缺少有效数值，未参与图表。`);
  }
  return parts;
}

function displayFraction(shown, total) {
  if (!total) return '0%';
  return `${Math.round((shown / total) * 1000) / 10}%`;
}

function pieCheck({ records, values, validTotal, ambiguousUnit }) {
  const valid = records.filter((r) => r.valid);
  if (valid.length === 0) return { ok: false, reason: '没有非空数值' };
  if (valid.some((r) => r.value < 0)) return { ok: false, reason: '存在负数，饼图比例无意义（可查看柱状图）' };
  if (validTotal <= 0) return { ok: false, reason: '数值总和为 0，无法形成占比' };
  if (ambiguousUnit) {
    return { ok: false, reason: '各数值单位/聚合含义不明确，不宜按占比展示' };
  }
  if (valid.some((r) => r.percent)) {
    return { ok: true, note: '该列为占比/率值，占比含义以原查询口径为准' };
  }
  return { ok: true, note: '' };
}

// ---- display helpers ----------------------------------------------------

export function formatValue(value, kind) {
  if (value === null || value === undefined) return '—';
  const abs = Math.abs(value);
  const digits = abs >= 1000 ? 0 : abs >= 10 ? 1 : abs >= 1 ? 2 : Math.max(2, Math.min(4, -Math.floor(Math.log10(abs))) + 2);
  const formatted = new Intl.NumberFormat('zh-CN', { maximumFractionDigits: Math.min(digits, 4) }).format(value);
  return kind === 'percent' ? `${formatted}%` : formatted;
}