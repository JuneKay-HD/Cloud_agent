"""Compile only supported tasks; every condition is parameterized and consumed."""
from dataclasses import dataclass, field
from datetime import date
from typing import Any

from app.schemas.task import QueryTask
from app.sql.schema_linker import load_sql_context


class UnsupportedQuery(ValueError):
    pass


@dataclass
class GeneratedSQL:
    sql: str
    params: dict[str, Any] = field(default_factory=dict)
    explanation: str = "受控 SQL：指标、国家、时间、分组和比较条件均由查询任务编译"
    mode: str = "controlled"


def validate_task(task: QueryTask) -> None:
    from app.workflow.comparison_contract import require_comparison_plan
    try:
        require_comparison_plan(task)
    except ValueError as exc:
        raise UnsupportedQuery(str(exc)) from None
    if task.unsupported_reason:
        raise UnsupportedQuery(task.unsupported_reason)
    if task.needs_clarification or task.missing_fields or task.ambiguities:
        raise UnsupportedQuery("查询条件尚未明确，不能执行")
    if task.intent not in {"sql", "hybrid"}:
        raise UnsupportedQuery("当前任务不是数据库查询")
    if task.query_scope == 'schema':
        if (task.intent!='sql' or not task.schema_plan or task.unresolved_fragments
                or task.filters or task.dimensions or task.time_range or task.baseline_time_range
                or task.comparison or task.comparison_requested or task.top_n is not None
                or task.invoice_amount is not None or task.customer_ids or task.metric_ids):
            raise UnsupportedQuery('语义计划不完整，未执行查询')
        from app.sql.schema_query import compile_plan
        compile_plan(task.schema_plan)
        return
    if task.query_scope == 'dataset':
        metrics = task.metric_ids or [task.metric_id]
        if (task.intent != 'sql' or any(m not in {'customer_count','invoice_count'} for m in metrics)
                or len(set(metrics)) != len(metrics) or task.metric_id != metrics[0]
                or task.filters or task.dimensions or task.time_range or task.baseline_time_range
                or task.comparison or task.comparison_requested or task.top_n is not None
                or task.invoice_amount is not None or task.customer_ids or task.unresolved_fragments):
            raise UnsupportedQuery('整体数量统计不能隐含或丢弃筛选、时间、比较及排名条件')
        return
    if task.metric_id not in {"sales_amount", "invoice_count", "customer_count"}:
        raise UnsupportedQuery("不支持该指标，不能自动改为销售额")
    if task.dimensions not in ([], ["billing_country"], ["month"], ["customer_id"]):
        raise UnsupportedQuery("不支持的分组维度")
    if any(m not in {'sales_amount','invoice_count','customer_count'} for m in task.metric_ids):
        raise UnsupportedQuery('多指标包含未支持的指标')
    if task.metric_ids and (len(set(task.metric_ids)) != len(task.metric_ids) or task.metric_id != task.metric_ids[0]):
        raise UnsupportedQuery('多指标顺序或内容不一致')
    if task.metric_ids and task.top_n:
        raise UnsupportedQuery('多指标排名需明确排序指标；请先按一个指标排名')
    if task.invoice_amount is not None:
        import math
        a = task.invoice_amount
        if set(a) != {'operator','value'} or a['operator'] not in {'>','>=','<','<='} or type(a['value']) not in {int,float} or not math.isfinite(a['value']) or a['value'] < 0:
            raise UnsupportedQuery('发票金额条件无效')
    if len(task.customer_ids) > 200 or any(type(i) is not int or i <= 0 for i in task.customer_ids):
        raise UnsupportedQuery('客户ID必须为正整数且最多200个')
    if task.comparison and (task.metric_ids or task.invoice_amount or task.customer_ids or task.dimensions in [['month'],['customer_id']]):
        raise UnsupportedQuery('扩展查询暂不支持同时进行年度比较')
    if task.top_n is not None and (not 1 <= task.top_n <= 200 or not task.dimensions):
        raise UnsupportedQuery("排名需要国家分组，数量须在1至200之间")
    if task.comparison not in {None, "year_over_year", "period_over_period"}:
        raise UnsupportedQuery("当前仅支持年度同比或明确的两个年度比较")
    if task.comparison and (task.top_n or task.intent == "hybrid"):
        raise UnsupportedQuery("当前不支持比较结果排名或比较后的跨源查询")
    if task.comparison_requested and not task.comparison:
        raise UnsupportedQuery("已请求增长率但比较方式尚未明确")
    if task.baseline_time_range and not task.comparison:
        raise UnsupportedQuery("存在基期但未指定比较方式")
    periods = [task.time_range]
    if task.comparison:
        periods.append(task.baseline_time_range)
    for period in periods:
        if not period or set(period) != {"start", "end"}:
            raise UnsupportedQuery("查询需要完整时间区间")
        try:
            start, end = date.fromisoformat(period["start"]), date.fromisoformat(period["end"])
        except (ValueError, TypeError):
            raise UnsupportedQuery("时间格式必须是 YYYY-MM-DD") from None
        if start >= end or (start.month, start.day, end.month, end.day) != (1, 1, 1, 1):
            raise UnsupportedQuery("当前仅支持有效的整年时间区间")
        if task.comparison and end.year != start.year + 1:
            raise UnsupportedQuery("年度比较的本期和基期必须各为一个完整年度")
    if task.comparison == "year_over_year":
        if int(task.baseline_time_range["start"][:4]) != int(task.time_range["start"][:4]) - 1:
            raise UnsupportedQuery("年度同比基期必须为上一年")
    if task.comparison and task.time_range == task.baseline_time_range:
        raise UnsupportedQuery("本期与基期不能相同")
    countries = load_sql_context()["entities"]["entities"]["billing_country"]["value_aliases"]
    if len(task.filters) > 1:
        raise UnsupportedQuery("当前只支持一个国家集合过滤条件")
    for condition in task.filters:
        if set(condition) != {"field", "operator", "values"}:
            raise UnsupportedQuery("过滤条件结构不完整或包含未知条件")
        values = condition["values"]
        if (condition["field"] != "billing_country" or condition["operator"] != "in"
                or not isinstance(values, list) or not values
                or any(not isinstance(v, str) or v not in countries for v in values)):
            raise UnsupportedQuery("不支持该字段、过滤操作或国家，未执行查询")


def compile_query(task: QueryTask) -> GeneratedSQL:
    validate_task(task)
    if task.query_scope == 'schema':
        from app.sql.schema_query import compile_plan
        sql,params,plan=compile_plan(task.schema_plan)
        return GeneratedSQL(sql,params,'基于真实schema的受控SQL：'+plan.interpretation)
    if task.query_scope == 'dataset':
        tables = {'customer_count':'Customer','invoice_count':'Invoice'}
        columns = [f'(SELECT COUNT(*) FROM {tables[m]}) AS {m}' for m in (task.metric_ids or [task.metric_id])]
        return GeneratedSQL('SELECT '+', '.join(columns)+', 1 AS matched_records',
                            explanation='统计真实数据集全部记录：客户定位Customer主表，发票定位Invoice表；无需年度条件')
    if task.metric_ids or task.invoice_amount or task.customer_ids or task.dimensions in [['month'],['customer_id']]:
        return compile_extended(task)
    params: dict[str, Any] = {"start": task.time_range["start"], "end": task.time_range["end"]}
    country_sql = ""
    if task.filters:
        values = sorted(set(task.filters[0]["values"]))
        placeholders = []
        for index, value in enumerate(values):
            key = f"country_{index}"
            params[key] = value
            placeholders.append(f":{key}")
        country_sql = " AND BillingCountry IN (" + ", ".join(placeholders) + ")"
    grouped = bool(task.dimensions)
    select_group = "BillingCountry, " if grouped else ""
    group_by = " GROUP BY BillingCountry" if grouped else ""
    metric = task.metric_id

    if task.comparison:
        params.update(baseline_start=task.baseline_time_range["start"],
                      baseline_end=task.baseline_time_range["end"])
        current = "InvoiceDate >= :start AND InvoiceDate < :end"
        baseline = "InvoiceDate >= :baseline_start AND InvoiceDate < :baseline_end"

        def aggregate(predicate: str) -> str:
            if metric == "sales_amount":
                return f"ROUND(SUM(CASE WHEN {predicate} THEN Total END), 2)"
            if metric == "invoice_count":
                return f"COUNT(CASE WHEN {predicate} THEN InvoiceId END)"
            return f"COUNT(DISTINCT CASE WHEN {predicate} THEN CustomerId END)"

        sql = (
            f"WITH totals AS (SELECT {select_group}"
            f"{aggregate(current)} AS current_value, "
            f"{aggregate(baseline)} AS baseline_value, "
            f"COUNT(CASE WHEN {current} THEN 1 END) AS current_records, "
            f"COUNT(CASE WHEN {baseline} THEN 1 END) AS baseline_records "
            f"FROM Invoice WHERE (({current}) OR ({baseline})){country_sql}{group_by}) "
            f"SELECT {select_group}current_value, baseline_value, "
            "CASE WHEN current_records > 0 AND baseline_records > 0 AND baseline_value != 0 "
            "THEN 1.0 * (current_value - baseline_value) / baseline_value ELSE NULL END AS growth_rate, "
            "current_records, baseline_records FROM totals"
        )
        if grouped:
            sql += " ORDER BY BillingCountry"
    else:
        expressions = {
            "sales_amount": "ROUND(SUM(Total), 2)",
            "invoice_count": "COUNT(InvoiceId)",
            "customer_count": "COUNT(DISTINCT CustomerId)",
        }
        sql = (
            f"SELECT {select_group}{expressions[metric]} AS {metric}, COUNT(*) AS matched_records "
            f"FROM Invoice WHERE InvoiceDate >= :start AND InvoiceDate < :end{country_sql}{group_by}"
        )
        if grouped:
            sql += f" ORDER BY {metric} DESC, BillingCountry ASC"
        if task.top_n is not None:
            sql += " LIMIT :limit"
            params["limit"] = task.top_n
    return GeneratedSQL(sql=sql, params=params)


def compile_extended(task: QueryTask) -> GeneratedSQL:
    """Only fixed expressions, identifiers and joins; all user values are bound."""
    validate_task(task)
    params = {'start':task.time_range['start'], 'end':task.time_range['end']}
    where = ['i.InvoiceDate >= :start', 'i.InvoiceDate < :end']
    if task.filters:
        values = sorted(set(task.filters[0]['values']))
        for n,v in enumerate(values): params[f'country_{n}'] = v
        where.append('i.BillingCountry IN (' + ', '.join(f':country_{n}' for n in range(len(values))) + ')')
    if task.invoice_amount:
        params['amount'] = task.invoice_amount['value']
        where.append('i.Total ' + task.invoice_amount['operator'] + ' :amount')
    if task.customer_ids:
        for n,v in enumerate(sorted(set(task.customer_ids))): params[f'customer_{n}'] = v
        where.append('i.CustomerId IN (' + ', '.join(f':customer_{n}' for n in range(len(set(task.customer_ids)))) + ')')
    dimensions = {
        'month': ("strftime('%Y-%m', i.InvoiceDate) AS month", "strftime('%Y-%m', i.InvoiceDate)", 'month'),
        'billing_country': ('i.BillingCountry','i.BillingCountry','BillingCountry'),
        'customer_id': ("i.CustomerId AS CustomerId, COALESCE(c.FirstName, '') || ' ' || COALESCE(c.LastName, '') AS CustomerName", 'i.CustomerId, c.FirstName, c.LastName','i.CustomerId'),
    }
    expr = {'sales_amount':'ROUND(SUM(i.Total), 2)', 'invoice_count':'COUNT(i.InvoiceId)', 'customer_count':'COUNT(DISTINCT i.CustomerId)'}
    metrics = task.metric_ids or [task.metric_id]
    select = [f'{expr[m]} AS {m}' for m in metrics] + ['COUNT(*) AS matched_records']
    source = 'Invoice i'
    if task.dimensions == ['customer_id']:
        source += ' LEFT JOIN Customer c ON c.CustomerId = i.CustomerId'
    if task.dimensions:
        group, keys, tie = dimensions[task.dimensions[0]]
        select.insert(0, group)
    sql = 'SELECT ' + ', '.join(select) + ' FROM ' + source + ' WHERE ' + ' AND '.join(where)
    if task.dimensions:
        sql += ' GROUP BY ' + keys
        sql += ' ORDER BY ' + (tie if task.dimensions == ['month'] and not task.top_n else metrics[0] + ' DESC, ' + tie)
    if task.top_n:
        sql += ' LIMIT :limit'
        params['limit'] = task.top_n
    return GeneratedSQL(sql, params, '扩展受控SQL：逐项消费指标、时间粒度、金额、客户ID及国家条件')


def _fallback(task: QueryTask) -> GeneratedSQL:
    """Compatibility entry point; unsupported conditions now fail closed."""
    return compile_query(task)


async def generate_sql(task: QueryTask) -> GeneratedSQL:
    # The model extracts intent; executable SQL is compiled from a bounded contract.
    # Arbitrary model SQL cannot bypass conditions through a different query shape.
    return compile_query(task)
