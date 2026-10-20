<script setup>
import { computed, nextTick, onUnmounted, ref, watch } from 'vue';
import { correctionCandidates, correctedPreview, defaultCorrections } from '../textCorrections.js';

const props = defineProps({ file: Object, sessionId: String });
const emit = defineEmits(['close', 'changed']);
const element = ref(null), report = ref(null), selected = ref([]), result = ref(null);
const loading = ref(false), saving = ref(false), error = ref('');
let sequence = 0, controller;
const candidates = computed(() => correctionCandidates(report.value?.text_checks));
const preview = computed(() => {
  if (result.value?.corrected_text != null) return { text: result.value.corrected_text };
  try { return { text: correctedPreview(report.value?.pages || [], candidates.value, selected.value) }; }
  catch (reason) { return { error: reason.message }; }
});
const base = () => `/api/sessions/${encodeURIComponent(props.sessionId)}/documents/${encodeURIComponent(props.file.id)}`;
async function load() {
  const seq = ++sequence;
  controller?.abort(); controller = new AbortController();
  report.value = null; result.value = null; selected.value = []; error.value = '';
  loading.value = true;
  try {
    const response = await fetch(base() + '/text-checks', { signal: controller.signal });
    const payload = await response.json();
    if (seq !== sequence) return;
    if (!response.ok) throw new Error(payload.detail || '文字检测失败');
    if (payload.document_id !== props.file.id) throw new Error('响应与所选附件不一致');
    report.value = payload;
    selected.value = defaultCorrections(correctionCandidates(payload.text_checks));
  } catch (reason) { if (seq === sequence) error.value = reason.message; }
  finally { if (seq === sequence) loading.value = false; }
}
watch(() => [props.file?.id, props.sessionId], async ([id, session]) => {
  const seq = ++sequence;
  controller?.abort(); report.value = null; result.value = null;
  saving.value = false; error.value = ''; loading.value = false;
  await nextTick();
  if (seq !== sequence) return;
  if (id && session) { if (!element.value?.open) element.value?.showModal(); await load(); }
  else if (element.value?.open) element.value.close();
}, { immediate: true });
onUnmounted(() => { ++sequence; controller?.abort(); });
function selectTraditional() {
  selected.value = candidates.value.filter(item => item.kind === 'traditional' && item.suggested).map(item => item.candidate_id);
}
async function apply() {
  if (!selected.value.length || preview.value.error || saving.value || result.value) return;
  const seq = sequence, sid = props.sessionId;
  saving.value = true; error.value = '';
  try {
    const confirmations = candidates.value.filter(item => selected.value.includes(item.candidate_id)).map(item => ({
      page_no: item.page_no, start: item.start, end: item.end, original: item.original,
      replacement: item.suggested, basis: item.basis, special_verify: !!(item.special_verify || item.require_review || item.ambiguous),
      confirmed: true, confirmed_by: 'user',
    }));
    const response = await fetch(base() + '/corrections', { method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ original_hash: report.value.original_hash, expected_text_hash: report.value.text_hash, confirmations }),
    });
    const payload = await response.json();
    if (response.ok) emit('changed', sid);
    if (seq !== sequence) return;
    if (!response.ok) throw new Error(typeof payload.detail === 'string' ? payload.detail : '校正失败，请重新读取候选');
    result.value = payload;
  } catch (reason) { if (seq === sequence) error.value = reason.message; }
  finally { if (seq === sequence) saving.value = false; }
}
async function restore() {
  if (saving.value) return;
  const seq = sequence, sid = props.sessionId;
  saving.value = true; error.value = '';
  try {
    const response = await fetch(base() + '/corrections/restore', { method: 'POST' });
    const payload = await response.json();
    if (response.ok) emit('changed', sid);
    if (seq !== sequence) return;
    if (!response.ok) throw new Error(payload.detail || '恢复失败');
    saving.value = false;
    await load();
  } catch (reason) { if (seq === sequence) error.value = reason.message; }
  finally { if (seq === sequence) saving.value = false; }
}
</script>

<template>
  <dialog ref="element" class="text-correction-dialog" aria-label="文字校正" @cancel.prevent="emit('close')">
    <header><div><h2>文字校正</h2><p>{{ file?.name }}</p></div><button type="button" @click="emit('close')">关闭</button></header>
    <p v-if="loading" role="status">正在检测繁体字与错别字…</p>
    <p v-if="error" role="alert" class="warning">{{ error }} <button v-if="!saving" @click="load">重新读取</button></p>
    <template v-if="report">
      <p class="note">繁体字通过 OpenCC 提供简体建议。请核对专名、多义字、数字与引文，点击确认后应用到当前附件正文。</p>
      <p v-if="report.text_checks?.reason" class="warning">{{ report.text_checks.reason }}</p>
      <div class="actions" v-if="!result">
        <button type="button" :disabled="saving" @click="selectTraditional">选择全部繁体建议</button>
        <button type="button" :disabled="saving" @click="selected = []">清空选择</button>
      </div>
      <div class="candidates">
        <label v-for="item in candidates" :key="item.candidate_id">
          <input type="checkbox" v-model="selected" :value="item.candidate_id" :disabled="saving || !!result" />
          <span>{{ item.kind === 'traditional' ? '繁简' : '错字' }}：<del>{{ item.original }}</del> → <strong>{{ item.suggested }}</strong></span>
          <small v-if="item.ambiguous || item.require_review || item.special_verify">需单独核对</small>
          <span class="context">{{ item.context }}</span>
        </label>
        <p v-if="!candidates.length">未检出繁体字或常见错别字候选。</p>
      </div>
      <div class="body-comparison">
        <section><h3>{{ result || report.has_corrections ? '本次校正前正文' : '原文' }}</h3><pre>{{ report.current_text }}</pre></section>
        <section><h3>{{ result ? '校正后正文' : '校正预览' }}</h3><p v-if="preview.error" class="warning">{{ preview.error }}</p><pre v-else>{{ preview.text }}</pre></section>
      </div>
      <p v-if="result" role="status">已应用 {{ result.applied_count }} 项校正，当前附件的后续问答使用校正后的正文。</p>
      <div class="actions">
        <button v-if="!result" type="button" :disabled="saving || !selected.length || !!preview.error" @click="apply">{{ saving ? '正在应用…' : `确认并应用校正（${selected.length}项）` }}</button>
        <button v-if="result" type="button" :disabled="saving" @click="load">继续校正</button>
        <a v-if="result || report.has_corrections" :href="result?.download_url || report.download_url">下载校正正文</a>
        <button v-if="result || report.has_corrections" type="button" :disabled="saving" @click="restore">恢复上传时正文</button>
      </div>
      <ul v-if="result"><li v-for="change in result.change_log" :key="change.id">{{ change.original_text }} → {{ change.replacement }}</li></ul>
    </template>
  </dialog>
</template>

<style scoped>
.text-correction-dialog { width:min(1050px,92vw); max-height:88vh; overflow:auto; border:1px solid #d7e2da; border-radius:14px; padding:24px; color:#263e30; }
dialog::backdrop { background:#17291f66; }
header,.actions { display:flex; gap:12px; align-items:center; flex-wrap:wrap; }
header { justify-content:space-between; } h2,h3 { margin:0 0 8px; } header p { margin:0; overflow-wrap:anywhere; }
button,a { border:1px solid #b8cebf; border-radius:7px; padding:8px 12px; background:#f1f7f2; color:#315d44; cursor:pointer; font:inherit; }
button:disabled { opacity:.5; cursor:default; } .note,.context { color:#65746a; line-height:1.7; } .warning,small { color:#8c531d; }
.candidates { max-height:220px; overflow:auto; margin:16px 0; } .candidates label { display:flex; gap:8px; flex-wrap:wrap; padding:9px; border-bottom:1px solid #e3ebe5; }
.context { flex-basis:100%; margin-left:25px; font-size:13px; } del { color:#9e3b2b; } .body-comparison { display:grid; grid-template-columns:1fr 1fr; gap:16px; margin:18px 0; }
.body-comparison section { min-width:0; padding:14px; background:#f4f7f4; border-radius:8px; } pre { white-space:pre-wrap; overflow-wrap:anywhere; font:inherit; line-height:1.9; max-height:320px; overflow:auto; }
@media(max-width:700px) { .body-comparison { grid-template-columns:1fr; } }
</style>
