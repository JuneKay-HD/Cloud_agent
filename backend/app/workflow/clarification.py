"""Detect missing or ambiguous conditions and build clarification prompts."""

from app.schemas.task import QueryTask

FIELD_LABELS = {
    "time_range": "查询时间范围",
    "metric_id": "需要统计的指标",
    "comparison": "比较方式（年度同比或指定本期、基期）",
    "baseline_time_range": "基期年份",
}


from app.workflow.progress import observed

@observed('准备必要澄清')
def build_clarification(task: QueryTask) -> tuple[str, dict[str, list[str]]]:
    labels = [FIELD_LABELS.get(field, field) for field in task.missing_fields]
    if task.ambiguities:
        labels.extend(task.ambiguities)
    needed = "、".join(labels) or "必要条件"
    options: dict[str, list[str]] = {}
    if (task.clarification_context or {}).get('kind') == 'unfinished_quantity':
        if 'metric_id' in task.missing_fields:
            options['metric_id'] = ['有交易的客户人数，按客户去重', '发票数量']
            prompt = '您想统计客户人数、发票数量，还是其他对象的数量？'
            if 'time_range' in task.missing_fields:
                prompt += '还请明确具体年份或时间范围。'
            return prompt, options
        if 'time_range' in task.missing_fields:
            return '统计对象已记录。请明确具体年份或时间范围；“这一年”在当前会话中没有唯一对应的年份。', options
        if task.ambiguities:
            return '还需要确认：' + '；'.join(task.ambiguities), options
    if task.suggested_question:
        return "请核对模型理解；如有遗漏请直接补充条件：" + task.suggested_question, {"确认查询条件": [task.suggested_question]}
    if "metric_id" in task.missing_fields:
        options["metric_id"] = ["销售额", "发票数量", "客户数量"]
    if "comparison" in task.missing_fields:
        options["comparison"] = ["同比", "2012年相比2011年"]
    if not task.ambiguities and task.missing_fields == ['time_range']:
        metric = {'sales_amount':'销售额', 'invoice_count':'发票数量', 'customer_count':'有交易的客户数量'}.get(task.metric_id, '该指标')
        return f"您想查哪一年或哪个时间范围的{metric}？", options
    if not task.ambiguities and task.missing_fields == ['metric_id']:
        return "您想统计销售额、发票数量，还是客户数量？", options
    return f"还需要确认：{needed}。请补充这些条件，我再继续查询。", options
