# consolidate/graph/executor.py
import json
import logging
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Dict

import networkx as nx

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("executor")


def load_json(p: Path) -> Any:
    return json.loads(p.read_text(encoding="utf-8"))


def detect_graph_cycles(graph_data: Dict) -> list:
    """
    Detect cycles using networkx node-link format
    """
    G = nx.node_link_graph(graph_data)
    try:
        cycles = list(nx.simple_cycles(G))
    except Exception:
        cycles = []
    return cycles


def detect_concurrent_ops(ops: list) -> list:
    """
    Simple heuristic: group by (target: detected_in_fragment_id or target_ref) and effective_date.
    If >1 op in same group => conflict.
    """
    from collections import defaultdict

    groups = defaultdict(list)
    for op in ops:
        tgt = (
            op.get("detected_in_fragment_id")
            or op.get("target_ref")
            or op.get("target_fragment")
            or "NO_TARGET"
        )
        date = op.get("effective_date") or "NO_DATE"
        key = (tgt, date)
        groups[key].append(op)

    conflicts = []
    for k, v in groups.items():
        if len(v) > 1 and k[0] != "NO_TARGET":
            conflicts.append(
                {
                    "target": k[0],
                    "date": k[1],
                    "ops": [
                        {
                            "id": op.get("id")
                            or op.get("op_id")
                            or (op.get("source_node") + "_" + str(i) if op.get("source_node") else str(i)),
                            "op_type": op.get("op_type"),
                            "confidence": op.get("confidence"),
                        }
                        for i, op in enumerate(v)
                    ],
                }
            )
    return conflicts


def main(
    graph_path: str = "out/consolidation_graph.json",
    ops_path: str = "out/change_log.json",
    html_dir: str = "out/work",
    out_dir: str = "out/consolidated",
) -> Dict[str, Any]:
    graph_p = Path(graph_path)
    ops_p = Path(ops_path)
    outp = Path(out_dir)
    outp.mkdir(parents=True, exist_ok=True)

    if not graph_p.exists():
        raise FileNotFoundError(f"{graph_p} manquant. Génère d'abord le graphe.")
    if not ops_p.exists():
        raise FileNotFoundError(f"{ops_p} manquant. Génère d'abord change_log.json")

    logger.info("Lecture du graphe et des opérations...")
    graph_data = load_json(graph_p)

    # READ OPS
    ops = load_json(ops_p)
    logger.info(f"Loaded {len(ops)} operations from {ops_p}")

    # Try to fill missing effective_date using node date found in HTMLs in html_dir
    try:
        from consolidate.parser.parse_html import parse_arretify_html
    except Exception:
        parse_arretify_html = None

    if parse_arretify_html:
        for op in ops:
            if not op.get("effective_date"):
                det = (
                    op.get("detected_in_fragment_id")
                    or op.get("target_fragment")
                    or op.get("detected_fragment")
                )
                if det:
                    # look for that fragment id in html_dir files
                    for p in Path(html_dir).glob("*.html"):
                        try:
                            node, frs, _ = parse_arretify_html(str(p))
                        except Exception:
                            continue
                        if any(f.get("fragment_id") == det for f in frs):
                            nd = node.get("date")
                            if nd:
                                op["effective_date"] = nd
                            break

    # 1) detect cycles
    logger.info("Détection des cycles dans le graphe...")
    cycles = detect_graph_cycles(graph_data)
    logger.info(f"Cycles trouvés : {len(cycles)}")

    # 2) detect concurrent ops
    logger.info("Détection des opérations concurrentes (même cible / même date)...")
    concurrent_conflicts = detect_concurrent_ops(ops)
    logger.info(f"Conflits concurrents détectés : {len(concurrent_conflicts)}")

    conflicts = {"graph_cycles": cycles, "concurrent_ops": concurrent_conflicts}
    conflicts_path = outp / "conflicts.json"
    conflicts_path.write_text(json.dumps(conflicts, indent=2, ensure_ascii=False), encoding="utf-8")
    logger.info(f"Wrote conflicts to {conflicts_path}")

    # 3) call consolidator to apply ops globally
    logger.info("Lancement du consolidator pour appliquer les opérations (global)...")
    try:
        from consolidate.apply import consolidator
    except Exception as e:
        logger.error("Impossible d'importer consolidate.apply.consolidator — vérifier que le fichier existe.")
        raise

    # Prepare args object for consolidator.main
    args = SimpleNamespace(ops=str(ops_p), html_dir=str(html_dir), out_dir=str(outp))
    consolidator.main(args)

    # 4) check applied ops produced by consolidator
    applied_ops_path = outp / "applied_ops.json"
    if applied_ops_path.exists():
        logger.info(f"Applied ops log present: {applied_ops_path}")
    else:
        logger.warning("Aucun applied_ops.json trouvé dans le dossier consolidé — vérifie le consolidator.")

    logger.info("Executor terminé. Sorties :")
    logger.info(f" - Consolidated HTMLs dans: {outp}")
    logger.info(f" - Conflicts: {conflicts_path}")
    return {"consolidated_dir": str(outp), "conflicts": str(conflicts_path), "applied_ops": str(applied_ops_path) if applied_ops_path.exists() else None}


if __name__ == "__main__":
    import argparse

    p = argparse.ArgumentParser()
    p.add_argument("--graph", default="out/consolidation_graph.json")
    p.add_argument("--ops", default="out/change_log.json")
    p.add_argument("--html-dir", default="out/work")
    p.add_argument("--out-dir", default="out/consolidated")
    args = p.parse_args()
    main(graph_path=args.graph, ops_path=args.ops, html_dir=args.html_dir, out_dir=args.out_dir)
