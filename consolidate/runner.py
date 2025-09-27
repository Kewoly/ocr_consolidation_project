# consolidate/runner.py
import argparse
from pathlib import Path
from dotenv import load_dotenv
import os
import json

load_dotenv()

from consolidate.ocr.mistral_worker import process_one_pdf_with_mistral, MISTRAL_API_KEY, MISTRAL_SDK_AVAILABLE
from consolidate.ocr.local_ocr import pdf_to_md_local
from consolidate.arretify_integration import md_to_arretify_html, ARRETIFY_AVAILABLE
from consolidate.parser.parse_html import parse_arretify_html
from consolidate.parser.detect_ops import detect_ops_in_text
from consolidate.parser.consolidate_engine import apply_operations
from bs4 import BeautifulSoup

def process_file(path: str, out_dir: str, prefer_mistral: bool = True):
    p = Path(path)
    work_dir = Path(out_dir) / "work"
    work_dir.mkdir(parents=True, exist_ok=True)

    # 1) OCR -> md (Mistral preferred)
    if p.suffix.lower() == ".pdf":
        if prefer_mistral and MISTRAL_API_KEY and MISTRAL_SDK_AVAILABLE:
            produced = process_one_pdf_with_mistral(str(p), str(work_dir))
            # produced is .md path or .html (Arrêtify) path
            if produced.suffix == ".md":
                md_path = produced
                if ARRETIFY_AVAILABLE:
                    ar_html = md_to_arretify_html(str(md_path), str(work_dir))
                    ar_path = ar_html
                else:
                    ar_path = None
            elif produced.suffix in (".html", ".htm"):
                ar_path = produced
                md_path = None
            else:
                ar_path = None; md_path = produced
        else:
            md_path = pdf_to_md_local(str(p), str(work_dir))
            ar_path = md_to_arretify_html(str(md_path), str(work_dir)) if ARRETIFY_AVAILABLE else None
    elif p.suffix.lower() in (".html", ".htm"):
        ar_path = p
        md_path = None
    else:
        raise RuntimeError("Unsupported file type: " + str(p.suffix))

    # 2) parse Arrêtify HTML
    if not ar_path:
        raise RuntimeError("Aucun HTML Arrêtify produit — vérifier OCR/Arrêtify")
    node, fragments, soup = parse_arretify_html(ar_path)

    # 3) detect operations
    text = soup.get_text("\n", strip=True)
    ops = detect_ops_in_text(text, node["node_id"], node.get("date"))

    # 4) apply operations
    fragments_map, change_log = apply_operations(ops, fragments)

    # 5) generate consolidated HTML (replace fragment markup)
    for fid, frag in fragments_map.items():
        el = soup.select_one(f'[data-id="{fid}"]')
        if el:
            newfrag = BeautifulSoup(frag["html"], "html.parser")
            el.replace_with(newfrag)

    out_consolidated = Path(out_dir) / f"{node['node_id']}_consolidated.html"
    out_consolidated.write_text(str(soup), encoding="utf-8")
    (Path(out_dir) / "change_log.json").write_text(json.dumps(change_log, indent=2, ensure_ascii=False), encoding="utf-8")
    print("Consolidated saved to:", out_consolidated)
    return out_consolidated

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--file", "-f", help="Fichier pdf/html à traiter")
    parser.add_argument("--input", "-i", help="Dossier d'entrée (traiter tous les pdf/html)")
    parser.add_argument("--output", "-o", required=True, help="Dossier de sortie")
    parser.add_argument("--prefer-mistral", action="store_true", help="Favoriser Mistral OCR quand disponible")
    args = parser.parse_args()
    if args.file:
        process_file(args.file, args.output, args.prefer_mistral)
    elif args.input:
        p = Path(args.input)
        for f in sorted(p.iterdir()):
            if f.suffix.lower() in (".pdf", ".html", ".htm"):
                process_file(str(f), args.output, args.prefer_mistral)
    else:
        parser.error("Préciser --file ou --input")

if __name__ == "__main__":
    main()
