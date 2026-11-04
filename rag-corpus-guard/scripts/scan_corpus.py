#!/usr/bin/env python3
"""Проверка материалов RAG-корпуса перед загрузкой.

Ищет скрытый текст в HTML, невидимые символы, фразы-инструкции,
персональные данные и посторонние ссылки. Работает локально, без сети.

    python scan_corpus.py <папка> [--report report.md] [--domain example.ru]

Код выхода: 0 — находок высокого уровня нет, 1 — есть, 2 — ошибка запуска.
"""
import argparse
import re
import sys
from pathlib import Path
from urllib.parse import urlparse

EXTENSIONS = {".md", ".txt", ".html", ".htm"}
HIGH, CHECK = "высокий", "проверить"

HIDDEN_HTML = [
    (r"display\s*:\s*none", "скрытый блок (display:none)"),
    (r"visibility\s*:\s*hidden", "скрытый блок (visibility:hidden)"),
    (r"font-size\s*:\s*0(?:px|pt|em|rem)?\s*[;\"']", "текст нулевого размера"),
    (r"opacity\s*:\s*0(?:\.0+)?\s*[;\"']", "прозрачный текст (opacity:0)"),
    (r"color\s*:\s*(?:#fff(?:fff)?|white|rgba?\(\s*255\s*,\s*255\s*,\s*255)", "белый текст"),
    (r"<!--.*?-->", "HTML-комментарий"),
]

INVISIBLE = re.compile("[​‌‍⁠﻿‪-‮⁦-⁩]")

INSTRUCTION_PHRASES = [
    r"игнорир\w*\s+(?:все\s+|всё\s+)?(?:предыдущ\w+\s+|прошл\w+\s+)?(?:инструкци|указани|правил)",
    r"забудь\w*\s+(?:все\s+|всё\s+)?(?:инструкци|указани|правил|что\s+тебе)",
    r"ты\s+теперь\b",
    r"(?:скажи|говори|сообщи|ответь)\s+(?:всем\s+)?(?:клиент\w*|пользовател\w*)",
    r"(?:ии|ai)[-\s]?(?:ассистент|бот|модел)\w*\s*[:,]",
    r"(?:системн\w+|system)\s+(?:промпт|prompt)",
    r"ignore\s+(?:all\s+)?(?:previous|prior|above)\s+instructions",
    r"disregard\s+(?:all\s+)?(?:previous|prior|above)",
    r"you\s+are\s+now\b",
    r"new\s+instructions\s*:",
]

PHONE = re.compile(r"(?<!\d)(?:\+7|8)[\s\-(]*\d{3}[\s\-)]*\d{3}[\s\-]*\d{2}[\s\-]*\d{2}(?!\d)")
EMAIL = re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+")
URL = re.compile(r"https?://[^\s\"'<>)\]]+")


def mask(value: str) -> str:
    if "@" in value:
        name, _, host = value.partition("@")
        return name[:1] + "***@" + host
    digits = re.sub(r"\D", "", value)
    return digits[:2] + "*" * max(len(digits) - 4, 0) + digits[-2:]


def line_of(text: str, pos: int) -> int:
    return text.count("\n", 0, pos) + 1


def snippet(text: str, start: int, end: int, width: int = 60) -> str:
    s = text[max(0, start - 20):min(len(text), end + 20)]
    s = INVISIBLE.sub("·", s)
    s = re.sub(r"\s+", " ", s).strip()
    return (s[:width] + "…") if len(s) > width else s


def scan_file(path: Path, domain: str | None):
    text = path.read_text(encoding="utf-8", errors="replace")
    found = []
    is_html = path.suffix.lower() in {".html", ".htm"} or "<html" in text[:500].lower()

    if is_html:
        for pattern, label in HIDDEN_HTML:
            for m in re.finditer(pattern, text, re.IGNORECASE | re.DOTALL):
                level = CHECK if label == "HTML-комментарий" and len(m.group(0)) < 40 else HIGH
                found.append((line_of(text, m.start()), level, label, snippet(text, m.start(), m.end())))

    for m in INVISIBLE.finditer(text):
        found.append((line_of(text, m.start()), HIGH, f"невидимый символ U+{ord(m.group(0)):04X}",
                      snippet(text, m.start(), m.end())))

    for pattern in INSTRUCTION_PHRASES:
        for m in re.finditer(pattern, text, re.IGNORECASE):
            found.append((line_of(text, m.start()), HIGH, "фраза-инструкция", snippet(text, m.start(), m.end())))

    contact_page = any(w in path.stem.lower() for w in ("contact", "kontakt", "контакт"))
    for regex, label in ((PHONE, "телефон"), (EMAIL, "email")):
        for m in regex.finditer(text):
            level = CHECK if contact_page else HIGH
            found.append((line_of(text, m.start()), level, label, mask(m.group(0))))

    if domain:
        for m in URL.finditer(text):
            host = (urlparse(m.group(0)).hostname or "").lower()
            if host and host != domain and not host.endswith("." + domain):
                found.append((line_of(text, m.start()), CHECK, "посторонний домен", host))

    return found


def main() -> int:
    ap = argparse.ArgumentParser(description="Проверка материалов RAG-корпуса")
    ap.add_argument("folder")
    ap.add_argument("--report", help="путь к отчёту .md")
    ap.add_argument("--domain", help="домен компании, например example.ru")
    args = ap.parse_args()

    root = Path(args.folder)
    if not root.is_dir():
        print(f"Папка не найдена: {root}", file=sys.stderr)
        return 2
    domain = args.domain.lower().removeprefix("www.") if args.domain else None

    files = sorted(p for p in root.rglob("*") if p.is_file() and p.suffix.lower() in EXTENSIONS)
    rows = []
    for f in files:
        for line, level, kind, detail in sorted(scan_file(f, domain), key=lambda r: r[0]):
            rows.append((str(f.relative_to(root)), line, level, kind, detail))

    high = sum(1 for r in rows if r[2] == HIGH)
    lines = [
        f"# Проверка корпуса: {root}",
        f"Файлов: {len(files)} · находок: {len(rows)} · высокого уровня: {high}",
        "",
    ]
    if rows:
        lines += ["| Файл | Строка | Уровень | Тип | Фрагмент |", "|---|---|---|---|---|"]
        for path, line, level, kind, detail in rows:
            lines.append(f"| {path} | {line} | {level} | {kind} | {detail.replace('|', '/')} |")
    else:
        lines.append("Типовых проблем не найдено. Это не гарантия безопасности — пройдите ручную проверку из SKILL.md.")
    report = "\n".join(lines) + "\n"

    if args.report:
        Path(args.report).write_text(report, encoding="utf-8")
        print(f"Отчёт: {args.report}")
    print(report)
    return 1 if high else 0


if __name__ == "__main__":
    sys.exit(main())
