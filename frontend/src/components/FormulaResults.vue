<script setup>
import { computed, ref } from 'vue';
import FormulaCard from './FormulaCard.vue';
import DisplayDialog from './DisplayDialog.vue';
import AnswerText from './AnswerText.vue';
const props=defineProps({ result:Object, text:String });
const emit=defineEmits(['citation']);
const expanded=ref(false);
const groups=computed(() => {
  const result=new Map();
  for (const row of props.result.data?.rows || []) {
    const key=String(row.formula || '').trim();
    if (!result.has(key)) result.set(key,{ expression:key,rows:[] });
    result.get(key).rows.push(row);
  }
  for (const group of result.values()) {
    const proof=props.result.evidence_assessment?.formulas?.find(formula => formula.expression===group.expression)?.source;
    const source=proof && props.result.evidence?.find(item => item.evidence_id===proof.chunk_id ||
      (item.metadata?.chunk_id===proof.chunk_id && item.metadata.start_char===proof.start_char && item.metadata.end_char===proof.end_char));
    group.sourceEvidence=source;
    if (source?.metadata.citation) group.citation='['+source.metadata.citation+']';
  }
  return [...result.values()];
});
const warnings=computed(() => [...new Set(props.result.evidence_assessment?.warnings || [])]);
</script>
<template>
  <div class="formula-results">
    <div class="formula-toolbar"><div><h2>公式与参数提取</h2><p>{{ groups.length }} 个公式 · 按公式整理符号与常量</p></div><button type="button" @click="expanded=true">放大查看全部结果</button></div>
    <p class="formula-note">本次只提取原文，不执行计算。符号含义、取值及单位未明确时保留为“未注明”；视觉转写请对照原页核对。</p>
    <p v-for="warning in warnings" :key="warning" class="formula-warning">{{ warning }}</p>
    <FormulaCard v-for="(group,index) in groups" :key="group.expression" :group="group" :index="index" :evidence="result.evidence || []" @citation="token => emit('citation',token)" />
    <details class="original-answer"><summary>查看完整提取说明与原始表达式</summary><AnswerText :text="text || result.message" :evidence="result.evidence || []" @citation="token => emit('citation',token)" /></details>
    <DisplayDialog :open="expanded" title="公式与参数 · 放大视图" @close="expanded=false"><p class="formula-note">公式、符号、常量及核验状态与原结果一致。</p><FormulaCard v-for="(group,index) in groups" :key="group.expression" :group="group" :index="index" :evidence="result.evidence || []" :expandable="false" @citation="token => { expanded=false; emit('citation',token); }" /></DisplayDialog>
  </div>
</template>
<style scoped>
.formula-results { min-width:0; } .formula-toolbar { display:flex; flex-wrap:wrap; gap:14px; align-items:center; justify-content:space-between; margin:14px 0; } h2 { margin:0; font-size:18px; color:#304b39; } .formula-toolbar p { font-size:13px; color:#7a857c; margin:6px 0 0; }
button { cursor:pointer; border:1px solid #ccdcd0; border-radius:8px; padding:8px 12px; background:#f3f8f3; color:#37684b; } .formula-note { line-height:1.7; font-size:.85em; color:#68776b; background:#f4f8f4; padding:12px; border-radius:8px; } .formula-warning { font-size:13px; color:#927039; line-height:1.6; } .original-answer { margin:18px 0; color:#526d5b; } summary { cursor:pointer; }
</style>
