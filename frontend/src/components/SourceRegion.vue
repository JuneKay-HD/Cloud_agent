<script setup>
import { ref,computed,watch } from 'vue';
const props=defineProps({source:Object});
const size=ref(null),failed=ref(false);
const image=computed(()=>props.source.page_image_url || props.source.formula_image);
const boxes=computed(()=>(props.source.highlight_boxes || []).filter(b=>[b.left,b.top,b.width,b.height].every(Number.isFinite) && b.width>0 && b.height>0 && b.left>=0 && b.top>=0 && b.left+b.width<=100.01 && b.top+b.height<=100.01));
watch(image,()=>{size.value=null;failed.value=false;});
function cropStyle(b){return {aspectRatio:`${b.width*size.value.width} / ${b.height*size.value.height}`};}
function imageStyle(b){return {width:(10000/b.width)+'%',left:(-100*b.left/b.width)+'%',top:(-100*b.top/b.height)+'%'};}
</script>
<template><section v-if="source.position_status==='located' && boxes.length && image" class="source-regions"><p>原页对应区域 · 已标注<span v-if="source.region_type"> · {{ {formula:'公式',text:'正文',table:'表格',figure:'图示'}[source.region_type] || '引用' }}</span></p><img class="measure" :src="image" alt="" @load="size={width:$event.target.naturalWidth,height:$event.target.naturalHeight}" @error="failed=true" /><p v-if="failed">原页图片加载失败，请打开原始文件核对。</p><div v-if="size && !failed" class="regions"><div v-for="(box,i) in boxes" :key="i" class="region" :style="cropStyle(box)"><img :src="image" :style="imageStyle(box)" alt="对应引用在原始页面中的标注区域" /></div></div></section></template>
<style scoped>p{font-size:12px;color:#75613a}.measure{display:none}.regions{display:grid;gap:8px}.region{position:relative;overflow:hidden;border:2px solid #d9a528;background:white;min-width:0}.region img{position:absolute;max-width:none;height:auto;display:block}</style>
