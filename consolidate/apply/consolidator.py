# consolidate/apply/consolidator.py
import argparse
import json
import logging
from pathlib import Path
from datetime import datetime
import difflib
from typing import Dict, List, Optional
import re


from bs4 import BeautifulSoup

# Try to reuse project utilities if present
try:
    from consolidate.parser.parse_html import parse_arretify_html
except Exception:
    raise RuntimeError("Impossible d'importer parse_arretify_html depuis consolidate.parser.parse_html")

# try to import resolution helper if present
try:
    from consolidate.parser.consolidate_engine import resolve_target_fragment
except Exception:
    resolve_target_fragment = None  # we'll use fallback


# optional rapidfuzz for better fuzzy matching
try:
    from rapidfuzz import process, fuzz
    HAVE_RAPIDFUZZ = True
except Exception:
    HAVE_RAPIDFUZZ = False

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("consolidator")


def load_ops(path: Path) -> List[Dict]:
    data = json.loads(path.read_text(encoding="utf-8"))
    # ensure it's a list
    if isinstance(data, dict):
        # maybe wrapped; try keys
        data = data.get("ops", []) or data.get("operations", []) or []
    if not isinstance(data, list):
        raise RuntimeError(f"Format inattendu pour {path}")
    return data


def collect_fragments(html_dir: Path) -> Dict[str, Dict]:
    """
    Parcourt les html Arrêtify du dossier et construit un map fragment_id -> fragment dict
    fragment dict: {fragment_id, parent_node, title, text, html, source_html_path, node_date}
    """
    fragmap = {}
    html_files = sorted(Path(html_dir).glob("*.html"))
    for p in html_files:
        node, fragments, soup = parse_arretify_html(str(p))
        node_date = node.get("date")  # new
        for f in fragments:
            f["source_html_path"] = str(p)
            f["node_date"] = node_date  # new
            # ensure keys exist
            fragmap[f["fragment_id"]] = f
    return fragmap



def _parse_date(dstr: Optional[str]) -> float:
    if not dstr:
        return 0.0
    try:
        # best-effort parse
        from dateutil import parser as dateparser
        dt = dateparser.parse(dstr, dayfirst=True)
        return dt.timestamp()
    except Exception:
        # fallback: try YYYY-MM-DD
        try:
            dt = datetime.fromisoformat(dstr)
            return dt.timestamp()
        except Exception:
            return 0.0


def fallback_resolve(op: Dict, fragments: Dict[str, Dict]) -> Optional[str]:
    """
    Résolution conservative améliorée :
     - si detected_in_fragment_id présent et fragment contient un vrai data_number/title -> accept
     - si op.detected_span contient "Article N" -> retrouver fragment avec data_number==N ou titre
     - privilégier fragments ayant 'data_number' ou 'title'
     - else fuzzy/difflib as fallback
    """
    det = op.get("detected_in_fragment_id")
    # If det points to header-like fragment (very long & no data_number) then we prefer to search inside same source
    if det and det in fragments:
        fdet = fragments[det]
        # if det has a data_number or title, it's probably ok
        if fdet.get("data_number") or fdet.get("title"):
            return det
        # else continue searching (do not immediately return det)

    # 1) Check for explicit "Article N" in op target or detected_span
    span = (op.get("target_ref") or op.get("detected_span") or "")
    m = re.search(r"\bArticle[s]?\s*\.?\s*(?:n[°º]?\s*)?(\d+(-\d+)?)\b", span, re.IGNORECASE)
    if m:
        num = m.group(1)
        # try to match fragments with that data_number or title
        for fid, fr in fragments.items():
            fn = str(fr.get("data_number") or fr.get("data-number") or fr.get("number") or "")
            title = (fr.get("title") or "").lower()
            if fn and fn.strip() == num:
                return fid
            if f"article {num}" in title:
                return fid

    # 2) If detected_in_fragment_id exists and looks like an article/alinéa (has data_number/title) use it
    if det and det in fragments:
        fdet = fragments[det]
        if fdet.get("data_number") or fdet.get("title"):
            return det

    # 3) Prefer fragments from same source_html_path as det (if we have det)
    if det and det in fragments:
        src = fragments[det].get("source_html_path")
        # build candidate list: fragments from same source and having data_number/title
        candidates = {fid: fr for fid, fr in fragments.items() if fr.get("source_html_path") == src and (fr.get("data_number") or fr.get("title"))}
        if candidates:
            # if target_ref present, fuzzy-match on titles
            tref = op.get("target_ref")
            if tref and HAVE_RAPIDFUZZ:
                best = process.extractOne(str(tref), {k: v.get("title","") for k,v in candidates.items()}, scorer=fuzz.WRatio)
                if best and best[1] >= 75:
                    return best[2]
            # else try matching by similarity on detected_span
            if HAVE_RAPIDFUZZ:
                best = process.extractOne(op.get("detected_span","")[:300], {k: v.get("text","") for k,v in candidates.items()}, scorer=fuzz.token_set_ratio)
                if best and best[1] >= 65:
                    return best[2]
            else:
                # difflib fallback
                q = (op.get("detected_span") or "")[:200]
                bestfid, bestr = None, 0.0
                for fid, fr in candidates.items():
                    r = difflib.SequenceMatcher(a=q, b=(fr.get("text") or "")[:1000]).ratio()
                    if r > bestr:
                        bestr = r; bestfid = fid
                if bestr >= 0.55:
                    return bestfid

    # 4) global fuzzy fallback (same as before)
    texts = {fid: fr.get("text","") for fid,fr in fragments.items()}
    if HAVE_RAPIDFUZZ:
        best = process.extractOne(op.get("detected_span","")[:300], texts, scorer=fuzz.token_set_ratio)
        if best and best[1] >= 65:
            return best[2]
    else:
        q = op.get("detected_span","")[:200]
        bestfid, bestr = None, 0.0
        for fid,txt in texts.items():
            r = difflib.SequenceMatcher(a=q, b=(txt or "")[:1000]).ratio()
            if r > bestr:
                bestr = r; bestfid = fid
        if bestr >= 0.55:
            return bestfid

    return None


def re_search_article(num: str, title: str) -> bool:
    # simple check for "Article 3" variants inside title
    try:
        num = str(num)
        return f"article {num}" in title.lower()
    except Exception:
        return False


def targets_best_match(op: Dict, choices: Dict[str, str]) -> Optional[str]:
    """
    If rapidfuzz available, prefer that; otherwise none.
    """
    if not choices:
        return None
    target_ref = op.get("target_ref")
    if not target_ref:
        return None
    if HAVE_RAPIDFUZZ:
        best = process.extractOne(str(target_ref), choices, scorer=fuzz.WRatio)
        if best and best[1] >= 80:
            return best[2]
    return None


def apply_modification_to_fragment(fragment: Dict, detected_span: str, opid: str) -> Dict:
    """
    Try simple replacement or fuzzy alignment. Return updated fragment and applied flag.
    We'll operate on fragment['html'] (string) and fragment['text'] as convenience.
    """
    old_html = fragment.get("html", "")
    old_text = fragment.get("text", "")

    # prefer exact substring in plain text
    if detected_span and detected_span.strip() and detected_span.strip() in old_text:
        # replace first occurrence in html by wrapping the matched text (approximate)
        # do a best-effort: replace in plain text and then in html by sequence matching
        new_plain = old_text.replace(detected_span, f"<del data-op='{opid}'>{detected_span}</del><ins data-op='{opid}'>[MODIFIED]</ins>", 1)
        # replace the text node inside html by naive approach: replace text substring
        # best effort: replace plain substring in html (may fail if tags inside)
        if detected_span in old_html:
            new_html = old_html.replace(detected_span, f"<del data-op='{opid}'>{detected_span}</del><ins data-op='{opid}'>[MODIFIED]</ins>", 1)
        else:
            # fallback: wrap whole fragment
            new_html = f"<!-- op:{opid} -->{old_html}"
        fragment["html"] = new_html
        fragment["text"] = new_plain
        return {"fragment": fragment, "applied": True, "note": "exact_text_replace"}
    # else try difflib matching
    sm = difflib.SequenceMatcher(None, old_text, detected_span or "")
    # find largest matching block
    blocks = sm.get_matching_blocks()
    # choose block if size > threshold
    best_block = max(blocks, key=lambda b: b.size) if blocks else None
    if best_block and best_block.size and best_block.size >= 30:
        # compute indices
        a, size = best_block.a, best_block.size
        before = old_text[:a]
        matched = old_text[a:a+size]
        after = old_text[a+size:]
        replaced = before + f"<del data-op='{opid}'>{matched}</del><ins data-op='{opid}'>[MODIFIED]</ins>" + after
        # best-effort replace whole html content
        fragment["text"] = replaced
        # naive: replace old_text in html with replaced (if old_text present)
        if old_text and old_text in old_html:
            fragment["html"] = old_html.replace(old_text, replaced, 1)
        else:
            fragment["html"] = f"<!-- op:{opid} -->{old_html}"
        return {"fragment": fragment, "applied": True, "note": "aligned_replace"}
    return {"fragment": fragment, "applied": False, "note": "no_alignment"}


def apply_abrogation_to_fragment(fragment: Dict, opid: str) -> Dict:
    old_html = fragment.get("html", "")
    fragment["html"] = f"<del data-op='{opid}'>{old_html}</del>"
    fragment["text"] = ""
    return {"fragment": fragment, "applied": True, "note": "abrogation"}


def apply_ajout_to_fragment(fragment: Dict, detected_span: str, opid: str) -> Dict:
    # Insert the new text after the fragment
    old_html = fragment.get("html", "")
    fragment["html"] = old_html + f"<ins data-op='{opid}'>{detected_span}</ins>"
    fragment["text"] = (fragment.get("text","") + "\n" + detected_span).strip()
    return {"fragment": fragment, "applied": True, "note": "ajout_appended"}


def main(args):
    ops_path = Path(args.ops)
    html_dir = Path(args.html_dir)
    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    ops = load_ops(ops_path)
    logger.info(f"Loaded {len(ops)} operations from {ops_path}")

    # collect fragments from html_dir
    fragmap = collect_fragments(html_dir)
    logger.info(f"Collected {len(fragmap)} fragments from {html_dir}")

    # sort ops by effective date (old -> new). fallback to 0
    def keydate(o):
        return _parse_date(o.get("effective_date"))

    ops_sorted = sorted(ops, key=keydate)

    applied_log = []
    # apply each operation
    for i, op in enumerate(ops_sorted):
        opid = op.get("id") or f"op_{i}"
        optype = op.get("op_type")
        detected_span = op.get("detected_span")
        # First try to resolve target fragment via detected_in_fragment_id or resolve_target_fragment
        target = op.get("detected_in_fragment_id")
        if not target:
            if resolve_target_fragment:
                try:
                    # resolve_target_fragment expects op and fragments list
                    # convert fragmap to list
                    fr_list = list(fragmap.values())
                    target = resolve_target_fragment(op, fr_list)
                except Exception as e:
                    logger.debug(f"resolve_target_fragment error: {e}")
                    target = None
            if not target:
                target = fallback_resolve(op, fragmap)

        if not target:
            logger.info(f"[{opid}] No target found for op_type={optype}, note will be 'no_target'")
            applied_log.append({**op, "id": opid, "applied": False, "note": "no_target"})
            continue

        if target not in fragmap:
            logger.info(f"[{opid}] Resolved target '{target}' not in fragments map")
            applied_log.append({**op, "id": opid, "applied": False, "note": "target_missing_in_fragmap"})
            continue

        frag = fragmap[target]
        if optype and optype.upper().startswith("MODIF"):
            res = apply_modification_to_fragment(frag, detected_span, opid)
        elif optype and optype.upper().startswith("ABROG"):
            res = apply_abrogation_to_fragment(frag, opid)
        elif optype and optype.upper().startswith("AJOUT"):
            res = apply_ajout_to_fragment(frag, detected_span or "", opid)
        else:
            # unknown op type: skip
            logger.info(f"[{opid}] Unknown op_type '{optype}' - skipping")
            applied_log.append({**op, "id": opid, "applied": False, "note": "unknown_op_type"})
            continue

        fragmap[target] = res["fragment"]
        applied_log.append({**op, "id": opid, "applied": bool(res.get("applied", False)), "note": res.get("note")})

    # After applying, write consolidated HTMLs: for each source html, load soup and replace fragments by their html
    # group fragments by source_html_path
    by_source = {}
    for fid, fr in fragmap.items():
        src = fr.get("source_html_path")
        by_source.setdefault(src, []).append(fr)

    consolidated_paths = []
    for src, frs in by_source.items():
        srcp = Path(src)
        soup = BeautifulSoup(srcp.read_text(encoding="utf-8"), "html.parser")
        for fr in frs:
            fid = fr["fragment_id"]
            el = soup.select_one(f'[data-id="{fid}"]')
            if el:
                try:
                    newfrag = BeautifulSoup(fr["html"], "html.parser")
                    el.replace_with(newfrag)
                except Exception:
                    # fallback: replace element string
                    el.string = ""
        outp = out_dir / f"{Path(src).stem}_consolidated.html"
        outp.write_text(str(soup), encoding="utf-8")
        consolidated_paths.append(str(outp))
        logger.info(f"Wrote consolidated HTML: {outp}")

    # save applied ops log
    applied_path = out_dir / "applied_ops.json"
    applied_path.write_text(json.dumps(applied_log, indent=2, ensure_ascii=False), encoding="utf-8")
    logger.info(f"Wrote applied ops log: {applied_path}")

    # also copy original change_log for trace
    try:
        original_change_log = out_dir / "change_log_input.json"
        original_change_log.write_text(json.dumps(ops, indent=2, ensure_ascii=False), encoding="utf-8")
    except Exception:
        pass

    print("Consolidation finished.")
    print("Consolidated files:", consolidated_paths)
    print("Applied ops log:", applied_path)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--ops", "-o", help="Chemin vers change_log.json", default="out/change_log.json")
    parser.add_argument("--html-dir", "-i", help="Dossier contenant les HTML Arrêtify (ex: out/work)", default="out/work")
    parser.add_argument("--out-dir", "-d", help="Dossier de sortie pour HTML consolidés", default="out/work")
    args = parser.parse_args()
    main(args)
