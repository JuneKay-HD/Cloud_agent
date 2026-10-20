<script setup>
import { computed } from 'vue';
import { visibleLocationExcerpts } from '../evidenceVisibility';
const props = defineProps({ excerpts:{type:Array,default:()=>[]}, showFailures:{type:Boolean,default:true},
  activeExcerpt:{type:String,default:''}, label:{type:String,default:'本页引用'},
  colorFor:{type:Function,default:()=> '#1565c0'} });
defineEmits(['select']);
const visible = computed(() => visibleLocationExcerpts(props.excerpts, props.showFailures));
</script>
<template>
  <div v-if="visible.length" class="page-references" :aria-label="label">
    <button v-for="excerpt in visible" :key="excerpt.excerptId" type="button" :style="{'--citation-color':colorFor(excerpt)}" :aria-pressed="excerpt.excerptId===activeExcerpt" :title="excerpt.quote" @click="$emit('select',excerpt)">[{{ excerpt.subcitation }}]<span v-if="excerpt.status==='pending'"> · 定位中</span><span v-else-if="excerpt.status==='error'"> · 请求失败</span><span v-else-if="excerpt.status==='text_only'"> · 无可靠区域</span></button>
  </div>
</template>
<style scoped>
.page-references { display:flex; flex-wrap:wrap; gap:8px; margin:12px 0; }
button { font:inherit; padding:7px 10px; border-radius:6px; cursor:pointer; color:var(--citation-color); background:white; border:1px solid var(--citation-color); }
button[aria-pressed="true"] { font-weight:700; box-shadow:0 0 0 2px var(--citation-color); }
button:focus-visible { outline:3px solid #174f82; outline-offset:3px; }
</style>
