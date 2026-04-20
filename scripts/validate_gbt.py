#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import date, datetime
from pathlib import Path

import yaml

BOOKMARK_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,39}$")
INLINE_FIELD_RE = re.compile(r"\{\{(ref|refnum|page):([A-Za-z0-9_]+)\}\}")
STANDARD_NUMBER_RE = re.compile(r"^[A-Z]+(?:/[A-Z])?\s+\d+(?:\.\d+)?[—-]\d{4}$")
DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
YEAR_MONTH_RE = re.compile(r"^\d{4}-\d{2}$")
ADOPTION_FOREWORD_PHRASES = {
    "IDT": "等同采用",
    "MOD": "修改采用",
    "NEQ": "非等效采用",
}


def load_data(path: Path):
    text = path.read_text(encoding="utf-8")
    if path.suffix.lower() == ".json":
        return json.loads(text)
    return yaml.safe_load(text)


def to_plain(obj):
    if isinstance(obj, dict):
        return {k: to_plain(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [to_plain(v) for v in obj]
    if isinstance(obj, (date, datetime)):
        return obj.isoformat()
    return obj


def sanitize_bookmark_name(value: str) -> str:
    text = re.sub(r"[^A-Za-z0-9_]+", "_", str(value).strip())
    text = re.sub(r"_+", "_", text).strip("_")
    if not text:
        return "ref"
    if text[0].isdigit():
        text = f"ref_{text}"
    return text[:40]


def format_number_token(value: str | int) -> str:
    return sanitize_bookmark_name(str(value))


def format_series_number(index: int, annex_id: str = "") -> str:
    if annex_id:
        return f"{annex_id}.{index}"
    return str(index)


def add_bookmark(name: str, location: str, bookmarks: dict[str, str], errors: list[str]) -> None:
    if not name:
        return
    if not BOOKMARK_RE.fullmatch(name):
        errors.append(f"{location} 的 bookmark 不合法：{name}")
        return
    previous = bookmarks.get(name)
    if previous is not None and previous != location:
        errors.append(f"{location} 的 bookmark 与 {previous} 重复：{name}")
        return
    bookmarks[name] = location


def collect_inline_refs(text: str, location: str, refs: list[tuple[str, str, str]]) -> None:
    for kind, target in INLINE_FIELD_RE.findall(text or ""):
        refs.append((kind, target, location))


def validate_text_value(value, location: str, errors: list[str], refs: list[tuple[str, str, str]] | None = None) -> None:
    text = str(value or "")
    if not text.strip():
        errors.append(f"{location} 不能为空")
        return
    if refs is not None:
        collect_inline_refs(text, location, refs)


def starts_with_forbidden_prefix(text: str, prefixes: list[str]) -> str | None:
    stripped = text.lstrip()
    for prefix in prefixes:
        if stripped.startswith(prefix):
            return prefix
    return None


def validate_text_rules(
    text: str,
    location: str,
    rules: dict,
    warnings: list[str],
    *,
    term_definition: bool = False,
) -> None:
    if not text.strip():
        return

    prefix = starts_with_forbidden_prefix(text, rules.get("forbidden_list_prefixes", []))
    if prefix is not None:
        warnings.append(f"{location} 使用了不推荐的列项前缀：{prefix}")

    if any(location.startswith(prefix) for prefix in rules.get("forbidden_modal_locations", [])):
        for word in rules.get("forbidden_modal_words", []):
            if word in text:
                warnings.append(f"{location} 含有不推荐的助动词或表述：{word}")

    for word in rules.get("forbidden_legal_terms", []):
        if word in text:
            warnings.append(f"{location} 含有不宜写入标准正文的法律性表述：{word}")

    for word in rules.get("forbidden_contract_terms", []):
        if word in text:
            warnings.append(f"{location} 含有不宜写入标准正文的合同性表述：{word}")

    if term_definition:
        for word in rules.get("term_definition_forbidden_words", []):
            if word in text:
                warnings.append(f"{location} 作为术语定义不应包含请求性或限制性措辞：{word}")


def validate_content_block(
    item,
    location: str,
    errors: list[str],
    refs: list[tuple[str, str, str]],
    bookmarks: dict[str, str],
    rules: dict,
    warnings: list[str],
    context: dict[str, int] | None = None,
    annex_id: str = "",
) -> None:
    if isinstance(item, str):
        validate_text_value(item, location, errors, refs)
        validate_text_rules(str(item), location, rules, warnings)
        return
    if not isinstance(item, dict):
        errors.append(f"{location} 必须是字符串或对象")
        return

    block_type = str(item.get("type", "")).strip()
    if block_type not in {"paragraph", "styled_paragraph", "figure", "table", "note", "example"}:
        errors.append(f"{location}.type 非法")
        return

    bookmark = item.get("bookmark")
    if bookmark is not None and (not isinstance(bookmark, str) or not bookmark.strip()):
        errors.append(f"{location}.bookmark 不能为空")

    if block_type == "paragraph":
        text = item.get("text", "")
        validate_text_value(text, f"{location}.text", errors, refs)
        validate_text_rules(str(text), f"{location}.text", rules, warnings)
        if isinstance(bookmark, str) and bookmark.strip():
            add_bookmark(bookmark.strip(), f"{location}.bookmark", bookmarks, errors)
        return

    if block_type == "styled_paragraph":
        text = item.get("text", "")
        validate_text_value(text, f"{location}.text", errors, refs)
        validate_text_rules(str(text), f"{location}.text", rules, warnings)
        style_name = str(item.get("style_name", "")).strip()
        style_key = str(item.get("style_key", "")).strip()
        if not style_name and not style_key:
            errors.append(f"{location} 需要 style_name 或 style_key")
        if isinstance(bookmark, str) and bookmark.strip():
            add_bookmark(bookmark.strip(), f"{location}.bookmark", bookmarks, errors)
        return

    if block_type in {"figure", "table"}:
        validate_text_value(item.get("title", ""), f"{location}.title", errors)
        title_en = item.get("title_en")
        if title_en is not None and not str(title_en).strip():
            errors.append(f"{location}.title_en 不能为空字符串")
        if block_type == "table":
            rows = item.get("rows")
            if rows is not None:
                if not isinstance(rows, list) or not rows:
                    errors.append(f"{location}.rows 必须是非空二维数组")
                else:
                    expected_len = None
                    for ridx, row in enumerate(rows):
                        if not isinstance(row, list) or not row:
                            errors.append(f"{location}.rows[{ridx}] 必须是非空数组")
                            continue
                        if expected_len is None:
                            expected_len = len(row)
                        elif len(row) != expected_len:
                            errors.append(f"{location}.rows[{ridx}] 列数不一致")
                        for cidx, cell in enumerate(row):
                            if not str(cell).strip():
                                errors.append(f"{location}.rows[{ridx}][{cidx}] 不能为空")
            header_rows = item.get("header_rows")
            if header_rows is not None and (not isinstance(header_rows, int) or header_rows < 0):
                errors.append(f"{location}.header_rows 必须是大于等于 0 的整数")
        if context is not None:
            context[block_type] = int(context.get(block_type, 0)) + 1
            generated = f"{'fig' if block_type == 'figure' else 'table'}_{format_number_token(format_series_number(context[block_type], annex_id))}"
        else:
            generated = ""
        resolved = str(bookmark).strip() if isinstance(bookmark, str) and str(bookmark).strip() else generated
        add_bookmark(resolved, f"{location}.bookmark", bookmarks, errors)
        for pidx, paragraph in enumerate(item.get("paragraphs", []) or []):
            validate_text_value(paragraph, f"{location}.paragraphs[{pidx}]", errors, refs)
            validate_text_rules(str(paragraph), f"{location}.paragraphs[{pidx}]", rules, warnings)
        for nidx, note in enumerate(item.get("notes", []) or []):
            validate_text_value(note, f"{location}.notes[{nidx}]", errors, refs)
            validate_text_rules(str(note), f"{location}.notes[{nidx}]", rules, warnings)
        return

    if block_type == "note":
        items = item.get("items", [])
        if not isinstance(items, list) or not items:
            errors.append(f"{location}.items 必须是非空数组")
            return
        for nidx, note in enumerate(items):
            validate_text_value(note, f"{location}.items[{nidx}]", errors, refs)
            validate_text_rules(str(note), f"{location}.items[{nidx}]", rules, warnings)
        return

    example_items = item.get("items", [])
    if not isinstance(example_items, list) or not example_items:
        errors.append(f"{location}.items 必须是非空数组")
        return
    for eidx, example in enumerate(example_items):
        current = f"{location}.items[{eidx}]"
        if isinstance(example, str):
            validate_text_value(example, current, errors, refs)
            validate_text_rules(str(example), current, rules, warnings)
            continue
        if not isinstance(example, dict):
            errors.append(f"{current} 必须是字符串或对象")
            continue
        title = str(example.get("title", "")).strip()
        if title:
            collect_inline_refs(title, f"{current}.title", refs)
            validate_text_rules(title, f"{current}.title", rules, warnings)
        paragraphs = example.get("paragraphs", []) or []
        if not title and not paragraphs:
            errors.append(f"{current} 至少需要 title 或 paragraphs")
        for pidx, paragraph in enumerate(paragraphs):
            validate_text_value(paragraph, f"{current}.paragraphs[{pidx}]", errors, refs)
            validate_text_rules(str(paragraph), f"{current}.paragraphs[{pidx}]", rules, warnings)


def validate_content_list(
    items,
    location: str,
    errors: list[str],
    refs: list[tuple[str, str, str]],
    bookmarks: dict[str, str],
    rules: dict,
    warnings: list[str],
    context: dict[str, int] | None = None,
    annex_id: str = "",
) -> None:
    if not isinstance(items, list):
        errors.append(f"{location} 必须是数组")
        return
    for idx, item in enumerate(items):
        validate_content_block(
            item,
            f"{location}[{idx}]",
            errors,
            refs,
            bookmarks,
            rules,
            warnings,
            context=context,
            annex_id=annex_id,
        )


def validate_clause_list(
    clauses,
    location: str,
    errors: list[str],
    refs: list[tuple[str, str, str]],
    bookmarks: dict[str, str],
    rules: dict,
    warnings: list[str],
    prefix: list[int],
    clause_kind: str,
    context: dict[str, int] | None = None,
    annex_id: str = "",
    start_index: int = 1,
) -> None:
    if not isinstance(clauses, list):
        errors.append(f"{location} 必须是数组")
        return
    local_context = context if context is not None else {"figure": 0, "table": 0}
    for idx, clause in enumerate(clauses, start=start_index):
        current = f"{location}[{idx - start_index}]"
        if not isinstance(clause, dict):
            errors.append(f"{current} 必须是对象")
            continue
        title = str(clause.get("title", "")).strip()
        if not title:
            errors.append(f"{current}.title 不能为空")
        path = prefix + [idx]
        if clause_kind == "annex":
            generated = f"annex_{format_number_token(annex_id)}_{'_'.join(str(x) for x in path)}"
        else:
            generated = f"clause_{'_'.join(str(x) for x in path)}"
        bookmark = clause.get("bookmark")
        if bookmark is not None and (not isinstance(bookmark, str) or not bookmark.strip()):
            errors.append(f"{current}.bookmark 不能为空")
        resolved = str(bookmark).strip() if isinstance(bookmark, str) and str(bookmark).strip() else generated
        add_bookmark(resolved, f"{current}.bookmark", bookmarks, errors)
        paragraphs = clause.get("paragraphs", []) or []
        children = clause.get("children", []) or []
        if not paragraphs and not children:
            errors.append(f"{current} 至少需要 paragraphs 或 children")
        validate_content_list(
            paragraphs,
            f"{current}.paragraphs",
            errors,
            refs,
            bookmarks,
            rules,
            warnings,
            context=local_context,
            annex_id=annex_id,
        )
        if children:
            validate_clause_list(
                children,
                f"{current}.children",
                errors,
                refs,
                bookmarks,
                rules,
                warnings,
                path,
                clause_kind,
                context=local_context,
                annex_id=annex_id,
            )


def validate_date_field(value, location: str, pattern: re.Pattern[str], errors: list[str]) -> None:
    if value is None or str(value).strip() == "":
        return
    if not pattern.fullmatch(str(value).strip()):
        errors.append(f"{location} 格式非法")


def validate_cover_fields(cover: dict, rules: dict, errors: list[str]) -> None:
    for field in rules.get("required_cover_fields", []):
        if not str(cover.get(field, "")).strip():
            errors.append(f"cover.{field} 不能为空")

    standard_number = str(cover.get("standard_number", "")).strip()
    if standard_number and not STANDARD_NUMBER_RE.fullmatch(standard_number):
        errors.append("cover.standard_number 格式非法")

    validate_date_field(cover.get("published_date"), "cover.published_date", DATE_RE, errors)
    validate_date_field(cover.get("implementation_date"), "cover.implementation_date", DATE_RE, errors)
    validate_date_field(cover.get("completion_date"), "cover.completion_date", YEAR_MONTH_RE, errors)


def validate_reserved_sections(sections: dict, rules: dict, errors: list[str]) -> None:
    for field in rules.get("required_sections", []):
        if field not in sections:
            errors.append(f"sections.{field} 缺失")


def validate_special_sections(sections: dict, rules: dict, errors: list[str]) -> None:
    normative = sections.get("normative_references", {})
    intro = str(normative.get("intro", "")).strip()
    allowed_normative = rules.get("allowed_normative_intro", [])
    if intro and intro not in allowed_normative:
        errors.append("规范性引用文件引导语不在允许列表内")
    if intro == "本文件没有规范性引用文件。" and (normative.get("items") or []):
        errors.append("规范性引用文件引导语声明无引用文件，但 items 非空")

    terms = sections.get("terms_definitions", {})
    terms_intro = str(terms.get("intro", "")).strip()
    allowed_terms = rules.get("allowed_terms_intro", [])
    if terms_intro and terms_intro not in allowed_terms:
        errors.append("术语和定义引导语不在允许列表内")
    if terms_intro == "本文件没有需要界定的术语和定义。" and (terms.get("items") or []):
        errors.append("术语和定义引导语声明无术语条，但 items 非空")


def validate_term_items(
    terms: dict,
    errors: list[str],
    refs: list[tuple[str, str, str]],
    bookmarks: dict[str, str],
    rules: dict,
    warnings: list[str],
) -> None:
    items = terms.get("items", []) or []
    for idx, item in enumerate(items):
        current = f"sections.terms_definitions.items[{idx}]"
        if isinstance(item, str):
            validate_text_value(item, current, errors, refs)
            add_bookmark(f"term_{idx + 1}", f"{current}.bookmark", bookmarks, errors)
            continue
        if not isinstance(item, dict):
            errors.append(f"{current} 必须是字符串或对象")
            continue
        validate_text_value(item.get("term", ""), f"{current}.term", errors, refs)
        definitions = item.get("definition", [])
        if not isinstance(definitions, list) or not definitions:
            errors.append(f"{current}.definition 必须是非空数组")
        for didx, definition in enumerate(definitions if isinstance(definitions, list) else []):
            validate_text_value(definition, f"{current}.definition[{didx}]", errors, refs)
            validate_text_rules(
                str(definition),
                f"{current}.definition[{didx}]",
                rules,
                warnings,
                term_definition=True,
            )
        bookmark = item.get("bookmark")
        if bookmark is not None and (not isinstance(bookmark, str) or not bookmark.strip()):
            errors.append(f"{current}.bookmark 不能为空")
        resolved = str(bookmark).strip() if isinstance(bookmark, str) and str(bookmark).strip() else f"term_{idx + 1}"
        add_bookmark(resolved, f"{current}.bookmark", bookmarks, errors)
        level = item.get("level", 1)
        if not isinstance(level, int) or not (1 <= level <= 5):
            errors.append(f"{current}.level 必须是 1 到 5 的整数")


def expected_annex_id(index: int) -> str:
    return chr(ord("A") + index - 1)


def is_adoption_annex_id(value: str) -> bool:
    text = str(value or "").strip()
    return bool(re.fullmatch(r"Z[A-Z]", text))


def validate_annexes(
    annexes,
    errors: list[str],
    refs: list[tuple[str, str, str]],
    bookmarks: dict[str, str],
    rules: dict,
    warnings: list[str],
) -> None:
    if not isinstance(annexes, list):
        errors.append("annexes 必须是数组")
        return
    seen_ids: set[str] = set()
    for idx, annex in enumerate(annexes, start=1):
        current = f"annexes[{idx - 1}]"
        if not isinstance(annex, dict):
            errors.append(f"{current} 必须是对象")
            continue
        annex_id = str(annex.get("id", "")).strip()
        if annex_id != expected_annex_id(idx) and not is_adoption_annex_id(annex_id):
            errors.append(f"{current}.id 应按 A、B、C 连续编排，当前应为 {expected_annex_id(idx)}")
        if annex_id in seen_ids:
            errors.append(f"{current}.id 重复：{annex_id}")
        seen_ids.add(annex_id)
        kind = annex.get("kind")
        if kind not in {"normative", "informative"}:
            errors.append(f"{current}.kind 非法")
        validate_text_value(annex.get("title", ""), f"{current}.title", errors)
        bookmark = annex.get("bookmark")
        if bookmark is not None and (not isinstance(bookmark, str) or not bookmark.strip()):
            errors.append(f"{current}.bookmark 不能为空")
        resolved = str(bookmark).strip() if isinstance(bookmark, str) and str(bookmark).strip() else f"annex_{format_number_token(annex_id)}"
        add_bookmark(resolved, f"{current}.bookmark", bookmarks, errors)
        annex_context = {"figure": 0, "table": 0}
        validate_content_list(
            annex.get("paragraphs", []) or [],
            f"{current}.paragraphs",
            errors,
            refs,
            bookmarks,
            rules,
            warnings,
            context=annex_context,
            annex_id=annex_id,
        )
        validate_clause_list(
            annex.get("clauses", []) or [],
            f"{current}.clauses",
            errors,
            refs,
            bookmarks,
            rules,
            warnings,
            [],
            "annex",
            annex_id=annex_id,
        )


def validate_refs(refs: list[tuple[str, str, str]], bookmarks: dict[str, str], errors: list[str]) -> None:
    for kind, target, location in refs:
        if not BOOKMARK_RE.fullmatch(target):
            errors.append(f"{location} 中的 {kind} 目标不合法：{target}")
            continue
        if target not in bookmarks:
            errors.append(f"{location} 中引用的目标不存在：{target}")


def validate_bibliography(items, errors: list[str], refs: list[tuple[str, str, str]]) -> None:
    if items is None:
        return
    if not isinstance(items, list):
        errors.append("bibliography 必须是数组")
        return
    for idx, item in enumerate(items):
        validate_text_value(item, f"bibliography[{idx}]", errors, refs)


def validate_foreword_introduction(
    data: dict,
    rules: dict,
    errors: list[str],
    refs: list[tuple[str, str, str]],
    warnings: list[str],
) -> None:
    for block_name in ("foreword", "introduction"):
        block = data.get(block_name, [])
        if block and not isinstance(block, list):
            errors.append(f"{block_name} 必须是数组")
            continue
        if not isinstance(block, list):
            continue
        for idx, item in enumerate(block):
            validate_text_value(item, f"{block_name}[{idx}]", errors, refs)
            validate_text_rules(str(item), f"{block_name}[{idx}]", rules, warnings)


def contains_phrase(items: list[str], phrase: str) -> bool:
    return any(phrase in str(item) for item in items)


def validate_string_list(
    value,
    location: str,
    errors: list[str],
    *,
    allow_empty: bool = True,
) -> list[str]:
    if value is None:
        return []
    if not isinstance(value, list):
        errors.append(f"{location} 必须是数组")
        return []
    result: list[str] = []
    for idx, item in enumerate(value):
        text = str(item or "").strip()
        if not text:
            errors.append(f"{location}[{idx}] 不能为空")
            continue
        result.append(text)
    if not allow_empty and not result:
        errors.append(f"{location} 不能为空")
    return result


def validate_adoption(
    data: dict,
    errors: list[str],
    warnings: list[str],
) -> None:
    adoption = data.get("adoption")
    if adoption is None:
        return
    if not isinstance(adoption, dict):
        errors.append("adoption 必须是对象")
        return

    mode = str(adoption.get("mode", "")).strip().upper()
    if mode not in ADOPTION_FOREWORD_PHRASES:
        errors.append("adoption.mode 必须是 IDT、MOD 或 NEQ")
        return

    source_standard = str(adoption.get("source_standard", "")).strip()
    if not source_standard:
        errors.append("adoption.source_standard 不能为空")

    structure_adjustments = validate_string_list(
        adoption.get("structure_adjustments"),
        "adoption.structure_adjustments",
        errors,
    )
    technical_differences = validate_string_list(
        adoption.get("technical_differences"),
        "adoption.technical_differences",
        errors,
    )
    validate_string_list(
        adoption.get("editorial_changes"),
        "adoption.editorial_changes",
        errors,
    )

    foreword = data.get("foreword", [])
    foreword_texts = [str(item) for item in foreword] if isinstance(foreword, list) else []
    expected_phrase = ADOPTION_FOREWORD_PHRASES[mode]
    if foreword_texts and not contains_phrase(foreword_texts, expected_phrase):
        errors.append(f"foreword 未体现采标方式：应包含“{expected_phrase}”")

    annexes = data.get("annexes", []) or []
    annex_roles: dict[str, list[str]] = {}
    for idx, annex in enumerate(annexes):
        if not isinstance(annex, dict):
            continue
        role = str(annex.get("role", "")).strip()
        if not role:
            continue
        annex_roles.setdefault(role, []).append(f"annexes[{idx}]")

    if mode == "MOD":
        if not structure_adjustments:
            errors.append("adoption.mode 为 MOD 时，adoption.structure_adjustments 不能为空")
        if not technical_differences:
            errors.append("adoption.mode 为 MOD 时，adoption.technical_differences 不能为空")
        if "structure_adjustment" not in annex_roles:
            errors.append("adoption.mode 为 MOD 时，至少应有一个附录标注 role=structure_adjustment")
        if "technical_differences" not in annex_roles:
            errors.append("adoption.mode 为 MOD 时，至少应有一个附录标注 role=technical_differences")
    else:
        if structure_adjustments:
            errors.append(f"adoption.mode 为 {mode} 时，不应填写 adoption.structure_adjustments")
        if technical_differences:
            errors.append(f"adoption.mode 为 {mode} 时，不应填写 adoption.technical_differences")
        if "structure_adjustment" in annex_roles:
            errors.append(f"adoption.mode 为 {mode} 时，不应设置 role=structure_adjustment 的附录")
        if "technical_differences" in annex_roles:
            errors.append(f"adoption.mode 为 {mode} 时，不应设置 role=technical_differences 的附录")

    if mode == "IDT" and foreword_texts and contains_phrase(foreword_texts, "修改采用"):
        errors.append("adoption.mode 为 IDT，但 foreword 中出现了“修改采用”")
    if mode == "MOD" and foreword_texts and contains_phrase(foreword_texts, "等同采用"):
        errors.append("adoption.mode 为 MOD，但 foreword 中出现了“等同采用”")
    if mode == "NEQ" and foreword_texts and (contains_phrase(foreword_texts, "等同采用") or contains_phrase(foreword_texts, "修改采用")):
        errors.append("adoption.mode 为 NEQ，但 foreword 中出现了其他采标方式表述")

    if mode == "MOD" and not foreword_texts:
        warnings.append("当前声明为 MOD，但 foreword 为空，无法校验采标说明完整性")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", required=True)
    parser.add_argument("--rules", required=True)
    args = parser.parse_args()

    data = to_plain(load_data(Path(args.input)))
    rules = load_data(Path(args.rules))
    errors: list[str] = []
    warnings: list[str] = []
    refs: list[tuple[str, str, str]] = []
    bookmarks: dict[str, str] = {}

    cover = data.get("cover", {})
    sections = data.get("sections", {})

    validate_cover_fields(cover, rules, errors)
    validate_reserved_sections(sections, rules, errors)
    validate_foreword_introduction(data, rules, errors, refs, warnings)
    validate_adoption(data, errors, warnings)

    toc = data.get("table_of_contents", {})
    if toc and not isinstance(toc, dict):
        errors.append("table_of_contents 必须是对象")
    elif isinstance(toc, dict):
        enabled = toc.get("enabled")
        if enabled is not None and not isinstance(enabled, bool):
            errors.append("table_of_contents.enabled 必须是布尔值")
        levels = toc.get("levels")
        if levels is not None and (not isinstance(levels, int) or not (1 <= levels <= 9)):
            errors.append("table_of_contents.levels 必须是 1 到 9 的整数")
        title = toc.get("title")
        if title is not None and not str(title).strip():
            errors.append("table_of_contents.title 不能为空字符串")

    body_context = {"figure": 0, "table": 0}
    if "scope" in sections:
        validate_content_list(
            sections.get("scope", []),
            "sections.scope",
            errors,
            refs,
            bookmarks,
            rules,
            warnings,
            context=body_context,
        )

    validate_special_sections(sections, rules, errors)
    validate_term_items(sections.get("terms_definitions", {}), errors, refs, bookmarks, rules, warnings)

    normative_items = sections.get("normative_references", {}).get("items", []) or []
    if not isinstance(normative_items, list):
        errors.append("sections.normative_references.items 必须是数组")
    else:
        for idx, item in enumerate(normative_items):
            validate_text_value(item, f"sections.normative_references.items[{idx}]", errors, refs)

    if "clauses" in data:
        validate_clause_list(
            data.get("clauses", []) or [],
            "clauses",
            errors,
            refs,
            bookmarks,
            rules,
            warnings,
            [],
            "main",
            context=body_context,
            start_index=4,
        )

    forbidden = rules.get("forbidden_patterns", [])
    all_text = json.dumps(data, ensure_ascii=False)
    for pattern in forbidden:
        if pattern in all_text:
            errors.append(f"检测到不建议直接出现在标准正文输入中的词语：{pattern}")

    validate_annexes(data.get("annexes", []) or [], errors, refs, bookmarks, rules, warnings)
    validate_bibliography(data.get("bibliography"), errors, refs)
    validate_refs(refs, bookmarks, errors)

    if errors:
        print("校验失败：")
        for error in errors:
            print(f"- {error}")
        sys.exit(1)

    if warnings:
        print("校验通过，但有审查提示：")
        for warning in warnings:
            print(f"- {warning}")
        return

    print("校验通过")


if __name__ == "__main__":
    main()
