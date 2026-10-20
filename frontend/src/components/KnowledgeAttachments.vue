<script setup>
import AppIcon from './AppIcon.vue';
defineProps({ files: { type: Array, default: () => [] }, disabled: Boolean });
const emit = defineEmits(['remove']);
</script>

<template>
  <div v-if="files.length" class="attachment-list" aria-label="本次问题的知识库文档">
    <div v-for="file in files" :key="file.doc_id" class="attachment" :title="file.name">
      <div class="knowledge-attachment-content">
        <span class="file-icon"><AppIcon name="file" /></span>
        <span class="attachment-info"><strong>{{ file.name }}</strong><small>知识库文档 · 本次提问使用</small></span>
      </div>
      <button type="button" class="icon-button" :disabled="disabled" :aria-label="`取消附加 ${file.name}`" @click="emit('remove', file.doc_id)"><AppIcon name="close" /></button>
    </div>
  </div>
</template>

<style scoped>
.knowledge-attachment-content { display: flex; align-items: center; gap: 9px; flex: 1; min-width: 0; }
.knowledge-attachment-content .file-icon { flex-shrink: 0; }
</style>
