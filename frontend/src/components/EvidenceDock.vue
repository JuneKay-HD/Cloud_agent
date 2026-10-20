<script setup>
// Right-column evidence dock for the currently selected answer.
// Shows actual task steps, SQL/params/results, document citations, retrieval
// method & scores, and checks / conflicts — never model-internal reasoning.
import { computed, ref, watch } from 'vue';
import TracePanel from './TracePanel.vue';
import EvidencePanel from './EvidencePanel.vue';

const props = defineProps({
  result: { type: Object, default: null },
  idPrefix: { type: String, default: 'dock-' },
});
const emit = defineEmits(['close']);
const tab = ref('steps');

watch(() => props.result?.run_id, () => { tab.value = 'steps'; });

const hasSql = computed(() => Boolean(props.result?.sql));
const hasEvidence = computed(() => (props.result?.evidence || []).length > 0);
const hasAssessment = computed(() => Boolean(props.result?.evidence_assessment));
const hasData = computed(() => Boolean(props.result?.data));
const hasTrace = computed(() => (props.result?.trace || []).length > 0 || (props.result?.plan || []).length > 0);

const tabs = computed(() => [
  { key: 'steps', label: '任务步骤', show: hasTrace.value },
  { key: 'sql', label: 'SQL 与结果', show: hasSql.value || hasData.value },
  { key: 'evidence', label: `文档引用 ${(props.result?.evidence || []).filter(e => e.source_type === 'document').length || ''}`, show: hasEvidence.value },
  { key: 'retrieval', label: '检索方式', show: hasAssessment.value },
  { key: 'checks', label: '检查与冲突', show: hasAssessment.value },
].filter(t => t.show));

function statusLabel(status) {
  const labels = {
    completed: '已完成', needs_clarification: '需要补充', insufficient_evidence: '依据不足',
    partial_answer: '部分结果', conflicting_evidence: '依据冲突', failed: '未完成',
    unsupported: '暂不支持', knowledge_unavailable: '知识库不可用',
  };
  return labels[status] || status || '';
}
const typeLabel = computed(() => ({
  chat: '基础对话', sql: '数据查询', rag: '文档问答', hybrid: '跨源分析',
}[props.result?.task_type] || (props.result?.sql ? '数据查询' : '回答')));

function retrievalLine(assessment) {
  if (!assessment) return '';
  const mode = assessment.retrieval_mode;
  const m = {
    hybrid: '关键词与向量联合检索（RRF 融合）', lexical: '本地关键词检索',
    document_browse: '按文档顺序浏览正文', formula_document_scan: '按文档提取公式与参数',
  }[mode] || mode || '未记录';
  return m;
}
function rerankLine(assessment) {
  if (!assessment?.rerank_status) return '';
  return { applied: '模型重排已执行', fallback: '模型重排失败，保留原召回', not_requested: '未调用重排' }[assessment.rerank_status] || assessment.rerank_status;
}
function metricLine(stage, metric) {
  const names = {
    index_read: '读取索引', rewrite: '检索改写', recall: '召回与融合', query_embedding: '问题向量化',
    rerank: '重排', evidence_preflight: '证据充分性检查', generation: '生成', repair: '修复', verification: '核验',
  };
  const parts = [];
  parts.push(names[stage] || stage);
  if (typeof metric.duration_ms === 'number') parts.push(`${metric.duration_ms} 毫秒`);
  if (typeof metric.calls === 'number') parts.push(`${metric.calls} 次`);
  if (metric.http_requests != null) parts.push(`${metric.http_requests} 次模型请求`);
  if (metric.total_tokens != null) parts.push(`${metric.total_tokens} tokens`);
  if (metric.failed_calls) parts.push(`${metric.failed_calls} 次失败`);
  return parts.join(' · ');
}
</script>

<template>
  <aside class="evidence-dock" aria-label="当前回答的证据与执行详情">
    <header class="dock-header">
      <div class="dock-title">
        <strong>回答依据与执行详情</strong>
        <small v-if="result">本次 · {{ typeLabel }} · {{ statusLabel(result.status) }}</small>
        <small v-else>选择一条助手回答以查看其证据</small>
      </div>
      <button class="icon-button dock-close" aria-label="关闭证据面板" @click="emit('close')">×</button>
    </header>

    <div v-if="!result" class="dock-empty">
      <p>从左侧选择一条助手回答，右侧将显示它的任务步骤、SQL、引用与核验结果。</p>
    </div>

    <template v-else>
      <div class="dock-tabs" role="tablist">
        <button v-for="t in tabs" :key="t.key" type="button" :class="{ active: tab === t.key }" role="tab"
          :aria-selected="tab === t.key" @click="tab = t.key">{{ t.label }}</button>
      </div>

      <div class="dock-body">
        <!-- 任务步骤与执行状态 -->
        <div v-if="tab === 'steps'" class="dock-tab-pane" role="tabpanel">
          <TracePanel :trace="result.trace || []" :plan="result.plan || []" :status="result.status" :initially-open="true" />
          <p v-if="!hasTrace" class="dock-note">本次回答没有可展示的工具执行记录。</p>
        </div>

        <!-- SQL、参数、结果、耗时 -->
        <div v-else-if="tab === 'sql'" class="dock-tab-pane" role="tabpanel">
          <section v-if="result.sql" class="dock-section">
            <h3>实际执行 SQL</h3>
            <pre class="dock-sql">{{ result.sql }}</pre>
          </section>
          <section v-if="result.sql_params && Object.keys(result.sql_params).length" class="dock-section">
            <h3>绑定参数</h3>
            <pre class="dock-json">{{ JSON.stringify(result.sql_params, null, 2) }}</pre>
          </section>
          <section v-if="result.data" class="dock-section">
            <h3>查询结果
              <small v-if="result.data.duration_ms != null"> · {{ result.data.duration_ms }} 毫秒</small>
              <small v-if="result.data.row_count != null"> · {{ result.data.row_count }} 行</small>
            </h3>
            <p v-if="result.data.truncated" class="dock-warning">结果超过 200 行，仅展示前 200 行，不能视为完整结果。</p>
            <p v-if="result.data.partial" class="dock-warning">存在缺失数据或不可计算结果，请查看每行的结果状态。</p>
            <div v-if="result.data.rows?.length" class="dock-table-wrap">
              <table class="dock-table"><thead><tr><th v-for="c in result.data.columns" :key="c">{{ c }}</th></tr></thead>
              <tbody><tr v-for="(row, i) in result.data.rows" :key="i"><td v-for="c in result.data.columns" :key="c">{{ row[c] === null || row[c] === undefined ? '—' : row[c] }}</td></tr></tbody></table>
            </div>
            <p v-else class="dock-note">没有返回结果行。</p>
          </section>
        </div>

        <!-- 文档引用、页码、章节、命中片段 -->
        <div v-else-if="tab === 'evidence'" class="dock-tab-pane" role="tabpanel">
          <EvidencePanel :evidence="result.evidence || []" :assessment="result.evidence_assessment" :status="result.status"
            :id-prefix="idPrefix" />
        </div>

        <!-- 实际检索方式与评分 -->
        <div v-else-if="tab === 'retrieval'" class="dock-tab-pane" role="tabpanel">
          <section class="dock-section">
            <h3>实际检索方式</h3>
            <p>{{ retrievalLine(result.evidence_assessment) }}</p>
            <p v-if="rerankLine(result.evidence_assessment)">{{ rerankLine(result.evidence_assessment) }}</p>
            <p v-if="result.evidence_assessment?.normalized_query && result.evidence_assessment.normalized_query !== result.evidence_assessment.original_query" class="dock-note">
              检索用语：{{ result.evidence_assessment.normalized_query }}（原问题保留）
            </p>
          </section>
          <section v-if="result.evidence_assessment?.stage_metrics && Object.keys(result.evidence_assessment.stage_metrics).length" class="dock-section">
            <h3>阶段耗时与调用</h3>
            <ul class="dock-metrics"><li v-for="(metric, stage) in result.evidence_assessment.stage_metrics" :key="stage">{{ metricLine(stage, metric) }}</li></ul>
          </section>
          <section v-if="result.evidence?.length" class="dock-section">
            <h3>片段检索评分</h3>
            <ul class="dock-scores">
              <li v-for="item in result.evidence" :key="item.evidence_id">
                <span v-if="item.metadata?.citation">[{{ item.metadata.citation }}]</span>
                <span>{{ item.title }}</span>
                <small v-if="item.metadata?.page_no">第{{ item.metadata.page_no }}页</small>
                <small v-if="item.metadata?.bm25_score != null">BM25 {{ item.metadata.bm25_score.toFixed ? item.metadata.bm25_score.toFixed(4) : item.metadata.bm25_score }}</small>
                <small v-if="item.metadata?.vector_score != null">向量 {{ item.metadata.vector_score.toFixed ? item.metadata.vector_score.toFixed(4) : item.metadata.vector_score }}</small>
                <small v-if="item.metadata?.fusion_method">{{ item.metadata.fusion_method }}</small>
                <small v-if="item.metadata?.rerank_score != null">重排 {{ item.metadata.rerank_score }}</small>
              </li>
            </ul>
          </section>
        </div>

        <!-- 检查结果、证据不足、来源冲突 -->
        <div v-else-if="tab === 'checks'" class="dock-tab-pane" role="tabpanel">
          <section class="dock-section">
            <h3>核验状态</h3>
            <p :class="{ 'dock-warning': result.status !== 'completed' }">
              {{ result.evidence_assessment?.answer_mode === 'public_semantic_rejected' ? '回答核验未通过' : statusLabel(result.status) }}
              <span v-if="result.evidence_assessment?.candidate_sources_only" class="dock-warning"> · 以下仅为检索候选，尚未形成通过核验的事实结论</span>
            </p>
          </section>
          <section v-if="result.evidence_assessment?.unresolved_conflicts?.length" class="dock-section">
            <h3>待核对冲突</h3>
            <p v-for="(c, i) in result.evidence_assessment.unresolved_conflicts" :key="i" class="dock-warning">{{ c }}</p>
          </section>
          <section v-if="result.evidence_assessment?.missing_aspects?.length" class="dock-section">
            <h3>尚缺证据</h3>
            <p v-for="(m, i) in result.evidence_assessment.missing_aspects" :key="i" class="dock-warning">{{ m }}</p>
          </section>
          <section v-if="result.evidence_assessment?.conflicts?.length" class="dock-section">
            <h3>来源冲突</h3>
            <p v-for="(c, i) in result.evidence_assessment.conflicts" :key="i" class="dock-warning">{{ c.key }}：{{ c.values.join(' / ') }}</p>
          </section>
          <section v-if="result.evidence_assessment?.warnings?.length" class="dock-section">
            <h3>提示</h3>
            <p v-for="(w, i) in result.evidence_assessment.warnings" :key="i" class="dock-note">{{ w }}</p>
          </section>
          <section v-if="result.evidence_assessment?.document_coverage?.length" class="dock-section">
            <h3>所选文档覆盖</h3>
            <p v-for="s in result.evidence_assessment.document_coverage" :key="s.doc_id">{{ s.filename || s.title }}：读取 {{ s.read_chunk_count }}，可用 {{ s.usable_chunk_count }}，引用 {{ s.cited_chunk_count }}</p>
          </section>
          <section v-if="result.evidence_assessment?.aspect_coverage?.length" class="dock-section">
            <h3>尚需核对的要素</h3>
            <p v-for="a in result.evidence_assessment.aspect_coverage" :key="a.aspect">{{ a.aspect }}：{{ a.status }}</p>
          </section>
          <p v-if="!hasAssessment" class="dock-note">本次回答没有可展示的核验记录。</p>
        </div>
      </div>
    </template>
  </aside>
</template>

<style scoped>
.evidence-dock { width: var(--dock-width, 340px); flex-shrink: 0; border-left: 1px solid var(--line); background: #fbfcfa; display: flex; flex-direction: column; min-height: 0; }
.dock-header { padding: 14px 16px; border-bottom: 1px solid #eef0ea; display: flex; align-items: flex-start; gap: 10px; }
.dock-title { flex: 1; min-width: 0; }
.dock-title strong { display: block; font-size: 14px; color: #35413a; }
.dock-title small { display: block; font-size: 11px; color: #8a9388; margin-top: 4px; }
.dock-close { align-self: center; }
.dock-empty { padding: 24px 16px; font-size: 12px; color: #8a9388; line-height: 1.8; }
.dock-tabs { display: flex; flex-wrap: wrap; gap: 6px; padding: 10px 12px 0; border-bottom: 1px solid #eef0ea; }
.dock-tabs button { font-size: 11px; padding: 6px 9px; border-radius: 7px; border: 1px solid transparent; background: transparent; color: #6b756c; cursor: pointer; }
.dock-tabs button.active { background: #e5efe9; color: #2f6047; border-color: #cfe0d5; font-weight: 600; }
.dock-body { flex: 1; overflow-y: auto; min-height: 0; padding: 12px; scrollbar-width: thin; }
.dock-section { margin-bottom: 16px; }
.dock-section h3 { font-size: 12px; color: #3d4a42; margin: 0 0 8px; font-weight: 600; }
.dock-section p { font-size: 12px; line-height: 1.7; color: #5d6a60; margin: 6px 0; overflow-wrap: anywhere; }
.dock-sql, .dock-json { white-space: pre-wrap; overflow-wrap: anywhere; background: #152638; color: #e8f2fc; padding: 10px; border-radius: 8px; font-size: 11px; line-height: 1.6; max-height: 280px; overflow: auto; }
.dock-warning { color: #a05c1e !important; }
.dock-note { color: #8a9388 !important; }
.dock-metrics, .dock-scores { list-style: none; padding: 0; margin: 0; }
.dock-metrics li, .dock-scores li { font-size: 12px; padding: 7px 0; border-bottom: 1px dashed #e9ede6; color: #5d6a60; display: flex; flex-wrap: wrap; gap: 6px; align-items: baseline; }
.dock-scores small { font-size: 10px; color: #8a9388; }
.dock-table-wrap { overflow: auto; max-height: 300px; border: 1px solid #e5eae1; border-radius: 8px; }
.dock-table { width: 100%; border-collapse: collapse; font-size: 11px; }
.dock-table th, .dock-table td { padding: 6px 8px; border-bottom: 1px solid #eef0ea; text-align: left; white-space: nowrap; }
.dock-table th { background: #f2f6f1; color: #4a5a50; position: sticky; top: 0; }
@media (max-width: 900px) { .evidence-dock { width: 300px; } }
@media (max-width: 1000px) {
  .evidence-dock { position: fixed; top: 0; right: 0; height: 100dvh; z-index: 30; width: min(360px, 92vw); box-shadow: -12px 0 30px #24354a14; }
  .dock-header { padding-top: 20px; }
}
</style>
