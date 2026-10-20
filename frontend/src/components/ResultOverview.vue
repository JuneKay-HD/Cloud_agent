<script setup>
import { computed } from 'vue';
const props = defineProps({ result: Object });
const labels = { chat: '基础对话', sql: '数据查询', rag: '文档问答', hybrid: '跨源分析' };
const statuses = { completed: '已完成', needs_clarification: '需要补充', insufficient_evidence: '依据不足', partial_answer: '部分结果', conflicting_evidence: '依据冲突', failed: '未完成', unsupported: '暂不支持', knowledge_unavailable: '知识库不可用' };
const type = computed(() => labels[props.result.task_type] || (props.result.sql ? '数据查询' : props.result.evidence?.length ? '资料回答' : '回答'));
const isFormula = computed(() => props.result.evidence_assessment?.answer_mode === 'formula_inputs_verified' || ['formula','symbol','raw_values'].every(key => props.result.data?.columns?.includes(key)));
const explanation = computed(() => {
  const r = props.result;
  if (r.task_type === 'chat' && r.status === 'partial_answer') return '模型输出尚未完整结束，可继续追问。';
  if (isFormula.value && r.status === 'needs_clarification') return '请补充或核对下方缺失的输入参数及单位，再继续提取。';
  if (r.status === 'needs_clarification') return '补充下方条件后再继续查询，避免使用猜测的范围。';
  if (['insufficient_evidence','partial_answer','conflicting_evidence','knowledge_unavailable'].includes(r.status)) return '当前证据存在缺失或限制，请结合下方说明核实，勿将部分结果视为完整结论。';
  if (r.status !== 'completed') return '';
  if (r.task_type === 'chat') return '';
  const sources = r.evidence?.filter(e => e.source_type === 'document').length || 0;
  if (isFormula.value && r.data?.rows) return `本次展示 ${r.data.rows.length} 项参数提取结果${sources ? `，并引用 ${sources} 个文档片段` : ''}。变量、数值、单位及来源核验可在下方查看。`;
  if (r.data?.rows) return `本次展示 ${r.data.rows.length} 行查询结果${sources ? `，并结合 ${sources} 个文档片段` : ''}。统计范围、SQL 和来源可在下方核对。`;
  return sources ? `本回答附有 ${sources} 个文档片段。点击文中的引用编号，可以核对对应原文。` : '';
});
const bars = computed(() => {
  const r = props.result, rows = r.data?.rows || [];
  if (r.status !== 'completed' || r.data?.partial || r.data?.truncated || rows.length < 2 || rows.length > 12) return [];
  const key = ['sales_amount', 'invoice_count', 'customer_count'].find(k => rows.every(x => typeof x[k] === 'number' && Number.isFinite(x[k]) && x[k] >= 0 && x.BillingCountry));
  if (!key) return [];
  const max = Math.max(...rows.map(x => x[key]));
  if (!max) return [];
  return rows.map(x => ({ label: x.BillingCountry, value: x[key], width: x[key] / max * 100, key }));
});
</script>
<template>
  <div class="result-overview">
    <div class="answer-tags"><span>{{ type }}</span><span :class="{ caution: result.status !== 'completed' }">{{ statuses[result.status] || result.status }}</span></div>
    <p v-if="explanation" class="result-explanation">{{ explanation }}</p>
    <figure v-if="bars.length" class="result-chart"><figcaption>结果对比 · {{ { sales_amount: '销售额', invoice_count: '发票数量', customer_count: '客户数量' }[bars[0].key] }}{{ result.query_conditions?.unit ? `（${result.query_conditions.unit}）` : '' }}</figcaption><div v-for="(bar,i) in bars" :key="i" class="bar-row"><span>{{ bar.label }}</span><div class="bar-track"><i :style="{ width: bar.width + '%' }" /></div><strong>{{ bar.value.toLocaleString() }}</strong></div><small>长度按本次最大值缩放；精确值见下方表格。</small></figure>
  </div>
</template>
