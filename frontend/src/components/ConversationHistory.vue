<script setup>
import { nextTick, onMounted, onUnmounted, ref } from 'vue';
import AppIcon from './AppIcon.vue';

const props = defineProps({ sessions: Array, selected: String, disabled: Boolean, generating: Boolean, generatingIds: Array, search: String });
function isGenerating(id) { return props.generatingIds?.includes(id) || (props.generating && id === props.selected); }
const emit = defineEmits(['open', 'updated', 'deleted', 'busy']);
const root = ref(null), dialog = ref(null), nameInput = ref(null);
const menuId = ref(''), target = ref(null), action = ref(''), title = ref('');
const saving = ref(false), error = ref(''), notice = ref('');

function closeMenu(event) {
  if (!event.target.closest('.history-row')) menuId.value = '';
}
onMounted(() => document.addEventListener('pointerdown', closeMenu));
onUnmounted(() => document.removeEventListener('pointerdown', closeMenu));

async function showDialog(item, mode) {
  target.value = item;
  action.value = mode;
  title.value = item.title;
  error.value = '';
  menuId.value = '';
  await nextTick();
  dialog.value.showModal();
  if (mode === 'rename') nameInput.value?.select();
}
function closeDialog() {
  if (!saving.value) dialog.value.close();
}
function dialogClosed() {
  const id = target.value?.session_id;
  action.value = '';
  error.value = '';
  nextTick(() => root.value?.querySelector(`[data-session-id="${CSS.escape(id || '')}"] .history-more`)?.focus());
}
async function request(id, method, body) {
  const response = await fetch(`/api/sessions/${encodeURIComponent(id)}`, {
    method,
    headers: { 'Content-Type': 'application/json' },
    body: body ? JSON.stringify(body) : undefined,
  });
  const payload = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(typeof payload.detail === 'string' ? payload.detail : '操作失败，请稍后重试');
  return payload;
}
function setSaving(value) { saving.value = value; emit('busy', value); }
async function togglePin(item) {
  if (props.disabled || saving.value || isGenerating(item.session_id)) return;
  menuId.value = '';
  error.value = '';
  notice.value = '';
  setSaving(true);
  try {
    const result = await request(item.session_id, 'PATCH', { pinned: !item.pinned });
    emit('updated', result);
    notice.value = result.pinned ? '对话已置顶' : '已取消置顶';
  } catch (reason) { error.value = reason.message; }
  finally { setSaving(false); }
}
async function submit() {
  if (props.disabled || saving.value || isGenerating(target.value?.session_id)) return;
  error.value = '';
  notice.value = '';
  if (action.value === 'rename' && !title.value.trim()) {
    error.value = '请输入对话名称';
    nameInput.value?.focus();
    return;
  }
  const item = target.value;
  setSaving(true);
  try {
    if (action.value === 'delete') {
      await request(item.session_id, 'DELETE');
      emit('deleted', item.session_id);
      notice.value = '对话已删除';
    } else {
      const result = await request(item.session_id, 'PATCH', { title: title.value.trim() });
      emit('updated', result);
      notice.value = '对话名称已保存';
    }
    dialog.value.close();
  } catch (reason) { error.value = reason.message; }
  finally { setSaving(false); }
}
function dayLabel(date) { return new Date(date).toLocaleDateString('zh-CN', { month: 'numeric', day: 'numeric' }); }
</script>

<template>
  <nav ref="root" class="history-list" aria-label="对话记录" @keydown.esc="menuId = ''">
    <div v-for="item in sessions" :key="item.session_id" class="history-row" :class="{ active: item.session_id === selected }" :data-session-id="item.session_id">
      <div class="history-row-header">
        <button class="history-item" :disabled="disabled" :aria-current="item.session_id === selected ? 'page' : undefined" :title="item.title" @click="menuId = ''; emit('open', item.session_id)">
          <AppIcon :name="item.pinned ? 'pin' : 'chat'" />
          <span>{{ item.title }}</span>
          <span v-if="item.pinned" class="sr-only">已置顶</span>
          <small v-if="isGenerating(item.session_id)" class="history-generating" role="status">思考中</small>
          <time v-else :datetime="item.updated_at">{{ dayLabel(item.updated_at) }}</time>
        </button>
        <button class="icon-button history-more" :disabled="disabled || isGenerating(item.session_id)" :aria-label="`管理对话：${item.title}`" :aria-expanded="menuId === item.session_id" :aria-controls="`history-actions-${item.session_id}`" title="管理对话" @click="menuId = menuId === item.session_id ? '' : item.session_id"><AppIcon name="more" /></button>
      </div>
      <div v-if="menuId === item.session_id" :id="`history-actions-${item.session_id}`" class="history-actions" role="group" :aria-label="`${item.title}的操作`">
        <button :disabled="disabled || isGenerating(item.session_id)" @click="showDialog(item, 'rename')"><AppIcon name="edit" />重命名</button>
        <button :disabled="disabled || isGenerating(item.session_id)" @click="togglePin(item)"><AppIcon name="pin" />{{ item.pinned ? '取消置顶' : '置顶' }}</button>
        <button class="history-delete" :disabled="disabled || isGenerating(item.session_id)" @click="showDialog(item, 'delete')"><AppIcon name="trash" />删除</button>
      </div>
    </div>
    <p v-if="!sessions.length" class="history-empty">{{ search ? '没有找到相关对话' : '你的对话会保存在这里' }}</p>
    <p v-if="error && !action" class="history-feedback history-error" role="alert">{{ error }}</p>
    <p class="history-feedback" role="status">{{ notice }}</p>

    <dialog ref="dialog" class="workspace-dialog history-dialog" aria-labelledby="history-dialog-title" @close="dialogClosed" @cancel="event => { if (saving) event.preventDefault(); }" @click="event => { if (event.target === dialog) closeDialog(); }">
      <header><h2 id="history-dialog-title">{{ action === 'delete' ? '删除对话' : '重命名对话' }}</h2><button class="icon-button" :disabled="saving" aria-label="关闭对话操作" @click="closeDialog"><AppIcon name="close" /></button></header>
      <form class="panel-content" @submit.prevent="submit">
        <template v-if="action === 'rename'">
          <label class="history-name-label" for="conversation-name">对话名称</label>
          <input id="conversation-name" ref="nameInput" v-model="title" class="history-name-input" maxlength="80" :disabled="saving" autocomplete="off" aria-describedby="conversation-name-hint" />
          <p id="conversation-name-hint" class="panel-note">最多 80 个字符</p>
        </template>
        <p v-else class="history-delete-note">确认删除“{{ target?.title }}”？该对话的消息和附件将被删除，无法恢复。</p>
        <p v-if="error" class="history-feedback history-error" role="alert">{{ error }}</p>
        <div class="history-dialog-actions"><button type="button" class="panel-button" :disabled="saving" @click="closeDialog">取消</button><button type="submit" class="panel-button history-confirm" :class="{ danger: action === 'delete' }" :disabled="saving || (action === 'rename' && !title.trim())">{{ saving ? '正在处理…' : action === 'delete' ? '确认删除' : '保存' }}</button></div>
      </form>
    </dialog>
  </nav>
</template>
