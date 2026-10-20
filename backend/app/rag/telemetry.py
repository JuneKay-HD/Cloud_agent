"""Public stage measurements, never prompts or internal model reasoning."""
from time import perf_counter
from contextvars import ContextVar

_active = ContextVar('public_stage_metrics',default=None)


def model_request():
    row = _active.get()
    if row is not None:
        row['http_requests'] = row.get('http_requests',0) + 1


def model_usage(usage):
    row = _active.get()
    if row is None or not isinstance(usage,dict):
        return
    for target,keys in {'input_tokens':('prompt_tokens','input_tokens'),
                        'output_tokens':('completion_tokens','output_tokens'),
                        'total_tokens':('total_tokens',)}.items():
        value = next((usage[k] for k in keys if k in usage),None)
        if type(value) is int and value >= 0:
            row[target] = row.get(target,0) + value


async def measured(metrics, stage, operation):
    row = metrics.setdefault(stage, {'calls':0,'failed_calls':0,'duration_ms':0})
    row['calls'] += 1
    token = _active.set(row)
    started = perf_counter()
    try:
        return await operation()
    except BaseException:
        row['failed_calls'] += 1
        raise
    finally:
        _active.reset(token)
        row['duration_ms'] = round(row['duration_ms'] + (perf_counter()-started)*1000,2)
