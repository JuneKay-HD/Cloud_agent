<script setup>
import { ref, computed, watch, nextTick } from 'vue';
import AppIcon from './AppIcon.vue';
import DocumentAssessment from './DocumentAssessment.vue';
import DocumentQualityDialog from './DocumentQualityDialog.vue';
import KnowledgeDiagnostics from './KnowledgeDiagnostics.vue';
const props = defineProps({ panel: String, settings: Object, storageError: String, knowledge: Object, busy: Boolean, knowledgeBaseId: { type: String, default: '' }, hasAttachments: Boolean, addedDocumentIds: { type: Array, default: () => [] } });
const emit = defineEmits(['close', 'reset', 'knowledge', 'select-documents', 'catalog', 'add-document', 'deleted-documents']);
const selectedDocuments = ref([]);
const dialog = ref(null), loading = ref(false), error = ref(''), files = ref([]), health = ref(null), checkedAt = ref('');
const qualityTarget = ref(null);
const search = ref('');
const deleting = ref(false), pendingDelete = ref([]), notice = ref('');
const allVisibleSelected = computed(() => visibleFiles.value.length > 0 && visibleFiles.value.every(file => selectedDocuments.value.includes(file.doc_id)));
const pendingNames = computed(() => pendingDelete.value.map(id => files.value.find(file => file.doc_id === id)?.name).filter(Boolean));
const visibleFiles = computed(() => {
  const query = search.value.trim().toLocaleLowerCase();
  return query ? files.value.filter(file => (file.name || '').toLocaleLowerCase().includes(query)) : files.value;
});
const statusName = { ready: '就绪', partial: '部分可用', unavailable: '不可用' };
watch(() => props.panel, async panel => {
  if (!panel) { dialog.value?.close(); return; }
  search.value = ''; pendingDelete.value = []; notice.value = '';
  await nextTick(); dialog.value?.showModal();
  dialog.value?.querySelector('.panel-content')?.scrollTo(0, 0);
  if (panel === 'knowledge') await refresh();
});
async function refresh() {
  if (loading.value) return;
  loading.value = true; error.value = ''; files.value = []; health.value = null;
  selectedDocuments.value = [];
  try {
    const kb = props.knowledgeBaseId;
    const params = kb ? `?knowledge_base_id=${encodeURIComponent(kb)}` : '';
    const results = await Promise.all(['/api/knowledge/health','/api/documents'].map(async url => {
      const r = await fetch(url + params, { signal: AbortSignal.timeout(15000) });
      if (!r.ok) throw new Error('知识库信息暂时无法读取，请稍后重试。');
      return r.json();
    }));
    health.value = results[0]; files.value = results[1];
    emit('catalog', files.value);
    emit('knowledge', health.value); checkedAt.value = new Date().toLocaleTimeString();
  } catch { error.value = '知识库信息暂时无法读取，请检查后端连接后重试。'; }
  finally { loading.value = false; }
}
watch(() => props.knowledgeBaseId, () => { pendingDelete.value = []; notice.value = ''; qualityTarget.value = null; if (props.panel === 'knowledge' && !loading.value) refresh(); });
function selectAll() {
  const visibleIds = new Set(visibleFiles.value.map(file => file.doc_id));
  selectedDocuments.value = allVisibleSelected.value
    ? selectedDocuments.value.filter(id => !visibleIds.has(id))
    : [...new Set([...selectedDocuments.value, ...visibleIds])];
}
function requestDelete(ids) {
  if (deleting.value || loading.value || props.busy) return;
  error.value = ''; notice.value = '';
  pendingDelete.value = [...ids];
  nextTick(() => dialog.value?.querySelector('.delete-confirmation')?.focus());
}
async function confirmDelete() {
  if (!pendingDelete.value.length || deleting.value || props.busy) return;
  const ids = [...pendingDelete.value], kb = props.knowledgeBaseId;
  deleting.value = true; error.value = ''; notice.value = '';
  try {
    const params = kb ? `?knowledge_base_id=${encodeURIComponent(kb)}` : '';
    const response = await fetch(`/api/documents/batch-delete${params}`, {
      method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ doc_ids: ids }),
    });
    const result = await response.json();
    if (!response.ok) throw new Error(typeof result.detail === 'string' ? result.detail : '删除失败，请刷新后重试。');
    if (kb !== props.knowledgeBaseId) return;
    pendingDelete.value = [];
    if (ids.includes(qualityTarget.value?.doc_id)) qualityTarget.value = null;
    emit('deleted-documents', result.deleted_ids || ids);
    await refresh();
    notice.value = `已删除 ${result.deleted_count} 份文档，原文件已保留在本地回收目录。`;
  } catch (err) {
    if (kb === props.knowledgeBaseId) error.value = err.message || '删除未完成，请刷新列表核对后再重试。';
  } finally { deleting.value = false; }
}
function addDocument(file) {
  if (props.busy || loading.value || deleting.value || pendingDelete.value.length || (props.addedDocumentIds || []).includes(file.doc_id)) return;
  if (props.hasAttachments) { error.value = '请先移除本次问题中的上传附件，再添加知识库文档。'; return; }
  if ((props.addedDocumentIds || []).length >= 50) { error.value = '单次提问最多添加50份知识库文档，请先移除部分文档。'; return; }
  emit('add-document', file);
}

function prepareSummary() {
  if (!selectedDocuments.value.length || selectedDocuments.value.length > 50 || props.busy || deleting.value) return;
  emit('select-documents', [...selectedDocuments.value]);
  emit('close');
}
</script>
<template>
  <dialog ref="dialog" class="workspace-dialog" :class="{ 'knowledge-dialog': panel === 'knowledge' }" aria-labelledby="panel-title" @close="emit('close')" @click="e => { if (e.target === dialog) dialog.close(); }">
    <header><div><small>{{ panel === 'settings' ? '让工作更顺手' : '查看回答所依据的资料' }}</small><h2 id="panel-title">{{ panel === 'settings' ? '个人空间与偏好' : '知识库信息' }}</h2></div><button class="icon-button" aria-label="关闭面板" @click="dialog.close()"><AppIcon name="close" /></button></header>
    <div v-if="panel === 'settings'" class="panel-content settings-form">
      <p class="panel-note">设置保存在当前浏览器，不会改变模型、数据口径或已有对话。</p>
      <label>空间名称<input v-model="settings.name" maxlength="24" placeholder="我的空间" /></label>
      <label>回答字号<select v-model.number="settings.fontSize"><option :value="14">标准 · 14px</option><option :value="16">舒适 · 16px</option><option :value="18">大字 · 18px</option></select></label>
      <label>发送快捷键<select v-model="settings.enterSend"><option :value="true">Enter 发送，Shift + Enter 换行</option><option :value="false">Ctrl / ⌘ + Enter 发送，Enter 换行</option></select></label>
      <label class="check-setting"><input v-model="settings.showDetails" type="checkbox" /><span>默认展开引用与执行过程</span></label>
      <p class="panel-note">桌面端可拖动历史记录右侧边界调整宽度，也可聚焦边界后用方向键调整。</p>
      <p class="save-state" role="status">{{ storageError || '设置已自动保存在当前浏览器，刷新后仍然有效。' }}</p>
      <button class="panel-button" @click="emit('reset')">恢复默认设置</button>
    </div>
    <div v-else class="panel-content">
      <div class="panel-toolbar"><span>{{ checkedAt ? `上次读取 ${checkedAt}` : '读取知识库状态' }}</span><button class="panel-button" :disabled="loading || deleting" @click="refresh">{{ loading ? '正在读取…' : '刷新状态' }}</button></div>
      <p v-if="notice" class="management-notice" role="status"><AppIcon name="check" />{{ notice }}</p>
      <p v-if="error" class="error-banner" role="alert">{{ error }}</p>
      <p v-else-if="loading" role="status">正在读取文档与索引信息…</p>
      <template v-if="health && !loading">
        <div class="knowledge-metrics"><div><strong>{{ statusName[health.status] || '未知' }}</strong><small>知识库状态</small></div><div><strong>{{ health.documents }}</strong><small>份文档</small></div><div><strong>{{ health.chunks }}</strong><small>个片段</small></div></div>
        <ul v-if="health.issues?.length" class="knowledge-errors"><li v-for="(issue,i) in health.issues" :key="i">{{ typeof issue === 'string' ? issue : (issue.filename || issue.file || '文档') + '：' + (issue.reason || issue.error || issue.message || '解析异常') }}<DocumentAssessment v-if="typeof issue === 'object'" :file="issue" /></li></ul>
        <div class="documents-heading"><h3>可用文档 <span>{{ files.length }} 份</span></h3><label class="document-search"><AppIcon name="search" /><input v-model="search" type="search" aria-label="搜索文档名称" placeholder="搜索文档名称" /></label></div><p v-if="search" class="panel-note" role="status">找到 {{ visibleFiles.length }} 份文档</p><p v-if="!files.length" class="panel-note">暂无可用文档。</p>
        <div v-if="files.length" class="selection-toolbar"><div class="selection-tools"><button type="button" class="panel-button" :disabled="busy || deleting || !visibleFiles.length" @click="selectAll">{{ allVisibleSelected ? '取消当前全选' : search ? '选择全部匹配项' : '选择全部' }}</button><button v-if="selectedDocuments.length" type="button" class="text-button" :disabled="deleting" @click="selectedDocuments = []">清空选择</button><span>已选 {{ selectedDocuments.length }} / {{ files.length }} 份</span></div><button type="button" class="danger-button" :disabled="busy || deleting || !selectedDocuments.length" @click="requestDelete(selectedDocuments)"><AppIcon name="trash" />批量删除</button></div>
        <p v-if="selectedDocuments.length > 50" class="selection-hint" role="status">当前可批量删除所选文档；一次总结最多支持 50 份，请减少选择后再总结。</p>
        <ul class="knowledge-files"><li v-for="file in visibleFiles" :key="file.doc_id" :class="{ selected: selectedDocuments.includes(file.doc_id) }"><label class="source-choice"><input v-model="selectedDocuments" type="checkbox" :value="file.doc_id" :disabled="busy || deleting" :aria-label="`选择 ${file.name}`" /><AppIcon name="file" /><span><strong>{{ file.name }}</strong><small v-if="file.metadata?.version">版本 {{ file.metadata.version }}</small><small v-if="file.complexity_report?.level">复杂度 {{ { low: '低', medium: '中', high: '高', unknown: '未知' }[file.complexity_report.level] || file.complexity_report.level }}<template v-if="file.complexity_report.score != null"> · {{ file.complexity_report.score }}</template></small><small v-if="file.quality_report?.status">质量 {{ { pass: '通过', suspect: '存疑', fail: '失败', unknown: '未知', not_applicable: '不适用' }[file.quality_report.status] || file.quality_report.status }}</small></span></label><div class="document-actions"><button type="button" class="quality-entry" :disabled="busy || deleting || addedDocumentIds.includes(file.doc_id)" @click="addDocument(file)">{{ addedDocumentIds.includes(file.doc_id) ? '已添加' : '添加对话' }}</button><a :href="`/api/documents/${encodeURIComponent(file.doc_id)}/file${knowledgeBaseId ? '?knowledge_base_id=' + encodeURIComponent(knowledgeBaseId) : ''}`" target="_blank" rel="noopener noreferrer">查看原文 ↗</a><a v-if="file.markdown_url" :href="file.markdown_url + (knowledgeBaseId ? (file.markdown_url.includes('?') ? '&' : '?') + 'knowledge_base_id=' + encodeURIComponent(knowledgeBaseId) : '')">下载 Markdown</a><button type="button" class="quality-entry" :disabled="loading" @click="qualityTarget = file">查看文档分析质量</button><button type="button" class="file-delete" :disabled="busy || deleting" :aria-label="`删除 ${file.name}`" @click="requestDelete([file.doc_id])"><AppIcon name="trash" />删除</button></div></li></ul><p v-if="search && !visibleFiles.length" class="panel-note">没有匹配的文档，请尝试其他名称。</p>
        <DocumentQualityDialog :open="Boolean(qualityTarget)" :file="qualityTarget" :knowledge-base-id="knowledgeBaseId" @close="qualityTarget = null" />
        <p class="panel-note">这里展示公共知识库。当前对话的上传附件可在聊天区“当前对话的资料”中查看。</p>
      </template>
    </div>
    <section v-if="pendingDelete.length" class="delete-confirmation" tabindex="-1" role="alertdialog" aria-label="确认删除文档" aria-describedby="delete-description">
      <div><strong>删除 {{ pendingDelete.length }} 份文档？</strong><p id="delete-description">{{ pendingDelete.length === 1 ? pendingNames[0] : '这些文档将退出当前知识库检索。' }}<br />原文件将移入本地回收目录，历史对话保留。</p></div>
      <div class="confirmation-actions"><button type="button" class="panel-button" :disabled="deleting" @click="pendingDelete = []">取消</button><button type="button" class="danger-button danger-solid" :disabled="deleting || busy" @click="confirmDelete">{{ deleting ? '正在删除…' : '确认删除' }}</button></div>
    </section>
    <footer class="workspace-footer">
      <span v-if="panel === 'knowledge'" class="selection-count">已选择 {{ selectedDocuments.length }} 份文档</span>
      <div class="footer-actions"><button type="button" class="panel-button" @click="dialog.close()">关闭</button><button v-if="panel === 'knowledge'" type="button" class="panel-button summary-button" :disabled="busy || deleting || !selectedDocuments.length || selectedDocuments.length > 50" @click="prepareSummary">总结所选文档</button></div>
    </footer>
  </dialog>
</template>

<style scoped>
.workspace-dialog[open] { display:flex; flex-direction:column; overflow:hidden; max-height:min(88dvh, 900px); }
.workspace-dialog.knowledge-dialog { width:min(880px, calc(100vw - 32px)); }
.workspace-dialog > header { flex:0 0 auto; padding:22px 28px; }
.workspace-dialog > header .icon-button { flex:0 0 auto; width:40px; height:40px; border:1px solid #dce6dc; border-radius:10px; background:#fff; }
.panel-content { flex:1 1 auto; min-height:0; overflow-y:auto; overscroll-behavior:contain; scrollbar-gutter:stable; padding:24px 28px; }
.workspace-footer { flex:0 0 auto; display:flex; align-items:center; justify-content:space-between; gap:12px; padding:16px 28px; border-top:1px solid #e4ebe1; background:#f8faf6; }
.selection-count { font-size:12px; color:#74806b; }
.footer-actions { display:flex; gap:10px; margin-left:auto; }
.footer-actions .panel-button { min-height:38px; padding:9px 16px; }
.summary-button { background:#26755f; color:#fff; border-color:#26755f; }
.summary-button:disabled { opacity:.45; cursor:not-allowed; }
.documents-heading { display:flex; align-items:center; justify-content:space-between; gap:16px; margin:24px 0 16px; }
.documents-heading h3 { margin:0; white-space:nowrap; }
.documents-heading h3 span { font-size:12px; font-weight:400; color:#74806b; margin-left:8px; }
.document-search { display:flex; align-items:center; gap:8px; border:1px solid #dce6dc; border-radius:10px; padding:9px 12px; min-width:0; width:min(300px, 55%); background:#fff; }
.document-search svg { width:16px; height:16px; flex-shrink:0; color:#74806b; }
.document-search input { width:100%; min-width:0; border:0; outline:0; font:inherit; font-size:13px; background:transparent; }
.document-search:focus-within { outline:2px solid #87b6a5; outline-offset:2px; }
.knowledge-files { margin:0; display:flex; flex-direction:column; gap:12px; }
.knowledge-files li.selected { border-color:#8db9a6; background:#f2f9f5; box-shadow:inset 3px 0 #3b896d; }
.selection-toolbar { display:flex; align-items:center; justify-content:space-between; flex-wrap:wrap; gap:12px; padding:12px 14px; margin-bottom:16px; background:#f4f7f3; border:1px solid #e2e9df; border-radius:12px; }
.selection-tools { display:flex; flex-wrap:wrap; align-items:center; gap:12px; }
.selection-tools span,.selection-hint { font-size:12px; color:#74806b; line-height:1.6; }
.text-button { font:inherit; font-size:12px; border:0; background:transparent; color:#26755f; cursor:pointer; padding:6px 0; }
.danger-button,.file-delete { display:inline-flex; align-items:center; justify-content:center; gap:6px; font:inherit; font-size:12px; padding:8px 12px; color:#b14c40; background:#fff8f6; border:1px solid #ebd4ce; border-radius:8px; cursor:pointer; }
.danger-button svg,.file-delete svg,.management-notice svg { width:16px; height:16px; flex-shrink:0; }
.danger-button:hover,.file-delete:hover { background:#fbeae5; border-color:#cf9386; }
.danger-button:disabled,.file-delete:disabled { opacity:.45; cursor:not-allowed; }
.file-delete { background:transparent; border-color:transparent; }
.management-notice { display:flex; align-items:center; gap:8px; background:#edf7f0; border:1px solid #cce3d4; border-radius:10px; padding:12px; color:#32694d; font-size:13px; line-height:1.6; }
.delete-confirmation { flex:0 0 auto; padding:18px 28px; background:#fff7f3; border-top:1px solid #efd9ce; display:flex; flex-wrap:wrap; align-items:center; justify-content:space-between; gap:12px; max-height:35dvh; overflow:auto; }
.delete-confirmation > div:first-child { min-width:0; flex:1 1 260px; }
.delete-confirmation strong { color:#8f4939; font-size:14px; }
.delete-confirmation p { margin:7px 0 0; font-size:12px; color:#856f64; line-height:1.7; overflow-wrap:anywhere; }
.confirmation-actions { display:flex; gap:10px; margin-left:auto; }
.danger-solid { background:#b45343; color:#fff; border-color:#b45343; }
.danger-solid:hover { background:#983f32; color:#fff; }

.knowledge-files li { display:grid; grid-template-columns:minmax(0, 1fr); gap:12px; padding:18px; border:1px solid #e3e9df; border-radius:12px; background:#fff; transition:background .15s, border-color .15s; }
.document-actions { display:flex; flex-wrap:wrap; align-items:center; justify-content:flex-end; gap:10px 18px; padding-left:50px; }
.document-actions a { font-size:12px; padding:6px 0; }
.knowledge-files strong { display:block; line-height:1.65; font-size:14px; }
.source-choice > svg { flex:0 0 20px; width:20px; height:20px; align-self:flex-start; margin-top:2px; }
.source-choice > input { width:16px; height:16px; accent-color:#26755f; }
@media (max-width:600px) {
  .workspace-dialog.knowledge-dialog { width:calc(100vw - 20px); max-height:92dvh; border-radius:16px; }
  .workspace-dialog > header { padding:16px; }
  .panel-content { padding:18px 16px; }
  .workspace-footer { padding:12px 16px; flex-wrap:wrap; }
  .documents-heading { align-items:stretch; flex-direction:column; gap:12px; }
  .document-search { width:100%; }
  .document-actions { padding-left:0; justify-content:flex-start; gap:8px 14px; }
  .panel-toolbar { flex-wrap:wrap; }
  .knowledge-files li { padding:14px; }
  .delete-confirmation { padding:14px 16px; }
  .selection-toolbar { align-items:stretch; }
}

.source-choice { display: flex; align-items: center; gap: 10px; flex: 1; min-width: 0; cursor: pointer; }
.source-choice span { overflow-wrap: anywhere; }
.source-choice input { flex: 0 0 auto; }
.source-choice small { display: block; }
.quality-entry { font: inherit; font-size: 12px; padding: 5px 10px; border: 1px solid #c9d9cb; background: #f2f8f2; border-radius: 7px; color: #2f6047; cursor: pointer; }
.quality-entry:disabled { opacity: .5; cursor: default; }
.quality-entry:hover { background: #e5f1e6; }
</style>

