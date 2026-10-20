"""Exact source geometry; uncertain matches never draw rectangles."""
import json
import math
import re
from hashlib import sha256
from functools import lru_cache
from pathlib import Path
from uuid import uuid4

LAYOUT_VERSION = 'characters-v4'
LIGATURES = str.maketrans({'ﬀ':'ff','ﬁ':'fi','ﬂ':'fl','ﬃ':'ffi','ﬄ':'ffl'})
# 版面文字层的常见排版变体：弯引号/直引号、各类横线、软连字符。归一只影响
# 匹配与偏移换算，不改动原文；每个变体字符仍保留到原串的偏移映射。
_TYPOGRAPHIC = str.maketrans({
    '‘': "'", '’': "'", '“': '"', '”': '"',
    '‐': '-', '‑': '-', '‒': '-', '–': '-', '—': '-', '―': '-',
    '­': '',  # 软连字符不参与匹配
})
_NORMALIZE = {**LIGATURES, **_TYPOGRAPHIC}
NOTES = {
 'located':'原页区域已按真实字符或OCR行坐标定位。',
 'missing_original':'未保留可读取的原件；可按引用文字和原文偏移核对。',
 'no_text_layer':'原页没有可用文字层，且没有可靠OCR坐标。',
 'missing_ocr_boxes':'识别文字未附带可靠OCR行框，无法标注原页区域。',
 'no_match':'引用文字未在原页文字层中精确匹配。',
 'ambiguous_match':'原页有多处相同文字，现有上下文无法可靠消歧。',
 'unsupported_transform':'原页裁剪或旋转坐标尚无法可靠转换。',
 'partial_coverage':'只有部分引用文字具备可靠坐标，未标注整条引用。',
 'invalid_offsets':'引用偏移与当前原文不一致，请重新读取来源。',
 'uncertain_ocr':'该引用包含未经可靠识别的文字，无法确认原页区域。',
}


def normalized(text):
    letters, offsets = [], []
    for index, char in enumerate(text):
        for letter in char.translate(_NORMALIZE):
            if not letter.isspace():
                letters.append(letter)
                offsets.append(index)
    return ''.join(letters), offsets


def hits(text, target):
    return [m.start() for m in re.finditer('(?=' + re.escape(target) + ')', text)] if target else []

@lru_cache(maxsize=64)
def measured_page(path, digest, page_no, cache_dir=None):
    # The digest is part of the key: changing an original cannot reuse old geometry.
    return _layout_uncached(path, page_no, cache_dir)


def _layout(path, page_no, cache_dir=None):
    digest = sha256(Path(path).read_bytes()).hexdigest()
    return measured_page(str(path), digest, page_no, str(cache_dir) if cache_dir else None)


def _layout_uncached(path, page_no, cache_dir=None):
    import pdfplumber
    import pypdfium2 as pdfium
    from app.rag.pdf_runtime import pdfium_lock
    digest = sha256(Path(path).read_bytes()).hexdigest()
    cache = Path(cache_dir) / f'{digest}-{page_no}-{LAYOUT_VERSION}.json' if cache_dir else None
    if cache and cache.is_file():
        try:
            return json.loads(cache.read_text(encoding='utf-8')), digest
        except (OSError, ValueError):
            pass
    with pdfplumber.open(path) as pdf:
        if not 1 <= page_no <= len(pdf.pages):
            return {'reason':'no_match'}, digest
        page = pdf.pages[page_no - 1]
        with pdfium_lock, pdfium.PdfDocument(str(path)) as rendered:
            rendered_page = rendered[page_no - 1]
            try:
                width, height = rendered_page.get_size()
            finally:
                rendered_page.close()
        layout = {'width':width, 'height':height, 'rotation':page.rotation,
                  'bbox':list(page.bbox), 'cropbox':page.cropbox, 'reason':None, 'chars':[]}
        bounds = page.bbox
        if page.cropbox:
            bounds = (max(bounds[0],page.cropbox[0]),max(bounds[1],page.cropbox[1]),
                      min(bounds[2],page.cropbox[2]),min(bounds[3],page.cropbox[3]))
        layout['visible_bbox'] = list(bounds)
        if (abs(width-(bounds[2]-bounds[0]))>.5 or abs(height-(bounds[3]-bounds[1]))>.5):
            layout['reason'] = 'unsupported_transform'
        else:
            layout['chars'] = [{'text':char['text'], 'x0':char['x0']-bounds[0],
                                'x1':char['x1']-bounds[0], 'top':char['top']-bounds[1],
                                'bottom':char['bottom']-bounds[1]} for char in page.chars]
        layout['streams'] = list(_streams(layout['chars']))
    if cache:
        cache.parent.mkdir(parents=True, exist_ok=True)
        temporary = cache.with_suffix('.'+uuid4().hex+'.tmp')
        try:
            temporary.write_text(json.dumps(layout), encoding='utf-8')
            temporary.replace(cache)
        finally:
            temporary.unlink(missing_ok=True)
    return layout, digest


def rows(chars):
    """Visual line clusters: same-top rows (±3pt), each sorted left to right."""
    grouped = []
    for index in sorted(range(len(chars)), key=lambda i: (chars[i]['top'], chars[i]['x0'])):
        row = next((r for r in grouped if abs(chars[r[0]]['top'] - chars[index]['top']) < 3), None)
        if row is None:
            grouped.append([index])
        else:
            row.append(index)
    return [sorted(row, key=lambda i: chars[i]['x0']) for row in grouped]


def _streams(chars):
    visual = [i for row in rows(chars) for i in row]
    for order in (list(range(len(chars))),visual):
        text, mapping = [], []
        for index in order:
            value, _ = normalized(chars[index]['text'])
            text.append(value)
            mapping.extend([index]*len(value))
        joined = ''.join(text)
        yield joined, mapping
        # Remove only an alphabetic word break backed by a measured next line.
        omitted = set()
        for pos in range(1, len(joined)-1):
            if joined[pos] != '-' or not (joined[pos-1].isalpha() and joined[pos+1].isalpha()):
                continue
            before, after = chars[mapping[pos]], chars[mapping[pos+1]]
            line_height = max(1, before['bottom']-before['top'])
            if (3 < after['top']-before['top'] <= 2.5*line_height
                    and after['x0'] < before['x0']
                    and not any(abs(c['top']-before['top'])<3 and c['x0']>before['x1']
                                and c['x0']-before['x1']<line_height for c in chars)):
                omitted.add(pos)
        if omitted:
            yield ''.join(c for i,c in enumerate(joined) if i not in omitted), [v for i,v in enumerate(mapping) if i not in omitted]


def _valid_box(box):
    return (all(math.isfinite(box[k]) for k in ('left','top','width','height'))
            and box['left']>=0 and box['top']>=0 and box['width']>0 and box['height']>0
            and box['left']+box['width']<=100 and box['top']+box['height']<=100)


def _rectangles(chars, indices, width, height):
    rows = []
    for index in sorted(set(indices),key=lambda i:(chars[i]['top'],chars[i]['x0'])):
        char = chars[index]
        row = next((r for r in rows if abs(r['top']-char['top'])<3
                    and -1<=char['x0']-r['x1']<=max(6,char['bottom']-char['top'])),None)
        if row is None:
            rows.append({k:float(char[k]) for k in ('x0','x1','top','bottom')})
        else:
            row['x1'] = max(row['x1'],char['x1'])
            row['bottom'] = max(row['bottom'],char['bottom'])
    boxes = [{'left':r['x0']/width*100,'top':r['top']/height*100,
              'width':(r['x1']-r['x0'])/width*100,'height':(r['bottom']-r['top'])/height*100,
              'method':'pdf_character_match'} for r in rows]
    return boxes if all(_valid_box(b) for b in boxes) else []


def locate_quote(path,page,quote,start_char=None,end_char=None,cache_dir=None):
    target, _ = normalized(quote)
    text = page.get('text','')
    supplied = start_char is not None or end_char is not None
    valid_span = (isinstance(start_char,int) and isinstance(end_char,int)
                  and 0<=start_char<end_char<=len(text) and text[start_char:end_char]==quote)
    text_hits = hits(text,quote)
    if not supplied and len(text_hits)==1:
        start_char,end_char = text_hits[0],text_hits[0]+len(quote)
        valid_span = True
    # 排版变体（弯引号/横线等）在规范化字符域中仍应能精确定位；把规范命中
    # 映射回原文偏移，供状态判定与 OCR 行覆盖使用。
    norm_text, norm_map = normalized(text)
    norm_hits = hits(norm_text, target)
    if not valid_span and not text_hits and len(norm_hits)==1:
        norm_start = norm_hits[0]
        start_char = norm_map[norm_start]
        end_char = norm_map[norm_start + len(target) - 1] + 1
        valid_span = True
    result = {'highlight_boxes':[],'position_status':'text_only','method':None,
              'matched_chars':0,'total_chars':len(target),'layout_version':LAYOUT_VERSION,
              'original_hash':None,'text_hash':sha256(text.encode()).hexdigest(),
              'text_status':'located' if valid_span else ('ambiguous' if (text_hits or norm_hits) else 'not_found'),
              'start_char':start_char if valid_span else None,'end_char':end_char if valid_span else None}
    def finish(reason,boxes=None,method=None,matched=0):
        result.update(reason_code=reason,position_note=NOTES[reason],method=method,
                      matched_chars=matched,highlight_boxes=boxes or [],
                      position_status='located' if boxes else 'text_only')
        return result
    if supplied and not valid_span:
        return finish('invalid_offsets')
    reason = 'missing_original'
    if path and Path(path).suffix.lower()=='.pdf':
        layout,digest = _layout(path,page.get('page_no',1),cache_dir)
        result['original_hash'] = digest
        reason = layout.get('reason') or 'no_text_layer'
        chars = layout.get('chars',[])
        if chars and not layout.get('reason'):
            candidates = {}
            source_text,source_map = normalized(text)
            for stream,mapping in layout['streams']:
                matches = hits(stream,target)
                if len(matches)>1 and valid_span:
                    # Offsets alone are safe only with full exact page alignment.
                    if stream==source_text:
                        selected = [i for i,offset in enumerate(source_map) if start_char<=offset<end_char]
                        matches = [selected[0]] if selected else []
                    else:
                        context_start = max(0,start_char-80)
                        context,_ = normalized(text[context_start:min(len(text),end_char+80)])
                        context_hits = hits(stream,context)
                        prefix,_ = normalized(text[context_start:start_char])
                        if len(context_hits)==1:
                            matches = [context_hits[0]+len(prefix)]
                for index in matches:
                    indices = mapping[index:index+len(target)]
                    candidates[tuple(sorted(set(indices)))] = indices
            if len(candidates)==1:
                boxes = _rectangles(chars,next(iter(candidates.values())),layout['width'],layout['height'])
                if boxes:
                    return finish('located',boxes,'pdf_character_match',len(target))
                reason = 'unsupported_transform'
            elif candidates:
                reason = 'ambiguous_match'
            else:
                reason = 'no_match'
    visual = (page.get('ocr_validation')=='unverified_visual_transcription'
              or page.get('extraction_method')=='vision_transcription'
              or 'qwen' in str(page.get('ocr_engine','')).lower())
    lines = page.get('ocr_boxes',[])
    if visual and not lines:
        # 视觉转写页本身无实测框：不得用不可靠坐标画框。
        return finish(reason if reason not in ('missing_original','no_text_layer') else 'missing_ocr_boxes')
    if not lines:
        return finish(reason)
    width,height = page.get('ocr_page_width'),page.get('ocr_page_height')
    if not width or not height or width<=0 or height<=0:
        return finish('missing_ocr_boxes')
    if not valid_span:
        return finish('ambiguous_match' if text_hits else 'no_match')
    boxes,covered = [],set()
    for line in lines:
        begin,end = line.get('start_char',0),line.get('end_char',0)
        points = line.get('box',[])
        if begin>=end_char or end<=start_char or not points:
            continue
        xs,ys = [p[0] for p in points],[p[1] for p in points]
        box = {'left':min(xs)/width*100,'top':min(ys)/height*100,
               'width':(max(xs)-min(xs))/width*100,'height':(max(ys)-min(ys))/height*100,'method':'ocr_line_match'}
        if not _valid_box(box):
            return finish('unsupported_transform')
        boxes.append(box)
        covered.update(range(max(begin,start_char),min(end,end_char)))
    matched = sum(len(normalized(text[i])[0]) for i in covered)
    if matched!=len(target):
        return finish('partial_coverage',method='ocr_line_match',matched=matched)
    if any(r.get('start_char',0)<end_char and r.get('end_char',0)>start_char for r in page.get('ocr_uncertain_ranges',[])):
        return finish('uncertain_ocr')
    return finish('located',boxes,'ocr_line_match',matched)


def native_rectangles(path,page_no,quote):
    return locate_quote(path, {'page_no': page_no}, quote)['highlight_boxes']


def ocr_rectangles(page,quote):
    return locate_quote(None,page,quote)['highlight_boxes']


def locate_text(page,quote,start_char=None,end_char=None):
    text=page.get('text','')
    supplied=start_char is not None or end_char is not None
    valid_span=(isinstance(start_char,int) and isinstance(end_char,int)
                and 0<=start_char<end_char<=len(text) and text[start_char:end_char]==quote)
    matches=hits(text,quote)
    if (supplied and not valid_span) or (not supplied and len(matches)!=1):
        return dict(source_text=text,highlight_boxes=[],position_status='unresolved',
                    position_note='解析原文中无法唯一匹配引用。')
    start=start_char if valid_span else matches[0]
    return dict(source_text=text,start_char=start,end_char=start+len(quote),highlight_boxes=[],
                region_id='text-'+sha256((text+str(start)).encode()).hexdigest()[:20],
                position_status='located_text',
                position_note='已在解析原文中高亮对应文字；此格式没有可靠原始页坐标。',
                view_kind='parsed_text')
