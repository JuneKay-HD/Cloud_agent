<script setup>
import DocumentAssessment from './DocumentAssessment.vue';
defineProps({ issues: { type: Array, default: () => [] } });
const originalUrl = issue => /^\/api\/documents\/[^/]+\/file$/.test(issue.file_url || '') ? issue.file_url : null;
</script>
<template>
  <section v-if="issues.length" class="knowledge-diagnostics" aria-label="资料状态与核对提示">
    <h3>资料状态与核对提示</h3>
    <ul>
      <li v-for="(issue, index) in issues" :key="issue.doc_id || index" :class="typeof issue === 'object' ? issue.severity : 'warning'">
        <template v-if="typeof issue === 'object'">
          <strong>{{ issue.filename || issue.file || '文档' }}</strong>
          <p class="reading-state">{{ issue.usable === true ? '已入库 · 可检索' : issue.usable === false ? '解析未完成' : '需要核对' }}</p>
          <p>{{ issue.reason || issue.error || issue.message || '解析异常' }}</p>
          <p v-if="issue.page_nos?.length">核对范围：第 {{ issue.page_nos.join('、') }} 页</p>
          <p v-if="issue.excluded_pages?.length">第 {{ issue.excluded_pages.join('、') }} 页含不确定片段，这些片段已排除出回答依据。</p>
          <DocumentAssessment v-if="issue.quality_report" :file="issue" />
          <details v-if="issue.details?.length"><summary>查看检测说明</summary>
            <ul><li v-for="(detail, i) in issue.details" :key="i">{{ detail.page_nos?.length ? `第 ${detail.page_nos.join('、')} 页：` : '' }}{{ detail.reason }}</li></ul>
            <p>图像清晰度和对比度不代表文字或公式识别正确；是否支持答案仍由问答时的来源核验判断。</p>
          </details>
          <a v-if="originalUrl(issue)" :href="originalUrl(issue)" target="_blank" rel="noopener">打开原件核对</a>
        </template>
        <p v-else>{{ issue }}</p>
      </li>
    </ul>
  </section>
</template>
<style scoped>
.knowledge-diagnostics > ul { padding:0; list-style:none; }
.knowledge-diagnostics > ul > li { margin:10px 0; padding:12px; border:1px solid #d8e2dc; border-radius:8px; overflow-wrap:anywhere; }
.notice { background:#f3f7f4; color:#485a4c; }
.warning { background:#fff8e9; color:#805b1e; }
.error { background:#fff3f0; color:#a34832; }
p { margin:6px 0; font-size:13px; line-height:1.6; }
.reading-state { font-weight:600; }
details { margin:8px 0; font-size:13px; }
details ul { padding-left:20px; }
a { display:inline-block; margin-top:8px; color:#28624d; font-size:13px; }
</style>
