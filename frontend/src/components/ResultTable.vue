<script setup>
import { computed, ref } from 'vue';
import DisplayDialog from './DisplayDialog.vue';
const expanded=ref(false);
const props = defineProps({
  data: { type: Object, default: null },
  sql: { type: String, default: "" },
  params: { type: Object, default: null },
});
const isFormula = computed(() => ['formula','symbol','raw_values'].every(key => props.data?.columns?.includes(key)));
const formulaLabels = { formula: '公式', symbol: '变量', name: '参数名称', raw_values: '原始数值及单位', state: '核验状态' };
const labels = {
  month: "月份", CustomerName: "客户姓名",
  BillingCountry: "国家", sales_amount: "销售额", invoice_count: "发票数量", customer_count: "客户数量",
  current_value: "本期值", baseline_value: "基期值", growth_rate: "增长率",
  current_records: "本期记录数", baseline_records: "基期记录数", matched_records: "匹配记录数",
  result_status: "结果状态",
  refund_amount: "已确认退款（元）", net_income: "净收入（元）", CustomerId: "客户ID", invalid_values: "无效金额记录数",
};
function formatCell(row, column) {
  const value = row[column];
  if (value === null || value === undefined) return column === "growth_rate" ? "不适用" : "无数据";
  if (column === "growth_rate") return (value * 100).toFixed(2) + "%";
  return value;
}
</script>

<template>
  <div v-if="data || sql" class="result">
    <button v-if="data?.rows?.length" type="button" class="expand-table" @click="expanded=true">放大表格</button>
    <h2 v-if="data">{{ isFormula ? '参数提取结果' : '查询结果' }}</h2>
    <p v-if="data?.truncated" class="warning">结果超过200行，当前仅展示前200行，不能视为完整结果。</p>
    <p v-if="data?.partial" class="warning">存在缺失数据或不可计算结果，请查看每行的结果状态。</p>
    <div v-if="data?.rows?.length" class="table-wrap">
      <table><thead><tr><th v-for="column in data.columns" :key="column">{{ data.column_labels?.[column] || (isFormula && formulaLabels[column]) || labels[column] || column }}</th></tr></thead>
      <tbody><tr v-for="(row, index) in data.rows" :key="index"><td v-for="column in data.columns" :key="column">{{ formatCell(row, column) }}</td></tr></tbody></table>
    </div>
    <DisplayDialog :open="expanded" title="结果表格 · 放大视图" @close="expanded=false">    <div v-if="data?.rows?.length" class="table-wrap">
      <table><thead><tr><th v-for="column in data.columns" :key="column">{{ data.column_labels?.[column] || (isFormula && formulaLabels[column]) || labels[column] || column }}</th></tr></thead>
      <tbody><tr v-for="(row, index) in data.rows" :key="index"><td v-for="column in data.columns" :key="column">{{ formatCell(row, column) }}</td></tr></tbody></table>
    </div>
</DisplayDialog>
    <details v-if="sql"><summary>查看实际执行 SQL 与参数</summary><pre>{{ sql }}</pre><pre v-if="params">{{ JSON.stringify(params, null, 2) }}</pre></details>
  </div>
</template>

<style scoped>
.result { margin-top: 22px; border-top: 1px solid #e2e8f0; padding-top: 14px; }
h2 { font-size: 17px; }
.expand-table { float:right; cursor:pointer; padding:6px 10px; background:#f3f8f3; border:1px solid #d3dfd5; border-radius:7px; color:#37684b; }
.warning { color: #8a4b08; background: #fff5df; padding: 10px; border-radius: 6px; }
.table-wrap { overflow-x: auto; }
table { width: 100%; border-collapse: collapse; }
th, td { border-bottom: 1px solid #e5eaf1; padding: 9px; text-align: left; white-space: normal; overflow-wrap:anywhere; }
th { background: #f2f7fc; color: #345775; }
details { margin-top: 12px; }
pre { white-space: pre-wrap; overflow-wrap: anywhere; background: #152638; color: #e8f2fc; padding: 12px; border-radius: 8px; }
</style>
