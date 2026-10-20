"""Execute bounded plans with per-step status, dependencies and partial-result retention."""
import asyncio
import re
import time
from app.rag.retriever import retrieve_evidence
from app.schemas.response import AgentResponse
from app.schemas.evidence import Evidence
from app.sql.generator import generate_sql, UnsupportedQuery
from app.sql.validator import validate_query_contract
from app.sql.executor import execute_sql
from app.sql.results import describe_task, check_result, render_sql_answer
from app.workflow.rule_query import resolve_rule, check_schema, compile_rule_query, validate_rule_contract


async def execute_plan(request, run_id, trace, *, extra_chunks=None, session_id=None):
    # Runtime import keeps the standard orchestration helpers shared.
    from app.workflow.orchestrator import _answer_with_evidence, _document_evidence
    task = request.task
    steps = []
    response = AgentResponse(run_id=run_id, status="failed", message="", trace=trace,
                             query_conditions=describe_task(task), plan=steps)
    if request.kind == "net_income":
        response.query_conditions.update(metric="净收入", definition="待核对文档公式及已确认退款的业务映射", unit="元")
    elif request.kind == "eligible_customers":
        response.query_conditions.update(metric="客户资格对应销售额", group_by=["customer_id"], definition="待核对文档资格门槛，按CustomerId汇总")
    citations = {}

    def step(identifier, label, dependencies=(), **inputs):
        item = {"id": identifier, "label": label, "depends_on": list(dependencies),
                "status": "pending", "inputs": inputs}
        steps.append(item)
        return item

    def finish(item, status, detail):
        item.update(status=status, detail=detail)
        trace.append({"step": item["label"], "step_id": item["id"], "depends_on": item["depends_on"],
                      "status": status, "detail": detail, "duration_ms": round((time.perf_counter() - item.pop("_started", time.perf_counter())) * 1000, 2)})

    def begin(item):
        item["_started"] = time.perf_counter()

    def attach(answer, entity=None):
        mapping = {}
        for item in _document_evidence(answer, session_id=session_id, session_scoped=extra_chunks is not None):
            if item.evidence_id not in citations:
                citations[item.evidence_id] = len(citations) + 1
                item.metadata["citation"] = citations[item.evidence_id]
                response.evidence.append(item)
            mapping[next(i for i,c in enumerate(answer.chunks, 1) if c["chunk_id"] == item.evidence_id)] = citations[item.evidence_id]
        response.claims.extend({**c, "citation": mapping[c["citation"]], "entity": entity} for c in answer.claims)
        return re.sub(r"\[(\d+)\]", lambda m: f"[{mapping.get(int(m[1]), int(m[1]))}]", answer.message)

    def attach_sql(generated, data, step_id):
        response.sql, response.sql_params, response.data = generated.sql, generated.params, data
        response.evidence.insert(0, Evidence(evidence_id=f"sql-{run_id}", source_type="database",
            title="数据库查询结果", content=str(data["rows"]),
            metadata={"sql": generated.sql, "params": generated.params, "step_id": step_id}))

    if not any(e.get("step") == "选择处理路径" for e in trace):
        trace.append({"step": "选择处理路径", "status": "success", "detail": "hybrid", "duration_ms": 0})
    if request.kind == "entity_advice":
        query = step("query", "查询目标实体与指标", time_range=task.time_range, filters=task.filters)
        fanout = step("documents", "逐实体检索适用资料", ["query"], max_entities=10)
        merge = step("merge", "按实体整合结果", ["documents"])
    else:
        query = step("rules", "检索并核对计算规则", time_range=task.time_range, kind=request.kind)
        fanout = step("calculate", "编译并执行规则计算", ["rules"], filters=task.filters)
        merge = step("merge", "整合规则、数值与证据", ["calculate", "rules"])
    active = query
    try:
        begin(query)
        if request.kind == "entity_advice":
            generated = await generate_sql(task)
            validate_query_contract(generated.sql, generated.params, task)
            data, result_status = check_result(task, await asyncio.to_thread(execute_sql, generated.sql, generated.params))
            attach_sql(generated, data, query["id"])
            response.message = render_sql_answer(task, data)
            finish(query, result_status, f"只读查询与结果核验完成，返回{data['row_count']}行")
            begin(fanout)
            active = fanout
            entities = list(dict.fromkeys(r["BillingCountry"] for r in data["rows"] if r.get("BillingCountry") and r.get("matched_records")))
            requested = task.filters[0]["values"] if task.filters and not task.top_n else entities
            missing = [e for e in requested if e not in entities]
            if not entities or data.get("truncated") or len(entities) > 10:
                finish(fanout, "blocked", "目标实体为空或超出10个实体预算，未执行不完整的检索")
                response.status = "partial_answer" if entities else "insufficient_evidence"
                response.message += "\n实体集合未完整确认，文档步骤已停止。"
            else:
                outcomes, warnings, absent, conflicts = [], [], [], []
                for index, entity in enumerate([*entities, *missing], 1):
                    item = step(f"document-{index}", f"检索{entity}资料", ["query"], entity=entity, time_range=task.time_range)
                    begin(item)
                    active = item
                    if entity in missing:
                        finish(item, "blocked", "数据库没有该实体的匹配记录，未推断其经营结论")
                        outcomes.append({"entity": entity, "status": "insufficient_evidence"})
                        absent.append(f"{entity}：数据库匹配记录")
                        response.message += f"\n{entity}：无匹配数据，未检索经营建议。"
                        continue
                    answer = await _answer_with_evidence(f"{entity} 经营建议", trace, entity_ids=[entity], time_range=task.time_range,
                                                         extra_chunks=extra_chunks)
                    response.message += f"\n\n{entity}的文档依据：\n" + attach(answer, entity)
                    finish(item, answer.status, f"{len(answer.claims)}条核验摘录；实体和查询年度保持一致")
                    outcomes.append({"entity": entity, "status": answer.status, "evidence_ids": [c["chunk_id"] for c in answer.chunks]})
                    warnings.extend(f"{entity}：{w}" for w in answer.assessment.get("warnings", []))
                    absent.extend(f"{entity}：{p}" for p in answer.assessment.get("missing_aspects", []))
                    conflicts.extend(answer.assessment.get("conflicts", []))
                merge["depends_on"] = ["query", *[s["id"] for s in steps if s["id"].startswith("document-")]]
                statuses = [o["status"] for o in outcomes]
                response.status = ("conflicting_evidence" if "conflicting_evidence" in statuses else
                                   "completed" if result_status == "completed" and all(s == "completed" for s in statuses) else "partial_answer")
                response.evidence_assessment = {"retrieval_mode": "lexical", "entities": outcomes, "warnings": warnings,
                    "missing_aspects": absent, "conflicts": conflicts,
                    "document_status": statuses[0] if len(statuses) == 1 else response.status}
                finish(fanout, response.status, f"分别完成{len(outcomes)}个实体分支；未将某一实体资料套用于其他实体")
                if response.status != "completed":
                    response.message += "\n已保留确认的数据库结果和成功分支，其余部分尚缺证据或存在冲突。"
        else:
            topic = "退款" if request.kind == "net_income" else "优惠资格"
            entities = task.filters[0]["values"] if task.filters else None
            retrieval = await retrieve_evidence(topic, time_range=task.time_range, entity_ids=entities, extra_chunks=extra_chunks)
            rule, answer = resolve_rule(request.kind, retrieval)
            response.message = attach(answer)
            response.evidence_assessment = answer.assessment
            finish(query, answer.status, "规则来源核对完成；只允许已注册的计算结构")
            if rule is None:
                response.status = answer.status
                finish(fanout, "blocked", "依赖规则未确认，未生成或执行SQL")
            else:
                active = fanout
                begin(fanout)
                fanout["inputs"]["rule"] = rule
                check_schema(request.kind)
                response.evidence_assessment["schema_status"] = "verified"
                trace.append({"step": "核对业务映射与必需字段", "status": "success",
                              "detail": "Invoice字段及主键已核对" + ("；Refund按InvoiceId关联，Amount映射为已确认退款，按发票年度归属" if request.kind == "net_income" else "；按CustomerId分别汇总"), "duration_ms": 0})
                generated = compile_rule_query(task, rule)
                validate_rule_contract(generated.sql, generated.params, task, rule)
                if request.kind == "eligible_customers":
                    population_query = await generate_sql(task)
                    validate_query_contract(population_query.sql, population_query.params, task)
                    population = await asyncio.to_thread(execute_sql, population_query.sql, population_query.params)
                    trace.append({"step": "检查计算输入记录", "status": "success",
                                  "detail": f"当前范围匹配{population['rows'][0]['matched_records']}条发票",
                                  "sql": population_query.sql, "params": population_query.params,
                                  "duration_ms": population["duration_ms"]})
                    if not population["rows"][0]["matched_records"]:
                        response.status = "insufficient_evidence"
                        response.message += "\n当前范围内没有发票记录，不能判断客户资格。"
                        attach_sql(population_query, population, fanout["id"])
                        finish(fanout, "blocked", "没有可计算的原始记录")
                        finish(merge, response.status, "保留规则来源与空数据查询记录")
                        response.plan, response.trace = steps, trace
                        return response
                data = await asyncio.to_thread(execute_sql, generated.sql, generated.params)
                attach_sql(generated, data, fanout["id"])
                if any(row.get("invalid_values") for row in data["rows"]):
                    response.status = "insufficient_evidence"
                    response.message += "\n数据含缺失或非数值金额，计算结果不作为已确认结论。"
                elif data.get("truncated"):
                    response.status = "partial_answer"
                    response.message += "\n结果超过200行，已截断，请缩小范围；不能视为完整名单。"
                elif request.kind == "net_income":
                    row = data["rows"][0]
                    response.status = "completed" if row["matched_records"] and row["net_income"] is not None else "insufficient_evidence"
                    if response.status == "completed":
                        response.message += f"\n净收入={row['sales_amount']}－{row['refund_amount']}={row['net_income']}元。"
                        response.message += "\n按查询年度的发票归属统计，扣除其关联的全部已确认退款；不是按退款发生年度统计。"
                    else:
                        response.message += "\n没有匹配发票，不能将无数据解释为净收入为零。"
                else:
                    response.status = "completed"
                    response.query_conditions["definition"] = f"年度已开具发票销售额至少{rule['threshold']}元，按CustomerId分别判断"
                    response.message += f"\n资格门槛：年度销售额至少{rule['threshold']}元，按CustomerId分别判断。"
                    response.message += "\n" + ("\n".join(f"客户{r['CustomerId']}：{r['sales_amount']}元。" for r in data["rows"][:10]) or "当前范围内没有符合资格的客户。")
                if request.kind == "net_income":
                    response.query_conditions.update(metric="净收入", definition="按发票归属年度：销售额－关联的全部已确认退款", unit="元")
                finish(fanout, response.status, generated.explanation)
        begin(merge)
        finish(merge, response.status, "各子任务状态、SQL参数与文档引用已保留")
    except Exception as exc:
        status = "unsupported" if isinstance(exc, UnsupportedQuery) else "failed"
        finish(active, status, str(exc))
        for item in steps:
            if item["status"] == "pending":
                finish(item, "blocked", "依赖步骤未成功，未继续执行")
        response.status = "partial_answer" if response.evidence else status
        if isinstance(exc, UnsupportedQuery) and request.kind != "entity_advice":
            response.evidence_assessment.setdefault("missing_aspects", []).append(str(exc))
            response.evidence_assessment["schema_status"] = "blocked"
        response.message += f"\n后续步骤未完成：{exc}。已保留可核对的证据。"
    # Dependency order, not append time, determines the displayed plan order.
    response.plan = sorted(steps, key=lambda s: (2 if s["id"] == "merge" else 1 if s["id"].startswith("document-") else 0))
    response.trace = trace
    return response
