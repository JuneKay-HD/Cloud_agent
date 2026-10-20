<script setup>
import { computed, ref, onUnmounted } from "vue";
import DisplayDialog from "./DisplayDialog.vue";
import SourceRegion from "./SourceRegion.vue";
import SourceText from "./SourceText.vue";
import SourceExcerpt from "./SourceExcerpt.vue";
import SourcePage from "./SourcePage.vue";
import LocationReferences from "./LocationReferences.vue";
import { nextTick } from "vue";
import { groupEvidence, groupSourcePages, sourcePageKey } from '../evidenceGroups';
import { citationChoices, locateChoices, locationSpan, createRequestQueue } from '../citationRegions';
import { visibleLocationExcerpts, visibleLocationChoices, visibleLocationNote } from '../evidenceVisibility';
const expandedImage=ref(null);
const activeEvidence=ref('');
const activeExcerpt=ref('');
const requestQueue=createRequestQueue(4), lifecycle=new AbortController();
onUnmounted(() => lifecycle.abort());
function sourceRequest(url, options={}) {
  return requestQueue(() => {
    lifecycle.signal.throwIfAborted();
    return fetch(url,{...options,signal:AbortSignal.any([lifecycle.signal,AbortSignal.timeout(15000)])});
  });
}
const props = defineProps({
  evidence: { type: Array, default: () => [] },
  assessment: { type: Object, default: null },
  status: { type: String, default: "" },
  idPrefix: { type: String, default: "" },
  showFailures: { type: Boolean, default: true },
});
const groups = computed(() => groupEvidence(props.evidence));
const unresolvedAspects = computed(() => (props.assessment?.aspect_coverage || []).filter(item => !['supported','model_checked'].includes(item.status)));
const sources = ref({});
const selectedQuotes=ref({}), versions=ref({});
const errors = ref({});
const loading = ref({});
const imageErrors = ref({});
const locations = ref({});
const pageRefs = {};
const pagesByGroup = computed(() => new Map(groups.value.map(group => [group.id, groupSourcePages(group,sources.value,locations.value)])));
const expandedPage = computed(() => [...pagesByGroup.value.values()].flat().find(page => page.key === expandedImage.value));
function pagesFor(group) { return pagesByGroup.value.get(group.id) || []; }
function pageId(key) { return props.idPrefix+'source-page-'+encodeURIComponent(key); }
function itemPageKey(item) {
  const group=groups.value.find(group => group.items.some(entry => entry.evidence_id===item.evidence_id));
  return group ? sourcePageKey(group.id,item,sources.value[item.evidence_id]) : '';
}
const palette=['#1565c0','#a23b72','#00796b','#9a6700','#6b4db1','#bb4624'];
function citationColor(item) { return palette[((item.metadata?.citation || 1)-1)%palette.length]; }
async function locateRegions(item,source,version,retry=false) {
  const id=item.evidence_id;
  const excerpts=choices(item);
  const key=JSON.stringify([source.page_no,source.source_hash,source.original_hash,excerpts]);
  if (!retry && locations.value[id]?.key===key && ['located','text_only'].includes(locations.value[id].status)) return;
  locations.value[id]={key,status:'pending',boxes:[],excerpts:excerpts.map(choice=>({...choice,status:'pending'})),note:'正在读取真实原页坐标…'};
  const unavailable='暂无可靠原页区域；请按文件、引用文字和原文偏移核对。';
  if (!Number.isInteger(source.page_no) || source.page_no<1 || !excerpts.length ||
      !(source.source_hash || source.original_hash)) {
    locations.value[id]={key,status:'text_only',boxes:[],excerpts:excerpts.map(choice=>({...choice,status:'text_only',note:unavailable})),note:unavailable}; return;
  }
  const url=new URL(item.metadata.source_url,window.location.origin);
  if (!/\/(source|formula-page)$/.test(url.pathname)) {
    locations.value[id]={key,status:'text_only',boxes:[],excerpts:excerpts.map(choice=>({...choice,status:'text_only',note:unavailable})),note:unavailable}; return;
  }
  url.pathname=url.pathname.replace(/\/(source|formula-page)$/,'/location'); url.search='';
  // 定位请求必须携带数据源上下文与引用条件：knowledge_base_id / chunk_id 从
  // source_url 的查询参数恢复；页码、引用跨度与版本由本次请求体携带。
  let blocked=false;
  const position=await locateChoices(excerpts,async quote => {
    const response=await sourceRequest(url,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({page_no:source.page_no,quote,...locationSpan(source,quote),expected_source_hash:source.source_hash,expected_original_hash:source.original_hash,...locationParams(item.metadata.source_url)})});
    const result=await response.json();
    if (!response.ok) {
      if ([401,403,409].includes(response.status)) blocked=true;
      throw new Error(typeof result.detail==='string' ? result.detail : '原页坐标读取失败。');
    }
    if ((source.source_hash && result.source_hash!==source.source_hash) || (source.original_hash && result.original_hash!==source.original_hash)) {
      blocked=true;
      throw new Error('坐标与原页版本不一致，请重新读取来源。');
    }
    return result;
  });
  if (versions.value[id]!==version) return;
  if (blocked) {
    delete sources.value[id];
    locations.value[id]={key,status:'text_only',boxes:[],excerpts:excerpts.map(choice=>({...choice,status:'text_only',note:'原页权限或版本检查未通过，请重新加载来源。'})),note:'原页权限或版本检查未通过，请重新加载来源。'};
    errors.value[id]=locations.value[id].note;
    return;
  }
  locations.value[id]={key,...position};
}
async function scrollToSource(item) {
  await nextTick();
  const key=itemPageKey(item), pageElement=document.getElementById(pageId(key));
  if (pageElement) {
    pageElement.scrollIntoView({behavior:'smooth',block:'start'});
    if (locations.value[item.evidence_id]?.boxes.length) void pageRefs[key]?.locate(item.evidence_id,activeExcerpt.value);
  } else document.getElementById(props.idPrefix+'evidence-'+item.evidence_id)?.scrollIntoView({behavior:'smooth',block:'start'});
}
function selectAnnotation(mark) {
  const item=props.evidence.find(item=>item.evidence_id===mark.evidenceId);
  if (item) void locate(item,mark.excerptId || mark.quote);
}
function fragmentClicked(event,item) {
  if (!event.target.closest('button,a,input,select,label')) void locate(item);
}

const statuses = {
  completed: "已找到支持材料", partial_answer: "部分回答，仍有缺失",
  insufficient_evidence: "证据不足", conflicting_evidence: "证据存在冲突",
  knowledge_unavailable: "知识库暂不可用",
};
function choices(item) {
  return citationChoices(item,props.assessment);
}
function locationParams(sourceUrl) {
  // 从保存引用的 source_url 查询参数恢复定位所需的数据源上下文与引用条件。
  const params={};
  if (typeof sourceUrl!=='string'||!sourceUrl) return params;
  try {
    const url=new URL(sourceUrl,window.location.origin);
    for (const key of ['knowledge_base_id','chunk_id']) {
      const value=url.searchParams.get(key);
      if (value) params[key]=value;
    }
  } catch { /* 非法URL不携带任何条件 */ }
  return params;
}
function visibleChoices(item) { return visibleLocationChoices(choices(item), locations.value[item.evidence_id], props.showFailures); }
function visibleExcerpts(excerpts) { return visibleLocationExcerpts(excerpts, props.showFailures); }
function excerptStatus(item,choice) { return locations.value[item.evidence_id]?.excerpts?.find(excerpt=>excerpt.excerptId===choice.excerptId); }
function itemFor(mark) { return props.evidence.find(item=>item.evidence_id===mark.evidenceId); }
function selected(item) { return selectedQuotes.value[item.evidence_id] || choices(item)[0]?.quote || ''; }
function visual(item) { return item.metadata.extraction_method==='vision_transcription' || item.metadata.ocr_validation==='unverified_visual_transcription'; }
async function locate(item,target,activate=true,retry=false) {
  const id=item.evidence_id;
  if (!item.metadata?.source_url) return;
  if (activate) activeEvidence.value=id;
  const targetChoice=typeof target==='string' ? choices(item).find(choice=>choice.excerptId===target || choice.subcitation===target || choice.quote===target) : null;
  if (targetChoice) selectedQuotes.value[id]=targetChoice.quote;
  else if (!selectedQuotes.value[id]) selectedQuotes.value[id]=choices(item)[0]?.quote || '';
  if (activate) activeExcerpt.value=choices(item).find(choice=>choice.quote===selected(item))?.excerptId || '';
  const selection=activeExcerpt.value;
  const version=(versions.value[id] || 0)+1;versions.value[id]=version;
  loading.value[id]=true;errors.value[id]='';
  try {
    let source=sources.value[id];
    if (retry || !source || source.located_quote !== selected(item)) {
      const url=new URL(item.metadata.source_url,window.location.origin);
      if (selected(item)) url.searchParams.set('quote',selected(item));
      const response=await sourceRequest(url);
      source=await response.json();
      if (!response.ok) {
        if (versions.value[id]!==version) return;
        if ([401,403,409].includes(response.status)) {
          delete sources.value[id];
          const note=typeof source.detail==='string' ? source.detail : '原页权限或版本检查未通过。';
          locations.value[id]={status:'text_only',boxes:[],excerpts:choices(item).map(choice=>({...choice,status:'text_only',note})),note};
        }
        throw new Error(typeof source.detail==='string' ? source.detail : '原文读取失败');
      }
    }
    if (versions.value[id]!==version)return;
    if (item.metadata.source_hash && source.source_hash!==item.metadata.source_hash) {
      delete sources.value[id];
      const note='当前来源与保存引用的原件版本不一致，请重新提问并核对来源。';
      locations.value[id]={status:'text_only',boxes:[],excerpts:choices(item).map(choice=>({...choice,status:'text_only',note})),note};
      throw new Error(note);
    }
    sources.value[id]=source;
    await locateRegions(item,source,version,retry);
    if (versions.value[id]!==version) return;
    if (activate && activeEvidence.value===id && activeExcerpt.value===selection) {
      if (!targetChoice) {
        const first=locations.value[id]?.excerpts?.find(excerpt=>excerpt.status==='located');
        if (first && selected(item)!==first.quote) { await locate(item,first.quote); return; }
      }
      await scrollToSource(item);
    }
  } catch(error) { if (versions.value[id]===version)errors.value[id]=error.message; }
  finally { if (versions.value[id]===version)loading.value[id]=false; }
}
function opened(event,group) {
  if (event.target===event.currentTarget && event.currentTarget.open) {
    // A direct citation click loads its selected source only. Expanding a group
    // manually retains the existing background loading of the whole group.
    if (event.currentTarget.dataset?.citationTarget) { delete event.currentTarget.dataset.citationTarget; return; }
    for (const item of group.items) if (item.metadata?.source_url && !loading.value[item.evidence_id] && (!sources.value[item.evidence_id] || locations.value[item.evidence_id]?.status!=='located')) void locate(item,undefined,false);
  }
}


</script>

<template>
  <div>
    <h2>证据与引用</h2>
    <component :is="status === 'completed' ? 'details' : 'div'" v-if="assessment" class="assessment">
      <summary v-if="status === 'completed'">查看证据核验记录</summary>
      <strong>{{ assessment.answer_mode === 'public_semantic_rejected' && assessment.rejection_reason !== 'question_not_answered' ? '回答核验未通过' : statuses[status] || status }}</strong>
      <p v-if="assessment.candidate_sources_only" class="warning">以下仅为检索候选，供查看原文；尚未形成通过核验的事实结论。</p>
      <p v-for="(conflict,index) in assessment.unresolved_conflicts || []" :key="'unresolved-'+index" class="warning">待核对冲突：{{ conflict }}</p>
      <p v-if="assessment.retrieval_mode === 'document_browse'">本次按文档顺序浏览并提炼内容。已检查 {{ assessment.read_chunk_count || 0 }} 个片段，其中 {{ assessment.usable_chunk_count ?? assessment.read_chunk_count ?? 0 }} 个可用于总结。{{ assessment.coverage === 'reliable_parsed_chunks_only' ? '不可靠的识别区域未作为依据。' : '' }}{{ evidence.some(item => item.source_type === 'document') ? '摘要引用可定位到原文。' : '尚未形成通过核验的摘要引用。' }}</p>
      <p v-else-if="assessment.retrieval_mode === 'formula_document_scan'">本次按所选文档提取公式、符号与原始参数。未注明的含义、数值和单位保留为缺失；视觉转写请点击引用核对原页。</p>
      <p v-else>本次使用{{ assessment.retrieval_mode === "hybrid" ? "关键词与向量联合检索" : "本地关键词检索" }}。{{ assessment.candidate_sources_only ? '候选原文可查看，尚未形成通过回答核验的引用。' : evidence.some(item => item.source_type === "document") ? "回答中的摘录可定位到原文。" : assessment.answer_mode === 'public_semantic_rejected' ? '已召回候选片段，但尚未形成通过回答核验的引用。' : "本次尚无可引用的文档证据。" }}</p>
      <p v-if="assessment.verification_failure" class="warning">{{ assessment.verification_failure }}</p>
      <p v-if="assessment.normalized_query && assessment.normalized_query !== assessment.original_query"><small>检索用语：{{ assessment.normalized_query }}（原问题保留）</small></p>
      <div v-if="unresolvedAspects.length" class="coverage">
        <strong>尚需核对的要素</strong>
        <p v-for="(item, index) in unresolvedAspects" :key="index">{{ item.aspect }}：{{ item.status === 'model_checked' ? '摘要与引用已由模型核验' : item.status === 'supported' ? '找到对应摘录' : '尚缺证据' }}<span v-if="item.citations?.length"> · {{ item.citations.map(n => `[${n}]`).join(" ") }}</span></p>
      </div>
      <div v-if="assessment.document_coverage?.length" class="coverage">
        <strong>所选文档覆盖</strong>
        <p v-for="source in assessment.document_coverage" :key="source.doc_id">{{ source.filename || source.title }}：读取 {{ source.read_chunk_count }} 个片段，可用 {{ source.usable_chunk_count }} 个，引用 {{ source.cited_chunk_count }} 个；{{ { verified: '摘要有对应引用', batch_verified: '分段摘要已核验；全文合并未通过', partial: '部分正文被排除', unreadable: '没有适用的可靠正文', failed: '摘要处理失败，请按失败原因重试', pending: assessment.rejection_reason ? '摘要处理已结束，未形成通过核验的摘要' : '摘要核验尚未完成' }[source.status] || source.status }}<span v-if="source.excluded_ocr_chunk_count"> · {{ source.excluded_ocr_chunk_count }} 个识别不可靠片段</span><span v-if="source.filtered_chunk_count"> · {{ source.filtered_chunk_count }} 个片段不适用</span></p>
      </div>
      <p v-for="(warning, index) in assessment.warnings || []" :key="index" class="warning">{{ warning }}</p>
      <details v-if="assessment.retrieval_diagnostics">
        <summary>查看检索范围记录</summary>
        <p v-if="assessment.retrieval_diagnostics.retrieval_budget">匹配 {{ assessment.retrieval_diagnostics.retrieval_budget.matched }} 个候选，选取 {{ assessment.retrieval_diagnostics.retrieval_budget.selected }} 个；答案是否完整依据当前问题的证据核验。</p>
        <p v-if="assessment.retrieval_diagnostics.split_chunks?.length">{{ assessment.retrieval_diagnostics.split_chunks.length }} 个候选来自分块正文；分块本身不代表答案有缺失，具体情况见证据核验结果。</p>
      </details>
      <p v-for="part in assessment.missing_aspects || []" :key="part" class="warning">尚缺证据：{{ part }}</p>
      <details v-if="assessment.stage_metrics && Object.keys(assessment.stage_metrics).length"><summary>查看阶段耗时与调用次数</summary><p v-for="(metric,stage) in assessment.stage_metrics" :key="stage">{{ {index_read:'读取索引',rewrite:'检索改写',recall:'召回与融合',query_embedding:'问题向量化',rerank:'重排',evidence_preflight:'证据充分性检查',generation:'生成',repair:'修复',verification:'核验'}[stage] || stage }}：{{ metric.duration_ms }} 毫秒 · {{ metric.calls }} 次<span v-if="metric.http_requests"> · {{ metric.http_requests }} 次模型请求</span><span v-if="metric.total_tokens != null"> · {{ metric.total_tokens }} tokens</span><span v-if="metric.failed_calls"> · {{ metric.failed_calls }} 次失败</span></p></details>
      <p v-for="conflict in assessment.conflicts || []" :key="conflict.key" class="warning">{{ conflict.key }}：{{ conflict.values.join(" / ") }}</p>
      <details v-if="assessment.rejected?.length">
        <p v-if="assessment.rejected_summary?.details_truncated">共排除 {{ assessment.rejected_summary.total }} 个片段，展示前 {{ assessment.rejected_summary.shown }} 条记录；全部排除条件仍用于证据核验。</p>
        <summary>查看排除的来源</summary>
        <p v-for="(source, index) in assessment.rejected" :key="index">{{ source.title }}：{{ source.reason }}</p>
      </details>
      <p v-for="item in assessment.entities || []" :key="item.entity">{{ item.entity }}：{{ statuses[item.status] || item.status }}</p>
      <small>核验范围：原文摘录与适用范围检查，不是对所有事实真实性的自动认证。</small>
    </component>
    <p v-if="!evidence.length">暂无可引用证据。</p>
    <details v-for="group in groups" :key="group.id" class="evidence document-group" @toggle="event => opened(event,group)">
      <summary>{{ group.title }} · {{ group.items[0].source_type === 'database' ? '数据库' : '文档' }} · {{ group.items.length }} 个引用</summary>
      <section v-for="item in group.items" :key="item.evidence_id" :id="idPrefix + 'evidence-' + item.evidence_id" class="citation-fragment" :class="{ located: activeEvidence === item.evidence_id }" @click="event => fragmentClicked(event,item)" @locate-source="event => locate(item,event.detail?.quote || event.detail?.subcitation)">
      <button v-if="item.metadata?.citation" type="button" class="citation-link" @click="locate(item)">[{{ item.metadata.citation }}] 定位原页</button>
      <h3 class="section-title">{{ sources[item.evidence_id]?.section_heading?.title || sources[item.evidence_id]?.section_note || '未识别到章节标题' }}</h3>
      <p v-if="sources[item.evidence_id]?.section_path?.length > 1" class="section-path">{{ sources[item.evidence_id].section_path.map(h => h.title).join(' › ') }}</p>
      <div v-if="item.source_type==='document'" class="excerpt-choices" aria-label="引用摘录子编号">
        <button v-for="choice in visibleChoices(item)" :key="choice.excerptId" type="button" :aria-pressed="activeExcerpt===choice.excerptId" :title="choice.quote" @click="locate(item,choice.quote)">[{{ choice.subcitation }}] {{ choice.label }}<span v-if="excerptStatus(item,choice)?.status==='pending'"> · 定位中</span><span v-else-if="excerptStatus(item,choice)?.status==='error'"> · 请求失败</span><span v-else-if="excerptStatus(item,choice)?.status==='text_only'"> · 无可靠区域</span></button>
      </div>
      <SourceExcerpt :text="item.content" :quote="selected(item)" :visual="visual(item)" />
      <p v-for="excerpt in visibleExcerpts(locations[item.evidence_id]?.excerpts)" :key="excerpt.excerptId" class="source-hint"><small>[{{ excerpt.subcitation }}] 文本：{{ excerpt.text_status === 'located' ? '已定位' : '待核对' }} · 原页：{{ excerpt.status === 'located' ? '已定位' : excerpt.status === 'error' ? '请求失败' : '无可靠区域' }} · {{ excerpt.heading_status === 'confirmed' ? '标题已确认' : '该页未确认标题' }}</small></p>
      <p><small><span v-if="item.metadata?.page_no">第{{ item.metadata.page_no }}页 · </span>引用偏移 {{ sources[item.evidence_id]?.citation_start_char ?? sources[item.evidence_id]?.start_char ?? item.metadata?.start_char ?? '未提供' }}–{{ sources[item.evidence_id]?.citation_end_char ?? sources[item.evidence_id]?.end_char ?? item.metadata?.end_char ?? '未提供' }}<span v-if="sources[item.evidence_id]?.section_heading"> · 标题<span v-if="sources[item.evidence_id].section_heading.page_no">第{{ sources[item.evidence_id].section_heading.page_no }}页，</span>偏移 {{ sources[item.evidence_id].section_heading.start_char }}</span></small></p>
      <p v-if="item.metadata?.source_metadata?.version"><small>版本：{{ item.metadata.source_metadata.version }}</small></p>
      <p v-if="item.metadata?.source_metadata?.valid_from"><small>适用：{{ item.metadata.source_metadata.valid_from }} 至 {{ item.metadata.source_metadata.valid_to || "未注明结束日" }}（结束日不含）</small></p>
      <div v-if="item.metadata?.source_url" class="actions">
        <button :disabled="loading[item.evidence_id]" @click="locate(item,selected(item),true,true)">{{ loading[item.evidence_id] ? "读取中…" : "重新读取与定位" }}</button>
        <a v-if="item.metadata.file_url" :href="item.metadata.file_url" target="_blank" rel="noopener">查看原始文件</a>
      </div>
      <p v-if="showFailures && errors[item.evidence_id]" class="warning">{{ errors[item.evidence_id] }}</p>
      <div v-if="sources[item.evidence_id]" class="source">
        <strong>{{ sources[item.evidence_id].filename }}<span v-if="sources[item.evidence_id].page_no"> · 第{{ sources[item.evidence_id].page_no }}页</span></strong>
        <p v-if="!(sources[item.evidence_id].page_image_url || sources[item.evidence_id].source_image || sources[item.evidence_id].formula_image)" class="source-hint">{{ sources[item.evidence_id].original_unavailable ? '原始页面不可用，请重新上传原件；保留章节和引用片段供核对。' : '此处为提取文字视图，不代表原始文件排版。' }}</p>
        <a v-if="sources[item.evidence_id].file_url" class="original-link" :href="sources[item.evidence_id].file_url + (sources[item.evidence_id].page_no ? '#page=' + sources[item.evidence_id].page_no : '')" target="_blank" rel="noopener">查看原始文件</a>
        <SourceRegion :source="sources[item.evidence_id]" />
        <SourceText v-if="sources[item.evidence_id].source_text" :source="sources[item.evidence_id]" />
        <details class="extracted-text" v-if="!sources[item.evidence_id].source_text && !(sources[item.evidence_id].page_image_url || sources[item.evidence_id].source_image || sources[item.evidence_id].formula_image)">
          <summary>查看解析页全文（保留原始识别文字）</summary>
          <pre>{{ sources[item.evidence_id].before }}<mark>{{ sources[item.evidence_id].highlight }}</mark>{{ sources[item.evidence_id].after }}</pre>
        </details>
        <p v-if="visibleLocationNote(locations[item.evidence_id], showFailures)" :class="locations[item.evidence_id].status === 'text_only' ? 'warning' : 'source-hint'">{{ locations[item.evidence_id].note }}</p>
        <p v-for="(warning, index) in sources[item.evidence_id].parsing_warnings || []" :key="index" class="warning">{{ warning }}（识别文字请对照原件核对。）</p>
      </div>
      </section>
      <section v-for="page in pagesFor(group)" v-show="showFailures || !imageErrors[page.key]" :key="page.key" :id="pageId(page.key)" class="shared-page" :class="{ 'active-page':page.items.some(item=>item.evidence_id===activeEvidence) }">
        <p v-if="page.versionConflict" class="warning">同一文档同页存在原件版本冲突，已阻断区域标注；请重新读取来源后核对。</p>
        <header class="page-heading"><h3>{{ page.source.page_no ? '第'+page.source.page_no+'页' : '原始页面（页码未提供）' }}</h3><button type="button" @click="expandedImage=page.key">放大原页</button><a v-if="page.source.file_url" :href="page.source.file_url + (page.source.page_no ? '#page='+page.source.page_no : '')" target="_blank" rel="noopener">查看原件</a></header>
        <LocationReferences :excerpts="page.excerpts" :show-failures="showFailures" :active-excerpt="activeExcerpt" :color-for="excerpt => citationColor(itemFor(excerpt))" @select="selectAnnotation" />
        <div class="original-page"><SourcePage :ref="element => { pageRefs[page.key]=element; }" :source="page.source" :annotations="page.annotations" :active-evidence="activeEvidence" :active-excerpt="activeExcerpt" :show-failures="showFailures" @citation="selectAnnotation" @error="reason => { imageErrors[page.key]=reason; }" @loaded="imageErrors[page.key]=''" /></div>
        <p v-for="item in showFailures ? page.items.filter(item=>locations[item.evidence_id]?.status==='text_only') : []" :key="item.evidence_id" class="warning">[{{ item.metadata?.citation ?? '未编号' }}] {{ locations[item.evidence_id].note }}</p>
      </section>
    </details>
    <DisplayDialog :open="!!expandedPage" title="原始页面 · 放大查看" @close="expandedImage=null"><template #default="{ zoom }"><template v-if="expandedPage"><LocationReferences label="放大页引用" :excerpts="expandedPage.excerpts" :show-failures="showFailures" :active-excerpt="activeExcerpt" :color-for="excerpt => citationColor(itemFor(excerpt))" @select="selectAnnotation" /><SourcePage :source="expandedPage.source" :annotations="expandedPage.annotations" :active-evidence="activeEvidence" :active-excerpt="activeExcerpt" :zoom="zoom" :show-failures="showFailures" @citation="selectAnnotation" /></template></template></DisplayDialog>
  </div>
</template>

<style scoped>
h2 { margin-top: 0; font-size: 18px; color: #174f82; }
details { padding: 10px 0; border-bottom: 1px solid #e6ebf1; }
summary { cursor: pointer; font-weight: 700; }
p { white-space: pre-wrap; overflow-wrap: anywhere; line-height: 1.6; color: #44546a; }
small { color: #61738a; }
.assessment { padding: 12px; border-radius: 8px; background: #f0f6fb; font-size: 13px; }
.warning { color: #935113; }
.quote-selector { display:flex; gap:10px; align-items:center; font-size:13px; margin:12px 0; } select { max-width:100%; border:1px solid #d5dfd7; border-radius:6px; padding:6px; background:#fff; color:#42644e; }
.actions { display: flex; align-items: center; gap: 12px; flex-wrap: wrap; }
button { border: 0; border-radius: 6px; padding: 7px 10px; background: #e6f0fc; color: #13518b; cursor: pointer; }
a { color: #13518b; font-size: 13px; }
.source { margin-top: 6px; font-size: 13px; }
pre { white-space: pre-wrap; overflow-wrap: anywhere; max-height: 360px; overflow: auto; padding: 10px; background: #f7f8fa; line-height: 1.8; }
.original-link { margin-left: 12px; }
.source-hint { color: #61738a; }
.original-page { max-height: 680px; overflow: auto; border: 1px solid #dce3eb; background: #eef1f5; padding: 8px; }
.original-page img { display: block; width: 100%; height: auto; }
mark { background: #ffe49b; }
.evidence:target { background: #fff9e8; outline: 2px solid #eac664; }
.citation-fragment { padding:10px 4px; border-bottom:1px solid #e6ebf1; scroll-margin-top:24px; }
.located { outline:2px solid #eac664; background:#fff9e8; }
.section-title { margin:7px 0 4px; color:#435b40; font-size:15px; line-height:1.45; }
.section-path { font-size:12px; }
.shared-page { margin:12px 0; padding:10px; border:1px solid #dce3eb; border-radius:10px; scroll-margin-top:20px; }
.active-page { border-color:#b5c4d7; }
.page-heading { display:flex; flex-wrap:wrap; align-items:center; gap:12px; } .page-heading h3 { margin:0 auto 0 0; font-size:15px; }
.citation-link { font-weight:700; }
.excerpt-choices { display:flex; flex-wrap:wrap; gap:6px; margin:8px 0; }
.excerpt-choices button { font-size:12px; }
.excerpt-choices button[aria-pressed="true"] { background:#dce9ff; outline:2px solid #1565c0; }
button:focus-visible { outline:3px solid #174f82; outline-offset:3px; }
</style>
