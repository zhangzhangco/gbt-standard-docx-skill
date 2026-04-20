#!/usr/bin/env python3
from __future__ import annotations

import argparse
import shutil
import subprocess
import tempfile
import zipfile
from pathlib import Path
from xml.etree import ElementTree as ET

NS = {
    "w": "http://schemas.openxmlformats.org/wordprocessingml/2006/main",
}


def qn(tag: str) -> str:
    return f"{{{NS['w']}}}{tag}"


def iter_xml_files(root: Path) -> list[Path]:
    return sorted(root.rglob("*.xml"))


def pack_dir(source: Path, output: Path) -> None:
    with zipfile.ZipFile(output, "w", zipfile.ZIP_DEFLATED) as zf:
        for file in sorted(source.rglob("*")):
            if file.is_file():
                zf.write(file, file.relative_to(source))


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


def touch_docx_fields(docx: Path, output: Path) -> None:
    with tempfile.TemporaryDirectory() as tmpdir:
        work = Path(tmpdir)
        with zipfile.ZipFile(docx) as zf:
            zf.extractall(work)
        for path in iter_xml_files(work / "word"):
            root = ET.parse(path).getroot()
            mark_fields_dirty(root)
            ET.ElementTree(root).write(path, encoding="utf-8", xml_declaration=True)
        settings = work / "word" / "settings.xml"
        if settings.exists():
            ensure_update_fields(settings)
        output.parent.mkdir(parents=True, exist_ok=True)
        pack_dir(work, output)


def soffice_roundtrip(docx: Path, output: Path) -> None:
    with tempfile.TemporaryDirectory() as tmpdir:
        outdir = Path(tmpdir)
        subprocess.run(
            [
                "soffice",
                "--headless",
                "--convert-to",
                "docx",
                "--outdir",
                str(outdir),
                str(docx),
            ],
            check=True,
        )
        converted = outdir / docx.name
        if not converted.exists():
            raise FileNotFoundError(str(converted))
        output.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(converted, output)


def replace_file(source: Path, target: Path) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    source.replace(target)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", required=True, help="待刷新字段的 docx 文件")
    parser.add_argument("--output", help="输出 docx 文件，默认覆盖输入文件")
    parser.add_argument(
        "--soffice-roundtrip",
        action="store_true",
        help="在 OOXML 标记后再用 LibreOffice 回写一次 docx",
    )
    args = parser.parse_args()

    source = Path(args.input).resolve()
    target = Path(args.output).resolve() if args.output else source

    staged = target
    if source == target:
        staged = source.with_suffix(".refreshing.docx")

    touch_docx_fields(source, staged)
    if args.soffice_roundtrip:
        roundtrip_target = target
        if staged == target:
            roundtrip_target = target.with_suffix(".roundtrip.docx")
        soffice_roundtrip(staged, roundtrip_target)
        retouched = roundtrip_target.with_suffix(".retouched.docx")
        touch_docx_fields(roundtrip_target, retouched)
        replace_file(retouched, roundtrip_target)
        if roundtrip_target != target:
            replace_file(roundtrip_target, target)
        if staged.exists() and staged != target:
            staged.unlink()
    elif staged != target:
        replace_file(staged, target)

    print(f"output={target}")
    print(f"soffice_roundtrip={'yes' if args.soffice_roundtrip else 'no'}")


if __name__ == "__main__":
    main()
