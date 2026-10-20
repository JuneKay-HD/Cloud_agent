"""Name/acronym links extracted from actual source text, never model memory."""
import re
from functools import lru_cache

from app.workflow.document_routing import _index

# Require a literal definition and matching initials. Merely mentioning a short
# uppercase word cannot establish its meaning or an answer.
DEFINITION = re.compile(
    r'\b([A-Z][A-Za-z\'-]+(?:[ \t\n]+(?:[A-Z][A-Za-z\'-]+|and|of|the|for|\\?&)){1,12})'
    r'\s*\(([A-Z][A-Z0-9]{2,9})\)')


def defined_aliases(sources):
    aliases = {}
    for key, source in sources.items():
        for page in source.get('pages', []):
            text = re.sub(r'\n[ \t]*\n', '\x00', page.get('text', ''))
            for match in DEFINITION.finditer(text):
                name, alias = match.groups()
                words = re.findall(r'[A-Za-z][A-Za-z\'-]*', name)
                initials = ''.join(w[0].upper() for w in words if w.lower() not in {'and', 'of', 'the', 'for'})
                if initials != alias:
                    continue
                entry = {'doc_id': key, 'name': re.sub(r'\s+', ' ', name),
                         'alias': alias, 'page_no': page.get('page_no'), 'definition': match[0]}
                if entry not in aliases.setdefault(alias, []):
                    aliases[alias].append(entry)
    return aliases


@lru_cache(maxsize=4)
def _aliases(path, modified, size):
    return defined_aliases(_index(path, modified, size).get('sources', {}))


def mentioned_aliases(question):
    from app.config import get_settings
    path = get_settings().indexes_path / 'knowledge_index.json'
    try:
        stat = path.stat()
        aliases = _aliases(str(path), stat.st_mtime_ns, stat.st_size)
    except (OSError, ValueError, TypeError):
        return []
    # Preserve case: ordinary words such as "who" are not uppercase WHO.
    return [entry for alias, entries in aliases.items()
            if re.search(r'(?<![A-Za-z0-9_])' + re.escape(alias) + r'(?![A-Za-z0-9_])', question)
            for entry in entries]
