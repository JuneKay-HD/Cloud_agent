"""Schema-grounded plans compiled into bounded, parameterized read-only SQL."""
import json
import math
import re
import sqlite3
from contextlib import closing
from typing import Literal
from pydantic import BaseModel, ConfigDict, Field
from app.config import get_settings

TABLES = {'Customer':'客户主数据','Invoice':'销售发票，Total为原始金额，不含独立币种或税额',
          'InvoiceLine':'发票明细，UnitPrice为单价，Quantity为购买数量','Employee':'员工',
          'Track':'曲目/歌曲，Milliseconds为毫秒时长，Bytes为文件字节大小','Album':'专辑',
          'Artist':'艺人/歌手','Genre':'音乐类型','MediaType':'媒体格式',
          'Playlist':'播放列表','PlaylistTrack':'播放列表与曲目的关联记录，不是唯一曲目'}


class Strict(BaseModel):
    model_config = ConfigDict(extra='forbid',strict=True)


class Column(Strict):
    table: str
    column: str
    transform: Literal['none','year','month','quarter','date','minutes'] = 'none'


class Selection(Strict):
    operation: Literal['count','count_distinct','sum','avg','min','max','value','sum_product']
    field: Column | None = None
    second_field: Column | None = None
    label: str = Field(min_length=1,max_length=60)


class Filter(Strict):
    field: Column
    operator: Literal['eq','ne','gt','ge','lt','le','in','contains','between','is_null','is_not_null']
    value: str | int | float | list[str | int | float] | None = None


class Having(Strict):
    selection: int = Field(ge=0)
    operator: Literal['eq','ne','gt','ge','lt','le']
    value: int | float


class Order(Strict):
    selection: int = Field(ge=0)
    direction: Literal['asc','desc'] = 'asc'


class Plan(Strict):
    base_table: str
    joins: list[str] = Field(default_factory=list,max_length=5)
    selections: list[Selection] = Field(min_length=1,max_length=8)
    filters: list[Filter] = Field(default_factory=list,max_length=12)
    having: list[Having] = Field(default_factory=list,max_length=8)
    order: list[Order] = Field(default_factory=list,max_length=3)
    limit: int = Field(default=200,ge=1,le=200)
    interpretation: str = Field(min_length=1,max_length=1000)


def catalog():
    s=get_settings()
    if s.dataset_profile != 'public':
        raise ValueError('当前数据源未启用公开库语义查询')
    if not s.database_path.exists():
        raise ValueError('活动数据库不存在')
    with closing(sqlite3.connect(f'file:{s.database_path.as_posix()}?mode=ro',uri=True)) as db:
        present={x[0] for x in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        from app.workflow.domain_registry import load_domain_registry
        configuration = load_domain_registry(s.config_path)
        extra_tables = configuration.get('tables', {})
        if not isinstance(extra_tables, dict) or len(extra_tables) > 40:
            raise ValueError('扩展语义表配置无效')
        if any(not isinstance(name,str) or not name or not isinstance(item,dict) for name,item in extra_tables.items()):
            raise ValueError('扩展语义表条目无效')
        descriptions = {**TABLES, **{name: item.get('description', TABLES.get(name,name)) for name, item in extra_tables.items()}}
        if any(name not in present for name in extra_tables):
            raise ValueError('注册的语义表不在当前真实数据库中')
        tables={}
        quote = lambda name: '"' + name.replace('"', '""') + '"'
        for table,description in descriptions.items():
            if table not in present: continue
            columns={x[1]:x[2] for x in db.execute(f'PRAGMA table_info({quote(table)})')}
            fks=[{'column':x[3],'target_table':x[2],'target_column':x[4]} for x in db.execute(f'PRAGMA foreign_key_list({quote(table)})') if x[2] in descriptions]
            values={}
            for name in ['Country','BillingCountry','GenreId','MediaTypeId']:
                if name in columns:
                    values[name]=[x[0] for x in db.execute(f'SELECT DISTINCT "{name}" FROM "{table}" WHERE "{name}" IS NOT NULL LIMIT 40')]
            if table in ['Genre','MediaType','Artist','Playlist'] and 'Name' in columns:
                values['Name']=[x[0] for x in db.execute(f'SELECT "Name" FROM "{table}" ORDER BY "Name" LIMIT 30')]
            tables[table]={'description':description,'columns':columns,'foreign_keys':fks,'values':values}
            if table in extra_tables:
                tables[table]['aliases'] = extra_tables[table].get('aliases', [])
    return {'database':s.database_path.name,'tables':tables,
            'semantics':'Customer计数=注册主数据；Invoice.CustomerId去重=成交客户。总数不需要年份；问题带时间/筛选则必须保留。无利润、退款、VIP、税额字段。'}


def compile_plan(payload, schema=None):
    p=Plan.model_validate(payload)
    schema=schema or catalog()
    tables=schema['tables']
    # Names are interpolated only as quoted identifiers, never executable syntax.
    if any('"' in name or '\x00' in name for name in tables):
        raise ValueError('表名包含不支持的标识符字符')
    if any('"' in name or '\x00' in name for table in tables.values() for name in table['columns']):
        raise ValueError('字段名包含不支持的标识符字符')
    used=[p.base_table,*p.joins]
    if len(set(used))!=len(used) or any(t not in tables for t in used):
        raise ValueError('计划包含不存在或未授权的表')
    # Follow only actual many-to-one foreign keys; never multiply base rows by a reverse join.
    reached={p.base_table}; pending=list(p.joins); joins=[]
    while pending:
        found=False
        for target in pending:
            choices=[(src,fk) for src in sorted(reached) for fk in tables[src]['foreign_keys'] if fk['target_table']==target]
            if len(choices)==1:
                src,fk=choices[0]
                joins.append(f'LEFT JOIN "{target}" ON "{src}"."{fk["column"]}" = "{target}"."{fk["target_column"]}"')
                reached.add(target);pending.remove(target);found=True;break
        if not found: raise ValueError('关联路径缺失、歧义或可能放大统计；请明确统计对象')
    def field(f):
        if f.table not in reached or f.column not in tables[f.table]['columns']:
            raise ValueError('字段不属于当前真实数据源')
        expr=f'"{f.table}"."{f.column}"'
        typ=tables[f.table]['columns'][f.column].upper()
        if f.transform in {'year','month','quarter','date'}:
            if not any(x in typ for x in ['DATE','TIME']): raise ValueError('日期变换只允许日期字段')
            if f.transform=='quarter':
                expr=f"(strftime('%Y', {expr}) || '-Q' || CAST((CAST(strftime('%m', {expr}) AS INTEGER)+2)/3 AS INTEGER))"
            else:
                pattern={'year':'%Y','month':'%Y-%m','date':'%Y-%m-%d'}[f.transform]
                expr=f"strftime('{pattern}', {expr})"
        elif f.transform=='minutes':
            if f.column!='Milliseconds': raise ValueError('分钟换算只允许毫秒时长字段')
            expr=f'({expr} / 60000.0)'
        return expr
    selects=[]; groups=[]
    for n,s in enumerate(p.selections):
        expr=field(s.field) if s.field else '*'
        if s.operation!='count' and not s.field: raise ValueError('指标缺少真实字段')
        if s.operation in {'sum','avg','sum_product'}:
            typ=tables[s.field.table]['columns'][s.field.column].upper()
            if not any(v in typ for v in ['INT','REAL','NUMERIC','DECIMAL','FLOAT','DOUBLE']): raise ValueError('不能对文本进行数值聚合')
        if s.second_field and s.operation!='sum_product':raise ValueError('第二字段仅用于受控乘积求和')
        if s.operation=='sum_product':
            if not s.second_field or s.second_field.table!=s.field.table:raise ValueError('乘积求和需要同一表的两个数值字段')
            second=field(s.second_field)
            typ=tables[s.second_field.table]['columns'][s.second_field.column].upper()
            if not any(v in typ for v in ['INT','REAL','NUMERIC','DECIMAL','FLOAT','DOUBLE']):raise ValueError('乘积字段必须是数值')
            expr=f'ROUND(SUM({expr} * {second}), 6)'
        elif s.operation=='value': groups.append(expr)
        elif s.operation=='count_distinct': expr=f'COUNT(DISTINCT {expr})'
        else:
            expr=f'{s.operation.upper()}({expr})'
            if s.operation in {'sum','avg'}:expr=f'ROUND({expr}, 6)'
        selects.append(f'{expr} AS "c{n}"')
    aggregate=any(s.operation!='value' for s in p.selections)
    conditions=[];params={}
    ops={'eq':'=','ne':'!=','gt':'>','ge':'>=','lt':'<','le':'<='}
    for n,f in enumerate(p.filters):
        expr=field(f.field);v=f.value
        if f.operator in {'is_null','is_not_null'}:
            if v is not None:raise ValueError('空值操作不得包含值')
            conditions.append(expr+(' IS NULL' if f.operator=='is_null' else ' IS NOT NULL'))
            continue
        if v is None:raise ValueError('过滤值不能为null，请使用空值操作')
        vals=v if isinstance(v,list) else [v]
        if not vals or len(vals)>100 or any(isinstance(x,bool) or isinstance(x,float) and not math.isfinite(x) for x in vals):raise ValueError('过滤值无效')
        if f.operator=='between':
            if (not isinstance(v,list) or len(v)!=2
                    or not (all(type(x) in (int,float) for x in v) or all(isinstance(x,str) for x in v))
                    or v[0]>v[1]):raise ValueError('区间必须是两个有序数值或文本端点')
            params[f'p{n}_lo'],params[f'p{n}_hi']=v
            conditions.append(expr+f' BETWEEN :p{n}_lo AND :p{n}_hi')
        elif f.operator=='in':
            keys=[]
            for i,x in enumerate(vals):
                key=f'p{n}_{i}';params[key]=x;keys.append(':'+key)
            conditions.append(expr+' IN ('+', '.join(keys)+')')
        else:
            if isinstance(v,list):raise ValueError('单值操作不能包含列表')
            key=f'p{n}';params[key]=v
            if f.operator=='contains':
                if not isinstance(v,str):raise ValueError('包含查询需要文本')
                params[key]='%'+v.replace('\\','\\\\').replace('%','\\%').replace('_','\\_')+'%'
                conditions.append(expr+f" LIKE :{key} ESCAPE '\\'")
            else:conditions.append(expr+' '+ops[f.operator]+' :'+key)
    sql='SELECT '+', '.join(selects)+f' FROM "{p.base_table}"'
    if joins:sql+=' '+' '.join(joins)
    if conditions:sql+=' WHERE '+' AND '.join(conditions)
    if aggregate and groups:sql+=' GROUP BY '+', '.join(groups)
    if p.having:
        clauses=[]
        for n,h in enumerate(p.having):
            if not aggregate or h.selection>=len(selects) or p.selections[h.selection].operation=='value' or not math.isfinite(h.value):
                raise ValueError('HAVING仅允许现有聚合指标与有限数值比较')
            params[f'h{n}']=h.value
            clauses.append(f'"c{h.selection}" {ops[h.operator]} :h{n}')
        sql+=' HAVING '+' AND '.join(clauses)
    ordered=[]
    for o in p.order:
        if o.selection>=len(selects):raise ValueError('排序指标不存在')
        ordered.append(f'"c{o.selection}" {o.direction.upper()}')
    # Stable group ties; bound list reads to detect truncation using the existing executor.
    for n,s in enumerate(p.selections):
        if s.operation=='value' and not any(o.selection==n for o in p.order):ordered.append(f'"c{n}" ASC')
    if ordered:sql+=' ORDER BY '+', '.join(ordered)
    # 201 rows allows executor to flag truncation for a default 200-row request.
    params['result_limit']=201 if p.limit==200 else p.limit
    sql+=' LIMIT :result_limit'
    return sql,params,p
