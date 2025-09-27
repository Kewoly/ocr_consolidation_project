# consolidate/ocr/local_ocr.py
from pathlib import Path
from pdf2image import convert_from_path
import pytesseract

def pdf_to_md_local(pdf_path: str, out_dir: str, dpi: int = 300, lang: str = "fra"):
    pdf_path = Path(pdf_path)
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    pages = convert_from_path(str(pdf_path), dpi=dpi)
    md_lines = []
    for i, img in enumerate(pages):
        text = pytesseract.image_to_string(img, lang=lang)
        md_lines.append(f"<!-- PAGE {i+1} -->\n{text}\n")
    md_path = out_dir / f"{pdf_path.stem}.md"
    md_path.write_text("\n".join(md_lines), encoding="utf-8")
    return md_path
