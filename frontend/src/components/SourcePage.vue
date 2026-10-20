<script setup>
import { computed, nextTick, onUnmounted, ref, watch } from 'vue';
import { validRegion } from '../citationRegions';
const props=defineProps({ source:Object, zoom:{type:Number,default:100}, annotations:{type:Array,default:()=>[]}, activeEvidence:{type:String,default:''}, activeExcerpt:{type:String,default:''}, showFailures:{type:Boolean,default:true} });
const emit=defineEmits(['error', 'citation', 'loaded']);
const surface=ref(null), imageReady=ref(false), imageUrl=ref(''), imageError=ref(''), loading=ref(false), pageNote=ref('');
const fallbackUsed=ref(false);
const visiblePageNote=computed(()=>props.showFailures===false && fallbackUsed.value ? '当前显示附件中保留的真实原页图像。' : pageNote.value);
let controller, generation=0, objectUrl='', candidates=[], candidateIndex=-1, failure='';
function releaseImage() { if (objectUrl) URL.revokeObjectURL(objectUrl); objectUrl=''; }
function errorMessage(status,detail) {
  const meanings={401:'访问原页需要重新登录',403:'没有访问该原页的权限',404:'未找到保存的原件或原页图像',409:'原件或图像记录已变化，需要重新上传或建立索引',422:'原页页码或请求参数无效',500:'原页渲染失败，请确认后端PDF渲染依赖可用',503:'原页渲染服务暂不可用'};
  return (meanings[status] || '原页请求失败')+'（HTTP '+status+'）'+(detail ? '：'+detail : '');
}
async function attempt(version) {
  while (++candidateIndex<candidates.length && generation===version) {
    try {
      const response=await fetch(candidates[candidateIndex],{signal:controller.signal,cache:'no-store'});
      if (generation!==version) return;
      if (!response.ok) {
        const payload=await response.json().catch(()=>({}));
        if (generation!==version) return;
        const detail=typeof payload.detail==='string' ? payload.detail : '';
        failure=errorMessage(response.status,detail);
        // A changed source or denied access must never be concealed by an old snapshot.
        if ([401,403,409].includes(response.status)) break;
        continue;
      }
      const blob=await response.blob();
      if (generation!==version) return;
      if (!blob.type.startsWith('image/')) { failure='原页接口返回的内容不是图像，请确认原页地址和后端接口。'; continue; }
      if (generation!==version) return;
      releaseImage(); objectUrl=URL.createObjectURL(blob); imageUrl.value=objectUrl;
      fallbackUsed.value=candidates[candidateIndex].startsWith('data:image/');
      pageNote.value=fallbackUsed.value ? (failure ? failure+'。' : '')+'当前显示附件中保留的真实原页图像，未重新渲染原件。' : '';
      return;
    } catch(error) {
      if (error.name==='AbortError' || generation!==version) return;
      failure='无法连接原页接口，请确认后端已启动或稍后重新加载。';
    }
  }
  if (generation===version) { loading.value=false; imageError.value=failure || '未保存可读取的原页图像，请重新上传原件。'; emit('error',imageError.value); }
}
function reload() {
  controller?.abort(); controller=new AbortController(); const version=++generation;
  releaseImage(); imageUrl.value=''; imageReady.value=false; imageError.value=''; pageNote.value=''; loading.value=true; failure='';
  fallbackUsed.value=false;
  candidates=[...new Set([props.source.page_image_url,props.source.source_image,props.source.formula_image].filter(url=>typeof url==='string' && url))];
  candidateIndex=-1; void attempt(version);
}
function decodeFailed(event) { if (event?.target?.src!==imageUrl.value) return; imageReady.value=false; imageUrl.value=''; releaseImage(); failure='原页图像无法解码，可能已损坏；请重新加载或重新上传原件。'; void attempt(generation); }
watch(() => JSON.stringify([props.source.page_image_url,props.source.source_image,props.source.formula_image,props.source.source_hash,props.source.original_hash]),reload,{immediate:true});
onUnmounted(() => { generation++; controller?.abort(); releaseImage(); });
let pendingEvidence='', pendingExcerpt='';
const palette=['#1565c0','#a23b72','#00796b','#9a6700','#6b4db1','#bb4624'];
const boxes=computed(() => {
  if (!props.annotations.length) {
    return (props.source.highlight_boxes || []).filter(box =>
      ['left','top','width','height'].every(key => Number.isFinite(box[key]))
      && box.left>=0 && box.top>=0 && box.width>0 && box.height>0
      && box.left+box.width<=100 && box.top+box.height<=100)
      .map((box,index)=>({...box,evidenceId:'source',excerptId:'source-'+index,citation:1,subcitation:'1'}));
  }
  return props.annotations.filter(box => validRegion(box) && Number.isInteger(box.citation) && box.citation>0);
});
function color(citation) { return palette[(citation-1)%palette.length]; }
function isActive(box) { return props.activeExcerpt ? box.excerptId===props.activeExcerpt : box.evidenceId===props.activeEvidence; }
async function locate(evidenceId,excerptId='') {
  pendingEvidence=evidenceId;
  pendingExcerpt=excerptId;
  if (!imageReady.value) return;
  await nextTick();
  const element=[...(surface.value?.querySelectorAll('[data-evidence-id]') || [])]
    .find(el => el.dataset.evidenceId===evidenceId && (!excerptId || el.dataset.excerptId===excerptId));
  if (element) element.scrollIntoView({behavior:'smooth',block:'center',inline:'nearest'});
  pendingEvidence=''; pendingExcerpt='';
}
function loaded(event) { if (event?.target?.src!==imageUrl.value) return; imageReady.value=true; loading.value=false; emit('loaded'); if (pendingEvidence || props.activeEvidence) void locate(pendingEvidence || props.activeEvidence,pendingExcerpt || props.activeExcerpt); }
watch(() => [props.activeEvidence,props.activeExcerpt,props.annotations], () => {
  if (props.activeEvidence) void locate(props.activeEvidence,props.activeExcerpt);
});
defineExpose({ locate, reload });
</script>
<template>
  <div class="source-viewer" :style="{width:zoom+'%'}">
    <p v-if="loading && !imageReady" class="page-status" role="status">正在加载原页…</p>
    <div v-if="imageError && showFailures" class="page-error" role="status"><p>{{ imageError }}</p><button type="button" @click="reload">重新加载原页</button></div>
    <p v-else-if="imageError" class="page-status">暂无可展示的原页。</p>
    <p v-if="visiblePageNote" class="page-status">{{ visiblePageNote }}</p>
    <div v-if="imageUrl" ref="surface" class="source-page">
    <img v-if="imageUrl" :key="imageUrl" :src="imageUrl" :alt="source.page_no ? '原始附件第'+source.page_no+'页' : '原始附件页面'" @load="loaded" @error="decodeFailed" />
    <button v-for="(box,index) in boxes" :key="box.evidenceId+'-'+index" type="button"
      class="page-highlight" :class="{ active:isActive(box) }" :data-evidence-id="box.evidenceId" :data-excerpt-id="box.excerptId"
      :style="{left:box.left+'%',top:box.top+'%',width:box.width+'%',height:box.height+'%','--mark-color':color(box.citation)}"
      :aria-label="'引用 ['+(box.subcitation || box.citation)+'] 的原页区域'" :aria-pressed="isActive(box)"
      @click="$emit('citation',{evidenceId:box.evidenceId,excerptId:box.excerptId,quote:box.quote})">
      <span v-if="!boxes.slice(0,index).some(other=>other.excerptId===box.excerptId && other.evidenceId===box.evidenceId)" class="box-label" :class="{ inside:box.top<4, edge:box.left>90 }">[{{ box.subcitation || box.citation }}]</span>
    </button>
    </div>
  </div>
</template>
<style scoped>
.source-page { position:relative; line-height:0; min-width:0; }
.page-status,.page-error { line-height:1.6; font-size:12px; color:#68766b; margin:0; padding:8px; } .page-error { color:#93662e; } .page-error p { margin:0 0 8px; } .page-error button { border:1px solid #d8c7a4; border-radius:6px; padding:5px 10px; background:white; color:#765b30; cursor:pointer; } img { display:block; width:100%; height:auto; }
.page-highlight { position:absolute; z-index:1; padding:0; min-width:0; min-height:0; border:1px solid var(--mark-color); border-radius:2px; background:color-mix(in srgb,var(--mark-color) 13%,transparent); cursor:pointer; box-sizing:border-box; color:var(--mark-color); }
.page-highlight.active { z-index:2; border-width:2px; box-shadow:0 0 0 1px white,0 0 0 3px var(--mark-color); background:color-mix(in srgb,var(--mark-color) 26%,transparent); }
.page-highlight:focus-visible { outline:3px solid #111; outline-offset:3px; z-index:3; }
.box-label { position:absolute; bottom:100%; left:0; padding:2px 5px; white-space:nowrap; line-height:1.25; font-size:12px; font-weight:700; background:var(--mark-color); color:white; border-radius:3px 3px 0 0; }
.box-label.inside { bottom:auto; top:0; }
.box-label.edge { left:auto; right:0; }
</style>
