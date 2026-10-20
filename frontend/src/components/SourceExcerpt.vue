<script setup>
import { computed, ref, watch } from 'vue';
import AnswerText from './AnswerText.vue';
import { flowSourceText } from '../sourceText';
const props=defineProps({ text:{type:String,default:''},quote:{type:String,default:''},visual:Boolean });
const expanded=ref(false), contextOpen=ref(false);
watch(() => [props.text,props.quote], () => { expanded.value=false; contextOpen.value=false; });
const matched=computed(() => {
  if (!props.quote) return false;
  const start=props.text.indexOf(props.quote);
  return start>=0 && props.text.indexOf(props.quote,start+props.quote.length)<0;
});
const displayed=computed(() => matched.value ? props.quote : props.text);
const long=computed(() => displayed.value.length>700 || displayed.value.split('\n').length>12);
const hasContext=computed(() => matched.value && props.text!==props.quote);
function readable(text) {
  // Delimit bare math for presentation; keep the extracted text unchanged.
  return /\\(?:frac|sqrt|int|cos|sin|varepsilon|omega)|[=^]/.test(text) && !/\$/.test(text) ? '公式：'+text : text;
}
</script>
<template>
  <section class="source-excerpt">
    <header><strong>{{ matched ? '引用摘录' : '引用原文' }}</strong><span v-if="visual">视觉转写 · 请核对原页</span></header>
    <p v-if="quote && !matched" class="position-warning">此引用无法在当前片段中唯一匹配，以下保留原文供核对。</p>
    <div class="excerpt-content" :class="{ 'source-highlight':matched, 'is-collapsed':long && !expanded }" :role="matched ? 'mark' : undefined" :aria-label="matched ? '对应引用文字' : '引用原文'">
      <AnswerText :text="readable(flowSourceText(displayed))" />
    </div>
    <div v-if="long || hasContext" class="excerpt-actions">
      <button v-if="long" type="button" :aria-expanded="expanded" @click.stop="expanded=!expanded">{{ expanded ? '收起长摘录' : '展开完整摘录' }}</button>
      <button v-if="hasContext" type="button" :aria-expanded="contextOpen" @click.stop="contextOpen=!contextOpen">{{ contextOpen ? '收起上下文' : '查看完整上下文' }}</button>
    </div>
    <div v-if="contextOpen" class="context"><small>原文上下文（保留段落、表格与公式）</small><AnswerText :text="text" /></div>
  </section>
</template>
<style scoped>
.source-excerpt { width:100%; box-sizing:border-box; margin:8px 0; border:1px solid #e1e7df; border-radius:8px; overflow:hidden; min-height:0; background:#fff; }
header { display:flex; flex-wrap:wrap; gap:8px; align-items:center; padding:7px 10px; background:#f7f9f5; color:#506348; font-size:12px; } header span { color:#7f8879; font-size:11px; }
.excerpt-content { padding:8px 11px; min-height:0; height:auto; overflow-x:auto; overflow-wrap:anywhere; }
.source-highlight { border-left:3px solid #cba64b; background:#fffdf4; }
.is-collapsed { max-height:13em; overflow-y:hidden; }
:deep(.answer-text) { min-height:0; height:auto; font-size:13px; }
:deep(.answer-text p), :deep(.answer-text blockquote) { margin:4px 0; line-height:1.65; }
:deep(.answer-text p) { white-space:normal; }
:deep(.math-content) { padding:4px 0; font-size:1em; }
:deep(.answer-text h3) { font-size:14px; margin:7px 0 4px; }
:deep(.answer-text ul), :deep(.answer-text ol) { margin:4px 0; padding-left:22px; }
:deep(.math-formula) { min-height:0; background:transparent; border:0; margin:4px 0; }
:deep(.markdown-table) { margin:5px 0; overflow-x:auto; }
.excerpt-actions { display:flex; flex-wrap:wrap; gap:12px; padding:5px 10px 8px; }
.excerpt-actions button { padding:0; border:0; background:transparent; color:#4c7053; font-size:12px; cursor:pointer; }
.context { padding:8px 11px; border-top:1px solid #e8ede4; color:#5f6a5b; }
.context small { font-size:11px; color:#7f8879; }
.position-warning { margin:6px 10px; font-size:12px; color:#93662e; }
</style>
