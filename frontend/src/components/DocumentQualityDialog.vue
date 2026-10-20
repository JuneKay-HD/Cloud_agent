<script setup>
// DocumentQualityDialog.vue — 知识库文档的“查看文档分析质量”弹窗。
// 首次查看按需测量原件；报告独立缓存，校正生成用户确认的副本。
// 每次请求都携带 knowledge_base_id 与文档身份；同名文档不会混淆。
// 文档切换时取消旧请求并校验响应身份，旧响应永远不会覆盖新文档。
import { computed, ref, watch, nextTick, onUnmounted } from 'vue';
import { qualityItems } from '../quality.js';

const props = defineProps({
  open: Boolean,
  file: { type: Object, default: null },
  knowledgeBaseId: { type: String, default: '' },
});
const emit = defineEmits(['close']);

const element = ref(null);
const loading = ref(false);
const error = ref('');
const report = ref(null);
const activeTab = ref('overview');
const stale = ref(false);
let requestSeq = 0;
let controller = null;
const loadingText = ref('正在读取分析记录…');
const selectedCandidates = ref([]);
const applying = ref(false);
const correctionResult = ref(null);
const correctionError = ref('');

const STATUS_LABELS = {
  pass: '通过', suspect: '存疑', fail: '失败', unknown: '未知',
  not_applicable: '不适用', completed: '已完成', complete: '已完成', partial: '部分完成',
  blocked: '已阻断', not_yet_analyzed: '尚未分析',
};
const statusLevel = { fail: 'fail', suspect: 'suspect', unknown: 'unknown', not_applicable: 'na', pass: 'pass', completed: 'ok', partial: 'suspect', blocked: 'fail' };

watch(() => [props.open, props.file?.doc_id, props.knowledgeBaseId], async ([open]) => {
  const watchSeq = ++requestSeq;
  controller?.abort();
  selectedCandidates.value = []; correctionResult.value = null; correctionError.value = '';
  await nextTick();
  if (watchSeq !== requestSeq) return;
  if (open) {
    activeTab.value = 'overview';
    if (!element.value?.open) element.value?.showModal();
    if (props.file) await load();
  } else {
    if (element.value?.open) element.value.close();
    report.value = null;
    error.value = '';
    loading.value = false;
  }
});
onUnmounted(() => { ++requestSeq; controller?.abort(); });

function knowledgeParams() {
  const params = new URLSearchParams();
  if (props.knowledgeBaseId) params.set('knowledge_base_id', props.knowledgeBaseId);
  return params.toString();
}
function fileIdentity() {
  return props.file ? String(props.file.doc_id || props.file.source_hash || '') : '';
}

async function load(force = false) {
  const identity = fileIdentity();
  if (!identity) { report.value = null; error.value = ''; return; }
  const seq = ++requestSeq;
  controller?.abort();
  controller = new AbortController();
  loading.value = true; error.value = '';
  loadingText.value = '正在读取分析记录…';
  stale.value = false;
  try {
    const url = `/api/documents/${encodeURIComponent(identity)}/analysis?${knowledgeParams()}`;
    const response = await fetch(url, { signal: AbortSignal.any([controller.signal, AbortSignal.timeout(20000)]) });
    let payload = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(payload.detail || '分析记录读取失败，请稍后重试。');
    if (seq !== requestSeq) return;
    if (payload.analysis_available && (!payload.analysis_cached || force)) {
      loadingText.value = '正在分析原始文档：检查版式、方向、倾斜及插图质量…';
      const analyzed = await fetch(url + (force ? '&force=true' : ''), {
        method: 'POST', signal: AbortSignal.any([controller.signal, AbortSignal.timeout(90000)]),
      });
      payload = await analyzed.json().catch(() => ({}));
      if (!analyzed.ok) throw new Error(payload.detail || '原件分析未完成，请重试。');
    }
    if (seq !== requestSeq) return; // a newer request owns the dialog
    if (String(payload.doc_id || payload.source_hash || '') !== identity) {
      stale.value = true; report.value = null; error.value = '响应与所选文档不一致，已丢弃旧数据。';
      return;
    }
    report.value = payload;
  } catch (reason) {
    if (seq !== requestSeq) return;
    report.value = null;
    error.value = reason.message || '分析记录读取失败，请检查后端连接。';
  } finally {
    if (seq === requestSeq) loading.value = false;
  }
}

async function applySelected() {
  if (!selectedCandidates.value.length || applying.value) return;
  const seq = requestSeq;
  applying.value = true; correctionError.value = '';
  try {
    const response = await fetch(`/api/documents/${encodeURIComponent(fileIdentity())}/corrections?${knowledgeParams()}`, {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ expected_source_hash: report.value.source_hash,
        expected_text_hash: report.value.text_hash, candidate_ids: selectedCandidates.value }),
    });
    const payload = await response.json();
    if (seq !== requestSeq) return;
    if (!response.ok) throw new Error(payload.detail || '校正副本生成失败');
    correctionResult.value = payload;
    selectedCandidates.value = [];
  } catch (reason) {
    if (seq === requestSeq) correctionError.value = reason.message;
  } finally { applying.value = false; }
}

const complexity = () => report.value?.reports?.complexity_report || null;
const quality = () => report.value?.reports?.quality_report || null;
const execution = () => report.value?.reports?.execution_record || [];

const summaryChips = () => {
  const chips = [];
  const state = report.value?.state;
  if (state) chips.push({ key: 'state', label: '状态', text: STATUS_LABELS[state] || state, level: statusLevel[state] || 'unknown' });
  const c = complexity();
  if (c) chips.push({ key: 'complexity', label: '复杂度', text: c.level ? { low: '低', medium: '中', high: '高', unknown: '未知' }[c.level] || c.level : '未评估', level: c.level === 'high' ? 'fail' : c.level === 'medium' ? 'suspect' : c.level === 'unknown' ? 'unknown' : 'pass', extra: c.score != null ? `分数 ${c.score}` : '' });
  const q = quality();
  if (q) chips.push({ key: 'quality', label: '质量', text: STATUS_LABELS[q.status] || q.status, level: statusLevel[q.status] || 'unknown', extra: q.score != null ? `分数 ${q.score}` : '' });
  return chips;
};

const qualityMetrics = computed(() => report.value ? qualityItems({ ...report.value, ...report.value.reports }) : []);
const checkLabels = { orientation: '页面方向', skew: '倾斜', blur: '文字清晰度', contrast: '对比度', ocr: 'OCR探测可靠度', illustrations: '插图清晰度与分辨率' };
const qualityAlerts = computed(() => qualityRows.value.flatMap(row => row.cells
  .filter(cell => ['fail', 'suspect', 'unknown'].includes(cell.status))
  .map(cell => ({ ...cell, page: row.page }))));
const illustrations = computed(() => (quality()?.pages || []).flatMap(page =>
  (page.checks?.illustrations?.value?.regions || []).map(region => ({ ...region, page: page.page_no }))));
const qualityRows = computed(() => (quality()?.pages || []).map(page => ({
  page: page.page_no,
  cells: Object.entries(page.checks || {}).map(([key, item]) => ({ key, ...item, label: checkLabels[key] || key })),
})));

const complexityRows = computed(() => (complexity()?.pages || []).map(page => ({
  page: page.page_no,
  features: page.features || {},
  dimensions: page.dimensions || {},
  score: page.score,
  level: page.level,
  strategy: page.strategy || {},
  status: page.status,
  reason: page.reason,
})));
const featureLabels = {
  readable_character_count: '可读字符数', image_coverage: '图像覆盖率', bad_encoding_count: '坏编码数',
  math_mapping_missing: '公式映射缺失', math_character_count: '公式字符数', missing_math_character_count: '缺失公式字符数',
  column_count: '栏数', table_count: '表格数', ocr_required: '需OCR', blank_candidate: '疑似空白页', rotation_metadata: '旋转元数据',
};

const dimLabels = { A: '扫描/OCR', L: '版式/多栏', T: '表格', F: '公式' };
const stageLabels = { pre_parse: '解析前', post_parse: '解析后' };

const executionView = computed(() => (execution() || []).map(item => ({
  step: item.step, status: item.status, reason: item.reason,
  detail: item.reason || Object.entries(item).filter(([k]) => !['step', 'status', 'reason'].includes(k)).map(([k, v]) => `${k}: ${typeof v === 'object' ? JSON.stringify(v) : v}`).join('；'),
})));

const overview = computed(() => {
  const c = complexity(); const q = quality();
  return {
    filename: report.value?.name || '',
    state: report.value?.state,
    pageCount: report.value?.page_count,
    chunkCount: report.value?.chunk_count,
    format: report.value?.format,
    ruleVersion: c?.rule_version || q?.rule_version || '',
    sourceHash: report.value?.source_hash || '',
    markdownHash: report.value?.markdown_sha256 || '',
    complexity: c,
    quality: q,
    execution: executionView.value,
    unknownPages: [...(c?.unknown_pages || []), ...(q?.unknown_pages || [])].filter((v, i, a) => a.indexOf(v) === i),
    limitations: [...(c?.limitations || []), ...(q?.limitations || [])].filter((v, i, a) => a.indexOf(v) === i),
  };
});

const notApplicable = computed(() => report.value?.state === 'not_applicable' || quality()?.status === 'not_applicable');

// ---- 任务9：繁体字 / 错别字候选（只读检测，必须人工确认后才应用） --------
const textCheckSummary = computed(() => {
  const checks = report.value?.text_checks || null;
  if (!checks) return null;
  const suspects = checks.suspect_pages || [];
  const rows = [];
  for (const page of (checks.pages || [])) {
    for (const item of [...(page.traditional || []), ...(page.typos || [])]) {
      rows.push({ ...item, statusText: item.confirmation === 'pending' ? '待人工确认' : '待核对' });
    }
  }
  return {
    version: checks.method_version || '未知',
    traditionalCount: checks.traditional_count || 0,
    typoCount: checks.typo_count || 0,
    suspectPages: suspects,
    reason: checks.reason || '',
    rows,
  };
});
const kindLabels = { traditional: '繁体字', typo: '错别字候选' };
</script>

<template>
  <dialog ref="element" class="quality-dialog" aria-label="文档分析质量" @cancel.prevent="emit('close')" @close="emit('close')" @click="event => { if (event.target === element) emit('close'); }">
    <template v-if="open">
      <header><div><small>知识库文档分析</small><h2>文档分析质量</h2><p class="quality-filename">{{ report?.name || props.file?.name || '' }}</p></div><button autofocus type="button" aria-label="关闭文档分析质量" @click="emit('close')">关闭 ×</button></header>

      <div v-if="loading" class="quality-body" role="status"><p class="quality-note">{{ loadingText }}</p></div>
      <div v-else-if="error" class="quality-body" role="alert"><p class="quality-error">{{ error }}<button type="button" class="quality-retry" @click="load()">重试</button></p></div>

      <div v-else-if="report" class="quality-body">
        <p v-if="stale" class="quality-error">响应与所选文档不一致，已丢弃旧数据。</p>
        <nav class="quality-tabs" aria-label="分析详情分区">
          <button type="button" :class="{ active: activeTab === 'overview' }" @click="activeTab = 'overview'">基础信息</button>
          <button type="button" :class="{ active: activeTab === 'quality' }" @click="activeTab = 'quality'">质量检测</button>
          <button type="button" :class="{ active: activeTab === 'complexity' }" @click="activeTab = 'complexity'">复杂度</button>
          <button type="button" :class="{ active: activeTab === 'execution' }" @click="activeTab = 'execution'">覆盖与执行</button>
          <button type="button" :class="{ active: activeTab === 'alerts' }" @click="activeTab = 'alerts'">告警与校正</button>
          <button type="button" :class="{ active: activeTab === 'pages' }" @click="activeTab = 'pages'">逐页详情</button>
        </nav>

        <section v-if="activeTab === 'overview'" class="quality-section">
          <h3>基础信息</h3>
          <dl class="quality-dl">
            <div><dt>文件名</dt><dd>{{ report.name }}</dd></div>
            <div><dt>知识库</dt><dd>{{ knowledgeBaseId || '当前知识库' }}</dd></div>
            <div><dt>格式</dt><dd>{{ report.format || '未知' }}</dd></div>
            <div><dt>页数</dt><dd>{{ report.page_count ?? '未知' }}</dd></div>
            <div><dt>片段数</dt><dd>{{ report.chunk_count ?? '未知' }}</dd></div>
            <div><dt>分析状态</dt><dd>{{ STATUS_LABELS[report.state] || report.state }}</dd></div>
            <div><dt>原件哈希</dt><dd class="mono">{{ report.original_hash || report.source_hash || '未知' }}</dd></div>
            <div><dt>正文哈希</dt><dd class="mono">{{ report.text_hash || report.markdown_sha256 || '未知' }}</dd></div>
            <div><dt>规则版本</dt><dd>{{ overview.ruleVersion || '未知' }}</dd></div>
            <div v-if="report.original_filename"><dt>评估原件</dt><dd>{{ report.original_filename }}</dd></div>
          </dl>
          <p v-if="report.assessment_target === 'original_pdf'" class="quality-note">复杂度和图像质量来自对应原始 PDF 的实际测量；文字校正检查当前知识库正文。</p>
          <p v-if="report.analysis_reason" class="quality-error">{{ report.analysis_reason }}</p>
          <button v-if="report.analysis_available" type="button" class="quality-retry" @click="load(true)">重新分析原件</button>
          <ul class="quality-chips" aria-label="分析摘要">
            <li v-for="chip in summaryChips()" :key="chip.key" :class="chip.level"><strong>{{ chip.label }}</strong>{{ chip.text }}<span v-if="chip.extra">{{ chip.extra }}</span></li>
          </ul>
          <p class="quality-note">复杂度用于估计解析难度。质量检测显示原件的可读性问题；OCR探测分数为识别模型置信度，不代表正文正确率。</p>
          <ul v-if="quality()?.warnings?.length" class="quality-alerts"><li v-for="warning in quality().warnings" :key="warning">{{ warning }}</li></ul>
        </section>

        <section v-if="activeTab === 'quality'" class="quality-section">
          <h3>质量检测</h3>
          <p v-if="!quality()" class="quality-note">暂无质量检测记录（尚未分析）。</p>
          <template v-else>
            <ul class="quality-chips">
              <li v-for="item in qualityMetrics" :key="item.key" :class="item.level"><strong>{{ item.label }}</strong>{{ item.text }}<span v-if="item.coverage">（{{ item.coverage }}）</span><span v-if="item.pages.length">（第{{ item.pages.join('、') }}页）</span></li>
            </ul>
            <p class="quality-note">状态含义：通过 / 存疑 / 失败 / 未知 / 不适用。未知项会给出缺失原因（预算不足、无文本层、依赖缺失、缺少置信度、原件不可读等）。</p>
            <ul class="quality-alerts"><li v-for="item in qualityAlerts" :key="`${item.page}-${item.key}`">第 {{ item.page }} 页 · {{ item.label }}：{{ STATUS_LABELS[item.status] }}。{{ item.reason }}</li></ul>
            <h4 v-if="illustrations.length">插图检测</h4>
            <div v-for="(region, i) in illustrations" :key="i" class="page-card">
              <strong>第 {{ region.page }} 页 · 插图 {{ i + 1 }}</strong>
              <p>有效分辨率 {{ region.effective_resolution_dpi ?? '未知' }} DPI · {{ STATUS_LABELS[region.resolution?.status] || '未知' }}</p>
              <p>清晰度测量 {{ region.clarity?.value ?? '未知' }} · {{ STATUS_LABELS[region.clarity?.status] || '未知' }}</p>
            </div>
          </template>
        </section>

        <section v-if="activeTab === 'complexity'" class="quality-section">
          <h3>复杂度</h3>
          <template v-if="complexity()">
            <dl class="quality-dl">
              <div><dt>级别</dt><dd>{{ { low: '低', medium: '中', high: '高', unknown: '未知' }[complexity().level] || complexity().level }}</dd></div>
              <div><dt>分数</dt><dd>{{ complexity().score ?? '未知' }}</dd></div>
              <div><dt>均值 / P90</dt><dd>{{ complexity().mean_score ?? '—' }} / {{ complexity().p90_score ?? '—' }}</dd></div>
              <div><dt>评估页数</dt><dd>{{ complexity().assessed_page_count ?? '未知' }} / {{ complexity().page_count ?? '未知' }}</dd></div>
              <div><dt>需OCR页数</dt><dd>{{ complexity().ocr_page_count ?? '未知' }}</dd></div>
            </dl>
            <p class="quality-note">维度权重：扫描/OCR A=35，版式/多栏 L=25，表格 T=20，公式 F=20；分数 = 0.7×均值 + 0.3×P90（完整评估时）。规则版本 {{ complexity().rule_version || '未知' }}。</p>
            <p class="quality-note">低复杂度 &lt;30，中复杂度 30–59.99，高复杂度 ≥60。权重与阈值是项目启发式规则，尚未用真实业务标注集校准。</p>
            <h4 v-if="report.recommendations?.length">后续处理建议</h4>
            <div v-for="plan in report.recommendations || []" :key="plan.page_no" class="page-card"><strong>第 {{ plan.page_no }} 页</strong><ul><li v-for="step in plan.steps" :key="step">{{ step }}</li></ul></div>
          </template>
          <p v-else class="quality-note">暂无复杂度记录。</p>
        </section>

        <section v-if="activeTab === 'execution'" class="quality-section">
          <h3>覆盖与执行</h3>
          <ul v-if="overview.execution.length" class="execution-list">
            <li v-for="(item, i) in overview.execution" :key="i" :class="item.status"><strong>{{ item.step }}</strong><span>{{ STATUS_LABELS[item.status] || item.status }}</span><p>{{ item.detail }}</p></li>
          </ul>
          <p v-else class="quality-note">无执行记录。</p>
        </section>

        <section v-if="activeTab === 'alerts'" class="quality-section">
          <h3>告警与校正</h3>
          <ul class="quality-alerts"><li v-for="item in qualityAlerts" :key="`${item.page}-${item.key}`">第 {{ item.page }} 页 · {{ item.label }}：{{ STATUS_LABELS[item.status] }}。{{ item.reason }}</li></ul>
          <template v-if="textCheckSummary">
            <ul class="quality-chips">
              <li :class="textCheckSummary.suspectPages.length ? 'suspect' : 'ok'"><strong>繁体字</strong>{{ textCheckSummary.traditionalCount }} 处<template v-if="textCheckSummary.suspectPages.length"> · 第{{ textCheckSummary.suspectPages.join('、') }}页</template></li>
              <li :class="textCheckSummary.typoCount ? 'suspect' : 'ok'"><strong>错别字候选</strong>{{ textCheckSummary.typoCount }} 处</li>
              <li class="ok"><strong>规则版本</strong>{{ textCheckSummary.version }}</li>
            </ul>
            <p v-if="textCheckSummary.reason" class="quality-note">{{ textCheckSummary.reason }}</p>
            <div v-if="textCheckSummary.rows.length" class="correction-list">
              <div class="correction-card" v-for="(item, i) in textCheckSummary.rows" :key="i">
                <label v-if="item.candidate_id && item.suggested"><input v-model="selectedCandidates" type="checkbox" :value="item.candidate_id" :disabled="applying" /> 已核对，采纳此项</label>
                <div class="correction-head"><span :class="'kind-' + item.kind">{{ kindLabels[item.kind] || item.kind }}</span><span>第 {{ item.page_no }} 页 · 字符 {{ item.start }}-{{ item.end }}</span><span class="correction-status">{{ item.statusText }}</span></div>
                <p class="correction-context">…{{ item.context }}…</p>
                <p class="correction-diff"><del>{{ item.original }}</del> → <ins>{{ item.suggested || '待人工核对' }}</ins></p>
                <p class="correction-note" v-if="item.basis">{{ item.basis }}</p>
                <p class="correction-note correction-alert" v-if="item.require_review || item.ambiguous || item.special_verify">建议由人工核对后再采纳：{{ [item.ambiguous ? '一字多义' : '', item.require_review ? '专有名词/引文上下文' : '', item.special_verify ? '涉及数字/单位/公式或引文原句' : ''].filter(Boolean).join('、') }}。</p>
              </div>
            </div>
            <p v-else class="quality-note">未检出繁体字或常见错别字候选。</p>
            <p class="quality-note">繁简转换采用 OpenCC；繁体字属于字形规范建议。错字检测覆盖常见规则候选，专名、数字及引用请对照原文核对。勾选确认后生成校正副本，保留原始知识库正文与原件。</p>
            <button v-if="textCheckSummary.rows.length" type="button" class="quality-retry" :disabled="!selectedCandidates.length || applying" @click="applySelected">{{ applying ? '正在生成…' : `生成校正副本（${selectedCandidates.length} 项）` }}</button>
            <p v-if="correctionError" role="alert" class="quality-error">{{ correctionError }}</p>
            <div v-if="correctionResult" class="page-card"><p>已生成校正副本，共 {{ correctionResult.applied_count }} 项。</p><h4>校正后正文</h4><pre class="corrected-body">{{ correctionResult.corrected_text }}</pre><a :href="correctionResult.download_url">下载校正后的 Markdown</a><ul><li v-for="change in correctionResult.change_log" :key="change.id">第 {{ change.page_no ?? '正文' }} 页：{{ change.original_text }} → {{ change.replacement }}</li></ul></div>
          </template>
          <p v-else class="quality-note">暂无文字检测记录。</p>
        </section>

        <section v-if="activeTab === 'pages'" class="quality-section">
          <h3>逐页详情</h3>
          <div v-if="qualityRows.length || complexityRows.length" class="page-details">
            <div v-if="qualityRows.length">
              <h4>质量逐页</h4>
              <div class="page-card" v-for="row in qualityRows" :key="'q' + row.page">
                <strong>第 {{ row.page }} 页</strong>
                <ul>
                  <li v-for="cell in row.cells" :key="cell.key"><span class="page-cell-label">{{ cell.label }}</span><span :class="cell.status">{{ STATUS_LABELS[cell.status] || cell.status }}</span><small v-if="cell.value != null && typeof cell.value !== 'object'">测量值 {{ cell.value }}</small><small v-if="cell.method">方法 {{ cell.method }}</small><em v-if="cell.reason">{{ cell.reason }}</em></li>
                </ul>
              </div>
            </div>
            <div v-if="complexityRows.length">
              <h4>复杂度逐页</h4>
              <div class="page-card" v-for="row in complexityRows" :key="'c' + row.page">
                <strong>第 {{ row.page }} 页</strong>
                <span v-if="row.status !== 'completed'" class="quality-chip na">{{ STATUS_LABELS[row.status] || row.status }}<em v-if="row.reason">{{ row.reason }}</em></span>
                <dl class="quality-dl compact">
                  <div v-for="(value, dim) in row.dimensions" :key="dim"><dt>{{ dimLabels[dim] || dim }}</dt><dd>{{ value == null ? '未知' : value }}</dd></div>
                  <div><dt>分数</dt><dd>{{ row.score ?? '未知' }}</dd></div>
                  <div><dt>级别</dt><dd>{{ { low: '低', medium: '中', high: '高', unknown: '未知' }[row.level] || row.level }}</dd></div>
                  <div><dt>策略</dt><dd>{{ row.strategy?.method || '未知' }}</dd></div>
                </dl>
                <details v-if="Object.keys(row.features).length" class="feature-details"><summary>特征</summary><dl class="quality-dl compact"><div v-for="(value, key) in row.features" :key="key"><dt>{{ featureLabels[key] || key }}</dt><dd>{{ value }}</dd></div></dl></details>
              </div>
            </div>
          </div>
          <p v-else class="quality-note">无逐页记录。</p>
        </section>

        <section v-if="notApplicable" class="quality-section">
          <h3>不适用说明</h3>
          <p class="quality-note">{{ report.analysis_reason || quality()?.reason || '当前来源未提供可检测的PDF或图像原件，可在“告警与校正”查看文字检测。' }}</p>
        </section>

        <p v-if="overview.limitations.length" class="quality-limits"><strong>说明：</strong>{{ overview.limitations.join(' ') }}</p>
      </div>
    </template>
  </dialog>
</template>

<style scoped>
.quality-dialog { width: min(1000px, 94vw); max-width: 94vw; max-height: 92dvh; padding: 0; border: 1px solid #dce6df; border-radius: 16px; color: #24362c; background: #fff; box-shadow: 0 24px 80px #14261f44; }
.quality-dialog::backdrop { background: #14261f88; }
.quality-dialog header { display: flex; align-items: center; justify-content: space-between; gap: 20px; padding: 16px 22px; border-bottom: 1px solid #e5ece6; }
.quality-dialog h2 { margin: 0; font-size: 19px; }
.quality-dialog small { color: #738077; }
.quality-filename { margin: 4px 0 0; font-size: 12px; color: #5c6a5d; }
.quality-dialog header button { cursor: pointer; border: 1px solid #d5e1d8; background: #f3f7f3; border-radius: 8px; color: #315d44; padding: 7px 12px; font: inherit; }
.quality-body { padding: 18px 22px; max-height: calc(92dvh - 120px); overflow: auto; }
.quality-tabs { display: flex; flex-wrap: wrap; gap: 6px; margin-bottom: 14px; }
.quality-tabs button { font: inherit; font-size: 12px; padding: 5px 10px; border: 1px solid #d5e1d8; background: #f6faf6; border-radius: 7px; color: #456253; cursor: pointer; }
.quality-tabs button.active { background: #e3efe8; border-color: #477f5f; color: #255c3f; font-weight: 600; }
.quality-section h3 { margin: 0 0 10px; font-size: 14px; }
.quality-section h4 { margin: 12px 0 8px; font-size: 13px; }
.quality-dl { display: grid; grid-template-columns: repeat(auto-fill, minmax(220px, 1fr)); gap: 8px 16px; margin: 0 0 12px; }
.quality-dl.compact { grid-template-columns: repeat(auto-fill, minmax(160px, 1fr)); }
.quality-dl div { display: flex; justify-content: space-between; gap: 8px; border-bottom: 1px solid #eef2ee; padding-bottom: 6px; }
.quality-dl dt { color: #738077; font-size: 12px; }
.quality-dl dd { margin: 0; font-size: 12px; color: #2a3b30; overflow-wrap: anywhere; }
.quality-dl .mono { font-family: ui-monospace, Menlo, Consolas, monospace; font-size: 11px; }
.quality-chips { list-style: none; margin: 0 0 12px; padding: 0; display: flex; flex-wrap: wrap; gap: 8px; }
.quality-chips li { display: inline-flex; align-items: center; gap: 6px; font-size: 12px; padding: 5px 9px; border-radius: 7px; background: #f4f7f4; color: #456253; }
.quality-chips li strong { font-weight: 600; }
.quality-chips li.fail { background: #fbeae4; color: #a03a24; }
.quality-chips li.low { background: #fbeae4; color: #a03a24; }
.quality-chips li.medium { background: #fdf2d8; color: #8a5b13; }
.quality-chips li.high { background: #e6f2e7; color: #2e6b3d; }
.quality-alerts { padding-left: 20px; font-size: 12px; color: #8a5b13; line-height: 1.8; }
.quality-alerts li { margin: 5px 0; }
.quality-retry:disabled { cursor: default; opacity: .55; }
.correction-card label { display: block; margin-bottom: 8px; color: #315d44; }
.quality-chips li.suspect { background: #fdf2d8; color: #8a5b13; }
.quality-chips li.unknown { background: #f0f1ee; color: #6b726b; }
.quality-chips li.na { background: #eef1f4; color: #5c6a75; }
.quality-chips li.ok, .quality-chips li.pass { background: #e6f2e7; color: #2e6b3d; }
.quality-note { font-size: 12px; color: #738077; line-height: 1.8; }
.quality-error { font-size: 13px; color: #a03a24; background: #fbeae4; padding: 9px 12px; border-radius: 8px; }
.quality-retry { margin-left: 10px; font: inherit; font-size: 12px; cursor: pointer; border: 1px solid #e0b7aa; background: #fff; color: #a03a24; border-radius: 6px; padding: 3px 8px; }
.execution-list { list-style: none; margin: 0; padding: 0; }
.execution-list li { font-size: 12px; padding: 8px 10px; border-bottom: 1px solid #eef2ee; }
.execution-list li strong { margin-right: 8px; }
.execution-list li span { font-size: 11px; background: #eef1f4; border-radius: 5px; padding: 1px 6px; color: #5c6a75; }
.execution-list li.failed span { background: #fbeae4; color: #a03a24; }
.execution-list li.skipped span { background: #fdf2d8; color: #8a5b13; }
.execution-list li p { margin: 5px 0 0; color: #738077; font-size: 11px; line-height: 1.6; }
.page-card { font-size: 12px; border: 1px solid #e6ece7; border-radius: 9px; padding: 10px 12px; margin-bottom: 8px; }
.page-card > strong { font-size: 12px; color: #2a3b30; }
.page-card ul { list-style: none; margin: 8px 0 0; padding: 0; }
.page-card li { display: flex; flex-wrap: wrap; align-items: baseline; gap: 6px 10px; padding: 3px 0; }
.page-cell-label { color: #738077; min-width: 74px; }
.page-card li span:nth-child(2) { font-weight: 600; }
.page-card li span.fail { color: #a03a24; } .page-card li span.suspect { color: #8a5b13; } .page-card li span.unknown { color: #6b726b; } .page-card li span.not_applicable { color: #5c6a75; } .page-card li span.pass { color: #2e6b3d; }
.page-card li small { color: #86918a; } .page-card li em { color: #6b726b; font-style: normal; }
.feature-details { margin-top: 8px; }
.feature-details summary { cursor: pointer; font-size: 12px; color: #456253; }
.correction-list { display: flex; flex-direction: column; gap: 8px; }
.correction-card { font-size: 12px; border: 1px solid #e6ece7; border-radius: 9px; padding: 10px 12px; background: #fbfdfb; }
.correction-head { display: flex; flex-wrap: wrap; gap: 6px 12px; align-items: center; color: #5c6a5d; }
.correction-head span:first-child { font-weight: 600; }
.correction-head .kind-traditional { color: #8a5b13; }
.correction-head .kind-typo { color: #a03a24; }
.correction-status { background: #fdf2d8; color: #8a5b13; border-radius: 5px; padding: 1px 6px; font-size: 11px; }
.correction-context { margin: 6px 0 2px; color: #738077; }
.correction-diff { margin: 0; } .correction-diff del { color: #a03a24; } .correction-diff ins { color: #2e6b3d; text-decoration: none; font-weight: 600; }
.correction-note { margin: 4px 0 0; color: #738077; font-size: 11px; line-height: 1.6; }
.correction-alert { color: #8a5b13; }
.corrected-body { white-space:pre-wrap; overflow-wrap:anywhere; max-height:360px; overflow:auto; padding:12px; background:#f3f7f3; border-radius:8px; font:inherit; line-height:1.9; }
.quality-limits { font-size: 11px; color: #738077; background: #f6f9f6; border-radius: 8px; padding: 8px 10px; line-height: 1.7; margin-top: 14px; }
@media (max-width: 640px) { .quality-dialog header { flex-direction: column; align-items: flex-start; } .quality-body { padding: 12px; } .quality-dl { grid-template-columns: 1fr; } }
</style>
