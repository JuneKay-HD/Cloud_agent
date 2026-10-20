"""Deterministic result checks and numeric answers for controlled queries."""
import math

from app.sql.schema_linker import load_sql_context

STATUS_LABELS = {
    "valid": "有效",
    "no_data": "无匹配数据",
    "missing_current": "本期无数据",
    "missing_baseline": "基期无数据",
    "missing_value": "统计值缺失",
    "zero_baseline": "基期为零，增长率不适用",
}


def describe_task(task) -> dict:
    if task.query_scope=='schema':
        p=task.schema_plan
        return {'query_scope':'schema','canonical_question':p['interpretation'],'source_id':task.source_id,
            'raw_question':task.raw_question,'normalized_question':task.normalized_question,
            'element_evidence':task.element_evidence,'unresolved_fragments':task.unresolved_fragments,
            'metric':'、'.join(s['label'] for s in p['selections']),'definition':p['interpretation'],
            'schema_plan':p,'countries':[],'unit':'','mode':'真实schema语义计划 + 受控只读SQL',
            'mode_reason':task.mode_reason,'inherited_fields':[]}
    metric = load_sql_context()["metrics"]["metrics"].get(task.metric_id, {})
    result = {
        "query_scope": task.query_scope,
        "canonical_question": task.canonical_question,
        "element_evidence": task.element_evidence,
        "unresolved_fragments": task.unresolved_fragments,
        "source_id": task.source_id,
        "metric": '、'.join(load_sql_context()['metrics']['metrics'][m]['name'] for m in task.metric_ids) if task.metric_ids else metric.get("name", task.metric_id or "未确定"),
        "invoice_amount": task.invoice_amount,
        "customer_ids": task.customer_ids,
        "unit": '' if task.metric_ids else metric.get("unit", ""),
        "amount_unit": load_sql_context()["metrics"]["metrics"].get("sales_amount", {}).get("unit", ""),
        "definition": '；'.join(load_sql_context()['metrics']['metrics'][m].get('description','') for m in task.metric_ids) if task.metric_ids else metric.get("description", ""),
        "countries": task.filters[0].get("values", []) if task.filters else [],
        "time_range": task.time_range,
        "baseline_time_range": task.baseline_time_range,
        "comparison": task.comparison,
        "group_by": task.dimensions,
        "top_n": task.top_n,
        "mode": "模型解析已核对 + 受控 SQL" if task.parsing_mode == "model_checked" else "规则解析 + 受控 SQL",
        "mode_reason": task.mode_reason,
        "inherited_fields": [field for field in task.inherited_fields if field != "comparison_requested"],
    }
    if task.query_scope == 'dataset':
        result['definition'] = '客户数量为Customer主表全部记录；发票数量为Invoice表全部记录，不限年份，不是某年成交客户数'
    return result


from app.workflow.progress import observed

@observed('核对数据库结果')
def check_result(task, result: dict) -> tuple[dict, str]:
    from app.workflow.comparison_contract import require_comparison_plan
    require_comparison_plan(task)
    if task.query_scope=='schema':
        labels={f'c{i}':s['label'] for i,s in enumerate(task.schema_plan['selections'])}
        if set(result['columns'])!=set(labels):raise ValueError('SQL结果列与语义计划不一致')
        for row in result['rows']:
            if any(isinstance(v,float) and not math.isfinite(v) for v in row.values()):raise ValueError('结果存在无效数值')
        valid=sum(all(v is not None for v in r.values()) for r in result['rows'])
        checked={**result,'column_labels':labels,'valid_row_count':valid,
                 'partial':bool(result['truncated']) or valid<len(result['rows'])}
        return checked,'completed' if valid and not checked['partial'] else 'insufficient_evidence'
    checked = {**result, "rows": [dict(row) for row in result["rows"]]}
    valid = 0
    for row in checked["rows"]:
        if task.comparison:
            required = {"current_value", "baseline_value", "growth_rate", "current_records", "baseline_records"}
            if not required.issubset(row):
                raise ValueError("比较结果缺少本期、基期或记录数")
            current, baseline = row["current_value"], row["baseline_value"]
            if not row["current_records"] and not row["baseline_records"]:
                status = "no_data"
            elif not row["current_records"]:
                status = "missing_current"
            elif not row["baseline_records"]:
                status = "missing_baseline"
            elif current is None or baseline is None:
                status = "missing_value"
            elif baseline == 0:
                status = "zero_baseline"
            else:
                status = "valid"
                expected = (current - baseline) / baseline
                if row["growth_rate"] is None or not math.isclose(row["growth_rate"], expected, rel_tol=1e-9, abs_tol=1e-12):
                    raise ValueError("增长率与本期、基期数值不一致")
            if status != "valid":
                row["growth_rate"] = None
            if not row["current_records"]:
                row["current_value"] = None
            if not row["baseline_records"]:
                row["baseline_value"] = None
        else:
            for m in task.metric_ids or [task.metric_id]:
                if m not in row or (row[m] is not None and not math.isfinite(row[m])):
                    raise ValueError('结果缺少指标或含无效数值')
            if task.metric_id not in row or "matched_records" not in row:
                raise ValueError("查询结果缺少指标或匹配记录数")
            status = "valid" if row["matched_records"] and row[task.metric_id] is not None else "no_data"
            if status == "no_data":
                for m in task.metric_ids or [task.metric_id]: row[m] = None
        row["result_status"] = STATUS_LABELS[status]
        valid += status == "valid"
    checked["columns"] = [*result["columns"], "result_status"]
    if task.comparison:
        checked["calculation"] = {"operation": "growth_rate",
            "formula": "(current_value - baseline_value) / baseline_value",
            "current_period": task.time_range, "baseline_period": task.baseline_time_range,
            "comparison": task.comparison, "independently_checked": True}
    checked["valid_row_count"] = valid
    checked["partial"] = valid < len(checked["rows"]) or bool(result.get("truncated"))
    status = "completed" if valid and not checked["partial"] else "insufficient_evidence"
    return checked, status


def _number(value) -> str:
    return "无数据" if value is None else f"{value:,.2f}".rstrip("0").rstrip(".")


def render_sql_answer(task, result: dict) -> str:
    if task.query_scope=='schema':
        if not result['rows']:return '已实际查询数据库，没有符合条件的记录；不能据此编造结果。'
        labels=result['column_labels']
        lines=['查询口径：'+task.schema_plan['interpretation']]
        for row in result['rows'][:10]:
            lines.append('；'.join(f'{labels[k]}：{_number(v) if isinstance(v,(int,float)) or v is None else v}' for k,v in row.items()))
        if len(result['rows'])>10:lines.append('完整返回记录请查看结果表格。')
        if result['truncated']:lines.append('结果超过200行，当前仅展示前200行，不能视为全部记录。')
        if result['partial'] and not result['truncated']:lines.append('部分统计值缺失，不能给出完整确定结论。')
        return '\n'.join(lines)
    conditions = describe_task(task)
    name, unit = conditions["metric"], conditions["unit"]
    rows = result["rows"]
    if not rows:
        return "查询已执行，但当前条件下没有匹配数据；不能将无数据视为零。"
    lines = []
    for row in rows[:10]:
        region = row.get("BillingCountry") or "、".join(conditions["countries"]) or "全部国家"
        if task.query_scope == 'dataset':
            region = '当前数据集全部记录'
        if task.comparison:
            rate = f"{row['growth_rate']:.2%}" if row["growth_rate"] is not None else row["result_status"]
            lines.append(
                f"{region}：本期{name}{_number(row['current_value'])}{unit if row['current_value'] is not None else ''}，"
                f"基期{_number(row['baseline_value'])}{unit if row['baseline_value'] is not None else ''}；"
                f"{'同比增长率' if task.comparison == 'year_over_year' else '增长率'}：{rate}。"
            )
        elif row["result_status"] != "有效":
            lines.append(f"{region}：当前条件下没有有效统计数据，不能视为零。")
        else:
            label = row.get('month') or (f"客户{row['CustomerId']}（{row.get('CustomerName','')}）" if 'CustomerId' in row else region)
            catalog = load_sql_context()['metrics']['metrics']
            values = '，'.join(f"{catalog[m]['name']}{_number(row[m])}{catalog[m].get('unit','')}" for m in (task.metric_ids or [task.metric_id]))
            lines.append(f"{label}：{values}。")
    if len(rows) > 10:
        lines.append("其余结果请查看下方表格。")
    if result.get("truncated"):
        lines.append("结果已截断，仅展示前200行，不能将其视为完整结果。")
    if task.comparison:
        lines.append("计算公式：（本期－基期）÷基期；基期为零或缺少数据时不计算增长率。")
    if task.dimensions == ['month']:
        lines.append('按月统计仅列有匹配记录的月份；未出现的月份不能据此认定为零。')
    if task.dimensions == ['customer_id'] and task.top_n:
        lines.append('按客户ID区分同名客户；金额并列时按客户ID升序取前N名。')
    return "\n".join(lines)
