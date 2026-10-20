<script setup>
import { computed } from 'vue';
import { qualityItems } from '../quality';
const props = defineProps({ file: { type: Object, required: true } });
const items = computed(() => qualityItems(props.file));
</script>
<template>
  <div class="document-assessment" aria-label="质量等级">
    <span v-for="item in items" :key="item.key" :class="item.level" :title="item.reason">{{ item.label }}：{{ item.text }}<small v-if="item.pages.length">（第{{ item.pages.join('、') }}页）</small></span>
  </div>
</template>
<style scoped>
.document-assessment { display:flex; flex-wrap:wrap; gap:4px 0; width:100%; font-size:12px; line-height:1.7; color:#788178; }
span + span::before { content:'·'; margin:0 7px; color:#a2aaa1; }
.low { color:#a34832; } .medium { color:#956d21; } .high { color:#477352; } small { font-size:inherit; }
</style>
