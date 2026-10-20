"""Bounded dependency plans; unsupported conditions are never silently removed."""
import re
from dataclasses import dataclass

from app.schemas.task import QueryTask
from app.workflow.intent_parser import _fallback


@dataclass
class PlannedRequest:
    kind: str
    task: QueryTask
    question: str


def plan_request(question: str) -> PlannedRequest | None:
    q = question.strip()
    kind, numeric = None, None
    advice = re.search(r"(?:[，,\s]+|并且|并|然后|再)(?:(?:并且|并|然后|再))?(?:分别)?(?:总结|给出|提供)(?:该地区|该国家|各地区|各国家)?经营建议[。？?]*$", q)
    if advice:
        kind, numeric = "entity_advice", q[:advice.start()]
        if numeric.startswith(("比较", "对比")) and not re.search(r"同比|增长率|相比|相较", numeric):
            numeric = re.sub(r"^(比较|对比)", "查询", numeric) + "分别"
    elif re.match(r"根据退款规则计算", q) and "净收入" in q:
        kind = "net_income"
        numeric = re.sub(r"^根据退款规则计算", "查询", q).replace("净收入", "销售额")
    elif re.match(r"根据优惠资格规则找出", q) and "符合条件的客户" in q:
        kind = "eligible_customers"
        numeric = re.sub(r"^根据优惠资格规则找出", "查询", q)
        numeric = re.sub(r"符合条件的客户(?:及|和)销售额", "销售额", numeric)
    if kind is None:
        return None
    task = _fallback(numeric, {})
    task.intent = "hybrid"
    task.raw_question = q
    task.mode_reason = "受控跨源计划：保留年度/实体条件，按依赖顺序执行"
    if kind != 'entity_advice' and (task.invoice_amount or task.customer_ids or task.metric_ids):
        task.unsupported_reason = '当前公共库规则计算尚未支持额外金额、客户ID或多指标条件，未执行简化查询。'
        return PlannedRequest(kind, task, q)
    if kind == "entity_advice":
        if task.dimensions in [['month'], ['customer_id']]:
            task.unsupported_reason = '地区经营建议需按国家定位，不能把月度或客户分组替换成国家分组。'
            return PlannedRequest(kind, task, q)
        # Explicit entities need individual results, even without the word “分别”.
        if task.filters and not re.search(r"合计|总计", numeric):
            task.dimensions = ["billing_country"]
        if not task.dimensions:
            task.unsupported_reason = "跨源经营建议需要明确实体或国家分组，不能从总计结果猜测实体。"
        if task.top_n and task.top_n > 10:
            task.unsupported_reason = "当前跨源计划最多处理10个实体，请缩小排名范围。"
    elif task.dimensions or task.comparison or task.top_n:
        task.unsupported_reason = "规则计算当前仅支持指定年度及国家范围，不支持排名、同比或额外分组。"
    return PlannedRequest(kind, task, q)
