"""Whole-document overview requests, distinct from point-specific document questions."""
import re

# 指代词/泛化词不属于可继承的新闻主题（避免把“总结这些新闻”误判为新话题）。
GENERIC_TOPICS = frozenset({
    '这些', '那些', '上述', '刚才', '相关', '有关', '它', '该', '本批',
    '全部', '所有', '当前', '现有', '资料', '内容', '文档', '文件',
})

_RETRIEVAL_VERBS = r'检索|搜索|查找|查询|查阅|找一下|看看|总结|概括|提炼|摘要|归纳|整理|提取'


def news_topic(question):
    """从“检索当前知识库，特朗普的新闻”类请求中提取新闻主题。

    返回的主题只用于识别意图、继承范围核对与续问聚焦，属于识别结果，
    不充当任何事实结论。无法提取明确主题时返回 None。
    """
    q = question.strip()
    q = re.sub(r'^(?:请|帮我|麻烦|麻烦你|把|将)+', '', q)
    q = re.sub(r'(?:当前|现有)?知识库(?:中|里|内|的)?', '', q)
    q = re.sub(r'^(?:检索|搜索|查找|查询|查阅|找一下|获取|从|总结|概括|提炼|归纳|整理|提取)+(?:一下)?', '', q)
    q = re.sub(r'(?:概括成|总结成|整理成|写成|提取成|做成|生成|写)?摘要$', '', q)
    q = re.sub(r'^[，,、。；;：:\s]*', '', q)
    q = re.sub(r'^(?:当前|现有)', '', q)
    q = re.sub(r'[，,、。；;：:]', '', q)
    q = re.sub(r'(?:提取成|整理成|做成|生成|写)?摘要$', '', q)
    q = re.sub(r'(?:的|相关|有关)?(?:新闻|报道|资讯|资料|文档|内容|文章|消息|信息)?[。？?!！]?$', '', q)
    q = re.sub(r'(?:相关|有关|的)?(?:全部|所有|一切)$', '', q)
    q = re.sub(r'(?:相关|有关)$', '', q)
    q = q.strip('的').strip()
    if not q or q in GENERIC_TOPICS:
        return None
    if re.search(r'[一-鿿]{2,}', q) or re.search(r'\b[a-zA-Z]{3,}\b', q):
        return q
    return None


def news_topic_request(question):
    """是否“检索/总结某主题新闻”类请求：有检索或摘要动词、新闻词和一个具体主题。

    这类请求应由主题事件摘要路径处理（带引用），而不是当作逐字问答去核验。
    用户明确只要来源列表时，由 wants_source_list 单独识别。
    """
    if not re.search(_RETRIEVAL_VERBS, question):
        return False
    if not re.search(r'新闻|报道|资讯', question):
        return False
    topic = news_topic(question)
    if not topic:
        return False
    if re.search(r'什么|哪些|谁|多少|是否|如何|为什么|何时|哪|几', topic):
        return False
    return True


def wants_source_list(question):
    """用户明确只要来源列表（不要综合摘要）时返回 True。"""
    return bool(re.search(
        r'列出|列举|罗列|来源列表|来源清单|来源有哪些|来源是哪些|'
        r'只?(?:要|需).*(?:来源|列表|清单)|'
        r'分别列出|单独列出|只要列表|不要?(?:摘要|总结|概括)|'
        r'不用?(?:总结|概括|提炼).*(?:直接)?列出|'
        r'哪些(?:文档|文件|新闻|报道)(?:涉及|包含|提到|有关)', question, re.I))


def is_document_summary(question: str, *, document_context: bool = False) -> bool:
    if re.search(r'(?:不要|无需|不必|不需要)\s*(?:总结|概括|提炼)', question):
        return False
    summarizing = re.search(r'总结|概括|概述|提炼|summari[sz]e|overview', question, re.I)
    if not summarizing and re.search(r'逐字|原样|全文复制|导出原文|提取原文|原文提取|完整原文', question):
        return False
    if summarizing:
        return True
    if re.search(r'谁|多少|哪[一几]?页|页码|位置|何时|哪一天', question):
        return False
    if re.search(r'摘要|主要内容|内容概要|核心内容|主要讲|大意|讲了什么|讲些什么|内容要点|summary', question, re.I):
        return True
    # Generic extraction of a selected document means a useful overview. Specific
    # extraction (dates, fields, rules, amounts) remains a fact question.
    has_source = document_context or bool(re.search(r'文档|文件|附件|PDF|Word|DOCX', question, re.I))
    if has_source and re.fullmatch(
        r'(?:请|帮我|麻烦)?\s*(?:介绍|说明|展示|看看|阅读)?\s*'
        r'(?:(?:当前|刚刚|上传的?|选中的?|这份|这个|该|附件|文档|文件|TXT|MD|Markdown|PDF|Word|DOCX|的)\s*)+'
        r'(?:内容(?:是什么|有哪些)?|写了什么|讲了什么)[。！？!?\s]*', question, re.I):
        return True
    return has_source and bool(re.fullmatch(
        r'(?:请|帮我|麻烦|能否|可以)?\s*(?:提取|整理|阅读|浏览|读一下|看看|看一下)'
        r'(?:一下)?\s*(?:(?:其中|里面|文中|它|这份|这个|上传的|选中的|附件|文档|文件|PDF|Word|DOCX|全文|中|里|的)\s*)*'
        r'(?:内容|都讲了什么|讲的什么|讲了什么)[。！？!?\s]*', question, re.I))
