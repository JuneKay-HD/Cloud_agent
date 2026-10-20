"""History changes wording; fresh tools determine facts and repeat consistency."""
import hashlib
import json
import re
from pathlib import Path

from app.config import get_settings
from app.workflow.language import normalize_question
from app.workflow.understanding import inspect_language
from app.sql.schema_linker import load_sql_context

SHORT_REQUERY=re.compile(r'^(?:请|请您|帮我)?(?:再查一次|再查一遍|重新查询|再次查询|再查询一次|重新查一下|再查一下)[。！？!\s]*$')

def _hash(value):
    return hashlib.sha256(json.dumps(value,ensure_ascii=False,sort_keys=True,default=str).encode()).hexdigest()

def history_pairs(state):
    pairs=[]
    question=None
    for message in state.get('messages',[])[-80:]:
        if message.get('role')=='user':
            question=message.get('text','')
        elif message.get('role')=='assistant' and question is not None:
            pairs.append((question,message))
            question=None
    return pairs

def resolve_requery(question,state):
    if not SHORT_REQUERY.fullmatch(question): return question
    pairs=history_pairs(state)
    if not pairs: return None
    previous_question,reply=pairs[-1]
    result=reply.get('result') or {}
    if result.get('status') not in {'completed','partial_answer','insufficient_evidence'} or result.get('task_type') not in {'sql','rag','hybrid'}:
        return None
    metadata=result.get('repetition') or {}
    if metadata.get('relation')=='style_only' and not metadata.get('execution_question'):
        return None
    return metadata.get('execution_question') or previous_question

def source_identity(route,attachment_ids):
    settings=get_settings()
    return {'route':route,'database':str(settings.database_path.resolve()) if route in {'sql','hybrid'} else None,
            'knowledge':str(settings.documents_path.resolve()) if route in {'rag','hybrid'} else None,
            'attachments':sorted(attachment_ids or [])}

def source_snapshot(route,attachments,state):
    settings=get_settings()
    digest=hashlib.sha256()
    if route in {'sql','hybrid'}:
        for path in [settings.database_path,Path(str(settings.database_path)+'-wal')]:
            if path.exists(): digest.update(path.read_bytes())
    if route in {'rag','hybrid'}:
        if attachments:
            digest.update(json.dumps([d for d in state.get('documents',[]) if d['id'] in attachments],sort_keys=True,ensure_ascii=False).encode())
        elif settings.documents_path.exists():
            for path in sorted(settings.documents_path.iterdir()):
                if path.is_file(): digest.update(path.name.encode()+path.read_bytes())
    return digest.hexdigest()

def prepare_history(question,state,route,attachments):
    return {'question':question,'state':state,'identity':source_identity(route,attachments),
            'snapshot':source_snapshot(route,attachments,state),'pairs':history_pairs(state),
            'previous_reply':next((m.get('text','')[:1200] for m in reversed(state.get('messages',[])) if m.get('role')=='assistant'),'')}

def _question_key(question):
    normalized=inspect_language(question,load_sql_context())['normalized']
    return re.sub(r'[\s，。！？、：；,.!?;:（）()]+','',normalize_question(normalized)).casefold()

def _result_key(response):
    if response.sql:
        return {'sql':response.sql,'params':response.sql_params}
    return {'question':_question_key(response.message)}

def analyze_repeat(question,response,context):
    actual=bool(response.sql and response.data is not None and any('只读' in e.get('step','') or '查询数据库' in e.get('step','') for e in response.trace))
    retrieved=any(e.get('step')=='检索文档' for e in response.trace)
    signature=_hash({'source':context['identity'],'query':_result_key(response) if response.sql else {'question':_question_key(question)}})
    previous=None
    count=0
    legacy=False
    for old_question,reply in context['pairs']:
        old=reply.get('result') or {}
        if old.get('status') not in {'completed','partial_answer','insufficient_evidence'}:
            continue
        metadata=old.get('repetition') or {}
        if metadata.get('relation')=='style_only':
            continue
        if metadata.get('signature')==signature:
            previous=old; count+=1; legacy=False
        elif not metadata and old.get('task_type')==response.task_type and (_question_key(old_question)==_question_key(question) or (response.sql and old.get('sql')==response.sql and old.get('sql_params')==response.sql_params)):
            previous=old; count+=1; legacy=True
    relation='not_repeated'
    if previous and (actual or retrieved):
        relation='rechecked'
        if response.sql and previous.get('sql') and not legacy:
            current_rows=(response.data or {}).get('rows',[])
            old_rows=(previous.get('data') or {}).get('rows',[])
            same=_hash(sorted(current_rows,key=lambda r:json.dumps(r,sort_keys=True)))==_hash(sorted(old_rows,key=lambda r:json.dumps(r,sort_keys=True)))
            relation='same' if same else 'changed'
            if not same and (previous.get('repetition') or {}).get('snapshot')==context['snapshot']:
                relation='conflict'
                response.status='conflicting_evidence'
                response.message='相同条件和数据快照下，本次结果与前文不一致，暂不输出确定结论。请核对下方SQL与实际结果。'
    metadata={'signature':signature,'snapshot':context['snapshot'],'source':context['identity'],
              'execution_question':question,'is_repeat':bool(previous),'repeat_count':count,
              'previous_run_id':previous.get('run_id') if previous else None,
              'relation':relation,'fresh_query':actual,'fresh_retrieval':retrieved,
              'style':['简洁结论','核对说明','分点说明'][count%3]}
    response.repetition=metadata
    context.update(metadata)
    if previous and (actual or retrieved):
        response.trace.append({'step':'复查历史结果','status':'failed' if relation=='conflict' else 'success',
            'detail':f"已重新执行本次任务；历史运行 {previous.get('run_id')}；比较结果：{relation}；未复用历史数值作为答案",
            'duration_ms':0})
    return context

def decorate_answer(response,context):
    if response.status not in {'completed','partial_answer','insufficient_evidence'}:
        if response.status=='unsupported': response.message='请您留意，'+response.message
        return response
    metadata=response.repetition or {}
    if metadata.get('is_repeat') and (metadata.get('fresh_query') or metadata.get('fresh_retrieval')):
        if metadata['relation']=='same':
            intros=['您前面已经问过这个问题，我刚刚重新查询了一次，结果与前文一致。',
                    '前文已经给出过这一结果；这次重新查了数据库，关键数值保持一致。',
                    '已为您再次核对相同条件，本次查询结果与之前一致。']
            intro=intros[(metadata['repeat_count']-1)%len(intros)]
        elif metadata['relation']=='changed':
            intro='您前面问过这个问题。我重新查询后发现结果有变化，请以本次SQL和返回数据为依据。'
        elif metadata.get('fresh_query'):
            intro='前文出现过这个问题，我这次按当前数据源重新查询，结果如下。'
        else:
            intro='前文已讨论过这个问题。我这次重新检索并核对了资料，以下回答以本次引用为依据。'
        if (context.get('reply_contract') or {}).get('length') == 'brief':
            if metadata['relation'] == 'same': intro='复查结果与前文一致：'
            elif metadata['relation'] == 'changed': intro='本次结果有变化：'
            else: intro='本次重新核对结果：'
            if response.verified_message is None: response.verified_message=response.message
            response.message=intro+response.message
            return response
    elif metadata.get('fresh_query') and response.status=='completed':
        return response
    elif metadata.get('fresh_retrieval') and response.evidence:
        return response
    else:
        return response
    if response.verified_message is None: response.verified_message=response.message
    response.message=intro+'\n\n'+response.message
    return response
