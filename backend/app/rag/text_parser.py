"""Decode local text with explicit encoding provenance, never replacement glyphs."""
import codecs


def parse_text(path):
    content = path.read_bytes()
    if content.startswith((codecs.BOM_UTF32_LE, codecs.BOM_UTF32_BE)):
        encoding = 'utf-32'
    elif content.startswith((codecs.BOM_UTF16_LE, codecs.BOM_UTF16_BE)):
        encoding = 'utf-16'
    else:
        encoding = 'utf-8-sig'
    warnings = []
    try:
        text = content.decode(encoding)
    except UnicodeDecodeError as error:
        if encoding != 'utf-8-sig':
            raise ValueError('文本BOM与实际编码不一致，请另存为UTF-8后上传') from error
        try:
            text = content.decode('gb18030')
        except UnicodeDecodeError as fallback:
            raise ValueError('文本编码无法识别，支持UTF-8、带BOM的UTF-16/UTF-32和GB18030；请另存为UTF-8后上传') from fallback
        encoding = 'gb18030'
        warnings.append('原文件不是UTF-8，已按GB18030兼容解码，请对照原件核对字符。')
    if any(ord(c) < 32 and c not in '\t\n\r\f' for c in text):
        raise ValueError('文本含二进制控制字符或缺少Unicode编码标记，请另存为UTF-8后上传')
    return {'page_no': None, 'text': text.replace('\r\n', '\n').replace('\r', '\n'),
            'text_format': 'markdown' if path.suffix.lower() == '.md' else 'text',
            'text_encoding': encoding, 'extraction_method': 'text', 'parsing_warnings': warnings}
