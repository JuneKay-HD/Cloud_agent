"""Only explicit evidence refinements inherit an unresolved document question."""
import re


def resolve_evidence_followup(message, pending):
    if not pending:
        return message, None, False
    text = message.strip().rstrip("。！？?!")
    for label in pending.get("versions", []):
        if text in {"版本" + label, "用版本" + label, "按版本" + label, "采用版本" + label}:
            return pending["question"], label, True
    year = re.fullmatch(r"(?:按|用|适用|改为|改成)?\s*((?:19|20|21)\d{2})\s*年?(?:的)?", text)
    version = re.fullmatch(r"(?:按|用|采用)?\s*版本\s*([A-Za-z0-9_.-]+)", text)
    if year:
        base = re.sub(r"(?<!\d)(?:19|20|21)\d{2}年?", "", pending["question"])
        return f"{year.group(1)}年 {base}", None, True
    if version:
        return pending["question"], version.group(1), True
    if text in {"重新检索", "重新查询资料", "已补充资料"}:
        return pending["question"], pending.get("version"), True
    # A new question must not inherit previous entity/year/version restrictions.
    return message, None, False


def evidence_options(answer):
    if answer.status != "conflicting_evidence":
        return {}
    versions = sorted({c.get("source_metadata", {}).get("version") for c in answer.chunks
                       if c.get("source_metadata", {}).get("version")})
    years = sorted({c.get("source_metadata", {}).get("valid_from", "")[:4] for c in answer.chunks
                    if c.get("source_metadata", {}).get("valid_from")})
    result = {}
    if len(versions) > 1:
        result["指定文档版本"] = ["版本" + value for value in versions]
    if len(years) > 1:
        result["指定适用年份"] = [value + "年" for value in years]
    return result
