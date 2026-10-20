<script setup>
defineProps({ name: String });
const paths = {
  cloud: 'M7 18h11a4 4 0 0 0 .7-7.9A7 7 0 0 0 5.2 8a5 5 0 0 0 1.8 10Z',
  plus: 'M12 5v14M5 12h14',
  edit: 'm16 3 5 5-12 12H4v-5L16 3ZM14 5l5 5',
  search: 'M21 21l-5-5M18 10a8 8 0 1 1-16 0 8 8 0 0 1 16 0Z',
  panel: 'M9 3v18M5 3h14a2 2 0 0 1 2 2v14a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2Z',
  chat: 'M21 11a9 9 0 0 1-9 9H4l-2 2v-11a9 9 0 1 1 19 0Z',
  file: 'M14 2H5v20h14V7l-5-5ZM14 2v6h5M8 12h8M8 16h6',
  arrow: 'M12 19V5m-6 6 6-6 6 6',
  close: 'm6 6 12 12M6 18 18 6',
  chart: 'M4 20h17M7 16v-5M12 16V4M17 16V8',
  book: 'M12 5v15M12 5C8 2 5 3 2 4v15c4-1 7-1 10 1 3-2 6-2 10-1V4c-3-1-6-2-10 1Z',
  sparkle: 'm12 3 2.8 6.2L21 12l-6.2 2.8L12 21l-2.8-6.2L3 12l6.2-2.8L12 3Z',
  check: 'm5 12 4 4L19 6',
  more: 'M5 12h.01M12 12h.01M19 12h.01',
  pin: 'm16 3 5 5-4 2-3 6-2-2-6 6m0-12 6-3 2-4M6 8l10 10',
  trash: 'M3 6h18M9 6V3h6v3M5 6l1 15h12l1-15M10 10v7M14 10v7',
  database: 'M12 3c4.5 0 8 1.5 8 3s-3.5 3-8 3-8-1.5-8-3 3.5-3 8-3ZM4 6v6c0 1.7 3.6 3 8 3s8-1.3 8-3V6M4 12v6c0 1.7 3.6 3 8 3s8-1.3 8-3v-6',
  layers: 'm12 3 9 5-9 5-9-5 9-5ZM3 13 12 18l9-5M3 17l9 5 9-5',
};
</script>
<template><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.65" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path :d="paths[name] || paths.chat" /></svg></template>
