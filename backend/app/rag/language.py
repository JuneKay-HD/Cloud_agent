"""Small, explicit vocabulary; no trained model or answer examples required."""
import json
import re
from functools import lru_cache

from app.config import get_settings


@lru_cache(maxsize=16)
def _read_aliases(path, modified):
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict) or not all(isinstance(k, str) and k and isinstance(v, str) and v for k, v in data.items()):
        raise ValueError("retrieval_aliases.json 必须是非空词语到规范词语的映射")
    return data


def canonicalize(text):
    # Meaning-preserving colloquial forms; never remove a negation or an amount.
    text = re.sub(r"(?:把)?钱退给客户(?:后)?|退钱(?:后)?", "退款", text)
    text = re.sub(r"会(?:跟着|随之)?减少吗", "是否扣除", text)
    path = get_settings().config_path / "retrieval_aliases.json"
    aliases = _read_aliases(path, path.stat().st_mtime_ns) if path.exists() else {}
    if not aliases:
        return text
    # One substitution pass avoids recursively expanding aliases or already canonical words.
    alternatives = [re.escape(k) + (r"(?!量)" if k in {"客户数", "发票数"} else "")
                    for k in sorted(aliases, key=len, reverse=True)]
    return re.sub("|".join(alternatives), lambda m: aliases[m.group()], text)
