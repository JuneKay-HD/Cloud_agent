"""Rule-based text checks for 任务9：繁体字识别与规范建议、错别字候选。

只读：这些检测只读页面文本并返回候选/建议，不修改原件、不调用模型。
建议是否采纳必须由人工确认；数字/单位/公式/专有名词/引文原句的改动会标记
special_verify 要求单独核对。方法版本固定写入每个候选。

定位使用 Python 字符偏移（text[start:end]），与原页视觉位置无关；引用时按
派生正文重建切片后再定位，不直接使用原页坐标。
"""
from __future__ import annotations

import re
from difflib import SequenceMatcher
from functools import lru_cache

TEXT_CHECK_VERSION = 'text-checks-v1'

# 常用繁体字 → 简体字。仅收录一对一的常见字形；一字多义的“发/后/干”等标记
# ambiguous=True，必须人工核对后再采纳。
TRADITIONAL_TO_SIMPLIFIED: dict[str, str] = {
    '東': '东', '國': '国', '學': '学', '對': '对', '說': '说', '應': '应',
    '這': '这', '個': '个', '與': '与', '會': '会', '後': '后', '來': '来',
    '為': '为', '發': '发', '見': '见', '們': '们', '時': '时', '當': '当',
    '將': '将', '問': '问', '機': '机', '業': '业', '經': '经', '統': '统',
    '計': '计', '級': '级', '結': '结', '給': '给', '義': '义', '號': '号',
    '資': '资', '買': '买', '賣': '卖', '進': '进', '過': '过', '關': '关',
    '馬': '马', '魚': '鱼', '鳥': '鸟', '語': '语', '讀': '读', '錢': '钱',
    '長': '长', '門': '门', '開': '开', '電': '电', '頭': '头', '驗': '验',
    '體': '体', '頁': '页', '風': '风', '飛': '飞', '飯': '饭', '飲': '饮',
    '網': '网', '區': '区', '實': '实', '條': '条', '樣': '样', '麼': '么',
    '萬': '万', '聯': '联', '雙': '双', '戲': '戏', '書': '书', '興': '兴',
    '產': '产', '農': '农', '鄉': '乡', '辦': '办', '尋': '寻', '職': '职',
    '聽': '听', '聲': '声', '認': '认', '談': '谈', '話': '话', '請': '请',
    '謝': '谢', '講': '讲', '該': '该', '記': '记', '論': '论', '設': '设',
    '證': '证', '識': '识', '許': '许', '讓': '让', '課': '课', '調': '调',
    '報': '报', '執': '执', '強': '强', '態': '态', '懷': '怀', '斷': '断',
    '隊': '队', '際': '际', '難': '难', '雲': '云', '響': '响', '預': '预',
    '題': '题', '類': '类', '龍': '龙', '點': '点', '黨': '党',
    # 商务/技术文本常见
    '測': '测', '試': '试', '帳': '账', '營': '营', '務': '务', '財': '财',
    '總': '总', '員': '员', '責': '责', '現': '现', '導': '导', '師': '师',
    '復': '复', '複': '复', '寫': '写', '議': '议', '準': '准', '規': '规',
    '則': '则', '術': '术', '權': '权', '構': '构', '專': '专', '傳': '传',
    '優': '优', '價': '价', '費': '费', '團': '团', '圖': '图', '獎': '奖',
    '組': '组', '舉': '举', '顧': '顾', '餘': '余', '臺': '台', '灣': '湾',
    '漢': '汉', '華': '华', '國': '国', '眾': '众', '係': '系', '離': '离',
    '種': '种', '積': '积', '稱': '称', '壓': '压', '員': '员', '廣': '广',
    '廠': '厂', '發': '发', '質': '质', '彙': '汇', '紅': '红', '紙': '纸',
    '約': '约', '級': '级', '線': '线', '絕': '绝', '聯': '联', '總': '总',
    '義': '义', '習': '习', '課': '课', '貫': '贯', '賞': '赏', '邊': '边',
    '達': '达', '進': '进', '遠': '远', '還': '还', '郵': '邮', '鄰': '邻',
    '釘': '钉', '鈔': '钞', '鋼': '钢', '鍵': '键', '鎮': '镇', '鏡': '镜',
    '長': '长', '開': '开', '閱': '阅', '陳': '陈', '陣': '阵', '陰': '阴',
    '陽': '阳', '隨': '随', '險': '险', '雜': '杂', '難': '难', '電': '电',
    '需': '需', '靜': '静', '靈': '灵', '頑': '顽', '順': '顺', '預': '预',
    '領': '领', '額': '额', '顯': '显', '驗': '验', '驚': '惊', '驛': '驿',
}
# 一字多义/多音字：建议必须人工核对（仅对映射表中出现的字生效）
_AMBIGUOUS = {'後', '發', '乾', '裡', '係', '臺'}

# 常见错别字候选（词级）。basis 给出修正依据；全部要求人工确认后才应用。
TYPO_DICTIONARY: dict[str, str] = {
    '帐号': '账号', '帐单': '账单', '帐目': '账目', '帐户': '账户', '结帐': '结账',
    '记帐': '记账', '对帐': '对账', '按装': '安装', '既使': '即使', '即然': '既然',
    '报据': '根据', '合谐': '和谐', '克苦': '刻苦', '认请': '认清', '涨幅': '涨幅',
    '再接再励': '再接再厉', '迫不急待': '迫不及待', '一愁莫展': '一筹莫展',
    '相形见拙': '相形见绌', '名符其实': '名副其实', '谈笑风声': '谈笑风生',
    '帐号': '账号', '帐号': '账号',
}
# 需要单独核对的上下文特征：数字/单位/公式符号/引文原句（编译后用于 context 扫描）
_SPECIAL_PATTERNS = (
    ('数字或单位', re.compile(r'[0-9０-９]|\d{1,3}(?:[.,]\d+)?[%％‰]|[￥$€¥]|\d+\s*[万千百十]|(\d[\d,，.]*)\s*(元|台|件|个|份|公斤|千克|吨|人)')),
    ('公式符号', re.compile(r'[Σ∫√±×÷^_{}≤≥]|\b[A-Z][0-9]?\b|\\')),
)
_QUOTE_MARKERS = ('“', '”', '"', "'", '「', '」', '『', '』')


@lru_cache(maxsize=1)
def _converter():
    from opencc import OpenCC
    return OpenCC('t2s')


def detect_traditional(text: str, page_no: int, *, limit: int = 200) -> list[dict]:
    """扫描文本中的繁体字（一对一常见映射），返回规范建议。

    每条：{kind, original, suggested, page_no, start, end, context,
    method_version, ambiguous, require_review}。suggested 为 None 时表示未收录
    映射，需要人工核对或用标准工具转换。"""
    found: list[dict] = []
    try:
        simplified = _converter().convert(text)
        method = 'OpenCC t2s phrase conversion'
    except ImportError:
        simplified = ''.join(TRADITIONAL_TO_SIMPLIFIED.get(c, c) for c in text)
        method = 'limited_builtin_mapping'
    changes = []
    if len(text) == len(simplified):
        changes = [(i, i+1, simplified[i]) for i, c in enumerate(text) if c != simplified[i]]
    else:
        changes = [(i, j, simplified[a:b]) for tag, i, j, a, b in
                   SequenceMatcher(None, text, simplified, autojunk=False).get_opcodes() if tag != 'equal' and j > i]
    for index, end, suggested in changes:
        char = text[index:end]
        ambiguous = any(c in _AMBIGUOUS for c in char)
        ctx_start = max(0, index - 6)
        ctx_end = min(len(text), end + 6)
        found.append({
            'kind': 'traditional',
            'original': char, 'suggested': suggested,
            'page_no': page_no, 'start': index, 'end': end,
            'context': text[ctx_start:ctx_end],
            'method_version': TEXT_CHECK_VERSION,
            'method': method, 'confirmation': 'pending',
            'basis': '繁简字形规范化建议；繁体字本身不是错别字',
            'ambiguous': ambiguous,
            'require_review': ambiguous or _proper_noun_context(text, ctx_start, ctx_end),
        })
        if len(found) >= limit:
            break
    return found


def _proper_noun_context(text: str, start: int, end: int) -> bool:
    """专有名词启发：紧邻引号、或前后为常见姓氏/地名用字时要求人工核对。"""
    before = text[max(0, start - 1):start]
    after = text[end:end + 1]
    if before in _QUOTE_MARKERS or after in _QUOTE_MARKERS:
        return True
    name_chars = set('李王张刘陈杨赵黄周吴徐孙马朱胡郭何高林罗郑梁谢宋唐许韩冯邓曹彭曾肖田董袁潘蒋蔡余杜叶程苏魏吕丁任沈姚卢姜崔钟谭陆汪范金石廖贾夏韦付方白邹孟熊秦邱江尹薛闫段雷侯龙史陶黎贺顾毛郝龚邵万钱严覃武戴莫孔向汤')
    return before in name_chars


def detect_typo_candidates(text: str, page_no: int, *, limit: int = 200) -> list[dict]:
    """词级常见错别字候选。

    每条：{kind, original, suggested, page_no, start, end, context,
    method_version, basis, confirmation:'pending', special_verify}。
    数字/单位/公式/专有名词/引文原句相关改动 special_verify=True。"""
    found: list[dict] = []
    for word, suggestion in TYPO_DICTIONARY.items():
        if word == suggestion:
            continue
        search_from = 0
        while True:
            start = text.find(word, search_from)
            if start < 0:
                break
            end = start + len(word)
            ctx_start = max(0, start - 8)
            ctx_end = min(len(text), end + 9)
            context = text[ctx_start:ctx_end]
            special = any(pattern.search(context) for name, pattern in _SPECIAL_PATTERNS)
            if _QUOTE_MARKERS and any(m in context for m in _QUOTE_MARKERS):
                special = True
            found.append({
                'kind': 'typo', 'original': word, 'suggested': suggestion,
                'page_no': page_no, 'start': start, 'end': end, 'context': context,
                'method_version': TEXT_CHECK_VERSION,
                'basis': '内置常见错别字对照表，需人工确认后再应用',
                'confirmation': 'pending', 'special_verify': special,
            })
            search_from = end
            if len(found) >= limit:
                return found
    return found


def check_page_text(text: str, page_no: int) -> dict:
    """聚合一页的文本检查结果，供质量报告展示。"""
    traditional = detect_traditional(text, page_no)
    typos = detect_typo_candidates(text, page_no)
    return {
        'page_no': page_no,
        'status': 'suspect' if (traditional or typos) else 'pass',
        'traditional_count': len(traditional),
        'typo_count': len(typos),
        'traditional': traditional,
        'typos': typos,
        'method_version': TEXT_CHECK_VERSION,
    }


def aggregate_text_checks(pages) -> dict:
    """对文档全部页运行文本检查；只读，不修改任何存储。"""
    rows = [check_page_text(p.get('text', ''), p.get('page_no', i + 1))
            for i, p in enumerate(pages)]
    return {
        'method_version': TEXT_CHECK_VERSION,
        'pages': rows,
        'traditional_count': sum(r['traditional_count'] for r in rows),
        'typo_count': sum(r['typo_count'] for r in rows),
        'suspect_pages': [r['page_no'] for r in rows if r['status'] == 'suspect'],
    }
