"""Open entity types, real field/value candidates, and auditable safe edits."""
import hashlib
import json
import re
import sqlite3
import time
import unicodedata
from contextlib import closing
from difflib import SequenceMatcher

import yaml

from app.config import get_settings

MAX_CANDIDATES = 80
PROTECTED = re.compile(r'\d|不|非|除|仅|只|至少|至多|超过|大于|小于|以上|以下|未|已|同比|环比|VIP|退款|利润|渠道|状态', re.I)


def normalized(value):
    return unicodedata.normalize('NFKC', str(value)).casefold().strip()


def load_configuration(schema):
    settings = get_settings()
    from app.workflow.domain_registry import load_domain_registry
    data = load_domain_registry(settings.config_path)
    legacy = settings.config_path / 'entities.yaml'
    vocabulary = (yaml.safe_load(legacy.read_text(encoding='utf-8')) or {}).get('entities', {}) if legacy.exists() else {}
    fields = data.get('fields', [])
    if not isinstance(fields, list) or len(fields) > 100:
        raise ValueError('语义实体字段配置无效')
    result = []
    for item in fields:
        item = dict(item)
        if item.get('alias_catalog'):
            if item['alias_catalog'] not in vocabulary:
                raise ValueError('引用的实体别名词表不存在')
            item['aliases'] = vocabulary[item['alias_catalog']].get('value_aliases', {})
        if (not isinstance(item, dict) or not isinstance(item.get('entity_type'), str)
                or not item['entity_type'].strip() or not isinstance(item.get('aliases', {}), dict)):
            raise ValueError('实体类型或别名配置无效')
        table, column = item.get('table'), item.get('column')
        if table not in schema['tables'] or column not in schema['tables'][table]['columns']:
            raise ValueError('实体配置引用不存在或未授权的字段')
        if item.get('aliases') and not str(item.get('provenance', '')).strip():
            raise ValueError('实体别名必须标注来源')
        roles = item.get('role_aliases', [])
        if (not isinstance(roles, list) or len(roles) > 40
                or any(not isinstance(a, str) or not a.strip() or len(a) > 100 for a in roles)):
            raise ValueError('字段角色别名配置无效')
        for value, aliases in item.get('aliases', {}).items():
            if not isinstance(value, str) or not value or not isinstance(aliases, list) or any(not isinstance(a, str) or not a for a in aliases):
                raise ValueError('实体别名必须为规范值到字符串列表')
        result.append(item)
    # Existing vocabulary is schema-linked, not restricted to a hard-coded entity type.
    if legacy.exists():
        for kind, item in vocabulary.items():
            table, column = item.get('source_table'), item.get('source_field')
            if table in schema['tables'] and column in schema['tables'][table]['columns']:
                result.append({'table': table, 'column': column, 'entity_type': kind,
                    'aliases': item.get('value_aliases', {}), 'provenance': 'entities.yaml业务词表'})
    return result


def candidate_catalog(question, schema):
    """Only the active authorized schema, with bounded live reads and exact provenance."""
    settings = get_settings()
    definitions = load_configuration(schema)
    grouped = {}
    for definition in definitions:
        grouped.setdefault((definition['table'], definition['column']), []).append(definition)
    candidates, truncated = {}, []
    q = normalized(question)
    started = time.perf_counter()
    with closing(sqlite3.connect(f'file:{settings.database_path.as_posix()}?mode=ro', uri=True)) as db:
        db.set_progress_handler(lambda: int(time.perf_counter() - started > 2), 2000)
        for table, description in schema['tables'].items():
            for column, datatype in description['columns'].items():
                configs = grouped.get((table, column), [])
                if not configs and not any(t in datatype.upper() for t in ('CHAR', 'TEXT', 'CLOB')):
                    continue
                # Values are data; even malicious stored strings cannot become tool instructions.
                table_sql = '"' + table.replace('"', '""') + '"'
                column_sql = '"' + column.replace('"', '""') + '"'
                values = []
                try:
                    rows = db.execute(f'SELECT DISTINCT {column_sql} FROM {table_sql} WHERE {column_sql} IS NOT NULL ORDER BY {column_sql} LIMIT 201').fetchall()
                    values = [str(row[0]) for row in rows[:200] if len(str(row[0])) <= 150]
                    if len(rows) > 200:
                        truncated.append(f'{table}.{column}')
                        # Resolve exact values anywhere in the table, beyond the sample.
                        values.extend(str(row[0]) for row in db.execute(
                            f'SELECT DISTINCT {column_sql} FROM {table_sql} WHERE length({column_sql}) BETWEEN 2 AND 150 '
                            f'AND instr(lower(?), lower({column_sql})) > 0 LIMIT 20', (question,)))
                except sqlite3.OperationalError:
                    truncated.append(f'{table}.{column}:读取预算不足')
                configured = {v for c in configs for v in c.get('aliases', {})}
                for value in dict.fromkeys([*values, *configured]):
                    metadata = next((c for c in configs if value in c.get('aliases', {})), configs[0] if configs else {})
                    aliases = list(dict.fromkeys(a for c in configs for a in c.get('aliases', {}).get(value, [])))
                    identifier = hashlib.sha256(json.dumps([table, column, value], ensure_ascii=False).encode()).hexdigest()[:16]
                    names = [value, *aliases]
                    exact = [name for name in names if len(normalized(name)) >= 2 and normalized(name) in q]
                    words = re.findall(r'[A-Za-z][A-Za-z ._-]{2,}|[\u4e00-\u9fff]{2,}', question)
                    similarity = max((SequenceMatcher(None, normalized(word), normalized(name)).ratio()
                                      for word in words for name in names), default=0)
                    score = 2 + max(map(len, exact)) / 100 if exact else similarity
                    candidates[identifier] = {'id': identifier, 'table': table, 'column': column,
                        'entity_type': metadata.get('entity_type', f'{table}.{column}'),
                        'value': value, 'aliases': aliases, 'present': value in values,
                        'provenance': metadata.get('provenance', '活动数据库真实字段值'), 'score': round(score, 4)}
    pool = sorted(candidates.values(), key=lambda c: (-c['score'], c['id']))
    exact = [c for c in pool if c['score'] >= 2]
    if len(exact) > MAX_CANDIDATES:
        raise ValueError('实体精确候选过多，请明确字段或对象')
    pool = [*exact, *[c for c in pool if c['score'] < 2][:MAX_CANDIDATES-len(exact)]]
    spelling = []
    surfaces = [m[0] for m in re.finditer(r'[A-Za-z][A-Za-z0-9_-]{3,}', question)]
    for source in surfaces:
        if any(normalized(source)==normalized(a) for c in pool for a in [c['value'],*c['aliases']]):
            continue
        close = []
        for c in pool:
            for alias in [c['value'],*c['aliases']]:
                a,b=normalized(source),normalized(alias)
                if len(b)<4 or abs(len(a)-len(b))>1:
                    continue
                distance=sum(max(i2-i1,j2-j1) for tag,i1,i2,j1,j2 in SequenceMatcher(None,a,b).get_opcodes() if tag!='equal')
                if distance==1:
                    close.append({'candidate_id':c['id'],'matched_alias':alias,'edit_distance':1})
        if close:
            spelling.append({'source':source,'candidates':close,'unique_value':len({next(c['value'] for c in pool if c['id']==v['candidate_id']) for v in close})==1})
    from app.workflow.element_index import extract_elements
    entries=[{'alias':name,'candidate_id':candidate['id']}
             for candidate in pool for name in [candidate['value'],*candidate['aliases']]
             if len(normalized(name))>=2]
    mentions=[]
    for hit in extract_elements(question,entries):
        # A threshold/ranking numeral may coincidentally equal a stored title or
        # category code. Explicit numeric grammar is a condition, not an entity.
        numeric = re.fullmatch(r'[-+]?\d+(?:\.\d+)?', unicodedata.normalize('NFKC',hit['source']))
        prefix = unicodedata.normalize('NFKC',question[max(0,hit['start']-12):hit['start']])
        if numeric and re.search(r'(?:不超过|不低于|至少|至多|超过|大于|小于|低于|[<>≤≥]=?|前|后)\s*$',prefix):
            continue
        mentions.append({k:hit[k] for k in ('start','end','source','candidate_id')})
    longest = []
    for mention in sorted(mentions, key=lambda m: (-(m['end']-m['start']), m['start'])):
        if not any(mention['start'] < old['end'] and old['start'] < mention['end']
                   and (mention['start'],mention['end']) != (old['start'],old['end']) for old in longest):
            longest.append(mention)
    return {'candidates': pool, 'mentions': longest, 'sampled_fields': sorted(set(truncated)),
            'spelling_suggestions':spelling,
            'table_context': {table:[table,*item.get('aliases', [])] for table,item in schema['tables'].items()},
            'duration_ms': round((time.perf_counter()-started)*1000, 2)}


def transaction_population_field(question, schema):
    """An explicit transacting customer population uses the registered DISTINCT key."""
    if not (re.search(r'客户|顾客|买家|customer', question, re.I)
            and re.search(r'有交易|有成交|成交的|购买过|买过|发生交易', question)
            and re.search(r'人数|数量|客户数|多少|去重|算一人|只算一', question)):
        return None
    from app.sql.schema_linker import load_sql_context
    expression = load_sql_context()['metrics']['metrics'].get('customer_count', {}).get('expression', '')
    match = re.fullmatch(r'COUNT\s*\(\s*DISTINCT\s+(\w+)\.(\w+)\s*\)', expression, re.I)
    if not match:
        return None
    table, column = match.groups()
    if table not in schema['tables'] or column not in schema['tables'][table]['columns']:
        return None
    return table, column


def verify_transaction_population(question, plan, schema):
    field = transaction_population_field(question, schema)
    if field is not None and not any(s.operation == 'count_distinct' and s.field is not None
            and (s.field.table, s.field.column) == field for s in plan.selections):
        raise ValueError('有交易的客户人数须按真实交易记录的客户标识去重，不能用发票条数或客户主表总数替代')


def scope_field_roles(question, schema, entities):
    """Bind a value occurrence to its nearby configured field qualifier.

    Only a qualifier attached to the value assigns its field. A separate
    grouping or filter in a supplement does not reinterpret earlier values.
    """
    hits = []
    for definition in load_configuration(schema):
        table, column = definition['table'], definition['column']
        aliases = [f'{table}.{column}', *definition.get('role_aliases', [])]
        for alias in aliases:
            for match in re.finditer(re.escape(alias), question, re.I):
                hits.append({'table':table,'column':column,'source':match[0],
                             'start':match.start(),'end':match.end()})
    hits = [h for h in hits if not any(other['start'] <= h['start'] and h['end'] <= other['end']
            and other['end']-other['start'] > h['end']-h['start'] for other in hits)]
    hits = [h for h in hits if not re.search(r'不是|不按|不要|而非', question[max(0,h['start']-3):h['start']])]
    lookup = {c['id']:c for c in entities['candidates']}
    groups = {}
    for mention in entities['mentions']:
        groups.setdefault((mention['start'],mention['end']), []).append(mention)
    mentions, removed, bindings = [], set(), []
    for (start,end), group in groups.items():
        fields = {(lookup[m['candidate_id']]['table'],lookup[m['candidate_id']]['column']) for m in group}
        relevant = [h for h in hits if (h['table'],h['column']) in fields]
        local = [h for h in relevant if 0 <= start-h['end'] <= 12
                 and not re.search(r'[，,。！？;；\n]',question[h['end']:start])]
        if local:
            nearest = max(h['end'] for h in local)
            selected = [h for h in local if h['end']==nearest]
        else:
            selected = []
        chosen = {(h['table'],h['column']) for h in selected}
        if len(chosen)==1:
            field = next(iter(chosen))
            kept = [m for m in group if (lookup[m['candidate_id']]['table'],lookup[m['candidate_id']]['column'])==field]
            removed.update(m['candidate_id'] for m in group if m not in kept)
            bindings.append({'source':question[start:end],'start':start,'end':end,
                             'table':field[0],'column':field[1],
                             'qualifier':max(selected,key=lambda h:len(h['source'])),
                             'basis':'configured_field_role'})
            group = kept
        mentions.extend(group)
    # A candidate may be valid at a different occurrence of the same word.
    removed -= {m['candidate_id'] for m in mentions}
    entities['candidates'] = [c for c in entities['candidates'] if c['id'] not in removed]
    entities['mentions'] = mentions
    for suggestion in entities['spelling_suggestions']:
        suggestion['candidates'] = [c for c in suggestion['candidates'] if c['candidate_id'] not in removed]
    entities['field_role_bindings'] = bindings
    return entities


def scope_candidates(question, schema, entities):
    """Resolve field roles from explicit table aliases and forward FK reachability.

    Only competing candidates for the same recognized span are narrowed. Separate
    entities and ambiguity within one table retain all their candidates.
    """
    entities = scope_field_roles(question, schema, entities)
    anchors = []
    for table, names in entities['table_context'].items():
        for name in names:
            for match in re.finditer(re.escape(str(name)), question, re.I):
                anchors.append((match.start(), match.end(), table))
    anchors = [a for a in anchors if not any(b[0] <= a[0] and a[1] <= b[1]
               and b[1]-b[0] > a[1]-a[0] for b in anchors)]
    tables = {a[2] for a in anchors}
    explicit_tables = sorted(tables)
    population = transaction_population_field(question, schema)
    customer_location = bool(re.search(r'客户(?:的)?(?:所在地|所在(?:国家|城市|地区)|居住地)|居住|住在|来自|顾客(?:的)?所在地', question))
    if population is not None and not customer_location:
        tables = {population[0]}
    if len(tables) != 1:
        entities['role_scope'] = {'explicit_tables': sorted(tables), 'narrowed': False}
        return entities
    base = next(iter(tables))
    reached = {base}
    for _ in range(len(schema['tables'])):
        parents = {fk['target_table'] for t in reached for fk in schema['tables'][t]['foreign_keys']}
        if parents <= reached:
            break
        reached |= parents
    lookup = {c['id']: c for c in entities['candidates']}
    groups = {}
    for m in entities['mentions']:
        groups.setdefault((m['start'], m['end']), []).append(m)
    excluded, kept_mentions = set(), []
    bound = {(b['start'],b['end']) for b in entities.get('field_role_bindings', [])}
    for span, group in groups.items():
        if span in bound:
            kept_mentions.extend(group)
            continue
        direct = [m for m in group if lookup[m['candidate_id']]['table'] == base]
        reachable = [m for m in group if lookup[m['candidate_id']]['table'] in reached]
        keep = direct or reachable
        if keep:
            excluded |= {m['candidate_id'] for m in group if m not in keep}
        kept_mentions.extend(keep or group)
    excluded -= {m['candidate_id'] for m in kept_mentions}
    entities['candidates'] = [c for c in entities['candidates'] if c['id'] not in excluded]
    entities['mentions'] = kept_mentions
    for suggestion in entities['spelling_suggestions']:
        suggestion['candidates'] = [c for c in suggestion['candidates'] if c['candidate_id'] not in excluded]
    entities['role_scope'] = {'explicit_tables': [base], 'forward_reachable_tables': sorted(reached),
                              'narrowed': bool(excluded), 'excluded_count': len(excluded)}
    if population is not None and not customer_location:
        entities['role_scope'].update(mentioned_tables=explicit_tables,
            statistical_base_table=base, population_key=population[1], basis='registered_transaction_population')
    return entities


def clarify_candidates(clarification, entities):
    """Resolve a same-field alias collision only with an explicit canonical value.

    Do not remove the other values globally: other spans may refer to them.
    """
    lookup={c['id']:c for c in entities['candidates']}
    grouped={}
    for m in entities['mentions']:
        grouped.setdefault((m['start'],m['end']),[]).append(m)
    keep=[]
    for group in grouped.values():
        fields={(lookup[m['candidate_id']]['table'],lookup[m['candidate_id']]['column']) for m in group}
        if len(fields)==1 and len({lookup[m['candidate_id']]['value'] for m in group})>1:
            explicit=[m for m in group if normalized(lookup[m['candidate_id']]['value']) in normalized(clarification)]
            if len({lookup[m['candidate_id']]['value'] for m in explicit})==1:
                group=explicit
        keep.extend(group)
    entities['mentions']=keep
    return entities


class LanguageEditError(ValueError):
    """The model's proposed text edit is invalid, not a missing user condition."""


def validate_edits(original, edits, entities):
    if edits is None:
        edits = []
    if not isinstance(edits, list) or len(edits) > 12:
        raise LanguageEditError('改写编辑格式无效')
    lookup = {c['id']: c for c in entities['candidates']}
    changes = []
    for edit in edits:
        if not isinstance(edit, dict):
            raise LanguageEditError('改写编辑必须为对象')
        source, target = edit.get('source'), edit.get('target')
        if (not isinstance(source, str) or not source or not isinstance(target, str)
                or not target.strip() or len(source) > 100 or len(target) > 150):
            raise LanguageEditError('编辑不能删除原文或使用无效文本')
        if 'start' in edit or 'end' in edit:
            start, end = edit.get('start'), edit.get('end')
            if (type(start) is not int or type(end) is not int
                    or not 0 <= start < end <= len(original) or original[start:end] != source):
                raise LanguageEditError('编辑位置与当前原文不一致')
        else:
            if original.count(source) != 1:
                raise LanguageEditError('编辑不能唯一定位原文或删除原文')
            start = original.index(source)
        candidate_id = edit.get('candidate_id')
        if candidate_id is not None and not isinstance(candidate_id, str):
            raise LanguageEditError('编辑实体候选标识必须为字符串或null')
        candidate = lookup.get(candidate_id)
        if candidate:
            if target != candidate['value']:
                raise LanguageEditError('实体改写目标不是可核对的规范值')
            known = [m for m in entities['mentions'] if m['start']==start and m['end']==start+len(source)]
            if known and candidate['id'] not in {m['candidate_id'] for m in known}:
                raise LanguageEditError('不能把已匹配的实体改成另一个实体')
            if not known and PROTECTED.search(source):
                raise LanguageEditError('未知实体纠错不能改变数字或否定条件')
            # Entity names can contain digits; their registered identity is preserved.
            basis = 'catalog_alias' if any(normalized(source)==normalized(a) for a in [candidate['value'],*candidate['aliases']]) else 'model_entity_link'
        else:
            if edit.get('candidate_id') or PROTECTED.search(source) or PROTECTED.search(target):
                raise LanguageEditError('纠错不能改变数字、否定或真实筛选条件')
            if any(original[m['start']:m['end']] in source for m in entities['mentions']):
                raise LanguageEditError('普通纠错不能覆盖已识别实体')
            if SequenceMatcher(None, normalized(source), normalized(target)).ratio() < .5:
                raise LanguageEditError('非实体编辑超过局部纠错范围')
            basis = 'model_spelling_correction'
        changes.append({**edit, 'start': start, 'end': start+len(source), 'basis': basis})
    ordered = sorted(changes, key=lambda c: c['start'])
    if any(a['end'] > b['start'] for a,b in zip(ordered,ordered[1:])):
        raise LanguageEditError('编辑跨度重叠')
    question = original
    for edit in reversed(ordered):
        question = question[:edit['start']] + edit['target'] + question[edit['end']:]
    return question, ordered


def verify_entity_bindings(original, plan, entities, edits):
    """Independently bind every known entity mention to actual parameter filters."""
    used = {plan.base_table, *plan.joins}
    lookup = {c['id']: c for c in entities['candidates']}
    grouped = {}
    for mention in entities['mentions']:
        candidate = lookup[mention['candidate_id']]
        grouped.setdefault((mention['start'], mention['end']), []).append(candidate)
    for edit in edits:
        if edit.get('candidate_id'):
            grouped.setdefault((edit['start'],edit['end']), []).append(lookup[edit['candidate_id']])
    evidence = []
    for (start,end), candidates in grouped.items():
        role_question = original
        for e in reversed(sorted([e for e in edits if not e.get('candidate_id')],key=lambda e:e['start'])):
            role_question = role_question[:e['start']]+e['target']+role_question[e['end']:]
        if len({(c['table'],c['column']) for c in candidates}) > 1 and len({c['table'] for c in candidates}) == 1:
            qualified = [c for c in candidates if c['entity_type'].casefold() in role_question.casefold()
                         or re.search(r'\b'+re.escape(c['column'])+r'\b',role_question,re.I)]
            if not qualified:
                raise ValueError('同名实体存在不同字段角色，请明确实体类型：'+original[start:end])
            candidates = qualified
        if len({c['table'] for c in candidates}) > 1:
            explicit_tables = {table for table,names in entities.get('table_context',{}).items()
                               if any(str(name) and str(name).casefold() in role_question.casefold() for name in names)}
            if not explicit_tables.intersection(used):
                raise ValueError('同名实体存在不同业务角色，请明确对象：'+original[start:end])
        candidates = [c for c in candidates if c['table'] in used]
        if not candidates:
            raise ValueError('实体存在但查询计划选择了不相关的表：'+original[start:end])
        bindings = []
        for f in plan.filters:
            allowed = [c for c in candidates if c['table']==f.field.table and c['column']==f.field.column]
            if not allowed:
                continue
            if len({c['value'] for c in allowed}) > 1:
                raise ValueError('实体别名对应同一字段多个规范值，请明确具体对象：'+original[start:end])
            for c in allowed:
                values = f.value if isinstance(f.value,list) else [f.value]
                if c['value'] in map(str,values):
                    if re.search(r'不含|排除|除了|除去|不是|不要', original[max(0,start-6):start]) and f.operator != 'ne':
                        raise ValueError('查询计划未保留实体排除条件：'+original[start:end])
                    bindings.append(c)
        if not bindings:
            raise ValueError('查询计划遗漏已识别实体或字段绑定不符：'+original[start:end])
        for c in {b['id']: b for b in bindings}.values():
            evidence.append({'role':'entity', 'source':original[start:end], 'start':start, 'end':end,
                'entity_type':c['entity_type'], 'canonical':c['value'], 'table':c['table'], 'field':c['column'],
                'basis':c['provenance'], 'present_in_sample_or_exact_read':c['present']})
    return evidence


def standardize_source(original, edits, source_elements):
    changes=list(edits)
    for element in source_elements:
        if not element.get('canonical'):
            continue
        if any(element['start'] < e['end'] and e['start'] < element['end'] for e in changes):
            raise ValueError('数据源标准化与模型编辑发生重叠')
        changes.append({'source':element['source'],'target':'当前数据源中的','start':element['start'],
                        'end':element['end'],'basis':'active_data_source'})
    question=original
    for edit in sorted(changes,key=lambda e:e['start'],reverse=True):
        question=question[:edit['start']]+edit['target']+question[edit['end']:]
    return question,changes


def verify_numeric_operators(question, plan):
    from decimal import Decimal, InvalidOperation
    operations = {'不超过':'le','至多':'le','不低于':'ge','至少':'ge','超过':'gt','大于':'gt','小于':'lt','低于':'lt',
                  '>=':'ge','<=':'le','≤':'le','≥':'ge','>':'gt','<':'lt'}
    pattern = '('+'|'.join(map(re.escape,operations))+r')\s*([-+]?\d+(?:\.\d+)?)'
    for match in re.finditer(pattern, unicodedata.normalize('NFKC',question)):
        expected = Decimal(match[2])
        matches = []
        for f in [*plan.filters, *getattr(plan,'having',[])]:
            if isinstance(f.value,list):
                continue
            try:
                if Decimal(str(f.value)) == expected:
                    matches.append(f)
            except InvalidOperation:
                pass
        if not any(f.operator == operations[match[1]] for f in matches):
            raise ValueError('查询计划未保留数值比较符：'+match[0])


def verify_inherited_filters(question, plan, context, elements):
    if not context.get('inherit_previous'):
        return
    if re.search(r'取消|去掉|不限|重新开始|全部.*(?:条件|筛选)',question):
        return
    previous = (context.get('previous_task') or {}).get('schema_plan') or {}
    changed = {(e['table'],e['field']) for e in elements if e['role']=='entity'}
    current = [f.model_dump() for f in plan.filters]
    for old in previous.get('filters', []):
        key = (old['field']['table'],old['field']['column'])
        if key in changed:
            continue
        if re.search(r'(?<!\d)(?:19|20|21)\d{2}(?!\d)', question) and old['field'].get('transform')=='year':
            continue
        if old not in current:
            raise ValueError('追问遗漏上一轮未修改的筛选条件：'+'.'.join(key))
    def aggregate_condition(condition, selections):
        position = condition.get('selection')
        if type(position) is not int or not 0 <= position < len(selections):
            raise ValueError('聚合筛选引用无效')
        from app.sql.schema_query import Selection
        selection = Selection.model_validate(selections[position]).model_dump()
        return ({k: selection.get(k) for k in ('operation', 'field', 'second_field')},
                condition.get('operator'), condition.get('value'))
    current_having = [aggregate_condition(h.model_dump(), [s.model_dump() for s in plan.selections])
                      for h in getattr(plan, 'having', [])]
    for old in previous.get('having', []):
        if aggregate_condition(old, previous.get('selections', [])) not in current_having:
            raise ValueError('追问遗漏或改变上一轮聚合筛选条件，请明确取消或修改条件')
