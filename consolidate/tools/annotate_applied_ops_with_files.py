#!/usr/bin/env python3
"""
Annotate applied_ops.json with a best matching consolidated file path found in out/consolidated/.

Usage:
  python tools/annotate_applied_ops_with_files.py

Outputs:
  - out/consolidated/applied_ops_annotated.json (new file)
  - prints mapping summary
"""
from pathlib import Path
import json
from difflib import SequenceMatcher

try:
    from rapidfuzz import process, fuzz
    HAVE_RAPIDFUZZ = True
except Exception:
    HAVE_RAPIDFUZZ = False

BASE = Path("out/consolidated")
# search for applied_ops.json in out/consolidated first, fallback to out/
candidates = [BASE / "applied_ops.json", Path("out") / "applied_ops.json", Path("out") / "change_log.json"]
applied_p = None
for c in candidates:
    if c.exists():
        applied_p = c
        break
if applied_p is None:
    raise SystemExit("Impossible de trouver applied_ops.json (essayé: out/consolidated, out/change_log.json).")

html_files = sorted([p for p in BASE.glob("*.html")])
html_names = [p.name for p in html_files]

print(f"Found {len(html_files)} html files in {BASE}")
print("Using ops file:", applied_p)

ops = json.loads(applied_p.read_text(encoding="utf-8"))
annotated = []
mapping_summary = []

def simple_score(a,b):
    # a and b are strings
    if not a or not b:
        return 0.0
    return SequenceMatcher(None, a, b).ratio()

for op in ops:
    # try derive node-like token(s) to match filenames
    node = (op.get("source_node") or op.get("source") or op.get("target_fragment") or "")
    node_norm = str(node).lower().replace(" ", "_").replace("/", "_")
    # also consider id and detected_in_fragment_id and detected_span short snippet
    candidates_for_op = []

    # 1) first take files that contain the node token
    for name in html_names:
        if node_norm and node_norm in name.lower():
            candidates_for_op.append(name)

    # 2) if none, try files that contain detected_in_fragment_id or other identifiers
    if not candidates_for_op:
        det = op.get("detected_in_fragment_id") or op.get("target_fragment") or ""
        det_norm = str(det).lower().replace(" ", "_")
        for name in html_names:
            if det_norm and det_norm in name.lower():
                candidates_for_op.append(name)

    # 3) if still none, fallback to fuzzy search on detected_span preview
    chosen = None
    score = 0.0
    if candidates_for_op:
        # choose shortest candidate (prefer exact-ish)
        chosen = sorted(candidates_for_op, key=lambda x: (len(x), x))[0]
        score = 1.0
    else:
        snippet = (op.get("detected_span") or "")[:250].strip().lower()
        if snippet:
            if HAVE_RAPIDFUZZ:
                # use token_set_ratio for robustness to reordering / OCR noise
                best = process.extractOne(snippet, {n: n for n in html_names}, scorer=fuzz.token_set_ratio)
                if best:
                    chosen, bestscore = best[0], best[1]/100.0
                    score = bestscore
            else:
                # difflib scan
                bestname = None
                best_r = 0.0
                for name in html_names:
                    r = simple_score(snippet, name.lower())
                    if r > best_r:
                        best_r = r
                        bestname = name
                chosen = bestname
                score = best_r

    if chosen:
        op["consolidated_path"] = f"/consolidated/{chosen}"
        mapping_summary.append({"op_id": op.get("id") or op.get("op_id") or op.get("source_node"), "chosen": chosen, "score": score})
    else:
        op["consolidated_path"] = None
        mapping_summary.append({"op_id": op.get("id") or op.get("op_id") or op.get("source_node"), "chosen": None, "score": 0.0})

    annotated.append(op)

outp = BASE / "applied_ops_annotated.json"
outp.write_text(json.dumps(annotated, ensure_ascii=False, indent=2), encoding="utf-8")
print("Wrote annotated ops to:", outp)
print("Mappings (sample):")
for m in mapping_summary:
    print(m)
