<script setup>
import { computed } from 'vue';
import MathFormula from './MathFormula.vue';
const props = defineProps({ text: String, mathValues:Boolean, evidence: { type: Array, default: () => [] } });
defineEmits(['citation']);
// Render a bounded Markdown subset as Vue text nodes, never as raw HTML.
const blocks = computed(() => {
  const lines = String(props.text || '').split('\n'), result = [];
  for (let i = 0; i < lines.length; i++) {
    const line = lines[i];
    if (line.startsWith('```')) {
      const language = line.slice(3).trim(), code = [];
      while (++i < lines.length && !lines[i].startsWith('```')) code.push(lines[i]);
      result.push({ type: 'code', language, text: code.join('\n') });
    } else if (/^公式[：:]/.test(line.trim())) {
      let formula=line.trim().replace(/^公式[：:]\s*/,'');
      const citations=formula.match(/(?:\s*\[\d+(?:\.\d+)?\])+\s*$/)?.[0] || '';
      formula=formula.slice(0,formula.length-citations.length);
      while (i+1<lines.length && lines[i+1].trim() && !/^(?:公式[：:]|[^\s]+[：:])/.test(lines[i+1].trim())) formula+='\n'+lines[++i];
      result.push({type:'math',text:formula,citations});
    } else if (/^\$\$/.test(line.trim()) || /^\\\[/.test(line.trim())) {
      let formula=line;
      if (!/(?:\$\$|\\\])$/.test(line.trim().slice(2))) {
        while (i+1<lines.length) { formula+='\n'+lines[++i]; if (/(?:\$\$|\\\])\s*$/.test(lines[i])) break; }
      }
      result.push({type:'math',text:formula,citations:''});
    } else if (line.includes('|') && /^\s*\|?\s*:?-{3,}/.test(lines[i + 1] || '')) {
      const cells = x => x.trim().replace(/^\||\|$/g, '').split('|').map(c => c.trim());
      const head = cells(line), rows = []; i++;
      while (i + 1 < lines.length && lines[i + 1].includes('|')) rows.push(cells(lines[++i]));
      result.push({ type: 'table', head, rows });
    } else if (/^#{1,6}\s/.test(line)) result.push({ type: 'heading', text: line.replace(/^#{1,6}\s+/, '') });
    else if (/^\s*([-*+]\s|\d+[.)]\s)/.test(line)) {
      const ordered = /^\s*\d/.test(line), items = [line.replace(/^\s*(?:[-*+]|\d+[.)])\s+/, '')];
      while (i + 1 < lines.length && (ordered ? /^\s*\d+[.)]\s/ : /^\s*[-*+]\s/).test(lines[i + 1])) items.push(lines[++i].replace(/^\s*(?:[-*+]|\d+[.)])\s+/, ''));
      result.push({ type: 'list', ordered, items });
    } else if (line.trim()) result.push({ type: line.startsWith('> ') ? 'quote' : 'paragraph', text: line.replace(/^> /, '') });
  }
  return result;
});
function parts(text) {
  return String(text).split(/(`[^`\n]+`|\*\*[^*\n]+\*\*|\$[^$\n]+\$|\\\([^\n]*?\\\)|\[\d+(?:\.\d+)?\])/g).filter(Boolean).map(text => {
    if ((text.startsWith('$') && text.endsWith('$')) || (text.startsWith('\\(') && text.endsWith('\\)'))) return {type:'math',text};
    if (props.mathValues && /\\[A-Za-z]+/.test(text) && !/[\u4e00-\u9fff]/.test(text)) return {type:'math',text:text.trim()};
    if (text.startsWith('`')) return { type: 'code', text: text.slice(1, -1) };
    if (text.startsWith('**')) return { type: 'strong', text: text.slice(2, -2) };
    if (/^\[\d+(?:\.\d+)?\]$/.test(text) && props.evidence.some(e => e.source_type === 'document' && e.metadata?.citation === Number(text.slice(1, -1).split('.')[0]))) return { type: 'citation', text };
    return { type: 'span', text };
  });
}
</script>

<template>
  <div class="answer-text">
    <template v-for="(block, i) in blocks" :key="i">
      <div v-if="block.type === 'math'"><MathFormula :expression="block.text" /><button v-for="part in parts(block.citations).filter(part => part.type === 'citation')" :key="part.text" class="citation-link" @click="$emit('citation',part.text)">{{ part.text }}</button></div>
      <div v-else-if="block.type === 'code'" class="code-block"><small>{{ block.language || '代码' }}</small><pre><code>{{ block.text }}</code></pre></div>
      <div v-else-if="block.type === 'table'" class="markdown-table"><table><thead><tr><th v-for="(cell,j) in block.head" :key="j">{{ cell }}</th></tr></thead><tbody><tr v-for="(row,j) in block.rows" :key="j"><td v-for="(cell,k) in row" :key="k"><template v-for="(part,n) in parts(cell)" :key="n"><button v-if="part.type === 'citation'" class="citation-link" @click="$emit('citation', part.text)">{{ part.text }}</button><MathFormula v-else-if="part.type === 'math'" :expression="part.text" inline /><component v-else :is="part.type">{{ part.text }}</component></template></td></tr></tbody></table></div>
      <component v-else-if="block.type === 'list'" :is="block.ordered ? 'ol' : 'ul'"><li v-for="(item,j) in block.items" :key="j"><template v-for="(part,k) in parts(item)" :key="k"><button v-if="part.type === 'citation'" class="citation-link" @click="$emit('citation', part.text)">{{ part.text }}</button><MathFormula v-else-if="part.type === 'math'" :expression="part.text" inline /><component v-else :is="part.type">{{ part.text }}</component></template></li></component>
      <component v-else :is="block.type === 'heading' ? 'h3' : block.type === 'quote' ? 'blockquote' : 'p'"><template v-for="(part,j) in parts(block.text)" :key="j"><button v-if="part.type === 'citation'" class="citation-link" @click="$emit('citation', part.text)">{{ part.text }}</button><MathFormula v-else-if="part.type === 'math'" :expression="part.text" inline /><component v-else :is="part.type">{{ part.text }}</component></template></component>
    </template>
  </div>
</template>
