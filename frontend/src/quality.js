// Image proxies and actual OCR engine scores are separate measurements.
export function qualityItems(file) {
  const report = file.quality_report;
  const name = file.name || file.filename || '';
  const hasVisualReading = (file.execution_record || []).some(item => item.step === 'visual_transcription');
  const textOnly = /\.(txt|md)$/i.test(name) || /\.ohr\.json$/i.test(name)
    || (/\.docx$/i.test(name) && !hasVisualReading);
  const fields = [['ocr', 'OCR可靠性'], ['blur', '清晰度'], ['contrast', '对比度']];
  const thresholds = report?.grade_thresholds || (report?.rule_version === 'document-quality-v1'
    ? { blur:[15,50], contrast:[15,35], ocr:[.65,.85] } : {});
  const rank = { low: 0, unknown: 1, medium: 2, high: 3, not_applicable: 4 };
  return fields.map(([key, label]) => {
    const pages = (report?.pages || []).map(page => {
      const item = page.checks?.[key];
      let level = 'unknown';
      if (item?.status === 'not_applicable') level = 'not_applicable';
      else if (key === 'ocr') {
        const value = item?.value?.min;
        if (thresholds[key] && ['actual_ocr_lines', 'rapidocr_probe_lines'].includes(item?.method) && Number.isFinite(value) && value >= 0 && value <= 1) {
          const [low, high] = thresholds[key];
          level = value < low ? 'low' : value < high ? 'medium' : 'high';
        }
      } else if (thresholds[key] && item?.method === (key === 'blur' ? 'local_laplacian' : 'local_percentile')
          && Number.isFinite(item?.value) && item.value >= 0 && (key !== 'contrast' || item.value <= 255)) {
        const [low, high] = thresholds[key];
        level = item.value < low ? 'low' : item.value < high ? 'medium' : 'high';
      }
      if (key === 'ocr' && item?.method === 'qwen_visual_transcription'
          && item?.status === 'fail' && item.value?.excluded_range_count > 0) level = 'low';
      const unmeasuredOCR = key === 'ocr' && level === 'unknown' && item?.method === 'qwen_visual_transcription';
      return { page: page.page_no, level, unmeasuredOCR,
        reason: item?.reason || report?.reason || '没有保存该项实测结果' };
    });
    const measured = pages.filter(page => ['low', 'medium', 'high'].includes(page.level));
    const unknown = pages.filter(page => page.level === 'unknown').length;
    const level = measured.length
      ? measured.reduce((worst, page) => rank[page.level] < rank[worst] ? page.level : worst, 'high')
      : pages.length && !unknown || textOnly ? 'not_applicable' : 'unknown';
    const coverage = measured.length ? `${measured.length}/${pages.length}页已测${unknown ? `，${unknown}页未评估` : ''}`
      : unknown ? `${unknown}页缺少可靠测量` : pages.length
        ? key === 'ocr' ? '无需OCR或无可测正文' : '无可测正文' : '';
    return { key, label, level, text: { low: '低', medium: '中', high: '高', unknown: '未评估', not_applicable: '不适用' }[level],
      reason: [...new Set(pages.map(page => page.reason).filter(Boolean))].join('；') || report?.reason || '',
      coverage, pages: pages.filter(page => page.level === 'low').map(page => page.page) };
  }).sort((a, b) => rank[a.level] - rank[b.level]);
}
