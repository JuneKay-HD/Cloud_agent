<script setup>
import { ref,onMounted,nextTick } from 'vue';
const props=defineProps({source:Object});
const root=ref(null);
onMounted(async()=>{await nextTick();root.value?.querySelector('mark')?.scrollIntoView({block:'center'});});
</script>
<template><section ref="root" class="source-text"><p>解析原文位置 · 保留段落顺序（非原始分页排版）</p><pre v-if="source.position_status==='located_text'">{{ source.source_text?.slice(0,source.start_char) }}<mark v-if="source.position_status==='located_text'">{{ source.source_text?.slice(source.start_char,source.end_char) }}</mark>{{ source.source_text?.slice(source.position_status==='located_text'?source.end_char:0) }}</pre><pre v-else>{{ source.source_text }}</pre></section></template>
<style scoped>.source-text{border:1px solid #dde5dc;padding:12px;border-radius:8px;background:#fff}p{font-size:12px;color:#758171}pre{white-space:pre-wrap;overflow-wrap:anywhere;font:inherit;line-height:1.8;max-height:500px;overflow:auto}mark{background:#fff0a7;border-bottom:2px solid #d6a322}</style>
