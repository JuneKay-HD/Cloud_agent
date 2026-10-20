"""Local OCR probes for page geometry; never replace the evidence body."""
from functools import lru_cache
import math
import threading
import time

import cv2
import numpy as np

_LOCK = threading.Lock()


@lru_cache(maxsize=1)
def engine():
    from rapidocr_onnxruntime import RapidOCR
    return RapidOCR(intra_op_num_threads=2, inter_op_num_threads=1)


def scan_geometry(pixels, rules, *, deadline=None):
    from app.rag.document_quality import check
    image = np.asarray(pixels)
    scale = min(1.0, 1200 / max(image.shape[:2]))
    if scale < 1:
        image = cv2.resize(image, None, fx=scale, fy=scale, interpolation=cv2.INTER_AREA)
    candidates, readings = [], {}
    try:
        with _LOCK:
            runner = engine()
            for turns in range(4):
                if deadline and time.perf_counter() >= deadline:
                    break
                # Disable automatic line flipping: each rotation must be measured.
                rows, _ = runner(np.ascontiguousarray(np.rot90(image, turns)), use_cls=False)
                rows = rows or []
                usable = []
                for box, text, confidence in rows:
                    box = np.asarray(box, dtype=float)
                    if not text.strip() or not math.isfinite(float(confidence)):
                        continue
                    width = float(np.linalg.norm(box[1]-box[0]))
                    height = float(np.linalg.norm(box[3]-box[0]))
                    # The detector can normalize vertical crops; only horizontal
                    # reading lines support the page's proposed orientation.
                    if width > height * 1.5:
                        usable.append((box, text, float(confidence)))
                chars = sum(len(t) for _, t, _ in usable)
                score = sum(len(t)*c for _, t, c in usable)/max(chars, 1)
                score *= min(1, chars / max(20, rules['orientation_min_characters']))
                angle = turns * 90
                candidates.append({'rotation_ccw': angle, 'score': round(score, 4),
                                   'characters': chars, 'lines': len(usable)})
                readings[angle] = usable
    except Exception as exc:
        reason = f'本地方向检测不可用：{type(exc).__name__}: {exc}'
        return {name: check('unknown', method='rapidocr_rotation_probe', reason=reason)
                for name in ('orientation', 'skew', 'ocr')}
    candidates.sort(key=lambda item: item['score'], reverse=True)
    best = candidates[0] if candidates else None
    confident = (len(candidates) == 4 and best['characters'] >= rules['orientation_min_characters']
                 and best['score'] >= .65
                 and best['score']-candidates[1]['score'] >= rules['orientation_margin'])
    orientation = check('pass' if confident and best['rotation_ccw'] == 0 else 'fail' if confident else 'unknown',
        best['rotation_ccw'] if confident else None, method='rapidocr_rotation_probe',
        reason='四个方向的本地OCR实测比较；角度为建议逆时针旋转量。' if confident else
               '方向候选差距不足、文字不足或预算用尽，需人工核对。',
        probe_count=len(candidates), candidates=candidates)
    if not confident:
        return {'orientation': orientation,
                'skew': check('unknown', method='rapidocr_boxes', reason='页面方向尚未确认'),
                'ocr': check('unknown', method='rapidocr_probe_lines', reason='方向探测未通过可靠性门槛')}
    rows = readings[best['rotation_ccw']]
    angles = [math.degrees(math.atan2(box[1, 1]-box[0, 1], box[1, 0]-box[0, 0])) for box, _, _ in rows]
    skew = float(np.median(angles))
    stable = len(angles) >= 3 and float(np.std(angles)) < 3
    status = 'fail' if abs(skew) >= rules['skew_fail_degrees'] else 'suspect' if abs(skew) >= rules['skew_suspect_degrees'] else 'pass'
    scores = [confidence for _, _, confidence in rows]
    return {'orientation': orientation,
            'skew': check(status if stable else 'unknown', round(skew, 3) if stable else None,
                          method='rapidocr_boxes', support=len(angles),
                          reason='建议方向下文字基线的顺时针倾角；文字行不稳定时不给确定角度。'),
            'ocr': check('suspect' if min(scores) < rules.get('ocr_grade_low', .65) else 'pass',
                         {'min': min(scores), 'median': float(np.median(scores)), 'lines': len(scores)},
                         method='rapidocr_probe_lines', reason='本地探测文字行的模型置信分数，不是正确率，也不代表已存正文。')}
