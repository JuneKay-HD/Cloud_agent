"""Keep user-supplied facets separate from an unconfirmed statistical measure.

This resolves an unfinished quantity request, not a SQL plan or a query result.
The resolved question still goes through normal schema and independent checks.
"""
from copy import deepcopy
from dataclasses import dataclass
import re

from app.schemas.task import QueryTask
from app.sql.schema_linker import load_sql_context

YEAR = re.compile(r'(?<!\d)(?:19|20|21)\d{2}(?!\d)')
YEAR_REFERENCE = re.compile(r'这一年|那一年|该年|当年|同一年|同年')
COUNTRY_REFERENCE = re.compile(r'这个国家|该国家|同一国家|那个国家')
KIND = 'unfinished_quantity'


def _country_aliases():
    return load_sql_context().get('entities', {}).get('entities', {}).get('billing_country', {}).get('value_aliases', {})


def _countries(text):
    from app.workflow.language import mentioned_countries
    return mentioned_countries(text, _country_aliases())


def _facets_only(text):
    """A closed grammar for a quantity with no named object, never discard unknowns."""
    text = YEAR.sub('', text)
    for name, aliases in _country_aliases().items():
        for alias in sorted([name, *aliases], key=len, reverse=True):
            pattern = re.escape(alias)
            if str(alias).isascii():
                pattern = r'(?<![A-Za-z])' + pattern + r'(?![A-Za-z])'
            text = re.sub(pattern, '', text, flags=re.I)
    text = YEAR_REFERENCE.sub('', text)
    text = COUNTRY_REFERENCE.sub('', text)
    words = ['某一年', '某年', '数据集中', '数据库中', '数据库', '数据集', 'Chinook',
             '查一下', '帮我', '我想', '请问', '查询', '统计', '看看', '多少', '数量', '总数',
             '一共', '总共', '共有', '只查', '只看', '改成', '改为', '换成', '继续',
             '刚才', '上面', '这次', '同上', '的', '是', '有', '年', '和', '与', '及', '呢', '查', '请']
    for word in sorted(words, key=len, reverse=True):
        text = re.sub(re.escape(word), '', text, flags=re.I)
    return not re.sub(r'[\s，。！？、：；,.!?;:（）()]+', '', text)


def _named_measure(text):
    definitions = load_sql_context().get('metrics', {}).get('metrics', {}).values()
    aliases = [a for d in definitions for a in [d['name'], *d.get('aliases', [])]]
    if any(a in text for a in aliases):
        return True
    return bool(re.search(r'数量|人数|多少|几张|几位|几条|去重|算一人|只算一', text) and not _facets_only(text))


def _year_matches(text):
    # A four-digit amount or identifier is not a calendar year. A bare facet
    # answer such as "2013，加拿大" can still fill the missing year.
    bare = _facets_only(text)
    return [m for m in YEAR.finditer(text) if bare or text[m.end():].startswith('年')
            or re.search(r'(?:年份|year)\s*[:：为]?\s*$', text[:m.start()], re.I)]


def _years(text):
    return list(dict.fromkeys(int(m[0]) for m in _year_matches(text)))


def initial_context(question, state=None):
    text = question.strip()
    bare = bool(re.search(r'数量|总数|多少', text) and _facets_only(text))
    missing_reference = bool(YEAR_REFERENCE.search(text) and not _years(text)
                             and _named_measure(text) and not (state or {}).get('last_task'))
    if not bare and not missing_reference:
        return None
    return {'kind': KIND, 'origin': text, 'request': text,
            'years': _years(text),
            'countries': _countries(text), 'measure_confirmed': not bare,
            'source': 'explicit_user_input'}


def pending_context(state):
    if state.get('cancelled_task') or not state.get('pending_message'):
        return None
    task = state.get('last_task') or {}
    if task.get('intent') not in {'sql', 'hybrid'}:
        return None
    saved = task.get('clarification_context') or {}
    if saved.get('kind') == KIND:
        return deepcopy(saved)
    # Compatibility with old pending tasks: do not inherit a model-suggested metric.
    pending = state['pending_message'].split('用户补充：', 1)[0].strip()
    context = initial_context(pending)
    if context and not context['years'] and YEAR_REFERENCE.search(pending):
        # Older routing may already have discarded the first task's slots. Recover
        # only across an uninterrupted SQL clarification chain, never across a
        # completed task, a chat topic or an arbitrary older year in history.
        messages = state.get('messages', [])
        matches = [i for i, m in enumerate(messages) if m.get('role') == 'user'
                   and m.get('text', '').strip() == pending]
        if matches:
            index, supplements = matches[-1], [pending]
            for _ in range(4):
                if index < 2:
                    break
                previous, reply = messages[index-2:index]
                result = reply.get('result') or {}
                if (previous.get('role') != 'user' or reply.get('role') != 'assistant'
                        or result.get('status') != 'needs_clarification'
                        or result.get('task_type') not in {'sql', 'hybrid'}):
                    break
                seed = initial_context(previous.get('text', ''))
                if seed is None:
                    break
                if seed['years']:
                    for supplement in reversed(supplements):
                        seed = _resolve(seed, supplement).context
                    return seed
                supplements.append(previous['text'])
                index -= 2
    return context


def is_quantity_followup(question, state):
    context = pending_context(state)
    if not context or len(question) > 500:
        return False
    if re.search(r'换个问题|另一个问题|重新查询|不要继续|不查了|取消|知识库|文档|附件|翻译|口径|定义|解释', question):
        return False
    if YEAR_REFERENCE.search(question) or COUNTRY_REFERENCE.search(question):
        return True
    if re.match(r'继续|改成|改为|换成|只查|只看', question):
        return True
    # A new self-contained country/year/measure request starts its own task.
    if _years(question) and _countries(question) and _named_measure(question):
        return False
    return _named_measure(question) or _facets_only(question)


@dataclass
class Resolution:
    question: str
    context: dict
    missing: list[str]
    ambiguities: list[str]


def _resolve(context, supplement=None):
    context = deepcopy(context)
    if supplement is not None:
        years = _years(supplement)
        countries = _countries(supplement)
        if years:
            context['years'] = years
        if countries:
            context['countries'] = countries
        if _named_measure(supplement):
            context['request'] = supplement
            context['measure_confirmed'] = True
        elif not _facets_only(supplement):
            # Keep additional qualifiers for semantic validation, never silently erase them.
            context['request'] += '\n' + supplement
        context['latest_supplement'] = supplement
    missing, ambiguities = [], []
    if not context['measure_confirmed']:
        missing.append('metric_id')
    if len(context['years']) != 1:
        missing.append('time_range')
        if len(context['years']) > 1:
            ambiguities.append('前文包含多个年份，请明确查哪一年，或说明分别统计、合计还是比较')
    request = context['request']
    if COUNTRY_REFERENCE.search(request) and len(context['countries']) != 1:
        ambiguities.append('请明确指代哪个国家，当前会话不能唯一确定')
    if len(context['years']) == 1:
        # This unfinished-count context has no comparison contract. Explicit edits replace
        # its prior year; the audit trail keeps the original rather than forcing both.
        year = str(context['years'][0])
        request = YEAR_REFERENCE.sub(year + '年', request)
        for match in reversed(_year_matches(request)):
            request = request[:match.start()] + year + request[match.end():]
    if len(context['countries']) == 1:
        request = COUNTRY_REFERENCE.sub(context['countries'][0], request)
    facets = []
    if len(context['years']) == 1 and not _years(request):
        facets.append(f"查询年份：{context['years'][0]}年")
    missing_countries = [c for c in context['countries'] if c not in _countries(request)]
    if missing_countries:
        facets.append('国家范围：' + '、'.join(missing_countries))
    resolved = request + ('\n会话中用户已明确的条件：' + '；'.join(facets) if facets else '')
    return Resolution(resolved, context, missing, ambiguities)


def resolve_quantity_followup(question, state):
    if not is_quantity_followup(question, state):
        return None
    return _resolve(pending_context(state), question)


def clarification_task(question, state):
    resolved = state.get('_quantity_resolution')
    if resolved is None:
        context = initial_context(question, state)
        if context is None:
            return None
        resolved = _resolve(context)
    if not resolved.missing and not resolved.ambiguities:
        return None
    years = resolved.context['years']
    return QueryTask(intent='sql', raw_question=resolved.context.get('latest_supplement', question),
                     time_range={'start': f'{years[0]}-01-01', 'end': f'{years[0]+1}-01-01'} if len(years) == 1 else None,
                     needs_clarification=True, missing_fields=resolved.missing,
                     ambiguities=resolved.ambiguities, clarification_context=resolved.context,
                     mode_reason='保留用户明确的会话条件；统计对象或唯一时间未明确，不推测指标、不执行SQL')
