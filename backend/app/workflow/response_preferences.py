"""Explicit reply preferences, kept separate from factual query conditions."""
import re


STYLE_FOLLOWUP = re.compile(
    r'^(?:(?:请|请您|帮我|麻烦|能不能|可以)[，,\s]*)?(?:再)?(?:'
    r'简短一点|简洁一点|简单一点|简单点|说简单点|说得简单点|'
    r'回答(?:简短|简洁|简单)(?:一点)?|详细解释一下|详细一点|展开说说|'
    r'通俗一点|说人话|只说(?:结果|结论)|不要长篇解释|'
    r'用一句话(?:说|回答|讲|解释|总结)?|换个说法|用表格展示|用表格回答|'
    r'分点(?:回答|说明|列出)|简单列一下)[。！？!?\s]*$')


def split_reply_request(question: str) -> tuple[str, list[str]]:
    """Remove only standalone style clauses, retaining every business qualifier."""
    if STYLE_FOLLOWUP.fullmatch(question.strip()):
        return question.strip(), []
    parts = re.split(r'[,，;；\n]|\s+(?=请(?:用|简短|详细))', question)
    styles = [p.strip() for p in parts if STYLE_FOLLOWUP.fullmatch(p.strip())]
    if not styles:
        return question, []
    kept = [p.strip() for p in parts if p.strip() and not STYLE_FOLLOWUP.fullmatch(p.strip())]
    return '，'.join(kept), styles


def reply_contract(question: str, state: dict | None = None) -> dict:
    result = {'length': 'adaptive', 'format': 'prose', 'plain_language': True,
              **((state or {}).get('reply_preferences') or {})}
    if re.search(r'简短|简洁|简单点|简单一点|只说结果|只说结论|一句话|不要长篇', question):
        result['length'] = 'brief'
    if re.search(r'详细|展开|具体步骤|完整说明', question):
        result['length'] = 'detailed'
    if re.search(r'一句话', question):
        result['format'] = 'sentence'
    elif re.search(r'用表格|表格展示', question):
        result['format'] = 'table'
    elif re.search(r'分点|列一下|罗列|列出要点', question):
        result['format'] = 'bullets'
    if re.search(r'不懂|通俗|说人话|小白|容易理解', question):
        result['plain_language'] = True
    if re.match(r'^(?:请|请您|记住|我希望)?(?:以后|之后|接下来|默认|我喜欢)', question):
        result['persist'] = True
    return result


REPLY_GUIDANCE = '''按reply_contract回应实际需求，先直接回答当前问题，避免“好的，已查询”“为您整理如下”等重复开场。
单一数值或判断通常一句话；普通概念默认一个短段落，需要时用一个贴切例子；用户要求详细时再展开。
length=brief时省去旁枝和重复背景；format=sentence时一句话保留结论与必要限制；table或bullets按用户指定组织。
先区分用户要结论、解释、比较、依据还是下一步；不能只复述原文关键词，答案必须回应所问要点。
缺信息时只问完成当前任务必需的条件，说明要补什么；有多种解释时说清差别，不让用户猜系统术语。
篇幅与格式是表达要求，不能删除事实条件、否定、证据不足、来源冲突或引用。不能补造经营原因、计算或结果。
历史回答和偏好只影响交流与表达；业务事实仍由真实数据库或原文核验提供。不要展示内部思维链。'''
