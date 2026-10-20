"""Import a local Chinook/OHR package without indexing benchmark answers or noise."""
import argparse
import hashlib
import json
import os
import re
import shutil
import sqlite3
import sys
from collections import Counter
from contextlib import closing
from pathlib import Path

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from app.config import PROJECT_ROOT, get_settings

ENV_KEYS=('DATABASE_PATH','DOCUMENTS_PATH','INDEXES_PATH','PARSED_PATH','CONFIG_PATH',
          'DATASET_PROFILE','DEMO_DATA_LABEL','PUBLIC_DATASET_ROOT')

def io_path(path):
    return Path('\\\\?\\'+str(path.resolve())) if os.name=='nt' else path


def digest(path):
    hasher=hashlib.sha256()
    with io_path(path).open('rb') as handle:
        for block in iter(lambda:handle.read(1048576),b''):hasher.update(block)
    return hasher.hexdigest()

def copy_verified(source,target):
    before=digest(source)
    if io_path(target).exists():
        if digest(target)!=before:raise ValueError(f'Existing copied file differs: {target.name}')
        return before
    io_path(target.parent).mkdir(parents=True,exist_ok=True)
    shutil.copy2(io_path(source),io_path(target))
    if before!=digest(target) or before!=digest(source):
        raise ValueError(f'Source changed while copying: {source.name}')
    return before

def find_package(source):
    for path in [source,source/'public_benchmark',source/'data/public_benchmark']:
        if (path/'chinook/Chinook.sqlite').is_file() and (path/'ohr/retrieval/gt').is_dir():return path
    raise ValueError('No complete Chinook and OHR retrieval/gt package found')

def import_package(source,destination,resume=False):
    package=find_package(source.resolve())
    destination=destination.resolve()
    if not destination.is_relative_to((PROJECT_ROOT/'data').resolve()) or destination==(PROJECT_ROOT/'data').resolve():
        raise ValueError('Destination must be a new dataset directory inside project/data')
    if destination.exists() and not resume:raise FileExistsError('Preserve existing dataset; use a new destination or verified --resume')
    gt=package/'ohr/retrieval/gt'
    inputs=sorted(gt.rglob('*.json'))
    pdfs=sorted((package/'ohr/pdfs').rglob('*.pdf'))
    identities={f.relative_to(gt).with_suffix('') for f in inputs}
    pdf_identities={f.relative_to(package/'ohr/pdfs').with_suffix('') for f in pdfs}
    if not inputs or identities!=pdf_identities:
        raise ValueError('Ground-truth/PDF document coverage differs; wait for source download to complete')
    sources=[]
    for path in inputs:
        rows=json.loads(path.read_text(encoding='utf-8-sig'))
        if not isinstance(rows,list) or not rows:raise ValueError('Empty or invalid OHR source')
        seen=set()
        for row in rows:
            if (not isinstance(row,dict) or set(row)-{'text','page_idx'}
                    or type(row.get('page_idx')) is not int or row['page_idx']<0
                    or not isinstance(row.get('text'),str) or row['page_idx'] in seen):
                raise ValueError(f'Invalid page-only source, not an answer file: {path.name}')
            seen.add(row['page_idx'])
        relative=path.relative_to(gt)
        if len(relative.parts)!=2:raise ValueError('Expected category/document.json structure')
        sources.append({'category':relative.parts[0],'doc_name':path.stem,
            'filename':relative.parts[0]+'__'+path.stem+'.ohr.json','pages':len(rows),
            'has_text':any(row['text'].strip() for row in rows),
            'relative_original':relative.as_posix(),'sha256':digest(path)})
    if resume and destination.exists():
        if (destination/'previous_profile.json').exists():raise ValueError('Cannot resume a previously activated dataset')
        expected={s['filename']:s['sha256'] for s in sources}
        for path in (destination/'ohr/documents').glob('*'):
            if path.name not in expected or digest(path)!=expected[path.name]:
                raise ValueError('Existing partial corpus does not match the selected source')
        if (destination/'chinook/Chinook.sqlite').exists() and digest(destination/'chinook/Chinook.sqlite')!=digest(package/'chinook/Chinook.sqlite'):
            raise ValueError('Partial database does not match selected source')
    destination.mkdir(parents=True,exist_ok=True)
    for path in (package/'chinook').iterdir():
        if path.is_file():copy_verified(path,destination/'chinook'/path.name)
    for path,metadata in zip(inputs,sources):
        copied=copy_verified(path,destination/'ohr/documents'/metadata['filename'])
        if copied!=metadata['sha256']:raise ValueError('GT source changed after validation')
        copy_verified(path,destination/'ohr/retrieval/gt'/metadata['relative_original'])
    print(f'Copied {len(sources)} verified page-text documents',flush=True)
    for path in pdfs:
        copy_verified(path,destination/'ohr/pdfs'/path.relative_to(package/'ohr/pdfs'))
    # Preserve complete downloaded variants for later OCR comparisons, but keep
    # them outside the active documents directory to avoid duplicate evidence.
    for variant in (package/'ohr/retrieval').iterdir():
        if not variant.is_dir() or variant.name=='gt':continue
        for path in variant.rglob('*'):
            if io_path(path).is_file():copy_verified(path,destination/'ohr/retrieval'/path.relative_to(package/'ohr/retrieval'))
    for name in ['OHR-Bench.parquet','OHR-Bench_v2.parquet','README.md']:
        path=package/'ohr'/name
        if path.is_file():copy_verified(path,destination/'ohr'/name)
    for path in (package/'ohr/evaluation').rglob('*'):
        if path.is_file():copy_verified(path,destination/'ohr/evaluation'/path.relative_to(package/'ohr/evaluation'))
    for name in ['manifest.json','README.md']:
        if (package/name).is_file():copy_verified(package/name,destination/('upstream_subset_'+name))
    if (package/'ohr/manifest.json').is_file():
        copy_verified(package/'ohr/manifest.json',destination/'ohr/upstream_subset_manifest.json')
    database=destination/'chinook/Chinook.sqlite'
    with closing(sqlite3.connect(f'file:{database.as_posix()}?mode=ro',uri=True)) as db:
        assert db.execute('PRAGMA integrity_check').fetchone()[0]=='ok'
        assert not db.execute('PRAGMA foreign_key_check').fetchall()
        names=[r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")]
        counts={name:db.execute('SELECT COUNT(*) FROM "'+name.replace('"','""')+'"').fetchone()[0] for name in names}
        dates=list(db.execute('SELECT MIN(InvoiceDate),MAX(InvoiceDate) FROM Invoice').fetchone())
    manifest={'source_package':str(package),'source_kind':'official page-only ground-truth text; local package provenance',
        'chinook':{'sha256':digest(database),'customers':counts['Customer'],'invoices':counts['Invoice'],
                   'table_counts':counts,'date_range':dates},
        'ohr':{'documents':len(sources),'pages':sum(s['pages'] for s in sources),
               'categories':dict(Counter(s['category'] for s in sources)),'sources':sources,
               'pdf_documents':len(pdfs),'qa_indexed':False,'noise_indexed':False,
               'noise_original_path':str(package/'ohr/retrieval'),
               'noise_variants':{d.name:len(list(d.rglob('*.json'))) for d in (destination/'ohr/retrieval').iterdir()
                                 if d.is_dir() and d.name!='gt'}}}
    (destination/'manifest.json').write_text(json.dumps(manifest,ensure_ascii=False,indent=2),encoding='utf-8')
    (destination/'ohr/manifest.json').write_text(json.dumps(manifest['ohr'],ensure_ascii=False,indent=2),encoding='utf-8')
    return manifest

def activate(destination,manifest):
    env=PROJECT_ROOT/'.env'
    original=env.read_text(encoding='utf-8')
    previous={key:(m[1] if (m:=re.search(rf'(?m)^{key}=(.*)$',original)) else None) for key in ENV_KEYS}
    (destination/'previous_profile.json').write_text(json.dumps(previous,ensure_ascii=False,indent=2),encoding='utf-8')
    label=(f"Chinook 公开样例：{manifest['chinook']['customers']}位客户、{manifest['chinook']['invoices']}张发票；"
           f"OHR-Bench：{manifest['ohr']['documents']}份官方校对文本、{manifest['ohr']['pages']}页、7类主题；非本地OCR结果")
    values={'DATABASE_PATH':destination/'chinook/Chinook.sqlite','DOCUMENTS_PATH':destination/'ohr/documents',
            'INDEXES_PATH':destination/'ohr/indexes','PARSED_PATH':destination/'ohr/parsed',
            'CONFIG_PATH':PROJECT_ROOT/'config/public_benchmark','DATASET_PROFILE':'public','DEMO_DATA_LABEL':label,
            'PUBLIC_DATASET_ROOT':destination}
    changed=original
    for key,value in values.items():
        rendered=json.dumps(str(value).replace('\\','/'),ensure_ascii=False)
        replacement=f'{key}={rendered}\n'
        pattern=rf'(?m)^{key}=.*(?:\n|$)'
        changed=re.sub(pattern,lambda _:replacement,changed) if re.search(pattern,changed) else changed+('' if changed.endswith('\n') else '\n')+replacement
    def unaffected(text):return [line for line in text.splitlines() if line.split('=',1)[0] not in ENV_KEYS]
    assert unaffected(original)==unaffected(changed),'Unexpected credential or unrelated configuration change'
    temporary=env.with_suffix('.dataset.tmp')
    temporary.write_text(changed,encoding='utf-8');temporary.replace(env)
    get_settings.cache_clear()

def main():
    p=argparse.ArgumentParser();p.add_argument('--source',type=Path,required=True);p.add_argument('--destination',type=Path,required=True)
    p.add_argument('--activate',action='store_true');p.add_argument('--resume',action='store_true');args=p.parse_args()
    manifest=import_package(args.source,args.destination,args.resume)
    settings=get_settings()
    settings.documents_path=args.destination.resolve()/'ohr/documents'
    settings.indexes_path=args.destination.resolve()/'ohr/indexes'
    from app.rag.indexer import get_local_index
    data=get_local_index()
    expected=sum(s['has_text'] for s in manifest['ohr']['sources'])
    if data['errors'] or not data['sources'] or len(data['sources'])!=expected:
        raise ValueError('Ingestion failed; current .env configuration was not changed')
    manifest['ingestion']={'documents':len(data['sources']),'chunks':len(data['chunks']),
                           'skipped_sources':data.get('skipped_sources',[]),
                           'fingerprint':data['fingerprint'],'mode':data['mode'],'errors':data['errors']}
    (args.destination/'manifest.json').write_text(json.dumps(manifest,ensure_ascii=False,indent=2),encoding='utf-8')
    if args.activate:activate(args.destination.resolve(),manifest)
    print(json.dumps({'chinook':manifest['chinook'],'ohr':{k:v for k,v in manifest['ohr'].items() if k!='sources'},
                      'ingestion':manifest['ingestion'],'activated':args.activate},ensure_ascii=False),flush=True)

if __name__=='__main__':main()
