"""Cached trie extraction with original offsets and explicit ambiguity preservation."""
import json
import unicodedata
from functools import lru_cache


def normal(value):
    return unicodedata.normalize('NFKC',str(value)).casefold().strip()


@lru_cache(maxsize=8)
def _trie(serialized):
    entries=json.loads(serialized)
    if len(entries)>20000:raise ValueError('Element dictionary exceeds budget')
    trie={}
    for entry in entries:
        name=normal(entry['alias'])
        if not name or len(name)>150:raise ValueError('Invalid element alias length')
        node=trie
        for ch in name:node=node.setdefault(ch,{})
        node.setdefault('',[]).append(entry)
    return trie


def extract_elements(question, entries):
    if not isinstance(question,str) or len(question)>10000:raise ValueError('Question exceeds extraction budget')
    trie=_trie(json.dumps(entries,ensure_ascii=False,sort_keys=True))
    chars=[];offsets=[]
    for i,ch in enumerate(question):
        for c in unicodedata.normalize('NFKC',ch).casefold():chars.append(c);offsets.append((i,i+1))
    text=''.join(chars);hits=[]
    word=lambda c:c.isascii() and (c.isalnum() or c=='_')
    for start in range(len(text)):
        node=trie
        for end in range(start,min(len(text),start+150)):
            node=node.get(text[end])
            if node is None:break
            for entry in node.get('',[]):
                if entry['alias'].isascii() and ((start and word(text[start-1])) or (end+1<len(text) and word(text[end+1]))):continue
                left,right=offsets[start][0],offsets[end][1]
                hits.append({**{k:v for k,v in entry.items() if k!='alias'},'source':question[left:right],'start':left,'end':right,'matched_alias':entry['alias']})
    selected=[];seen=set()
    for hit in sorted(hits,key=lambda h:(-(h['end']-h['start']),h['start'])):
        key=json.dumps(hit,ensure_ascii=False,sort_keys=True)
        if key in seen:continue
        seen.add(key)
        if any(hit['start']<old['end'] and old['start']<hit['end'] and (hit['start'],hit['end'])!=(old['start'],old['end']) for old in selected):continue
        selected.append(hit)
        if len(selected)>200:raise ValueError('Too many element matches; clarify the question')
    return sorted(selected,key=lambda h:(h['start'],h['end']))
