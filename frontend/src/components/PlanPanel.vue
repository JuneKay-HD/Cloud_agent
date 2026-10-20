<script setup>
defineProps({ plan: { type: Array, default: () => [] } });
const labels = { completed: "完成", partial_answer: "部分完成", insufficient_evidence: "缺少依据",
  conflicting_evidence: "证据冲突", knowledge_unavailable: "资料不可用", unsupported: "暂不支持",
  blocked: "依赖未满足", failed: "失败", pending: "待执行" };
</script>

<template>
  <div v-if="plan.length" class="plan">
    <h2>跨源任务计划</h2>
    <p>按依赖顺序执行；失败分支不会覆盖已经确认的结果。</p>
    <ol>
      <li v-for="item in plan" :key="item.id">
        <strong>{{ item.label }}</strong>
        <span :class="{ warning: item.status !== 'completed' }">{{ labels[item.status] || item.status }}</span>
        <small v-if="item.depends_on.length">依赖：{{ item.depends_on.map(id => plan.find(p => p.id === id)?.label || id).join("、") }}</small>
        <small v-if="item.inputs?.entity">目标实体：{{ item.inputs.entity }}</small>
        <p>{{ item.detail }}</p>
      </li>
    </ol>
  </div>
</template>

<style scoped>
h2 { margin-top: 0; font-size: 18px; color: #174f82; }
ol { padding-left: 22px; }
li { margin: 12px 0; }
span { margin-left: 10px; color: #18784e; font-size: 13px; }
.warning { color: #96530f; }
small { display: block; color: #65788c; margin-top: 4px; }
p { color: #46566a; font-size: 13px; line-height: 1.6; overflow-wrap: anywhere; }
</style>
