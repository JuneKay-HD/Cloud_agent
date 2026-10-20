"""Versioned business vocabularies, never prompt examples or executable code."""
import hashlib
import json
from pathlib import Path


def load_domain_registry(config_path):
    root=Path(config_path)
    paths=([root/'semantic_entities.json'] if (root/'semantic_entities.json').exists() else [])
    packs=root/'domain_packs'
    if packs.exists():
        paths+=sorted(packs.glob('*.json'))
    if len(paths)>33:
        raise ValueError('Too many domain packs')
    merged={'tables':{},'fields':[],'domains':[]}
    digest=hashlib.sha256()
    for path in paths:
        raw=path.read_bytes()
        if len(raw)>2_000_000:raise ValueError('Domain pack too large')
        data=json.loads(raw)
        if not isinstance(data,dict) or set(data)-{'domain_id','version','description','tables','fields'}:
            raise ValueError('Domain packs allow metadata, tables and fields only; prompt examples are not allowed')
        if path.parent==packs and (not isinstance(data.get('domain_id'),str) or not data.get('domain_id') or not isinstance(data.get('version'),str) or not data.get('version')):
            raise ValueError('Domain pack requires domain_id and version')
        tables=data.get('tables',{});fields=data.get('fields',[])
        if not isinstance(tables,dict) or not isinstance(fields,list):raise ValueError('Invalid domain structure')
        for name,definition in tables.items():
            if name in merged['tables'] and merged['tables'][name]!=definition:
                raise ValueError('Conflicting table definitions: '+name)
            merged['tables'][name]=definition
        merged['fields'].extend(fields)
        merged['domains'].append({'domain_id':data.get('domain_id',path.stem),'version':data.get('version','legacy'),'file':path.name})
        digest.update(path.name.encode());digest.update(raw)
    if len(merged['tables'])>40 or len(merged['fields'])>100:raise ValueError('Domain schema budget exceeded')
    merged['revision']=digest.hexdigest()
    return merged
