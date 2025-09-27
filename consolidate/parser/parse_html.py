# consolidate/parser/parse_html.py
from bs4 import BeautifulSoup
from pathlib import Path
import re
from typing import Tuple, List, Dict, Any

def _clean_text(el):
    return el.get_text("\n", strip=True)

def parse_arretify_html(path: str) -> Tuple[Dict[str,Any], List[Dict], BeautifulSoup]:
    """
    Parse an Arrêtify HTML and return (node, fragments, soup).
    Each fragment corresponds to a meaningful unit: article / alinea / section.
    """
    path = Path(path)
    text = path.read_text(encoding="utf-8")
    soup = BeautifulSoup(text, "html.parser")

    # header metadata
    header = soup.select_one(".arretify-header")
    date_text = None
    if header:
        date_el = header.select_one(".arretify-date") or header.find(class_="date")
        if date_el:
            date_text = date_el.get_text(strip=True)
        else:
            m = re.search(r"\b(19|20)\d{2}\b", header.get_text())
            date_text = m.group(0) if m else None

    # node id: clean stem of file (without _arretify suffix)
    stem = path.stem
    stem = re.sub(r'(_arretify|_consolidated|_arretify_consolidated)$', '', stem)
    node_id = stem

    node = {"node_id": node_id, "path": str(path), "date": date_text}

    fragments: List[Dict] = []
    frag_counter = 0

    # Priority 1: explicit .arretify-article elements
    articles = soup.select(".arretify-article")
    if articles:
        for art in articles:
            frag_id = art.get("data-id") or f"{node_id}_frag_{frag_counter}"
            frag_counter += 1
            title_el = art.select_one(".arretify-title") or art.select_one("h2") or art.select_one("h3")
            title = title_el.get_text(strip=True) if title_el else None
            # If article contains alinea elements, split them further
            alineas = art.select(".arretify-alinea")
            if alineas:
                for a in alineas:
                    sub_id = a.get("data-id") or f"{frag_id}_a{frag_counter}"
                    frag = {
                        "fragment_id": sub_id,
                        "parent_node": node_id,
                        "parent_article": frag_id,
                        "title": title,
                        "text": _clean_text(a),
                        "html": str(a),
                        "data_number": a.get("data-number") or art.get("data-number"),
                    }
                    fragments.append(frag)
                    frag_counter += 1
            else:
                frag = {
                    "fragment_id": frag_id,
                    "parent_node": node_id,
                    "title": title,
                    "text": _clean_text(art),
                    "html": str(art),
                    "data_number": art.get("data-number"),
                }
                fragments.append(frag)
    else:
        # Priority 2: fallback to sections (Arrêtify sometimes uses sections)
        sections = soup.select(".arretify-section")
        for sec in sections:
            sec_num = sec.get("data-number") or sec.get("data-number") or None
            title_el = sec.select_one(".arretify-section_title") or sec.select_one("h2") or sec.select_one("h3")
            title = title_el.get_text(strip=True) if title_el else None
            # try to split into alinea within the section
            alineas = sec.select(".arretify-alinea")
            if alineas:
                for a in alineas:
                    frag_id = a.get("data-id") or f"{node_id}_frag_{frag_counter}"
                    frag = {
                        "fragment_id": frag_id,
                        "parent_node": node_id,
                        "parent_section": sec_num,
                        "title": title,
                        "text": _clean_text(a),
                        "html": str(a),
                        "data_number": a.get("data-number") or sec_num,
                    }
                    fragments.append(frag)
                    frag_counter += 1
            else:
                # entire section as one fragment
                frag_id = sec.get("data-id") or f"{node_id}_frag_{frag_counter}"
                frag = {
                    "fragment_id": frag_id,
                    "parent_node": node_id,
                    "title": title,
                    "text": _clean_text(sec),
                    "html": str(sec),
                    "data_number": sec_num,
                }
                fragments.append(frag)
                frag_counter += 1

    # If still no fragments, fallback: create a fragment per reasonable block (divs in main)
    if not fragments:
        main = soup.select_one(".arretify-main") or soup.body or soup
        blocks = main.find_all(recursive=False)
        for b in blocks:
            frag_id = b.get("data-id") or f"{node_id}_frag_{frag_counter}"
            frag = {
                "fragment_id": frag_id,
                "parent_node": node_id,
                "title": None,
                "text": _clean_text(b),
                "html": str(b),
                "data_number": b.get("data-number"),
            }
            fragments.append(frag)
            frag_counter += 1

    return node, fragments, soup
