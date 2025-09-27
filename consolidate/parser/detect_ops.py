import re
from typing import List, Dict, Optional
from rapidfuzz import process, fuzz

# Patterns (améliorables)
OP_PATTERNS = {
    "ABROG": [r"\best abrog[eé]\b", r"\babrog(e|é)\b", r"\b(est|sera) abrog[ée]\b"],
    "MODIF": [r"\best modifi\w+\b", r"\best remplac\w+\b", r"\bmodification\b", r"\best modif"],
    "AJOUT": [r"\best ajout", r"\bcompl[eè]t", r"\bins?ert"],
}

# target article patterns: capture Article numbers, with/without "L' "
TARGET_ART_RE = re.compile(r"(?:l['’]\s*article|article|art\.?)\s*(?:n[°o]\s*)?(\d+(-\d+)?)", re.IGNORECASE)
DATE_RE = re.compile(r"(\d{1,2}\s+\w+\s+\d{4})|((?:1er|\d{1,2})\s+\w+\s+\d{4})", re.IGNORECASE)


def _find_article_number_in_text(text: str) -> Optional[str]:
    m = TARGET_ART_RE.search(text)
    if m:
        return m.group(1)
    return None


def detect_ops_in_fragments(fragments: List[Dict]) -> List[Dict]:
    """
    Parcourt la liste de fragments (issus du parse Arrêtify).
    Pour chaque fragment, split en phrases simples et détecte opérations.
    Retourne une liste d'opérations avec tentative de target_ref (numéro d'article)
    """
    ops = []
    for frag in fragments:
        src_node = frag.get("parent_node") or frag.get("parent")
        text = frag.get("text", "")
        # simple sentence split — suffisant pour PoC ; on peut remplacer par spaCy.sentencizer
        sents = re.split(r"(?<=[\.\!\?;:])\s+", text)
        for s in sents:
            low = s.lower()
            found = None
            for op_type, patterns in OP_PATTERNS.items():
                for p in patterns:
                    if re.search(p, low):
                        found = op_type
                        break
                if found:
                    break
            if not found:
                continue
            # try to extract an explicit article number
            art_num = _find_article_number_in_text(s)
            # fallback: try to extract date (useful for ordering)
            mdate = DATE_RE.search(s)
            eff_date = mdate.group(0) if mdate else None

            op = {
                "op_type": found,
                "source_node": src_node,
                "detected_span": s.strip(),
                "target_ref": art_num,        # possibly numeric string like "3" or "1-1"
                "effective_date": eff_date,
                "confidence": 0.7,
            }
            # attach the fragment where the op was detected (helps resolution)
            op["detected_in_fragment_id"] = frag.get("fragment_id")
            ops.append(op)
    return ops

# compatibility wrapper (ajouter en bas de detect_ops.py)
def detect_ops_in_text(text: str, source_node_id: str = None, pub_date=None):
    """
    Wrapper rétrocompatible : transforme un texte brut en un unique fragment
    puis appelle detect_ops_in_fragments(...) pour produire la même liste d'opérations.
    """
    temp_frag = {
        "fragment_id": f"{source_node_id or 'tmp'}_frag_0",
        "parent_node": source_node_id,
        "text": text,
        "title": None,
        "data_number": None
    }
    return detect_ops_in_fragments([temp_frag])