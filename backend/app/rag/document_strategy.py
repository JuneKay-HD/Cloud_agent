"""Choose bounded extraction from observed features, never from total score."""


def page_strategy(features: dict, *, unknown: bool = False) -> dict:
    if unknown:
        return {'method': 'blocked', 'reason': '页面预检未完成，请提供可读原件', 'warnings': []}
    warnings = []
    if features['ocr_required']:
        method = 'ocr'
    elif features['table_count'] and features['column_count'] > 1:
        method = 'pdf_mixed'
        warnings.append('表格与多栏并存，按区域提取；不承诺恢复任意跨栏或嵌套结构。')
    elif features['table_count']:
        method = 'pdf_tables'
    elif features['column_count'] > 1:
        method = 'pdf_columns'
    else:
        method = 'pdf_text'
    if features['math_mapping_missing']:
        warnings.append('PDF数学字体的文字映射缺失，已尝试OCR；公式下标、积分及变量仍需核对原件。')
    return {'method': method, 'reason': '根据实际文字层、栏、表格及字形特征选择', 'warnings': warnings}
