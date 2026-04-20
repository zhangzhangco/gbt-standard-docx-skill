#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import zipfile
from pathlib import Path
from xml.etree import ElementTree as ET

NS = {
    "w": "http://schemas.openxmlformats.org/wordprocessingml/2006/main",
    "cp": "http://schemas.openxmlformats.org/package/2006/metadata/core-properties",
    "dc": "http://purl.org/dc/elements/1.1/",
    "vt": "http://schemas.openxmlformats.org/officeDocument/2006/docPropsVTypes",
}


def qn(prefix: str, tag: str) -> str:
    return f"{{{NS[prefix]}}}{tag}"


def text_of(element: ET.Element) -> str:
    return "".join(element.itertext()).strip()


def load_xml(zf: zipfile.ZipFile, name: str) -> ET.Element:
    return ET.fromstring(zf.read(name))


def extract_styles(root: ET.Element) -> list[dict]:
    styles = []
    for style in root.findall("w:style", NS):
        entry = {
            "style_id": style.get(qn("w", "styleId"), ""),
            "type": style.get(qn("w", "type"), ""),
            "custom": style.get(qn("w", "customStyle")) == "1",
            "name": "",
            "based_on": "",
            "next": "",
        }
        name = style.find("w:name", NS)
        based_on = style.find("w:basedOn", NS)
        next_style = style.find("w:next", NS)
        if name is not None:
            entry["name"] = name.get(qn("w", "val"), "")
        if based_on is not None:
            entry["based_on"] = based_on.get(qn("w", "val"), "")
        if next_style is not None:
            entry["next"] = next_style.get(qn("w", "val"), "")
        styles.append(entry)
    return styles


def extract_headers_footers(zf: zipfile.ZipFile) -> dict[str, list[dict]]:
    result: dict[str, list[dict]] = {"headers": [], "footers": []}
    for kind in ("header", "footer"):
        for name in sorted(n for n in zf.namelist() if n.startswith(f"word/{kind}") and n.endswith(".xml")):
            root = load_xml(zf, name)
            paragraphs = []
            for paragraph in root.findall("w:p", NS):
                paragraphs.append(text_of(paragraph))
            result[f"{kind}s"].append({"part": name, "texts": [p for p in paragraphs if p]})
    return result


def extract_content_controls(root: ET.Element) -> list[dict]:
    items = []
    for sdt in root.findall(".//w:sdt", NS):
        pr = sdt.find("w:sdtPr", NS)
        tag = ""
        control_type = "plain"
        if pr is not None:
            tag_el = pr.find("w:tag", NS)
            if tag_el is not None:
                tag = tag_el.get(qn("w", "val"), "")
            if pr.find("w:dropDownList", NS) is not None:
                control_type = "dropdown"
            elif pr.find("w:comboBox", NS) is not None:
                control_type = "combobox"
        items.append({
            "tag": tag,
            "type": control_type,
            "text": text_of(sdt),
        })
    return items


def extract_form_fields(root: ET.Element) -> list[dict]:
    fields = []
    for ff in root.findall(".//w:ffData", NS):
        name = ff.find("w:name", NS)
        default = ff.find(".//w:default", NS)
        fields.append({
            "name": "" if name is None else name.get(qn("w", "val"), ""),
            "default": "" if default is None else default.get(qn("w", "val"), ""),
        })
    return fields


def extract_custom_props(root: ET.Element) -> dict[str, str]:
    props = {}
    for prop in root:
        name = prop.get("name", "")
        value = ""
        for child in prop:
            value = text_of(child)
            break
        props[name] = value
    return props


def extract_numbering(root: ET.Element) -> dict:
    abstracts = []
    nums = []
    for abstract in root.findall("w:abstractNum", NS):
        levels = []
        for level in abstract.findall("w:lvl", NS):
            text = level.find("w:lvlText", NS)
            fmt = level.find("w:numFmt", NS)
            levels.append({
                "ilvl": level.get(qn("w", "ilvl"), ""),
                "text": "" if text is None else text.get(qn("w", "val"), ""),
                "format": "" if fmt is None else fmt.get(qn("w", "val"), ""),
            })
        abstracts.append({
            "abstract_num_id": abstract.get(qn("w", "abstractNumId"), ""),
            "levels": levels,
        })
    for num in root.findall("w:num", NS):
        abstract_num_id = num.find("w:abstractNumId", NS)
        nums.append({
            "num_id": num.get(qn("w", "numId"), ""),
            "abstract_num_id": "" if abstract_num_id is None else abstract_num_id.get(qn("w", "val"), ""),
        })
    return {"abstracts": abstracts, "instances": nums}


def extract_document_outline(root: ET.Element) -> list[dict]:
    body = root.find("w:body", NS)
    if body is None:
        return []
    outline = []
    for child in body:
        kind = child.tag.rsplit("}", 1)[-1]
        if kind == "p":
            ppr = child.find("w:pPr", NS)
            style = ""
            if ppr is not None:
                pstyle = ppr.find("w:pStyle", NS)
                if pstyle is not None:
                    style = pstyle.get(qn("w", "val"), "")
            text = text_of(child)
            outline.append({"kind": "paragraph", "style": style, "text": text})
        elif kind == "tbl":
            outline.append({"kind": "table", "text": text_of(child)})
        elif kind == "sectPr":
            outline.append({"kind": "section"})
    return outline


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--template", required=True)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()

    template = Path(args.template)
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)

    with zipfile.ZipFile(template) as zf:
        core = load_xml(zf, "docProps/core.xml")
        custom = load_xml(zf, "docProps/custom.xml")
        styles = load_xml(zf, "word/styles.xml")
        document = load_xml(zf, "word/document.xml")
        numbering = load_xml(zf, "word/numbering.xml")
        manifest = {
            "template": str(template),
            "core": {
                "title": core.findtext("dc:title", "", NS),
                "creator": core.findtext("dc:creator", "", NS),
                "last_modified_by": core.findtext("cp:lastModifiedBy", "", NS),
            },
            "custom_properties": extract_custom_props(custom),
            "styles": extract_styles(styles),
            "content_controls": extract_content_controls(document),
            "form_fields": extract_form_fields(document),
            "headers_footers": extract_headers_footers(zf),
            "numbering": extract_numbering(numbering),
            "outline": extract_document_outline(document),
        }

    output.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")


if __name__ == "__main__":
    main()
