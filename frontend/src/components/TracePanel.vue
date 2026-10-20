<script setup>
import { computed, onUnmounted, ref, watch } from 'vue';
import { formatElapsed } from '../traceTiming';
const props = defineProps({ trace: { type: Array, default: () => [] }, plan: { type: Array, default: () => [] },
  loading: Boolean, status: String, initiallyOpen: Boolean,
  elapsedMs: { type: Number, default: null }, startedAt: { type: Number, default: null } });
const labels = {
  running: '执行中', success: '已完成', completed: '完成', degraded: '降级处理',
  failed: '失败', needs_input: '需要补充', needs_clarification: '需要补充',
  partial_answer: '部分完成', insufficient_evidence: '依据不足',
  conflicting_evidence: '依据冲突', knowledge_unavailable: '资料不可用',
  unsupported: '暂不支持', blocked: '依赖未满足', pending: '等待中', skipped: '未执行',
};
const elapsed = ref(0);
let timer;
let started;
watch(() => [props.loading, props.startedAt], ([value]) => {
  clearInterval(timer);
  if (value) {
    started = props.startedAt ?? Date.now();
    const tick = () => { elapsed.value = Math.max(0, Math.floor((Date.now() - started) / 1000)); };
    tick();
    timer = setInterval(tick, 1000);
  }
}, { immediate: true });
onUnmounted(() => clearInterval(timer));
const summary = computed(() => props.loading
  ? `正在处理 · ${elapsed.value}秒`
  : [labels[props.status] || '处理记录',
    formatElapsed(props.elapsedMs) ? `用时${formatElapsed(props.elapsedMs)}` : '',
    `${props.trace.length}项记录`].filter(Boolean).join(' · '));
const cautions = ['failed','degraded','needs_input','needs_clarification','partial_answer',
  'insufficient_evidence','conflicting_evidence','knowledge_unavailable','unsupported','blocked'];
function duration(value) {
  return typeof value === 'number' && Number.isFinite(value)
    ? (value < 1000 ? `${Math.round(value)}毫秒` : `${(value / 1000).toFixed(1)}秒`) : '';
}
function displayDetail(item) {
  const text = String(item.detail || '');
  const old = text.match(/^实际读取 (\d+) 份会话附件；解析发生在上传阶段，未重新OCR$/);
  return old ? `已读取${old[1]}份会话附件信息` : text;
}
</script>

<template>
  <details class="process-panel" :open="loading || initiallyOpen">
    <summary>
      <span class="process-symbol" :class="{ active: loading }" aria-hidden="true">◌</span>
      <strong>任务执行与来源核验</strong>
      <span class="process-summary" role="status" :title="!loading && formatElapsed(elapsedMs) ? '本次请求在服务端的实际处理用时，包含模型调用、查询与核验，不含网络传输时间' : undefined">{{ summary }}</span>
    </summary>
    <div class="process-content">
      <p v-if="loading" class="process-note">请求正在处理。完成后显示实际任务步骤、工具调用与来源核验记录。</p>
      <p v-else-if="!trace.length" class="process-note">本次没有可展示的执行记录。</p>
      <ol v-if="trace.length" class="process-timeline">
        <li v-for="(item, index) in trace" :key="index" :class="{ caution: cautions.includes(item.status) }">
          <span class="step-dot" aria-hidden="true" />
          <div class="step-heading">
            <strong>{{ item.step }}</strong>
            <span>{{ labels[item.status] || item.status || '已记录' }}</span>
            <time v-if="duration(item.duration_ms)">{{ duration(item.duration_ms) }}</time>
          </div>
          <div v-if="item.detail || item.error || item.input_summary" class="step-detail">
            <p v-if="item.input_summary && item.input_summary !== item.detail">{{ item.input_summary }}</p>
            <p v-if="item.detail">{{ displayDetail(item) }}</p>
            <p v-if="item.error" class="step-error">{{ typeof item.error === 'string' ? item.error : JSON.stringify(item.error) }}</p>
          </div>
        </li>
      </ol>
      <details v-if="plan.length" class="task-plan">
        <summary>任务安排与依赖</summary>
        <ol>
          <li v-for="item in plan" :key="item.id">
            <strong>{{ item.label }}</strong> · {{ labels[item.status] || item.status }}
            <p v-if="item.depends_on?.length">依赖：{{ item.depends_on.map(id => plan.find(p => p.id === id)?.label || id).join('、') }}</p>
            <p v-if="item.detail">{{ displayDetail(item) }}</p>
          </li>
        </ol>
      </details>
    </div>
  </details>
</template>

<style scoped>
.process-panel { margin: 10px 0 16px; color: #64715d; font-size: 13px; width: 100%; }
.process-panel > summary { display: flex; align-items: center; flex-wrap: wrap; gap: 8px; cursor: pointer; list-style: none; padding: 9px 0; }
.process-panel > summary::-webkit-details-marker { display: none; }
.process-panel > summary::after { content: '›'; margin-left: auto; transform: rotate(90deg); transition: transform .15s; }
.process-panel[open] > summary::after { transform: rotate(-90deg); }
.process-summary { color: #8a9584; font-size: 12px; }
.process-symbol { font-size: 20px; line-height: 1; }
.active { color: #42644e; animation: turn 1.4s linear infinite; }
@keyframes turn { to { transform: rotate(360deg); } }
.process-content { margin-left: 10px; padding-left: 17px; border-left: 2px solid #e8ede3; }
.process-note { margin: 5px 0 12px; line-height: 1.7; color: #7f8978; }
.process-timeline { list-style: none; padding: 0; margin: 0; }
.process-timeline > li { position: relative; padding: 9px 0 12px; }
.step-dot { position: absolute; left: -23px; top: 15px; width: 8px; height: 8px; background: #7b9870; border: 2px solid white; border-radius: 50%; }
.caution .step-dot { background: #c58d3a; }
.step-heading { display: flex; align-items: baseline; gap: 10px; flex-wrap: wrap; }
.step-heading strong { color: #53614c; font-weight: 500; }
.step-heading > span, time { font-size: 11px; color: #8e9988; }
.caution .step-heading > span { color: #a96b20; }
.step-detail { margin-top: 6px; font-size: 12px; }
.step-detail p, .task-plan p { white-space: pre-wrap; overflow-wrap: anywhere; line-height: 1.7; margin: 6px 0; }
.step-error { color: #a96b20; }
.task-plan { padding-bottom: 10px; }
.task-plan summary { cursor: pointer; }
.task-plan li { margin: 8px 0; }
@media (prefers-reduced-motion: reduce) { .active { animation: none; } }
</style>
