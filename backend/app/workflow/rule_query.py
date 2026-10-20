"""Two explicitly supported document rules, compiled to parameterized read-only SQL."""
import re
import json
import sqlite3
from decimal import Decimal

from sqlglot import parse
from app.config import get_settings
from app.rag.answerer import GroundedAnswer, materialize, sentences
from app.rag.indexer import read_serving_index as get_local_index
from app.sql.generator import GeneratedSQL, UnsupportedQuery, validate_task
from app.sql.validator import validate_sql


def resolve_rule(kind, retrieval):
    if retrieval.knowledge_status != "ready":
        return None, GroundedAnswer("knowledge_unavailable" if not retrieval.chunks else "partial_answer",
                                   "规则资料不可用或未完整检查，已停止后续计算。",
                                   assessment={"warnings": retrieval.warnings, "missing_aspects": ["完整规则"], "conflicts": []})
    # Inspect every paragraph of each selected rule source in the same scope;
    # a low-scoring exception must not disappear merely because retrieval omitted it.
    scopes = {(c["doc_id"], json.dumps(c.get("source_metadata", {}), sort_keys=True)) for c in retrieval.chunks}
    corpus = retrieval.corpus_chunks if retrieval.corpus_chunks is not None else get_local_index()["chunks"]
    expanded = [c for c in corpus if (c["doc_id"], json.dumps(c.get("source_metadata", {}), sort_keys=True)) in scopes]
    if len(expanded) > 64:
        return None, GroundedAnswer("partial_answer", "规则来源超过64个片段检查预算，已停止计算。", assessment={"warnings": ["规则检查未完成"]})
    retrieval.chunks = expanded
    found, selected, unsupported = [], [], []
    unsupported_claims = []
    for chunk in retrieval.chunks:
        for claim in sentences(chunk):
            quote = claim["quote"].strip()
            if kind == "net_income" and "净收入" in quote:
                match = re.fullmatch(r"净收入等于销售额减去已确认退款金额[。；]?", quote)
                if match:
                    found.append(("net_income", None))
                    selected.append(claim)
                else:
                    unsupported.append(quote)
                    unsupported_claims.append(claim)
            elif kind == "eligible_customers" and "优惠资格" in quote:
                match = re.fullmatch(r"优惠资格要求同一年度已开具发票销售额至少(\d+(?:\.\d{1,2})?)元[，,]按CustomerId汇总[。]?", quote)
                if match and Decimal(match[1]) <= Decimal("1000000000000"):
                    found.append(("eligible_customers", str(Decimal(match[1]))))
                    selected.append(claim)
                else:
                    unsupported.append(quote)
                    unsupported_claims.append(claim)
            elif re.match(r"但|仅|除非|例外|同时|还需|需要|需扣|应扣|另外|其中", quote):
                unsupported.append(quote)
                unsupported_claims.append(claim)
            elif kind == "eligible_customers" and quote == "同名客户必须分别判断。":
                selected.append(claim)
            elif kind == "eligible_customers" and not re.fullmatch(r"(?:版本[\w.-]+|适用年度?\d{4}年?|实体标识\w+)[。；]?", quote):
                unsupported.append(quote)
                unsupported_claims.append(claim)
    assessment = {"retrieval_mode": retrieval.mode, "warnings": list(retrieval.warnings),
                  "missing_aspects": [], "conflicts": [], "rule_kind": kind,
                  "rejected": retrieval.rejected}
    if unsupported or not found:
        code = "unsupported_rule_condition" if unsupported else "missing_applicable_rule"
        needed = ("规则存在未支持的条款，需要明确其计算方式" if unsupported else
                  "缺少覆盖查询实体和年度的明确计算公式或资格门槛")
        assessment.update(rule_issue=code, missing_aspects=[needed])
        cited, claims = materialize(selected + unsupported_claims, retrieval.chunks)
        quotes = "\n".join(f"“{c['quote']}”[{c['citation']}]" for c in claims)
        message = needed + "，未生成或执行计算SQL。请补充相应规则或修改查询范围。"
        if quotes:
            message += "\n已找到但尚不足以完成计算的原文：\n" + quotes
        return None, GroundedAnswer("insufficient_evidence", message, cited, claims, assessment)
    cited, claims = materialize(selected, retrieval.chunks)
    if len(set(found)) != 1:
        assessment["conflicts"] = [{"key": "计算规则", "values": [str(v[1]) for v in set(found)]}]
        assessment["rule_issue"] = "conflicting_rules"
        return None, GroundedAnswer("conflicting_evidence", "适用规则的金额门槛冲突，已停止计算。", cited, claims, assessment)
    rule = {"kind": kind, "threshold": found[0][1]}
    assessment["rule_contract"] = rule
    return rule, GroundedAnswer("completed", "已核对规则：\n" + "\n".join(f"“{c['quote']}”[{c['citation']}]" for c in claims),
                                cited, claims, assessment)


def check_schema(kind):
    if kind == "net_income":
        path = get_settings().config_path / "rule_bindings.json"
        contract = json.loads(path.read_text(encoding="utf-8")).get("net_income", {}) if path.exists() else {}
        if contract != {"refund_amount": "confirmed_only", "period_basis": "invoice_date", "refund_time_scope": "all_linked"}:
            raise UnsupportedQuery("缺少已确认退款及发票归属年度的业务映射，不能仅凭列名计算净收入。")
    required = {"Invoice": {"InvoiceId", "CustomerId", "InvoiceDate", "BillingCountry", "Total"}}
    if kind == "net_income":
        required["Refund"] = {"InvoiceId", "Amount"}
    with sqlite3.connect(f"file:{get_settings().database_path.as_posix()}?mode=ro", uri=True) as conn:
        for table, fields in required.items():
            rows = conn.execute(f'PRAGMA table_info("{table}")').fetchall()
            if not rows:
                raise UnsupportedQuery(f"当前数据库缺少{table}表，请提供该事实表及字段{', '.join(sorted(fields))}；不能执行该规则计算。")
            missing = fields - {r[1] for r in rows}
            if missing:
                raise UnsupportedQuery(f"当前数据库{table}表缺少字段{', '.join(sorted(missing))}，请补充字段及业务含义；不能执行该规则计算。")
            if table == "Invoice" and [r[1] for r in rows if r[5]] != ["InvoiceId"]:
                raise UnsupportedQuery("InvoiceId必须是唯一主键，无法确认关联基数。")


def compile_rule_query(task, rule):
    validate_task(task)
    if task.comparison or task.dimensions or task.top_n:
        raise UnsupportedQuery("规则计算不支持额外排名、比较或分组")
    params = {"start": task.time_range["start"], "end": task.time_range["end"]}
    where = "i.InvoiceDate >= :start AND i.InvoiceDate < :end"
    if task.filters:
        names = []
        for n, country in enumerate(sorted(set(task.filters[0]["values"]))):
            key = f"country_{n}"
            params[key] = country
            names.append(":" + key)
        where += " AND i.BillingCountry IN (" + ", ".join(names) + ")"
    invalid = "CASE WHEN typeof(i.Total) NOT IN ('integer','real') OR i.Total < 0 THEN 1 ELSE 0 END"
    if rule["kind"] == "net_income" and rule.get("threshold") is None:
        sql = (
            "WITH refunds AS (SELECT InvoiceId, SUM(Amount) AS refund_amount, "
            "SUM(CASE WHEN typeof(Amount) NOT IN ('integer','real') OR Amount < 0 THEN 1 ELSE 0 END) AS invalid "
            "FROM Refund GROUP BY InvoiceId) "
            "SELECT ROUND(SUM(i.Total), 2) AS sales_amount, "
            "ROUND(SUM(COALESCE(r.refund_amount, 0)), 2) AS refund_amount, "
            "ROUND(SUM(i.Total - COALESCE(r.refund_amount, 0)), 2) AS net_income, "
            f"COUNT(*) AS matched_records, SUM({invalid} + COALESCE(r.invalid, 0)) AS invalid_values "
            "FROM Invoice i LEFT JOIN refunds r ON i.InvoiceId = r.InvoiceId WHERE " + where
        )
    elif rule["kind"] == "eligible_customers":
        threshold = Decimal(rule["threshold"])
        if not threshold.is_finite() or not 0 <= threshold <= Decimal("1000000000000"):
            raise UnsupportedQuery("规则门槛数值无效")
        params["threshold"] = float(threshold)
        sql = (
            "WITH totals AS (SELECT i.CustomerId, ROUND(SUM(i.Total), 2) AS sales_amount, "
            f"COUNT(*) AS matched_records, SUM({invalid}) AS invalid_values "
            f"FROM Invoice i WHERE {where} GROUP BY i.CustomerId) "
            "SELECT CustomerId, sales_amount, matched_records, invalid_values FROM totals "
            "WHERE sales_amount >= :threshold OR invalid_values > 0 ORDER BY CustomerId"
        )
    else:
        raise UnsupportedQuery("未注册的计算规则")
    return GeneratedSQL(sql, params, "文档规则编译：绑定年度/实体/门槛；退款先按发票聚合，资格按CustomerId汇总")


def validate_rule_contract(sql, params, task, rule):
    validate_sql(sql, allowed_tables={"invoice", "refund"} if rule["kind"] == "net_income" else {"invoice"})
    expected = compile_rule_query(task, rule)
    if parse(sql, read="sqlite")[0] != parse(expected.sql, read="sqlite")[0] or params != expected.params:
        raise ValueError("计算SQL或参数与已确认规则不一致")
