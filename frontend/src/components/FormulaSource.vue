<script setup>
import { ref, onMounted, onBeforeUnmount } from 'vue';
import SourceRegion from './SourceRegion.vue';
import SourceText from './SourceText.vue';
import SourcePage from './SourcePage.vue';
import DisplayDialog from './DisplayDialog.vue';
import { locateFormula } from '../formulaLocation.js';
const props=defineProps({ source:Object, expression:String });
const root=ref(null), page=ref(null), busy=ref(false), error=ref(''), expanded=ref(false);
let observer, alive=true;
async function load() {
  if(busy.value || page.value)return;
  busy.value=true; error.value='';
  try { const value=await locateFormula(props.source,props.expression); if(alive)page.value=value; }
  catch(e) { if(alive)error.value=e.message; }
  finally { if(alive)busy.value=false; }
}
onMounted(() => {
  if(!props.source?.metadata?.source_url)return;
  observer=new IntersectionObserver(entries => { if(entries.some(e=>e.isIntersecting)){observer.disconnect();load();} },{rootMargin:'100px'});
  observer.observe(root.value);
});
onBeforeUnmount(()=>{alive=false;observer?.disconnect();});
</script>
<template>
  <section ref="root" class="formula-source">
    <header><strong>本公式原文标注</strong><span v-if="source">{{ source.metadata?.filename || source.title }}<template v-if="page?.page_no || source.metadata?.page_no"> · 第{{ page?.page_no || source.metadata?.page_no }}页</template><template v-else> · 解析原文</template></span></header>
    <p v-if="!source?.metadata?.source_url">此公式缺少可访问的原文引用，尚不能标注原页。</p>
    <p v-else-if="busy">正在定位原页区域并核对公式，请稍候…</p>
    <p v-if="error" role="alert">{{ error }} <button @click="load">重试</button></p>
    <template v-if="page">
      <p>{{ page.position_note }}</p>
      <SourceText v-if="page.source_text" :source="page" />
      <button v-if="!['located','located_text'].includes(page.position_status)" @click="page=null;load()">重新定位原页</button>
      <SourceRegion :source="page" />
      <div class="actions"><button v-if="page.page_image_url || page.formula_image" @click="expanded=true">放大查看原页标注</button><a v-if="page.file_url" :href="page.file_url+(page.page_no ? '#page='+page.page_no : '')" target="_blank" rel="noopener">打开原始文件</a></div>
    </template>
    <DisplayDialog :open="expanded" title="本公式 · 原页定位" @close="expanded=false"><template #default="{zoom}"><p>{{ page?.position_note }}</p><SourcePage v-if="page" :source="page" :zoom="zoom" /></template></DisplayDialog>
  </section>
</template>
<style scoped>
.formula-source{margin:16px 0;padding:14px;background:#fafbf7;border:1px solid #dce6d9;border-radius:10px}header{display:flex;flex-wrap:wrap;gap:10px;font-size:13px}header span,p{color:#667466;font-size:13px;line-height:1.6} .measure{display:none}.regions{display:grid;gap:10px}.region{position:relative;overflow:hidden;border:2px solid #d9a528;background:white;min-width:0}.region img{position:absolute;max-width:none;height:auto;display:block}.actions{display:flex;flex-wrap:wrap;gap:14px;align-items:center;margin-top:12px}button{border:1px solid #cadbcf;background:#edf5ef;color:#346747;padding:7px 10px;border-radius:6px;cursor:pointer}a{color:#346747;font-size:13px}
</style>
