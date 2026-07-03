import re


def tokenize_text(text: str) -> set[str]:
    normalized = text.lower()
    ascii_tokens = set(re.findall(r"[a-z0-9_][a-z0-9_.-]*", normalized))
    chinese_chars = re.findall(r"[\u4e00-\u9fff]", normalized)
    chinese_bigrams = {
        f"{chinese_chars[index]}{chinese_chars[index + 1]}"
        for index in range(len(chinese_chars) - 1)
    }
    return ascii_tokens | set(chinese_chars) | chinese_bigrams
