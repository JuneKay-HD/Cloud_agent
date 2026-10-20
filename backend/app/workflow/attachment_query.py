"""Explicit customer-ID lists and invoice amount rules, never document instructions."""
import asyncio
import re
from uuid import uuid4

from app.schemas.response import AgentResponse
from app.schemas.evidence import Evidence
from app.storage.sessions import update_session
from app.workflow.intent_parser import parse_intent
from app.sql.generator import compile_query
from app.sql.validator import validate_query_contract
from app.sql.executor import execute_sql
from app.sql.results import check_result, describe_task, render_sql_answer


from app.workflow.progress import observed

@observed('读取附件正文与检索')
async def query_attachment(session_id, question, state, ids):
    run_id = str(uuid4())
    response = AgentResponse(run_id=run_id, task_type='hybrid', status='needs_clarification',
        message='请提供明确客户ID名单或单笔发票金额规则。支持每行“客户ID：1”或“单笔发票金额至少100元”；无法唯一映射的姓名、复杂规则不会自动执行。')
    documents = [d for d in state.get('documents',[]) if d['id'] in ids]
    customers, amounts, evidence = set(), set(), []
    for doc in documents:
        for chunk in doc['chunks']:
            for line in chunk['text'].splitlines():
                line = line.strip()
                if not line: continue
                m = re.fullmatch(r'(?:客户ID|CustomerId)\s*[：:]\s*([1-9]\d*)[。]?',line,re.I)
                a = re.fullmatch(r'单笔发票金额(至少|超过|不超过|小于)(\d+(?:\.\d+)?)元[。]?',line)
                if m: customers.add(int(m[1]))
                elif a: amounts.add(({'至少':'>=','超过':'>','不超过':'<=','小于':'<'}[a[1]],float(a[2])))
                else:
                    response.message += '\n附件包含尚不能可靠映射的内容，请明确适用规则或整理为上述格式。'
                    return response
            evidence.append(Evidence(evidence_id=chunk['chunk_id'],source_type='document',title=doc['name'],content=chunk['text'],metadata={
                'source_url':f"/api/sessions/{session_id}/documents/{doc['id']}/source?chunk_id={chunk['chunk_id']}",
                'chunk_id':chunk['chunk_id'],'source_hash':chunk.get('source_hash'),'page_no':chunk.get('page_no')}))
    if not customers and not amounts: return response
    if len(customers)>200 or len(amounts)>1:
        response.message='客户名单超过200个，或存在多条金额规则；请缩小范围或明确唯一规则。'
        return response
    # Remove a source selector, never unknown query constraints.
    q = re.sub(r'^(?:请)?(?:根据|按照|依据)(?:上传的)?附件(?:中的|中|的)?(?:客户名单|名单|规则)?[，,：:\s]*','',question)
    task = await parse_intent(q, {})
    if task.unsupported_reason or task.needs_clarification or task.intent!='sql':
        response.message=task.unsupported_reason or '请补充完整查询指标和年份，例如：根据附件客户名单，查询2012年销售额。'
        return response
    if task.customer_ids or (task.invoice_amount and amounts):
        response.message='问题和附件同时指定筛选条件，请先明确以哪一个条件为准。'
        return response
    task.customer_ids = sorted(customers)
    if amounts:
        op,value=next(iter(amounts)); task.invoice_amount={'operator':op,'value':value}
    if customers:
        placeholders=', '.join(f':id{n}' for n in range(len(customers)))
        lookup=f'SELECT CustomerId FROM Customer WHERE CustomerId IN ({placeholders})'
        from app.sql.validator import validate_sql
        validate_sql(lookup)
        present=await asyncio.to_thread(execute_sql, lookup, {f'id{n}':v for n,v in enumerate(sorted(customers))})
        missing=customers-{r['CustomerId'] for r in present['rows']}
        if missing:
            response.message=f'附件客户ID在数据库中不存在：{sorted(missing)}。请核对，未执行汇总。'
            return response
    try:
        sql=compile_query(task); validate_query_contract(sql.sql,sql.params,task)
        data,status=check_result(task,await asyncio.to_thread(execute_sql, sql.sql, sql.params))
    except ValueError as exc:
        response.status='unsupported'; response.message=str(exc); return response
    evidence.append(Evidence(evidence_id='sql-'+run_id,source_type='database',title='只读查询结果',content=str(data['rows']),metadata={'sql':sql.sql,'params':sql.params}))
    response=AgentResponse(run_id=run_id,task_type='hybrid',status=status,
        message='已将附件中的客户ID/单笔发票金额条件映射到业务表，按这些条件查询：\n'+render_sql_answer(task,data),
        data=data,sql=sql.sql,sql_params=sql.params,evidence=evidence,query_conditions=describe_task(task),
        plan=[{'id':'attachment_conditions','label':'提取附件明确条件','depends_on':[],'status':'completed','inputs':{'document_ids':ids}},
              {'id':'database_query','label':'按客户ID和金额条件查询业务表','depends_on':['attachment_conditions'],'status':status,'inputs':{'sql':sql.sql,'params':sql.params}}],
        trace=[{'step':'附件条件映射','status':'success','detail':f'客户ID：{task.customer_ids}；单笔金额：{task.invoice_amount}；仅使用明确字段','duration_ms':0},
               {'step':'SQL条件核对与只读执行','status':status,'detail':'已核对受控SQL结构、全部参数和结果；附件正文未作为工具指令','duration_ms':data['duration_ms']}])
    # Do not silently reuse file filters in an ordinary later SQL query.
    update_session(session_id,last_task=None,pending_message=None,last_document_context={'question':question,'attachment_ids':ids})
    return response
