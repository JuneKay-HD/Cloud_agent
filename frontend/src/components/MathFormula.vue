<script setup>
import { computed, ref } from 'vue';
import katex from 'katex';
import DisplayDialog from './DisplayDialog.vue';
const props = defineProps({ expression: { type:String,default:'' }, inline:Boolean, expandable: { type:Boolean,default:true } });
const expanded=ref(false);
const rendered=computed(() => {
  let expression=String(props.expression).trim();
  expression=expression.replace(/^\$\$([\s\S]*)\$\$$/,'$1').replace(/^\$([\s\S]*)\$$/,'$1').replace(/^\\\[([\s\S]*)\\\]$/,'$1').replace(/^\\\(([\s\S]*)\\\)$/,'$1');
  if (expression.length>8000) return null;
  // Alignment wrappers affect layout only; the original expression stays available.
  if (/[&]|\\\\/.test(expression) && !/\\begin\{/.test(expression)) expression='\\begin{aligned}'+expression+'\\end{aligned}';
  try { return katex.renderToString(expression,{ displayMode:!props.inline,throwOnError:true,trust:false,strict:'ignore',maxExpand:500,maxSize:8,output:'htmlAndMathml' }); }
  catch { return null; }
});
</script>
<template>
  <span v-if="inline" class="inline-math"><span v-if="rendered" v-html="rendered"></span><code v-else>{{ expression }}</code></span>
  <div v-else class="math-formula">
    <div class="math-content" :aria-label="expression"><span v-if="rendered" v-html="rendered"></span><code v-else>{{ expression }}</code></div>
    <p v-if="!rendered" class="math-warning">此表达式暂无法排版，已保留原始写法。</p>
    <button v-if="expandable" type="button" class="enlarge-formula" @click="expanded=true">放大公式</button>
    <DisplayDialog :open="expanded" title="公式放大查看" @close="expanded=false">
      <div class="math-content"><span v-if="rendered" v-html="rendered"></span><code v-else>{{ expression }}</code></div>
      <details><summary>查看原始表达式</summary><pre>{{ expression }}</pre></details>
    </DisplayDialog>
  </div>
</template>
<style scoped>
.math-formula { position:relative; padding:16px 14px; background:#f8faf8; border:1px solid #e0e9e2; border-radius:10px; margin:12px 0; }
.math-content { overflow-x:auto; overflow-y:hidden; padding:10px 3px; font-size:1.12em; } .math-content :deep(.katex-display) { margin:0; text-align:left; } .math-content :deep(.katex-display > .katex) { text-align:left; }
.inline-math { display:inline-block; max-width:100%; overflow-x:auto; vertical-align:middle; padding:2px 0; } code,pre { white-space:pre-wrap; overflow-wrap:anywhere; }
.enlarge-formula { display:block; margin:8px 0 0 auto; border:1px solid #d5e1d8; border-radius:6px; padding:5px 10px; color:#37664b; background:#fff; cursor:pointer; font-size:13px; }
.math-warning { font-size:13px; color:#875c24; } details { margin-top:20px; font-size:14px; } summary { cursor:pointer; }
</style>
