"""Preserve a requested growth calculation across clarification and plan boundaries.

This compiles the existing bounded annual business contract. Unknown constraints
remain unknown; unsupported combinations are never replaced with raw yearly data.
"""
import math
import re
from datetime import date

from app.config import get_settings

YEAR = re.compile(r'(?<!\d)(?:19|20|21)\d{2}(?!\d)')
RATE = re.compile(r'同比|增长率|增幅|变化率|增长百分比|增速')
CANCEL_RATE = re.compile(r'(?:不要|不用|无需|不再)(?:计算|算|看)?(?:同比|增长率|增幅|变化率)|取消(?:同比|增长率|比较)')


def requested_rate(question):
    latest = question.rsplit('用户补充：', 1)[-1]
    if CANCEL_RATE.search(latest):
        return False
    return bool(RATE.search(question))


def _period(year):
    return {'start': date(year, 1, 1).isoformat(), 'end': date(year + 1, 1, 1).isoformat()}


def _roles(parts):
    current = baseline = None
    mode = None
    for index, text in enumerate(parts):
        years = list(dict.fromkeys(int(m[0]) for m in YEAR.finditer(text)))
        negated_yoy = bool(re.search(r'不是同比|不按同比|改为年度比较|改成年度比较', text))
        positive = re.sub(r'不是同比|不按同比', '', text)
        if negated_yoy:
            mode = 'period_over_period'
        elif '同比' in positive:
            mode = 'year_over_year'
        elif mode is None and re.search(r'相比|相较|对比|基期|基准年|比.*年', text):
            mode = 'period_over_period'
        direct = re.search(r'((?:19|20|21)\d{2})年?\s*(?:相比|相较于?|对比|比|与|和)\s*((?:19|20|21)\d{2})年?', text)
        explicit_current = re.search(r'(?:本期|当前年份|本年)(?:年份)?(?:改为|改成|为|是|：|:)?\s*((?:19|20|21)\d{2})', text)
        explicit_base = re.search(r'(?:基期|基准年|基年)(?:年份)?(?:改为|改成|为|是|：|:)?\s*((?:19|20|21)\d{2})', text)
        compared_to = re.search(r'(?:与|和|相比|相较于?|对比|同比)\s*((?:19|20|21)\d{2})年?', positive)
        if direct:
            current, baseline = map(int, direct.groups())
        elif compared_to and current is not None:
            baseline = int(compared_to[1])
        elif len(years) == 1:
            if current is None:
                if explicit_base:
                    baseline = years[0]
                else:
                    current = years[0]
            elif index and not explicit_current:
                baseline = years[0]
                mode = mode or 'period_over_period'
        elif len(years) == 2:
            if compared_to:
                baseline = int(compared_to[1])
                current = next(y for y in years if y != baseline)
            else:
                current, baseline = years
        if explicit_current:
            current = int(explicit_current[1])
        if explicit_base:
            baseline = int(explicit_base[1])
    if mode == 'year_over_year' and current is not None and baseline is None:
        baseline = current - 1
    return current, baseline, mode


def annual_comparison_task(question, state, fallback):
    latest = question.rsplit('用户补充：', 1)[-1].strip()
    cancellation = CANCEL_RATE.search(latest)
    if cancellation:
        # Explicit user cancellation supersedes the pending operation, while
        # keeping metric/filters available to a short follow-up.
        clean = latest[:cancellation.start()] + latest[cancellation.end():]
        clean = re.sub(r'^了?[，,。；;\s]*', '', clean)
        clean = re.sub(r'^(?:只查询|只看|仅查询|仅看)', '查询', clean)
        previous = dict(state.get('last_task') or {})
        previous.update(comparison=None, comparison_requested=False, baseline_time_range=None,
                        clarification_context=None)
        task = fallback(clean, {**state, 'pending_message': None, 'last_task': previous})
        task.raw_question = question
        task.mode_reason = '用户明确取消增长率计算，按本轮剩余条件重新核对并执行'
        return task
    pending = state.get('pending_message') or ''
    if (pending and '用户补充：' not in question and requested_rate(pending)
            and (re.match(r'与|和|相比|相较|基期|基准年|同比|改成|改为|换成', question)
                 or re.fullmatch(r'\s*(?:19|20|21)\d{2}年?[。？?\s]*', question))):
        question = pending + '\n用户补充：' + question
    latest = question.rsplit('用户补充：', 1)[-1].strip()
    # An explicit, self-contained query replaces a pending calculation.
    if ('用户补充：' in question and re.match(r'(?:请|帮我)?(?:查询|统计)', latest)
            and re.search(r'销售额|发票数|客户数', latest)):
        question = latest
        state = {}
    if not requested_rate(question):
        # A completed comparison's short country/metric follow-up keeps its checked
        # fields through the existing inheritance path, without inventing context.
        return None
    if re.search(r'知识库|附件|文档|什么是|是什么意思|定义|公式是什么', question):
        return None
    if get_settings().dataset_profile == 'public':
        from app.sql.schema_query import catalog
        invoice = catalog()['tables'].get('Invoice', {})
        if not {'InvoiceDate', 'BillingCountry', 'Total', 'CustomerId', 'InvoiceId'}.issubset(invoice.get('columns', {})):
            return None
    parts = [part.strip() for part in question.split('用户补充：') if part.strip()]
    source = '，'.join(parts)
    # Normalize only known calculation wording. Entity, numeric, exclusion and
    # unknown business qualifiers remain in the input checked by the compiler.
    clean = re.sub(r'不是同比|不按同比', '', source)
    clean = re.sub(r'改为年度比较|改成年度比较', '相比', clean)
    clean = re.sub(r'变化率|增长百分比|增速|增幅', '增长率', clean)
    clean = re.sub(r'基准年', '基期', clean)
    clean = re.sub(r'(^|[，,。；;\n])\s*(?:请|帮我)?(?:计算|算一下|算出|给出)', r'\1', clean)
    clean = re.sub(r'(?:计算|算一下|算出|给出)(?=同比|增长率)', '', clean)
    task = fallback(clean, {} if len(parts) > 1 else state)
    if task.intent != 'sql' or task.metric_id not in {None, 'sales_amount', 'invoice_count', 'customer_count'}:
        return None
    if task.query_scope != 'business':
        return None
    current, baseline, mode = _roles(parts)
    if len(parts) == 1 and state.get('last_task') and not YEAR.search(question):
        previous = state['last_task']
        if previous.get('query_scope', 'business') == 'business' and 'time_range' in task.inherited_fields:
            current = int(task.time_range['start'][:4]) if task.time_range else current
            baseline = int(task.baseline_time_range['start'][:4]) if task.baseline_time_range else baseline
            if mode == 'year_over_year' and current is not None and baseline is None:
                baseline = current - 1
    task.comparison_requested = True
    task.comparison = mode
    task.time_range = _period(current) if current is not None else None
    task.baseline_time_range = _period(baseline) if baseline is not None and mode else None
    task.missing_fields = [f for f in task.missing_fields if f not in {'time_range', 'baseline_time_range', 'comparison'}]
    if current is None:
        task.missing_fields.append('time_range')
    if mode is None:
        task.missing_fields.append('comparison')
    elif baseline is None:
        task.missing_fields.append('baseline_time_range')
    generated = ('多个年份需要明确', '请明确本期和基期', '年度同比的基期须为本期上一年')
    task.ambiguities = [a for a in task.ambiguities if not a.startswith(generated)]
    if current is not None and baseline is not None:
        if current == baseline:
            task.ambiguities.append('本期与基期不能是同一年，请确认比较年份')
        elif mode == 'year_over_year' and baseline != current - 1:
            task.ambiguities.append('年度同比基期必须是本期上一年，请确认年份或改为明确的年度比较')
    task.missing_fields = list(dict.fromkeys(task.missing_fields))
    task.needs_clarification = bool(task.missing_fields or task.ambiguities)
    task.raw_question = question
    task.normalized_question = clean
    task.mode_reason = ('保留原增长率目标与用户补充；比较条件尚待明确，未执行统计' if task.needs_clarification
                        else '保留原增长率目标与用户补充；本期、基期和指标进入受控年度比较计划')
    task.clarification_context = {'kind': 'annual_comparison', 'origin': parts[0], 'supplements': parts[1:],
                                  'operation': 'growth_rate', 'current_year': current,
                                  'baseline_year': baseline, 'comparison': mode}
    task.element_evidence.append({'role': 'analysis_goal', 'source': question,
                                  'canonical': {'operation': 'growth_rate', 'metric_id': task.metric_id,
                                                'current_year': current, 'baseline_year': baseline,
                                                'comparison': mode}})
    return task


def require_comparison_plan(task):
    if task.intent not in {'sql', 'hybrid'}:
        return
    if requested_rate(task.raw_question) and (task.query_scope != 'business' or not task.comparison_requested or not task.comparison):
        raise ValueError('原问题要求增长率，但查询计划没有保留已确认的增长计算；不能仅列两年数据后标记完成')


def require_comparison_answer(answer, data):
    """A verified comparison must survive natural-language editing, including sign."""
    rows = (data or {}).get('rows', [])
    values = [r['growth_rate'] for r in rows if r.get('growth_rate') is not None]
    if not values:
        return
    compact = answer.replace(',', '')
    percentages = list(re.finditer(r'(?<![\d.])([+\-−]?\d+(?:\.\d+)?)\s*[%％]', compact))
    for value in values:
        if not isinstance(value, (int, float)) or not math.isfinite(value):
            raise ValueError('增长率结果不是有限数值')
        expected = round(value * 100, 2)
        matched = False
        for match in percentages:
            actual = float(match[1].replace('−', '-'))
            prefix = compact[max(0, match.start() - 18):match.start()]
            falling = bool(re.search(r'下降|减少|降低|下滑|回落|负增长', prefix))
            signed = -abs(actual) if actual >= 0 and falling else actual
            if math.isclose(signed, expected, abs_tol=.005, rel_tol=0):
                matched = True
                break
        if not matched:
            raise ValueError('自然回答遗漏或改变了实际增长率/正负方向，保留包含完整计算结果的已核验答案')
