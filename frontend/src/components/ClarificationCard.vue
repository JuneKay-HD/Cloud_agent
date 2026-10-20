<script setup>
defineProps({ options: { type: Object, default: () => ({}) } });
defineEmits(["select"]);
</script>

<template>
  <div v-if="Object.keys(options).length" class="card">
    <strong>可选补充条件</strong>
    <div v-for="(values, field) in options" :key="field" class="group">
      <span>{{ field }}</span>
      <button v-for="value in values" :key="value" @click="$emit('select', value)">{{ value }}</button>
    </div>
  </div>
</template>

<style scoped>
.card { margin-top: 12px; padding: 12px; border: 1px dashed #9fc3e7; border-radius: 8px; }
.group { display: flex; flex-wrap: wrap; align-items: center; gap: 8px; margin-top: 8px; }
.group span { color: #66758a; font-size: 13px; }
button { border: 0; border-radius: 999px; padding: 6px 11px; background: #eaf3fc; color: #0a579e; cursor: pointer; }
</style>
