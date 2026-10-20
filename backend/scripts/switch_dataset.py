"""Switch active dataset paths while preserving model credentials."""
import argparse
import json
from pathlib import Path
import re
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.config import PROJECT_ROOT, get_settings

KEYS = ('DATABASE_PATH','DOCUMENTS_PATH','INDEXES_PATH','PARSED_PATH','CONFIG_PATH',
        'DEMO_DATA_LABEL','DATASET_PROFILE','RAG_MIN_LEXICAL_COVERAGE','PUBLIC_DATASET_ROOT')

def _rendered(value) -> str:
    """Render a path as a PROJECT_ROOT-relative string so the delivery stays
    location-independent; absolute values (e.g. restored previous profile from
    another machine) are kept as-is."""
    path = Path(str(value))
    try:
        relative = path.relative_to(PROJECT_ROOT)
        return relative.as_posix()
    except ValueError:
        return str(path).replace('\\', '/')


def switch(profile):
    root = get_settings().public_dataset_root or PROJECT_ROOT / 'data/public_benchmark'
    env = PROJECT_ROOT / '.env'
    text = env.read_text(encoding='utf-8') if env.exists() else ''
    previous = root/'previous_profile.json'
    if profile == 'public':
        manifest = json.loads((root/'manifest.json').read_text(encoding='utf-8'))
        if not previous.exists():
            old={key: (match[1] if (match:=re.search(rf'(?m)^{key}=(.*)$',text)) else None) for key in KEYS}
            previous.write_text(json.dumps(old,ensure_ascii=False,indent=2),encoding='utf-8')
        values={
            'DATABASE_PATH':root/'chinook/Chinook.sqlite',
            'DOCUMENTS_PATH':root/'ohr/documents', 'INDEXES_PATH':root/'ohr/indexes',
            'PARSED_PATH':root/'ohr/parsed', 'CONFIG_PATH':PROJECT_ROOT/'config/public_benchmark',
            'DATASET_PROFILE':'public', 'RAG_MIN_LEXICAL_COVERAGE':'0.2',
            'PUBLIC_DATASET_ROOT':root,
            'DEMO_DATA_LABEL':f"Chinook 公开样例：{manifest['chinook']['customers']}位客户、{manifest['chinook']['invoices']}张发票（2009—2013）；OHR-Bench：{manifest['ohr']['documents']}份官方校对文本，非本地OCR结果",
        }
    elif profile == 'rule_demo':
        values={'DATABASE_PATH':PROJECT_ROOT/'data/rule_demo/business.db',
                'DOCUMENTS_PATH':PROJECT_ROOT/'data/rule_demo/documents',
                'INDEXES_PATH':PROJECT_ROOT/'data/rule_demo/indexes',
                'PARSED_PATH':PROJECT_ROOT/'data/rule_demo/parsed',
                'CONFIG_PATH':PROJECT_ROOT/'config','DATASET_PROFILE':'rule_demo',
                'RAG_MIN_LEXICAL_COVERAGE':'0.45',
                'DEMO_DATA_LABEL':'合成演示资料：10位客户、20张发票、4笔已确认退款；仅用于机制验证'}
    else:
        values=json.loads(previous.read_text(encoding='utf-8'))
    for key,value in values.items():
        pattern=rf'(?m)^{key}=.*(?:\n|$)'
        if value is None:
            text=re.sub(pattern,'',text)
            continue
        rendered = _rendered(value)
        replacement = f'{key}={rendered}\n' if profile=='previous' else f'{key}={json.dumps(rendered,ensure_ascii=False)}\n'
        text=re.sub(pattern,lambda _:replacement,text) if re.search(pattern,text) else text.rstrip()+'\n'+replacement
    env.write_text(text,encoding='utf-8')
    print(f'Active dataset profile: {profile}. Restart backend to reload configuration.')

if __name__=='__main__':
    parser=argparse.ArgumentParser()
    parser.add_argument('--profile',choices=['public','rule_demo','previous'],required=True)
    switch(parser.parse_args().profile)
