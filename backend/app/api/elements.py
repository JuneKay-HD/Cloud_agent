"""Read-only element inspection; no model call and no SQL execution."""
from fastapi import APIRouter,HTTPException
from pydantic import BaseModel,Field
from starlette.concurrency import run_in_threadpool
from app.config import get_settings
from app.sql.schema_query import catalog
from app.workflow.entity_linking import candidate_catalog,scope_candidates
from app.workflow.domain_registry import load_domain_registry

router=APIRouter(tags=['elements'])
class ExtractionRequest(BaseModel):
    question:str=Field(min_length=1,max_length=10000)

def inspect(question):
    settings=get_settings()
    schema=catalog()
    registry=load_domain_registry(settings.config_path)
    elements=scope_candidates(question,schema,candidate_catalog(question,schema))
    groups={}
    for mention in elements['mentions']:
        key=(mention['start'],mention['end'])
        groups.setdefault(key,set()).add(mention['candidate_id'])
    return {'method':'schema_and_dictionary_index','model_calls':0,'domains':registry['domains'],
            'revision':registry['revision'],'elements':elements,
            'needs_clarification':any(len(ids)>1 for ids in groups.values()),
            'scope':'Entity extraction only; SQL plans still require field, condition and read-only validation.'}

@router.post('/elements/extract')
async def extract(request:ExtractionRequest):
    try:return await run_in_threadpool(inspect,request.question)
    except (ValueError,OSError) as exc:raise HTTPException(422,'业务配置或数据源无法支持本次提取，请检查配置和字段') from exc
