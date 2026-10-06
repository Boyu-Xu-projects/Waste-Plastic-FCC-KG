from __future__ import annotations

from pathlib import Path
import sqlite3
import pandas as pd

# ======================================================================================
# 04_16_build_M2_route_layer_shared_KG_v3_1.py
#
# PURPOSE
# -------
# Add a NON-DESTRUCTIVE paper-route layer on top of the frozen WastePlastic-FCC KG.
#
# The core KG is NOT modified.
# No Publication/Paper nodes are created.
#
# Input:
#   1) v3.1 final four-class paper classification
#   2) frozen core KG SQLite
#
# Output:
#   04_16_M2_route_layer/
#       paper_route_layer.csv
#       route_provenance.csv
#       route_edge_support.csv
#       route_node_support.csv
#       route_relation_summary.csv
#       m2_route_layer.sqlite
#       ROUTE_LAYER_SUMMARY.txt
#
# Key idea:
#   Paper -> route label
#   Provenance row -> inherits paper route
#   Existing KG edges/nodes keep their original canonical IDs.
#   R1/R2/R3 are FILTERS / VIEWS over the SAME KG, not separate KGs.
# ======================================================================================

ROOT = Path(r"C:\Users\xuboy\plastic-waste-project")

ROUTE_FILE = (
    ROOT
    / "04_14_paper_route_classification_v3_1_EXPANDED"
    / "paper_route_final_v3_1_EXPANDED.csv"
)

CORE_DB = ROOT / "05_final_kg_polymer_cleaned_v1" / "kg.sqlite"

OUT_DIR = ROOT / "04_16_M2_route_layer"
OUT_DIR.mkdir(parents=True, exist_ok=True)

PAPER_ROUTE_OUT = OUT_DIR / "paper_route_layer.csv"
ROUTE_PROV_OUT = OUT_DIR / "route_provenance.csv"
ROUTE_EDGE_OUT = OUT_DIR / "route_edge_support.csv"
ROUTE_NODE_OUT = OUT_DIR / "route_node_support.csv"
ROUTE_REL_OUT = OUT_DIR / "route_relation_summary.csv"
ROUTE_DB_OUT = OUT_DIR / "m2_route_layer.sqlite"
SUMMARY_OUT = OUT_DIR / "ROUTE_LAYER_SUMMARY.txt"

VALID_ROUTES = {
    "R1_DIRECT_FCC",
    "R2_PYROLYSIS_OIL_WAX_TO_FCC",
    "R3_COPROCESSING_WITH_FCC_FEED",
    "RELATED",
}

TARGET_ROUTES = {
    "R1_DIRECT_FCC",
    "R2_PYROLYSIS_OIL_WAX_TO_FCC",
    "R3_COPROCESSING_WITH_FCC_FEED",
}


def clean(x) -> str:
    if pd.isna(x):
        return ""
    return str(x).strip()


def normalise_doc_key(x: str) -> str:
    """
    Make paper IDs joinable whether the route table stores:
        EN::EN_abcd...
    while the core KG provenance stores:
        EN_abcd...
    or vice versa.
    """
    s = clean(x)
    if "::" in s:
        left, right = s.split("::", 1)
        if right:
            return right
    return s


def find_first(cols: list[str], candidates: list[str]) -> str | None:
    for c in candidates:
        if c in cols:
            return c
    return None


def table_columns(con: sqlite3.Connection, table: str) -> list[str]:
    return [r[1] for r in con.execute(f"PRAGMA table_info({table})").fetchall()]


def read_core_table(con: sqlite3.Connection, table: str) -> pd.DataFrame:
    return pd.read_sql_query(f"SELECT * FROM {table}", con)


def choose_join_strategy(route: pd.DataFrame, prov: pd.DataFrame):
    """
    Prefer stable document IDs. Fall back to DOI, then title.
    """
    route_cols = list(route.columns)
    prov_cols = list(prov.columns)

    route_id = find_first(route_cols, ["global_doc_id", "doc_id"])
    prov_id = find_first(prov_cols, ["global_doc_id", "doc_id"])

    if route_id and prov_id:
        return ("doc_id", route_id, prov_id)

    if "doi" in route_cols and "doi" in prov_cols:
        return ("doi", "doi", "doi")

    if "title" in route_cols and "title" in prov_cols:
        return ("title", "title", "title")

    raise RuntimeError(
        "Could not find a common paper key between route file and provenance. "
        "Need doc_id/global_doc_id, DOI, or title."
    )


def route_label_columns(df: pd.DataFrame) -> tuple[str, str | None, str | None, str | None]:
    primary = find_first(
        list(df.columns),
        ["primary_route_v3_1", "primary_route_v3", "primary_route", "integration_route_v2"],
    )
    if not primary:
        raise RuntimeError(
            "Route file does not contain primary_route_v3_1 / primary_route_v3 / primary_route / integration_route_v2."
        )

    secondary = find_first(
        list(df.columns),
        ["secondary_routes_v3_1", "secondary_routes_v3", "secondary_routes"],
    )
    confidence = find_first(
        list(df.columns),
        ["route_confidence_v3_1", "route_confidence_v3", "route_confidence_v2", "confidence"],
    )
    reason = find_first(
        list(df.columns),
        ["route_reason_v3_1", "route_reason_v3", "route_reason_v2", "reason"],
    )
    return primary, secondary, confidence, reason


def main():
    if not ROUTE_FILE.exists():
        raise SystemExit(
            f"Missing route classification:\n{ROUTE_FILE}\n\n"
            "Run 04_14C_v3_1_expand_RELATED_for_route_analysis.py first."
        )

    if not CORE_DB.exists():
        raise SystemExit(f"Missing core KG SQLite:\n{CORE_DB}")

    route = pd.read_csv(ROUTE_FILE, encoding="utf-8-sig", dtype=str).fillna("")

    primary_col, secondary_col, confidence_col, reason_col = route_label_columns(route)

    route["primary_route"] = route[primary_col].map(clean).str.upper()

    bad = route[~route["primary_route"].isin(VALID_ROUTES)]
    if len(bad):
        examples = bad[[primary_col]].head(10).to_string(index=False)
        raise SystemExit(
            f"Found {len(bad)} rows with invalid primary route.\n{examples}"
        )

    with sqlite3.connect(CORE_DB) as con:
        tables = {
            r[0]
            for r in con.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        }

        if "provenance" not in tables:
            raise SystemExit("Core KG has no 'provenance' table.")

        prov = read_core_table(con, "provenance")

        # Nodes and edges are read only to enrich the side layer.
        edges = read_core_table(con, "edges") if "edges" in tables else pd.DataFrame()
        nodes = read_core_table(con, "nodes") if "nodes" in tables else pd.DataFrame()

    join_kind, route_key_col, prov_key_col = choose_join_strategy(route, prov)

    # ----------------------------------------------------------------------------------
    # 1. Paper route layer
    # ----------------------------------------------------------------------------------
    if join_kind == "doc_id":
        route["paper_key"] = route[route_key_col].map(normalise_doc_key)
        prov["paper_key"] = prov[prov_key_col].map(normalise_doc_key)
    elif join_kind == "doi":
        route["paper_key"] = route[route_key_col].map(clean).str.lower()
        prov["paper_key"] = prov[prov_key_col].map(clean).str.lower()
    else:
        route["paper_key"] = route[route_key_col].map(clean).str.casefold()
        prov["paper_key"] = prov[prov_key_col].map(clean).str.casefold()

    paper_cols = ["paper_key", "primary_route"]

    for c, out_name in [
        (route_key_col, "original_paper_id"),
        ("global_doc_id", "global_doc_id"),
        ("doc_id", "doc_id"),
        ("title", "title"),
        ("year", "year"),
        ("doi", "doi"),
    ]:
        if c in route.columns and c not in paper_cols:
            route[out_name] = route[c].map(clean)
            paper_cols.append(out_name)

    if secondary_col:
        route["secondary_routes"] = route[secondary_col].map(clean)
    else:
        route["secondary_routes"] = ""
    paper_cols.append("secondary_routes")

    if confidence_col:
        route["route_confidence"] = route[confidence_col].map(clean)
    else:
        route["route_confidence"] = ""
    paper_cols.append("route_confidence")

    if reason_col:
        route["route_reason"] = route[reason_col].map(clean)
    else:
        route["route_reason"] = ""
    paper_cols.append("route_reason")

    route_source_col = find_first(
        list(route.columns),
        ["route_source_v3_1", "route_source"],
    )
    if route_source_col:
        route["route_source"] = route[route_source_col].map(clean)
    else:
        route["route_source"] = ""
    paper_cols.append("route_source")

    evidence_col = find_first(
        list(route.columns),
        ["route_evidence_cues_v3_1", "route_evidence_cues_v3", "route_evidence_cues_v2"],
    )
    if evidence_col:
        route["route_evidence_cues"] = route[evidence_col].map(clean)
    else:
        route["route_evidence_cues"] = ""
    paper_cols.append("route_evidence_cues")

    paper_route = (
        route[paper_cols]
        .drop_duplicates(subset=["paper_key"], keep="first")
        .copy()
    )

    paper_route.to_csv(PAPER_ROUTE_OUT, index=False, encoding="utf-8-sig")

    # ----------------------------------------------------------------------------------
    # 2. Provenance inherits the route label
    # ----------------------------------------------------------------------------------
    route_map_cols = [
        "paper_key",
        "primary_route",
        "secondary_routes",
        "route_confidence",
    ]

    route_prov = prov.merge(
        paper_route[route_map_cols],
        on="paper_key",
        how="left",
        validate="many_to_one",
    )

    route_prov["primary_route"] = route_prov["primary_route"].fillna("UNMATCHED")
    route_prov["secondary_routes"] = route_prov["secondary_routes"].fillna("")
    route_prov["route_confidence"] = route_prov["route_confidence"].fillna("")

    route_prov.to_csv(ROUTE_PROV_OUT, index=False, encoding="utf-8-sig")

    matched = route_prov[route_prov["primary_route"].isin(VALID_ROUTES)].copy()
    target_prov = matched[matched["primary_route"].isin(TARGET_ROUTES)].copy()

    # ----------------------------------------------------------------------------------
    # 3. Route x edge support
    #
    # SAME edge_id can occur in R1, R2 and R3.
    # That is exactly what we want for shared-KG comparison.
    # ----------------------------------------------------------------------------------
    if "edge_id" not in matched.columns:
        raise SystemExit("Core provenance has no edge_id; cannot build route-edge support.")

    paper_id_for_count = (
        "global_doc_id"
        if "global_doc_id" in matched.columns
        else ("doc_id" if "doc_id" in matched.columns else "paper_key")
    )
    case_id_for_count = (
        "global_case_id"
        if "global_case_id" in matched.columns
        else ("case_id" if "case_id" in matched.columns else None)
    )

    agg_spec = {
        "occurrence_count": ("edge_id", "size"),
        "paper_count": (paper_id_for_count, pd.Series.nunique),
    }
    if case_id_for_count:
        agg_spec["case_count"] = (case_id_for_count, pd.Series.nunique)

    route_edge = (
        matched.groupby(["primary_route", "edge_id"], dropna=False)
        .agg(**agg_spec)
        .reset_index()
    )

    if not edges.empty and "edge_id" in edges.columns:
        enrich = [
            c for c in [
                "edge_id",
                "source_canonical_id",
                "source_canonical_name_en",
                "source_type",
                "relation",
                "target_canonical_id",
                "target_canonical_name_en",
                "target_type",
            ]
            if c in edges.columns
        ]
        route_edge = route_edge.merge(
            edges[enrich].drop_duplicates("edge_id"),
            on="edge_id",
            how="left",
        )

    route_edge = route_edge.sort_values(
        ["primary_route", "paper_count", "occurrence_count"],
        ascending=[True, False, False],
    )
    route_edge.to_csv(ROUTE_EDGE_OUT, index=False, encoding="utf-8-sig")

    # ----------------------------------------------------------------------------------
    # 4. Route x node support
    # ----------------------------------------------------------------------------------
    node_parts = []

    if {
        "source_canonical_id",
        "source_canonical_name_en",
        "source_type",
    }.issubset(matched.columns):
        s = matched[
            [
                "primary_route",
                paper_id_for_count,
                "source_canonical_id",
                "source_canonical_name_en",
                "source_type",
            ]
        ].copy()
        s.columns = [
            "primary_route",
            "paper_id",
            "canonical_id",
            "canonical_name_en",
            "type",
        ]
        node_parts.append(s)

    if {
        "target_canonical_id",
        "target_canonical_name_en",
        "target_type",
    }.issubset(matched.columns):
        t = matched[
            [
                "primary_route",
                paper_id_for_count,
                "target_canonical_id",
                "target_canonical_name_en",
                "target_type",
            ]
        ].copy()
        t.columns = [
            "primary_route",
            "paper_id",
            "canonical_id",
            "canonical_name_en",
            "type",
        ]
        node_parts.append(t)

    if node_parts:
        node_rows = pd.concat(node_parts, ignore_index=True)
        node_rows = node_rows[node_rows["canonical_id"].map(clean) != ""]

        route_node = (
            node_rows.groupby(
                [
                    "primary_route",
                    "canonical_id",
                    "canonical_name_en",
                    "type",
                ],
                dropna=False,
            )
            .agg(
                occurrence_count=("canonical_id", "size"),
                paper_count=("paper_id", pd.Series.nunique),
            )
            .reset_index()
        )

        route_node = route_node.sort_values(
            ["primary_route", "paper_count", "occurrence_count"],
            ascending=[True, False, False],
        )
    else:
        route_node = pd.DataFrame()

    route_node.to_csv(ROUTE_NODE_OUT, index=False, encoding="utf-8-sig")

    # ----------------------------------------------------------------------------------
    # 5. Route x relation summary
    # ----------------------------------------------------------------------------------
    rel_group = ["primary_route"]
    for c in ["source_type", "relation", "target_type"]:
        if c in matched.columns:
            rel_group.append(c)

    route_rel = (
        matched.groupby(rel_group, dropna=False)
        .agg(
            occurrence_count=("edge_id", "size"),
            canonical_edge_count=("edge_id", pd.Series.nunique),
            paper_count=(paper_id_for_count, pd.Series.nunique),
        )
        .reset_index()
        .sort_values(
            ["primary_route", "paper_count", "occurrence_count"],
            ascending=[True, False, False],
        )
    )
    route_rel.to_csv(ROUTE_REL_OUT, index=False, encoding="utf-8-sig")

    # ----------------------------------------------------------------------------------
    # 6. SQLite sidecar
    #
    # This is the object the interface can load later.
    # The frozen core kg.sqlite remains untouched.
    # ----------------------------------------------------------------------------------
    if ROUTE_DB_OUT.exists():
        ROUTE_DB_OUT.unlink()

    with sqlite3.connect(ROUTE_DB_OUT) as out_con:
        paper_route.to_sql("paper_route", out_con, index=False, if_exists="replace")
        route_edge.to_sql("route_edge_support", out_con, index=False, if_exists="replace")
        route_node.to_sql("route_node_support", out_con, index=False, if_exists="replace")
        route_rel.to_sql("route_relation_summary", out_con, index=False, if_exists="replace")

        # Keep route_provenance in SQLite too; this enables evidence filtering in UI.
        route_prov.to_sql("route_provenance", out_con, index=False, if_exists="replace")

        out_con.execute(
            "CREATE INDEX IF NOT EXISTS idx_paper_route_key "
            "ON paper_route(paper_key)"
        )
        out_con.execute(
            "CREATE INDEX IF NOT EXISTS idx_paper_route_route "
            "ON paper_route(primary_route)"
        )
        out_con.execute(
            "CREATE INDEX IF NOT EXISTS idx_route_edge_route "
            "ON route_edge_support(primary_route)"
        )
        out_con.execute(
            "CREATE INDEX IF NOT EXISTS idx_route_edge_edge "
            "ON route_edge_support(edge_id)"
        )
        out_con.execute(
            "CREATE INDEX IF NOT EXISTS idx_route_node_route "
            "ON route_node_support(primary_route)"
        )
        out_con.execute(
            "CREATE INDEX IF NOT EXISTS idx_route_node_node "
            "ON route_node_support(canonical_id)"
        )
        out_con.execute(
            "CREATE INDEX IF NOT EXISTS idx_route_prov_route "
            "ON route_provenance(primary_route)"
        )
        out_con.execute(
            "CREATE INDEX IF NOT EXISTS idx_route_prov_edge "
            "ON route_provenance(edge_id)"
        )
        out_con.commit()

    # ----------------------------------------------------------------------------------
    # 7. Summary
    # ----------------------------------------------------------------------------------
    paper_counts = paper_route["primary_route"].value_counts().to_dict()

    prov_counts = matched["primary_route"].value_counts().to_dict()

    target_edge_counts = (
        route_edge[route_edge["primary_route"].isin(TARGET_ROUTES)]
        .groupby("primary_route")["edge_id"]
        .nunique()
        .to_dict()
    )

    target_node_counts = (
        route_node[route_node["primary_route"].isin(TARGET_ROUTES)]
        .groupby("primary_route")["canonical_id"]
        .nunique()
        .to_dict()
        if not route_node.empty
        else {}
    )

    unmatched_rows = int((route_prov["primary_route"] == "UNMATCHED").sum())
    matched_paper_keys = set(matched["paper_key"].map(clean))
    route_paper_keys = set(paper_route["paper_key"].map(clean))
    route_papers_without_prov = len(route_paper_keys - matched_paper_keys)

    lines = [
        "=" * 108,
        "04_16 M2 SHARED-KG ROUTE LAYER SUMMARY — v3.1 FOUR-CLASS",
        "=" * 108,
        "",
        "Architecture:",
        "  Frozen core KG          : unchanged",
        "  Paper/Publication nodes : NOT created",
        "  Route layer             : paper -> R1/R2/R3/RELATED metadata (v3.1)",
        "  Route-specific KG       : filtered views over the SAME canonical nodes/edges",
        "",
        f"Join strategy             : {join_kind} ({route_key_col} -> {prov_key_col})",
        f"Route-classified papers   : {len(paper_route):,}",
        "",
        "Paper primary-route counts:",
        f"  R1_DIRECT_FCC                      : {paper_counts.get('R1_DIRECT_FCC', 0):,}",
        f"  R2_PYROLYSIS_OIL_WAX_TO_FCC       : {paper_counts.get('R2_PYROLYSIS_OIL_WAX_TO_FCC', 0):,}",
        f"  R3_COPROCESSING_WITH_FCC_FEED     : {paper_counts.get('R3_COPROCESSING_WITH_FCC_FEED', 0):,}",
        f"  RELATED                            : {paper_counts.get('RELATED', 0):,}",
        "",
        "KG support after joining provenance:",
        f"  R1 provenance rows                 : {prov_counts.get('R1_DIRECT_FCC', 0):,}",
        f"  R2 provenance rows                 : {prov_counts.get('R2_PYROLYSIS_OIL_WAX_TO_FCC', 0):,}",
        f"  R3 provenance rows                 : {prov_counts.get('R3_COPROCESSING_WITH_FCC_FEED', 0):,}",
        f"  RELATED provenance rows            : {prov_counts.get('RELATED', 0):,}",
        "",
        "Unique shared-KG edges visible by route:",
        f"  R1                                  : {target_edge_counts.get('R1_DIRECT_FCC', 0):,}",
        f"  R2                                  : {target_edge_counts.get('R2_PYROLYSIS_OIL_WAX_TO_FCC', 0):,}",
        f"  R3                                  : {target_edge_counts.get('R3_COPROCESSING_WITH_FCC_FEED', 0):,}",
        f"  RELATED                             : {int(route_edge[route_edge['primary_route'] == 'RELATED']['edge_id'].nunique()) if not route_edge.empty else 0:,}",
        "",
        "Unique shared-KG nodes visible by route:",
        f"  R1                                  : {target_node_counts.get('R1_DIRECT_FCC', 0):,}",
        f"  R2                                  : {target_node_counts.get('R2_PYROLYSIS_OIL_WAX_TO_FCC', 0):,}",
        f"  R3                                  : {target_node_counts.get('R3_COPROCESSING_WITH_FCC_FEED', 0):,}",
        f"  RELATED                             : {int(route_node[route_node['primary_route'] == 'RELATED']['canonical_id'].nunique()) if not route_node.empty else 0:,}",
        "",
        f"Unmatched provenance rows            : {unmatched_rows:,}",
        f"Route papers without provenance rows : {route_papers_without_prov:,}",
        "",
        "Outputs:",
        f"  {PAPER_ROUTE_OUT}",
        f"  {ROUTE_PROV_OUT}",
        f"  {ROUTE_EDGE_OUT}",
        f"  {ROUTE_NODE_OUT}",
        f"  {ROUTE_REL_OUT}",
        f"  {ROUTE_DB_OUT}",
        "",
        "NEXT:",
        "  1. Keep one shared core KG.",
        "  2. In M2 interface add Route filter: ALL / R1 / R2 / R3 / RELATED.",
        "  3. Query route_edge_support + route_node_support for filtered graph views.",
        "  4. Use route_provenance for route-specific evidence panels.",
        "  5. Build R1/R2/R3 comparison figures from these side-layer tables.",
        "=" * 108,
    ]

    SUMMARY_OUT.write_text("\n".join(lines), encoding="utf-8")
    print("\n".join(lines))


if __name__ == "__main__":
    main()
