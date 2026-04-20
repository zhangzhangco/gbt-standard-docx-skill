#!/usr/bin/env python3
from __future__ import annotations

import argparse
from pathlib import Path

from docx import Document


FOCUS_PREFIXES = (
    "7.4",
    "7.5",
    "8.2",
    "8.3",
    "8.4",
    "8.5",
    "8.6",
    "8.7",
    "8.8",
    "9.4.2",
    "9.5",
    "9.6",
)


def load_paragraphs(docx_path: Path) -> list[str]:
    document = Document(str(docx_path))
    return [paragraph.text.strip() for paragraph in document.paragraphs if paragraph.text.strip()]


def filter_focus(paragraphs: list[str]) -> list[str]:
    focus: list[str] = []
    enabled = False
    for text in paragraphs:
        if text.startswith(FOCUS_PREFIXES):
            enabled = True
        elif enabled and text[:2].isdigit() and not text.startswith(FOCUS_PREFIXES):
            enabled = False
        if enabled:
            focus.append(text)
    return focus


def write_markdown(paragraphs: list[str], output_path: Path, title: str) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    lines = [f"# {title}", ""]
    for item in paragraphs:
        lines.append(item)
        lines.append("")
    output_path.write_text("\n".join(lines).strip() + "\n", encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", required=True)
    parser.add_argument("--full-output", required=True)
    parser.add_argument("--focus-output", required=True)
    args = parser.parse_args()

    paragraphs = load_paragraphs(Path(args.input))
    write_markdown(paragraphs, Path(args.full_output), "GB/T 1.1 提取全文")
    write_markdown(filter_focus(paragraphs), Path(args.focus_output), "GB/T 1.1 高频规则摘录")
    print("已生成知识抽取文件")


if __name__ == "__main__":
    main()
