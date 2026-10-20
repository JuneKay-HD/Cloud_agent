<script setup>
import { computed, ref, watch, nextTick, onMounted, onUnmounted } from 'vue';
import EvidencePanel from './EvidencePanel.vue';
import AnswerText from './AnswerText.vue';
import AppIcon from './AppIcon.vue';
import { groupEvidence } from '../evidenceGroups';
import { DEFAULT_SHOW_EVIDENCE_FAILURES } from '../evidenceVisibility';

const props = defineProps({ entry: { type: Object, required: true }, overlay: Boolean,
  showFailures: { type: Boolean, default: DEFAULT_SHOW_EVIDENCE_FAILURES } });
const emit = defineEmits(['close', 'update:showFailures']);
const drawer = ref(null), body = ref(null), closeButton = ref(null), wide = ref(false);
const result = computed(() => props.entry.result);
const evidence = computed(() => result.value.evidence || []);
const groups = computed(() => groupEvidence(evidence.value));
const citations = computed(() => evidence.value.filter(item => item.source_type === 'document' && item.metadata?.citation));
let returnFocus = null, disposed = false, selectionVersion = 0;

async function revealCitation(token) {
  const version = ++selectionVersion;
  await nextTick();
  if (disposed || version !== selectionVersion || !body.value) return;
  const label = typeof token === 'string' ? token : token.citation;
  const match = /^\[(\d+)(?:\.\d+)?\]$/.exec(label || '');
  const item = match && evidence.value.find(item => item.source_type === 'document' && item.metadata?.citation === Number(match[1]));
  if (!item) return;
  const element = document.getElementById(props.entry.idPrefix + 'evidence-' + item.evidence_id);
  if (!element || !body.value.contains(element)) return;
  const group = element.closest('details.document-group');
  if (group && !group.open) { group.dataset.citationTarget = 'true'; group.open = true; }
  await nextTick();
  if (disposed || version !== selectionVersion) return;
  element.scrollIntoView({ behavior: 'smooth', block: 'start' });
  element.dispatchEvent(new CustomEvent('locate-source', { detail: {
    quote: typeof token === 'object' ? token.quote : undefined, subcitation: label.slice(1, -1),
  } }));
}

watch(() => props.entry.sequence, async sequence => {
  await nextTick();
  if (disposed || sequence !== props.entry.sequence) return;
  if (props.entry.target) void revealCitation(props.entry.target);
  else if (body.value) body.value.scrollTop = 0;
}, { immediate: true, flush: 'post' });
watch(() => props.overlay, async overlay => {
  if (overlay) { await nextTick(); if (!disposed) closeButton.value?.focus({ preventScroll: true }); }
});

function keydown(event) {
  // Native preview/zoom dialogs own Escape and focus while they are open.
  if (event.defaultPrevented || document.querySelector('dialog[open]')) return;
  if (event.key === 'Escape') { event.preventDefault(); emit('close'); return; }
  if (event.key !== 'Tab' || !props.overlay || !drawer.value) return;
  const elements = [...drawer.value.querySelectorAll('button:not(:disabled),a[href],input:not(:disabled),select:not(:disabled),textarea:not(:disabled),summary,[tabindex="0"]')]
    .filter(element => element.getClientRects().length > 0);
  const first = elements[0], last = elements.at(-1);
  if (!first) { event.preventDefault(); drawer.value.focus(); return; }
  if (!drawer.value.contains(document.activeElement) || (event.shiftKey && document.activeElement === first)) {
    event.preventDefault(); (event.shiftKey ? last : first).focus();
  } else if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first.focus(); }
}
onMounted(() => {
  returnFocus = document.activeElement;
  closeButton.value?.focus({ preventScroll: true });
  document.addEventListener('keydown', keydown);
});
onUnmounted(() => {
  disposed = true;
  document.removeEventListener('keydown', keydown);
  if (returnFocus?.isConnected) returnFocus.focus({ preventScroll: true });
});
</script>

<template>
  <button v-if="overlay" class="evidence-backdrop" aria-label="收起证据面板" tabindex="-1" @click="emit('close')" />
  <aside id="evidence-sidebar" ref="drawer" class="evidence-sidebar" :class="{ overlay, wide }" :role="overlay ? 'dialog' : 'complementary'" :aria-modal="overlay ? true : undefined" aria-labelledby="evidence-sidebar-title" tabindex="-1">
    <header class="drawer-header">
      <div class="drawer-heading"><small>回答依据</small><h2 id="evidence-sidebar-title">证据与引用</h2></div>
      <button v-if="!overlay" type="button" class="width-button" :aria-pressed="wide" @click="wide = !wide">{{ wide ? '标准宽度' : '加宽阅读' }}</button>
      <button ref="closeButton" type="button" class="drawer-close" aria-label="关闭证据侧栏" title="关闭证据侧栏（Esc）" @click="emit('close')"><AppIcon name="close" /></button>
    </header>
    <div class="drawer-context">
      <p v-if="entry.question" class="source-question" :title="entry.question">{{ entry.question }}</p>
      <p class="source-count">{{ evidence.length }} {{ result.evidence_assessment?.candidate_sources_only ? '条检索候选' : '条引用' }} · {{ groups.length }} 份来源<span>与左侧回答对应</span></p>
      <div class="location-visibility"><label><input type="checkbox" :checked="showFailures" @change="emit('update:showFailures', $event.target.checked)" />显示完整定位状态</label><small v-if="!showFailures">原页标签仅显示定位成功项</small></div>
      <nav v-if="citations.length" class="citation-index" aria-label="引用目录"><button v-for="item in citations" :key="item.evidence_id" type="button" :title="item.title" @click="revealCitation(`[${item.metadata.citation}]`)">[{{ item.metadata.citation }}]</button></nav>
    </div>
    <div ref="body" class="evidence-scroll" tabindex="0" aria-label="证据详情">
      <details v-if="result.verified_message" class="verified-answer"><summary>查看原始核验答案</summary><AnswerText :text="result.verified_message" :evidence="evidence" @citation="revealCitation" /></details>
      <EvidencePanel v-if="result.task_type !== 'chat' || evidence.length" class="drawer-evidence" :evidence="evidence" :assessment="result.evidence_assessment" :status="result.status" :id-prefix="entry.idPrefix" :show-failures="showFailures" />
    </div>
    <footer>引用来自本次回答的执行结果，可展开来源核对摘录与原页。</footer>
  </aside>
</template>

<style scoped>
.evidence-sidebar { width:clamp(420px,40vw,640px); max-width:calc(100vw - var(--open-history-width,0px) - 380px); height:100%; flex-shrink:0; min-width:0; min-height:0; display:flex; flex-direction:column; border-left:1px solid #dfe6df; background:#fff; color:#334838; overflow:hidden; }
.evidence-sidebar.wide { width:min(840px,58vw); }
.drawer-header { display:flex; align-items:center; gap:12px; padding:18px 20px; border-bottom:1px solid #e8ece6; flex-shrink:0; }
.drawer-heading { flex:1; min-width:0; }
.drawer-heading small { display:block; margin-bottom:4px; color:#849080; font-size:11px; }
h2 { margin:0; font-size:18px; font-weight:650; }
button { font:inherit; cursor:pointer; }
.drawer-close { display:grid; place-items:center; width:34px; height:34px; border:1px solid #e2e8df; border-radius:8px; background:#fff; color:#667363; flex-shrink:0; }
.drawer-close svg { width:18px; height:18px; }
.width-button { padding:6px 9px; font-size:12px; color:#4b6d53; border:1px solid #dfe7dd; background:#f6f9f3; border-radius:6px; white-space:nowrap; }
.drawer-context { padding:14px 20px; background:#fafbf8; border-bottom:1px solid #e8ece6; flex-shrink:0; }
.source-question { margin:0 0 8px; color:#41553d; overflow:hidden; display:-webkit-box; -webkit-line-clamp:2; -webkit-box-orient:vertical; font-size:13px; line-height:1.65; overflow-wrap:anywhere; }
.source-count { display:flex; flex-wrap:wrap; gap:6px 14px; margin:0; color:#7a8775; font-size:11px; }
.source-count span { color:#959d91; }
.location-visibility { display:flex; flex-wrap:wrap; align-items:center; gap:6px 12px; margin-top:10px; color:#788574; font-size:11px; }
.location-visibility label { display:flex; align-items:center; gap:6px; cursor:pointer; }
.location-visibility input { accent-color:#477f5f; }
.location-visibility small { font-size:11px; color:#8a9585; }
.citation-index { display:flex; flex-wrap:wrap; gap:6px; max-height:96px; overflow:auto; margin-top:12px; }
.citation-index button { padding:4px 7px; background:#fff; border:1px solid #dce5d8; border-radius:5px; color:#456849; font-size:12px; }
.citation-index button:hover { background:#edf4e8; }
.evidence-scroll { flex:1; min-height:0; overflow:auto; overscroll-behavior:contain; scrollbar-width:thin; padding:20px; font-size:14px; line-height:1.75; overflow-wrap:anywhere; }
.evidence-scroll :deep(.drawer-evidence > h2) { display:none; }
.evidence-scroll :deep(.original-page) { max-height:60dvh; }
.verified-answer { margin-bottom:18px; border-bottom:1px solid #e8ece6; padding-bottom:14px; }
.verified-answer summary { font-size:13px; cursor:pointer; font-weight:600; }
footer { padding:10px 20px; flex-shrink:0; border-top:1px solid #e8ece6; color:#8a9484; background:#fafbf8; font-size:11px; line-height:1.6; }
button:focus-visible,summary:focus-visible { outline:2px solid #477f5f; outline-offset:3px; }
.evidence-scroll:focus-visible { outline:2px solid #477f5f; outline-offset:-2px; }
.evidence-backdrop { position:fixed; inset:0; background:#1b2a2266; z-index:59; border:0; }
.evidence-sidebar.overlay { position:fixed; inset:0 0 0 auto; z-index:60; width:min(640px,100vw); max-width:100vw; height:100dvh; box-shadow:-12px 0 40px #1b2a2222; }
@media(max-width:600px) { .drawer-header { padding:14px 16px; } .drawer-context { padding:12px 16px; } .evidence-scroll { padding:16px; } footer { padding:10px 16px max(10px,env(safe-area-inset-bottom)); } }
</style>
