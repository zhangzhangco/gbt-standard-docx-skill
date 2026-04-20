#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import re
from pathlib import Path

import yaml


RFC_RE = re.compile(r"\bRFC\s+(\d+)\b", re.IGNORECASE)
YEAR_RE = re.compile(r"(19|20)\d{2}")
SMPTE_RE = re.compile(r"\bSMPTE\s+(?:ST\s+)?([0-9]+(?:-[0-9]+)?[A-Z]?)", re.IGNORECASE)
ISO_RE = re.compile(r"\bISO(?:/IEC)?\s+([0-9]+(?:-[0-9]+)?)", re.IGNORECASE)
IEC_RE = re.compile(r"\bIEC\s+([0-9]+(?:-[0-9]+)?)", re.IGNORECASE)
GB_RE = re.compile(r"\bGB(?:/T)?\s+([0-9]+(?:\.[0-9]+)?)", re.IGNORECASE)


def load_data(path: Path):
    text = path.read_text(encoding="utf-8")
    if path.suffix.lower() == ".json":
        return json.loads(text)
    return yaml.safe_load(text)


def classify_reference(text: str) -> dict:
    stripped = " ".join(str(text).split())
    years = YEAR_RE.findall(stripped)
    has_year = bool(re.search(r"(19|20)\d{2}", stripped))
    is_dynamic_registry = any(token in stripped.upper() for token in ("IANA", "REGISTRY", "ASSIGNMENT", "PORT NUMBER"))

    source_type = "other"
    identifier = ""
    if match := RFC_RE.search(stripped):
        source_type = "rfc"
        identifier = f"RFC {match.group(1)}"
    elif match := SMPTE_RE.search(stripped):
        source_type = "smpte"
        identifier = f"SMPTE {match.group(1)}"
    elif match := ISO_RE.search(stripped):
        source_type = "iso"
        identifier = f"ISO {match.group(1)}"
    elif match := IEC_RE.search(stripped):
        source_type = "iec"
        identifier = f"IEC {match.group(1)}"
    elif match := GB_RE.search(stripped):
        source_type = "gb"
        identifier = f"GB {match.group(1)}"

    reference_style = "undated"
    if has_year and not is_dynamic_registry:
        reference_style = "dated"

    verification_needed = False
    reasons: list[str] = []
    recommendation = "可保持原样"

    if reference_style == "undated":
        verification_needed = True
        reasons.append("不注日期引用应核验当前最新版本或当前注册表状态。")
        recommendation = "建议联网核验当前有效版本"

    if source_type in {"rfc", "smpte", "iso", "iec"} and reference_style == "dated":
        verification_needed = True
        reasons.append("注日期引用建议核验是否已有新版、废止或历史化状态。")
        recommendation = "如拟升级版本将构成技术差异，需人工确认"

    if source_type == "rfc":
        verification_needed = True
        reasons.append("RFC 还应核验是否被 Obsoleted、Updated 或转为 Historic。")
        recommendation = "需联网核验 RFC 状态，升级版本前先评估技术差异"

    if is_dynamic_registry:
        verification_needed = True
        reasons.append("动态注册表或在线分配表不应仅按历史文本处理，应核验当前状态。")
        recommendation = "建议改为不注日期引用并联网核验当前页面状态"

    return {
        "text": stripped,
        "source_type": source_type,
        "identifier": identifier,
        "reference_style": reference_style,
        "verification_needed": verification_needed,
        "reasons": reasons,
        "recommendation": recommendation,
    }


def build_report(data: dict, input_path: Path) -> dict:
    items = data.get("sections", {}).get("normative_references", {}).get("items", []) or []
    entries = []
    for idx, item in enumerate(items):
        entry = classify_reference(str(item))
        entry["index"] = idx + 1
        entries.append(entry)
    return {
        "input": str(input_path),
        "count": len(entries),
        "verification_needed_count": sum(1 for entry in entries if entry["verification_needed"]),
        "entries": entries,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", required=True)
    parser.add_argument("--output")
    args = parser.parse_args()

    input_path = Path(args.input)
    data = load_data(input_path)
    report = build_report(data, input_path)

    if args.output:
        Path(args.output).write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    else:
        print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
