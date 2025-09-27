# consolidate/parser/consolidate_engine.py
from typing import List, Dict, Optional
from rapidfuzz import process, fuzz
import difflib
import re
from dateutil import parser as dateparser


def resolve_target_fragment(op: dict, fragments: list) -> Optional[str]:
    """
    op: operation dict with keys 'target_ref' (article number if present) and 'detected_in_fragment_id'
    fragments: list of fragment dicts with keys fragment_id, title (optionnel), text, html and optionally data-number
    Returns fragment_id or None
    """
    # 1) if op was detected inside a fragment, prefer that fragment (local change)
    det_frag = op.get("detected_in_fragment_id")
    if det_frag and det_frag in {f["fragment_id"] for f in fragments}:
        return det_frag

    target_ref = op.get("target_ref")
    # 2) if target_ref is numeric (article number), try to match fragments by data-number or by title containing that number
    if target_ref:
        # normalize e.g. "1-1" or "3"
        num = str(target_ref).strip()
        # search fragments for data-number attribute if present in fragment metadata
        for f in fragments:
            # try fragment metadata 'data-number' or title like "Article 2 -"
            # some fragments may store number in 'title' or 'parent' fields
            title = (f.get("title") or "").lower()
            # try exact "article {num}" in title
            if re.search(rf"\barticle\s+{re.escape(num)}\b", title, re.IGNORECASE):
                return f["fragment_id"]
            # try data-number stored in fragment (string)
            fn = f.get("data_number") or f.get("data-number") or f.get("number")
            if fn and str(fn) == num:
                return f["fragment_id"]
    # 3) fuzzy match on titles (high priority)
    titles = {f["fragment_id"]: (f.get("title") or "").strip() for f in fragments}
    if any(titles.values()):
        # build candidate mapping excluding empties
        choices = {k: v for k, v in titles.items() if v}
        if op.get("target_ref"):
            best = process.extractOne(str(op["target_ref"]), choices, scorer=fuzz.WRatio)
            if best and best[1] >= 80:
                return best[2]
    # 4) fuzzy match on fragment text
    texts = {f["fragment_id"]: f.get("text","") for f in fragments}
    if op.get("detected_span"):
        best = process.extractOne(op["detected_span"][:200], texts, scorer=fuzz.token_set_ratio)
        if best and best[1] >= 65:
            return best[2]
    return None

def apply_operations(ops_list, fragments):
    fragments_map = {f["fragment_id"]: f for f in fragments}
    def key_fn(op):
        dt = op.get("effective_date")
        try:
            return dateparser.parse(dt).timestamp() if dt else 0
        except Exception:
            return 0
    ops_sorted = sorted(ops_list, key=key_fn)
    change_log = []
    for i, op in enumerate(ops_sorted):
        target = resolve_target_fragment(op, fragments)
        if not target:
            texts = {fid: f["text"] for fid, f in fragments_map.items()}
            best = process.extractOne(op.get("detected_span","")[:200], texts, scorer=fuzz.token_set_ratio)
            if best and best[1] > 50:
                target = best[2]
        if not target:
            op_res = {**op, "applied": False, "note": "no_target"}
            change_log.append(op_res)
            continue
        frag = fragments_map[target]
        old = frag["text"]
        applied = False; note = ""
        if op["op_type"] == "abrogation":
            frag["html"] = f"<del data-op='op_{i}'>{frag['html']}</del>"
            frag["text"] = ""
            applied, note = True, "abrogated"
        elif op["op_type"] in ("modification","remplacement"):
            span = op.get("detected_span","").strip()
            if span and span in old:
                frag["html"] = frag["html"].replace(span, f"<del data-op='op_{i}'>{span}</del><ins data-op='op_{i}'>[MODIFIED]</ins>")
                frag["text"] = re.sub(r"<.*?>","",frag["html"])
                applied, note = True, "modified_by_span"
            else:
                s = difflib.SequenceMatcher(None, old, op.get("detected_span",""))
                match = None
                for a,b,size in s.get_matching_blocks():
                    if size>30:
                        match=(a,a+size); break
                if match:
                    a,b = match
                    replaced = old[:a] + f"<del data-op='op_{i}'>" + old[a:b] + f"</del><ins data-op='op_{i}'>[MODIFIED]</ins>" + old[b:]
                    frag["html"] = frag["html"].replace(old, replaced, 1)
                    frag["text"] = re.sub(r"<.*?>","",frag["html"])
                    applied, note = True, "modified_by_alignment"
                else:
                    applied, note = False, "could_not_localize"
        elif op["op_type"] == "ajout":
            frag["html"] = frag["html"] + f"<ins data-op='op_{i}'><!-- ADDED -->{op.get('detected_span','[ADDED]')}</ins>"
            frag["text"] = re.sub(r"<.*?>","",frag["html"])
            applied, note = True, "added_append"
        else:
            applied, note = False, "unknown_op"
        op_res = {**op, "applied": applied, "note": note, "target_fragment": target}
        change_log.append(op_res)
    return fragments_map, change_log
