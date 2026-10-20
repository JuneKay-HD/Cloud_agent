"""A reversible search view. Original evidence text and its offsets never change."""
import re
from dataclasses import dataclass

SEARCH_TEXT_VERSION = 'mapped-soft-wrap-v1'


@dataclass
class SearchView:
    text: str
    spans: list[tuple[int, int]]

    def original_span(self, start, end):
        if not 0 <= start < end <= len(self.spans):
            raise ValueError('invalid_search_span')
        return self.spans[start][0], self.spans[end - 1][1]


def search_view(text):
    # Only measured character edits: join alphabetic line-wrap hyphens and
    # collapse whitespace. Never reorder columns or invent an article heading.
    output, spans = [], []
    pattern = r'(?<=[A-Za-z])-\r?\n[ \t]*(?=[a-z])|\s+|[^\s]'
    for match in re.finditer(pattern, text):
        value = match.group()
        if value.startswith('-') and '\n' in value:
            continue
        output.append(' ' if value.isspace() else value)
        spans.append((match.start(), match.end()))
    return SearchView(''.join(output), spans)


def search_text(chunk):
    # Indexing needs the same normalized text, without allocating one offset
    # tuple per character. Citation callers use search_view for the mapping.
    text = re.sub(r'(?<=[A-Za-z])-\r?\n[ \t]*(?=[a-z])', '', chunk['text'])
    return re.sub(r'\s+', ' ', text)


def article_key(chunk):
    # Section boundaries are only a conservative proxy, not proven article IDs.
    return (chunk.get('doc_id'), chunk.get('page_no'), chunk.get('source_hash'), chunk.get('section'))
