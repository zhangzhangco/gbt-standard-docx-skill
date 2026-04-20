#!/usr/bin/env python3
from __future__ import annotations

import argparse
import copy
import json
import re
import tempfile
import zipfile
from datetime import date, datetime
from pathlib import Path
from xml.etree import ElementTree as ET

import yaml

NS = {
    "w": "http://schemas.openxmlformats.org/wordprocessingml/2006/main",
}
REL_NS = "http://schemas.openxmlformats.org/package/2006/relationships"
DOC_REL_NS = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
WP_NS = "http://schemas.openxmlformats.org/drawingml/2006/wordprocessingDrawing"
A_NS = "http://schemas.openxmlformats.org/drawingml/2006/main"
PIC_NS = "http://schemas.openxmlformats.org/drawingml/2006/picture"

INLINE_FIELD_RE = re.compile(r"\{\{(ref|refnum|page):([A-Za-z0-9_]+)\}\}")
SUPERSCRIPT_RE = re.compile(r"[²³]|(?<=[A-Za-zµμΩΩ/])(?:2|3)(?=\b)")
SUPERSCRIPT_CHAR_MAP = {
    "²": "2",
    "³": "3",
}


def qn(tag: str) -> str:
    return f"{{{NS['w']}}}{tag}"


def load_input(path: Path) -> dict:
    text = path.read_text(encoding="utf-8")
    if path.suffix.lower() == ".json":
        return json.loads(text)
    return yaml.safe_load(text)


def load_yaml(path: Path) -> dict:
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def scalar_to_text(value) -> str:
    if value is None:
        return ""
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    return str(value)


def sanitize_bookmark_name(value: str) -> str:
    text = re.sub(r"[^A-Za-z0-9_]+", "_", scalar_to_text(value).strip())
    text = re.sub(r"_+", "_", text).strip("_")
    if not text:
        return "ref"
    if text[0].isdigit():
        text = f"ref_{text}"
    return text[:40]


def iter_xml_files(root: Path) -> list[Path]:
    return sorted(root.rglob("*.xml"))


def read_core_title(template: Path) -> str:
    with zipfile.ZipFile(template) as zf:
        root = ET.fromstring(zf.read("docProps/core.xml"))
    return root.findtext("{http://purl.org/dc/elements/1.1/}title", "")


def style_name_to_id(template: Path) -> dict[str, str]:
    with zipfile.ZipFile(template) as zf:
        root = ET.fromstring(zf.read("word/styles.xml"))
    mapping = {}
    for style in root.findall("w:style", NS):
        name = style.find("w:name", NS)
        if name is None:
            continue
        mapping[name.get(qn("val"), "")] = style.get(qn("styleId"), "")
    return mapping


def style_id_to_name(template: Path) -> dict[str, str]:
    with zipfile.ZipFile(template) as zf:
        root = ET.fromstring(zf.read("word/styles.xml"))
    mapping = {}
    for style in root.findall("w:style", NS):
        style_id = style.get(qn("styleId"), "")
        name = style.find("w:name", NS)
        mapping[style_id] = "" if name is None else name.get(qn("val"), "")
    return mapping


def template_bookmark_names(template: Path) -> set[str]:
    names: set[str] = set()
    with zipfile.ZipFile(template) as zf:
        for part in [name for name in zf.namelist() if name.startswith("word/") and name.endswith(".xml")]:
            root = ET.fromstring(zf.read(part))
            for bookmark in root.findall(".//w:bookmarkStart", NS):
                name = bookmark.get(qn("name"), "")
                if name:
                    names.add(name)
    return names


def infer_profile_path(template: Path) -> Path:
    title = read_core_title(template)
    base = Path("templates/gbt/profiles")
    if "行业标准" in title or "行业标准" in template.name:
        return base / "industry.yaml"
    return base / "national.yaml"


def normalize_content_types(path: Path) -> None:
    text = path.read_text(encoding="utf-8")
    text = text.replace(
        "application/vnd.openxmlformats-officedocument.wordprocessingml.template.main+xml",
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml",
    )
    path.write_text(text, encoding="utf-8")


def replace_text_everywhere(root: ET.Element, mapping: dict[str, str]) -> None:
    for text_node in root.findall(".//w:t", NS):
        value = text_node.text or ""
        if value in mapping:
            text_node.text = mapping[value]


def replace_paragraphs_by_exact_text(root: ET.Element, mapping: dict[str, str]) -> None:
    for paragraph in root.findall(".//w:p", NS):
        if paragraph.find(".//w:ffData", NS) is not None:
            continue
        value = paragraph_text(paragraph)
        if value in mapping:
            set_paragraph_text(paragraph, mapping[value])


def mark_fields_dirty(root: ET.Element) -> None:
    for fld_simple in root.findall(".//w:fldSimple", NS):
        fld_simple.set(qn("dirty"), "true")
    for fld_char in root.findall(".//w:fldChar", NS):
        fld_type = fld_char.get(qn("fldCharType"))
        if fld_type in {"begin", "separate"}:
            fld_char.set(qn("dirty"), "true")


def ensure_update_fields(settings_path: Path) -> None:
    root = ET.parse(settings_path).getroot()
    update_fields = root.find("w:updateFields", NS)
    if update_fields is None:
        update_fields = ET.SubElement(root, qn("updateFields"))
    update_fields.set(qn("val"), "true")
    ET.ElementTree(root).write(settings_path, encoding="utf-8", xml_declaration=True)


def set_sdt_text_by_tag(root: ET.Element, tag: str, value: str) -> None:
    for sdt in root.findall(".//w:sdt", NS):
        pr = sdt.find("w:sdtPr", NS)
        if pr is None:
            continue
        tag_el = pr.find("w:tag", NS)
        if tag_el is None:
            continue
        if tag_el.get(qn("val")) != tag:
            continue
        content = sdt.find("w:sdtContent", NS)
        if content is None:
            continue
        for text_node in content.findall(".//w:t", NS):
            text_node.text = value
            return


def find_sdt_by_control_type(root: ET.Element, control_type: str) -> ET.Element | None:
    for sdt in root.findall(".//w:sdt", NS):
        pr = sdt.find("w:sdtPr", NS)
        if pr is None:
            continue
        if control_type == "dropdown" and pr.find("w:dropDownList", NS) is not None:
            return sdt
        if control_type == "combobox" and pr.find("w:comboBox", NS) is not None:
            return sdt
    return None


def set_first_text_in_sdt(sdt: ET.Element | None, value: str) -> None:
    if sdt is None:
        return
    for text_node in sdt.findall(".//w:t", NS):
        text_node.text = value
        return


def set_form_field_values(root: ET.Element, mapping: dict[str, str]) -> None:
    set_form_field_values_with_format(root, mapping, {})


def ensure_child(parent: ET.Element, tag: str) -> ET.Element:
    child = parent.find(tag, NS)
    if child is None:
        child = ET.SubElement(parent, qn(tag.split(":")[1]))
    return child


def apply_run_format(run: ET.Element, fmt: dict) -> None:
    rpr = run.find("w:rPr", NS)
    if rpr is None:
        rpr = ET.Element(qn("rPr"))
        run.insert(0, rpr)
    if fmt.get("font_ascii") or fmt.get("font_eastAsia") or fmt.get("font_hansi"):
        rfonts = rpr.find("w:rFonts", NS)
        if rfonts is None:
            rfonts = ET.SubElement(rpr, qn("rFonts"))
        if fmt.get("font_ascii"):
            rfonts.set(qn("ascii"), fmt["font_ascii"])
        if fmt.get("font_eastAsia"):
            rfonts.set(qn("eastAsia"), fmt["font_eastAsia"])
        if fmt.get("font_hansi"):
            rfonts.set(qn("hAnsi"), fmt["font_hansi"])
    if "bold" in fmt:
        bold = rpr.find("w:b", NS)
        if bold is None:
            bold = ET.SubElement(rpr, qn("b"))
        bold.set(qn("val"), "1" if fmt["bold"] else "0")
    if "font_size" in fmt:
        size = rpr.find("w:sz", NS)
        if size is None:
            size = ET.SubElement(rpr, qn("sz"))
        size.set(qn("val"), str(fmt["font_size"]))
        size_cs = rpr.find("w:szCs", NS)
        if size_cs is None:
            size_cs = ET.SubElement(rpr, qn("szCs"))
        size_cs.set(qn("val"), str(fmt["font_size"]))


def apply_paragraph_format(paragraph: ET.Element, fmt: dict) -> None:
    ppr = paragraph.find("w:pPr", NS)
    if ppr is None:
        ppr = ET.Element(qn("pPr"))
        paragraph.insert(0, ppr)
    if "paragraph_align" in fmt:
        jc = ppr.find("w:jc", NS)
        if jc is None:
            jc = ET.SubElement(ppr, qn("jc"))
        jc.set(qn("val"), fmt["paragraph_align"])
    if "first_line" in fmt:
        ind = ppr.find("w:ind", NS)
        if ind is None:
            ind = ET.SubElement(ppr, qn("ind"))
        ind.set(qn("firstLine"), str(fmt["first_line"]))
        ind.set(qn("firstLineChars"), "0")


def set_form_field_values_with_format(root: ET.Element, mapping: dict[str, str], formats: dict[str, dict]) -> None:
    for paragraph in root.findall(".//w:p", NS):
        children = list(paragraph)
        i = 0
        while i < len(children):
            node = children[i]
            if node.tag != qn("r"):
                i += 1
                continue
            fld = node.find("w:fldChar", NS)
            if fld is None or fld.get(qn("fldCharType")) != "begin":
                i += 1
                continue
            ff = fld.find("w:ffData", NS)
            if ff is None:
                i += 1
                continue
            name = ff.find("w:name", NS)
            field_name = "" if name is None else name.get(qn("val"), "")
            if field_name not in mapping:
                i += 1
                continue
            separate = None
            end = None
            j = i + 1
            while j < len(children):
                other = children[j]
                if other.tag == qn("r"):
                    other_fld = other.find("w:fldChar", NS)
                    if other_fld is not None:
                        fld_type = other_fld.get(qn("fldCharType"))
                        if fld_type == "separate" and separate is None:
                            separate = j
                        elif fld_type == "end":
                            end = j
                            break
                j += 1
            if separate is None or end is None:
                i += 1
                continue
            text_nodes = []
            text_runs = []
            for child in children[separate + 1:end]:
                if child.tag == qn("r"):
                    text_runs.append(child)
                text_nodes.extend(child.findall(".//w:t", NS))
            if text_nodes:
                text_nodes[0].text = mapping[field_name]
                for text_node in text_nodes[1:]:
                    text_node.text = ""
            field_format = formats.get(field_name, {})
            if field_format:
                apply_paragraph_format(paragraph, field_format)
                for run in text_runs:
                    apply_run_format(run, field_format)
            i = end + 1


def max_bookmark_id(root: ET.Element) -> int:
    max_id = 0
    for node in root.findall(".//w:bookmarkStart", NS):
        try:
            max_id = max(max_id, int(node.get(qn("id"), "0")))
        except ValueError:
            continue
    for node in root.findall(".//w:bookmarkEnd", NS):
        try:
            max_id = max(max_id, int(node.get(qn("id"), "0")))
        except ValueError:
            continue
    return max_id


def collect_bookmark_manifest(root: ET.Element, style_names: dict[str, str], excluded_names: set[str] | None = None) -> list[dict]:
    manifest: list[dict] = []
    excluded = excluded_names or set()
    seen: set[str] = set()
    for paragraph in root.findall(".//w:body/w:p", NS):
        text = paragraph_text(paragraph)
        ppr = paragraph.find("w:pPr", NS)
        style_id = ""
        if ppr is not None:
            pstyle = ppr.find("w:pStyle", NS)
            if pstyle is not None:
                style_id = pstyle.get(qn("val"), "")
        for bookmark in paragraph.findall("w:bookmarkStart", NS):
            name = bookmark.get(qn("name"), "")
            if not name or name.startswith("_") or name in seen or name in excluded:
                continue
            seen.add(name)
            manifest.append(
                {
                    "name": name,
                    "paragraph_text": text,
                    "style_id": style_id,
                    "style_name": style_names.get(style_id, ""),
                }
            )
    return manifest


def collect_field_manifest(root: ET.Element) -> list[dict]:
    manifest: list[dict] = []
    for instr in root.findall(".//w:instrText", NS):
        text = "".join(instr.itertext()).strip()
        if not text:
            continue
        normalized = " ".join(text.split())
        if normalized.startswith("REF "):
            parts = normalized.split()
            if len(parts) >= 2:
                manifest.append({"kind": "REF", "target": parts[1], "instruction": normalized})
        elif normalized.startswith("PAGEREF "):
            parts = normalized.split()
            if len(parts) >= 2:
                manifest.append({"kind": "PAGEREF", "target": parts[1], "instruction": normalized})
        elif normalized.startswith("TOC "):
            manifest.append({"kind": "TOC", "target": "", "instruction": normalized})
    return manifest


def validate_field_targets(bookmarks: list[dict], fields: list[dict]) -> list[str]:
    names = {item["name"] for item in bookmarks}
    missing = []
    for field in fields:
        target = field.get("target", "")
        if field.get("kind") in {"REF", "PAGEREF"} and target and target not in names:
            missing.append(target)
    return sorted(set(missing))


def write_reference_manifest(output: Path, bookmarks: list[dict], fields: list[dict]) -> Path:
    target = output.with_suffix(".refs.json")
    payload = {
        "docx": str(output.resolve()),
        "bookmarks": bookmarks,
        "fields": fields,
    }
    target.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return target


def split_standard_number(value: str) -> tuple[str, str, str]:
    text = scalar_to_text(value).strip()
    if not text:
        return "", "", ""
    if " " in text:
        prefix, rest = text.split(" ", 1)
    else:
        prefix, rest = "", text
    parts = re.split(r"[—-]", rest, maxsplit=1)
    if len(parts) == 2:
        return prefix, parts[0].strip(), parts[1].strip()
    return prefix, rest.strip(), ""


def split_date(value) -> tuple[str, str, str]:
    text = scalar_to_text(value).strip()
    if not text:
        return "", "", ""
    parts = text.split("-")
    if len(parts) >= 3:
        return parts[0], parts[1], parts[2]
    if len(parts) == 2:
        return parts[0], parts[1], ""
    return text, "", ""


def build_computed(cover: dict, profile_id: str) -> dict[str, str]:
    prefix, front, back = split_standard_number(cover.get("standard_number", ""))
    py, pm, pd = split_date(cover.get("published_date", ""))
    iy, im, iday = split_date(cover.get("implementation_date", ""))
    system_name = scalar_to_text(cover.get("standard_system_name", "")).strip()
    replaced_standard = scalar_to_text(cover.get("replaced_standard", "")).strip()
    computed = {
        "standard_prefix": prefix,
        "standard_number_front": front,
        "standard_number_back": back,
        "published_year": py,
        "published_month": pm,
        "published_day": pd,
        "implementation_year": iy,
        "implementation_month": im,
        "implementation_day": iday,
        "banner_line": "中华人民共和国国家标准",
        "replaced_line": "",
    }
    if replaced_standard:
        computed["replaced_line"] = f"代替 {replaced_standard}"
    if profile_id == "industry" and system_name:
        computed["banner_line"] = f"中华人民共和国{system_name}行业标准"
    return computed


def resolve_ref(ref: str, data: dict, computed: dict) -> str:
    if ref.startswith("cover."):
        key = ref.split(".", 1)[1]
        return scalar_to_text(data.get("cover", {}).get(key, ""))
    if ref.startswith("computed."):
        key = ref.split(".", 1)[1]
        return scalar_to_text(computed.get(key, ""))
    return scalar_to_text(ref)


def paragraph_text(paragraph: ET.Element) -> str:
    return "".join(t.text or "" for t in paragraph.findall(".//w:t", NS)).strip()


def append_run_with_text(paragraph: ET.Element, text: str, superscript: bool = False) -> None:
    if not text:
        return
    run = ET.SubElement(paragraph, qn("r"))
    if superscript:
        rpr = ET.SubElement(run, qn("rPr"))
        vert = ET.SubElement(rpr, qn("vertAlign"))
        vert.set(qn("val"), "superscript")
    text_node = ET.SubElement(run, qn("t"))
    text_node.set("{http://www.w3.org/XML/1998/namespace}space", "preserve")
    text_node.text = text


def append_text_run(paragraph: ET.Element, text: str) -> None:
    if not text:
        return
    last = 0
    for match in SUPERSCRIPT_RE.finditer(text):
        if match.start() > last:
            append_run_with_text(paragraph, text[last:match.start()])
        token = match.group(0)
        append_run_with_text(paragraph, SUPERSCRIPT_CHAR_MAP.get(token, token), superscript=True)
        last = match.end()
    if last < len(text):
        append_run_with_text(paragraph, text[last:])


def append_field_runs(paragraph: ET.Element, field_kind: str, bookmark: str) -> None:
    instruction_map = {
        "ref": f" REF {bookmark} \\\\h ",
        "refnum": f" REF {bookmark} \\\\h \\\\n ",
        "page": f" PAGEREF {bookmark} \\\\h ",
    }
    instruction = instruction_map[field_kind]

    begin_run = ET.SubElement(paragraph, qn("r"))
    begin = ET.SubElement(begin_run, qn("fldChar"))
    begin.set(qn("fldCharType"), "begin")
    begin.set(qn("dirty"), "true")

    instr_run = ET.SubElement(paragraph, qn("r"))
    instr = ET.SubElement(instr_run, qn("instrText"))
    instr.set("{http://www.w3.org/XML/1998/namespace}space", "preserve")
    instr.text = instruction

    separate_run = ET.SubElement(paragraph, qn("r"))
    separate = ET.SubElement(separate_run, qn("fldChar"))
    separate.set(qn("fldCharType"), "separate")
    separate.set(qn("dirty"), "true")

    end_run = ET.SubElement(paragraph, qn("r"))
    end = ET.SubElement(end_run, qn("fldChar"))
    end.set(qn("fldCharType"), "end")


def append_inline_text(paragraph: ET.Element, text: str) -> None:
    if "\n" in text:
        parts = text.split("\n")
        for idx, part in enumerate(parts):
            if idx > 0:
                run = ET.SubElement(paragraph, qn("r"))
                ET.SubElement(run, qn("br"))
            if part:
                append_inline_text(paragraph, part)
        return
    last = 0
    for match in INLINE_FIELD_RE.finditer(text):
        if match.start() > last:
            append_text_run(paragraph, text[last:match.start()])
        append_field_runs(paragraph, match.group(1), match.group(2))
        last = match.end()
    if last < len(text):
        append_text_run(paragraph, text[last:])


def apply_bookmark(paragraph: ET.Element, bookmark_name: str, bookmark_id: int) -> None:
    if not bookmark_name:
        return
    children = list(paragraph)
    insert_pos = 1 if children and children[0].tag == qn("pPr") else 0
    start = ET.Element(qn("bookmarkStart"))
    start.set(qn("id"), str(bookmark_id))
    start.set(qn("name"), bookmark_name)
    end = ET.Element(qn("bookmarkEnd"))
    end.set(qn("id"), str(bookmark_id))
    paragraph.insert(insert_pos, start)
    paragraph.append(end)


def next_bookmark_id(state: dict) -> int:
    state["next_id"] = int(state.get("next_id", 0)) + 1
    return state["next_id"]


def resolve_bookmark_name(explicit: str, generated: str) -> str:
    explicit_text = scalar_to_text(explicit).strip()
    if explicit_text:
        return sanitize_bookmark_name(explicit_text)
    return sanitize_bookmark_name(generated)


def style_name_to_style_id(styles: dict[str, str], style_name: str = "", style_key: str = "") -> str:
    if style_name:
        return styles.get(style_name, "")
    if style_key:
        return scalar_to_text(styles.get(style_key, "")).strip()
    return ""


def resolve_profile_style(style_ids: dict[str, str], profile: dict, logical_key: str, default_style_name: str) -> str:
    override = scalar_to_text(profile.get("heading_style_overrides", {}).get(logical_key, "")).strip()
    style_name = override or default_style_name
    return style_ids[style_name]


def make_paragraph(
    text: str,
    style_id: str = "",
    page_break_before: bool = False,
    bookmark_name: str = "",
    bookmark_state: dict | None = None,
    suppress_numbering: bool = False,
) -> ET.Element:
    paragraph = ET.Element(qn("p"))
    if style_id or page_break_before:
        ppr = ET.SubElement(paragraph, qn("pPr"))
        if style_id:
            pstyle = ET.SubElement(ppr, qn("pStyle"))
            pstyle.set(qn("val"), style_id)
        if suppress_numbering:
            num_pr = ET.SubElement(ppr, qn("numPr"))
            ilvl = ET.SubElement(num_pr, qn("ilvl"))
            ilvl.set(qn("val"), "0")
            num_id = ET.SubElement(num_pr, qn("numId"))
            num_id.set(qn("val"), "0")
    if page_break_before:
        break_run = ET.SubElement(paragraph, qn("r"))
        br = ET.SubElement(break_run, qn("br"))
        br.set(qn("type"), "page")
    append_inline_text(paragraph, text)
    if bookmark_name and bookmark_state is not None:
        apply_bookmark(paragraph, bookmark_name, next_bookmark_id(bookmark_state))
    return paragraph


def make_end_line_paragraph(
    image_rel_id: str,
    style_id: str = "",
    bookmark_name: str = "",
    bookmark_state: dict | None = None,
) -> ET.Element:
    paragraph = ET.Element(qn("p"))
    ppr = ET.SubElement(paragraph, qn("pPr"))
    if style_id:
        pstyle = ET.SubElement(ppr, qn("pStyle"))
        pstyle.set(qn("val"), style_id)
    jc = ET.SubElement(ppr, qn("jc"))
    jc.set(qn("val"), "center")
    ind = ET.SubElement(ppr, qn("ind"))
    ind.set(qn("firstLine"), "0")
    ind.set(qn("firstLineChars"), "0")

    run = ET.SubElement(paragraph, qn("r"))
    drawing = ET.SubElement(run, qn("drawing"))
    inline = ET.SubElement(
        drawing,
        f"{{{WP_NS}}}inline",
        {"distT": "0", "distB": "0", "distL": "114300", "distR": "114300"},
    )
    ET.SubElement(inline, f"{{{WP_NS}}}extent", {"cx": "1485900", "cy": "317500"})
    ET.SubElement(inline, f"{{{WP_NS}}}effectExtent", {"l": "0", "t": "0", "r": "0", "b": "6350"})
    ET.SubElement(inline, f"{{{WP_NS}}}docPr", {"id": "1001", "name": "结束线", "descr": "ending-line"})
    ET.SubElement(inline, f"{{{WP_NS}}}cNvGraphicFramePr")
    graphic = ET.SubElement(inline, f"{{{A_NS}}}graphic")
    graphic_data = ET.SubElement(graphic, f"{{{A_NS}}}graphicData", {"uri": PIC_NS})
    pic = ET.SubElement(graphic_data, f"{{{PIC_NS}}}pic")
    nv_pic_pr = ET.SubElement(pic, f"{{{PIC_NS}}}nvPicPr")
    ET.SubElement(nv_pic_pr, f"{{{PIC_NS}}}cNvPr", {"id": "1001", "name": "结束线", "descr": "ending-line"})
    ET.SubElement(nv_pic_pr, f"{{{PIC_NS}}}cNvPicPr")
    blip_fill = ET.SubElement(pic, f"{{{PIC_NS}}}blipFill")
    ET.SubElement(blip_fill, f"{{{A_NS}}}blip", {f"{{{DOC_REL_NS}}}embed": image_rel_id})
    stretch = ET.SubElement(blip_fill, f"{{{A_NS}}}stretch")
    ET.SubElement(stretch, f"{{{A_NS}}}fillRect")
    sp_pr = ET.SubElement(pic, f"{{{PIC_NS}}}spPr")
    xfrm = ET.SubElement(sp_pr, f"{{{A_NS}}}xfrm")
    ET.SubElement(xfrm, f"{{{A_NS}}}off", {"x": "0", "y": "0"})
    ET.SubElement(xfrm, f"{{{A_NS}}}ext", {"cx": "1485900", "cy": "317500"})
    prst_geom = ET.SubElement(sp_pr, f"{{{A_NS}}}prstGeom", {"prst": "rect"})
    ET.SubElement(prst_geom, f"{{{A_NS}}}avLst")

    if bookmark_name and bookmark_state is not None:
        apply_bookmark(paragraph, bookmark_name, next_bookmark_id(bookmark_state))
    return paragraph


def ensure_run(paragraph: ET.Element) -> ET.Element:
    run = ET.Element(qn("r"))
    text = ET.SubElement(run, qn("t"))
    text.set("{http://www.w3.org/XML/1998/namespace}space", "preserve")
    paragraph.append(run)
    return text


def make_text_run(text: str, spacing: int | None = None) -> ET.Element:
    run = ET.Element(qn("r"))
    if spacing is not None:
        rpr = ET.SubElement(run, qn("rPr"))
        spacing_el = ET.SubElement(rpr, qn("spacing"))
        spacing_el.set(qn("val"), str(spacing))
    text_el = ET.SubElement(run, qn("t"))
    text_el.set("{http://www.w3.org/XML/1998/namespace}space", "preserve")
    text_el.text = text
    return run


def apply_heading_spacing(paragraph: ET.Element, full_text: str, spaced_chars: int, spacing: int) -> None:
    ppr = paragraph.find("w:pPr", NS)
    has_page_break = paragraph.find(".//w:br[@w:type='page']", NS) is not None
    bookmark_starts = [copy.deepcopy(node) for node in paragraph.findall("w:bookmarkStart", NS)]
    bookmark_ends = [copy.deepcopy(node) for node in paragraph.findall("w:bookmarkEnd", NS)]
    paragraph.clear()
    if ppr is not None:
        paragraph.append(copy.deepcopy(ppr))
    if has_page_break:
        run = ET.SubElement(paragraph, qn("r"))
        br = ET.SubElement(run, qn("br"))
        br.set(qn("type"), "page")
    for node in bookmark_starts:
        paragraph.append(node)
    head = full_text[:spaced_chars]
    tail = full_text[spaced_chars:]
    if head:
        paragraph.append(make_text_run(head, spacing=spacing))
    if tail:
        paragraph.append(make_text_run(tail))
    for node in bookmark_ends:
        paragraph.append(node)


def set_paragraph_text(paragraph: ET.Element, value: str) -> None:
    keep = []
    ppr = paragraph.find("w:pPr", NS)
    if ppr is not None:
        keep.append(copy.deepcopy(ppr))
    paragraph.clear()
    for item in keep:
        paragraph.append(item)
    text_node = ensure_run(paragraph)
    text_node.text = value


def clone_paragraph(paragraph: ET.Element) -> ET.Element:
    return copy.deepcopy(paragraph)


def insert_section_paragraphs(body: ET.Element, heading_text: str, values: list[str]) -> None:
    children = list(body)
    heading_index = None
    for idx, child in enumerate(children):
        if child.tag == qn("p") and paragraph_text(child) == heading_text:
            heading_index = idx
            break
    if heading_index is None or not values:
        return

    anchor = None
    insert_at = heading_index + 1
    for idx in range(heading_index + 1, len(children)):
        child = children[idx]
        insert_at = idx + 1
        if child.tag != qn("p"):
            continue
        if paragraph_text(child):
            continue
        anchor = child
        break

    if anchor is None:
        anchor = ET.Element(qn("p"))
        body.insert(insert_at, anchor)
        children = list(body)
        insert_at = list(body).index(anchor) + 1

    set_paragraph_text(anchor, values[0])
    last = anchor
    for value in values[1:]:
        new_paragraph = clone_paragraph(anchor)
        set_paragraph_text(new_paragraph, value)
        body.insert(list(body).index(last) + 1, new_paragraph)
        last = new_paragraph


def find_heading_index(body: ET.Element, heading_text: str) -> int | None:
    for idx, child in enumerate(list(body)):
        if child.tag == qn("p") and paragraph_text(child) == heading_text:
            return idx
    return None


def find_section_end_index(body: ET.Element, heading_text: str, next_headings: list[str]) -> int | None:
    heading_idx = find_heading_index(body, heading_text)
    if heading_idx is None:
        return None
    children = list(body)
    for idx in range(heading_idx + 1, len(children)):
        child = children[idx]
        if child.tag == qn("sectPr"):
            return idx
        if child.tag == qn("p") and paragraph_text(child) in next_headings:
            return idx
    return len(children)


def clear_empty_paragraphs(body: ET.Element, start: int, end: int) -> None:
    children = list(body)
    removable = [
        child
        for child in children[start:end]
        if child.tag == qn("p") and not paragraph_text(child)
    ]
    for child in removable:
        body.remove(child)


def insert_section_blocks(body: ET.Element, heading_text: str, next_headings: list[str], blocks: list[ET.Element]) -> None:
    heading_idx = find_heading_index(body, heading_text)
    end_idx = find_section_end_index(body, heading_text, next_headings)
    if heading_idx is None or end_idx is None:
        return
    clear_empty_paragraphs(body, heading_idx + 1, end_idx)
    if not blocks:
        return
    end_idx = find_section_end_index(body, heading_text, next_headings)
    if end_idx is None:
        return
    for offset, block in enumerate(blocks):
        body.insert(end_idx + offset, block)


def insert_blocks_before_heading(body: ET.Element, heading_text: str, blocks: list[ET.Element]) -> None:
    idx = find_heading_index(body, heading_text)
    if idx is None or not blocks:
        return
    for offset, block in enumerate(blocks):
        body.insert(idx + offset, block)


def insert_blocks_before_first_heading(body: ET.Element, heading_texts: list[str], blocks: list[ET.Element]) -> None:
    if not blocks:
        return
    for heading_text in heading_texts:
        idx = find_heading_index(body, heading_text)
        if idx is None:
            continue
        for offset, block in enumerate(blocks):
            body.insert(idx + offset, block)
        return


def insert_blocks_before_sdt_tag(body: ET.Element, tag: str, blocks: list[ET.Element]) -> bool:
    if not blocks:
        return False
    children = list(body)
    for idx, child in enumerate(children):
        if child.tag != qn("sdt"):
            continue
        pr = child.find("w:sdtPr", NS)
        if pr is None:
            continue
        tag_el = pr.find("w:tag", NS)
        if tag_el is None or tag_el.get(qn("val")) != tag:
            continue
        for offset, block in enumerate(blocks):
            body.insert(idx + offset, block)
        return True
    return False


def apply_special_heading_spacing(body: ET.Element) -> None:
    specs = {
        "目次": (1, 320),
        "前言": (1, 320),
        "引言": (1, 320),
        "参考文献": (3, 105),
    }
    for paragraph in body.findall("w:p", NS):
        text = paragraph_text(paragraph)
        if text not in specs:
            continue
        spaced_chars, spacing = specs[text]
        apply_heading_spacing(paragraph, text, spaced_chars, spacing)


def insert_blocks_before_sectpr(body: ET.Element, blocks: list[ET.Element]) -> None:
    if not blocks:
        return
    children = list(body)
    sect_idx = next((i for i, child in enumerate(children) if child.tag == qn("sectPr")), len(children))
    for offset, block in enumerate(blocks):
        body.insert(sect_idx + offset, block)


def build_major_section_blocks(
    title: str,
    paragraphs: list[str],
    heading_style: str,
    body_style: str,
    bookmark_state: dict | None = None,
) -> list[ET.Element]:
    if not paragraphs:
        return []
    blocks = [make_paragraph(title, heading_style, page_break_before=True, bookmark_state=bookmark_state)]
    blocks.extend(make_paragraph(text, body_style, bookmark_state=bookmark_state) for text in paragraphs)
    return blocks


def build_bibliography_blocks(items, heading_style: str, item_style: str, bookmark_state: dict | None = None) -> list[ET.Element]:
    values = [scalar_to_text(item).strip() for item in items or [] if scalar_to_text(item).strip()]
    if not values:
        return []
    blocks = [make_paragraph("参考文献", heading_style, page_break_before=True, bookmark_state=bookmark_state)]
    blocks.extend(make_paragraph(item, item_style, bookmark_state=bookmark_state) for item in values)
    return blocks


def make_field_paragraph(instruction: str, style_id: str = "", placeholder: str = "") -> ET.Element:
    paragraph = ET.Element(qn("p"))
    if style_id:
        ppr = ET.SubElement(paragraph, qn("pPr"))
        pstyle = ET.SubElement(ppr, qn("pStyle"))
        pstyle.set(qn("val"), style_id)

    begin_run = ET.SubElement(paragraph, qn("r"))
    begin = ET.SubElement(begin_run, qn("fldChar"))
    begin.set(qn("fldCharType"), "begin")
    begin.set(qn("dirty"), "true")

    instr_run = ET.SubElement(paragraph, qn("r"))
    instr = ET.SubElement(instr_run, qn("instrText"))
    instr.set("{http://www.w3.org/XML/1998/namespace}space", "preserve")
    instr.text = instruction

    separate_run = ET.SubElement(paragraph, qn("r"))
    separate = ET.SubElement(separate_run, qn("fldChar"))
    separate.set(qn("fldCharType"), "separate")
    separate.set(qn("dirty"), "true")

    if placeholder:
        text_node = ensure_run(paragraph)
        text_node.text = placeholder

    end_run = ET.SubElement(paragraph, qn("r"))
    end = ET.SubElement(end_run, qn("fldChar"))
    end.set(qn("fldCharType"), "end")
    return paragraph


def build_toc_blocks(toc: dict, styles: dict[str, str], bookmark_state: dict | None = None) -> list[ET.Element]:
    if not toc or not toc.get("enabled"):
        return []
    title = scalar_to_text(toc.get("title", "目次")).strip() or "目次"
    levels = int(toc.get("levels", 3) or 3)
    instruction = f'TOC \\\\o "1-{levels}" \\\\h \\\\z \\\\u'
    return [
        make_paragraph(title, styles["toc_heading"], page_break_before=True, bookmark_state=bookmark_state),
        make_field_paragraph(instruction, styles["toc_body"], placeholder="右键单击以更新目录。"),
        make_paragraph("", "", page_break_before=True),
    ]


def normalize_term_items(items) -> list[dict]:
    normalized = []
    for item in items or []:
        if isinstance(item, str):
            normalized.append({"term": item, "definition": [], "level": 1, "bookmark": ""})
        else:
            normalized.append({
                "term": scalar_to_text(item.get("term", "")),
                "definition": [scalar_to_text(x) for x in item.get("definition", [])],
                "level": int(item.get("level", 1) or 1),
                "bookmark": scalar_to_text(item.get("bookmark", "")).strip(),
            })
    return normalized


def normalize_example_items(items) -> list[dict]:
    normalized = []
    for item in items or []:
        if isinstance(item, str):
            normalized.append({"title": "", "paragraphs": [scalar_to_text(item)]})
            continue
        normalized.append(
            {
                "title": scalar_to_text(item.get("title", "")),
                "paragraphs": [scalar_to_text(x) for x in item.get("paragraphs", [])],
            }
        )
    return normalized


def next_sequence(context: dict, key: str) -> int:
    context[key] = int(context.get(key, 0)) + 1
    return context[key]


def format_number_token(value: str | int) -> str:
    return sanitize_bookmark_name(str(value))


def format_series_number(index: int, annex_id: str = "") -> str:
    if annex_id:
        return f"{annex_id}.{index}"
    return str(index)


def build_note_blocks(items, styles: dict[str, str], note_key: str = "注", bookmark_state: dict | None = None) -> list[ET.Element]:
    notes = [scalar_to_text(item).strip() for item in items or [] if scalar_to_text(item).strip()]
    if not notes:
        return []
    blocks: list[ET.Element] = []
    single = len(notes) == 1
    for idx, item in enumerate(notes, start=1):
        style = styles["note_single"] if single else styles["note_multi"]
        blocks.append(make_paragraph(item, style, bookmark_state=bookmark_state))
    return blocks


def build_table_paragraph(text: str, style_id: str, bold: bool = False) -> ET.Element:
    paragraph = make_paragraph(text, style_id)
    if bold:
        for run in paragraph.findall("w:r", NS):
            apply_run_format(run, {"bold": True})
    return paragraph


def build_table_element(rows, paragraph_style: str, header_rows: int = 1) -> ET.Element | None:
    normalized_rows = []
    for row in rows or []:
        if not isinstance(row, list):
            continue
        values = [scalar_to_text(cell) for cell in row]
        if any(value.strip() for value in values):
            normalized_rows.append(values)
    if not normalized_rows:
        return None

    col_count = max(len(row) for row in normalized_rows)
    total_width = 8600
    col_width = max(total_width // max(col_count, 1), 600)

    table = ET.Element(qn("tbl"))
    tbl_pr = ET.SubElement(table, qn("tblPr"))
    tbl_w = ET.SubElement(tbl_pr, qn("tblW"))
    tbl_w.set(qn("w"), "0")
    tbl_w.set(qn("type"), "auto")
    tbl_layout = ET.SubElement(tbl_pr, qn("tblLayout"))
    tbl_layout.set(qn("type"), "fixed")
    tbl_jc = ET.SubElement(tbl_pr, qn("jc"))
    tbl_jc.set(qn("val"), "center")
    tbl_borders = ET.SubElement(tbl_pr, qn("tblBorders"))
    for edge in ("top", "left", "bottom", "right", "insideH", "insideV"):
        border = ET.SubElement(tbl_borders, qn(edge))
        border.set(qn("val"), "single")
        border.set(qn("sz"), "4")
        border.set(qn("space"), "0")
        border.set(qn("color"), "000000")

    tbl_grid = ET.SubElement(table, qn("tblGrid"))
    for _ in range(col_count):
        grid_col = ET.SubElement(tbl_grid, qn("gridCol"))
        grid_col.set(qn("w"), str(col_width))

    for row_idx, row in enumerate(normalized_rows):
        tr = ET.SubElement(table, qn("tr"))
        for col_idx in range(col_count):
            tc = ET.SubElement(tr, qn("tc"))
            tc_pr = ET.SubElement(tc, qn("tcPr"))
            tc_w = ET.SubElement(tc_pr, qn("tcW"))
            tc_w.set(qn("w"), str(col_width))
            tc_w.set(qn("type"), "dxa")
            v_align = ET.SubElement(tc_pr, qn("vAlign"))
            v_align.set(qn("val"), "center")
            text = row[col_idx] if col_idx < len(row) else ""
            tc.append(build_table_paragraph(text, paragraph_style, bold=row_idx < header_rows))
    return table


def build_figure_or_table_blocks(
    block: dict,
    styles: dict[str, str],
    context: dict,
    annex_id: str = "",
    bookmark_state: dict | None = None,
    render_caption_english: bool = False,
) -> list[ET.Element]:
    block_type = block.get("type", "")
    if block_type not in {"figure", "table"}:
        return []
    number = format_series_number(next_sequence(context, block_type), annex_id)
    title = scalar_to_text(block.get("title", "")).strip()
    title_en = scalar_to_text(block.get("title_en", "")).strip()
    body_style = styles["body"]
    if block_type == "figure":
        zh_style = styles["annex_figure_title"] if annex_id else styles["figure_title"]
        en_style = styles["annex_figure_title"] if annex_id else styles["figure_title_en"]
        zh_prefix = "图"
        en_prefix = "Figure"
    else:
        zh_style = styles["annex_table_title"] if annex_id else styles["table_title"]
        en_style = styles["annex_table_title"] if annex_id else styles["table_title_en"]
        zh_prefix = "表"
        en_prefix = "Table"
    generated_bookmark = f"{'fig' if block_type == 'figure' else 'table'}_{format_number_token(number)}"
    bookmark_name = resolve_bookmark_name(block.get("bookmark", ""), generated_bookmark)
    display_title = title
    suppress_numbering = False
    if annex_id:
        display_title = f"{zh_prefix}{number} {title}".strip()
        suppress_numbering = True
    blocks = [
        make_paragraph(
            display_title,
            zh_style,
            bookmark_name=bookmark_name,
            bookmark_state=bookmark_state,
            suppress_numbering=suppress_numbering,
        )
    ]
    if title_en and render_caption_english:
        display_title_en = title_en
        if annex_id:
            display_title_en = f"{en_prefix} {number} {title_en}".strip()
        blocks.append(
            make_paragraph(
                display_title_en,
                en_style,
                bookmark_state=bookmark_state,
                suppress_numbering=suppress_numbering,
            )
        )
    if block_type == "table":
        header_rows = int(block.get("header_rows", 1) or 1)
        table_el = build_table_element(block.get("rows", []), styles["table_cell"], header_rows=header_rows)
        if table_el is not None:
            blocks.append(table_el)
    for paragraph in block.get("paragraphs", []) or []:
        text = scalar_to_text(paragraph).strip()
        if text:
            blocks.append(make_paragraph(text, body_style, bookmark_state=bookmark_state))
    blocks.extend(build_note_blocks(block.get("notes", []), styles, note_key="注", bookmark_state=bookmark_state))
    return blocks


def build_example_blocks(block: dict, styles: dict[str, str], bookmark_state: dict | None = None) -> list[ET.Element]:
    items = normalize_example_items(block.get("items", []))
    if not items:
        return []
    blocks: list[ET.Element] = []
    single = len(items) == 1
    for idx, item in enumerate(items, start=1):
        paragraphs = [x for x in item.get("paragraphs", []) if x.strip()]
        title = item.get("title", "").strip()
        first_line = title or (paragraphs[0] if paragraphs else "")
        if not first_line:
            continue
        style = styles["example_single"] if single else styles["example_multi"]
        blocks.append(make_paragraph(first_line, style, bookmark_state=bookmark_state))
        remaining = paragraphs if title else paragraphs[1:]
        for paragraph in remaining:
            blocks.append(make_paragraph(paragraph, styles["example_body"], bookmark_state=bookmark_state))
    return blocks


def build_content_blocks(
    items,
    styles: dict[str, str],
    context: dict | None = None,
    annex_id: str = "",
    bookmark_state: dict | None = None,
    render_caption_english: bool = False,
) -> list[ET.Element]:
    blocks: list[ET.Element] = []
    local_context = context if context is not None else {"figure": 0, "table": 0}
    for item in items or []:
        if isinstance(item, str):
            text = scalar_to_text(item).strip()
            if text:
                blocks.append(make_paragraph(text, styles["body"], bookmark_state=bookmark_state))
            continue
        if not isinstance(item, dict):
            continue
        block_type = scalar_to_text(item.get("type", "paragraph")).strip()
        if block_type == "paragraph":
            text = scalar_to_text(item.get("text", "")).strip()
            if text:
                blocks.append(
                    make_paragraph(
                        text,
                        styles["body"],
                        bookmark_name=scalar_to_text(item.get("bookmark", "")).strip(),
                        bookmark_state=bookmark_state,
                    )
                )
        elif block_type == "styled_paragraph":
            text = scalar_to_text(item.get("text", "")).strip()
            style_lookup = styles.get("__style_name_map", styles)
            style_id = style_name_to_style_id(
                style_lookup,
                scalar_to_text(item.get("style_name", "")).strip(),
                scalar_to_text(item.get("style_key", "")).strip(),
            )
            if text and style_id:
                blocks.append(
                    make_paragraph(
                        text,
                        style_id,
                        bookmark_name=scalar_to_text(item.get("bookmark", "")).strip(),
                        bookmark_state=bookmark_state,
                    )
                )
        elif block_type in {"figure", "table"}:
            blocks.extend(
                build_figure_or_table_blocks(
                    item,
                    styles,
                    local_context,
                    annex_id=annex_id,
                    bookmark_state=bookmark_state,
                    render_caption_english=render_caption_english,
                )
            )
        elif block_type == "note":
            blocks.extend(build_note_blocks(item.get("items", []), styles, bookmark_state=bookmark_state))
        elif block_type == "example":
            blocks.extend(build_example_blocks(item, styles, bookmark_state=bookmark_state))
    return blocks


def build_term_blocks(items, term_style_ids: dict[int, str], body_style: str, bookmark_state: dict | None = None) -> list[ET.Element]:
    blocks = []
    for idx, item in enumerate(normalize_term_items(items), start=1):
        level = min(max(item["level"], 1), 5)
        blocks.append(
            make_paragraph(
                item["term"],
                term_style_ids[level],
                bookmark_name=resolve_bookmark_name(item.get("bookmark", ""), f"term_{idx}"),
                bookmark_state=bookmark_state,
            )
        )
        for paragraph in item["definition"]:
            blocks.append(make_paragraph(paragraph, body_style, bookmark_state=bookmark_state))
    return blocks


def build_clause_blocks(
    clauses,
    styles: dict[str, str],
    bookmark_state: dict | None,
    prefix: list[int],
    clause_kind: str,
    annex_id: str = "",
    context: dict | None = None,
    start_index: int = 1,
) -> list[ET.Element]:
    blocks: list[ET.Element] = []
    local_context = context if context is not None else {"figure": 0, "table": 0}
    for idx, clause in enumerate(clauses or [], start=start_index):
        path = prefix + [idx]
        level = min(max(len(path), 1), 5)
        if clause_kind == "annex":
            style = styles[f"annex_clause_level_{level}"]
            generated = f"annex_{format_number_token(annex_id)}_{'_'.join(str(x) for x in path)}"
        else:
            style = styles[f"clause_level_{level}"]
            generated = f"clause_{'_'.join(str(x) for x in path)}"
        blocks.append(
            make_paragraph(
                scalar_to_text(clause.get("title", "")),
                style,
                bookmark_name=resolve_bookmark_name(clause.get("bookmark", ""), generated),
                bookmark_state=bookmark_state,
            )
        )
        blocks.extend(
            build_content_blocks(
                clause.get("paragraphs", []),
                styles,
                local_context,
                annex_id=annex_id,
                bookmark_state=bookmark_state,
                render_caption_english=bool(styles.get("__render_caption_english")),
            )
        )
        blocks.extend(
            build_clause_blocks(
                clause.get("children", []),
                styles,
                bookmark_state,
                path,
                clause_kind,
                annex_id=annex_id,
                context=local_context,
            )
        )
    return blocks


def build_annex_blocks(
    annexes,
    styles: dict[str, str],
    bookmark_state: dict | None = None,
    render_annex_marker_english: bool = False,
) -> list[ET.Element]:
    blocks: list[ET.Element] = []
    for annex in annexes or []:
        kind = annex.get("kind", "informative")
        zh_kind = "（规范性）" if kind == "normative" else "（资料性）"
        en_kind = "(normative)" if kind == "normative" else "(informative)"
        annex_token = format_number_token(annex.get("id", "A"))
        annex_bookmark = resolve_bookmark_name(annex.get("bookmark", ""), f"annex_{annex_token}")
        blocks.append(make_paragraph("", "", page_break_before=True, bookmark_state=bookmark_state))
        blocks.append(make_paragraph("\n" + zh_kind, styles["annex_marker"], bookmark_state=bookmark_state))
        if render_annex_marker_english:
            blocks.append(make_paragraph(en_kind, styles["annex_marker_en"], bookmark_state=bookmark_state))
        title_paragraph = make_paragraph(
            scalar_to_text(annex.get("title", "")),
            styles["annex_title"],
            bookmark_name=annex_bookmark,
            bookmark_state=bookmark_state,
        )
        apply_paragraph_format(title_paragraph, {"paragraph_align": "center", "first_line": 0})
        blocks.append(title_paragraph)
        annex_context = {"figure": 0, "table": 0}
        annex_id_text = scalar_to_text(annex.get("id", "")).strip()
        blocks.extend(
            build_content_blocks(
                annex.get("paragraphs", []),
                styles,
                annex_context,
                annex_id=annex_id_text,
                bookmark_state=bookmark_state,
                render_caption_english=bool(styles.get("__render_caption_english")),
            )
        )
        blocks.extend(build_clause_blocks(annex.get("clauses", []), styles, bookmark_state, [], "annex", annex_id=annex_id_text))
    return blocks


def to_display_date(value: str, suffix: str) -> str:
    return f"{scalar_to_text(value)}{suffix}"


def ensure_media_relationship(work: Path, image_path: Path, media_name: str) -> str:
    media_dir = work / "word" / "media"
    media_dir.mkdir(parents=True, exist_ok=True)
    target_media = media_dir / media_name
    target_media.write_bytes(image_path.read_bytes())

    rels_path = work / "word" / "_rels" / "document.xml.rels"
    root = ET.parse(rels_path).getroot()
    ids = []
    for rel in root.findall(f"{{{REL_NS}}}Relationship"):
        rid = rel.get("Id", "")
        if rid.startswith("rId"):
            try:
                ids.append(int(rid[3:]))
            except ValueError:
                continue
    rel = ET.SubElement(root, f"{{{REL_NS}}}Relationship")
    rel.set("Id", f"rId{max(ids or [0]) + 1}")
    rel.set("Type", f"{DOC_REL_NS}/image")
    rel.set("Target", f"media/{media_name}")
    ET.ElementTree(root).write(rels_path, encoding="utf-8", xml_declaration=True)
    return rel.get("Id", "")


def pack_dir(source: Path, output: Path) -> None:
    with zipfile.ZipFile(output, "w", zipfile.ZIP_DEFLATED) as zf:
        for file in sorted(source.rglob("*")):
            if file.is_file():
                zf.write(file, file.relative_to(source))


def render(template: Path, profile: dict, data: dict, output: Path) -> None:
    cover = data.get("cover", {})
    sections = data.get("sections", {})
    computed = build_computed(cover, profile.get("profile_id", "national"))
    style_ids = style_name_to_id(template)
    style_names = style_id_to_name(template)
    base_bookmarks = template_bookmark_names(template)
    block_styles = {
        "__render_caption_english": bool(profile.get("render_caption_english", False)),
        "__style_name_map": style_ids,
        "foreword_heading": resolve_profile_style(style_ids, profile, "foreword_heading", "标准文件_前言、引言标题"),
        "introduction_heading": resolve_profile_style(style_ids, profile, "introduction_heading", "标准文件_引言标题"),
        "paragraph": style_ids["标准文件_段"],
        "body": style_ids["标准文件_段"],
        "clause_level_1": style_ids["标准文件_章标题"],
        "clause_level_2": style_ids["标准文件_一级条标题"],
        "clause_level_3": style_ids["标准文件_二级条标题"],
        "clause_level_4": style_ids["标准文件_三级条标题"],
        "clause_level_5": style_ids["标准文件_四级条标题"],
        "toc_heading": resolve_profile_style(style_ids, profile, "toc_heading", "标准文件_目次、标准名称标题"),
        "toc_body": style_ids["目次、索引正文"],
        "annex_marker": style_ids["标准文件_附录标识"],
        "annex_marker_en": style_ids["标准文件_附录英文标识"],
        "annex_title": style_ids["标准文件_附录章标题"],
        "annex_clause_level_1": style_ids["标准文件_附录一级条标题"],
        "annex_clause_level_2": style_ids["标准文件_附录二级条标题"],
        "annex_clause_level_3": style_ids["标准文件_附录三级条标题"],
        "annex_clause_level_4": style_ids["标准文件_附录四级条标题"],
        "annex_clause_level_5": style_ids["标准文件_附录五级条标题"],
        "figure_title": style_ids["标准文件_正文图标题"],
        "figure_title_en": style_ids["标准文件_正文英文图标题"],
        "table_title": style_ids["标准文件_正文表标题"],
        "table_title_en": style_ids["标准文件_正文英文表标题"],
        "table_cell": style_ids["标准文件_表格"],
        "item_level_1": style_ids["标准文件_一级项"],
        "item_alpha_level_1": style_ids["标准文件_字母编号列项（一级）"],
        "item_number_level_1": style_ids["标准文件_数字编号列项"],
        "item_number_level_2": style_ids["标准文件_数字编号列项（二级）"],
        "item_number_level_3": style_ids["标准文件_编号列项（三级）"],
        "item_dash_level_1": style_ids["标准文件_破折号列项"],
        "item_dash_level_2": style_ids["标准文件_破折号列项（二级）"],
        "formula": style_ids["标准文件_正文公式"],
        "annex_formula": style_ids["标准文件_附录公式"],
        "annex_figure_title": style_ids["标准文件_附录图标题"],
        "annex_table_title": style_ids["标准文件_附录表标题"],
        "note_single": style_ids["标准文件_注："],
        "note_multi": style_ids["标准文件_注×："],
        "example_single": style_ids["标准文件_示例："],
        "example_multi": style_ids["标准文件_示例×："],
        "example_body": style_ids["标准文件_示例内容"],
        "bibliography_heading": resolve_profile_style(style_ids, profile, "bibliography_heading", "标准文件_参考文献标题"),
        "bibliography_item": style_ids["标准文件_参考文献条目"],
    }
    term_style_ids = {
        1: style_ids["标准文件_术语条一"],
        2: style_ids["标准文件_术语条二"],
        3: style_ids["标准文件_术语条三"],
        4: style_ids["标准文件_术语条四"],
        5: style_ids["标准文件_术语条五"],
    }
    with tempfile.TemporaryDirectory() as tmpdir:
        work = Path(tmpdir)
        with zipfile.ZipFile(template) as zf:
            zf.extractall(work)

        normalize_content_types(work / "[Content_Types].xml")
        ending_line_rel_id = ""
        ending_line_asset = scalar_to_text(profile.get("ending_line_image", "")).strip()
        if ending_line_asset:
            asset_path = Path(ending_line_asset)
            if not asset_path.is_absolute():
                asset_path = Path.cwd() / asset_path
            if asset_path.exists():
                ending_line_rel_id = ensure_media_relationship(work, asset_path, asset_path.name)

        files = iter_xml_files(work / "word")
        replacements = {
            "点击此处添加ICS号": scalar_to_text(cover.get("ics", "")),
            "点击此处添加CCS号": scalar_to_text(cover.get("ccs", "")),
            "点击此处添加标准名称": scalar_to_text(cover.get("title_zh", "")),
            "点击此处添加标准名称的英文译名": scalar_to_text(cover.get("title_en", "")),
            "(点击此处添加与国际标准一致性程度的标识)": scalar_to_text(cover.get("alignment_degree", "")),
        }
        for old, ref in profile.get("exact_text_map", {}).items():
            replacements[old] = resolve_ref(ref, data, computed)

        for path in files:
            root = ET.parse(path).getroot()
            replace_text_everywhere(root, replacements)
            replace_paragraphs_by_exact_text(root, replacements)
            field_values = {
                name: resolve_ref(ref, data, computed)
                for name, ref in profile.get("field_map", {}).items()
            }
            set_form_field_values_with_format(root, field_values, profile.get("field_format", {}))
            mark_fields_dirty(root)
            tree = ET.ElementTree(root)
            tree.write(path, encoding="utf-8", xml_declaration=True)

        document_path = work / "word" / "document.xml"
        document_root = ET.parse(document_path).getroot()
        bookmark_state = {"next_id": max_bookmark_id(document_root)}
        set_sdt_text_by_tag(document_root, "NEW_STAND_NAME", scalar_to_text(cover.get("title_zh", "")))
        set_first_text_in_sdt(
            find_sdt_by_control_type(document_root, "dropdown"),
            scalar_to_text(sections.get("normative_references", {}).get("intro", "")),
        )
        set_first_text_in_sdt(
            find_sdt_by_control_type(document_root, "combobox"),
            scalar_to_text(sections.get("terms_definitions", {}).get("intro", "")),
        )

        body = document_root.find("w:body", NS)
        if body is not None:
            body_context = {"figure": 0, "table": 0}
            toc_blocks = build_toc_blocks(data.get("table_of_contents", {}), block_styles, bookmark_state=bookmark_state)
            if not insert_blocks_before_sdt_tag(body, "NEW_STAND_NAME", toc_blocks):
                insert_blocks_before_first_heading(body, ["前言", "引言", "范围"], toc_blocks)
            intro_blocks = []
            intro_blocks.extend(
                build_major_section_blocks(
                    "前言",
                    [scalar_to_text(x) for x in data.get("foreword", [])],
                    block_styles["foreword_heading"],
                    block_styles["paragraph"],
                    bookmark_state=bookmark_state,
                )
            )
            intro_blocks.extend(
                build_major_section_blocks(
                    "引言",
                    [scalar_to_text(x) for x in data.get("introduction", [])],
                    block_styles["introduction_heading"],
                    block_styles["paragraph"],
                    bookmark_state=bookmark_state,
                )
            )
            if data.get("introduction", []):
                intro_blocks.append(make_paragraph("", "", page_break_before=True, bookmark_state=bookmark_state))
            if not insert_blocks_before_sdt_tag(body, "NEW_STAND_NAME", intro_blocks):
                insert_blocks_before_heading(body, "范围", intro_blocks)
            insert_section_blocks(
                body,
                "范围",
                ["规范性引用文件", "术语和定义"],
                build_content_blocks(
                    sections.get("scope", []),
                    block_styles,
                    body_context,
                    bookmark_state=bookmark_state,
                    render_caption_english=bool(profile.get("render_caption_english", False)),
                ),
            )
            normative_blocks = [
                make_paragraph(scalar_to_text(x), block_styles["body"], bookmark_state=bookmark_state)
                for x in sections.get("normative_references", {}).get("items", [])
                if scalar_to_text(x).strip()
            ]
            insert_section_blocks(
                body,
                "规范性引用文件",
                ["术语和定义"],
                normative_blocks,
            )
            term_blocks = build_term_blocks(
                sections.get("terms_definitions", {}).get("items", []),
                term_style_ids,
                block_styles["paragraph"],
                bookmark_state=bookmark_state,
            )
            insert_section_blocks(body, "术语和定义", [], term_blocks)
            main_clause_blocks = build_clause_blocks(
                data.get("clauses", []),
                block_styles,
                bookmark_state,
                [],
                "main",
                context=body_context,
                start_index=4,
            )
            annex_blocks = build_annex_blocks(
                data.get("annexes", []),
                block_styles,
                bookmark_state=bookmark_state,
                render_annex_marker_english=bool(profile.get("render_annex_marker_english", False)),
            )
            bibliography_blocks = build_bibliography_blocks(
                data.get("bibliography", []),
                block_styles["bibliography_heading"],
                block_styles["bibliography_item"],
                bookmark_state=bookmark_state,
            )
            closing_blocks = []
            if ending_line_rel_id:
                closing_blocks.append(
                    make_end_line_paragraph(
                        ending_line_rel_id,
                        style_id=block_styles["body"],
                        bookmark_state=bookmark_state,
                    )
                )
            insert_blocks_before_sectpr(body, main_clause_blocks + annex_blocks + bibliography_blocks + closing_blocks)
            apply_special_heading_spacing(body)
        mark_fields_dirty(document_root)
        bookmarks = collect_bookmark_manifest(document_root, style_names, excluded_names=base_bookmarks)
        fields = collect_field_manifest(document_root)
        missing_targets = validate_field_targets(bookmarks, fields)
        if missing_targets:
            raise ValueError(f"未找到交叉引用目标: {', '.join(missing_targets)}")
        ET.ElementTree(document_root).write(document_path, encoding="utf-8", xml_declaration=True)
        ensure_update_fields(work / "word" / "settings.xml")

        output.parent.mkdir(parents=True, exist_ok=True)
        pack_dir(work, output)
        write_reference_manifest(output, bookmarks, fields)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", required=True)
    parser.add_argument("--template", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--profile")
    args = parser.parse_args()

    data = load_input(Path(args.input))
    profile_path = Path(args.profile) if args.profile else infer_profile_path(Path(args.template))
    profile = load_yaml(profile_path)
    render(Path(args.template), profile, data, Path(args.output))


if __name__ == "__main__":
    main()
