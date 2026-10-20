"""Local, bounded quality measurements. Never rotate source evidence in place."""
import math
import time

import numpy as np


def check(status, value=None, *, method='', phase='pre_parse', reason='', **extra):
    return {'status':status, 'value':value, 'method':method, 'phase':phase, 'reason':reason, **extra}


def inspect_native_geometry(page, rules):
    angles = []
    for char in page.chars:
        matrix = char.get('matrix')
        if matrix and char.get('text','').strip():
            angles.append(math.degrees(math.atan2(matrix[1],matrix[0])) % 360)
    if len(angles) < 8:
        return {'orientation':check('unknown',method='glyph_matrix',reason='有效字形方向不足'),
                'skew':check('unknown',method='glyph_matrix',reason='有效文字基线不足')}
    quadrants = [int(round(a/90))%4 for a in angles]
    winner = max(range(4),key=quadrants.count)
    support = quadrants.count(winner)/len(angles)
    if support < .7:
        return {'orientation':check('unknown',method='glyph_matrix',reason='文字方向不一致',support=support),
                'skew':check('unknown',method='glyph_matrix',reason='文字方向不一致')}
    residuals = [((a-winner*90+180)%360)-180 for a,q in zip(angles,quadrants) if q==winner]
    skew = float(np.median(residuals))
    status = 'fail' if abs(skew)>=rules['skew_fail_degrees'] else 'suspect' if abs(skew)>=rules['skew_suspect_degrees'] else 'pass'
    return {'orientation':check('pass' if winner==0 else 'fail',(-winner*90)%360,
                                method='glyph_matrix',support=round(support,4),
                                reason='文字字形方向；数值为核对时建议的逆时针旋转角度，未修改原件'),
            'skew':check(status,round(skew,3),method='glyph_matrix',support=round(support,4),
                         reason='由文字基线测量，不使用表格边框')}


def inspect_raster(pixels, rules):
    import cv2
    gray = cv2.cvtColor(np.asarray(pixels),cv2.COLOR_RGB2GRAY)
    # A scanned paper background can produce connected components at the
    # border, and even a high Laplacian value, without measurable text.
    # Check the page interior before treating those components as glyphs.
    height, width = gray.shape
    interior = gray[int(height * .02):max(int(height * .98), 1),
                    int(width * .02):max(int(width * .98), 1)]
    if interior.size and float(interior.std()) < 10 and \
            float(np.mean(cv2.Canny(interior, 60, 120) > 0)) < .002:
        reason = '页面内部未检出足够的可测文字纹理；不能用纸张或透印评价正文清晰度'
        return {'blur': check('not_applicable', method='text_texture_gate', reason=reason),
                'contrast': check('not_applicable', method='text_texture_gate', reason=reason),
                'regions': []}
    if int(gray.max())-int(gray.min()) < 1:
        return {'blur':check('not_applicable',method='local_laplacian',reason='均匀页面，没有可检测文字纹理'),
                'contrast':check('not_applicable',method='local_percentile',reason='均匀页面，没有可检测文字纹理'),
                'regions':[]}
    _, mask = cv2.threshold(gray,0,255,cv2.THRESH_BINARY_INV+cv2.THRESH_OTSU)
    count, labels, stats, _ = cv2.connectedComponentsWithStats(mask)
    height,width = gray.shape
    glyphs = []
    for x,y,w,h,area in stats[1:]:
        if area >= 6 and 4 <= h <= height*.15 and .08 <= w/max(1,h) <= 15 and w < width*.8 and area < width*height*.03:
            glyphs.append((int(x),int(y),int(w),int(h)))
    if len(glyphs) < 12:
        return {'blur':check('unknown',method='local_laplacian',reason='缺少稳定的文字候选，不能将图表/背景视为清晰文字'),
                'contrast':check('unknown',method='local_percentile',reason='缺少稳定文字区域'), 'regions':[]}
    # Up to two actual-content regions, excluding uniform page margins.
    centers = sorted(glyphs,key=lambda g:g[1]+g[3]/2)
    groups = [centers[:len(centers)//2],centers[len(centers)//2:]]
    sharpness,contrast,regions = [],[],[]
    for group in groups:
        x0=max(0,min(g[0] for g in group)-3); y0=max(0,min(g[1] for g in group)-3)
        x1=min(width,max(g[0]+g[2] for g in group)+3); y1=min(height,max(g[1]+g[3] for g in group)+3)
        crop=gray[y0:y1,x0:x1]
        sharpness.append(float(cv2.Laplacian(crop,cv2.CV_64F).var()))
        contrast.append(float(np.percentile(crop,95)-np.percentile(crop,5)))
        regions.append([x0,y0,x1,y1])
    blur_value=float(np.median(sharpness)); contrast_value=float(np.median(contrast))
    def classify(value,prefix):
        return 'fail' if value < rules[prefix+'_fail'] else 'suspect' if value < rules[prefix+'_suspect'] else 'pass'
    return {'blur':check(classify(blur_value,'blur'),round(blur_value,3),method='local_laplacian',regions=regions,
                         reason='固定渲染尺度下文字候选区域的清晰度代理，不能证明OCR准确率'),
            'contrast':check(classify(contrast_value,'contrast'),round(contrast_value,3),method='local_percentile',regions=regions),
            'regions':regions}


def inspect_orientation(pixels, rules, *, regions=None, deadline=None):
    from app.rag.quality_ocr import scan_geometry
    return scan_geometry(pixels, rules, deadline=deadline)['orientation']

def render_pixels(path, number, rules):
    import pypdfium2 as pdfium
    from app.rag.pdf_runtime import pdfium_lock
    with pdfium_lock, pdfium.PdfDocument(str(path)) as doc:
        page=doc[number-1]
        try:
            width,height=page.get_size()
            scale=min(rules['render_scale'],rules['max_dimension']/max(width,height))
            bitmap=page.render(scale=scale)
            try:
                return np.array(bitmap.to_pil().convert('RGB')),scale
            finally:
                bitmap.close()
        finally:
            page.close()


def raster_quality_row(image, number, rules):
    """Local inspection of an actual image/frame/slide, with bounded resolution."""
    tick = time.perf_counter()
    image = image.convert('RGB').copy()
    image.thumbnail((rules['max_dimension'], rules['max_dimension']))
    try:
        checks = {k: v for k, v in inspect_raster(np.asarray(image), rules).items() if k != 'regions'}
    except Exception as exc:
        checks = {k: check('unknown', reason=f'图像检测未完成：{type(exc).__name__}') for k in ('blur', 'contrast')}
    checks['ocr'] = check('unknown', method='qwen_visual_transcription', reason='Qwen未提供实测置信分数')
    from app.rag.quality_ocr import scan_geometry
    checks.update(scan_geometry(np.asarray(image), rules, deadline=tick + rules.get('seconds', 30)))
    pixels = np.asarray(image)
    kind = classify_illustration(pixels, 1.0, rules)
    checks['illustrations'] = check(
        'unknown' if kind == 'unknown' else 'pass', {'region_count': 1, 'regions': [{
            'kind': kind, 'area_fraction': 1.0, 'display_size_pt': list(image.size),
            'pixel_size': list(image.size), 'effective_resolution_dpi': None,
            'resolution': check('unknown', method='illustration_resolution',
                                reason='独立图片无源图DPI元数据；仅记录显示分辨率'),
            'clarity': checks['blur'], 'clarity_method': 'local_laplacian',
        }]}, method='standalone_image', phase='pre_parse',
        reason='整页单图按区域分析；分辨率未知因缺源图元数据。')
    return {'page_no': number, 'checks': checks, 'image_size': list(image.size),
            'duration_ms': round((time.perf_counter()-tick)*1000, 2)}


def classify_illustration(image_np, area_fraction, rules):
    """Classify a crop as full-page scan / photo / line-chart / illustration.

    Heuristic: whole-page raster -> scan; strongly saturated pixels -> photo;
    otherwise high edge density (monochrome axes/graphs) -> line chart."""
    import cv2
    gray = cv2.cvtColor(image_np, cv2.COLOR_RGB2GRAY)
    hsv = cv2.cvtColor(image_np, cv2.COLOR_RGB2HSV)
    saturation = hsv[:, :, 1]
    colorful = float(np.mean(saturation > 60))
    edges = cv2.Canny(gray, 60, 180)
    edge_density = float(np.mean(edges > 0))
    height, width = gray.shape
    if area_fraction >= 0.85:
        return 'full_page_scan'
    if colorful > 0.12:
        return 'photo'
    if edge_density > 0.04 and not (width < 24 or height < 24):
        return 'line_chart'
    return 'illustration'


def _crop_to(pixels, box, scale, width_px, height_px):
    x0, y0, x1, y1 = box
    px0 = max(0, int(round(x0 * scale))), max(0, int(round(y0 * scale)))
    px1 = min(width_px, int(round(x1 * scale))), min(height_px, int(round(y1 * scale)))
    if px1[0] <= px0[0] or px1[1] <= px0[1]:
        return None
    return np.asarray(pixels)[px0[1]:px1[1], px0[0]:px1[0]]


def inspect_illustrations(page, pixels, scale, rules):
    """Per-page illustration region analysis for PDF pages.

    Uses pdfplumber image records when present (region display box, effective
    resolution derived from source pixel size / display size). For pages without
    image records (scans, some renders) a whole-page proxy is used and
    resolution is reported as unknown with the reason given. Returns a list of
    illustration rows and a combined page-level check."""
    q = rules['quality']
    region_limit = q.get('illustration_max_regions', 4)
    areas = []
    page_area = max(1.0, float(getattr(page, 'width', 0) or 1) * float(getattr(page, 'height', 0) or 1))
    img_height, img_width = (np.asarray(pixels).shape[:2]) if pixels is not None else (0, 0)
    entries = getattr(page, 'images', None) or []
    usable = 0
    for image in entries[:region_limit]:
        try:
            width_pt = float(image.get('width') or (image.get('x1', 0) - image.get('x0', 0)))
            height_pt = float(image.get('height') or (image.get('bottom', 0) - image.get('top', 0)))
            srcsize = image.get('srcsize') or (0, 0)
            source_width = float(image.get('srcwidth') or srcsize[0])
            source_height = float(image.get('srcheight') or srcsize[1])
            box = (float(image['x0']), float(image['top']),
                   float(image['x1']), float(image['bottom']))
        except (TypeError, ValueError, KeyError):
            continue
        effective_dpi = min(source_width / (width_pt / 72), source_height / (height_pt / 72)) \
            if min(source_width, source_height, width_pt, height_pt) > 0 else None
        bbox_area = max(0.0, box[2] - box[0]) * max(0.0, box[3] - box[1])
        area_fraction = min(1.0, bbox_area / page_area) if page_area else 0.0
        crop = _crop_to(pixels, box, scale, img_width, img_height)
        kind = 'unknown'
        clarity = None
        if crop is not None and crop.size:
            kind = classify_illustration(crop, area_fraction, rules)
            gray = cv2_bgr(crop)
            clarity = float(cv2_laplacian(gray))
        resolution = _illustration_status(effective_dpi, q['illustration_resolution_fail'],
                                          q['illustration_resolution_suspect'], 'resolution')
        clarity_status = _illustration_status(clarity, q['illustration_blur_fail'],
                                              q['illustration_blur_suspect'], 'clarity')
        areas.append({
            'region': [round(v, 1) for v in box],
            'display_size_pt': [round(width_pt, 1), round(height_pt, 1)],
            'pixel_size': [int(source_width), int(source_height)] if source_width else None,
            'effective_resolution_dpi': round(effective_dpi, 1) if effective_dpi else None,
            'area_fraction': round(area_fraction, 4),
            'kind': kind,
            'clarity_method': 'local_laplacian' if clarity is not None else None,
            'resolution': resolution,
            'clarity': clarity_status,
        })
        usable += 1
    if not areas and not entries:
        return [], check('not_applicable', method='visible_illustrations',
                         reason='该页没有记录到插图区域', phase='pre_parse')
    statuses = [item for region in areas for item in (region['resolution']['status'], region['clarity']['status'])]
    if usable < len(entries):
        statuses.append('unknown')
    worst = _worst(statuses) if statuses else 'unknown'
    reason = '插图分辨率/清晰度检测；不自动修图，保留原件核对。' if usable else '未取得可读插图区域信息'
    if usable < len(entries):
        reason += f' 已检测 {usable}/{len(entries)} 个区域，其余因预算或区域信息不足未检测。'
    return areas, check(worst, {'region_count': usable, 'total_region_count': len(entries), 'regions': areas},
                        method='pdf_plugin_images', phase='pre_parse', reason=reason)


def cv2_bgr(crop):
    import cv2
    return cv2.cvtColor(crop, cv2.COLOR_RGB2GRAY)


def cv2_laplacian(gray):
    import cv2
    if gray.size == 0:
        return None
    return float(cv2.Laplacian(gray, cv2.CV_64F).var())


def _illustration_status(value, fail, suspect, name):
    if value is None:
        return check('unknown', method='illustration_' + name, reason='缺少来源像素尺寸，无法估算有效分辨率/清晰度')
    if value < fail:
        return check('fail', round(value, 2), method='illustration_' + name)
    if value < suspect:
        return check('suspect', round(value, 2), method='illustration_' + name)
    return check('pass', round(value, 2), method='illustration_' + name)


def _worst(statuses):
    for level in ('fail', 'suspect', 'unknown', 'pass'):
        if level in statuses:
            return level
    return 'unknown'


def quality_preflight(path, pdf, prepared, rules):
    from app.config import get_settings
    q=rules['quality']; rows=[]; probes=0; started=time.perf_counter()
    for number,(page,item) in enumerate(zip(pdf.pages,prepared),1):
        tick=time.perf_counter()
        checks={}
        try:
            if time.perf_counter()-started > q.get('seconds',30):
                raise TimeoutError('文档质量检测时间预算已用尽')
            pixels,scale=render_pixels(path,number,q)
            raster=inspect_raster(pixels,q)
            checks={k:v for k,v in raster.items() if k!='regions'}
            illustration_regions, illustration_check = inspect_illustrations(page, pixels, scale, rules)
            checks['illustrations'] = illustration_check
            if item['report']['features'].get('ocr_required') and \
                    raster['blur']['status'] == 'not_applicable':
                checks.update({k: check('not_applicable', method='text_texture_gate',
                    reason='原页未检出足够的可测正文；无法评价文字方向、倾斜或OCR可靠性')
                    for k in ('orientation', 'skew', 'ocr')})
            elif item['report']['features'].get('blank_candidate') and int(pixels.min())>=254:
                checks.update({k:check('not_applicable',reason='已渲染核对为空白页') for k in ('orientation','skew','ocr')})
            elif item['report']['features'].get('ocr_required'):
                if not get_settings().enable_ocr:
                    checks['orientation']=check('unknown',reason='OCR关闭，未执行方向探测')
                elif raster['regions'] and probes < q['orientation_max_pages']:
                    from app.rag.quality_ocr import scan_geometry
                    checks.update(scan_geometry(pixels,q,deadline=started+q.get('seconds',30)))
                    probes+=1
                else:
                    checks['orientation']=check('unknown',reason='方向探测页预算用尽或无稳定文字候选')
                checks.setdefault('skew',check('unknown',phase='post_parse',method='ocr_boxes',reason='等待实际OCR文字框'))
                checks.setdefault('ocr',check('unknown',phase='post_parse',reason='等待实际正文OCR'))
            else:
                checks.update(inspect_native_geometry(page,q))
                checks['ocr']=check('not_applicable',reason='原生文字页不需要OCR')
            rows.append({'page_no':number,'checks':checks,'render_scale':scale,
                         'duration_ms':round((time.perf_counter()-tick)*1000,2)})
        except Exception as exc:
            rows.append({'page_no':number,'checks':{k:check('unknown',reason=f'{type(exc).__name__}: {exc}')
                         for k in ('orientation','skew','blur','contrast','ocr','illustrations')},
                         'duration_ms':round((time.perf_counter()-tick)*1000,2)})
    return rows


def finish_page_quality(row, extracted, rules):
    checks=row['checks']
    if extracted.get('extraction_method')=='pdf_blank':
        for name in checks:
            checks[name]=check('not_applicable',reason='实际渲染证实为空白页',phase='post_parse')
    elif extracted.get('extraction_method')=='ocr':
        if extracted.get('ocr_validation') == 'unverified_visual_transcription':
            checks['ocr'] = check('fail' if extracted.get('ocr_uncertain_ranges') else 'unknown',
                {'confidence': None, 'excluded_range_count': len(extracted.get('ocr_uncertain_ranges', []))},
                method='qwen_visual_transcription', phase='post_parse',
                reason='模型转写未提供实测置信度；格式校验与引用匹配不能证明识别正确')
            if checks.get('skew', {}).get('method') != 'rapidocr_boxes':
                checks['skew'] = check('unknown', method='no_measured_text_boxes', phase='post_parse',
                                       reason='没有实测文字框，不能计算文字倾角')
            return row
        boxes=extracted.get('ocr_boxes',[])
        scores=[float(b['confidence']) for b in boxes if math.isfinite(float(b.get('confidence',float('nan'))))]
        if scores:
            checks['ocr']=check('fail' if extracted.get('ocr_uncertain_ranges') else 'pass',
                {'min':min(scores),'median':float(np.median(scores)),
                 'excluded_range_count':len(extracted.get('ocr_uncertain_ranges',[])),
                 'retry_count':extracted.get('ocr_retry_count',0)},method='actual_ocr_lines',phase='post_parse',
                 reason='OCR置信度与隔离范围，不是识别真值准确率')
        angles=[]
        for b in boxes:
            points=np.asarray(b['box']); dx=points[1][0]-points[0][0]; dy=points[1][1]-points[0][1]
            if abs(dx)>1:
                angle=((math.degrees(math.atan2(dy,dx))+45)%90)-45
                angles.append(angle)
        if len(angles)>=3 and float(np.std(angles))<3:
            value=float(np.median(angles))
            status='fail' if abs(value)>=rules['skew_fail_degrees'] else 'suspect' if abs(value)>=rules['skew_suspect_degrees'] else 'pass'
            checks['skew']=check(status,round(value,3),method='actual_ocr_boxes',phase='post_parse',support=len(angles))
    return row


def quality_warnings(row):
    labels={'orientation':'方向异常','skew':'倾斜','blur':'模糊','contrast':'低对比',
            'ocr':'OCR低可靠区域','illustrations':'插图分辨率或清晰度低'}
    return [f"第{row['page_no']}页质量提示：{labels[k]}（{v['status']}）；请核对原件，检测未自动修正文档。"
            for k,v in row['checks'].items() if v['status'] in {'suspect','fail'}]


def summarize_quality(rows, source_hash, rules):
    all_checks=[v for row in rows for v in row['checks'].values() if v['status']!='not_applicable']
    statuses=[v['status'] for v in all_checks]
    unknown=[r['page_no'] for r in rows if any(v['status']=='unknown' for v in r['checks'].values())]
    status='fail' if 'fail' in statuses else 'suspect' if 'suspect' in statuses else 'unknown' if 'unknown' in statuses else 'pass' if statuses else 'not_applicable'
    penalties={}
    for row in rows:
        for name,item in row['checks'].items():
            if name == 'illustrations':
                # 插图子区域单独计分，不归入总清晰度分；集中展示在插图分区。
                continue
            penalties[name]=max(penalties.get(name,0),40 if item['status']=='fail' else 20 if item['status']=='suspect' else 0)
    score=None if unknown or not statuses else max(0,100-sum(penalties.values()))
    return {'status':status,'score':score,'source_hash':source_hash,'rule_version':rules['version'],
            'grade_thresholds': {'blur': [rules['blur_fail'], rules['blur_suspect']],
                                 'contrast': [rules['contrast_fail'], rules['contrast_suspect']],
                                 'illustrations': [rules['illustration_resolution_fail'], rules['illustration_resolution_suspect']],
                                 'ocr': [rules.get('ocr_grade_low', .65), rules.get('ocr_grade_high', .85)]},
            'pages':rows,'unknown_pages':unknown,'issue_pages':[r['page_no'] for r in rows if quality_warnings(r)],
            'warnings':[w for r in rows for w in quality_warnings(r)],
            'limitations':['质量指标是启发式可读性检测，不能代替原件核对或人工忠实度。',
                           'OCR框倾角只覆盖可检测文字，不证明整页倾斜或图像完整。',
                           'Qwen返回Markdown转写，无实测行置信度、检测框或方向得分；这些质量维度标记未知。',
                           '未执行原页自动旋转或倾斜纠正，保留原件供核对。',
                           '方向判定按横排文字假设检查；竖排、混合方向或文字支持不足需人工核对。']}

