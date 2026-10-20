"""Recognize explicit whole-dataset counts without discarding any conditions."""
import re
from app.schemas.task import QueryTask
from app.sql.schema_linker import load_sql_context


def dataset_count_followup(question, state):
    """Infer the count operation only from an active, unfiltered total-count task."""
    state = state or {}
    previous = state.get('last_task') or {}
    if state.get('cancelled_task') or state.get('last_route') not in (None, 'sql'):
        return None
    scope = previous.get('query_scope')
    plan = previous.get('schema_plan') or {}
    selections = plan.get('selections', [])
    unfiltered_count = (scope == 'schema' and not plan.get('filters') and not plan.get('joins')
        and len(selections) == 1 and selections[0].get('operation') == 'count'
        and selections[0].get('field') is None)
    if scope != 'dataset' and not unfiltered_count:
        return None
    match = re.fullmatch(r'(?:那|那么|再看|改成|换成)(客户|发票)(?:的)?(?:数量|总数|数)?(?:呢|是多少|有多少)?[？?。！!\s]*', question.strip())
    if not match:
        return None
    task = dataset_count_task('数据集中' + match[1] + '数量')
    task.raw_question = question
    task.inherited_fields = ['query_scope']
    task.mode_reason += '；明确切换统计对象，沿用前轮全量计数操作与范围'
    return task


def dataset_count_task(question):
    text = re.sub(r'[\s？?。！!，,、]', '', question.strip())
    text = re.sub(r'^(?:请问|请|帮我|帮我看看|查询|统计|告诉我)+', '', text)
    # A bare customer count still needs the business time/scope clarification.
    explicit = bool(re.search(r'数据集|数据库|库中|表中|表里|客户表|发票表|Customer表|Invoice表|总数|总数量|一共|总共有', text, re.I))
    if not explicit:
        return None
    text = re.sub(r'^(?:当前|这个|本项目的)?(?:数据集|数据库)(?:中|里|内)?(?:的)?', '', text)
    text = re.sub(r'(?:Customer表|客户表)(?:中|里|内)?', '客户', text, flags=re.I)
    text = re.sub(r'(?:Invoice表|发票表)(?:中|里|内)?', '发票', text, flags=re.I)
    remaining = re.sub(r'客户|发票', '', text)
    remaining = re.sub(r'总共有|一共有|总数量|总数|数量|多少|一共|全部|所有|记录|共有|分别|以及|和|与|有|的|是|个|位|张|条|人|数', '', remaining)
    # Unknown filters, years, writes, or metric definitions must use normal guards.
    if remaining or not re.search(r'客户|发票', text):
        return None
    metrics = []
    for match in re.finditer(r'客户|发票', text):
        metric = 'customer_count' if match[0] == '客户' else 'invoice_count'
        if metric not in metrics:
            metrics.append(metric)
    return QueryTask(intent='sql', query_scope='dataset', metric_id=metrics[0],
                     metric_ids=metrics if len(metrics)>1 else [], raw_question=question,
                     source_id=load_sql_context()['schema']['database'],
                     mode_reason='明确查询数据集全部记录；客户统计Customer主表，发票统计Invoice表，无年度过滤')
