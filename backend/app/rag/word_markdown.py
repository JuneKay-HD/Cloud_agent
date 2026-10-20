"""Word body to Markdown, with inline image OCR in original block order."""
import re

from docx import Document
from docx.oxml.ns import qn
from docx.table import Table

from app.config import get_settings
from app.rag.ocr import recognize_image


def parse_word(path, *, on_ocr=None):
    document = Document(path)
    image_count = len(document.element.body.xpath('.//a:blip')) + len(document.element.body.xpath('.//*[local-name()="imagedata"]'))
    if image_count > get_settings().ocr_max_pages:
        raise ValueError('DOCX图片数超过OCR预算，请拆分文档')
    images, warnings, ranges, list_counts = [], [], [], {}
    offset = 0
    pieces = []

    def paragraph_text(paragraph):
        nonlocal offset
        values, local_ranges = [], []
        cursor = 0
        for node in paragraph._p.iter():
            value = ''
            if node.tag == qn('w:t'):
                value = node.text or ''
            elif node.tag == qn('w:tab'):
                value = '\t'
            elif node.tag in {qn('w:br'), qn('w:cr')}:
                value = '\n'
            elif node.tag in {qn('a:blip'), '{urn:schemas-microsoft-com:vml}imagedata'}:
                rid = node.get(qn('r:embed')) or node.get(qn('r:id'))
                relation = paragraph.part.rels.get(rid)
                if not relation or relation.is_external:
                    raise ValueError('DOCX含外部或不可读取的图片，未下载未知地址，请嵌入图片后上传')
                image = recognize_image(relation.target_part.blob, source_label=f'DOCX原图{len(images) + 1}')
                image['image_id'] = len(images) + 1
                images.append(image)
                if on_ocr is not None:
                    on_ocr(image)
                warnings.extend(image['parsing_warnings'])
                value = '\n\n' + (image['text'] or '[此图片未提取到文字，需核对原图]') + '\n\n'
                if not image['text']:
                    local_ranges.append({'start_char': cursor, 'end_char': cursor + len(value)})
                for item in image['ocr_uncertain_ranges']:
                    local_ranges.append({**item, 'start_char': cursor + 2 + item['start_char'],
                                         'end_char': cursor + 2 + item['end_char']})
            values.append(value)
            cursor += len(value)
        text = ''.join(values)
        style = paragraph.style.name if paragraph.style else ''
        heading = re.search(r'Heading\s*(\d+)', style, re.I)
        prefix = '#' * min(6, int(heading.group(1))) + ' ' if heading else '# ' if style == 'Title' else ''
        if not prefix:
            properties = paragraph._p.pPr
            outline = properties.find(qn('w:outlineLvl')) if properties is not None else None
            if outline is not None and 0 <= int(outline.get(qn('w:val'))) < 6:
                prefix = '#' * (int(outline.get(qn('w:val'))) + 1) + ' '
        if not prefix:
            properties = paragraph._p.pPr
            numbering = properties.numPr if properties is not None else None
            if numbering is None and paragraph.style is not None and paragraph.style.element.pPr is not None:
                numbering = paragraph.style.element.pPr.numPr
            key, depth, numbered = style, 0, 'List Number' in style
            if numbering is not None and numbering.numId is not None:
                num_id = numbering.numId.val
                if num_id == 0:
                    numbered = False
                if num_id != 0:
                    depth = numbering.ilvl.val if numbering.ilvl is not None else 0
                    key = (num_id, depth)
                    root = document.part.numbering_part.element
                    number = root.xpath(f'./w:num[@w:numId="{num_id}"]/w:abstractNumId')
                    levels = root.xpath(f'./w:abstractNum[@w:abstractNumId="{number[0].val}"]/w:lvl[@w:ilvl="{depth}"]/w:numFmt') if number else []
                    numbered = bool(levels and levels[0].get(qn('w:val')) != 'bullet')
            if numbered:
                list_counts[key] = list_counts.get(key, 0) + 1
                prefix = '  ' * depth + f'{list_counts[key]}. '
            elif 'List Bullet' in style or (numbering is not None and numbering.numId is not None and numbering.numId.val != 0):
                prefix = '  ' * depth + '- '
        value = prefix + text
        return value, ([{'start_char': 0, 'end_char': len(value),
                         'reason': 'uncertain_image_in_word_paragraph'}] if local_ranges else [])

    def table_text(table):
        rows = []
        uncertain = False
        seen_cells = set()
        for row in table.rows:
            cells = []
            for cell in row.cells:
                # python-docx repeats merged cells; transcribe their contents once.
                if cell._tc in seen_cells:
                    cells.append('')
                    continue
                seen_cells.add(cell._tc)
                parts = []
                for block in cell.iter_inner_content():
                    value, weak = table_text(block) if isinstance(block, Table) else paragraph_text(block)
                    parts.append(value)
                    uncertain = uncertain or bool(weak)
                cells.append('<br>'.join(parts).replace('|', '\\|').replace('\n', '<br>'))
            rows.append('| ' + ' | '.join(cells) + ' |')
        if not rows:
            return '', []
        rows.insert(1, '| ' + ' | '.join('---' for _ in table.rows[0].cells) + ' |')
        text = '\n'.join(rows)
        return text, ([{'start_char': 0, 'end_char': len(text), 'reason': 'uncertain_image_in_word_table'}] if uncertain else [])

    spans, unsafe = [], set()
    for block in document.iter_inner_content():
        value, weak = table_text(block) if isinstance(block, Table) else paragraph_text(block)
        if not value.strip():
            continue
        if pieces:
            offset += 2
        if weak:
            unsafe.add(len(pieces))
        spans.append((offset, offset + len(value)))
        ranges.extend({**r, 'start_char': offset + r['start_char'], 'end_char': offset + r['end_char']} for r in weak)
        pieces.append(value)
        offset += len(value)
        if offset > 100_000:
            raise ValueError('DOCX文字超过10万字，请拆分文档')
    for index in list(unsafe):
        for neighbor in (index - 1, index + 1):
            if 0 <= neighbor < len(pieces) and not re.match(r'^#{1,6}\s', pieces[max(index, neighbor)]):
                unsafe.add(neighbor)
    ranges = [{'start_char': spans[i][0], 'end_char': spans[i][1],
               'reason': 'uncertain_word_paragraph_or_adjacent_context'} for i in sorted(unsafe)]
    return {'page_no': None, 'text': '\n\n'.join(pieces), 'text_format': 'markdown',
            'extraction_method': 'docx', 'embedded_images': images,
            'ocr_uncertain_ranges': ranges, 'parsing_warnings': list(dict.fromkeys(warnings))}
