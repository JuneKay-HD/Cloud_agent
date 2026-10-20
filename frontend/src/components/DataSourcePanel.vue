<script setup>
// Left-column data source selector: database + knowledge base entries.
// Requirement: SQL needs a selected database, knowledge-base QA needs a selected
// knowledge base, cross-source needs both. Unselected operations are blocked by
// the chat panel before sending and again by the backend. Upload/create flows
// are NOT gated here.
import { onMounted, ref } from 'vue';
import AppIcon from './AppIcon.vue';

const props = defineProps({
  databaseId: { type: String, default: '' },
  knowledgeBaseId: { type: String, default: '' },
  busy: Boolean,
});
const emit = defineEmits(['update:databaseId', 'update:knowledgeBaseId']);

const data = ref({ status: 'loading', sources: [] });
const error = ref('');
const collapsed = ref(false);

async function refresh() {
  error.value = '';
  try {
    const response = await fetch('/api/datasources', { signal: AbortSignal.timeout(15000) });
    data.value = await response.json();
    if (!response.ok) throw new Error(data.value.message || '数据源读取失败');
  } catch (reason) {
    error.value = reason.message || '数据源信息暂时无法读取，请检查后端连接。';
  }
}
onMounted(refresh);

const databases = () => data.value.sources?.filter(s => s.kind === 'database') || [];
const knowledgeBases = () => data.value.sources?.filter(s => s.kind === 'knowledge_base') || [];
function clearSelection(kind) {
  if (props.busy) return;
  if (kind === 'database') emit('update:databaseId', '');
  if (kind === 'knowledge_base') emit('update:knowledgeBaseId', '');
}
</script>

<template>
  <section class="data-source-panel" aria-label="数据源选择">
    <header class="ds-heading"><AppIcon name="database" /><span>数据源</span><button type="button" class="ds-refresh" title="刷新数据源" :disabled="busy" @click="refresh">↻</button><button type="button" class="ds-collapse" :class="{ collapsed }" :aria-expanded="!collapsed" aria-controls="data-source-content" :aria-label="collapsed ? '展开数据源' : '收起数据源'" :title="collapsed ? '展开数据源' : '收起数据源'" @click="collapsed = !collapsed"><AppIcon name="arrow" /></button></header>
    <div id="data-source-content" v-show="!collapsed" class="ds-content">

    <p v-if="error" class="ds-error" role="alert">{{ error }}</p>
    <p v-else-if="data.status === 'loading'" class="ds-note">正在读取数据源…</p>

    <template v-else-if="data.status === 'ok'">
      <div class="ds-group">
        <h4>数据库 <button v-if="databaseId" type="button" class="ds-clear" :disabled="busy" @click="clearSelection('database')">取消数据库选择</button></h4>
        <p v-if="!databases().length" class="ds-note">暂无可选数据库。</p>
        <label v-for="db in databases()" :key="db.source_id" class="ds-choice" :class="{ disabled: !db.accessible }">
          <input type="radio" name="database" :value="db.source_id" :checked="databaseId === db.source_id"
            :disabled="busy || !db.accessible" @change="emit('update:databaseId', db.accessible ? db.source_id : '')" />
          <span class="ds-name">{{ db.name }}</span>
          <small v-if="databaseId === db.source_id" class="ds-active">已选</small>
          <small v-else-if="!db.accessible" class="ds-inaccessible" :title="db.reason">{{ db.reason }}</small>
        </label>
      </div>

      <div class="ds-group">
        <h4>知识库 <button v-if="knowledgeBaseId" type="button" class="ds-clear" :disabled="busy" @click="clearSelection('knowledge_base')">取消知识库选择</button></h4>
        <p v-if="!knowledgeBases().length" class="ds-note">暂无可选知识库。</p>
        <label v-for="kb in knowledgeBases()" :key="kb.source_id" class="ds-choice" :class="{ disabled: !kb.accessible }">
          <input type="radio" name="knowledge_base" :value="kb.source_id" :checked="knowledgeBaseId === kb.source_id"
            :disabled="busy || !kb.accessible" @change="emit('update:knowledgeBaseId', kb.accessible ? kb.source_id : '')" />
          <span class="ds-name">{{ kb.source_id === 'rule_demo_docs' ? '自定义知识库' : (kb.name || '').replace(/\s*[（(][^（）()]*[）)]/g, '').trim() }}</span>
        </label>
      </div>

      <p class="ds-guidance">问数前请先选数据库，知识库问答前请先选知识库；跨源任务需要同时选择。未选择时，相关操作会被拦截并要求补充选择。</p>
    </template>
    </div>
  </section>
</template>

<style scoped>
.data-source-panel { border-top: 1px solid #e3e6df; padding: 14px 9px 6px; }
.ds-heading { display: flex; align-items: center; gap: 7px; font-size: 12px; font-weight: 600; color: #5c6a5d; padding: 0 5px 8px; }
.ds-heading svg { width: 15px; height: 15px; color: var(--green); }
.ds-refresh { margin-left: auto; border: 0; background: transparent; color: #85917f; cursor: pointer; font-size: 14px; }
.ds-collapse { display:flex; align-items:center; justify-content:center; width:26px; height:26px; border-radius:6px; color:#687f70; }
.ds-collapse svg { width:15px; height:15px; transition:transform .15s ease; }
.ds-collapse.collapsed svg { transform:rotate(180deg); }
.ds-group { margin-bottom: 12px; }
.ds-group h4 { font-size: 11px; color: #8a948a; margin: 0 0 6px; padding: 0 5px; font-weight: 500; }
.ds-clear { margin-left:8px; border:0; background:transparent; color:#477352; padding:2px; font-size:11px; cursor:pointer; }
.ds-clear:disabled { opacity:.5; cursor:default; }
.ds-choice { display: flex; align-items: center; gap: 7px; padding: 7px 8px; border-radius: 8px; cursor: pointer; font-size: 12px; }
.ds-choice:hover { background: #edf1eb; }
.ds-choice input { flex-shrink: 0; accent-color: #26755f; }
.ds-name { flex: 1; min-width: 0; overflow-wrap: anywhere; color: #3e4c42; }
.ds-active { font-size: 10px; color: #2f6047; background: #e3efe8; border-radius: 4px; padding: 2px 5px; }
.ds-inaccessible { font-size: 10px; color: #b0aaa0; }
.ds-error { font-size: 11px; color: #a05c1e; line-height: 1.7; margin: 4px 5px; }
.ds-note { font-size: 11px; color: #98a095; line-height: 1.7; padding: 2px 6px; }
.ds-guidance { font-size: 10px; color: #99a096; line-height: 1.7; margin: 8px 6px 4px; }
</style>
