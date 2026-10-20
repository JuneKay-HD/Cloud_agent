<script setup>
import { computed, onUnmounted, ref, watch } from 'vue';
import DisplayDialog from './DisplayDialog.vue';

const props = defineProps({ file: Object, sessionId: String });
const emit = defineEmits(['close']);
const preview = ref(null), loading = ref(false), error = ref('');
const pageIndex = ref(0), mode = ref('text'), imageErrors = ref({});
const currentPage = computed(() => preview.value?.pages?.[pageIndex.value]);
const isPdf = computed(() => preview.value?.type === 'pdf');
let controller;
watch(() => [props.file?.id, props.sessionId], async ([id, session]) => {
  controller?.abort();
  preview.value = null;
  error.value = '';
  pageIndex.value = 0;
  imageErrors.value = {};
  loading.value = Boolean(id && session);
  if (!id || !session) return;
  const request = new AbortController();
  controller = request;
  try {
    const response = await fetch(`/api/sessions/${encodeURIComponent(session)}/documents/${encodeURIComponent(id)}/preview`, { signal: request.signal });
    const payload = await response.json();
    if (!response.ok) throw new Error(typeof payload.detail === 'string' ? payload.detail : '文件暂时无法打开，请稍后重试');
    if (request.signal.aborted) return;
    preview.value = payload;
    mode.value = payload.type === 'pdf' && payload.pages.some(page => page.image_url) ? 'original' : 'text';
  } catch (reason) {
    if (!request.signal.aborted) error.value = reason.message;
  } finally {
    if (controller === request) loading.value = false;
  }
});
onUnmounted(() => controller?.abort());
</script>

<template>
  <DisplayDialog :open="Boolean(file)" :title="`查看文件 · ${file?.name || ''}`" @close="emit('close')">
    <template #default="{ zoom }">
      <p v-if="loading" role="status">正在打开文件…</p>
      <p v-else-if="error" class="preview-warning" role="alert">{{ error }}</p>
      <template v-else-if="preview">
        <div class="preview-actions">
          <a v-if="preview.file_url && isPdf" :href="preview.file_url" target="_blank" rel="noopener">新窗口打开PDF</a>
          <a v-if="preview.download_url" :href="preview.download_url">{{ preview.type === 'docx' ? '下载Word原文件' : '下载原文件' }}</a>
          <button v-if="isPdf && currentPage?.image_url" type="button" :aria-pressed="mode === 'original'" @click="mode = 'original'">原始页面</button>
          <button v-if="isPdf" type="button" :aria-pressed="mode === 'text'" @click="mode = 'text'">解析正文</button>
        </div>
        <p v-if="preview.original_note" class="preview-warning">{{ preview.original_note }}</p>
        <p v-if="preview.text_note" class="preview-warning">{{ preview.text_note }}</p>
        <p v-if="preview.type === 'docx'" class="preview-note">这里展示已解析的Word正文。查看原始排版、图片及完整表格，请下载原文件后用Word打开。</p>
        <p v-if="preview.parsing_status === 'partial'" class="preview-warning">部分内容未能可靠识别。原文件仍可查看，解析正文可能不完整。</p>
        <div v-if="preview.pages.length" class="preview-pages">
          <button type="button" :disabled="pageIndex === 0" @click="pageIndex--">上一{{ isPdf ? '页' : '段' }}</button>
          <label>{{ isPdf ? '页码' : '正文分段' }}
            <select v-model.number="pageIndex" :aria-label="isPdf ? '预览页码' : '预览正文分段'">
              <option v-for="(page, index) in preview.pages" :key="index" :value="index">第{{ isPdf ? page.page_no : index + 1 }}{{ isPdf ? '页' : '段' }}</option>
            </select>
          </label>
          <span>共 {{ preview.pages.length }} {{ isPdf ? '页' : '段' }}</span>
          <button type="button" :disabled="pageIndex >= preview.pages.length - 1" @click="pageIndex++">下一{{ isPdf ? '页' : '段' }}</button>
        </div>
        <p v-for="(warning, index) in currentPage?.parsing_warnings || []" :key="index" class="preview-warning">{{ warning }}</p>
        <div v-if="mode === 'original' && currentPage?.image_url" class="preview-page">
          <p v-if="imageErrors[pageIndex]" class="preview-warning" role="alert">原始页面加载失败，请用新窗口打开PDF或下载原文件。</p>
          <img v-else :key="currentPage.image_url" :src="currentPage.image_url" :alt="`${preview.name} 第${currentPage.page_no}页原始页面`" :style="{ width: zoom + '%', maxWidth: 'none' }" @error="imageErrors[pageIndex] = true" />
        </div>
        <pre v-else-if="currentPage" class="preview-text">{{ currentPage.text || '这一页没有可显示的解析文字，请对照原文件查看。' }}</pre>
        <p v-else class="preview-warning">此附件没有保留可显示的正文，请重新上传。</p>
      </template>
    </template>
  </DisplayDialog>
</template>

<style scoped>
.preview-actions,.preview-pages { display:flex; flex-wrap:wrap; gap:12px; align-items:center; margin-bottom:14px; font-size:14px; }
.preview-actions a { color:#315d44; text-decoration:underline; text-underline-offset:3px; }
button,select { border:1px solid #d5e1d8; border-radius:7px; padding:7px 10px; color:#315d44; background:#f3f7f3; font:inherit; cursor:pointer; }
button[aria-pressed="true"] { background:#dfece1; }
button:disabled { opacity:.45; cursor:default; }
label { display:flex; gap:8px; align-items:center; }
.preview-note { font-size:14px; color:#68776b; line-height:1.6; }
.preview-warning { font-size:14px; color:#a46128; line-height:1.6; }
.preview-page { overflow:auto; background:#edf1ed; padding:12px; }
.preview-page img { display:block; height:auto; background:#fff; }
.preview-text { margin:16px 0 0; white-space:pre-wrap; overflow-wrap:anywhere; font:inherit; line-height:1.9; color:#283c30; }
</style>
