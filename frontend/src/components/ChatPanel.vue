<script setup>
import { computed, nextTick, onMounted, onUnmounted, ref, shallowReactive } from 'vue';
import { mergeRequestMessages } from '../conversationSync';
import AnswerText from './AnswerText.vue';
import FormulaResults from './FormulaResults.vue';
import ResultOverview from './ResultOverview.vue';
import WorkspacePanel from './WorkspacePanel.vue';
import { usePreferences } from '../preferences';
import AppIcon from './AppIcon.vue';
import ConversationHistory from './ConversationHistory.vue';
import ClarificationCard from './ClarificationCard.vue';
import EvidencePanel from './EvidencePanel.vue';
import EvidenceDock from './EvidenceDock.vue';
import DataSourcePanel from './DataSourcePanel.vue';
import EvidenceDrawer from './EvidenceDrawer.vue';
import ResultTable from './ResultTable.vue';
import TracePanel from './TracePanel.vue';
import DocumentPreview from './DocumentPreview.vue';
import KnowledgeAttachments from './KnowledgeAttachments.vue';
import DocumentAssessment from './DocumentAssessment.vue';
import TextCorrectionDialog from './TextCorrectionDialog.vue';
import QueryChart from './QueryChart.vue';
import { chartEligible } from '../chartData.js';

const sessions = ref([]);
const sessionId = ref('');
const messages = ref([]);
const documents = ref([]);
const documentAssessments = ref([]);
const attachments = ref([]);
const previewFile = ref(null);
const correctionFile = ref(null);
function openCorrection(file) { correctionFile.value = { file, sessionId: sessionId.value }; }
async function refreshCorrectedDocuments(sid) {
  if (sid !== sessionId.value) return;
  try {
    const detail = await api(`/api/sessions/${encodeURIComponent(sid)}`);
    if (sid !== sessionId.value) return;
    documents.value = detail.documents || [];
    attachments.value = attachments.value.map(file => documents.value.find(doc => doc.id === file.id) || file);
    previewFile.value = null;
  } catch (reason) { if (sid === sessionId.value) error.value = reason.message; }
}
function openAttachment(file) { closeEvidence(); previewFile.value = { file, sessionId: sessionId.value }; }
const evidenceEntry = ref(null);
let evidenceSequence = 0;
function closeEvidence() { evidenceEntry.value = null; }
function evidenceKey(message, index) { return `${sessionId.value}:${message.id || index}`; }
function openEvidence(message, index, target = null) {
  if (!message.result || switching.value) return;
  panel.value = '';
  const question = messages.value.slice(0, index).reverse().find(item => item.role === 'user')?.text || '';
  evidenceEntry.value = { key: evidenceKey(message, index), result: message.result,
    question, idPrefix: `message-${message.id || index}-`, target, sequence: ++evidenceSequence };
}
const selectedKnowledgeIds = ref([]);
const knowledgeCatalog = ref({});
const selectedKnowledgeFiles = computed(() => selectedKnowledgeIds.value.map(doc_id => ({ doc_id, name: knowledgeCatalog.value[doc_id]?.name || '知识库文档' })));
function registerKnowledgeFiles(files) {
  knowledgeCatalog.value = { ...knowledgeCatalog.value, ...Object.fromEntries(files.map(file => [file.doc_id, { doc_id: file.doc_id, name: file.name }])) };
}
function knowledgeFileName(id, message) { return message.knowledge_documents?.find(file => file.doc_id === id)?.name || knowledgeCatalog.value[id]?.name || '知识库文档'; }
const knowledge = ref(null);

// Explicit data-source selection (ChatPanel-level gate + send payload).
const databaseId = ref('');
const knowledgeBaseId = ref('');

// Evidence dock: follows the currently selected assistant answer.
const dockMessageId = ref('');
const dockOpen = ref(true);

const input = ref('');
const search = ref('');
const pendingRequests = shallowReactive(new Map());
const requestSnapshots = new Map();
const creatingSession = ref(false);
let creatingRequest = null;
let viewVersion = 0;
let localMessageSequence = 0;
let disposed = false;
const loading = computed(() => creatingSession.value || pendingRequests.has(sessionId.value));
const processingStartedAt = computed(() => pendingRequests.get(sessionId.value)?.startedAt || (creatingSession.value ? creatingRequest?.startedAt : null));
const generatingIds = computed(() => [...pendingRequests.keys()]);
const liveTraces = shallowReactive(new Map());
const liveTrace = computed(() => liveTraces.get(sessionId.value) || []);
function stopThinking() {
  const request = pendingRequests.get(sessionId.value) || creatingRequest;
  if (!request) return;
  request.cancelled = true;
  request.controller.abort();
  if (pendingRequests.get(request.id) === request) pendingRequests.delete(request.id);
  if (requestSnapshots.get(request.id) === request) requestSnapshots.delete(request.id);
  if (creatingRequest === request) { creatingRequest = null; creatingSession.value = false; }
  const trace = liveTraces.get(request.id) || [];
  liveTraces.set(request.id, trace.map(item => item.status === 'running' || item.status === 'pending'
    ? { ...item, status: 'failed', detail: item.detail + '；用户停止，未收到完成事件' } : item));
  nextTick(() => textInput.value?.focus());
}
onUnmounted(() => {
  disposed = true;
  viewVersion++;
  for (const request of pendingRequests.values()) { request.cancelled = true; request.controller.abort(); }
  creatingRequest?.controller.abort();
  pendingRequests.clear();
  requestSnapshots.clear();
  liveTraces.clear();
});
const uploading = ref(false);
const switching = ref(false);
const historyBusy = ref(false);
const error = ref('');
const sidebarOpen = ref(window.innerWidth > 760);
const fileInput = ref(null);
const textInput = ref(null);
const scrollArea = ref(null);
const { settings, storageError, reset } = usePreferences();
const panel = ref('');
const viewportWidth = ref(window.innerWidth);
const evidenceOverlay = computed(() => !!evidenceEntry.value && viewportWidth.value < 1280);
const resizing = ref(false);
const maxSidebar = computed(() => Math.max(230, Math.min(520, viewportWidth.value * .45)));
const sidebarWidth = computed(() => Math.min(settings.sidebarWidth, maxSidebar.value));
const dockResizing = ref(false);
const minDock = 260;
const maxDock = computed(() => Math.max(minDock, Math.min(900, viewportWidth.value - (sidebarOpen.value ? sidebarWidth.value + 6 : 0) - 426)));
const dockWidth = computed(() => Math.max(minDock, Math.min(settings.dockWidth, maxDock.value)));
function resizeViewport() { viewportWidth.value = window.innerWidth; }
window.addEventListener('resize', resizeViewport);
onUnmounted(() => window.removeEventListener('resize', resizeViewport));
function dragStart(e) { if (e.button !== 0) return; resizing.value = true; e.currentTarget.setPointerCapture(e.pointerId); e.preventDefault(); }
function dragMove(e) { if (resizing.value) settings.sidebarWidth = Math.round(Math.max(230, Math.min(maxSidebar.value, e.clientX))); }
function resizeKey(e) {
  if (!['ArrowLeft','ArrowRight','Home','End'].includes(e.key)) return;
  e.preventDefault();
  settings.sidebarWidth = e.key === 'Home' ? 230 : e.key === 'End' ? maxSidebar.value : Math.max(230, Math.min(maxSidebar.value, sidebarWidth.value + (e.key === 'ArrowLeft' ? -16 : 16)));
}
function dockDragStart(e) { if (e.button !== 0) return; dockResizing.value = true; e.currentTarget.setPointerCapture(e.pointerId); e.preventDefault(); }
function dockDragMove(e) { if (dockResizing.value) settings.dockWidth = Math.round(Math.max(minDock, Math.min(maxDock.value, viewportWidth.value - e.clientX - 3))); }
function dockResizeKey(e) {
  if (!['ArrowLeft', 'ArrowRight', 'Home', 'End'].includes(e.key)) return;
  e.preventDefault();
  settings.dockWidth = e.key === 'Home' ? minDock : e.key === 'End' ? maxDock.value : Math.max(minDock, Math.min(maxDock.value, dockWidth.value + (e.key === 'ArrowLeft' ? 16 : -16)));
}
const busy = computed(() => loading.value || uploading.value || switching.value || historyBusy.value);
const canSend = computed(() => !busy.value && (input.value.trim() || attachments.value.length || selectedKnowledgeIds.value.length));

// Route requirements (mirrored from backend/app/sources.py ROUTE_*_KINDS):
// sql -> database; rag -> knowledge base (unless scoped to attachments);
// hybrid -> database + knowledge base. These are advisory pre-checks; the
// backend re-validates and hard-blocks.
function sourceGateQuestion() {
  if (!input.value.trim() && !attachments.value.length && !selectedKnowledgeIds.value.length) return null;
  const text = input.value.trim();
  const withAttachments = attachments.value.length > 0;
  const withPublic = selectedKnowledgeIds.value.length > 0;
  // A pure "summarize attachment" send still scopes to session documents.
  const sqlHint = /(查询|统计|计算)\s*.*(?:销售额|数量|增长率|净收入|退款|发票|排名|多少|前三|最多|同比)：?/i.test(text) || /(查询|统计|计算)\s*.*(?:经营|业绩|销售|客户|发票)/.test(text);
  const kbHint = !sqlHint && (withPublic || /(知识库|文档|资料|文件|规则|口径|制度|经营建议|统计口径|公式|适用)/.test(text));
  const needsDb = sqlHint || (withAttachments && /(查询|统计|计算)/.test(text));
  const needsKb = kbHint || withPublic || (withAttachments && /(总结|内容|问题|要点)/.test(text));
  if (withPublic) return null; // explicit public docs — KB selection handled by docs scope
  if (needsDb && needsKb) {
    if (!databaseId.value && !knowledgeBaseId.value) return '跨源任务需要同时选择数据库和知识库，请先在左侧数据源面板选择。';
    if (!databaseId.value) return '本次任务需要数据库，请先在左侧数据源面板选择数据库。';
    if (!knowledgeBaseId.value) return '本次任务需要知识库，请先在左侧数据源面板选择知识库。';
  }
  if (needsDb && !databaseId.value) return '本次任务需要数据库，请先在左侧数据源面板选择数据库，再发起查询。';
  if (needsKb && !knowledgeBaseId.value && !withPublic && !(withAttachments && !/知识库/.test(text))) {
    if (withAttachments && !sqlHint && !kbHint) return null; // pure attachment summarise
    return '本次任务需要知识库，请先在左侧数据源面板选择知识库，再发起查询。';
  }
  return null;
}

// Evidence dock follows a selected assistant answer (or the newest one).
const dockedResult = computed(() => {
  const target = dockMessageId.value
    ? messages.value.find((message, index) => String(message.id || index) === dockMessageId.value)
    : null;
  return target?.result || null;
});
function dockOnMessage(message, index) {
  if (message.role === 'assistant' && message.result) {
    dockMessageId.value = String(message.id || index);
    dockOpen.value = true;
  }
}
function selectLatestAnswer() {
  // A session can end with an unanswered user message; select its latest result.
  for (let index = messages.value.length - 1; index >= 0; index--) {
    const message = messages.value[index];
    if (message.role === 'assistant' && message.result) {
      dockMessageId.value = String(message.id || index);
      dockOpen.value = true;
      return;
    }
  }
}
const visibleSessions = computed(() => sessions.value.filter(item => item.message_count > 0 || item.document_count > 0 || item.assessment_count > 0));
const filteredSessions = computed(() => visibleSessions.value
  .filter(item => item.title.toLowerCase().includes(search.value.trim().toLowerCase()))
  .sort((a, b) => Number(Boolean(b.pinned)) - Number(Boolean(a.pinned)) || b.updated_at.localeCompare(a.updated_at)));
function sessionUpdated(item) {
  sessions.value = sessions.value.map(existing => existing.session_id === item.session_id ? item : existing);
}
function sessionDeleted(id) {
  sessions.value = sessions.value.filter(item => item.session_id !== id);
  requestSnapshots.delete(id);
  if (sessionId.value === id) newConversation(true);
}
const currentTitle = computed(() => sessions.value.find(item => item.session_id === sessionId.value)?.title || '新对话');
const clarificationOptions = computed(() => messages.value.at(-1)?.result?.clarification_options || {});
const defaultExamples = [
  { icon: 'chart', title: '看看销售表现', text: '查询2012年各国家销售额前三名' },
  { icon: 'book', title: '了解指标口径', text: '销售额的统计口径是什么？' },
  { icon: 'sparkle', title: '分析经营机会', text: '找出2012年销售额最高的国家，并总结该地区经营建议' },
];

const examples = computed(() => knowledge.value?.examples?.length ? knowledge.value.examples : defaultExamples);

async function api(url, options) {
  const response = await fetch(url, options);
  const payload = await response.json().catch(() => ({}));
    if (!response.ok) {
      const failure = new Error(typeof payload.detail === 'string' ? payload.detail : payload.detail?.message || '请求失败，请稍后重试');
      failure.assessment = payload.detail?.assessment;
      throw failure;
    }
  return payload;
}
function remember(id) {
  try { localStorage.setItem('xiaoyun-session', id); } catch { /* History still works without local storage. */ }
}
let historyVersion = 0;
async function refreshSessions() {
  const version = ++historyVersion;
  const items = await api('/api/sessions');
  if (disposed || version !== historyVersion) return;
  // The first POST may not have saved its user message when this read started.
  for (const request of pendingRequests.values()) {
    if (!request.summary) continue;
    if (!items.some(item => item.session_id === request.id && item.message_count > 0)) {
      const index = items.findIndex(item => item.session_id === request.id);
      if (index >= 0) items[index] = request.summary;
      else items.push(request.summary);
    }
  }
  sessions.value = items;
}
async function refreshKnowledge() {
  try { knowledge.value = await api('/api/knowledge/health'); } catch { knowledge.value = null; }
}
async function scrollToEnd() {
  await nextTick();
  scrollArea.value?.scrollTo({ top: scrollArea.value.scrollHeight, behavior: 'smooth' });
}
function citationFor(message, token) {
  const citation = Number((typeof token === 'string' ? token : token.citation).slice(1,-1).split('.')[0]);
  return message.result?.evidence?.find(item => item.source_type === 'document' && item.metadata?.citation === citation);
}
async function showCitation(message, token, index) {
  const evidence = citationFor(message, token);
  if (!evidence) return;
  openEvidence(message, index, token);
}
async function openSession(id) {
  if (uploading.value || switching.value || historyBusy.value || creatingSession.value) return;
  closeEvidence();
  const version = ++viewVersion;
  switching.value = true;
  error.value = '';
  try {
    const detail = await api(`/api/sessions/${id}`);
    if (disposed || version !== viewVersion) return;
    const request = requestSnapshots.get(id);
    sessionId.value = id;
    messages.value = mergeRequestMessages(detail.messages, request);
    documents.value = detail.documents;
    documentAssessments.value = detail.document_assessments || [];
    attachments.value = [];
    selectedKnowledgeIds.value = [];
    input.value = '';
    error.value = request?.error || '';
    if (request?.assistantMessage?.result?.run_id && !pendingRequests.has(id)
      && detail.messages.some(message => message.result?.run_id === request.assistantMessage.result.run_id)) {
      requestSnapshots.delete(id);
    }
    remember(id);
    if (window.innerWidth <= 760) sidebarOpen.value = false;
    await scrollToEnd();
  } catch (reason) { if (!disposed && version === viewVersion) error.value = reason.message; }
  finally { if (version === viewVersion) switching.value = false; }
}
function newConversation(afterDelete = false) {
  if (uploading.value || switching.value || creatingSession.value || (historyBusy.value && afterDelete !== true)) return;
  closeEvidence();
  viewVersion++;
  sessionId.value = '';
  messages.value = [];
  documents.value = [];
  documentAssessments.value = [];
  attachments.value = [];
  selectedKnowledgeIds.value = [];
  input.value = '';
  error.value = '';
  dockMessageId.value = '';
  dockOpen.value = true;
  remember('');
  if (window.innerWidth <= 760) sidebarOpen.value = false;
  nextTick(() => textInput.value?.focus());
}
async function ensureSession(signal) {
  if (!sessionId.value) {
    const result = await api('/api/sessions', { method: 'POST', signal });
    if (signal?.aborted) throw new DOMException('Aborted', 'AbortError');
    sessionId.value = result.session_id;
    remember(sessionId.value);
  }
  return sessionId.value;
}
async function send(text = input.value) {
  if (busy.value) return;
  const question = String(text).trim() || (selectedKnowledgeIds.value.length ? '请分别总结所选知识库文档的主要内容，保留各自的重要限制和引用。' : attachments.value.length ? '请总结上传文件的主要内容。' : '');
  if (!question) return;
  // Frontend source gate: block clearly source-dependent requests before they
  // are sent. The backend re-validates and hard-blocks regardless.
  const gateMessage = sourceGateQuestion();
  if (gateMessage) {
    error.value = gateMessage;
    return;
  }
  const selected = [...attachments.value];
  const publicIds = [...selectedKnowledgeIds.value];
  const payloadDatabaseId = databaseId.value;
  const payloadKnowledgeBaseId = knowledgeBaseId.value;
  const publicFiles = [...selectedKnowledgeFiles.value];
  error.value = '';
  const controller = new AbortController();
  const request = { controller, id: sessionId.value, startedAt: Date.now(), cancelled: false, liveTrace: ref([]), assistantMessage: null, error: '' };
  if (!request.id) { creatingRequest = request; creatingSession.value = true; }
  else {
    pendingRequests.set(request.id, request);
    liveTraces.delete(request.id);
  }
  let id = sessionId.value;
  // Clear only the submitted draft; subsequent typing belongs to the next question.
  input.value = '';
  attachments.value = [];
  selectedKnowledgeIds.value = [];
  try {
    id = await ensureSession(controller.signal);
    if (request.cancelled || disposed) return;
    request.id = id;
    request.baseMessages = [...messages.value];
    request.userMessage = { id: `local-user-${++localMessageSequence}`, role: 'user', text: question,
      attachments: selected, knowledge_document_ids: publicIds, knowledge_documents: publicFiles };
    request.summary = { session_id: id, title: sessions.value.find(item => item.session_id === id)?.title || question.slice(0, 36),
      message_count: request.baseMessages.length + 1, document_count: documents.value.length, updated_at: new Date().toISOString() };
    pendingRequests.set(id, request);
    requestSnapshots.set(id, request);
    if (creatingRequest === request) { creatingRequest = null; creatingSession.value = false; }
    messages.value = [...request.baseMessages, request.userMessage];
    if (!sessions.value.some(item => item.session_id === id)) sessions.value.push(request.summary);
    await scrollToEnd();
    const response = await fetch('/api/chat/stream', {
      method: 'POST',
      signal: controller.signal,
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ session_id: id, message: question, attachment_ids: selected.map(file => file.id),
        ...(publicIds.length ? { knowledge_document_ids: publicIds } : {}),
        database_id: payloadDatabaseId, knowledge_base_id: payloadKnowledgeBaseId }),
    });
    if (!response.ok || !response.body) throw new Error('任务请求失败');
    const reader = response.body.getReader(), decoder = new TextDecoder();
    let buffer = '', result;
    const handleLine = line => {
      if (!line.trim()) return;
      const event = JSON.parse(line);
      if (request.cancelled || disposed) return;
      const trace = liveTraces.get(id) || [];
      if (event.type === 'step') {
        const index = trace.findIndex(item => item.id === event.id);
        if (index < 0) trace.push(event); else trace[index] = event;
        const filtered = event.id !== 'accepted' ? trace.filter(item => item.id !== 'accepted') : trace;
        liveTraces.set(id, [...filtered]);
      } else if (event.type === 'result') result = event.result;
      else if (event.type === 'error') {
        liveTraces.set(id, [...trace, { id:'request-error', step:'请求失败', status:'failed', detail:event.message }]);
        throw new Error(event.message);
      }
    };
    while (true) {
      const { value, done } = await reader.read();
      buffer += decoder.decode(value, { stream: !done });
      const lines = buffer.split('\n'); buffer = lines.pop();
      if (done && buffer.trim()) { lines.push(buffer); buffer = ''; }
      for (const line of lines) handleLine(line);
      if (done) break;
    }
    handleLine(buffer);
    if (!result) throw new Error('事件流中断，未收到完成结果');
    if (request.cancelled || disposed) return;
    request.assistantMessage = { id:`local-answer-${result.run_id || ++localMessageSequence}`,
      role:'assistant',text:result.message,result };
    request.error = '';
    if (sessionId.value === id) messages.value = mergeRequestMessages(messages.value,request);
    liveTraces.delete(id);
  } catch (reason) {
    if (controller.signal.aborted || request.cancelled || disposed) return;
    request.error = reason.message;
    const trace = liveTraces.get(id) || [];
    liveTraces.set(id, trace.map(item => item.status === 'running' || item.status === 'pending'
      ? { ...item, status:'failed', detail:(item.detail || '')+'；连接失败，未收到完成事件' } : item));
    if (sessionId.value === id) error.value = request.error;
    if (sessionId.value === id && !input.value && !attachments.value.length && !selectedKnowledgeIds.value.length) {
      input.value = question;
      attachments.value = selected;
      selectedKnowledgeIds.value = publicIds;
    }
  } finally {
    if (creatingRequest === request) { creatingRequest = null; creatingSession.value = false; }
    if (pendingRequests.get(id) === request) {
      pendingRequests.delete(id);
    }
    if (!disposed && !request.cancelled && sessionId.value === id && !switching.value) {
      await scrollToEnd();
      textInput.value?.focus();
    }
    // History refresh never holds the composer lock or rewrites the draft.
    if (!disposed) void refreshSessions().catch(() => {});
  }
}
function selectKnowledgeDocuments(ids) {
  if (busy.value) return;
  if (attachments.value.length) {
    error.value = '请先移除本次问题中的上传附件，再选择公共知识库文档。';
    return;
  }
  if (new Set(ids).size > 50) { error.value = '单次提问最多添加50份知识库文档，请减少选择。'; return; }
  selectedKnowledgeIds.value = [...new Set(ids)];
  if (!input.value.trim()) input.value = '请分别总结所选知识库文档的主要内容，比较共性与差异，保留重要限制和真实引用。';
}
function addKnowledgeDocument(file) {
  if (busy.value) return;
  if (attachments.value.length) { error.value = '请先移除本次问题中的上传附件，再添加知识库文档。'; return; }
  if (!file?.doc_id || selectedKnowledgeIds.value.includes(file.doc_id)) return;
  if (selectedKnowledgeIds.value.length >= 50) { error.value = '单次提问最多添加50份知识库文档，请先移除部分文档。'; return; }
  registerKnowledgeFiles([file]);
  selectedKnowledgeIds.value = [...selectedKnowledgeIds.value, file.doc_id];
  error.value = '';
  panel.value = '';
  nextTick(() => textInput.value?.focus());
}
const fileDragDepth = ref(0);
const fileDragActive = computed(() => fileDragDepth.value > 0);
function isFileDrag(event) { return Array.from(event.dataTransfer?.types || []).includes('Files'); }
function preventFileNavigation(event) { if (isFileDrag(event)) event.preventDefault(); }
function enterFileDrag(event) {
  if (!isFileDrag(event)) return;
  event.preventDefault();
  fileDragDepth.value++;
}
function leaveFileDrag(event) {
  if (isFileDrag(event)) fileDragDepth.value = Math.max(0, fileDragDepth.value - 1);
}
function overFileDrag(event) {
  if (!isFileDrag(event)) return;
  event.preventDefault();
  event.dataTransfer.dropEffect = busy.value ? 'none' : 'copy';
}
async function dropFiles(event) {
  if (!isFileDrag(event)) return;
  event.preventDefault();
  event.stopPropagation();
  fileDragDepth.value = 0;
  await uploadSelectedFiles(Array.from(event.dataTransfer.files || []));
}
async function uploadFiles(event) {
  const files = Array.from(event.target.files || []);
  event.target.value = '';
  await uploadSelectedFiles(files);
}
async function uploadSelectedFiles(files) {
  if (!files.length) return;
  if (busy.value) { error.value = '请先停止当前回答或等待上传完成，再添加文件。'; return; }
  if (selectedKnowledgeIds.value.length) { error.value = '请先取消公共文档选择，再上传附件。'; return; }
  uploading.value = true;
  liveTraces.delete(sessionId.value);
  error.value = '';
  const errors = [];
  try {
    const id = await ensureSession();
    for (const file of files) {
      try {
        if (!/\.(pdf|ppt|pptx|docx|txt|md|png|jpg|jpeg|webp|bmp|tif|tiff)$/i.test(file.name)) throw new Error('支持 PDF、PPT/PPTX、DOCX、TXT、Markdown 和图片');
        if (file.size > 10 * 1024 * 1024) throw new Error('文件不能超过 10 MB');
        const form = new FormData();
        form.append('file', file);
        const result = await api(`/api/sessions/${id}/documents`, { method: 'POST', body: form });
        attachments.value.push(result);
        documents.value.push(result);
      } catch (reason) {
        errors.push(`${file.name}：${reason.message}`);
        if (reason.assessment) documentAssessments.value.push(reason.assessment);
      }
    }
    await Promise.all([refreshSessions(), refreshKnowledge()]);
  } catch (reason) { errors.push(reason.message); }
  finally {
    uploading.value = false;
    error.value = errors.join('；');
    textInput.value?.focus();
  }
}
function keydown(event) {
  if (event.key === 'Enter' && !event.shiftKey && !event.isComposing && (settings.enterSend || event.ctrlKey || event.metaKey)) {
    event.preventDefault();
    send();
  }
}
function fileSize(bytes) { return bytes < 1024 ? `${bytes} B` : `${Math.round(bytes / 1024)} KB`; }

onMounted(async () => {
  await refreshKnowledge();
  try {
    await refreshSessions();
    let saved = '';
    try { saved = localStorage.getItem('xiaoyun-session') || ''; } catch { /* Optional preference. */ }
    if (sessions.value.some(item => item.session_id === saved)) await openSession(saved);
  } catch (reason) { error.value = reason.message; }
});
</script>

<template>
  <div @dragover="preventFileNavigation" @drop="preventFileNavigation" class="app-shell" :class="{ 'sidebar-visible': sidebarOpen, 'evidence-visible': !!evidenceEntry, resizing }" :style="{ '--sidebar-width': sidebarWidth + 'px', '--open-history-width': sidebarOpen ? sidebarWidth + 'px' : '0px', '--answer-size': settings.fontSize + 'px' }">
    <button v-if="sidebarOpen" class="sidebar-backdrop" aria-label="关闭侧栏" @click="sidebarOpen = false" />
    <aside class="sidebar" aria-label="对话历史" :inert="evidenceOverlay">
      <div class="sidebar-brand"><span class="brand-mark"><AppIcon name="cloud" /></span><strong>小云助手</strong><button class="icon-button collapse" aria-label="收起侧栏" @click="sidebarOpen = false"><AppIcon name="panel" /></button></div>
      <button class="new-chat" :disabled="uploading || switching || historyBusy || creatingSession" @click="newConversation()"><AppIcon name="edit" />开启新对话<span>＋</span></button>
      <label class="history-search"><AppIcon name="search" /><input v-model="search" placeholder="搜索对话" aria-label="搜索对话历史" /></label>
      <div class="history-heading">对话记录 <span>{{ visibleSessions.length }}</span></div>
      <ConversationHistory :sessions="filteredSessions" :selected="sessionId" :disabled="uploading || switching || historyBusy || creatingSession" :generating="loading" :generating-ids="generatingIds" :search="search" @open="openSession" @updated="sessionUpdated" @deleted="sessionDeleted" @busy="value => historyBusy = value" />
      <DataSourcePanel :database-id="databaseId" :knowledge-base-id="knowledgeBaseId" :busy="busy"
        @update:databaseId="value => { databaseId = value; closeEvidence(); }"
        @update:knowledgeBaseId="value => { knowledgeBaseId = value; closeEvidence(); }" />
      <button class="sidebar-footer" aria-label="打开个人空间设置" @click="panel = 'settings'"><span class="profile-avatar">{{ (settings.name || '我').slice(0, 1) }}</span><div><strong>{{ settings.name || '我的空间' }}</strong><small>个人偏好与本地设置</small></div><span class="settings-arrow">⚙</span></button>
    </aside>
    <div v-if="sidebarOpen" class="sidebar-resizer" role="separator" tabindex="0" aria-label="调整历史记录宽度" aria-orientation="vertical" :aria-valuenow="Math.round(sidebarWidth)" :aria-valuemin="230" :aria-valuemax="Math.round(maxSidebar)" @pointerdown="dragStart" @pointermove="dragMove" @pointerup="resizing = false" @pointercancel="resizing = false" @lostpointercapture="resizing = false" @keydown="resizeKey" @dblclick="settings.sidebarWidth = 270" title="拖动调整宽度，双击恢复默认" />
    <main class="workspace" :inert="evidenceOverlay" @dragenter="enterFileDrag" @dragleave="leaveFileDrag" @dragover="overFileDrag" @drop="dropFiles">
      <header class="topbar">
        <div class="topbar-title"><button v-if="!sidebarOpen" class="icon-button" aria-label="展开侧栏" @click="sidebarOpen = true"><AppIcon name="panel" /></button><strong>小云助手</strong><span class="version-tag">数据与知识助手</span></div>
        <div class="topbar-actions"><button class="knowledge-status" :class="knowledge?.status" aria-label="打开知识库信息" @click="panel = 'knowledge'"><AppIcon name="book" /><span>知识库 · {{ knowledge ? ({ ready: '就绪', partial: '部分可用', unavailable: '不可用' }[knowledge.status] || '未知') : '查看状态' }}</span><span>↗</span></button><button class="dock-toggle" :class="{ active: dockOpen && dockedResult }" aria-label="切换右侧证据面板" title="回答依据与执行详情" @click="dockOpen = !dockOpen">☰<span class="sr-only">打开/收起证据面板</span></button></div>
      </header>
      <div ref="scrollArea" class="conversation-scroll">
        <section v-if="!messages.length" class="welcome">
          <div class="welcome-symbol"><AppIcon name="cloud" /></div><div class="eyebrow">你好，我是小云</div><h1>今天，有什么可以帮你？</h1>
          <p>查数据、读文档、找依据。把问题交给我，我们一起理清思路。</p>
          <div class="suggestions"><button v-for="example in examples" :key="example.title" :disabled="busy" @click="send(example.text)"><AppIcon :name="example.icon" /><strong>{{ example.title }}</strong><span>{{ example.text }}</span><span class="suggestion-arrow">↗</span></button></div>
          <div class="welcome-note"><AppIcon name="file" />也可以上传一份文档，从你的资料开始</div>
          <p v-if="knowledge?.data_label" class="data-label">{{ knowledge.data_label }}</p>
        </section>
        <section v-else class="conversation" :aria-label="currentTitle" aria-live="polite" aria-relevant="additions">
          <article v-for="(message, index) in messages" :key="message.id || index" class="message" :class="[message.role, { 'dock-selected': message.role === 'assistant' && String(message.id || index) === String(dockMessageId) }]" @click="message.role === 'assistant' && dockOnMessage(message, index)">
            <div v-if="message.role === 'assistant'" class="assistant-avatar"><AppIcon name="cloud" /></div>
            <div class="message-body">
              <div v-if="message.role === 'assistant'" class="message-author">小云助手</div>
              <div v-if="message.attachments?.length" class="message-files"><button v-for="file in message.attachments" :key="file.id" type="button" :aria-label="`查看文件 ${file.name}`" :title="`点击查看文件 ${(file.parsing_warnings || []).join('；')}`" @click="openAttachment(file)"><AppIcon name="file" />{{ file.name }}{{ file.parsing_status === 'partial' ? '（部分可读）' : '' }}</button></div>
              <div v-if="message.knowledge_document_ids?.length" class="message-files"><span v-for="id in message.knowledge_document_ids" :key="id" class="knowledge-message-file"><AppIcon name="file" />{{ knowledgeFileName(id, message) }}</span></div>
              <ResultOverview v-if="message.result" :result="message.result" />
              <TracePanel v-if="message.result && (message.result.trace?.length || message.result.plan?.length || message.result.elapsed_ms != null)" :trace="message.result.trace || []" :plan="message.result.plan || []" :status="message.result.status" :elapsed-ms="message.result.elapsed_ms" :initially-open="settings.showDetails" />
              <FormulaResults v-if="message.role === 'assistant' && ['formula','symbol','raw_values'].every(key => message.result?.data?.columns?.includes(key))" :result="message.result" :text="message.text" @citation="token => showCitation(message,token,index)" />
              <AnswerText v-else-if="message.role === 'assistant'" :text="message.text" :evidence="message.result?.evidence || []" :class="{ 'failed-message': message.result?.status === 'failed' }" @citation="token => showCitation(message, token, index)" />
              <p v-else class="message-text">{{ message.text }}</p>
              <template v-if="message.result">
                <small v-if="message.result.answer_source" class="answer-source">{{ message.result.answer_source }}</small>
                <QueryChart v-if="chartEligible(message.result).eligible" :data="message.result.data" :hints="message.result.query_conditions || {}" />
                <ResultTable v-if="!['formula','symbol','raw_values'].every(key => message.result.data?.columns?.includes(key))" :data="message.result.data" :sql="message.result.sql || ''" :params="message.result.sql_params" />
                <div v-if="message.result.evidence?.length || message.result.evidence_assessment || message.result.verified_message" class="evidence-entry">
                  <button type="button" class="evidence-open-link" :disabled="switching" aria-controls="evidence-sidebar" :aria-expanded="evidenceEntry?.key === evidenceKey(message, index)" @click="openEvidence(message, index)"><AppIcon name="book" /><span>查看证据与引用</span><small>{{ message.result.evidence?.length || 0 }} {{ message.result.evidence_assessment?.candidate_sources_only ? '条候选' : '条引用' }}</small><span aria-hidden="true">↗</span></button>
                </div>
              </template>
            </div>
          </article>
          <div v-if="loading" class="thinking"><span class="assistant-avatar"><AppIcon name="cloud" /></span><TracePanel :loading="true" :started-at="processingStartedAt" :trace="liveTrace" /></div>
          <TracePanel v-if="!loading && liveTrace.length" :trace="liveTrace" status="failed" :initially-open="true" /><ClarificationCard v-if="!loading" :options="clarificationOptions" @select="send" />
        </section>
        <div v-if="documents.length || documentAssessments.length" class="session-files-container"><details class="session-files"><summary><AppIcon name="file" />当前对话的资料 · {{ documents.length }} 个文件</summary><div><div v-for="file in documents" :key="file.id" class="session-file"><button type="button" :disabled="switching" :aria-label="`查看文件 ${file.name}`" @click="openAttachment(file)"><AppIcon name="file" />{{ file.name }}</button><button type="button" :disabled="switching || attachments.some(item => item.id === file.id)" :aria-label="`添加到问题 ${file.name}`" @click="attachments.push(file)">添加到问题</button><a v-if="file.markdown_url" :href="file.markdown_url">下载 Markdown</a><button type="button" :disabled="busy" @click="openCorrection(file)">文字校正</button><DocumentAssessment :file="file" /></div><section v-for="record in documentAssessments" :key="record.assessment_id" class="assessed-session-file"><strong>{{ record.filename }} · 解析未完成</strong><DocumentAssessment :file="record" /></section></div></details></div>
      </div>
      <div class="composer-area" :class="{ 'file-drag-active': fileDragActive }"><div class="composer-container">
        <p v-if="fileDragActive" class="file-drop-hint" role="status">{{ busy ? '当前任务处理中，请稍后添加文件' : '松开文件即可上传并添加到本次问题' }}</p>
        <p v-if="error" class="error-banner" role="alert">{{ error }}<button aria-label="关闭提示" @click="error = ''">×</button></p>
        <form class="composer" @submit.prevent="send()">
          <KnowledgeAttachments :files="selectedKnowledgeFiles" :disabled="switching" @remove="id => selectedKnowledgeIds = selectedKnowledgeIds.filter(item => item !== id)" />
          <div v-if="attachments.length" class="attachment-list"><div v-for="file in attachments" :key="file.id" class="attachment" :title="(file.parsing_warnings || []).join('；')"><button type="button" class="attachment-open" :disabled="switching" :aria-label="`查看文件 ${file.name}`" title="点击查看文件" @click="openAttachment(file)"><span class="file-icon"><AppIcon name="file" /></span><span class="attachment-info"><strong>{{ file.name }}</strong><small>{{ fileSize(file.size) }} · {{ file.parsing_status === 'partial' ? '部分可读，模糊段落未作为依据' : '已入库 · 已就绪' }}</small></span></button><button type="button" :disabled="busy" :aria-label="`文字校正 ${file.name}`" @click="openCorrection(file)">文字校正</button><button type="button" class="icon-button" :disabled="switching" :aria-label="`取消附加 ${file.name}`" @click="attachments = attachments.filter(item => item.id !== file.id)"><AppIcon name="close" /></button></div></div>
          <label class="sr-only" for="question">向小云助手提问</label><textarea id="question" ref="textInput" v-model="input" rows="2" :disabled="switching" placeholder="向小云提问，或上传文档一起聊聊…" @keydown="keydown" />
          <div class="composer-actions"><input ref="fileInput" class="sr-only" type="file" accept=".pdf,.ppt,.pptx,.docx,.txt,.md,.png,.jpg,.jpeg,.webp,.bmp,.tif,.tiff" multiple :disabled="busy" aria-label="选择上传文件" @change="uploadFiles" /><button type="button" class="upload-button" :disabled="busy" title="上传 PDF、PPT/PPTX、DOCX、TXT、Markdown、图片，单个不超过 10 MB" @click="fileInput?.click()"><AppIcon name="plus" /><span>{{ uploading ? '正在解析文件…' : '上传文件' }}</span></button><span class="input-hint">{{ settings.enterSend ? 'Enter 发送 · Shift + Enter 换行' : 'Ctrl / ⌘ + Enter 发送 · Enter 换行' }}</span><button v-if="loading" type="button" class="send-button" aria-label="停止思考" title="停止当前回答" @click="stopThinking">■</button><button v-else class="send-button" :disabled="!canSend" aria-label="发送消息"><AppIcon name="arrow" /></button></div>
        </form>
        <p class="composer-disclaimer">小云的回答可能存在偏差，请结合引用核实重要信息。</p>
      </div></div>
    </main>
    <EvidenceDrawer v-if="evidenceEntry" :key="evidenceEntry.key" :entry="evidenceEntry" :overlay="evidenceOverlay" v-model:show-failures="settings.showEvidenceFailures" @close="closeEvidence" />
    <WorkspacePanel :panel="panel" :settings="settings" :storage-error="storageError" :knowledge="knowledge" :busy="busy" :knowledge-base-id="knowledgeBaseId" :added-document-ids="selectedKnowledgeIds" :has-attachments="attachments.length > 0" @close="panel = ''" @reset="reset" @knowledge="value => knowledge = value" @catalog="registerKnowledgeFiles" @add-document="addKnowledgeDocument" @select-documents="selectKnowledgeDocuments" @deleted-documents="ids => { selectedKnowledgeIds = selectedKnowledgeIds.filter(id => !ids.includes(id)); }" />
    <DocumentPreview :file="previewFile?.file" :session-id="previewFile?.sessionId" @close="previewFile = null" />
    <TextCorrectionDialog :file="correctionFile?.file" :session-id="correctionFile?.sessionId" @close="correctionFile = null" @changed="refreshCorrectedDocuments" />
  </div>
</template>

<style scoped>
.evidence-entry { margin-top:20px; padding-top:12px; border-top:1px solid #e6ebe4; }
.evidence-open-link { display:inline-flex; flex-wrap:wrap; align-items:center; gap:8px; padding:6px 0; color:#356b52; text-align:left; font:inherit; font-size:13px; cursor:pointer; }
.evidence-open-link > span:first-of-type { text-decoration:underline; text-underline-offset:4px; }
.evidence-open-link svg { width:17px; height:17px; }
.evidence-open-link small { color:#7d897c; font-size:11px; }
.evidence-open-link:hover { color:#234f3b; }
.evidence-open-link:focus-visible { outline:2px solid #477f5f; outline-offset:5px; border-radius:4px; }
.file-drag-active .composer { outline: 2px dashed #64865a; background: #f5f9f2; }
.file-drop-hint { margin: 0 0 8px; color: #47633f; text-align: center; }
.knowledge-message-file { display: flex; align-items: center; gap: 6px; padding: 5px 8px; border: 1px solid #dfe6d7; border-radius: 6px; font-size: 10px; color: #7c886f; overflow-wrap: anywhere; }
</style>
