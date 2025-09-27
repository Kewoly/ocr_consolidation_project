import json
import networkx as nx
from pathlib import Path
from typing import List, Dict, Any


def build_consolidation_graph(ops: List[Dict[str, Any]]) -> nx.DiGraph:
    """
    Construit un graphe de consolidation à partir d'une liste d'opérations.
    
    - Chaque arrêté (source_node, target_ref) devient un nœud
    - Chaque opération (MODIF, ABROG, etc.) devient une arête orientée
    """

    G = nx.DiGraph()

    for op in ops:
        source = op.get("source_node")
        target = op.get("target_ref")

        # Toujours ajouter le nœud source
        if source and source not in G:
            G.add_node(source, type="arrete")

        # Ajouter la cible si détectée
        if target:
            if target not in G:
                G.add_node(target, type="arrete")

            # Ajouter une arête orientée avec infos
            G.add_edge(
                source,
                target,
                op_type=op.get("op_type"),
                effective_date=op.get("effective_date"),
                span=op.get("detected_span"),
                confidence=op.get("confidence"),
            )

    return G


def save_graph(graph: nx.DiGraph, output_path: Path) -> None:
    """
    Sauvegarde le graphe en JSON pour visualisation ou debug
    """
    data = nx.node_link_data(graph)
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)


if __name__ == "__main__":
    # On lit directement change_log.json
    ops_path = Path("out/change_log.json")
    if not ops_path.exists():
        raise FileNotFoundError(f"{ops_path} manquant, exécute d'abord runner.py")

    with open(ops_path, "r", encoding="utf-8") as f:
        ops = json.load(f)

    G = build_consolidation_graph(ops)
    save_graph(G, Path("out/consolidation_graph.json"))

    print(f"Graphe construit avec {len(G.nodes)} nœuds et {len(G.edges)} arêtes.")
