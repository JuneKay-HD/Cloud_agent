"""Parse the supported annual-query contract without silently dropping conditions."""
import re
from typing import Any

from app.clients.bailian_chat import BailianChatError, chat_json
from app.config import get_settings
from app.schemas.task import QueryTask
from app.sql.schema_linker import load_sql_context
from app.workflow.language import normalize_question, mentioned_countries
from app.workflow.understanding import inspect_language, unresolved_fragment, protected_edit, canonical_question

SYSTEM_PROMPT = """解析年度问数任务，输出严格 JSON。
intent 为 sql、rag、hybrid、unknown。指标只能使用本次 business_catalog 中的已注册标识；不得发明指标或改变业务口径。
dimensions 仅 billing_country；filters 格式为 {field: billing_country, operator: in, values: [USA]}。
扩展dimensions可为month或customer_id。多个指标用metric_ids列表，metric_id为首项。
单笔发票金额筛选用invoice_amount={operator: >或>=或<或<=, value: 数值}；不能把客户累计消费条件当成单笔金额。
time_range 和 baseline_time_range 为 start/end，YYYY-MM-DD，结束日期不含。
comparison 为 year_over_year 或 period_over_period，不能将比较的两个年份合并。
缺失条件加入 missing_fields。不支持的指标、条件不得替换为销售额。
返回 intent, metric_id, dimensions, filters, time_range, baseline_time_range,
comparison, top_n, missing_fields, ambiguities。"""

CN_NUMBERS = {"一": 1, "二": 2, "两": 2, "三": 3, "四": 4, "五": 5,
              "六": 6, "七": 7, "八": 8, "九": 9, "十": 10}


def _period(year: int) -> dict[str, str]:
    return {"start": f"{year:04d}-01-01", "end": f"{year + 1:04d}-01-01"}


def _alias_pattern(alias: str) -> str:
    escaped = re.escape(alias)
    return rf"(?<![A-Za-z]){escaped}(?![A-Za-z])" if alias.isascii() else escaped


def is_metric_followup(question: str, state: dict) -> bool:
    """A standalone metric question changes only the metric of an active SQL task."""
    previous = state.get("last_task") or {}
    if state.get("cancelled_task") or previous.get("intent") != "sql":
        return False
    catalog = load_sql_context()
    aliases = [alias for definition in catalog["metrics"]["metrics"].values()
               for alias in [definition["name"], *definition.get("aliases", [])]]
    metric = "|".join(re.escape(a) for a in sorted(aliases, key=len, reverse=True))
    return bool(re.fullmatch(
        rf"(?:那|那么|再看|改成|改为|换成|换为)?(?:{metric})(?:是多少|有多少|多少|呢)?[？?。！!\s]*",
        normalize_question(question).strip()))


def _fallback_core(question: str, state: dict[str, Any] | None = None) -> QueryTask:
    from app.workflow.dataset_count import dataset_count_task, dataset_count_followup
    overview = dataset_count_task(question) or dataset_count_followup(question, state)
    if overview is None and ((state or {}).get('last_task') or {}).get('query_scope') == 'dataset' and is_metric_followup(question, state):
        followup = re.sub(r'^(?:那|那么|再看|改成|改为|换成|换为)', '', normalize_question(question))
        followup = re.sub(r'呢[？?。！!\s]*$', '', followup)
        overview = dataset_count_task('数据集中'+followup)
    if overview is not None:
        return overview
    state = state or {}
    question = question.rsplit("用户补充：", 1)[-1].strip()
    catalog = load_sql_context()
    previous = state.get("last_task") or {}
    original_question = question
    understanding = inspect_language(question, catalog)
    question = normalize_question(understanding["normalized"])
    month_group = bool(re.search(r"每个月|每月|按月|逐月", question))
    customer_group = bool(re.search(r"消费最多的客户|按客户|各客户|每个客户", question))
    amount = re.search(r"(?:只统计|仅统计|筛选)?(?:单笔)?(?:发票金额|金额)(超过|大于|小于|至少|至多|不低于|不超过)(\d+(?:\.\d+)?)元?(?:的发票)?", question)
    if amount:
        task_amount = {'operator': {'超过':'>','大于':'>','小于':'<','至少':'>=','不低于':'>=','至多':'<=','不超过':'<='}[amount[1]], 'value':float(amount[2])}
        question = question[:amount.start()] + question[amount.end():]
    else:
        task_amount = None
    question = re.sub(r"每个月|每月|按月|逐月", "", question)
    question = question.replace('消费最多的客户', '最高').replace('消费金额', '销售额')
    if customer_group:
        question = re.sub(r"客户姓名|按客户|各客户|每个客户|姓名", "", question)
    reference = re.search(r"前者|后者|该国家|该地区", question)
    reference_error = None
    if reference:
        countries = catalog["entities"]["entities"]["billing_country"]["value_aliases"]
        ordered = mentioned_countries(previous.get("raw_question", ""), countries)
        allowed = [v for f in previous.get("filters", []) if f.get("field") == "billing_country" for v in f.get("values", [])]
        if reference[0] in {"前者", "后者"} and len(ordered) == 2 and set(ordered) == set(allowed):
            target = ordered[0 if reference[0] == "前者" else 1]
        elif reference[0] in {"该国家", "该地区"} and len(allowed) == 1:
            target = allowed[0]
        else:
            target = None
        if target:
            question = question.replace(reference[0], target)
        else:
            reference_error = "请明确指代的国家（例如美国或加拿大），当前上下文不能唯一确定"
            question = question.replace(reference[0], "")
    metric_terms = [alias for definition in catalog["metrics"]["metrics"].values()
                    for alias in [definition["name"], *definition.get("aliases", [])]]
    fresh_query = bool(re.match(r"(?:请|帮我)?(?:查询|统计)", question)
                       and any(term in question for term in metric_terms))
    followup = not state.get("cancelled_task") and not fresh_query and (is_metric_followup(question, state) or bool(state.get("pending_message")) or bool(
        (reference or re.search(r"呢|改成|改为|换成|换为|同上|那|同比|环比", question))
        and previous.get("intent") in {"sql", "hybrid"}
    ))
    task = QueryTask(raw_question=original_question, intent="sql", normalized_question=question,
                     source_id=understanding["source_id"], element_evidence=understanding["elements"])
    if understanding["ambiguous_source"]:
        task.ambiguities.append("请您明确使用哪个业务数据库，当前存在多个来源")
    if reference_error:
        task.ambiguities.append(reference_error)
    if followup:
        for key in ("metric_id", "metric_ids", "invoice_amount", "customer_ids", "dimensions", "filters", "time_range", "comparison",
                    "baseline_time_range", "top_n", "comparison_requested"):
            setattr(task, key, previous.get(key, getattr(task, key)))
            if previous.get(key):
                task.inherited_fields.append(key)
    if re.search(r"口径|定义|规定|规则|什么是", question):
        return QueryTask(intent="rag", raw_question=question)
    hybrid = bool(re.search(r"建议|方法", question) and re.search(r"最高|最多|冠军", question))
    if hybrid:
        task.intent = "hybrid"

    unsupported = re.search(
        r"利润|毛利|净收入|净销售|销量|销售量|成本|扣除|退款|环比|季度|[上下]半年|"
        r"\d+\s*月|\d{4}-\d{1,2}|今年|去年|最近|排除|除了|除外|不含|不是|"
        r"大于|小于|超过|至少|至多|平均|最低|最少|倒数|按年|每年|逐年|每个月|每月|按月|逐月|按天|每天|逐日", question
    )
    if unsupported:
        task.unsupported_reason = (
            f"当前受控查询暂不支持“{unsupported.group()}”。支持整年销售额、发票数、客户数、"
            "国家筛选/分组/排名和年度同比；未执行简化查询。"
        )
        return task

    remaining = question
    matched_metrics = []
    for metric_id, definition in catalog["metrics"]["metrics"].items():
        aliases = sorted([definition["name"], *definition.get("aliases", [])], key=len, reverse=True)
        pattern = "|".join(_alias_pattern(alias) for alias in aliases)
        if re.search(pattern, question, re.I):
            matched_metrics.append(metric_id)
            remaining = re.sub(pattern, "", remaining, flags=re.I)
    if "销售增长率" in question:
        matched_metrics.append("sales_amount")
        remaining = remaining.replace("销售增长率", "增长率")
    if matched_metrics:
        task.metric_id = matched_metrics[0]
        task.metric_ids = list(dict.fromkeys(matched_metrics)) if len(set(matched_metrics)) > 1 else []
        task.inherited_fields = [x for x in task.inherited_fields if x != "metric_id"]

    countries = catalog["entities"]["entities"]["billing_country"]["value_aliases"]
    found = []
    for canonical, aliases in countries.items():
        pattern = "|".join(_alias_pattern(a) for a in sorted([canonical, *aliases], key=len, reverse=True))
        if re.search(pattern, question, re.I):
            found.append(canonical)
            remaining = re.sub(pattern, "", remaining, flags=re.I)
    if found:
        task.filters = [{"field": "billing_country", "operator": "in", "values": sorted(found)}]
        task.inherited_fields = [x for x in task.inherited_fields if x != "filters"]
    if re.search(r"所有国家|全部国家|全球|所有地区|全部地区", question):
        if found:
            task.ambiguities.append("同时指定了具体国家和全部国家，请明确查询范围")
        task.filters = []
        task.inherited_fields = [x for x in task.inherited_fields if x != "filters"]

    grouping = bool(re.search(r"分别|各国|各国家|各地区|每个国家|每个地区|按国家|按地区|国家排名|地区排名", question))
    ranking = re.search(r"前\s*(\d+|[一二两三四五六七八九十])\s*名?", question)
    highest = bool(re.search(r"最高|最多|冠军", question))
    if grouping or highest or ranking:
        task.dimensions = ["billing_country"]
        task.inherited_fields = [x for x in task.inherited_fields if x != "dimensions"]
    elif found or re.search(r"合计|总计|所有国家|全部国家|全球", question):
        task.dimensions = []
        task.top_n = None
    if ranking:
        value = ranking.group(1)
        task.top_n = int(value) if value.isdigit() else CN_NUMBERS[value]
        if not 1 <= task.top_n <= 200:
            task.unsupported_reason = "排名数量须在1至200之间，未执行查询。"
            return task
    elif highest:
        task.top_n = 1
    elif grouping:
        task.top_n = None
    if month_group:
        task.dimensions = ['month']
    if customer_group:
        task.dimensions = ['customer_id']
    if month_group or customer_group:
        task.inherited_fields = [f for f in task.inherited_fields if f != 'dimensions']
    if task_amount is not None:
        task.invoice_amount = task_amount
        task.inherited_fields = [f for f in task.inherited_fields if f != 'invoice_amount']
    if month_group and customer_group:
        task.unsupported_reason = '暂不支持同时按月和客户分组，请分别查询。'
    if (task.metric_ids or month_group or customer_group) and task.comparison:
        task.unsupported_reason = '新增分组和多指标暂不支持年度比较，请分别查询。'

    years = list(dict.fromkeys(int(v) for v in re.findall(r"(?<!\d)(?:19|20|21)\d{2}(?!\d)", question)))
    wants_comparison = bool(re.search(r"同比|增长率|增幅|相比|相较|比较|对比|比.*年", question))
    task.comparison_requested = task.comparison_requested or wants_comparison
    if wants_comparison and not task.comparison:
        task.comparison = "year_over_year" if "同比" in question else "period_over_period"
    if "同比" in question:
        task.comparison = "year_over_year"
    if years:
        task.inherited_fields = [x for x in task.inherited_fields if x not in {"time_range", "baseline_time_range"}]
        if task.comparison:
            direct = re.search(r"((?:19|20|21)\d{2})年?\s*(?:相比|相较于?|对比|比)\s*((?:19|20|21)\d{2})", question)
            reverse = re.search(r"相比\s*((?:19|20|21)\d{2})年.*?((?:19|20|21)\d{2})年", question)
            if len(years) > 2:
                task.ambiguities.append("一次年度比较仅支持本期和基期两个年份")
            if followup and "baseline_time_range" in previous.get("missing_fields", []) and len(years) == 1:
                current, baseline = int(previous["time_range"]["start"][:4]), years[0]
            elif direct:
                current, baseline = map(int, direct.groups())
            elif reverse:
                baseline, current = map(int, reverse.groups())
            elif len(years) == 1:
                current, baseline = years[0], years[0] - 1
                if task.comparison != "year_over_year":
                    task.missing_fields.append("baseline_time_range")
            elif len(years) == 2 and task.comparison == "year_over_year":
                baseline, current = sorted(years)
            else:
                current, baseline = years[0], years[-1]
                task.ambiguities.append("请明确本期和基期，例如2012年相比2011年")
            task.time_range, task.baseline_time_range = _period(current), _period(baseline)
            if task.comparison == "year_over_year" and baseline != current - 1:
                task.ambiguities.append("年度同比的基期须为本期上一年，请修改年份或改为两个年度比较")
        elif len(years) == 1:
            task.time_range = _period(years[0])
            task.baseline_time_range = None
        elif len(years) == 2 and re.search(r"合计|总计|至|到", question):
            if max(years) - min(years) > 1 and not re.search(r"至|到", question):
                task.unsupported_reason = "当前不支持不连续年份合计；请分别查询，或明确连续年份范围。"
            task.time_range = {"start": _period(min(years))["start"], "end": _period(max(years))["end"]}
        else:
            task.ambiguities.append("多个年份需要明确合计还是比较，请补充")
    elif task.comparison and task.time_range and not task.baseline_time_range:
        current = int(task.time_range["start"][:4])
        if task.comparison == "year_over_year":
            task.baseline_time_range = _period(current - 1)
        else:
            task.missing_fields.append("baseline_time_range")
    if "增长率" in question and not re.search(r"同比|相比|相较|比.*年", question) and not (followup and previous.get("comparison")):
        task.comparison = None
        task.missing_fields.append("comparison")

    if task.comparison_requested and not task.comparison:
        task.missing_fields.append("comparison")
    if not task.metric_id:
        task.missing_fields.append("metric_id")
    if not task.time_range:
        task.missing_fields.append("time_range")
    if task.comparison and task.top_n:
        task.unsupported_reason = "当前比较查询支持各国家对比，但暂不支持对增长率排名；未执行查询。"
    # Unknown words may be a filter. Do not discard them.
    remaining = re.sub(r"(?<!\d)(?:19|20|21)\d{2}(?!\d)|前\s*(?:\d+|[一二两三四五六七八九十])\s*名?", "", remaining)
    vocabulary = [
        "指定两个年份", "每个国家", "每个地区", "所有国家", "全部国家", "所有地区", "全部地区",
        "销售增长率", "同比增长率", "增长率", "相比", "相较于", "相较", "对比", "比较",
        "统计", "查询", "查一下", "帮我", "请问", "请", "多少", "是什么", "怎样", "如何",
        "各国家", "各地区", "各国", "按国家", "按地区", "国家排名", "地区排名", "国家", "地区",
        "合计", "总计", "总共", "总额", "金额", "全球", "最高", "最多", "冠军", "找出",
        "经营建议", "建议", "方法论", "方法", "总结", "同比", "增幅", "本期", "基期",
        "改成", "改为", "换成", "换为", "同上", "分别", "以及", "然后", "该", "那",
        "年", "的", "和", "与", "及", "并", "是", "有", "为", "呢", "吗", "比", "从", "到", "至",
    ]
    for word in sorted(vocabulary, key=len, reverse=True):
        remaining = remaining.replace(word, "")
    remaining = re.sub(r"[\s，。！？、：；,.!?;:（）()]+", "", remaining)
    if remaining:
        task.unresolved_fragments = [unresolved_fragment(remaining[:120])]
        task.unsupported_reason = f"已保留原文中的未解析内容“{remaining[:60]}”，尚未找到可靠的字段或操作映射，因此未执行查询。请您补充其含义或对应字段。"
    task.missing_fields = list(dict.fromkeys(task.missing_fields))
    task.needs_clarification = bool(task.missing_fields or task.ambiguities)
    return task


def _finalize_task(task, original):
    if not task.element_evidence:
        understanding = inspect_language(original, load_sql_context())
        task.element_evidence = understanding["elements"]
        task.source_id = understanding["source_id"]
        task.normalized_question = task.normalized_question or normalize_question(understanding["normalized"])
    if task.intent == 'sql' and task.schema_plan is None and any(x in task.mode_reason for x in ['schema', '实体', '要素']):
        task.canonical_question = '待明确：' + original
    else:
        task.canonical_question = canonical_question(task)
    return task


def _fallback(question: str, state: dict[str, Any] | None = None) -> QueryTask:
    return _finalize_task(_fallback_core(question, state), question.rsplit("用户补充：", 1)[-1].strip())


# Model rewrites may change language, never erase numerical/filter constraints.
_REWRITE_TARGETS = {"销售额", "客户数量", "发票数量", "最高", "查询", "经营建议", "口径", "规则", "单位", "时间字段", "国家字段"}
_PROTECTED = re.compile(r"[0-9]|美国|加拿大|中国|法国|德国|日本|USA|Canada|China|France|Germany|VIP|除了|排除|以外|不含|大于|小于|至少|至多|超过|同比|环比|利润|成本|退款|季度|半年|客户ID|CustomerId", re.I)


def _model_language(question, raw):
    rewritten = question
    edits = raw.get("language_edits", [])
    if not isinstance(edits, list) or len(edits) > 8:
        raise ValueError("语言改写结构无效")
    for edit in edits:
        if not isinstance(edit, dict) or set(edit) != {"source", "target"}:
            raise ValueError("语言改写字段无效")
        source, target = edit["source"], edit["target"]
        if (not isinstance(source, str) or not source.strip() or len(source) > 24
                or not isinstance(target, str) or target not in _REWRITE_TARGETS or rewritten.count(source) != 1
                or _PROTECTED.search(source) or protected_edit(source, load_sql_context())):
            raise ValueError("语言改写不能删除或更改实体、数值、业务限制")
        rewritten = rewritten.replace(source, target, 1)
    return rewritten


def _document_question(question):
    # A semantic document route is allowed only for explanatory requests.
    # Numerical requests and mutation instructions cannot become document answers.
    explanation = re.search(
        r"口径|定义|规定|规则|什么是|怎么处理|如何处理|哪个.*字段|单位|含义|说明|解释|制度|标准|"
        r"总结.*(?:文件|文档|附件)|(?:文件|文档|附件).*内容|这份(?:文件|文档)",
        question,
    )
    numerical = re.search(r"查询.*销售额|计算|合计|多少元|增长率|前[一二三四五六七八九十0-9]+|最高|最低", question)
    mutation = re.search(r"删除|修改.*表|DROP|DELETE|INSERT|UPDATE", question, re.I)
    return bool(explanation and not numerical and not mutation)


def _propose_candidate(candidate, original):
    """Unrecognized wording requires user confirmation before any execution."""
    from app.sql.generator import validate_task, UnsupportedQuery
    if candidate.intent not in {"sql", "hybrid"}:
        return None
    if candidate.query_scope == 'dataset':
        return None
    try:
        validate_task(candidate)
    except (UnsupportedQuery, ValueError, TypeError):
        return None
    names = {"sales_amount": "销售额", "invoice_count": "发票数量", "customer_count": "客户数量"}
    period = candidate.time_range
    start, end = int(period["start"][:4]), int(period["end"][:4]) - 1
    years = f"{start}年" if start == end else f"{start}年至{end}年"
    countries = "和".join(v for f in candidate.filters for v in f["values"])
    group = "各国家" if candidate.dimensions and not countries else "分别" if candidate.dimensions else ""
    text = "查询" + years + countries + group + names[candidate.metric_id]
    if candidate.comparison:
        baseline = candidate.baseline_time_range["start"][:4]
        text = "查询" + countries + group + names[candidate.metric_id] + f"{start}年相比{baseline}年"
        if candidate.comparison == "year_over_year":
            text += "同比"
    if candidate.top_n:
        text += f"前{candidate.top_n}名"
    if candidate.intent == "hybrid":
        text += "，并总结该地区经营建议"
    candidate.raw_question = original
    candidate.suggested_question = text
    candidate.needs_clarification = True
    candidate.ambiguities = ["模型提出了以下查询理解，请核对是否遗漏限制条件：" + text]
    candidate.parsing_mode = "model_checked"
    candidate.mode_reason = "模型提取了结构化条件；已校验支持范围，等待用户确认后执行"
    return candidate


async def _parse_intent_core(question: str, session_state: dict[str, Any]) -> QueryTask:
    from app.workflow.query_clarification import clarification_task
    incomplete = clarification_task(question, session_state)
    if incomplete is not None:
        return incomplete
    original = question.rsplit("用户补充：", 1)[-1].strip()
    from app.workflow.schema_semantics import semantic_task, schema_request, pending_schema
    if get_settings().dataset_profile == 'public' and session_state.get('_quantity_resolution') is not None:
        semantic = await semantic_task(question, session_state)
        if semantic is not None:
            return semantic
    from app.workflow.dataset_count import dataset_count_task, dataset_count_followup
    total = dataset_count_followup(original, session_state) or dataset_count_task(original)
    if total is not None:
        return total
    if get_settings().dataset_profile=='public' and pending_schema(session_state):
        semantic=await semantic_task(question, session_state)
        if semantic is not None:
            return semantic
    if get_settings().dataset_profile=='public' and (schema_request(original, session_state) or (session_state.get('last_task') or {}).get('query_scope')=='schema'):
        from app.sql.schema_query import catalog, TABLES
        if set(catalog()['tables']) - set(TABLES):
            semantic = await semantic_task(question, session_state)
            if semantic is not None:
                return semantic
    contract = _fallback(question, session_state)
    if contract.query_scope == 'dataset':
        return contract
    if schema_request(original,session_state) and (contract.unsupported_reason or contract.needs_clarification
            or (session_state.get('last_task') or {}).get('query_scope')=='schema'):
        semantic=await semantic_task(question,session_state)
        if semantic is not None:return semantic
    try:
        raw = await chat_json(
            SYSTEM_PROMPT + "\n先区分文档解释、数值查询和跨源需求。退款如何处理、字段及单位解释属于rag，不能当作退款金额查询。"
            "可额外返回language_edits列表，每项source为用户原文连续短语、target为规范术语。"
            "仅改写同义表达，不得删除实体、时间、数字、否定、过滤条件或未知业务词。"
            "允许target：" + "、".join(sorted(_REWRITE_TARGETS)),
            {
                "question": question,
                "known_context": session_state.get("last_task"),
                "business_catalog": load_sql_context()["metrics"],
                "entity_catalog": load_sql_context()["entities"],
                "schema_catalog": load_sql_context()["schema"],
                "identified_elements": contract.element_evidence,
                "unresolved_fragments": contract.unresolved_fragments,
                "uploaded_documents": [doc["name"] for doc in session_state.get("documents", [])],
                "recent_messages": [
                    {"role": msg["role"], "text": msg["text"][:2000]}
                    for msg in session_state.get("messages", [])[-6:]
                ],
            },
        )
        if not isinstance(raw, dict):
            raise ValueError("模型响应必须为对象")
        candidate = QueryTask.model_validate(raw)
        if candidate.intent == "rag" and _document_question(original):
            return QueryTask(intent="rag", raw_question=original, parsing_mode="model_checked",
                             mode_reason="模型识别为文档解释；已检查不涉及数值执行或写操作")
        if contract.unsupported_reason and contract.unsupported_reason.startswith("当前"):
            contract.mode_reason = "模型已参与识别；原问题包含未支持的条件，未生成简化查询"
            return contract
        if contract.unsupported_reason and any(x["role"] == "query_constraint" for x in contract.unresolved_fragments):
            contract.mode_reason = "模型参与识别，但真实筛选条件尚未映射；保留条件，不提议删除条件的简化查询"
            return contract
        if contract.unsupported_reason:
            proposed = _propose_candidate(candidate, original)
            if proposed is not None:
                return proposed
        rewritten = _model_language(original, raw) if contract.unsupported_reason else original
        if rewritten != original:
            checked = _fallback(rewritten, session_state)
            if not checked.unsupported_reason:
                checked.raw_question = original
                if contract.unsupported_reason and checked.intent != "rag":
                    checked.ambiguities.append("请核对模型改写是否完整保留您的条件，并按该问法重新提问：" + rewritten)
                    checked.needs_clarification = True
                checked.parsing_mode = "model_checked"
                checked.mode_reason = "模型同义改写经条件校验：" + rewritten
                return checked
        fields = ("intent", "metric_id", "filters", "dimensions", "time_range", "comparison",
                  "baseline_time_range", "top_n", "metric_ids", "invoice_amount", "customer_ids")
        if all(getattr(candidate, f) == getattr(contract, f) for f in fields):
            contract.parsing_mode = "model_checked"
            contract.mode_reason = "模型解析与受控条件核对一致"
        else:
            contract.mode_reason = "模型解析与受控条件不一致，保留原始限制；采用安全规则结果或请求补充"
    except BailianChatError as exc:
        reason = "模型服务请求失败"
        if "配置" in str(exc):
            reason = "模型服务未配置"
        elif "合法 JSON" in str(exc):
            reason = "模型未返回合法JSON"
        contract.mode_reason = reason + "，使用受控规则解析；不支持的条件将阻止执行"
    except (ValueError, TypeError, KeyError):
        if session_state.get("documents") and _document_question(original):
            return QueryTask(
                intent="rag",
                raw_question=original,
                parsing_mode="rules",
                mode_reason="模型输出未通过校验；根据当前会话文档和问题表达进入文档问答",
            )
        contract.mode_reason = "模型输出未通过条件校验，使用受控规则解析；未放宽原始限制"
    return contract


from app.workflow.progress import observed

@observed('识别指标、实体与时间')
async def parse_intent(question: str, session_state: dict[str, Any]) -> QueryTask:
    from app.workflow.comparison_contract import annual_comparison_task
    comparison = annual_comparison_task(question, session_state, _fallback_core)
    if comparison is not None:
        return _finalize_task(comparison, question)
    from app.workflow.schema_semantics import pending_schema
    original = question.strip() if pending_schema(session_state) else question.rsplit("用户补充：", 1)[-1].strip()
    return _finalize_task(await _parse_intent_core(question, session_state), original)
