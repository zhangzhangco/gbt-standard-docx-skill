#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import subprocess
from pathlib import Path


def run(cmd: list[str]) -> None:
    subprocess.run(cmd, check=True)


def ensure_exists(path: Path) -> None:
    if not path.exists():
        raise FileNotFoundError(str(path))


def render_pdf(docx: Path, outdir: Path) -> Path:
    run([
        "soffice",
        "--headless",
        "--convert-to",
        "pdf",
        "--outdir",
        str(outdir),
        str(docx),
    ])
    pdf = outdir / f"{docx.stem}.pdf"
    ensure_exists(pdf)
    return pdf


def render_pages(pdf: Path, outdir: Path, dpi: int, image_format: str) -> list[Path]:
    prefix = outdir / pdf.stem
    args = [
        "pdftoppm",
        f"-{image_format}",
        "-r",
        str(dpi),
        str(pdf),
        str(prefix),
    ]
    run(args)
    suffix = ".jpg" if image_format == "jpeg" else ".png"
    pages = sorted(outdir.glob(f"{pdf.stem}-*{suffix}"))
    if not pages:
        raise FileNotFoundError(f"no page images for {pdf}")
    return pages


def write_manifest(docx: Path, pdf: Path, pages: list[Path], outdir: Path, dpi: int, image_format: str) -> Path:
    manifest = {
        "docx": str(docx),
        "pdf": str(pdf),
        "pages": [str(p) for p in pages],
        "dpi": dpi,
        "image_format": image_format,
        "page_count": len(pages),
    }
    target = outdir / f"{docx.stem}.verify.json"
    target.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    return target


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", required=True, help="待验证的 docx 文件")
    parser.add_argument("--output-dir", required=True, help="PDF 和页面图片输出目录")
    parser.add_argument("--dpi", type=int, default=150)
    parser.add_argument("--image-format", choices=["png", "jpeg"], default="png")
    args = parser.parse_args()

    docx = Path(args.input).resolve()
    outdir = Path(args.output_dir).resolve()
    outdir.mkdir(parents=True, exist_ok=True)
    ensure_exists(docx)

    pdf = render_pdf(docx, outdir)
    pages = render_pages(pdf, outdir, args.dpi, args.image_format)
    manifest = write_manifest(docx, pdf, pages, outdir, args.dpi, args.image_format)

    print(f"pdf={pdf}")
    print(f"pages={len(pages)}")
    print(f"manifest={manifest}")


if __name__ == "__main__":
    main()
