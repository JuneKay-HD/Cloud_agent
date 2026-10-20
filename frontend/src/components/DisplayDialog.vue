<script setup>
import { ref, watch, nextTick, onUnmounted } from 'vue';
const props = defineProps({ open: Boolean, title: { type: String, default: '放大查看' } });
const emit = defineEmits(['close']);
const element = ref(null), zoom = ref(100);
watch(() => props.open, async open => {
  await nextTick();
  if (open) { zoom.value = 100; if (!element.value?.open) element.value?.showModal(); }
  else if (element.value?.open) element.value.close();
});
onUnmounted(() => element.value?.close());
</script>
<template>
  <dialog ref="element" class="display-dialog" :aria-label="title" @cancel.prevent="emit('close')" @close="emit('close')" @click="event => { if (event.target === element) emit('close'); }">
    <template v-if="open">
      <header><h2>{{ title }}</h2><button autofocus type="button" aria-label="关闭放大视图" @click="emit('close')">关闭 ×</button></header>
      <div class="zoom-controls"><button type="button" aria-label="缩小" :disabled="zoom <= 80" @click="zoom -= 20">−</button><label>缩放 <input v-model.number="zoom" type="range" min="80" max="240" step="20" /></label><output>{{ zoom }}%</output><button type="button" aria-label="放大" :disabled="zoom >= 240" @click="zoom += 20">＋</button><button type="button" @click="zoom = 100">重置</button><small>按 Esc 关闭</small></div>
      <div class="display-body" :style="{ fontSize: zoom / 100 + 'rem' }"><slot :zoom="zoom" /></div>
    </template>
  </dialog>
</template>
<style scoped>
.display-dialog { width: min(1200px,94vw); max-width:94vw; max-height:92dvh; padding:0; border:1px solid #dce6df; border-radius:16px; color:#24362c; background:#fff; box-shadow:0 24px 80px #14261f44; }
.display-dialog::backdrop { background:#14261f88; }
header { display:flex; align-items:center; justify-content:space-between; gap:20px; padding:18px 24px; border-bottom:1px solid #e5ece6; }
h2 { margin:0; font-size:19px; } button { cursor:pointer; border:1px solid #d5e1d8; background:#f3f7f3; border-radius:8px; color:#315d44; padding:7px 12px; font:inherit; } button:disabled { opacity:.4; cursor:default; }
.zoom-controls { display:flex; flex-wrap:wrap; gap:12px; align-items:center; padding:10px 24px; background:#f8faf8; font-size:14px; } label { display:flex; align-items:center; gap:8px; } input { width:140px; accent-color:#477f5f; } small { color:#738077; }
.display-body { padding:24px; max-height:calc(92dvh - 140px); overflow:auto; }
</style>
