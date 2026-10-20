import { reactive, ref, watch } from 'vue';
import { DEFAULT_SHOW_EVIDENCE_FAILURES } from './evidenceVisibility.js';
export const defaults = { name: '我的空间', fontSize: 14, enterSend: true, showDetails: false, sidebarWidth: 270, dockWidth: 340,
  showEvidenceFailures: DEFAULT_SHOW_EVIDENCE_FAILURES };
export function usePreferences() {
  const storageError = ref('');
  let saved = {};
  try { saved = JSON.parse(localStorage.getItem('xiaoyun-preferences-v1') || '{}') || {}; }
  catch { storageError.value = '无法读取本地设置，已使用默认值。'; }
  const settings = reactive({
    name: typeof saved.name === 'string' ? saved.name.slice(0, 24) : defaults.name,
    fontSize: [14,16,18].includes(saved.fontSize) ? saved.fontSize : 14,
    enterSend: typeof saved.enterSend === 'boolean' ? saved.enterSend : true,
    showDetails: typeof saved.showDetails === 'boolean' ? saved.showDetails : false,
    showEvidenceFailures: typeof saved.showEvidenceFailures === 'boolean' ? saved.showEvidenceFailures : defaults.showEvidenceFailures,
    sidebarWidth: Number.isFinite(saved.sidebarWidth) ? Math.max(230, Math.min(520, saved.sidebarWidth)) : 270,
    dockWidth: Number.isFinite(saved.dockWidth) ? Math.max(260, Math.min(900, saved.dockWidth)) : 340,
  });
  watch(settings, () => {
    try { localStorage.setItem('xiaoyun-preferences-v1', JSON.stringify(settings)); storageError.value = ''; }
    catch { storageError.value = '浏览器禁止本地保存，本次设置仅在当前页面有效。'; }
  }, { flush: 'sync' });
  return { settings, storageError, reset: () => Object.assign(settings, defaults) };
}
