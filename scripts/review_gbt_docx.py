#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import re
import sys
import zipfile
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from pathlib import Path


NS = {"w": "http://schemas.openxmlformats.org/wordprocessingml/2006/main"}
W_NS = "{%s}" % NS["w"]
STANDARD_REF_RE = re.compile(
    r"\b(?:GB(?:/T)?|ISO|IEC|RFC|SMPTE|ITU|IANA|W3C|GY/T|DY/T|SJ/T)\s+[A-Z0-9./\-—]*\d[A-Z0-9./\-—]*(?:\(\s*所有部分\s*\))?",
    re.IGNORECASE,
)
REQUEST_WORDS = ("应", "不应", "宜", "不宜", "不得", "必须", "需要")
ADOPTION_MODES = {
    "等同采用": "IDT",
    "修改采用": "MOD",
    "非等效采用": "NEQ",
}


@dataclass
class Paragraph:
    index: int
    style_id: str
    style_name: str
    text: str


@dataclass
class ParagraphContext:
    clause_id: str
    clause_title: str
    location_label: str


def load_docx_parts(docx_path: Path) -> tuple[ET.Element, ET.Element]:
    with zipfile.ZipFile(docx_path) as package:
        styles = ET.fromstring(package.read("word/styles.xml"))
        document = ET.fromstring(package.read("word/document.xml"))
    return styles, document


def build_style_map(styles_root: ET.Element) -> dict[str, str]:
    mapping: dict[str, str] = {}
    for style in styles_root.findall("w:style", NS):
        style_id = style.get(f"{W_NS}styleId", "")
        name = style.find("w:name", NS)
        if style_id and name is not None:
            mapping[style_id] = name.get(f"{W_NS}val", "")
    return mapping


def extract_paragraphs(document_root: ET.Element, style_map: dict[str, str]) -> list[Paragraph]:
    body = document_root.find("w:body", NS)
    if body is None:
        return []

    paragraphs: list[Paragraph] = []
    for idx, node in enumerate(body.findall("w:p", NS)):
        text = "".join(t.text or "" for t in node.findall(".//w:t", NS)).strip()
        ppr = node.find("w:pPr", NS)
        style_id = ""
        if ppr is not None:
            pstyle = ppr.find("w:pStyle", NS)
            if pstyle is not None:
                style_id = pstyle.get(f"{W_NS}val", "")
        paragraphs.append(
            Paragraph(
                index=idx,
                style_id=style_id,
                style_name=style_map.get(style_id, ""),
                text=text,
            )
        )
    return paragraphs


def is_heading(para: Paragraph) -> bool:
    name = para.style_name
    if not name:
        return False
    return (
        "章标题" in name
        or ("条标题" in name)
        or ("无标题" in name and "标准文件" in name)
        or name in {"标准文件_参考文献标题", "标准文件_目录标题", "标准文件_正文标准名称"}
    )


def heading_level(para: Paragraph) -> int | None:
    name = para.style_name
    if name == "标准文件_正文标准名称":
        return 0
    if name in {
        "标准文件_章标题",
        "标准文件_参考文献标题",
        "标准文件_目录标题",
        "标准文件_前言、引言标题",
    }:
        return 1
    if "一级条标题" in name or "一级无标题" in name:
        return 2
    if "二级条标题" in name or "二级无标题" in name:
        return 3
    if "三级条标题" in name or "三级无标题" in name:
        return 4
    if "四级条标题" in name or "四级无标题" in name:
        return 5
    if "五级条标题" in name or "五级无标题" in name:
        return 6
    return None


def first_index(paragraphs: list[Paragraph], predicate) -> int | None:
    for i, para in enumerate(paragraphs):
        if predicate(para):
            return i
    return None


def chapter_range(paragraphs: list[Paragraph], title: str) -> tuple[int, int] | None:
    start = first_index(paragraphs, lambda p: p.style_name == "标准文件_章标题" and p.text == title)
    if start is None:
        return None
    end = len(paragraphs)
    for i in range(start + 1, len(paragraphs)):
        if paragraphs[i].style_name in {"标准文件_章标题", "标准文件_参考文献标题"}:
            end = i
            break
    return start, end


def build_paragraph_contexts(paragraphs: list[Paragraph]) -> list[ParagraphContext]:
    contexts: list[ParagraphContext] = []
    chapter_no = 0
    clause_counts = [0, 0, 0, 0, 0]
    current_title = ""
    in_body = False
    front_section = ""

    for para in paragraphs:
        level = heading_level(para)
        if level == 0:
            current_title = para.text
            contexts.append(ParagraphContext("", current_title, "正文标准名称"))
            continue

        if level == 1:
            if para.style_name == "标准文件_目录标题" and para.text == "目次":
                front_section = para.text
                current_title = para.text
                contexts.append(ParagraphContext(para.text, current_title, para.text))
                continue
            if para.style_name == "标准文件_前言、引言标题" and para.text in {"前言", "引言"}:
                front_section = para.text
                current_title = para.text
                contexts.append(ParagraphContext(para.text, current_title, para.text))
                continue
            if para.style_name == "标准文件_章标题":
                chapter_no += 1
                in_body = True
                front_section = ""
                clause_counts = [0, 0, 0, 0, 0]
                clause_id = str(chapter_no)
                current_title = para.text
                contexts.append(ParagraphContext(clause_id, current_title, clause_id))
                continue
            if para.style_name == "标准文件_参考文献标题":
                current_title = para.text
                front_section = "参考文献"
                contexts.append(ParagraphContext("参考文献", current_title, "参考文献"))
                continue

        if level is not None and 2 <= level <= 6 and in_body:
            idx = level - 2
            clause_counts[idx] += 1
            for reset in range(idx + 1, len(clause_counts)):
                clause_counts[reset] = 0
            numbers = [str(chapter_no)] + [str(v) for v in clause_counts[: idx + 1]]
            clause_id = ".".join(numbers)
            current_title = para.text
            contexts.append(ParagraphContext(clause_id, current_title, clause_id))
            continue

        current_id = ""
        label = ""
        if front_section:
            current_id = front_section
            label = front_section
        if in_body and chapter_no:
            active = [str(chapter_no)]
            for value in clause_counts:
                if value <= 0:
                    break
                active.append(str(value))
            current_id = ".".join(active)
            label = current_id
        contexts.append(ParagraphContext(current_id, current_title, label))

    return contexts


def paragraph_index_from_location(location: str) -> int | None:
    match = re.search(r"段落#(\d+)", location)
    if not match:
        return None
    return int(match.group(1))


def enrich_issue_context(issues: list[dict], contexts: list[ParagraphContext]) -> None:
    for issue in issues:
        idx = paragraph_index_from_location(issue.get("location", ""))
        if idx is None or idx >= len(contexts):
            if issue.get("location") == "缩略语章节":
                issue["clause_id"] = "4"
                issue["clause_title"] = "缩略语"
                issue["location_label"] = "4"
            else:
                issue["clause_id"] = ""
                issue["clause_title"] = ""
                issue["location_label"] = issue.get("location", "")
            continue
        issue["clause_id"] = contexts[idx].clause_id
        issue["clause_title"] = contexts[idx].clause_title
        issue["location_label"] = contexts[idx].location_label or issue.get("location", "")


def normalize_ref_code(text: str) -> str:
    value = re.sub(r"\s+", " ", text.strip())
    value = value.replace("—", "-")
    value = value.replace("（所有部分）", "(所有部分)")
    return value


def canonical_ref_code(text: str) -> str:
    value = normalize_ref_code(text)
    value = re.sub(r"[-—]\d{4}$", "", value)
    return value.upper()


def extract_ref_code(text: str) -> str | None:
    match = STANDARD_REF_RE.search(text)
    if not match:
        return None
    return normalize_ref_code(match.group(0))


def make_issue(
    rule_id: str,
    severity: str,
    location: str,
    message: str,
    excerpt: str = "",
    *,
    suggested_action: str = "",
    decision_type: str = "report_only",
    can_auto_fix: bool = False,
    question: str = "",
    options: list[str] | None = None,
) -> dict:
    issue = {
        "rule_id": rule_id,
        "severity": severity,
        "location": location,
        "message": message,
        "excerpt": excerpt,
        "suggested_action": suggested_action,
        "decision_type": decision_type,
        "can_auto_fix": can_auto_fix,
    }
    if question:
        issue["question"] = question
    if options:
        issue["options"] = options
    return issue


def review_intro_requirements(paragraphs: list[Paragraph]) -> list[dict]:
    issues: list[dict] = []
    intro_start = first_index(paragraphs, lambda p: p.style_name in {"标准文件_前言、引言标题", "标准文件_目录标题", "标准文件_引言标题"} and "引言" in p.text)
    body_title = first_index(paragraphs, lambda p: p.style_name == "标准文件_正文标准名称")
    if intro_start is None or body_title is None or intro_start >= body_title:
        return issues
    for para in paragraphs[intro_start + 1 : body_title]:
        if "段" not in para.style_name or not para.text:
            continue
        if any(word in para.text for word in ("应", "不应")):
            issues.append(
                make_issue(
                    "intro_requirement",
                    "warning",
                    f"段落#{para.index}",
                    "引言中可能包含要求性表述。",
                    para.text,
                    suggested_action="确认该表述是否应弱化为资料性说明，或移入正文要求条款。",
                    decision_type="ask_user",
                    can_auto_fix=False,
                    question="引言中的要求性表述应如何处理？",
                    options=["保留不改", "改写为资料性表述", "移入正文条款"],
                )
            )
    return issues


def review_abbreviations(paragraphs: list[Paragraph]) -> list[dict]:
    issues: list[dict] = []
    span = chapter_range(paragraphs, "缩略语")
    if span is None:
        return issues
    start, end = span
    entries: list[tuple[str, Paragraph]] = []
    for para in paragraphs[start + 1 : end]:
        if not para.text or "下列缩略语适用于本文件" in para.text:
            continue
        match = re.match(r"^([A-Za-z0-9][A-Za-z0-9\-]*)\b", para.text)
        if match:
            entries.append((match.group(1), para))
    if not entries:
        return issues

    abbrs = [abbr for abbr, _ in entries]
    sorted_abbrs = sorted(abbrs, key=lambda x: x.upper())
    if abbrs != sorted_abbrs:
        issues.append(
            make_issue(
                "abbreviation_order",
                "warning",
                "缩略语章节",
                "缩略语条目未按字母顺序排列。",
                " / ".join(abbrs),
                suggested_action="按字母顺序重排缩略语条目。",
                decision_type="auto_fix",
                can_auto_fix=True,
            )
        )

    full_text = "\n".join(p.text for p in paragraphs)
    for abbr, para in entries:
        if len(re.findall(rf"(?<![A-Za-z0-9-]){re.escape(abbr)}(?![A-Za-z0-9-])", full_text)) <= 1:
            issues.append(
                make_issue(
                    "abbreviation_usage",
                    "warning",
                    f"段落#{para.index}",
                    f"缩略语 {abbr} 仅在缩略语章节中出现，未在正文再次使用。",
                    para.text,
                    suggested_action="确认是否需要在正文中补用该缩略语，或从缩略语章节删除该条目。",
                    decision_type="ask_user",
                    can_auto_fix=False,
                    question=f"缩略语 {abbr} 仅在缩略语章节中出现，如何处理？",
                    options=["保留不改", "在正文补用", "从缩略语章节删除"],
                )
            )
    return issues


def is_list_paragraph(para: Paragraph) -> bool:
    name = para.style_name
    return "项" in name and not any(token in name for token in ("标题", "附录", "图", "表", "注", "示例", "例"))


def prev_nonempty_paragraph(paragraphs: list[Paragraph], start: int) -> Paragraph | None:
    scan = start - 1
    while scan >= 0:
        if paragraphs[scan].text:
            return paragraphs[scan]
        scan -= 1
    return None


def next_nonempty_paragraph(paragraphs: list[Paragraph], end: int) -> Paragraph | None:
    scan = end + 1
    while scan < len(paragraphs):
        if paragraphs[scan].text:
            return paragraphs[scan]
        scan += 1
    return None


def looks_like_placeholder_list(items: list[Paragraph], prev: Paragraph | None) -> bool:
    if any(not item.text.strip() for item in items):
        return True
    if prev and any(token in prev.text for token in ("以下部分", "如下部分", "包括", "包含")):
        for item in items:
            if re.match(r"^第\d+部分：$", item.text):
                return True
    return False


def needs_llm_list_review(prev: Paragraph | None, items: list[Paragraph], nxt: Paragraph | None) -> tuple[bool, str]:
    texts = [item.text for item in items if item.text]
    if not texts:
        return False, ""
    if looks_like_placeholder_list(items, prev):
        return True, "疑似草稿占位列项"
    if prev and prev.text.endswith(":"):
        return True, "引导语使用半角冒号，需结合上下文判断是否属于格式问题"
    if all(STANDARD_REF_RE.search(text) for text in texts):
        return True, "疑似标准文件/参考资料列表，标点规则可能不同于普通技术列项"
    if any(text.endswith("：") for text in texts):
        return True, "列项中存在冒号收尾，疑似未完成草稿或嵌套结构"
    if nxt and nxt.style_name == "标准文件_段" and not nxt.text.endswith(("。", "：")):
        return True, "列项后续正文衔接不清晰，需结合上下文判断"
    return False, ""


def build_list_candidate(
    paragraphs: list[Paragraph],
    start: int,
    end: int,
    prev: Paragraph | None,
    nxt: Paragraph | None,
    reason: str,
) -> dict:
    items = paragraphs[start : end + 1]
    return {
        "candidate_type": "list_context",
        "start_location": f"段落#{items[0].index}",
        "end_location": f"段落#{items[-1].index}",
        "reason": reason,
        "lead": prev.text if prev else "",
        "items": [item.text for item in items],
        "next_paragraph": nxt.text if nxt else "",
        "question": "这组列项是正式列项、草稿占位，还是应按其他形式处理？",
    }


def review_list_context(paragraphs: list[Paragraph]) -> tuple[list[dict], list[dict]]:
    issues: list[dict] = []
    candidates: list[dict] = []
    idx = 0
    while idx < len(paragraphs):
        para = paragraphs[idx]
        if not is_list_paragraph(para):
            idx += 1
            continue
        start = idx
        while idx + 1 < len(paragraphs) and is_list_paragraph(paragraphs[idx + 1]):
            idx += 1
        end = idx

        prev = prev_nonempty_paragraph(paragraphs, start)
        nxt = next_nonempty_paragraph(paragraphs, end)
        items = paragraphs[start : end + 1]

        need_llm, reason = needs_llm_list_review(prev, items, nxt)
        if need_llm:
            candidates.append(build_list_candidate(paragraphs, start, end, prev, nxt, reason))
            idx += 1
            continue

        if prev is not None and not prev.text.endswith(("：", "。")):
            issues.append(
                make_issue(
                    "list_lead",
                    "warning",
                    f"段落#{paragraphs[start].index}",
                    "列项前缺少合适的引导语或引导语结尾标点不符合常见规则。",
                    prev.text,
                    suggested_action="确认该组列项的引导语形式，并按选定形式统一列项前一段的结尾标点。",
                    decision_type="ask_user",
                    can_auto_fix=False,
                    question="这组列项希望采用哪种引导语和结尾方式？",
                    options=["引导语以冒号结尾", "引导语以句号结尾", "改为并段表述"],
                )
            )

        expected = "；"
        if prev is not None and prev.text.endswith("。"):
            expected = "。"
        if expected == "；" and paragraphs[start].text.endswith("，"):
            expected = "，"

        for current in paragraphs[start : end + 1]:
            should_end = "。" if current is paragraphs[end] else expected
            if current.text and not current.text.endswith(should_end):
                issues.append(
                    make_issue(
                        "list_punctuation",
                        "warning",
                        f"段落#{current.index}",
                        f"列项结尾标点可能不正确，预期为“{should_end}”。",
                        current.text,
                        suggested_action=f"按当前列项上下文统一结尾标点为“{should_end}”。",
                        decision_type="ask_user",
                        can_auto_fix=False,
                        question="这组列项的标点统一方式是否采用当前建议？",
                        options=[f"统一为 {should_end}", "保留原文", "我另行指定"],
                    )
                )
        idx += 1
    return issues, candidates


def review_heading_structure(paragraphs: list[Paragraph]) -> list[dict]:
    issues: list[dict] = []
    for i, para in enumerate(paragraphs):
        level = heading_level(para)
        if level is None or level >= 6:
            continue
        children: list[int] = []
        has_body_before_child = False
        for j in range(i + 1, len(paragraphs)):
            other = paragraphs[j]
            other_level = heading_level(other)
            if other_level is not None and other_level <= level:
                break
            if other_level == level + 1:
                children.append(j)
                continue
            if children:
                continue
            if other.text and other_level is None:
                has_body_before_child = True

        if children and has_body_before_child:
            issues.append(
                make_issue(
                    "hanging_paragraph",
                    "warning",
                    f"段落#{para.index}",
                    "该标题下存在悬置段。",
                    para.text,
                    suggested_action="确认是否拆分为真正的子条款，或将悬置段并入标题后的正文。",
                    decision_type="report_only",
                    can_auto_fix=False,
                )
            )
        if len(children) == 1:
            child = paragraphs[children[0]]
            issues.append(
                make_issue(
                    "redundant_heading",
                    "warning",
                    f"段落#{child.index}",
                    "该标题可能为唯一子标题，存在冗余标题风险。",
                    child.text,
                    suggested_action="确认是否取消该唯一子标题，直接并回父标题下。",
                    decision_type="report_only",
                    can_auto_fix=False,
                )
            )
    return issues


def review_reference_mentions(paragraphs: list[Paragraph]) -> list[dict]:
    issues: list[dict] = []
    normative_span = chapter_range(paragraphs, "规范性引用文件")
    if normative_span is None:
        return issues
    n_start, n_end = normative_span
    scope_start = first_index(paragraphs, lambda p: p.style_name == "标准文件_章标题" and p.text == "范围")
    if scope_start is None:
        scope_start = n_end

    bibliography_start = first_index(paragraphs, lambda p: p.style_name == "标准文件_参考文献标题" or p.text == "参考文献")
    bibliography_end = len(paragraphs)
    if bibliography_start is not None:
        bibliography_codes = {
            extract_ref_code(p.text)
            for p in paragraphs[bibliography_start + 1 : bibliography_end]
            if p.text
        }
    else:
        bibliography_codes = set()
    bibliography_codes.discard(None)

    normative_codes = {
        canonical_ref_code(code)
        for p in paragraphs[n_start + 1 : n_end]
        if p.text and not p.text.startswith("下列文件中的内容") and (code := extract_ref_code(p.text))
    }
    bibliography_code_set = {
        canonical_ref_code(code) for code in bibliography_codes if code
    }
    known_codes = normative_codes | bibliography_code_set

    body_start = max(scope_start, n_end)
    body_end = bibliography_start or len(paragraphs)
    body_indices = list(range(body_start, body_end))
    seen_mentions: dict[str, str] = {}
    for idx in body_indices:
        para = paragraphs[idx]
        for match in STANDARD_REF_RE.findall(para.text):
            code = canonical_ref_code(match)
            seen_mentions.setdefault(code, f"段落#{para.index}")

    for code, location in seen_mentions.items():
        if code not in known_codes:
            issues.append(
                make_issue(
                    "reference_mention",
                    "warning",
                    location,
                    "正文提及了标准文件，但未在规范性引用文件或参考文献中找到对应条目。",
                    code,
                    suggested_action="确认该文件应补入规范性引用文件还是参考文献，或改写正文提及方式。",
                    decision_type="ask_user",
                    can_auto_fix=False,
                    question=f"{code} 应如何归类？",
                    options=["补入规范性引用文件", "补入参考文献", "仅保留正文提及", "删除该提及"],
                )
            )
    return issues


def foreword_range(paragraphs: list[Paragraph]) -> tuple[int, int] | None:
    start = first_index(
        paragraphs,
        lambda p: p.style_name in {"标准文件_前言、引言标题", "标准文件_前言标题"} and p.text == "前言",
    )
    if start is None:
        return None
    end = len(paragraphs)
    for i in range(start + 1, len(paragraphs)):
        if paragraphs[i].style_name in {"标准文件_前言、引言标题", "标准文件_引言标题", "标准文件_正文标准名称"}:
            end = i
            break
    return start, end


def detect_adoption_mode(texts: list[str]) -> str | None:
    joined = "\n".join(texts)
    found = [mode for phrase, mode in ADOPTION_MODES.items() if phrase in joined]
    if len(found) == 1:
        return found[0]
    return None


def review_adoption_consistency(paragraphs: list[Paragraph]) -> list[dict]:
    issues: list[dict] = []
    span = foreword_range(paragraphs)
    if span is None:
        return issues
    start, end = span
    foreword_paragraphs = [p for p in paragraphs[start + 1 : end] if p.text]
    foreword_texts = [p.text for p in foreword_paragraphs]
    joined = "\n".join(foreword_texts)
    detected_mode = detect_adoption_mode(foreword_texts)
    annex_titles = [
        para.text
        for para in paragraphs
        if "附录" in para.style_name and para.text
    ]
    has_structure_annex = any("结构" in title and "对照" in title for title in annex_titles)
    has_diff_annex = any("技术差异" in title for title in annex_titles)

    if "等同采用" in joined and "修改采用" in joined:
        issues.append(
            make_issue(
                "adoption_mode_conflict",
                "warning",
                f"段落#{paragraphs[start].index}",
                "前言同时出现了“等同采用”和“修改采用”，采标方式表述不一致。",
                " / ".join(foreword_texts),
                suggested_action="统一前言中的采标方式表述，并与正文和附录体系保持一致。",
                decision_type="report_only",
                can_auto_fix=False,
            )
        )
        return issues

    if detected_mode == "MOD":
        if "技术差异" not in joined:
            issues.append(
                make_issue(
                    "mod_foreword_missing_differences",
                    "warning",
                    f"段落#{paragraphs[start].index}",
                    "前言已表述为修改采用，但未明显说明技术差异。",
                    " / ".join(foreword_texts),
                    suggested_action="在前言中补充技术差异及其原因说明，或明确指向相应附录。",
                    decision_type="report_only",
                    can_auto_fix=False,
                )
            )
        if "结构调整" not in joined and "结构编号" not in joined:
            issues.append(
                make_issue(
                    "mod_foreword_missing_structure_adjustment",
                    "warning",
                    f"段落#{paragraphs[start].index}",
                    "前言已表述为修改采用，但未明显说明结构调整。",
                    " / ".join(foreword_texts),
                    suggested_action="在前言中补充结构调整说明，或明确指向相应附录。",
                    decision_type="report_only",
                    can_auto_fix=False,
                )
            )
        if not has_structure_annex:
            issues.append(
                make_issue(
                    "mod_missing_structure_annex",
                    "warning",
                    f"段落#{paragraphs[start].index}",
                    "文稿看起来为修改采用，但未发现明显的结构对照类附录标题。",
                    " / ".join(annex_titles),
                    suggested_action="确认是否需要补充结构调整/编号对照附录，或调整附录标题使其可识别。",
                    decision_type="report_only",
                    can_auto_fix=False,
                )
            )
        if not has_diff_annex:
            issues.append(
                make_issue(
                    "mod_missing_differences_annex",
                    "warning",
                    f"段落#{paragraphs[start].index}",
                    "文稿看起来为修改采用，但未发现明显的技术差异附录标题。",
                    " / ".join(annex_titles),
                    suggested_action="确认是否需要补充技术差异附录，或调整附录标题使其可识别。",
                    decision_type="report_only",
                    can_auto_fix=False,
                )
            )

    if detected_mode == "IDT" and (has_structure_annex or has_diff_annex):
        issues.append(
            make_issue(
                "idt_with_mod_annexes",
                "warning",
                f"段落#{paragraphs[start].index}",
                "前言表述为等同采用，但附录体系呈现出修改采用特征。",
                " / ".join(annex_titles),
                suggested_action="确认采标方式是否应调整为修改采用，或删除不必要的差异说明附录。",
                decision_type="report_only",
                can_auto_fix=False,
            )
        )

    return issues


def review_document(docx_path: Path) -> dict:
    styles_root, document_root = load_docx_parts(docx_path)
    style_map = build_style_map(styles_root)
    paragraphs = extract_paragraphs(document_root, style_map)
    contexts = build_paragraph_contexts(paragraphs)

    issues: list[dict] = []
    llm_review_candidates: list[dict] = []
    issues.extend(review_intro_requirements(paragraphs))
    issues.extend(review_abbreviations(paragraphs))
    list_issues, list_candidates = review_list_context(paragraphs)
    issues.extend(list_issues)
    llm_review_candidates.extend(list_candidates)
    issues.extend(review_heading_structure(paragraphs))
    issues.extend(review_reference_mentions(paragraphs))
    issues.extend(review_adoption_consistency(paragraphs))
    enrich_issue_context(issues, contexts)

    return {
        "file": str(docx_path),
        "paragraph_count": len(paragraphs),
        "issue_count": len(issues),
        "decision_summary": {
            "auto_fix": sum(1 for issue in issues if issue["decision_type"] == "auto_fix"),
            "ask_user": sum(1 for issue in issues if issue["decision_type"] == "ask_user"),
            "report_only": sum(1 for issue in issues if issue["decision_type"] == "report_only"),
        },
        "llm_review_candidate_count": len(llm_review_candidates),
        "llm_review_candidates": llm_review_candidates,
        "issues": issues,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", required=True)
    parser.add_argument("--output")
    args = parser.parse_args()

    report = review_document(Path(args.input))

    if args.output:
        out_path = Path(args.output)
        out_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    if report["issue_count"] == 0:
        print("审查完成：未发现问题")
        return

    print(f"审查完成：发现 {report['issue_count']} 个问题")
    for issue in report["issues"]:
        print(f"- [{issue['severity']}] {issue['location']} {issue['message']}")
        if issue["excerpt"]:
            print(f"  摘录：{issue['excerpt']}")


if __name__ == "__main__":
    main()
