# consolidate/pocevo.py
from pathlib import Path
from bs4 import BeautifulSoup
import re, json
import networkx as nx
from rapidfuzz import process, fuzz
import difflib
from datetime import datetime
from dateutil import parser as dateparser

# ---- helpers ----
OP_PATTERNS = {
    "ABROG": [r"\best abrog[eé]\b", r"\babrog(e|é)\b"],
    "MODIF": [r"\best modifi\w+", r"\best remplac", r"\bmodification\b"],
    "AJOUT": [r"\best ajout", r"\bcompl[eè]t"],
}

DATE_RE = re.compile(r"(\d{1,2}\s+\w+\s+\d{4})|((?:1er|\d{1,2})\s+\w+\s+\d{4})", re.IGNORECASE)
TARGET_ART_RE = re.compile(r"(article[s]?\s+[^\.,;\n]+)", re.IGNORECASE)

def parse_arretify(path: Path):
    soup = BeautifulSoup(path.read_text(encoding='utf-8'), 'html.parser')
    header = soup.select_one('.arretify-header')
    date = None
    if header:
        d = header.select_one('.date')
        date = d.get_text(strip=True) if d else None
    node_id = path.stem
    fragments = []
    for idx, art in enumerate(soup.select('.arretify-article')):
        fid = art.get('data-id') or f"{node_id}_frag_{idx}"
        fragments.append({
            "fragment_id": fid,
            "parent": node_id,
            "text": art.get_text("\n", strip=True),
            "html": str(art)
        })
    return {"node_id": node_id, "date": date, "path": str(path)}, fragments, soup

def detect_ops(text, src_node):
    ops = []
    sents = re.split(r"(?<=[\.\!\?])\s+", text)
    for s in sents:
        low = s.lower()
        found = None
        for op, pats in OP_PATTERNS.items():
            for p in pats:
                if re.search(p, low):
                    found = op; break
            if found: break
        if not found: continue
        targ = TARGET_ART_RE.search(s)
        eff = DATE_RE.search(s)
        ops.append({
            "op_type": found,
            "source_node": src_node,
            "detected_span": s.strip(),
            "target_ref": targ.group(1) if targ else None,
            "effective_date": eff.group(0) if eff else None,
            "confidence": 0.6
        })
    return ops

# ---- target resolution & application ----
def resolve_fragment(op, fragments):
    # if target_ref matches "article X", fuzzy-match on fragment titles or text
    if op.get("target_ref"):
        choices = {f["fragment_id"]: f["text"] for f in fragments}
        best = process.extractOne(op["target_ref"], choices, scorer=fuzz.WRatio)
        if best and best[1] > 60:
            return best[2]
    # fallback: try matching detected_span into fragments
    texts = {f["fragment_id"]: f["text"] for f in fragments}
    best = process.extractOne(op["detected_span"][:200], texts, scorer=fuzz.token_set_ratio)
    if best and best[1] > 50:
        return best[2]
    return None

def apply_modification(frag_html, old_text, detected_span, opid):
    # try direct replace
    if detected_span in old_text:
        new_html = frag_html.replace(detected_span,
            f"<del data-op='{opid}'>{detected_span}</del><ins data-op='{opid}'>[MODIFIED]</ins>")
        return new_html, True
    # alignment fallback
    sm = difflib.SequenceMatcher(None, old_text, detected_span)
    for a,b,size in sm.get_matching_blocks():
        if size > 30:
            replaced = old_text[:a] + f"<del data-op='{opid}'>" + old_text[a:a+size] + f"</del><ins data-op='{opid}'>[MODIFIED]</ins>" + old_text[a+size:]
            new_html = frag_html.replace(old_text, replaced, 1)
            return new_html, True
    return frag_html, False

# ---- main PoC flow for a folder of arrêtés ----
def build_and_resolve(folder):
    folder = Path(folder)
    nodes = []; fragments_all = []
    for f in sorted(folder.glob("*.html")):
        node, frags, soup = parse_arretify(f)
        nodes.append(node)
        fragments_all.extend(frags)
    G = nx.DiGraph()
    # add nodes & fragments
    for n in nodes:
        G.add_node(n['node_id'], **n)
    for fr in fragments_all:
        G.add_node(fr['fragment_id'], **fr)
        G.add_edge(fr['parent'], fr['fragment_id'], relation='contains')
    # detect ops on each node text
    ops = []
    for n in nodes:
        soup = Path(n['path']).read_text(encoding='utf-8')
        ops += detect_ops(soup, n['node_id'])
    # add op nodes
    for i,op in enumerate(ops):
        oid = f"op_{i}"
        op['id'] = oid
        G.add_node(oid, **op)
        G.add_edge(op['source_node'], oid, relation='declares')
    # apply ops sorted by effective_date
    def datekey(o):
        try:
            return dateparser.parse(G.nodes[o].get('effective_date')).timestamp()
        except Exception:
            return 0
    op_nodes = [n for n,d in G.nodes(data=True) if d.get('id') and d.get('op_type')]
    op_nodes.sort(key=datekey)
    change_log = []
    # fragments map
    fragmap = {f['fragment_id']: f for f in fragments_all}
    for opnode in op_nodes:
        op = G.nodes[opnode]
        target = resolve_fragment(op, fragments_all)
        if not target:
            change_log.append({**op, "applied": False, "note": "no_target"})
            continue
        frag = fragmap[target]
        new_html, applied = False, False
        if op['op_type']=="ABROG":
            frag['html'] = f"<del data-op='{opnode}'>{frag['html']}</del>"
            frag['text'] = ""
            applied = True
        elif op['op_type']=="MODIF":
            new_html, applied = apply_modification(frag['html'], frag['text'], op['detected_span'], opnode)
            if applied:
                frag['html'] = new_html
                frag['text'] = re.sub(r"<.*?>","",frag['html'])
        elif op['op_type']=="AJOUT":
            frag['html'] = frag['html'] + f"<ins data-op='{opnode}'>{op['detected_span']}</ins>"
            frag['text'] = re.sub(r"<.*?>","",frag['html'])
            applied = True
        change_log.append({"op": opnode, "applied": applied, "target": target})
    # write consolidated files in folder /consolidated
    out = folder.parent / (folder.name + "_consolidated")
    out.mkdir(parents=True, exist_ok=True)
    # generate consolidated html per original node
    # (simple approach: open original html and replace fragments by their frag['html'])
    for n in nodes:
        soup = BeautifulSoup(Path(n['path']).read_text(encoding='utf-8'), 'html.parser')
        for fid,fr in fragmap.items():
            if fr['parent']!=n['node_id']: continue
            el = soup.select_one(f'[data-id="{fid}"]')
            if el:
                el.replace_with(BeautifulSoup(fr['html'], 'html.parser'))
        (out / f"{n['node_id']}_consolidated.html").write_text(str(soup), encoding='utf-8')
    (out / "change_log.json").write_text(json.dumps(change_log, indent=2, ensure_ascii=False), encoding='utf-8')
    print("done ->", out)

if __name__=="__main__":
    build_and_resolve("out/work")  # adapte le dossier
