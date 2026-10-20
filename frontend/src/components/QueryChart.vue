<script setup>
// QueryChart.vue — 问数结果图表（柱状图 / 饼图 / 表格切换）。
// 仅接收可读 SQL 执行结果；数据转换全部在 ../chartData.js 完成，本组件只负责渲染。
// 图表数值一律来自当前只读结果，不虚构、不隐式聚合；截断/部分结果带覆盖率说明。
import { computed, ref } from 'vue';
import {
  chartEligible, buildChartPlan, formatValue,
  CATEGORICAL_SLOTS, OTHER_SLOT,
} from '../chartData.js';

const props = defineProps({
  data: { type: Object, default: null },
  // 来自 AgentResponse.query_conditions 的聚合/单位语义（可为空）。
  hints: { type: Object, default: () => ({}) },
});

const selectedMetric = ref(null);
const selectedDimension = ref(null);
const chartType = ref('bar'); // 'bar' | 'pie' | 'table'
const tableOpen = ref(false);

const gate = computed(() => chartEligible({ task_type: 'sql', status: 'completed', data: props.data }));
const plan = computed(() => {
  if (!gate.value.eligible || !props.data) return null;
  return buildChartPlan(props.data, {
    unit: props.hints?.unit,
    metric: selectedMetric.value || undefined,
    dimension: selectedDimension.value || undefined,
    title: props.hints?.title,
  });
});

function choose(option) {
  if (option && plan.value) {
    selectedMetric.value = option.key;
    chartType.value = plan.value.pie.ok ? 'pie' : 'bar';
  }
}
function chooseDimension(option) {
  if (option) selectedDimension.value = option.key;
}

const validRecords = computed(() => (plan.value?.records || []).filter((r) => r.valid));
const sliceColor = (index, total) => {
  if (total <= 8) return CATEGORICAL_SLOTS[index % CATEGORICAL_SLOTS.length];
  return index < CATEGORICAL_SLOTS.length ? CATEGORICAL_SLOTS[index] : OTHER_SLOT;
};

// ---- bar layout ----------------------------------------------------------
const BAR_W = 700, BAR_H = 300, BAR_PAD_T = 30, BAR_PAD_B = 46, BAR_PAD_L = 12, BAR_PAD_R = 16;
function barGeometry() {
  const rows = validRecords.value;
  const values = rows.map((r) => r.value);
  let min = Math.min(0, ...values);
  let max = Math.max(0, ...values);
  if (max === min) { if (max > 0) max += 1; else if (max < 0) min -= 1; else max = 1; }
  const headroom = (max - min) * 0.12;
  if (max > 0) max += headroom;
  if (min < 0) min -= headroom;
  const span = max - min;
  const plotH = BAR_H - BAR_PAD_T - BAR_PAD_B;
  const plotW = BAR_W - BAR_PAD_L - BAR_PAD_R;
  const yOf = (v) => BAR_PAD_T + plotH * (1 - (v - min) / span);
  const slotW = plotW / Math.max(1, rows.length);
  const barW = Math.min(80, Math.max(3, slotW * 0.62));
  const zeroY = yOf(0);
  const baseline = zeroY >= BAR_PAD_T && zeroY <= BAR_PAD_T + plotH;
  return { rows, min, max, span, plotH, plotW, yOf, slotW, barW, zeroY, baseline, x(i) { return BAR_PAD_L + slotW * i + (slotW - barW) / 2; } };
}
const bars = computed(() => {
  if (chartType.value !== 'bar' || !plan.value) return null;
  const g = barGeometry();
  const showLabels = g.rows.length <= 12;
  const labelStep = Math.max(1, Math.ceil(g.rows.length / 10));
  return {
    ...g,
    showLabels,
    labelStep,
    marks: g.rows.map((r, i) => ({
      index: i, x: g.x(i), y: g.yOf(r.value), h: Math.abs(g.yOf(r.value) - g.zeroY),
      value: r.value, category: r.category, label: r.category,
      tick: i % labelStep === 0,
    })),
  };
});

// ---- pie layout ----------------------------------------------------------
const PIE_CX = 180, PIE_CY = 170, PIE_R = 106;
function polar(cx, cy, r, angleDeg) {
  const a = ((angleDeg - 90) * Math.PI) / 180;
  return [cx + r * Math.cos(a), cy + r * Math.sin(a)];
}
function sliceD(cx, cy, r, start, end) {
  if (end - start >= 359.99) return null; // full circle — render <circle> instead
  const large = end - start > 180 ? 1 : 0;
  const [x1, y1] = polar(cx, cy, r, start);
  const [x2, y2] = polar(cx, cy, r, end);
  return `M ${cx} ${cy} L ${x1} ${y1} A ${r} ${r} 0 ${large} 1 ${x2} ${y2} Z`;
}
const pie = computed(() => {
  if (chartType.value !== 'pie' || !plan.value || !plan.value.pie.ok) return null;
  const rows = validRecords.value;
  const total = rows.reduce((sum, r) => sum + r.value, 0);
  let slices = [];
  let folded = 0;
  rows.forEach((r, i) => {
    if (rows.length > 8 && i >= 8) { folded += r.value; return; }
    slices.push({ category: r.category, value: r.value, index: i });
  });
  let angle = 0;
  const built = slices.map((s) => {
    const start = angle;
    const sweep = (s.value / total) * 360;
    angle += sweep;
    return { ...s, start, sweep, percent: total > 0 ? (s.value / total) * 100 : 0, color: sliceColor(s.index, rows.length) };
  });
  if (folded > 0) {
    built.push({ category: `其他 ${rows.length - 8} 项`, value: folded, index: CATEGORICAL_SLOTS.length,
      start: angle, sweep: 360 - angle, percent: total > 0 ? (folded / total) * 100 : 0, color: OTHER_SLOT, folded: true });
  }
  const showLabels = built.length <= 8;
  return { total, built, showLabels, cx: PIE_CX, cy: PIE_CY, r: PIE_R };
});
function pieLabel(slice) {
  const mid = slice.start + slice.sweep / 2;
  const [x1, y1] = polar(pie.value.cx, pie.value.cy, pie.value.r, mid);
  const [lx, ly] = polar(pie.value.cx, pie.value.cy, pie.value.r + 26, mid);
  return { x1, y1, lx, ly, text: slice.percent >= 0.1 ? `${slice.percent.toFixed(1)}%` : '<0.1%' };
}

const summaryLabel = computed(() => {
  const planValue = plan.value;
  if (!planValue) return '';
  return `${planValue.title}：维度 ${planValue.dimension ? planValue.dimension.label : '行号'}，指标 ${planValue.metric.label}`;
});
</script>

<template>
  <section class="query-chart" aria-label="问数结果图表">
    <div v-if="!gate.eligible || !plan" class="chart-note">{{ gate.eligible ? '当前结果无法生成图表，请查看下方表格。' : gate.reason }}</div>

    <template v-else>
      <header class="chart-head">
        <h3 class="chart-title">{{ plan.title }}</h3>
        <div class="chart-tools">
          <label class="chart-select">
            <span>维度</span>
            <select v-if="plan.dimensionOptions.length > 1" :value="selectedDimension || plan.dimension?.key" @change="chooseDimension(plan.dimensionOptions.find(o => o.key === $event.target.value))">
              <option v-for="option in plan.dimensionOptions" :key="option.key" :value="option.key">{{ option.label }}</option>
            </select>
            <span v-else class="chart-fixed">{{ plan.dimension ? plan.dimension.label : '行号' }}</span>
          </label>
          <label class="chart-select">
            <span>指标</span>
            <select v-if="plan.metricOptions.length > 1" :value="selectedMetric || plan.metric.key" @change="choose(plan.metricOptions.find(o => o.key === $event.target.value))">
              <option v-for="option in plan.metricOptions" :key="option.key" :value="option.key">{{ option.label }}</option>
            </select>
            <span v-else class="chart-fixed">{{ plan.metric.label }}</span>
          </label>
          <button type="button" class="chart-type" :class="{ active: chartType === 'bar' }" :disabled="!plan.bar.ok" @click="chartType = 'bar'">柱状图</button>
          <button type="button" class="chart-type" :class="{ active: chartType === 'pie' }" :disabled="!plan.pie.ok" :title="plan.pie.ok ? '' : plan.pie.reason" @click="chartType = 'pie'">饼图</button>
          <button type="button" class="chart-type" :class="{ active: chartType === 'table' }" @click="chartType = 'table'">表格</button>
        </div>
      </header>

      <p v-if="plan.ambiguousUnit" class="chart-hint">单位/聚合含义不明确，请在下方选择要对比的维度与指标；饼图需要同一单位。</p>
      <p v-for="note in plan.coverage" :key="note" class="chart-warning" role="note">{{ note }}</p>
      <p v-if="plan.pie.ok && plan.pie.note" class="chart-hint">{{ plan.pie.note }}</p>
      <p v-if="plan.duplicates.has" class="chart-hint">相同类别按独立记录保留，未隐式合并。</p>
      <p v-if="chartType === 'pie' && !plan.pie.ok" class="chart-warning">饼图不可用：{{ plan.pie.reason }}</p>

      <div v-if="chartType === 'bar' && bars" class="chart-stage chart-stage--bar">
        <svg class="chart-svg chart-svg--bar" role="img" :aria-label="summaryLabel + '，柱状图'" viewBox="0 0 700 300" width="100%">
          <line v-if="bars.baseline" :x1="0" :x2="700" :y1="bars.zeroY" :y2="bars.zeroY" class="axis-line" />
          <g v-for="mark in bars.marks" :key="mark.index">
            <rect :x="mark.x" :y="Math.min(mark.y, bars.zeroY)" :width="bars.barW" :height="mark.h" rx="2" class="bar-fill">
              <title>{{ mark.category }}：{{ formatValue(mark.value, plan.metric.metric.kind) }}</title>
            </rect>
            <text v-if="bars.showLabels" :x="mark.x + bars.barW / 2" :y="mark.value >= 0 ? mark.y - 8 : mark.y + 16" class="bar-label" text-anchor="middle">{{ formatValue(mark.value, plan.metric.metric.kind) }}</text>
            <text v-if="mark.tick" :x="mark.x + bars.barW / 2" :y="BAR_H - 10" class="axis-label" text-anchor="middle">{{ mark.label.length > 14 ? mark.label.slice(0, 13) + '…' : mark.label }}<title>{{ mark.label }}</title></text>
          </g>
        </svg>
      </div>

      <div v-else-if="chartType === 'pie' && pie" class="chart-stage chart-stage--pie">
        <svg class="chart-svg chart-svg--pie" role="img" :aria-label="summaryLabel + '，饼图'" viewBox="0 0 360 340" width="100%">
          <template v-for="slice in pie.built" :key="slice.index">
            <circle v-if="!sliceD(pie.cx, pie.cy, pie.r, slice.start, slice.start + slice.sweep)" :cx="pie.cx" :cy="pie.cy" :r="pie.r" class="slice-fill" :fill="slice.color"><title>{{ slice.category }}：{{ formatValue(slice.value, plan.metric.metric.kind) }}（{{ slice.percent.toFixed(1) }}%）</title></circle>
            <path v-else :d="sliceD(pie.cx, pie.cy, pie.r, slice.start, slice.start + slice.sweep)" class="slice-fill" :fill="slice.color"><title>{{ slice.category }}：{{ formatValue(slice.value, plan.metric.metric.kind) }}（{{ slice.percent.toFixed(1) }}%）</title></path>
          </template>
          <g v-if="pie.showLabels">
            <g v-for="slice in pie.built" :key="'l' + slice.index">
              <line :x1="pieLabel(slice).x1" :y1="pieLabel(slice).y1" :x2="pieLabel(slice).lx" :y2="pieLabel(slice).ly" class="leader" />
              <text :x="pieLabel(slice).lx + (pieLabel(slice).lx < pie.cx ? -7 : 7)" :y="pieLabel(slice).ly + 4" class="pie-label" :text-anchor="pieLabel(slice).lx < pie.cx ? 'end' : 'start'">{{ pieLabel(slice).text }}</text>
            </g>
          </g>
        </svg>
        <div class="chart-legend-panel">
        <p class="chart-total">当前展示合计：{{ formatValue(pie.total, plan.metric.metric.kind) }}</p>
        <ul class="chart-legend" aria-label="图例">
          <li v-for="slice in pie.built" :key="slice.index"><span class="legend-swatch" :style="{ background: slice.color }"></span><span class="legend-label">{{ slice.category }}</span><span class="legend-value">{{ formatValue(slice.value, plan.metric.metric.kind) }}（{{ slice.percent.toFixed(1) }}%）</span></li>
        </ul>
        </div>
        <p v-if="plan.coverage.some(c => c.includes('占比'))" class="chart-hint">占比为当前展示数据的占比。</p>
      </div>

      <div v-else-if="chartType === 'table'" class="chart-table-wrap">
        <table class="chart-table"><thead><tr><th>{{ plan.dimension ? plan.dimension.label : '行号' }}</th><th>{{ plan.metric.label }}</th></tr></thead>
        <tbody><tr v-for="record in plan.records" :key="record.index"><td>{{ record.category }}</td><td>{{ record.valid ? formatValue(record.value, plan.metric.metric.kind) : '—' }}</td></tr></tbody></table>
      </div>

      <p class="chart-source">数值来自本次只读查询结果；完整原始表格、实际 SQL 与参数在下方“查询结果”中查看。</p>
    </template>
  </section>
</template>

<style scoped>
/* Chart roles — light values; dark scope via prefers-color-scheme and
   data-theme toggle (app currently light-only, kept for parity with palette). */
.query-chart {
  --surface-1: #fcfcfb;
  --text-primary: #0b0b0b;
  --text-secondary: #52514e;
  --muted: #898781;
  --hairline: #e1e0d9;
  --baseline: #c3c2b7;
  --series-1: #2a78d6;
  color-scheme: light;
  margin-top: 14px; padding: 20px 22px; border: 1px solid var(--hairline); border-radius: 10px; background: var(--surface-1);
}
@media (prefers-color-scheme: dark) {
  .query-chart {
    --surface-1: #1a1a19; --text-primary: #f0efec; --text-secondary: #c3c2b7;
    --muted: #898781; --hairline: #2c2c2a; --baseline: #383835; --series-1: #3987e5;
    color-scheme: dark;
  }
}
:root[data-theme="dark"] .query-chart {
  --surface-1: #1a1a19; --text-primary: #f0efec; --text-secondary: #c3c2b7;
  --muted: #898781; --hairline: #2c2c2a; --baseline: #383835; --series-1: #3987e5;
  color-scheme: dark;
}
.chart-head { display: grid; gap: 14px; padding-bottom: 16px; border-bottom: 1px solid var(--hairline); }
.chart-title { margin: 0; line-height: 1.5; overflow-wrap: anywhere; font-size: 15px; font-weight: 600; color: var(--text-primary); }
.chart-tools { display: flex; flex-wrap: wrap; align-items: center; column-gap: 10px; row-gap: 12px; margin-left: 0; }
.chart-select { display: inline-flex; align-items: center; gap: 8px; font-size: 12px; line-height: 1.5; color: var(--text-secondary); }
.chart-select select { font: inherit; color: var(--text-primary); background: var(--surface-1); border: 1px solid var(--baseline); border-radius: 6px; padding: 6px 10px; min-height: 34px; }
.chart-fixed { color: var(--text-primary); }
.chart-type { font: inherit; font-size: 12px; padding: 6px 12px; min-height: 34px; line-height: 1.5; border: 1px solid var(--baseline); border-radius: 6px; background: var(--surface-1); color: var(--text-secondary); cursor: pointer; }
.chart-type.active { background: #e8f0fa; border-color: var(--series-1); color: var(--series-1); font-weight: 600; }
.chart-type:disabled { opacity: .45; cursor: not-allowed; }
/* Explicit dimensions override the shared 22px SVG icon rule. */
.query-chart { container-type: inline-size; min-width: 0; max-width: 100%; box-sizing: border-box; }
.chart-stage { margin-top: 22px; min-width: 0; }
.chart-stage--bar { overflow-x: auto; }
.chart-svg { display: block; width: 100%; height: auto; max-width: 100%; flex-shrink: 1; }
.chart-svg--bar { min-width: 480px; aspect-ratio: 7 / 3; }
.chart-stage--pie { display: grid; grid-template-columns: minmax(0, 340px) minmax(0, 1fr); align-items: center; gap: 32px; }
.chart-svg--pie { width: 100%; max-width: 340px; aspect-ratio: 36 / 34; justify-self: center; }
.chart-legend-panel { min-width: 0; }
.chart-total { margin: 0 0 18px; font-size: 14px; line-height: 1.6; font-weight: 600; color: var(--text-primary); }
.chart-stage--pie > .chart-hint { grid-column: 1 / -1; }
.bar-fill { fill: var(--series-1); }
.bar-label { font-size: 12px; fill: var(--text-secondary); }
.axis-label { font-size: 12px; fill: var(--muted); }
.axis-line { stroke: var(--baseline); stroke-width: 1.5; }
.leader { stroke: var(--baseline); stroke-width: 1; }
.pie-label { font-size: 12px; fill: var(--text-secondary); }
.slice-fill { stroke: var(--surface-1); stroke-width: 2; }
.total-text { font-size: 17px; fill: var(--text-primary); font-weight: 600; }
.total-caption { font-size: 11px; fill: var(--muted); }
.chart-legend { list-style: none; margin: 0; padding: 0; display: grid; gap: 18px; }
.chart-legend li { display: grid; grid-template-columns: 12px minmax(0, 1fr); align-items: start; column-gap: 10px; row-gap: 5px; line-height: 1.6; font-size: 13px; color: var(--text-secondary); }
.legend-swatch { width: 12px; height: 12px; margin-top: 4px; border-radius: 3px; flex-shrink: 0; }
.legend-label { min-width: 0; overflow-wrap: anywhere; }
.legend-value { grid-column: 2; color: var(--text-primary); overflow-wrap: anywhere; }
.chart-hint { font-size: 12px; color: var(--text-secondary); margin: 8px 0 0; }
.chart-warning { font-size: 12px; color: #8a4b08; background: #fff5df; padding: 7px 9px; border-radius: 6px; margin: 8px 0 0; }
.chart-note { font-size: 12px; color: var(--text-secondary); padding: 10px 12px; }
.chart-source { font-size: 12px; line-height: 1.75; color: var(--muted); margin: 20px 0 0; padding-top: 14px; border-top: 1px solid var(--hairline); }
.chart-table-wrap { margin-top: 10px; overflow-x: auto; }
.chart-table { width: 100%; border-collapse: collapse; font-size: 12px; }
.chart-table th, .chart-table td { border-bottom: 1px solid var(--hairline); padding: 6px 8px; text-align: left; color: var(--text-primary); }
.chart-table th { color: var(--text-secondary); font-weight: 600; }
@container (max-width: 560px) {
  .chart-stage--pie { grid-template-columns: minmax(0, 1fr); gap: 20px; }
  .chart-svg--pie { max-width: 300px; }
  .chart-legend { grid-template-columns: repeat(auto-fit, minmax(140px, 1fr)); }
}
@media (max-width: 640px) {
  .query-chart { padding: 16px 14px; }
  .chart-head { gap: 12px; padding-bottom: 14px; }
  .chart-head { flex-direction: column; align-items: flex-start; }
  .chart-tools { margin-left: 0; min-width: 0; max-width: 100%; }
  .chart-select { min-width: 0; max-width: 100%; }
  .chart-select select { min-width: 0; max-width: 190px; }
  .chart-stage--pie { grid-template-columns: minmax(0, 1fr); gap: 12px; }
  .chart-legend { grid-template-columns: repeat(auto-fit, minmax(145px, 1fr)); }
  .chart-svg--pie { max-width: 320px; }
}
</style>
