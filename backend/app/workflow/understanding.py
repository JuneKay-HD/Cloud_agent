"""Traceable query language roles, catalog-backed entities and protected constraints."""
import re

SOURCE = re.compile(r'(?:业务数据|当前数据库|当前业务库|当前数据源|数据库)(?:中的|里的|内的|中|里|内)?')
MODIFIERS = re.compile(r'^(?:(?:您好|你好|谢谢|好的|麻烦您|麻烦|请您|请问|请|帮我|帮忙|能否|能不能|可以|再次|重新|再)(?:[，,：:\s]*))+')
CONSTRAINT = re.compile(r'VIP|仅|只|排除|除了|不含|以外|不是|非|已|未|超过|大于|小于|至少|至多|不低于|不超过|渠道|线上|线下|客户|订单|发票|交易|状态|CustomerId|税|退款|利润|成本|季度|半年|城市|部门|公司|产品|测试数据|生产数据',re.I)

def _pattern(value):
    value=str(value)
    return rf'(?<![A-Za-z0-9_]){re.escape(value)}(?![A-Za-z0-9_])' if value.isascii() else re.escape(value)

def entity_mentions(question,catalog):
    from app.workflow.element_index import extract_elements
    entries=[]
    for kind,definition in catalog['entities'].get('entities',{}).items():
        for canonical,aliases in definition.get('value_aliases',{}).items():
            for alias in [str(canonical),*(aliases or [])]:
                entries.append({'alias':alias,'role':'entity','entity_type':kind,'canonical':str(canonical),
                    'field':definition.get('source_field'),'table':definition.get('source_table')})
    return extract_elements(question,entries)


def inspect_language(question,catalog):
    elements=entity_mentions(question,catalog)
    edits=[]
    source_choices=catalog['schema'].get('data_sources') or [catalog['schema'].get('database','当前数据库')]
    source_id=str(source_choices[0]) if len(source_choices)==1 else None
    ambiguous_source=False
    for match in SOURCE.finditer(question):
        # A restricted source/subset is a genuine condition, not removable filler.
        if re.search(r'仅|只|排除|不含|非|测试|生产|其他|另一',question[max(0,match.start()-6):match.start()]):
            continue
        edits.append((match.start(),match.end(),''))
        elements.append({'role':'data_source','source':match[0],'start':match.start(),'end':match.end(),
                         'canonical':source_id,'basis':'当前活动数据库' if source_id else '存在多个候选数据源'})
        ambiguous_source=ambiguous_source or source_id is None
    modifier=MODIFIERS.match(question)
    if modifier:
        edits.append((modifier.start(),modifier.end(),''))
        elements.append({'role':'request_modifier','source':modifier[0],'start':modifier.start(),'end':modifier.end()})
    for metric_id,definition in catalog['metrics'].get('metrics',{}).items():
        for alias in [definition['name'],*definition.get('aliases',[])]:
            for match in re.finditer(_pattern(alias),question,re.I):
                elements.append({'role':'metric','source':match[0],'start':match.start(),'end':match.end(),'canonical':metric_id})
    for match in re.finditer(r'(?<!\d)(?:19|20|21)\d{2}(?!\d)',question):
        elements.append({'role':'time','source':match[0],'start':match.start(),'end':match.end(),'canonical':match[0]})
    for match in CONSTRAINT.finditer(question):
        elements.append({'role':'constraint_marker','source':match[0],'start':match.start(),'end':match.end()})
    normalized=question
    for start,end,target in sorted(edits,reverse=True):
        normalized=normalized[:start]+target+normalized[end:]
    # These are request verbs, not data predicates.
    normalized=re.sub(r'^(?:帮我|帮忙|再|再次|重新)+(?=查询|统计|查一下)', '', normalized).strip(' ，,：:')
    normalized=re.sub(r'^(?:从|在|根据|基于)(?=查询|统计|查一下)', '', normalized)
    # Normalize only a leading request verb. Never drop unknown predicate text.
    request = re.match(r'^(?:查询(?:一下)?|查看(?:一下)?|查一查|查一下|看一下|看看|统计(?:一下)?|查(?=\s|美国|加拿大|中国|法国|德国|销售|客户|发票|\d))', normalized)
    if request:
        verb = request[0]
        target = '统计' if verb.startswith('统计') else '查询'
        original = question.find(verb)
        if original >= 0:
            elements.append({'role':'request_verb','source':verb,'start':original,
                             'end':original+len(verb),'canonical':target})
        normalized = target + normalized[request.end():]
    return {'normalized':normalized,'elements':sorted(elements,key=lambda e:e['start']),
            'source_id':source_id,'ambiguous_source':ambiguous_source}

def unresolved_fragment(value):
    return {'text':value,'role':'query_constraint' if CONSTRAINT.search(value) else 'unresolved_expression',
            'reason':'原文片段尚未映射到支持的字段或操作，不能丢弃'}

def protected_edit(source,catalog):
    if re.search(r'\d|否|不|除|仅|只|至少|至多|超过|同比|环比|VIP|利润|成本|退款|季度|半年|客户ID|CustomerId',source,re.I):
        return True
    if entity_mentions(source,catalog):
        return True
    return any(re.search(_pattern(alias),source,re.I) for definition in catalog['metrics']['metrics'].values()
               for alias in [definition['name'],*definition.get('aliases',[])])

def canonical_question(task):
    if task.query_scope=='schema':
        return task.schema_plan['interpretation']
    if task.query_scope == 'dataset':
        names={'customer_count':'Customer表全部客户数量','invoice_count':'Invoice表全部发票数量'}
        return '查询数据集 '+ '、'.join(names[m] for m in (task.metric_ids or [task.metric_id]))
    if task.intent=='rag':
        return task.normalized_question or task.raw_question
    pieces=['查询']
    if task.time_range:
        pieces.append(f"{task.time_range['start']}至{task.time_range['end']}（结束日不含）")
    else:
        pieces.append('待补充时间范围')
    countries=[v for f in task.filters if f.get('field')=='billing_country' for v in f.get('values',[])]
    pieces.append('、'.join(countries) if countries else '全部国家')
    names={'sales_amount':'销售额','invoice_count':'发票数量','customer_count':'客户数量'}
    pieces.append('、'.join(names.get(m,m) for m in (task.metric_ids or ([task.metric_id] if task.metric_id else ['待补充指标']))))
    if task.dimensions: pieces.append('按'+','.join({'billing_country':'国家','month':'月份','customer_id':'客户ID'}.get(d,d) for d in task.dimensions)+'分组')
    if task.top_n: pieces.append(f'前{task.top_n}名')
    if task.invoice_amount: pieces.append(f"单笔金额{task.invoice_amount['operator']}{task.invoice_amount['value']}")
    if task.customer_ids: pieces.append('客户ID '+','.join(map(str,task.customer_ids)))
    if task.comparison:
        pieces.append({'year_over_year':'年度同比','period_over_period':'年度比较'}.get(task.comparison,task.comparison))
        if task.baseline_time_range: pieces.append('基期'+task.baseline_time_range['start'])
    if task.unresolved_fragments: pieces.append('待解析：'+'、'.join(x['text'] for x in task.unresolved_fragments))
    return ' '.join(pieces)
