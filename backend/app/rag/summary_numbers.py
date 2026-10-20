"""Numeric facts normalized without treating digits in method names as counts."""
import re
import html
from datetime import date
import unicodedata
from decimal import Decimal, localcontext

DIGITS = dict(zip('零〇一二两三四五六七八九', [0,0,1,2,2,3,4,5,6,7,8,9]))
UNITS = {'十':10, '百':100, '千':1000, '万':10000, '亿':100000000}
CN = '零〇一二两三四五六七八九十百千万亿'


def normalize_compact_quantities(text):
    """Recover explicit date/duration tokens joined by PDF extraction.

    This is a comparison-only view. Never rewrite stored quotes or offsets, and
    never split arbitrary model names such as BM25, NL2SQL or Project2015.
    """
    months = 'january|february|march|april|may|june|july|august|september|october|november|december'
    text = re.sub(r'(forperiods?)(?=(?:' + months + r'))', r'\1 ', text, flags=re.I)
    text = re.sub(r'\b(forperiods?|periods?|from|to|last|since|dated|on|in|by|until|during|through|before|after)(?=(?:' + months + r'))',
                  r'\1 ', text, flags=re.I)
    text = re.sub(r'\b(' + months + r')(?=to(?:' + months + r'))', r'\1 ', text, flags=re.I)
    text = re.sub(r'\bto(?=(?:' + months + r'))', 'to ', text, flags=re.I)
    text = re.sub(r'\b(' + months + r')(\d{4}(?!\d)|\d{1,2}(?!\d))', r'\1 \2 ', text, flags=re.I)
    month_numbers = {m: i for i, m in enumerate(months.split('|'), 1)}
    text = re.sub(r'\b(' + months + r')\s+to\s+(' + months + r')\b',
                  lambda m: f'{month_numbers[m[1].lower()]} month to {month_numbers[m[2].lower()]} month',
                  text, flags=re.I)
    cardinals = 'one|two|three|four|five|six|seven|eight|nine|ten|eleven|twelve'
    quantity = r'(?:\d+(?:\.\d+)?|' + cardinals + ')'
    units = r'(?:days?|weeks?|months?|years?|hours?|minutes?|semesters?)'
    text = re.sub(r'\bthe(?=\d+semesters?)', 'the ', text, flags=re.I)
    text = re.sub(r'last(?=\d+years?\b)', 'last ', text, flags=re.I)
    text = re.sub(r'(within|after|before|for)(?=' + quantity + units + r')', r'\1 ', text, flags=re.I)
    text = re.sub(r'\b(' + quantity + ')(' + units + r')(?=\b|upon|after|before|of|in)',
                  r'\1 \2 ', text, flags=re.I)
    # Percent signs and explicit comparative/quantity phrases provide a unit or
    # grammatical boundary even when OCR removed the spaces on both sides.
    text = re.sub(r'([A-Za-z])(\d+(?:\.\d+)?\s*%)', r'\1 \2 ', text)
    text = re.sub(r'%(?=[A-Za-z])', '% ', text)
    number = r'\d[\d,]*(?:\.\d+)?'
    prefix = (r'\b(?:at|by|only|than|within|after|before)|'
              r'(?:decrease|increase|lower|higher|compared)(?:at|by|to)|'
              r'(?:last)?year[’\x27]s|(?:semester|period)\s*of')
    text = re.sub('(' + prefix + ')(' + number + ')', r'\1 \2 ', text, flags=re.I)
    text = re.sub(r'with(\d+)or(more|less)', r'with \1 or \2 ', text, flags=re.I)
    # Explicit plural magnitude, not an exact number of billions.
    text = re.sub(r'\bmulti[- ]?(billion|million|thousand)(?=\b|-)', r'\1s', text, flags=re.I)
    return text


def chinese_integer(text):
    if not any(c in UNITS for c in text):
        return int(''.join(str(DIGITS[c]) for c in text))
    total, section, current = 0, 0, 0
    for c in text:
        if c in DIGITS:
            current = DIGITS[c]
        elif UNITS[c] < 10000:
            section += (current or 1) * UNITS[c]
            current = 0
        else:
            total = ((total + section + current) * UNITS[c] if c == '亿'
                     else total + (section + current) * UNITS[c])
            section, current = 0, 0
    return total + section + current


def normalize_numeric_layout(text):
    """Normalize presentation only; never evaluate arbitrary document formulas.

    Work on a comparison copy. Exact quotes, hashes and page offsets remain intact.
    Unknown math operators are retained rather than joining their operands.
    """
    text = html.unescape(text)
    # Only explicit thin-space thousands groups; ordinary spaces may separate cells.
    text = re.sub(r'(?<=\d)(?:\\,|\\thinspace\b|\{,\}|[\u00a0\u202f])(?=\d{3}(?:\D|$))', ',', text)
    text = unicodedata.normalize('NFKC', text.replace('，', '；').replace('、', '；'))
    text = text.replace('\u2212', '-').replace('\u00ad', '').replace('\u200b', '')
    # OCR/Markdown often represents an empty-base footnote as ${ }^{12}$.
    # A genuine power such as 10^{12} must remain a numeric expression.
    text = re.sub(r'\{\s*\}\s*\^\s*\{\s*\d+\s*\}', ' ', text)
    for _ in range(4):
        updated = re.sub(r'\\(?:text|textrm|textbf|textit|mathrm|mathbf|mathit|operatorname)\s*\{([^{}]*)\}',
                         lambda m: m[1], text)
        if updated == text:
            break
        text = updated
    # TeX spacing and math delimiters do not separate a value from its magnitude.
    text = re.sub(r'\\(?:,|;|!|:|quad\b|qquad\b|enspace\b|thinspace\b|\s)', ' ', text)
    text = re.sub(r'\\[\[\]()]', ' ', text)
    text = text.replace(r'\$', '$').replace(r'\%', '%')
    text = re.sub(r'(\$\s*\d+(?:\.\d+)?)\s*([BM])\b',
                  lambda m: m[1] + (' billion' if m[2].upper() == 'B' else ' million'), text, flags=re.I)
    text = text.replace('$', ' ')
    text = re.sub(r'\{\s*([-+]?\d[\d.,\s]*)\s*\}', r'\1', text)
    number = r'[-+]?(?:\d+(?:\.\d+)?|\.\d+)'

    def scientific(match):
        exponent = int(match[2])
        if abs(exponent) > 30:
            return match[0]  # Do not expand unbounded document expressions.
        with localcontext() as context:
            context.prec = max(50, len(match[1]) + abs(exponent) + 2)
            return format(Decimal(match[1]) * Decimal(10) ** exponent, 'f')

    text = re.sub(r'(?<![\w.])(' + number + r')\s*(?:\\times|\\cdot|×|·|\*)\s*10\s*\^\s*\{?\s*([-+]?\d+)\s*\}?',
                  scientific, text)
    text = re.sub(r'(?<![\w.])(' + number + r')[eE]([-+]?\d+)(?!\w)', scientific, text)

    def calendar_date(match):
        year, month, day = map(int, match.groups())
        try:
            date(year, month, day)
        except ValueError:
            return match[0]
        return f'{year}年{month}月{day}日'

    # Only unambiguous year-first dates; 03/04 is not guessed as March or April.
    text = re.sub(r'(?<!\d)(\d{4})[-/](\d{1,2})[-/](\d{1,2})(?!\d)', calendar_date, text)
    text = re.sub(r'(?<!\d)([12]\d{3})\s*[-–—]\s*([12]\d{3})(?!\d)', r'\1 至 \2', text)
    text = re.sub(r'(?<!\d)(\d{1,2})(?:st|nd|rd|th)\b', r'\1', text, flags=re.I)
    # Decimal percentages and quantities in Chinese are translation, not new facts.
    def chinese_decimal(match):
        return str(chinese_integer(match[1])) + '.' + ''.join(str(DIGITS[c]) for c in match[2])
    text = re.sub(rf'([{CN}]+)点([零〇一二两三四五六七八九]+)', chinese_decimal, text)
    text = re.sub(r'百分之\s*([-+]?\d+(?:\.\d+)?)', r'\1%', text)
    return text

def normalize_temporal_translation(text, sources):
    """Restore an idiomatic time span to its source precision; never invent years.

    Only a cited superlative explicitly scoped to a generation can license this
    conservative repair. Exact years, quantities and unrelated claims stay intact.
    """
    quotes = [s.get('quote', '') for s in sources]
    support = next((q for q in quotes if re.search(
        r'\b(?:most|least|worst|best|biggest|largest|smallest|greatest|first)\b[\s\S]{0,250}?\bin (?:a|one) generation\b',
        q, re.I)), None)
    if not support:
        return text, []
    edits = []
    pattern = r'(?:过去|近)?(?:数十|几十)年(?:以来|来|间|中)(?=.{0,12}最)'
    def restore(match):
        edits.append({'original':match[0], 'corrected':'一代人以来', 'source_quote':support})
        return '一代人以来'
    return re.sub(pattern, restore, text), edits


def numeric_values(text):
    # Parser labels describe layout, not source quantities. Publication page
    # numbers and all actual document text remain guarded.
    text = re.sub(r'\[(?:区域\d+)?第\d+栏[^\]\n]*\]|\[表格\d+[^\]\n]*\]', '', text)
    # Chinese list punctuation must never concatenate adjacent numbers (3，10).
    text = normalize_compact_quantities(normalize_numeric_layout(text))
    text = re.sub(r'(?<=[A-Za-z])-\s*\n\s*(?=[A-Za-z])', '', text)
    approximations = set()
    # Compound approximations must be consumed before individual magnitudes:
    # hundreds of millions = 数亿, rather than unrelated 数百 and 数百万.
    def compound_approximation(match):
        base = {'tens':10, 'hundreds':100, 'thousands':1000}[match[1].lower()]
        scale = {'thousands':1000, 'millions':1000000, 'billions':1000000000}[match[2].lower()]
        approximations.add(f'approx:{base * scale}')
        return ' '
    text = re.sub(r'\b(tens|hundreds|thousands)\s+of\s+(thousands|millions|billions)\b',
                  compound_approximation, text, flags=re.I)
    def approximate_quantity(match):
        word = match[0].lower()
        magnitude = ({'tens':10, 'hundreds':100, 'thousands':1000, 'millions':1000000, 'billions':1000000000}.get(word)
                     if word[0].isascii() else {'十':10,'百':100,'千':1000,'万':10000,'十万':100000,'百万':1000000,
                           '千万':10000000,'亿':100000000,'十亿':1000000000,'百亿':10000000000,'千亿':100000000000}[word[1:]])
        approximations.add(f'approx:{magnitude}')
        return ' '
    text = re.sub(r'\b(?:tens|hundreds|thousands|millions|billions)\b|(?:数|几)(?:千万|百万|十万|十亿|百亿|千亿|十|百|千|万|亿)', approximate_quantity, text, flags=re.I)
    text = re.sub(r'(?<=\d),(?=\d{3}(?:\D|$))', '', text)
    text = re.sub(r'\b(USD|PHP|RMB|CNY|EUR|GBP|CAD|AUD|P)(?=\d)', r'\1 ', text)
    text = re.sub(r'((?:USD|PHP|RMB|CNY|EUR|GBP|CAD|AUD|美元|人民币|欧元|英镑)\s*\d+(?:\.\d+)?)\s*([BM])\b',
                  lambda m: m[1] + (' billion' if m[2].upper() == 'B' else ' million'), text, flags=re.I)
    text = re.sub(rf'百分之([{CN}]+)', lambda m: str(chinese_integer(m[1]))+'%', text)
    def chinese_quantity(match):
        # In 13.2万人, 万 is the magnitude suffix of the Arabic number.
        if match.start() and text[match.start()-1] in '0123456789.':
            return match[0]
        if not any(c in DIGITS or c in '十百千' for c in match[1]):
            return match[0]
        return str(chinese_integer(match[1]))
    text = re.sub(rf'([{CN}]+)(?=项|份|页|个|条|道|章|节|篇|次|年|月|日|天|小时|分钟|元|人|张|种|类|套|号|英里|公里|千米|米|周|本|匹|名|座|幅|场|枚|家|所|起|辆|位|岁|部)',
                  chinese_quantity, text)
    # Literal English quantities (seven miles -> 7英里) are translation, not new arithmetic.
    english = dict(zip('zero one two three four five six seven eight nine ten eleven twelve thirteen fourteen fifteen sixteen seventeen eighteen nineteen'.split(), range(20)))
    english.update(dict(zip('twenty thirty forty fifty sixty seventy eighty ninety'.split(), range(20, 100, 10))))
    # Parse cardinal phrases independently of the following noun/adjectives:
    # "two men", "three Iraqi men", "one hundred and twenty" are quantities.
    number_words = '|'.join(english)
    phrase_word = number_words + '|hundred|thousand|million|billion'
    pattern = r'\b(?:' + number_words + r')(?:[-\s]+(?:and\s+)?(?:' + phrase_word + r'))*\b'
    def english_quantity(match):
        total, current = 0, 0
        for word in re.split(r'[-\s]+', match[0].lower()):
            if word in english:
                current += english[word]
            elif word == 'hundred':
                current *= 100
            elif word in {'thousand', 'million', 'billion'}:
                total += current * {'thousand':1000, 'million':1000000, 'billion':1000000000}[word]
                current = 0
        return str(total + current)
    text = re.sub(pattern, english_quantity, text, flags=re.I)
    # A decade is an exact ten-year unit; unquantified plural decades remains
    # approximate. Resolve quantified plurals before the vague plural forms.
    time_scales = {'decade':10, 'decades':10, 'century':100, 'centuries':100,
                   'millennium':1000, 'millennia':1000}
    text = re.sub(r'\b(\d+(?:\.\d+)?)\s+(decades?|centur(?:y|ies)|millennium|millennia)\b',
                  lambda m: str(Decimal(m[1])*time_scales[m[2].lower()]) + ' years', text, flags=re.I)
    text = re.sub(r'\b(decade|century|millennium)\b',
                  lambda m: str(time_scales[m[1].lower()]) + ' years', text, flags=re.I)
    for unit, scale in [('decades',10), ('centuries',100), ('millennia',1000)]:
        if re.search(r'\b' + unit + r'\b', text, re.I):
            approximations.add(f'approx:{scale}')
    # Multiplicative/count adverbs carry real quantities even without digits.
    text = re.sub(r'\b(twice|thrice)\b', lambda m: {'twice':'2 times','thrice':'3 times'}[m[1].lower()], text, flags=re.I)
    # Month names are date units only beside a day/year, never the modal "may".
    months = {name: index for index, name in enumerate(
        'january february march april may june july august september october november december'.split(), 1)}
    abbreviations = {name[:3]: index for name, index in months.items() if name != 'may'}
    abbreviations['sept'] = 9
    # Expand only explicit dates; abbreviations in prose and the modal "may"
    # must not become factual quantities. Sept. 10 and 9月10日 are the same date.
    text = re.sub(r'\b(' + '|'.join(sorted(abbreviations, key=len, reverse=True)) + r')\.?\s+(\d{1,2}|\d{4})\b',
                  lambda m: str(abbreviations[m[1].lower()]) + ' month ' + m[2], text, flags=re.I)
    month_pattern = '|'.join(months)
    text = re.sub(r'\b(' + month_pattern + r')\s+(\d{1,2}|\d{4})\b',
                  lambda m: str(months[m[1].lower()]) + ' month ' + m[2], text, flags=re.I)
    text = re.sub(r'\b(\d{1,2})\s+(' + month_pattern + r')\b',
                  lambda m: m[1] + ' day ' + str(months[m[2].lower()]) + ' month', text, flags=re.I)
    text = re.sub(r'\b(as of|in|by|until|since|during|through|before|after)\s+(' + month_pattern + r')\b',
                  lambda m: m[1] + ' ' + str(months[m[2].lower()]) + ' month', text, flags=re.I)
    text = re.sub(r'\b(?:a|an)\s+(day|week|month|year|hour|minute)s?\b', r'1 \1', text, flags=re.I)
    text = re.sub(rf'(赛题|任务)([{CN}]+)', lambda m: m[1]+str(chinese_integer(m[2])), text)
    text = re.sub(r'[A-Za-z][A-Za-z0-9_.+-]*\d[A-Za-z0-9_.+-]*', ' ', text)
    scales = {'billion':10**9, 'million':10**6, 'thousand':10**3, '万亿':10**12, '千亿':10**11, '百亿':10**10, '十亿':10**9, '亿':10**8, '千万':10**7, '百万':10**6, '十万':10**5, '万':10**4}
    # BM25 / NL2SQL / model identifiers are names, checked by source entailment.
    # Vue 3, 10道, dates, percentages and actual numeric values remain guarded.
    return approximations | {Decimal(m[1]) * scales.get((m[2] or '').lower(), 1)
            for m in re.finditer(r'(?<![A-Za-z0-9_.])([-+]?(?:\d+(?:\.\d+)?|\.\d+))\s*'
                                 r'(billion\b|million\b|thousand\b|万亿|千亿|百亿|十亿|亿|千万|百万|十万|万)?', text, re.I)}
