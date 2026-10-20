<script setup>
import { computed } from 'vue';
import FormulaSource from './FormulaSource.vue';
import MathFormula from './MathFormula.vue';
import AnswerText from './AnswerText.vue';
const props=defineProps({ group:Object, index:Number, evidence:Array, expandable:{ type:Boolean,default:true } });
const emit=defineEmits(['citation']);
const citation=computed(() => props.group.citation || props.group.rows.map(row => row.location || '').join(' ').match(/\[\d+\]/)?.[0]);
const symbols=computed(() => props.group.rows.filter(row => row.symbol!=='常量'));
const constants=computed(() => props.group.rows.filter(row => row.symbol==='常量'));
const locations=computed(() => [...new Set(props.group.rows.map(row => row.location).filter(Boolean))]);
</script>
<template>
  <section class="formula-card">
    <header><h3>公式 {{ index+1 }}</h3><span>原文提取 · 保留核验状态</span></header>
    <MathFormula :expression="group.expression" :expandable="expandable" />
    <FormulaSource :source="group.sourceEvidence" :expression="group.expression" />
    <button v-if="citation" class="locate-formula" type="button" @click="emit('citation',{citation,quote:group.expression})">定位此公式 {{ citation }}</button>
    <div v-if="symbols.length" class="formula-table-scroll">
      <table><thead><tr><th>符号</th><th>原文含义</th><th>原始取值</th><th>单位</th><th>核验状态</th></tr></thead>
        <tbody><tr v-for="(row,i) in symbols" :key="i"><td><MathFormula v-if="!['未确认','常量'].includes(row.symbol)" :expression="row.symbol" inline /><span v-else>{{ row.symbol }}</span></td><td>{{ row.name || '未注明' }}</td><td><AnswerText :text="row.raw_values || '未注明'" :evidence="evidence" math-values @citation="token => $emit('citation',token)" /></td><td>{{ row.unit || (row.raw_values?.includes('未注明') ? '未注明' : '见原始取值') }}</td><td><span class="state" :class="{ confirmed:row.state.includes('已核验') || row.state==='已确认' }">{{ row.state }}</span></td></tr></tbody>
      </table>
    </div>
    <details v-if="constants.length" class="constants"><summary>原文常量 · {{ constants.length }} 条（未自动绑定为输入参数）</summary><div class="constant-grid"><div v-for="(row,i) in constants" :key="i"><AnswerText :text="row.raw_values" :evidence="evidence" math-values @citation="token => $emit('citation',token)" /><small>{{ row.unit || '单位未注明' }}</small><span>{{ row.state }}</span></div></div></details>
    <details v-if="locations.length" class="locations"><summary>原文位置</summary><p v-for="location in locations" :key="location"><AnswerText :text="location" :evidence="evidence" @citation="token => $emit('citation',token)" /></p></details>
  </section>
</template>
<style scoped>
.formula-card { min-width:0; margin:18px 0; padding:20px; border:1px solid #dde7df; border-radius:14px; background:#fff; } header { display:flex; flex-wrap:wrap; align-items:center; gap:12px; } h3 { font-size:1em; margin:0; color:#294e38; } header span { font-size:.78em; color:#7b877e; }
.formula-table-scroll { overflow:auto; } table { width:100%; border-collapse:collapse; min-width:540px; font-size:.9em; } th { color:#59715f; background:#f6f9f6; text-align:left; } th,td { padding:10px 12px; border-bottom:1px solid #e7ede8; vertical-align:top; } td { white-space:normal; overflow-wrap:anywhere; } td:first-child { min-width:60px; } .state { font-size:.85em; color:#886528; background:#fff6e5; padding:4px 7px; display:inline-block; border-radius:5px; } .confirmed { color:#356449; background:#edf7ee; }
details { margin-top:14px; font-size:.88em; } summary { cursor:pointer; color:#526d5b; } .constant-grid { display:grid; grid-template-columns:repeat(auto-fit,minmax(150px,1fr)); gap:10px; margin-top:12px; } .constant-grid > div { border:1px solid #e2e9e3; padding:10px; border-radius:8px; } small,.constant-grid span { display:block; color:#7d817a; font-size:.8em; margin-top:5px; } :deep(.answer-text p) { margin:0; } .locations p { margin:8px 0; overflow-wrap:anywhere; }
@media(max-width:600px) { .formula-card { padding:12px; } }
</style>
