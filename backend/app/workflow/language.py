"""Conservative wording normalization; never remove unknown conditions."""
import re


def normalize_question(question: str) -> str:
    question = question.replace("销受额", "销售额")
    question = re.sub(r"(?:一共|总共)?(?:卖了|卖出|销售了)多少钱", "销售额", question)
    question = re.sub(r"(?:怎么算|如何计算|怎么计算|怎样计算)", "的统计口径", question)
    return question


def mentioned_countries(question: str, countries: dict) -> list[str]:
    matches = []
    for canonical, aliases in countries.items():
        positions = []
        for alias in [canonical, *aliases]:
            pattern = re.escape(alias)
            if alias.isascii():
                pattern = rf"(?<![A-Za-z]){pattern}(?![A-Za-z])"
            match = re.search(pattern, question, re.I)
            if match:
                positions.append(match.start())
        if positions:
            matches.append((min(positions), canonical))
    return [country for _, country in sorted(matches)]
