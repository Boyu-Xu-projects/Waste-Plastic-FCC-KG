#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Waste Plastic–FCC KG interface — M1 updated on the original relation-driven UI.

This is an update of the original 05_04 relation-driven interface, NOT a new UI.

M01 now reads the approved M1 data layer:
    原料身份与组成 -> 原料性质 -> 反应倾向

The M1 interface also applies the final controlled browsing taxonomy:
    WasteSource: 34 classes
    WastePlastic: 35 classes
    Polymer: 84 controlled canonical concepts

The controlled taxonomy is an INTERFACE / ANALYSIS VIEW only. It does not
rewrite canonical IDs in the underlying KG. WasteSource and WastePlastic
classes expand to the original canonical nodes; Polymer classes select the
final controlled Polymer canonical node directly.

M01 remains unchanged.
M02 keeps the NON-DESTRUCTIVE paper-route filter layer (ALL / R1 / R2 / R3 / R4-related).
M03 is the condition-product-performance analysis layer. It reuses the SAME paper-route
provenance sidecar, but its default analysis scope is R1+R2+R3 (159 route papers).
The former standalone M04 is removed; co-processing/synergy is folded into M02/R3.
No module creates a second KG: all views share the same canonical core KG.

Key M1 principles
-----------------
- Reuse the same canonical IDs from the core KG.
- M1 is a view/subgraph, not a separate KG.
- M1 uses a fixed relation schema.
- ReactionTendency uses normalized categories from the M1 layer.
- Raw evidence/provenance remains available on click.
- No automatic Property -> ReactionTendency causal edge is invented.
"""

from __future__ import annotations

import argparse
import csv
import os
import sqlite3
import threading
import webbrowser
from collections import defaultdict
from pathlib import Path

try:
    from flask import Flask, jsonify, request, render_template_string
except ImportError as exc:
    raise SystemExit("Missing Flask. Install with: python -m pip install flask") from exc


ROOT = Path(os.environ.get("PW_FCC_ROOT", r"C:\Users\xuboy\plastic-waste-project"))
SCRIPT_DIR = Path(__file__).resolve().parent
DB = ROOT / "05_final_kg_polymer_cleaned_v1" / "kg.sqlite"

# Preferred final location. Two fallbacks are accepted so the user can also
# unzip the previously supplied M1_complete_package directly under the project.
M1_CANDIDATES = [
    ROOT / "06_M1_final_polymer_cleaned_v1",
    ROOT / "06_M1_final",
    ROOT / "M1_complete_package",
    ROOT / "06_M1_feedstock_property_layer",
]

M1_DIR = next(
    (
        p for p in M1_CANDIDATES
        if (p / "m1_nodes.csv").exists()
        and (p / "m1_edges.csv").exists()
        and (p / "m1_provenance.csv").exists()
    ),
    None,
)

if not DB.exists():
    raise SystemExit(
        f"Core KG database not found:\n{DB}\n"
        "Keep 05_final_kg/kg.sqlite under the project root."
    )

if M1_DIR is None:
    raise SystemExit(
        "M1 data layer not found.\n\n"
        "Expected one of:\n"
        + "\n".join(f"  {p}" for p in M1_CANDIDATES)
        + "\n\nRequired files: m1_nodes.csv, m1_edges.csv, m1_provenance.csv"
    )

# M2 route metadata layer. This sidecar NEVER modifies the core KG.
M2_ROUTE_DB = ROOT / "04_16_M2_route_layer" / "m2_route_layer.sqlite"
M2_ROUTE_READY = M2_ROUTE_DB.exists()

M2_ROUTE_OPTIONS = [
    {"value": "ALL", "short": "ALL", "label": "全部文献"},
    {"value": "R1_DIRECT_FCC", "short": "R1", "label": "废塑料直接进入FCC"},
    {"value": "R2_PYROLYSIS_OIL_WAX_TO_FCC", "short": "R2", "label": "废塑料 → 热解油/蜡 → FCC"},
    {"value": "R3_COPROCESSING_WITH_FCC_FEED", "short": "R3", "label": "废塑料/热解产物与FCC原料共加工"},
    {"value": "RELATED", "short": "R4", "label": "其他相关研究"},
]
M2_VALID_ROUTE_VALUES = {x["value"] for x in M2_ROUTE_OPTIONS}

TARGET_ROUTE_VALUES = [
    "R1_DIRECT_FCC",
    "R2_PYROLYSIS_OIL_WAX_TO_FCC",
    "R3_COPROCESSING_WITH_FCC_FEED",
]

M3_ROUTE_OPTIONS = [
    {"value": "TARGET", "short": "R1+R2+R3", "label": "三类 FCC 路线（默认分析集）"},
    {"value": "R1_DIRECT_FCC", "short": "R1", "label": "废塑料直接进入 FCC"},
    {"value": "R2_PYROLYSIS_OIL_WAX_TO_FCC", "short": "R2", "label": "废塑料 → 热解油/蜡 → FCC"},
    {"value": "R3_COPROCESSING_WITH_FCC_FEED", "short": "R3", "label": "废塑料/热解产物与 FCC 原料共加工"},
]
M3_VALID_ROUTE_VALUES = {x["value"] for x in M3_ROUTE_OPTIONS}


def route_sql_rows(sql, params=()):
    if not M2_ROUTE_READY:
        return []
    with sqlite3.connect(M2_ROUTE_DB) as con:
        con.row_factory = sqlite3.Row
        return [dict(r) for r in con.execute(sql, params).fetchall()]


# ----------------------------------------------------------------------
# Scientific modules
# ----------------------------------------------------------------------
MODULES = {
    "M01": {
        "label": "原料身份与组成 / 原料性质 / 反应倾向",
        "question": "废塑料来自哪里、属于哪类废塑料、由哪些聚合物组成，并具有什么性质和反应倾向？",
        # Visible selectors: the agreed 4 + 8 + 1 M1 entity schema.
        "types": [
            "WastePlastic", "Polymer", "PlasticForm", "WasteSource",
            "CompositionRatio", "HCRatio", "ChlorineContent",
            "OxygenContent", "AshContent", "BoilingRange", "Density",
            "Additive", "ReactionTendency",
        ],
        # PyrolysisOil is not a plastic type in M1. It is retained only as an
        # auxiliary owner of properties such as boiling range.
        "aux_types": ["PyrolysisOil"],
        "groups": [
            {
                "label": "原料身份与组成",
                "types": ["WasteSource", "WastePlastic", "Polymer", "PlasticForm"],
            },
            {
                "label": "原料性质",
                "types": [
                    "CompositionRatio", "HCRatio", "ChlorineContent",
                    "OxygenContent", "AshContent", "BoilingRange",
                    "Density", "Additive",
                ],
            },
            {
                "label": "反应倾向",
                "types": ["ReactionTendency"],
            },
        ],
        "relations": [
            "CONTAINS",
            "HAS_FORM",
            "DERIVED_FROM",
            "HAS_COMPOSITION_RATIO",
            "HAS_HC_RATIO",
            "HAS_CHLORINE_CONTENT",
            "HAS_OXYGEN_CONTENT",
            "HAS_ASH_CONTENT",
            "HAS_BOILING_RANGE",
            "HAS_DENSITY",
            "CONTAINS_ADDITIVE",
            "EXHIBITS",
        ],
        "note": (
            "M01 使用整理后的 M1 layer。WasteSource / WastePlastic / Polymer 在界面中增加受控分类层："
            "WasteSource 34类、WastePlastic 35类、Polymer 84类；底层 canonical ID 不改。"
            "PyrolysisOil 仅作为馏程等性质的辅助载体；ReactionTendency 默认显示 M1 归一化类别；"
            "不自动建立 Property → ReactionTendency 因果边。"
        ),
    },
    "M02": {
        "label": "FCC 技术路线 / 进料准备 / 反应体系 / 产品",
        "question": "不同废塑料通过什么技术路线进入 FCC，这些路线在进料准备、反应体系和产品上有何差异？",
        "types": [
            "WastePlastic", "Polymer", "PyrolysisOil", "Wax",
            "ProcessingRoute",
            "Pretreatment", "FCCFeedstock", "CoProcessingRatio",
            "Reactor", "Catalyst", "Zeolite", "CatalystProperty", "StudyScale",
            "Product", "SynergyEffect", "Mechanism",
        ],
        "groups": [
            {
                "label": "原料与加工过程",
                "types": ["WastePlastic", "Polymer", "PyrolysisOil", "Wax", "ProcessingRoute"],
            },
            {
                "label": "进料准备",
                "types": ["Pretreatment", "FCCFeedstock", "CoProcessingRatio"],
            },
            {
                "label": "反应体系（催化体系含 Catalyst / Zeolite / CatalystProperty）",
                "types": ["Reactor", "Catalyst", "Zeolite", "CatalystProperty", "StudyScale"],
            },
            {
                "label": "产品 / R3 共加工协同补充",
                "types": ["Product", "SynergyEffect", "Mechanism"],
            },
        ],
        "relations": [
            "DERIVED_FROM",
            "PROCESSED_VIA",
            "PRETREATED_BY",
            "CO_PROCESSED_WITH",
            "HAS_CO_PROCESSING_RATIO",
            "USES_REACTOR",
            "USES_CATALYST",
            "CONTAINS_ZEOLITE",
            "HAS_CATALYST_PROPERTY",
            "HAS_STUDY_SCALE",
            "PRODUCES",
            "EXHIBITS",
            "EXPLAINED_BY",
            "AFFECTS",
        ],
        "note": (
            "M02 仍使用同一套 core KG。R1/R2/R3/R4(RELATED) 仅作为论文级 route metadata 筛选；"
            "不会创建路线实体。原独立 M04 已取消，共加工比例、协同效应及其机制并入 R3 补充视图。"
        ),
    },
    "M03": {
        "label": "反应条件 / 产品分布 / 催化性能",
        "question": "三类 FCC 路线在什么反应条件下运行，这些条件与产品分布和催化性能如何关联？",
        "types": [
            "WastePlastic", "Polymer", "PyrolysisOil", "FCCFeedstock", "ProcessingRoute",
            "Catalyst", "Zeolite", "Reactor",
            "Temperature", "CatalystFeedRatio", "ReactionTime", "Pressure",
            "SpaceVelocity", "CoProcessingRatio", "FeedRate", "FluidizationGasRate",
            "Product", "ProductProperty", "Yield", "Selectivity", "Conversion",
        ],
        "groups": [
            {
                "label": "路线与反应体系上下文",
                "types": [
                    "WastePlastic", "Polymer", "PyrolysisOil", "FCCFeedstock",
                    "ProcessingRoute", "Catalyst", "Zeolite", "Reactor",
                ],
            },
            {
                "label": "操作条件",
                "types": [
                    "Temperature", "CatalystFeedRatio", "ReactionTime", "Pressure",
                    "SpaceVelocity", "CoProcessingRatio", "FeedRate", "FluidizationGasRate",
                ],
            },
            {
                "label": "产品与性能",
                "types": ["Product", "ProductProperty", "Yield", "Selectivity", "Conversion"],
            },
        ],
        "relations": [
            "PROCESSED_VIA",
            "USES_CATALYST",
            "CONTAINS_ZEOLITE",
            "USES_REACTOR",
            "HAS_TEMPERATURE",
            "HAS_PRESSURE",
            "HAS_REACTION_TIME",
            "HAS_SPACE_VELOCITY",
            "HAS_CATALYST_FEED_RATIO",
            "HAS_CO_PROCESSING_RATIO",
            "HAS_FEED_RATE",
            "HAS_FLUIDIZATION_GAS_RATE",
            "PRODUCES",
            "HAS_YIELD",
            "HAS_SELECTIVITY",
            "HAS_CONVERSION",
            "HAS_PRODUCT_PROPERTY",
            "AFFECTS",
        ],
        "note": (
            "M03 对应论文 2.3“反应条件与产品分布”。默认只统计 R1+R2+R3 三类明确 FCC 路线文献，"
            "不把 RELATED 文献混入路线条件统计；R1/R2/R3 仍只是同一共享 KG 的 provenance filter。"
        ),
    },

}


TYPE_ZH = {
    "WastePlastic":"废塑料","Polymer":"聚合物","PlasticForm":"塑料形态",
    "WasteSource":"废塑料来源","FCCFeedstock":"FCC 原料","PyrolysisOil":"热解油",
    "Wax":"蜡","Additive":"添加剂","CompositionRatio":"组成比例","HCRatio":"H/C 比",
    "ChlorineContent":"含氯量","OxygenContent":"含氧量","AshContent":"灰分",
    "BoilingRange":"热解油馏程","Density":"密度","ProcessingRoute":"加工路线",
    "Pretreatment":"预处理","Catalyst":"催化剂","Zeolite":"分子筛",
    "CatalystProperty":"催化剂性质","Reactor":"反应器","StudyScale":"研究尺度",
    "Temperature":"温度","Pressure":"压力","ReactionTime":"反应时间",
    "SpaceVelocity":"空速","CatalystFeedRatio":"剂料比","CoProcessingRatio":"共加工比例",
    "FeedRate":"进料速率","FluidizationGasRate":"流化气速率","Product":"产品",
    "ProductProperty":"产品性质","Yield":"收率","Selectivity":"选择性",
    "Conversion":"转化率","Mechanism":"机理","ReactionTendency":"反应倾向",
    "SynergyEffect":"协同效应","CarbonEmission":"碳排放","CarbonReduction":"碳减排",
}


REL_ZH = {
    "CONTAINS":"包含聚合物/组分",
    "HAS_FORM":"具有形态",
    "DERIVED_FROM":"来源于",
    "HAS_COMPOSITION_RATIO":"具有组成比例",
    "HAS_HC_RATIO":"具有 H/C 比",
    "HAS_CHLORINE_CONTENT":"具有含氯量",
    "HAS_OXYGEN_CONTENT":"具有含氧量",
    "HAS_ASH_CONTENT":"具有灰分",
    "HAS_BOILING_RANGE":"具有馏程",
    "HAS_DENSITY":"具有密度",
    "CONTAINS_ADDITIVE":"含有添加剂",
    "PROCESSED_VIA":"通过路线加工",
    "PRETREATED_BY":"经预处理",
    "CO_PROCESSED_WITH":"与…共加工",
    "USES_CATALYST":"使用催化剂",
    "CONTAINS_ZEOLITE":"包含分子筛",
    "HAS_CATALYST_PROPERTY":"具有催化剂性质",
    "USES_REACTOR":"使用反应器",
    "HAS_TEMPERATURE":"反应温度",
    "HAS_PRESSURE":"反应压力",
    "HAS_REACTION_TIME":"反应时间",
    "HAS_SPACE_VELOCITY":"空速",
    "HAS_CATALYST_FEED_RATIO":"剂料比",
    "HAS_CO_PROCESSING_RATIO":"共加工比例",
    "HAS_FEED_RATE":"进料速率",
    "HAS_FLUIDIZATION_GAS_RATE":"流化气速率",
    "PRODUCES":"生成",
    "HAS_YIELD":"具有收率",
    "HAS_SELECTIVITY":"具有选择性",
    "HAS_CONVERSION":"具有转化率",
    "HAS_PRODUCT_PROPERTY":"具有产品性质",
    "EXHIBITS":"表现出",
    "EXPLAINED_BY":"由…解释",
    "UNDERGOES":"发生/经历",
    "AFFECTS":"影响",
    "HAS_STUDY_SCALE":"研究尺度",
    "HAS_CARBON_EMISSION":"具有碳排放",
    "HAS_CARBON_REDUCTION":"具有碳减排",
}


app = Flask(__name__)


# ----------------------------------------------------------------------
# Core SQLite helpers
# ----------------------------------------------------------------------
def sql_rows(sql, params=()):
    con = sqlite3.connect(DB)
    con.row_factory = sqlite3.Row
    try:
        return [dict(r) for r in con.execute(sql, params).fetchall()]
    finally:
        con.close()


# ----------------------------------------------------------------------
# M1 controlled taxonomy used by the interface
# ----------------------------------------------------------------------
CONTROLLED_TAXONOMY_CANDIDATES = [
    ROOT / "m1_controlled_taxonomy",
    SCRIPT_DIR / "m1_controlled_taxonomy",
]

CONTROLLED_TAXONOMY_DIR = next(
    (
        p for p in CONTROLLED_TAXONOMY_CANDIDATES
        if (p / "wasteplastic_categories_35.csv").exists()
        and (p / "wasteplastic_mapping_35.csv").exists()
        and (p / "wastesource_categories_34.csv").exists()
        and (p / "wastesource_mapping_34.csv").exists()
        and (p / "polymer_84.csv").exists()
    ),
    None,
)

if CONTROLLED_TAXONOMY_DIR is None:
    raise SystemExit(
        "M1 controlled taxonomy not found.\n\n"
        "Expected folder 'm1_controlled_taxonomy' either under the project root or next to this script.\n"
        "Required files: wasteplastic_categories_35.csv, wasteplastic_mapping_35.csv, "
        "wastesource_categories_34.csv, wastesource_mapping_34.csv, polymer_84.csv"
    )


# ----------------------------------------------------------------------
# M1 in-memory layer
# ----------------------------------------------------------------------
def read_csv_rows(path: Path) -> list[dict]:
    with path.open("r", encoding="utf-8-sig", newline="") as f:
        return [dict(r) for r in csv.DictReader(f)]


def _num(v):
    try:
        return float(v or 0)
    except Exception:
        return 0.0


WP_CATEGORY_ROWS = read_csv_rows(CONTROLLED_TAXONOMY_DIR / "wasteplastic_categories_35.csv")
WP_MAPPING_ROWS = read_csv_rows(CONTROLLED_TAXONOMY_DIR / "wasteplastic_mapping_35.csv")
WS_CATEGORY_ROWS = read_csv_rows(CONTROLLED_TAXONOMY_DIR / "wastesource_categories_34.csv")
WS_MAPPING_ROWS = read_csv_rows(CONTROLLED_TAXONOMY_DIR / "wastesource_mapping_34.csv")
POLYMER84_ROWS = read_csv_rows(CONTROLLED_TAXONOMY_DIR / "polymer_84.csv")

CONTROLLED_TYPES = {"WasteSource", "WastePlastic", "Polymer"}

CONTROLLED_CATEGORIES = {
    "WastePlastic": [],
    "WasteSource": [],
    "Polymer": [],
}
CONTROLLED_MEMBERS = defaultdict(list)
CONTROLLED_CATEGORY_BY_NODE = {}

# WastePlastic: 35 controlled classes, each class expands to its original canonical nodes.
for r in WP_CATEGORY_ROWS:
    code = (r.get("Code") or "").strip()
    if not code:
        continue
    CONTROLLED_CATEGORIES["WastePlastic"].append({
        "code": code,
        "name_zh": (r.get("废塑料类别") or "").strip(),
        "name_en": (r.get("English category") or "").strip(),
        "member_count": int(_num(r.get("原始节点数"))),
        "occurrence_count": int(_num(r.get("Occurrence总数"))),
    })

for r in WP_MAPPING_ROWS:
    if (r.get("Action") or "").strip().upper() != "MERGE":
        continue
    cid = (r.get("Canonical ID") or "").strip()
    code = (r.get("Target code") or "").strip()
    if not cid or not code:
        continue
    CONTROLLED_MEMBERS[("WastePlastic", code)].append(cid)
    CONTROLLED_CATEGORY_BY_NODE[("WastePlastic", cid)] = {
        "code": code,
        "name_zh": (r.get("归并类别") or "").strip(),
        "name_en": (r.get("English category") or "").strip(),
    }

# WasteSource: 34 controlled classes. DROP / REVIEW rows are deliberately excluded
# from the controlled selector and from the curated M1 interface view.
for r in WS_CATEGORY_ROWS:
    code = (r.get("Code") or "").strip()
    if not code:
        continue
    CONTROLLED_CATEGORIES["WasteSource"].append({
        "code": code,
        "name_zh": (r.get("废塑料来源类别") or "").strip(),
        "name_en": (r.get("English category") or "").strip(),
        "member_count": int(_num(r.get("原始节点数"))),
        "occurrence_count": int(_num(r.get("Occurrence总数"))),
    })

for r in WS_MAPPING_ROWS:
    if (r.get("Action") or "").strip().upper() != "MERGE":
        continue
    cid = (r.get("Canonical ID") or "").strip()
    code = (r.get("Target code") or "").strip()
    if not cid or not code:
        continue
    CONTROLLED_MEMBERS[("WasteSource", code)].append(cid)
    CONTROLLED_CATEGORY_BY_NODE[("WasteSource", cid)] = {
        "code": code,
        "name_zh": (r.get("归并类别") or "").strip(),
        "name_en": (r.get("English category") or "").strip(),
    }

# Polymer: the final 84 concepts are already canonical entities, so the category
# itself is the selectable canonical node (no synthetic parent node is created).
for r in POLYMER84_ROWS:
    cid = (r.get("Canonical ID") or "").strip()
    if not cid:
        continue
    cat = {
        "code": cid,
        "canonical_id": cid,
        "rank": int(_num(r.get("Rank"))),
        "name_zh": (r.get("中文名称") or "").strip(),
        "name_en": (r.get("English label") or "").strip(),
        "member_count": 1,
        "occurrence_count": int(_num(r.get("Occurrence"))),
        "paper_count": int(_num(r.get("Papers"))),
        "case_count": int(_num(r.get("Cases"))),
    }
    CONTROLLED_CATEGORIES["Polymer"].append(cat)
    CONTROLLED_MEMBERS[("Polymer", cid)].append(cid)
    CONTROLLED_CATEGORY_BY_NODE[("Polymer", cid)] = {
        "code": cid,
        "name_zh": cat["name_zh"],
        "name_en": cat["name_en"],
        "rank": cat["rank"],
    }

CONTROLLED_CATEGORY_COUNTS = {t: len(v) for t, v in CONTROLLED_CATEGORIES.items()}
CONTROLLED_ALLOWED_IDS = {
    t: {cid for (tt, _), ids in CONTROLLED_MEMBERS.items() if tt == t for cid in ids}
    for t in CONTROLLED_TYPES
}


def controlled_meta(cid: str, etype: str) -> dict:
    return dict(CONTROLLED_CATEGORY_BY_NODE.get((etype, cid), {}))


def controlled_node_allowed(cid: str, etype: str) -> bool:
    if etype not in CONTROLLED_TYPES:
        return True
    return cid in CONTROLLED_ALLOWED_IDS.get(etype, set())


def m1_edge_allowed_in_controlled_view(e: dict) -> bool:
    return (
        controlled_node_allowed(e.get("source_canonical_id", ""), e.get("source_type", ""))
        and controlled_node_allowed(e.get("target_canonical_id", ""), e.get("target_type", ""))
    )


M1_NODES = read_csv_rows(M1_DIR / "m1_nodes.csv")
M1_EDGES_RAW = read_csv_rows(M1_DIR / "m1_edges.csv")
M1_PROV = read_csv_rows(M1_DIR / "m1_provenance.csv")


def controlled_core_identity_edges() -> list[dict]:
    """
    Read the two finalized L1 identity/composition relations from the latest
    polymer-cleaned core KG:

      WastePlastic --DERIVED_FROM--> WasteSource
      WastePlastic --CONTAINS-----> Polymer

    The 35/34/84 controlled sets are then applied in Python. This intentionally
    replaces the older copies of these two relation families in 06_M1_final, so
    the interface matches the finalized Figure 2 data (324 and 37,810 support).
    """
    rows = sql_rows(
        """
        SELECT edge_id,
               source_canonical_id,source_canonical_name_en,source_type,
               relation,
               target_canonical_id,target_canonical_name_en,target_type,
               occurrence_count,paper_count,case_count,
               languages,avg_confidence,example_evidence
        FROM edges
        WHERE (source_type='WastePlastic' AND relation='DERIVED_FROM' AND target_type='WasteSource')
           OR (source_type='WastePlastic' AND relation='CONTAINS' AND target_type='Polymer')
        """
    )
    out = []
    for e in rows:
        if not m1_edge_allowed_in_controlled_view(e):
            continue
        x = dict(e)
        x["edge_origin"] = "CORE_KG_CONTROLLED_M1"
        x["normalization_status"] = "CONTROLLED_TAXONOMY_VIEW"
        x["target_display_name_zh"] = ""
        out.append(x)
    return out


# Keep the curated M1 property and normalized-tendency relations, but replace the
# two L1 identity/composition relation families with their latest core-KG versions.
M1_EDGES_BASE = []
for e in M1_EDGES_RAW:
    if not m1_edge_allowed_in_controlled_view(e):
        continue
    sig = (e.get("source_type", ""), e.get("relation", ""), e.get("target_type", ""))
    if sig in {
        ("WastePlastic", "CONTAINS", "Polymer"),
        ("WastePlastic", "DERIVED_FROM", "WasteSource"),
        ("Polymer", "DERIVED_FROM", "WasteSource"),
    }:
        continue
    M1_EDGES_BASE.append(e)

M1_CORE_IDENTITY_EDGES = controlled_core_identity_edges()
M1_CORE_REPLACEMENT_EDGE_IDS = {e.get("edge_id", "") for e in M1_CORE_IDENTITY_EDGES}
M1_EDGES = M1_EDGES_BASE + M1_CORE_IDENTITY_EDGES

M1_NODE_BY_ID = {r["canonical_id"]: r for r in M1_NODES}
M1_EDGE_BY_ID = {r["edge_id"]: r for r in M1_EDGES}

M1_NODES_BY_TYPE = defaultdict(list)
for r in M1_NODES:
    M1_NODES_BY_TYPE[r.get("type", "")].append(r)

for t in list(M1_NODES_BY_TYPE):
    M1_NODES_BY_TYPE[t].sort(
        key=lambda x: (
            -int(float(x.get("paper_count") or 0)),
            -int(float(x.get("occurrence_count") or 0)),
            x.get("canonical_name_en", "").lower(),
        )
    )

M1_EDGES_BY_NODE = defaultdict(list)
for r in M1_EDGES:
    M1_EDGES_BY_NODE[r.get("source_canonical_id", "")].append(r)
    M1_EDGES_BY_NODE[r.get("target_canonical_id", "")].append(r)

M1_PROV_BY_EDGE = defaultdict(list)
M1_PROV_BY_NODE = defaultdict(list)
M1_VIEW_EDGE_IDS = set(M1_EDGE_BY_ID)
for r in M1_PROV:
    # Keep node/edge evidence consistent with the controlled M1 interface view.
    # Evidence belonging only to DROP / REVIEW WastePlastic or WasteSource nodes,
    # or to Polymer concepts outside the final Polymer84 set, stays in the source
    # files for audit but is not surfaced in the default M1 interface.
    # The finalized Source→WastePlastic and WastePlastic→Polymer relations are
    # sourced directly from the latest core KG, so their provenance is also read
    # from the core DB rather than from the older M1 snapshot.
    if r.get("edge_id", "") in M1_CORE_REPLACEMENT_EDGE_IDS:
        continue
    if r.get("edge_id", "") not in M1_VIEW_EDGE_IDS:
        continue
    M1_PROV_BY_EDGE[r.get("edge_id", "")].append(r)
    M1_PROV_BY_NODE[r.get("source_canonical_id", "")].append(r)
    if r.get("target_canonical_id") != r.get("source_canonical_id"):
        M1_PROV_BY_NODE[r.get("target_canonical_id", "")].append(r)


def as_int(v) -> int:
    try:
        return int(float(v or 0))
    except Exception:
        return 0


def m1_display_name(r: dict) -> str:
    return (r.get("display_name_zh") or "").strip() or r.get("canonical_name_en", "")


def add_controlled_meta_to_node(r: dict) -> dict:
    x = dict(r)
    meta = controlled_meta(x.get("canonical_id", ""), x.get("type", ""))
    if meta:
        x["controlled_category_code"] = meta.get("code", "")
        x["controlled_category_zh"] = meta.get("name_zh", "")
        x["controlled_category_en"] = meta.get("name_en", "")
        if meta.get("rank"):
            x["controlled_rank"] = meta.get("rank")
    return x


def m1_interface_display_name(r: dict) -> str:
    # For Polymer84 the controlled concept itself is the canonical selectable node,
    # so show the finalized bilingual/Chinese label in the graph. WastePlastic and
    # WasteSource remain specific raw canonical nodes, with their category shown as
    # metadata rather than replacing the node label.
    if r.get("type") == "Polymer":
        meta = controlled_meta(r.get("canonical_id", ""), "Polymer")
        if meta and meta.get("name_zh"):
            return meta["name_zh"]
    return m1_display_name(r)


def m1_relation_counts():
    d = {}
    for r in M1_EDGES:
        key = (r.get("relation",""), r.get("source_type",""), r.get("target_type",""))
        x = d.setdefault(
            key,
            {
                "relation": key[0],
                "source_type": key[1],
                "target_type": key[2],
                "canonical_edge_count": 0,
                "occurrence_count": 0,
                "paper_support_sum": 0,
            },
        )
        x["canonical_edge_count"] += 1
        x["occurrence_count"] += as_int(r.get("occurrence_count"))
        x["paper_support_sum"] += as_int(r.get("paper_count"))
    return sorted(
        d.values(),
        key=lambda x: (-x["canonical_edge_count"], x["source_type"], x["relation"], x["target_type"]),
    )


M1_REL_COUNTS = m1_relation_counts()
M1_TYPE_COUNTS = {t: len(v) for t, v in M1_NODES_BY_TYPE.items()}


# ----------------------------------------------------------------------
# Module helpers
# ----------------------------------------------------------------------
def module_relation_counts(mid):
    if mid == "M01":
        return M1_REL_COUNTS

    m = MODULES[mid]
    t = m["types"]
    r = m["relations"]
    tph = ",".join("?" for _ in t)
    rph = ",".join("?" for _ in r)
    return sql_rows(
        f"""
        SELECT relation, source_type, target_type,
               COUNT(*) AS canonical_edge_count,
               SUM(occurrence_count) AS occurrence_count,
               SUM(paper_count) AS paper_support_sum
        FROM edges
        WHERE relation IN ({rph})
          AND source_type IN ({tph})
          AND target_type IN ({tph})
        GROUP BY relation, source_type, target_type
        ORDER BY canonical_edge_count DESC
        """,
        r + t + t,
    )


def m2_route_paper_counts() -> dict[str, int]:
    if not M2_ROUTE_READY:
        return {}
    rows = route_sql_rows(
        "SELECT primary_route, COUNT(*) AS n FROM paper_route GROUP BY primary_route"
    )
    return {r["primary_route"]: as_int(r["n"]) for r in rows}


def m2_route_relation_counts(route_value: str) -> list[dict]:
    if route_value == "ALL" or not M2_ROUTE_READY:
        return module_relation_counts("M02")
    if route_value not in M2_VALID_ROUTE_VALUES:
        return []

    m = MODULES["M02"]
    types = m["types"]
    rels = m["relations"]
    tph = ",".join("?" for _ in types)
    rph = ",".join("?" for _ in rels)
    return route_sql_rows(
        f"""
        SELECT relation, source_type, target_type,
               canonical_edge_count, occurrence_count,
               paper_count AS paper_support_sum
        FROM route_relation_summary
        WHERE primary_route=?
          AND relation IN ({rph})
          AND source_type IN ({tph})
          AND target_type IN ({tph})
        ORDER BY canonical_edge_count DESC, paper_count DESC
        """,
        [route_value] + rels + types + types,
    )


def m2_route_graph_links(route_value: str, seeds: list[str], max_edges: int) -> list[dict]:
    if route_value == "ALL" or not M2_ROUTE_READY:
        return core_graph_links(["M02"], seeds, max_edges)
    if route_value not in M2_VALID_ROUTE_VALUES:
        return []

    m = MODULES["M02"]
    types = m["types"]
    rels = m["relations"]
    tph = ",".join("?" for _ in types)
    rph = ",".join("?" for _ in rels)
    where = [
        "primary_route=?",
        f"relation IN ({rph})",
        f"source_type IN ({tph})",
        f"target_type IN ({tph})",
    ]
    params = [route_value] + rels + types + types

    if seeds:
        sph = ",".join("?" for _ in seeds)
        where.append(
            f"(source_canonical_id IN ({sph}) OR target_canonical_id IN ({sph}))"
        )
        params += seeds + seeds

    rows = route_sql_rows(
        f"""
        SELECT edge_id,
               source_canonical_id,source_canonical_name_en,source_type,
               relation,
               target_canonical_id,target_canonical_name_en,target_type,
               occurrence_count,paper_count,case_count
        FROM route_edge_support
        WHERE {" AND ".join(where)}
        ORDER BY paper_count DESC, occurrence_count DESC
        LIMIT ?
        """,
        params + [max_edges],
    )
    for e in rows:
        e["edge_origin"] = "M2_ROUTE_VIEW"
        e["primary_route"] = route_value
    return rows


def m2_route_entity_rows(route_value: str, etype: str, q: str = "", limit: int = 250) -> list[dict]:
    if route_value == "ALL" or not M2_ROUTE_READY:
        return []
    if route_value not in M2_VALID_ROUTE_VALUES:
        return []

    m = MODULES["M02"]
    types = m["types"]
    rels = m["relations"]
    tph = ",".join("?" for _ in types)
    rph = ",".join("?" for _ in rels)
    params = [route_value, etype, route_value] + rels + types + types + [route_value] + rels + types + types
    qsql = ""
    if q:
        qsql = " AND rn.canonical_name_en LIKE ?"
        params.append(f"%{q}%")
    params.append(limit)

    return route_sql_rows(
        f"""
        SELECT rn.canonical_id, rn.type, rn.canonical_name_en,
               rn.occurrence_count, rn.paper_count,
               0 AS case_count, 'ROUTE_FILTERED' AS audit_status
        FROM route_node_support rn
        WHERE rn.primary_route=? AND rn.type=?
          AND rn.canonical_id IN (
              SELECT source_canonical_id FROM route_edge_support
              WHERE primary_route=?
                AND relation IN ({rph})
                AND source_type IN ({tph}) AND target_type IN ({tph})
              UNION
              SELECT target_canonical_id FROM route_edge_support
              WHERE primary_route=?
                AND relation IN ({rph})
                AND source_type IN ({tph}) AND target_type IN ({tph})
          )
          {qsql}
        ORDER BY rn.paper_count DESC, rn.occurrence_count DESC
        LIMIT ?
        """,
        params,
    )


def m2_route_node_support(route_value: str, ids: set[str]) -> dict[str, dict]:
    if not ids or route_value == "ALL" or not M2_ROUTE_READY:
        return {}
    ph = ",".join("?" for _ in ids)
    rows = route_sql_rows(
        f"""SELECT canonical_id,occurrence_count,paper_count
            FROM route_node_support
            WHERE primary_route=? AND canonical_id IN ({ph})""",
        [route_value] + list(ids),
    )
    return {r["canonical_id"]: r for r in rows}


def m2_route_edge_detail(route_value: str, eid: str) -> tuple[dict | None, list[dict]]:
    if route_value == "ALL" or not M2_ROUTE_READY:
        return None, []
    e = route_sql_rows(
        "SELECT * FROM route_edge_support WHERE primary_route=? AND edge_id=? LIMIT 1",
        (route_value, eid),
    )
    if not e:
        return None, []
    ev = route_sql_rows(
        """SELECT relation,source_canonical_name_en,target_canonical_name_en,
                  source_raw_name,target_raw_name,language,title,year,doi,filename,page,
                  global_case_id,case_id,evidence,confidence,
                  primary_route,route_confidence
           FROM route_provenance
           WHERE primary_route=? AND edge_id=?
           ORDER BY year DESC LIMIT 160""",
        (route_value, eid),
    )
    return e[0], ev


def m2_route_node_detail(route_value: str, cid: str) -> tuple[list[dict], list[dict]]:
    rels = m2_route_graph_links(route_value, [cid], 300)
    if not rels:
        return [], []

    m = MODULES["M02"]
    types = m["types"]
    rel_names = m["relations"]
    tph = ",".join("?" for _ in types)
    rph = ",".join("?" for _ in rel_names)
    ev = route_sql_rows(
        f"""SELECT relation,source_canonical_name_en,target_canonical_name_en,
                  source_raw_name,target_raw_name,language,title,year,doi,filename,page,
                  global_case_id,case_id,evidence,confidence,
                  primary_route,route_confidence
           FROM route_provenance
           WHERE primary_route=?
             AND relation IN ({rph})
             AND source_type IN ({tph})
             AND target_type IN ({tph})
             AND (source_canonical_id=? OR target_canonical_id=?)
           ORDER BY year DESC LIMIT 300""",
        [route_value] + rel_names + types + types + [cid, cid],
    )
    return rels, ev



# ----------------------------------------------------------------------
# Shared route-provenance helpers for M02 and M03
# ----------------------------------------------------------------------
def route_scope_values(route_value: str) -> list[str]:
    """Translate an interface route scope into primary-route values in the sidecar."""
    if route_value == "TARGET":
        return list(TARGET_ROUTE_VALUES)
    if route_value in M2_VALID_ROUTE_VALUES and route_value != "ALL":
        return [route_value]
    return []


def normalize_module_route_filter(mids: list[str], route_value: str) -> str:
    if mids == ["M03"]:
        return route_value if route_value in M3_VALID_ROUTE_VALUES else "TARGET"
    if mids == ["M02"]:
        return route_value if route_value in M2_VALID_ROUTE_VALUES else "ALL"
    return "ALL"


def route_module_relation_counts(mid: str, route_value: str) -> list[dict]:
    if not M2_ROUTE_READY:
        return module_relation_counts(mid)
    if mid == "M02" and route_value == "ALL":
        return module_relation_counts(mid)

    vals = route_scope_values(route_value)
    if not vals:
        return []

    m = MODULES[mid]
    types = m["types"]
    rels = m["relations"]
    vph = ",".join("?" for _ in vals)
    tph = ",".join("?" for _ in types)
    rph = ",".join("?" for _ in rels)
    return route_sql_rows(
        f"""
        SELECT relation, source_type, target_type,
               COUNT(DISTINCT edge_id) AS canonical_edge_count,
               SUM(occurrence_count) AS occurrence_count,
               SUM(paper_count) AS paper_support_sum
        FROM route_edge_support
        WHERE primary_route IN ({vph})
          AND relation IN ({rph})
          AND source_type IN ({tph})
          AND target_type IN ({tph})
        GROUP BY relation, source_type, target_type
        ORDER BY canonical_edge_count DESC, paper_support_sum DESC
        """,
        vals + rels + types + types,
    )


def route_module_type_counts(mid: str, route_value: str) -> dict[str, int]:
    """Count selectable canonical entities by entity type inside the current route scope."""
    if not M2_ROUTE_READY:
        return {}
    if mid == "M02" and route_value == "ALL":
        # ALL intentionally represents the unfiltered shared M2 core-KG view.
        return {
            r["type"]: int(r["n"])
            for r in sql_rows(
                "SELECT type, COUNT(*) AS n FROM nodes WHERE type IN (%s) GROUP BY type"
                % ",".join("?" for _ in MODULES[mid]["types"]),
                MODULES[mid]["types"],
            )
        }

    vals = route_scope_values(route_value)
    if not vals:
        return {}
    m = MODULES[mid]
    types = m["types"]
    rels = m["relations"]
    vph = ",".join("?" for _ in vals)
    tph = ",".join("?" for _ in types)
    rph = ",".join("?" for _ in rels)
    rows = route_sql_rows(
        f"""
        WITH relevant AS (
            SELECT source_canonical_id AS canonical_id, source_type AS type
            FROM route_edge_support
            WHERE primary_route IN ({vph})
              AND relation IN ({rph})
              AND source_type IN ({tph}) AND target_type IN ({tph})
            UNION
            SELECT target_canonical_id AS canonical_id, target_type AS type
            FROM route_edge_support
            WHERE primary_route IN ({vph})
              AND relation IN ({rph})
              AND source_type IN ({tph}) AND target_type IN ({tph})
        )
        SELECT type, COUNT(DISTINCT canonical_id) AS n
        FROM relevant
        GROUP BY type
        """,
        vals + rels + types + types + vals + rels + types + types,
    )
    return {r["type"]: int(r["n"]) for r in rows}


def route_module_graph_links(mid: str, route_value: str, seeds: list[str], max_edges: int) -> list[dict]:
    if not M2_ROUTE_READY:
        return core_graph_links([mid], seeds, max_edges)
    if mid == "M02" and route_value == "ALL":
        return core_graph_links([mid], seeds, max_edges)

    vals = route_scope_values(route_value)
    if not vals:
        return []

    m = MODULES[mid]
    types = m["types"]
    rels = m["relations"]
    vph = ",".join("?" for _ in vals)
    tph = ",".join("?" for _ in types)
    rph = ",".join("?" for _ in rels)
    where = [
        f"primary_route IN ({vph})",
        f"relation IN ({rph})",
        f"source_type IN ({tph})",
        f"target_type IN ({tph})",
    ]
    params = vals + rels + types + types

    if seeds:
        sph = ",".join("?" for _ in seeds)
        where.append(f"(source_canonical_id IN ({sph}) OR target_canonical_id IN ({sph}))")
        params += seeds + seeds

    rows = route_sql_rows(
        f"""
        SELECT edge_id,
               source_canonical_id,source_canonical_name_en,source_type,
               relation,
               target_canonical_id,target_canonical_name_en,target_type,
               SUM(occurrence_count) AS occurrence_count,
               SUM(paper_count) AS paper_count,
               SUM(case_count) AS case_count
        FROM route_edge_support
        WHERE {" AND ".join(where)}
        GROUP BY edge_id,
                 source_canonical_id,source_canonical_name_en,source_type,
                 relation,target_canonical_id,target_canonical_name_en,target_type
        ORDER BY paper_count DESC, occurrence_count DESC
        LIMIT ?
        """,
        params + [max_edges],
    )
    for e in rows:
        e["edge_origin"] = f"{mid}_ROUTE_VIEW"
        e["primary_route"] = route_value
    return rows


def route_module_entity_rows(mid: str, route_value: str, etype: str, q: str = "", limit: int = 250) -> list[dict]:
    vals = route_scope_values(route_value)
    if not vals or not M2_ROUTE_READY:
        return []

    m = MODULES[mid]
    types = m["types"]
    rels = m["relations"]
    vph = ",".join("?" for _ in vals)
    tph = ",".join("?" for _ in types)
    rph = ",".join("?" for _ in rels)

    params = vals + [etype] + vals + rels + types + types + vals + rels + types + types
    qsql = ""
    if q:
        qsql = " AND rn.canonical_name_en LIKE ?"
        params.append(f"%{q}%")
    params.append(limit)

    return route_sql_rows(
        f"""
        WITH rn AS (
            SELECT canonical_id,type,canonical_name_en,
                   SUM(occurrence_count) AS occurrence_count,
                   SUM(paper_count) AS paper_count
            FROM route_node_support
            WHERE primary_route IN ({vph}) AND type=?
            GROUP BY canonical_id,type,canonical_name_en
        ), relevant AS (
            SELECT source_canonical_id AS canonical_id
            FROM route_edge_support
            WHERE primary_route IN ({vph})
              AND relation IN ({rph})
              AND source_type IN ({tph}) AND target_type IN ({tph})
            UNION
            SELECT target_canonical_id AS canonical_id
            FROM route_edge_support
            WHERE primary_route IN ({vph})
              AND relation IN ({rph})
              AND source_type IN ({tph}) AND target_type IN ({tph})
        )
        SELECT rn.canonical_id, rn.type, rn.canonical_name_en,
               rn.occurrence_count, rn.paper_count,
               0 AS case_count, 'ROUTE_FILTERED' AS audit_status
        FROM rn JOIN relevant USING(canonical_id)
        WHERE 1=1 {qsql}
        ORDER BY rn.paper_count DESC, rn.occurrence_count DESC
        LIMIT ?
        """,
        params,
    )


def route_module_node_support(route_value: str, ids: set[str]) -> dict[str, dict]:
    vals = route_scope_values(route_value)
    if not ids or not vals or not M2_ROUTE_READY:
        return {}
    vph = ",".join("?" for _ in vals)
    iph = ",".join("?" for _ in ids)
    rows = route_sql_rows(
        f"""
        SELECT canonical_id,
               SUM(occurrence_count) AS occurrence_count,
               SUM(paper_count) AS paper_count
        FROM route_node_support
        WHERE primary_route IN ({vph}) AND canonical_id IN ({iph})
        GROUP BY canonical_id
        """,
        vals + list(ids),
    )
    return {r["canonical_id"]: r for r in rows}


def route_module_edge_detail(mid: str, route_value: str, eid: str) -> tuple[dict | None, list[dict]]:
    vals = route_scope_values(route_value)
    if not vals or not M2_ROUTE_READY:
        return None, []
    vph = ",".join("?" for _ in vals)
    e = route_sql_rows(
        f"""
        SELECT edge_id,
               source_canonical_id,source_canonical_name_en,source_type,
               relation,target_canonical_id,target_canonical_name_en,target_type,
               SUM(occurrence_count) AS occurrence_count,
               SUM(paper_count) AS paper_count,
               SUM(case_count) AS case_count
        FROM route_edge_support
        WHERE primary_route IN ({vph}) AND edge_id=?
        GROUP BY edge_id,
                 source_canonical_id,source_canonical_name_en,source_type,
                 relation,target_canonical_id,target_canonical_name_en,target_type
        LIMIT 1
        """,
        vals + [eid],
    )
    if not e:
        return None, []
    ev = route_sql_rows(
        f"""SELECT relation,source_canonical_name_en,target_canonical_name_en,
                  source_raw_name,target_raw_name,language,title,year,doi,filename,page,
                  global_case_id,case_id,evidence,confidence,
                  primary_route,route_confidence
           FROM route_provenance
           WHERE primary_route IN ({vph}) AND edge_id=?
           ORDER BY year DESC LIMIT 200""",
        vals + [eid],
    )
    e[0]["edge_origin"] = f"{mid}_ROUTE_VIEW"
    e[0]["primary_route"] = route_value
    return e[0], ev


def route_module_node_detail(mid: str, route_value: str, cid: str) -> tuple[list[dict], list[dict]]:
    rels = route_module_graph_links(mid, route_value, [cid], 400)
    if not rels:
        return [], []

    vals = route_scope_values(route_value)
    m = MODULES[mid]
    types = m["types"]
    rel_names = m["relations"]
    vph = ",".join("?" for _ in vals)
    tph = ",".join("?" for _ in types)
    rph = ",".join("?" for _ in rel_names)
    ev = route_sql_rows(
        f"""SELECT relation,source_canonical_name_en,target_canonical_name_en,
                  source_raw_name,target_raw_name,language,title,year,doi,filename,page,
                  global_case_id,case_id,evidence,confidence,
                  primary_route,route_confidence
           FROM route_provenance
           WHERE primary_route IN ({vph})
             AND relation IN ({rph})
             AND source_type IN ({tph})
             AND target_type IN ({tph})
             AND (source_canonical_id=? OR target_canonical_id=?)
           ORDER BY year DESC LIMIT 400""",
        vals + rel_names + types + types + [cid, cid],
    )
    return rels, ev


def core_graph_links(mids: list[str], seeds: list[str], max_edges: int) -> list[dict]:
    mids = [x for x in mids if x != "M01"]
    if not mids:
        return []

    type_set = []
    rel_set = []
    for mid in mids:
        for x in MODULES[mid]["types"]:
            if x not in type_set:
                type_set.append(x)
        for x in MODULES[mid]["relations"]:
            if x not in rel_set:
                rel_set.append(x)

    tph = ",".join("?" for _ in type_set)
    rph = ",".join("?" for _ in rel_set)
    where = [
        f"relation IN ({rph})",
        f"source_type IN ({tph})",
        f"target_type IN ({tph})",
    ]
    params = rel_set + type_set + type_set

    if seeds:
        sph = ",".join("?" for _ in seeds)
        where.append(
            f"(source_canonical_id IN ({sph}) OR target_canonical_id IN ({sph}))"
        )
        params += seeds + seeds

    sql = f"""
      SELECT edge_id,
             source_canonical_id,source_canonical_name_en,source_type,
             relation,
             target_canonical_id,target_canonical_name_en,target_type,
             occurrence_count,paper_count,case_count,
             avg_confidence,example_evidence
      FROM edges
      WHERE {" AND ".join(where)}
      ORDER BY paper_count DESC, occurrence_count DESC
      LIMIT ?
    """
    params.append(max_edges)
    out = sql_rows(sql, params)
    for e in out:
        e["edge_origin"] = e.get("edge_origin") or "CORE_KG"
    return out


def m1_node_layer(cid: str) -> str:
    """Return the scientific story layer used only for visual layout."""
    n = M1_NODE_BY_ID.get(cid, {})
    layer = n.get("m1_layer", "")
    if layer.startswith("L1"):
        return "L1"
    if layer.startswith("L2"):
        return "L2"
    if layer.startswith("L3"):
        return "L3"

    t = n.get("type", "")
    if t in {"WastePlastic", "Polymer", "PlasticForm", "WasteSource"}:
        return "L1"
    if t in {
        "CompositionRatio", "HCRatio", "ChlorineContent", "OxygenContent",
        "AshContent", "BoilingRange", "Density", "Additive", "PyrolysisOil"
    }:
        return "L2"
    if t == "ReactionTendency":
        return "L3"
    return "OTHER"


def m1_graph_links(seeds: list[str], max_edges: int) -> list[dict]:
    """
    M1 story retrieval.

    NO SEED:
      Show a balanced preview of the three M1 story components.

    WITH SEED(S):
      Show ALL DIRECT M1 RELATIONSHIPS of the selected entity/entities.
      Do not expand the neighbours' own relationships.

    Example: selecting LDPE shows every M1 edge directly involving LDPE:
      - WastePlastic -> CONTAINS -> LDPE
      - LDPE -> HAS_FORM -> PlasticForm
      - LDPE -> HAS_COMPOSITION_RATIO -> CompositionRatio
      - LDPE -> HAS_HC_RATIO -> HCRatio
      - LDPE -> HAS_OXYGEN_CONTENT -> OxygenContent
      - LDPE -> HAS_ASH_CONTENT -> AshContent
      - LDPE -> HAS_DENSITY -> Density
      - LDPE -> DERIVED_FROM -> WasteSource
      - LDPE -> EXHIBITS -> normalized ReactionTendency

    This keeps the selected material as the story backbone and prevents
    relations of neighbouring waste-plastic nodes from flooding the graph.
    """
    if seeds:
        seen = {}
        for sid in seeds:
            for e in M1_EDGES_BY_NODE.get(sid, []):
                seen[e["edge_id"]] = e

        pool = list(seen.values())

        # Keep every direct relation. Sorting only controls visual load order.
        def direct_story_rank(e):
            a = m1_node_layer(e.get("source_canonical_id", ""))
            b = m1_node_layer(e.get("target_canonical_id", ""))
            if "L3" in {a, b}:
                band = 0  # reaction tendency
            elif "L2" in {a, b}:
                band = 1  # feedstock property
            else:
                band = 2  # feedstock/type/source/form context
            return (
                band,
                e.get("relation", ""),
                -as_int(e.get("paper_count")),
                -as_int(e.get("occurrence_count")),
            )

        pool.sort(key=direct_story_rank)

        # max_edges is intentionally ignored for a normal one-entity M1 story
        # unless a pathological selection exceeds the hard safety ceiling.
        hard_ceiling = max(5000, max_edges)
        return [dict(x) for x in pool[:hard_ceiling]]

    # No entity selected: balanced M1 preview, not a frequency-only hairball.
    buckets = {"L1-L1": [], "L1-L2": [], "L1-L3": [], "OTHER": []}
    for e in M1_EDGES:
        a = m1_node_layer(e.get("source_canonical_id", ""))
        b = m1_node_layer(e.get("target_canonical_id", ""))
        key = "OTHER"
        if a == "L1" and b == "L1":
            key = "L1-L1"
        elif {a, b} == {"L1", "L2"} or (a == "L2" and b == "L2"):
            key = "L1-L2"
        elif {a, b} == {"L1", "L3"}:
            key = "L1-L3"
        buckets[key].append(e)

    for k in buckets:
        buckets[k].sort(
            key=lambda x: (
                -as_int(x.get("paper_count")),
                -as_int(x.get("occurrence_count")),
            )
        )

    quotas = {
        "L1-L1": max(40, int(max_edges * 0.22)),
        "L1-L2": max(80, int(max_edges * 0.43)),
        "L1-L3": max(80, int(max_edges * 0.35)),
        "OTHER": max(10, int(max_edges * 0.05)),
    }

    out = []
    for k in ("L1-L1", "L1-L2", "L1-L3", "OTHER"):
        out.extend(buckets[k][:quotas[k]])

    seen = {e["edge_id"]: e for e in out}
    out = list(seen.values())
    out.sort(
        key=lambda x: (
            -as_int(x.get("paper_count")),
            -as_int(x.get("occurrence_count")),
        )
    )
    return [dict(x) for x in out[:max_edges]]


def core_nodes_for_ids(ids: set[str]) -> list[dict]:
    if not ids:
        return []
    ph = ",".join("?" for _ in ids)
    return sql_rows(
        f"""SELECT canonical_id,type,canonical_name_en,
                   occurrence_count,paper_count,case_count,audit_status
            FROM nodes WHERE canonical_id IN ({ph})""",
        list(ids),
    )


def aliases_for_core_node(cid: str, limit=200) -> list[dict]:
    return sql_rows(
        """SELECT language,raw_name,label_en_precanonical
           FROM aliases
           WHERE canonical_id=?
           ORDER BY language,raw_name
           LIMIT ?""",
        (cid, limit),
    )



# ----------------------------------------------------------------------
# Cross-module entity story
# ----------------------------------------------------------------------
M1_PROPERTY_RELATIONS = {
    "CONTAINS", "HAS_FORM", "DERIVED_FROM",
    "HAS_COMPOSITION_RATIO", "HAS_HC_RATIO",
    "HAS_CHLORINE_CONTENT", "HAS_OXYGEN_CONTENT",
    "HAS_ASH_CONTENT", "HAS_BOILING_RANGE", "HAS_DENSITY",
    "CONTAINS_ADDITIVE", "EXHIBITS",
}

M2_RELATIONS = {
    "PROCESSED_VIA", "PRETREATED_BY",
    "USES_CATALYST", "CONTAINS_ZEOLITE",
    "HAS_CATALYST_PROPERTY", "USES_REACTOR",
    "HAS_STUDY_SCALE", "CO_PROCESSED_WITH",
    "HAS_CO_PROCESSING_RATIO",
}

M3_RELATIONS = {
    "HAS_TEMPERATURE", "HAS_PRESSURE", "HAS_REACTION_TIME",
    "HAS_SPACE_VELOCITY", "HAS_CATALYST_FEED_RATIO",
    "HAS_FEED_RATE", "HAS_FLUIDIZATION_GAS_RATE",
    "PRODUCES", "HAS_YIELD", "HAS_SELECTIVITY",
    "HAS_CONVERSION", "HAS_PRODUCT_PROPERTY",
}

M1_TYPES = {
    "WastePlastic", "Polymer", "PlasticForm", "WasteSource",
    "CompositionRatio", "HCRatio", "ChlorineContent",
    "OxygenContent", "AshContent", "BoilingRange",
    "Density", "Additive", "ReactionTendency",
}

M2_TYPES = {
    "ProcessingRoute", "Pretreatment", "Catalyst", "Zeolite",
    "CatalystProperty", "Reactor", "StudyScale",
    "PyrolysisOil", "Wax", "FCCFeedstock", "CoProcessingRatio",
    "SynergyEffect", "Mechanism",
}

M3_TYPES = {
    "Temperature", "Pressure", "ReactionTime", "SpaceVelocity",
    "CatalystFeedRatio", "FeedRate", "FluidizationGasRate",
    "Product", "ProductProperty", "Yield", "Selectivity", "Conversion",
}


def story_module_for_edge(e: dict) -> str:
    """Assign a direct relation to the three-module scientific story."""
    rel = e.get("relation", "")
    st = e.get("source_type", "")
    tt = e.get("target_type", "")
    types = {st, tt}

    # M1: feedstock identity / properties / normalized reaction tendency
    if rel == "EXHIBITS" and "ReactionTendency" in types:
        return "M01"
    if rel in {
        "HAS_FORM", "HAS_COMPOSITION_RATIO", "HAS_HC_RATIO",
        "HAS_CHLORINE_CONTENT", "HAS_OXYGEN_CONTENT",
        "HAS_ASH_CONTENT", "HAS_BOILING_RANGE", "HAS_DENSITY",
        "CONTAINS_ADDITIVE",
    }:
        return "M01"
    if rel == "CONTAINS" and (
        "Polymer" in types or "PlasticForm" in types or "WastePlastic" in types
    ):
        return "M01"
    if rel == "DERIVED_FROM" and (
        "WasteSource" in types or
        (types <= {"WastePlastic", "Polymer", "PlasticForm", "WasteSource"})
    ):
        return "M01"

    # M2: route, feed preparation, reaction system and R3 co-processing/synergy.
    if rel in M2_RELATIONS:
        return "M02"
    if "SynergyEffect" in types:
        return "M02"
    if rel == "DERIVED_FROM" and ("PyrolysisOil" in types or "Wax" in types or "FCCFeedstock" in types):
        return "M02"
    if rel == "UNDERGOES" and "ProcessingRoute" in types:
        return "M02"
    if "Mechanism" in types and rel in {"EXPLAINED_BY", "UNDERGOES", "EXHIBITS", "AFFECTS"}:
        return "M02"
    if types & {"ProcessingRoute", "Pretreatment", "Catalyst", "Zeolite", "CatalystProperty", "Reactor", "StudyScale"}:
        if rel in {
            "PROCESSED_VIA", "PRETREATED_BY", "USES_CATALYST",
            "CONTAINS_ZEOLITE", "HAS_CATALYST_PROPERTY",
            "USES_REACTOR", "HAS_STUDY_SCALE", "UNDERGOES",
            "DERIVED_FROM",
        }:
            return "M02"

    # M3: reaction conditions, products and performance.
    if rel in M3_RELATIONS:
        return "M03"
    if rel == "AFFECTS" and types & {
        "Product", "ProductProperty", "Yield", "Selectivity", "Conversion",
        "Temperature", "Pressure", "ReactionTime", "SpaceVelocity",
        "CatalystFeedRatio",
    }:
        return "M03"
    if types & M3_TYPES:
        return "M03"
    if rel == "AFFECTS" and ("Product" in types or "ProductProperty" in types):
        return "M03"

    return ""


def exact_entity_story_links(seeds: list[str]) -> list[dict]:
    """
    Exact-canonical entity story across M1-M3.

    IMPORTANT:
    - Selecting Polymer:LDPE means ONLY that canonical LDPE node is the centre.
    - No same-name WastePlastic/PlasticForm/WasteSource node is merged into it.
    - M1 uses the curated/normalized M1 layer.
    - M2-M3 use direct edges from the frozen core KG.
    - All returned edges directly touch the selected canonical node(s).
    """
    if not seeds:
        return []

    seen = {}

    # Curated M1 direct edges first.
    for sid in seeds:
        for e in M1_EDGES_BY_NODE.get(sid, []):
            x = dict(e)
            x["story_module"] = "M01"
            seen[x["edge_id"]] = x

    # Frozen core direct edges across all scientific modules.
    ph = ",".join("?" for _ in seeds)
    rows = sql_rows(
        f"""
        SELECT edge_id,
               source_canonical_id,source_canonical_name_en,source_type,
               relation,
               target_canonical_id,target_canonical_name_en,target_type,
               occurrence_count,paper_count,case_count,
               avg_confidence,example_evidence
        FROM edges
        WHERE source_canonical_id IN ({ph})
           OR target_canonical_id IN ({ph})
        """,
        seeds + seeds,
    )

    for e in rows:
        # Raw fragmented ReactionTendency edges are replaced by the curated M1 view.
        if "ReactionTendency" in {e.get("source_type"), e.get("target_type")}:
            continue

        mod = story_module_for_edge(e)
        if not mod:
            continue

        e["story_module"] = mod
        e["edge_origin"] = "CORE_KG"
        seen[e["edge_id"]] = e

    out = list(seen.values())

    module_order = {"M01": 0, "M02": 1, "M03": 2, "": 9}
    out.sort(
        key=lambda e: (
            module_order.get(e.get("story_module", ""), 9),
            e.get("relation", ""),
            -as_int(e.get("paper_count")),
            -as_int(e.get("occurrence_count")),
        )
    )
    return out


def story_node_module_from_edges(cid: str, links: list[dict], seeds: set[str]) -> str:
    if cid in seeds:
        return "SEED"

    mods = []
    for e in links:
        if cid in {e.get("source_canonical_id"), e.get("target_canonical_id")}:
            m = e.get("story_module", "")
            if m:
                mods.append(m)

    # A node may participate in more than one relation class. For layout,
    # position it in the earliest story module in which it appears.
    for m in ("M01", "M02", "M03"):
        if m in mods:
            return m
    return ""


def story_evidence_for_edge(eid: str, limit=120) -> list[dict]:
    # Finalized L1 identity/composition edges come from the latest core KG.
    if eid in M1_CORE_REPLACEMENT_EDGE_IDS:
        return sql_rows(
            """SELECT relation,source_canonical_name_en,target_canonical_name_en,
                      source_raw_name,target_raw_name,
                      language,title,year,doi,filename,page,
                      global_case_id,case_id,evidence,confidence
               FROM provenance
               WHERE edge_id=?
               ORDER BY year DESC
               LIMIT ?""",
            (eid, limit),
        )

    # M1 normalized/property edge
    if eid in M1_PROV_BY_EDGE:
        return [dict(x) for x in M1_PROV_BY_EDGE[eid]][:limit]

    return sql_rows(
        """SELECT relation,source_canonical_name_en,target_canonical_name_en,
                  source_raw_name,target_raw_name,
                  language,title,year,doi,filename,page,
                  global_case_id,case_id,evidence,confidence
           FROM provenance
           WHERE edge_id=?
           ORDER BY year DESC
           LIMIT ?""",
        (eid, limit),
    )


# ----------------------------------------------------------------------
# API
# ----------------------------------------------------------------------
@app.get("/api/config")
def config():
    core_type_counts = {
        r["type"]: int(r["n"])
        for r in sql_rows("SELECT type, COUNT(*) n FROM nodes GROUP BY type")
    }

    mods = []
    for mid, m in MODULES.items():
        mods.append(
            {
                "id": mid,
                "label": m["label"],
                "question": m["question"],
                "types": m["types"],
                "aux_types": m.get("aux_types", []),
                "groups": m.get("groups", []),
                "relations": m["relations"],
                "note": m.get("note", ""),
                "relation_signatures": module_relation_counts(mid),
            }
        )

    route_counts = m2_route_paper_counts()
    all_count = sum(route_counts.get(x["value"], 0) for x in M2_ROUTE_OPTIONS if x["value"] != "ALL")

    m2_route_options = []
    for opt in M2_ROUTE_OPTIONS:
        x = dict(opt)
        x["paper_count"] = all_count if opt["value"] == "ALL" else route_counts.get(opt["value"], 0)
        x["relation_signatures"] = route_module_relation_counts("M02", opt["value"])
        x["type_counts"] = route_module_type_counts("M02", opt["value"])
        m2_route_options.append(x)

    target_count = sum(route_counts.get(v, 0) for v in TARGET_ROUTE_VALUES)
    m3_route_options = []
    for opt in M3_ROUTE_OPTIONS:
        x = dict(opt)
        x["paper_count"] = target_count if opt["value"] == "TARGET" else route_counts.get(opt["value"], 0)
        x["relation_signatures"] = route_module_relation_counts("M03", opt["value"])
        x["type_counts"] = route_module_type_counts("M03", opt["value"])
        m3_route_options.append(x)

    return jsonify(
        {
            "modules": mods,
            "type_counts": core_type_counts,
            "m1_type_counts": M1_TYPE_COUNTS,
            "controlled_type_counts": CONTROLLED_CATEGORY_COUNTS,
            "controlled_types": sorted(CONTROLLED_TYPES),
            "controlled_taxonomy_dir": str(CONTROLLED_TAXONOMY_DIR),
            "m1_controlled_identity_stats": {
                "source_to_wasteplastic_edges": sum(1 for e in M1_CORE_IDENTITY_EDGES if e.get("relation") == "DERIVED_FROM"),
                "source_to_wasteplastic_occurrence": sum(as_int(e.get("occurrence_count")) for e in M1_CORE_IDENTITY_EDGES if e.get("relation") == "DERIVED_FROM"),
                "wasteplastic_to_polymer_edges": sum(1 for e in M1_CORE_IDENTITY_EDGES if e.get("relation") == "CONTAINS"),
                "wasteplastic_to_polymer_occurrence": sum(as_int(e.get("occurrence_count")) for e in M1_CORE_IDENTITY_EDGES if e.get("relation") == "CONTAINS"),
            },
            "type_zh": TYPE_ZH,
            "rel_zh": REL_ZH,
            "m1_dir": str(M1_DIR),
            "m2_route_ready": M2_ROUTE_READY,
            "m2_route_db": str(M2_ROUTE_DB),
            "m2_route_options": m2_route_options,
            "m3_route_options": m3_route_options,
        }
    )


@app.get("/api/categories")
def categories():
    mids = [
        x
        for x in (request.args.get("modules") or request.args.get("module") or "M01").split(",")
        if x in MODULES
    ]
    etype = (request.args.get("type") or "").strip()
    q = (request.args.get("q") or "").strip().lower()

    if mids != ["M01"] or etype not in CONTROLLED_TYPES:
        return jsonify([])

    rows = []
    for c0 in CONTROLLED_CATEGORIES.get(etype, []):
        c = dict(c0)
        member_ids = CONTROLLED_MEMBERS.get((etype, c.get("code", "")), [])
        # Recalculate actual member count from the final mapping so the UI reflects
        # exactly what is selectable, even if a summary sheet has a different audit count.
        c["member_count"] = len(member_ids) if etype != "Polymer" else 1

        if q:
            hay = " ".join([
                str(c.get("code", "")),
                str(c.get("name_zh", "")),
                str(c.get("name_en", "")),
            ]).lower()
            if q not in hay:
                # Also allow an original/raw node label to reveal its controlled category.
                found = False
                for cid in member_ids:
                    n = M1_NODE_BY_ID.get(cid, {})
                    if q in (n.get("canonical_name_en") or "").lower() or q in (n.get("display_name_zh") or "").lower():
                        found = True
                        break
                if not found:
                    continue
        rows.append(c)

    return jsonify(rows)


@app.get("/api/entities")
def entities():
    mids = [
        x
        for x in (request.args.get("modules") or request.args.get("module") or "M01").split(",")
        if x in MODULES
    ]
    etype = (request.args.get("type") or "").strip()
    q = (request.args.get("q") or "").strip()
    category = (request.args.get("category") or "").strip()
    route_filter = (request.args.get("route") or "ALL").strip()
    route_filter = normalize_module_route_filter(mids, route_filter)

    allowed = set()
    for mid in mids:
        allowed.update(MODULES[mid]["types"])
    if etype not in allowed:
        return jsonify([])

    # If M01 is the only active module, the selector MUST use the curated M1 node set.
    if mids == ["M01"]:
        rows = list(M1_NODES_BY_TYPE.get(etype, []))

        # Controlled M1 types are browsed through the final category layer.
        if etype in CONTROLLED_TYPES and category:
            allowed_ids = set(CONTROLLED_MEMBERS.get((etype, category), []))
            rows = [r for r in rows if r.get("canonical_id") in allowed_ids]
        elif etype in CONTROLLED_TYPES and not category:
            # The UI normally calls /api/categories first. Keeping all valid nodes here
            # preserves compatibility for direct API use while excluding DROP / REVIEW.
            allowed_ids = CONTROLLED_ALLOWED_IDS.get(etype, set())
            rows = [r for r in rows if r.get("canonical_id") in allowed_ids]

        if q:
            ql = q.lower()
            alias_ids = set()
            # Search core aliases as well so Chinese/original labels can find the same canonical node.
            like = f"%{q}%"
            try:
                ar = sql_rows(
                    """SELECT DISTINCT canonical_id
                       FROM aliases
                       WHERE raw_name LIKE ? OR label_en_precanonical LIKE ?
                       LIMIT 1000""",
                    (like, like),
                )
                alias_ids = {x["canonical_id"] for x in ar}
            except Exception:
                alias_ids = set()

            rows = [
                r for r in rows
                if ql in (r.get("canonical_name_en") or "").lower()
                or ql in (r.get("display_name_zh") or "").lower()
                or r.get("canonical_id") in alias_ids
            ]

        out = []
        for r in rows[:250]:
            x = add_controlled_meta_to_node(r)
            x["display_name"] = m1_interface_display_name(x)
            out.append(x)
        return jsonify(out)

    # Route-specific selectors reuse the same paper-route provenance sidecar.
    if mids == ["M02"] and route_filter != "ALL" and M2_ROUTE_READY:
        return jsonify(route_module_entity_rows("M02", route_filter, etype, q, limit=250))
    if mids == ["M03"] and M2_ROUTE_READY:
        return jsonify(route_module_entity_rows("M03", route_filter, etype, q, limit=250))

    # Other modules keep using the frozen core KG.
    params = [etype]
    sql = """
      SELECT canonical_id,type,canonical_name_en,
             occurrence_count,paper_count,case_count,audit_status
      FROM nodes WHERE type=?
    """
    if q:
        like = f"%{q}%"
        sql += """ AND (
          canonical_name_en LIKE ?
          OR canonical_id IN (
            SELECT canonical_id FROM aliases
            WHERE raw_name LIKE ? OR label_en_precanonical LIKE ?
          )
        )"""
        params += [like, like, like]
    sql += " ORDER BY paper_count DESC, occurrence_count DESC LIMIT 250"
    return jsonify(sql_rows(sql, params))


@app.get("/api/graph")
def graph():
    mids = [
        x for x in (request.args.get("modules") or "M01").split(",")
        if x in MODULES
    ]
    seeds = [x for x in (request.args.get("seeds") or "").split(",") if x]
    max_edges = min(max(int(request.args.get("max_edges") or 1000), 50), 8000)
    entity_story = (request.args.get("entity_story") or "0") == "1"
    route_filter = (request.args.get("route") or "ALL").strip()
    route_filter = normalize_module_route_filter(mids, route_filter)

    if not mids:
        return jsonify({"nodes": [], "links": [], "entity_story": False})

    # --------------------------------------------------------------
    # Exact entity story across M1-M3
    # --------------------------------------------------------------
    if entity_story and seeds:
        links = exact_entity_story_links(seeds)
        links = links[:max_edges]

        ids = set(seeds)
        for e in links:
            ids.add(e["source_canonical_id"])
            ids.add(e["target_canonical_id"])
            e["source"] = e["source_canonical_id"]
            e["target"] = e["target_canonical_id"]

        node_map = {r["canonical_id"]: r for r in core_nodes_for_ids(ids)}

        # Overlay curated M1 nodes, including normalized synthetic tendency nodes.
        for cid in ids:
            if cid in M1_NODE_BY_ID:
                r = add_controlled_meta_to_node(M1_NODE_BY_ID[cid])
                r["display_name"] = m1_interface_display_name(r)
                node_map[cid] = r

        seed_set = set(seeds)
        nodes = []
        for cid, n in node_map.items():
            x = dict(n)
            x["id"] = cid
            x["name"] = x.get("display_name") or x.get("canonical_name_en") or cid
            x["is_seed"] = cid in seed_set
            x["story_module"] = story_node_module_from_edges(cid, links, seed_set)
            nodes.append(x)

        counts = {"M01": 0, "M02": 0, "M03": 0}
        for e in links:
            m = e.get("story_module", "")
            if m in counts:
                counts[m] += 1

        return jsonify(
            {
                "nodes": nodes,
                "links": links,
                "entity_story": True,
                "story_counts": counts,
            }
        )

    # --------------------------------------------------------------
    # Original module view
    # --------------------------------------------------------------
    links = []
    if "M01" in mids:
        links.extend(m1_graph_links(seeds, max_edges))

    # Route filtering is module-specific. M03 defaults to the 159-paper R1+R2+R3 scope.
    if mids == ["M02"] and route_filter != "ALL" and M2_ROUTE_READY:
        links.extend(route_module_graph_links("M02", route_filter, seeds, max_edges))
    elif mids == ["M03"] and M2_ROUTE_READY:
        links.extend(route_module_graph_links("M03", route_filter, seeds, max_edges))
    else:
        links.extend(core_graph_links(mids, seeds, max_edges))

    by_id = {}
    for e in links:
        eid = e.get("edge_id", "")
        if not eid:
            continue
        if eid not in by_id or e.get("edge_origin") == "DERIVED_NORMALIZED_TENDENCY":
            by_id[eid] = e

    links = list(by_id.values())
    links.sort(
        key=lambda x: (
            -as_int(x.get("paper_count")),
            -as_int(x.get("occurrence_count")),
        )
    )

    m1_seeded_story = (mids == ["M01"] and bool(seeds))
    if not m1_seeded_story:
        links = links[:max_edges]

    ids = set(seeds)
    for e in links:
        ids.add(e["source_canonical_id"])
        ids.add(e["target_canonical_id"])
        e["source"] = e["source_canonical_id"]
        e["target"] = e["target_canonical_id"]

    node_map = {r["canonical_id"]: r for r in core_nodes_for_ids(ids)}

    for cid in ids:
        if cid in M1_NODE_BY_ID:
            r = add_controlled_meta_to_node(M1_NODE_BY_ID[cid])
            r["display_name"] = m1_interface_display_name(r)
            node_map[cid] = r

    route_node_map = {}
    if mids == ["M02"] and route_filter != "ALL" and M2_ROUTE_READY:
        route_node_map = route_module_node_support(route_filter, ids)
    elif mids == ["M03"] and M2_ROUTE_READY:
        route_node_map = route_module_node_support(route_filter, ids)

    nodes = []
    for cid, n in node_map.items():
        x = dict(n)
        x["id"] = cid
        x["name"] = x.get("display_name") or x.get("canonical_name_en") or cid
        x["is_seed"] = cid in seeds
        if cid in route_node_map:
            x["paper_count"] = as_int(route_node_map[cid].get("paper_count"))
            x["occurrence_count"] = as_int(route_node_map[cid].get("occurrence_count"))
            x["route_filter"] = route_filter

        if "M01" in mids and cid in M1_NODE_BY_ID:
            x["story_layer"] = m1_node_layer(cid)
        else:
            x["story_layer"] = ""

        nodes.append(x)

    return jsonify({
        "nodes": nodes, "links": links, "entity_story": False,
        "route_filter": route_filter if mids in (["M02"], ["M03"]) else "ALL",
    })


@app.get("/api/node/<cid>")
def node(cid):
    mids = [
        x for x in (request.args.get("modules") or "").split(",")
        if x in MODULES
    ]

    story_all = (request.args.get("story_all") or "0") == "1"
    route_filter = (request.args.get("route") or "ALL").strip()
    route_filter = normalize_module_route_filter(mids, route_filter)

    if story_all:
        n0 = sql_rows("SELECT * FROM nodes WHERE canonical_id=?", (cid,))
        if not n0 and cid in M1_NODE_BY_ID:
            n0 = [add_controlled_meta_to_node(M1_NODE_BY_ID[cid])]
        elif n0:
            n0 = [add_controlled_meta_to_node(n0[0])]
        if not n0:
            return jsonify({"error": "not found"}), 404

        links = exact_entity_story_links([cid])
        aliases = aliases_for_core_node(cid) if cid.startswith("ENT_") else []

        ev = []
        seen_prov = set()
        for e in links:
            for pr in story_evidence_for_edge(e.get("edge_id", ""), limit=40):
                pid = pr.get("provenance_id", "") or (
                    pr.get("title", "") + "|" + pr.get("page", "") + "|" + pr.get("evidence", "")
                )
                if pid in seen_prov:
                    continue
                seen_prov.add(pid)
                ev.append(pr)
                if len(ev) >= 500:
                    break
            if len(ev) >= 500:
                break

        return jsonify(
            {
                "node": n0[0],
                "aliases": aliases,
                "relations": links,
                "evidence": ev,
                "data_source": "CROSS_MODULE_ENTITY_STORY",
            }
        )

    if mids in (["M02"], ["M03"]) and M2_ROUTE_READY and not (mids == ["M02"] and route_filter == "ALL"):
        mid = mids[0]
        n = sql_rows("SELECT * FROM nodes WHERE canonical_id=?", (cid,))
        if not n:
            return jsonify({"error": "not found"}), 404
        aliases = aliases_for_core_node(cid)
        rels, ev = route_module_node_detail(mid, route_filter, cid)
        support = route_module_node_support(route_filter, {cid}).get(cid, {})
        node0 = dict(n[0])
        if support:
            node0["paper_count"] = as_int(support.get("paper_count"))
            node0["occurrence_count"] = as_int(support.get("occurrence_count"))
        return jsonify({
            "node": node0,
            "aliases": aliases,
            "relations": rels,
            "evidence": ev,
            "data_source": f"ROUTE_LAYER::{mid}::{route_filter}",
            "route_filter": route_filter,
        })

    if "M01" in mids and cid in M1_NODE_BY_ID:
        n = add_controlled_meta_to_node(M1_NODE_BY_ID[cid])
        n["display_name"] = m1_interface_display_name(n)

        aliases = []
        if cid.startswith("ENT_"):
            aliases = aliases_for_core_node(cid)

        rels = [dict(x) for x in M1_EDGES_BY_NODE.get(cid, [])]
        rels.sort(
            key=lambda x: (
                x.get("relation", ""),
                -as_int(x.get("paper_count")),
                -as_int(x.get("occurrence_count")),
            )
        )

        # Keep evidence consistent with exactly the relations displayed in the
        # controlled M1 view, including the latest core-KG identity/composition edges.
        ev = []
        seen_prov = set()
        for e in rels:
            for pr in story_evidence_for_edge(e.get("edge_id", ""), limit=40):
                pid = pr.get("provenance_id", "") or (
                    str(pr.get("title", "")) + "|" + str(pr.get("page", "")) + "|" + str(pr.get("evidence", ""))
                )
                if pid in seen_prov:
                    continue
                seen_prov.add(pid)
                ev.append(pr)
                if len(ev) >= 400:
                    break
            if len(ev) >= 400:
                break
        return jsonify(
            {
                "node": n,
                "aliases": aliases,
                "relations": rels,
                "evidence": ev,
                "data_source": "M1_CURATED_LAYER",
            }
        )

    n = sql_rows("SELECT * FROM nodes WHERE canonical_id=?", (cid,))
    if not n:
        return jsonify({"error": "not found"}), 404

    aliases = aliases_for_core_node(cid)
    rels = sql_rows(
        """SELECT edge_id,source_canonical_id,source_canonical_name_en,source_type,
                  relation,target_canonical_id,target_canonical_name_en,target_type,
                  occurrence_count,paper_count,case_count
           FROM edges
           WHERE source_canonical_id=? OR target_canonical_id=?
           ORDER BY paper_count DESC,occurrence_count DESC LIMIT 100""",
        (cid, cid),
    )
    ev = sql_rows(
        """SELECT relation,source_canonical_name_en,target_canonical_name_en,
                  language,title,year,doi,filename,page,evidence,confidence
           FROM provenance
           WHERE source_canonical_id=? OR target_canonical_id=?
           ORDER BY year DESC LIMIT 100""",
        (cid, cid),
    )
    return jsonify(
        {
            "node": n[0],
            "aliases": aliases,
            "relations": rels,
            "evidence": ev,
            "data_source": "CORE_KG",
        }
    )


@app.get("/api/edge/<eid>")
def edge(eid):
    mids = [
        x for x in (request.args.get("modules") or "").split(",")
        if x in MODULES
    ]

    story_all = (request.args.get("story_all") or "0") == "1"
    route_filter = (request.args.get("route") or "ALL").strip()
    route_filter = normalize_module_route_filter(mids, route_filter)

    if story_all:
        if eid in M1_EDGE_BY_ID:
            e = dict(M1_EDGE_BY_ID[eid])
            e["story_module"] = "M01"
            ev = story_evidence_for_edge(eid, limit=160)
            return jsonify(
                {
                    "edge": e,
                    "evidence": ev,
                    "data_source": "CROSS_MODULE_ENTITY_STORY",
                }
            )

        e0 = sql_rows("SELECT * FROM edges WHERE edge_id=?", (eid,))
        if not e0:
            return jsonify({"error": "not found"}), 404
        e = e0[0]
        e["story_module"] = story_module_for_edge(e)
        ev = story_evidence_for_edge(eid, limit=160)
        return jsonify(
            {
                "edge": e,
                "evidence": ev,
                "data_source": "CROSS_MODULE_ENTITY_STORY",
            }
        )

    if mids in (["M02"], ["M03"]) and M2_ROUTE_READY and not (mids == ["M02"] and route_filter == "ALL"):
        mid = mids[0]
        e, ev = route_module_edge_detail(mid, route_filter, eid)
        if not e:
            return jsonify({"error": "not found"}), 404
        return jsonify({
            "edge": e,
            "evidence": ev,
            "data_source": f"ROUTE_LAYER::{mid}::{route_filter}",
            "route_filter": route_filter,
        })

    if "M01" in mids and eid in M1_EDGE_BY_ID:
        e = dict(M1_EDGE_BY_ID[eid])
        ev = story_evidence_for_edge(eid, limit=120)
        return jsonify(
            {
                "edge": e,
                "evidence": ev,
                "data_source": "M1_CURATED_LAYER",
            }
        )

    e = sql_rows("SELECT * FROM edges WHERE edge_id=?", (eid,))
    if not e:
        return jsonify({"error": "not found"}), 404
    ev = sql_rows(
        """SELECT language,title,year,doi,filename,page,evidence,confidence,
                  source_raw_name,target_raw_name
           FROM provenance
           WHERE edge_id=?
           ORDER BY year DESC LIMIT 120""",
        (eid,),
    )
    return jsonify({"edge": e[0], "evidence": ev, "data_source": "CORE_KG"})


# ----------------------------------------------------------------------
# Original 3D FCC-KG interface — only M1 content logic is updated.
# ----------------------------------------------------------------------
HTML = r"""
<!doctype html><html><head><meta charset="utf-8">
<title>Waste Plastic–FCC Relation-Driven KG</title>
<script src="https://unpkg.com/three@0.150.1/build/three.min.js"></script>
<script src="https://unpkg.com/3d-force-graph@1.70.12/dist/3d-force-graph.min.js"></script>
<script src="https://unpkg.com/three-spritetext"></script>
<style>
*{box-sizing:border-box}html,body{height:100%;margin:0;overflow:hidden;font-family:Arial,"Microsoft YaHei",sans-serif}
#graph{position:absolute;inset:0;background:white}
#left{position:absolute;left:12px;top:12px;width:470px;max-height:calc(100vh - 24px);overflow:auto;background:#fff;border:1px solid #d3d3d3;border-radius:10px;padding:12px;z-index:1000;box-shadow:0 4px 18px #0002}
h2{font-size:18px;margin:0 0 8px;border-bottom:2px solid #24669b;padding-bottom:7px}
.sec{border:1px solid #e0e0e0;background:#fafafa;border-radius:7px;padding:8px;margin:7px 0}
.st{font-size:12px;font-weight:700;margin-bottom:5px}.small{font-size:10px;color:#666;line-height:1.45}
.module{width:100%;text-align:left;border:1px solid #ccc;background:#fff;border-radius:6px;padding:8px;margin:3px 0;cursor:pointer}
.module.active{background:#e8f2fa;border-color:#24669b;box-shadow:inset 3px 0 #24669b}
.routeGrid{display:grid;grid-template-columns:1fr 1fr;gap:5px}.routeBtn{border:1px solid #c9d3d8;background:#fff;border-radius:7px;padding:7px 6px;cursor:pointer;text-align:left;font-size:10px;line-height:1.3}.routeBtn.active{border-color:#6f8e7c;background:#edf4ef;box-shadow:inset 3px 0 #6f8e7c}.routeBtn b{font-size:11px}.routeBtn .count{float:right;color:#637078;font-weight:400}.routeHint{font-size:9px;color:#68767d;margin-top:5px;line-height:1.4}
.relrow{font-size:9.5px;padding:3px;border-bottom:1px solid #eee}.relname{font-weight:700;color:#244f6a}
details{border:1px solid #ddd;background:white;border-radius:6px;margin:5px 0}summary{cursor:pointer;padding:7px;font-size:11px;font-weight:700}
.body{padding:6px}.q{width:100%;padding:6px;border:1px solid #ccc;border-radius:5px}
.list{max-height:180px;overflow:auto;margin-top:5px}.row{font-size:10px;padding:3px;border-bottom:1px solid #eee}.row label{cursor:pointer}
.chips{display:flex;gap:4px;flex-wrap:wrap}.chip{font-size:9.5px;background:#eef3f7;border:1px solid #cedbe5;border-radius:12px;padding:3px 6px}.chip button{border:0;background:none;cursor:pointer}
.actions{display:grid;grid-template-columns:1fr 1fr;gap:6px}.actions button{border:0;border-radius:5px;padding:8px;color:#fff;font-weight:700;cursor:pointer}#build{background:#32865d}#reset{background:#bd4b40}#center{background:#3178c6;grid-column:1/span 2}
#status{font-size:10px;color:#555;background:#f0f3f5;padding:6px;border-radius:5px;margin-top:6px}
#right{position:absolute;right:0;top:0;width:540px;height:100%;overflow:auto;background:#fff;border-left:1px solid #ddd;padding:15px;z-index:1100;display:none;box-shadow:-4px 0 18px #0002}#detailClose{position:sticky;top:0;float:right;border:1px solid #d4dadd;background:#f4f6f7;color:#111;border-radius:50%;width:32px;height:32px;cursor:pointer;z-index:5;font-size:20px;line-height:28px}#detailClose:hover{background:#e6ebed}
.scene-tooltip{color:#000!important;background:rgba(255,255,255,.96)!important;border:1px solid #ccd5da!important;border-radius:5px!important;padding:5px 7px!important;box-shadow:0 2px 8px #0002!important}
.card{border:1px solid #ddd;border-radius:6px;padding:7px;margin:6px 0;font-size:10.5px}.evidence{white-space:pre-wrap;line-height:1.45;border-left:3px solid #91acc1;padding:6px;margin-top:4px;background:#fafafa}
#legend{position:absolute;left:490px;bottom:10px;background:#fffffff0;border:1px solid #ddd;border-radius:6px;padding:6px;z-index:500;font-size:9px;display:flex;gap:7px;flex-wrap:wrap;max-width:calc(100vw - 1020px)}
#m1Story{position:absolute;left:510px;right:565px;top:16px;z-index:650;display:none;align-items:center;justify-content:center;gap:10px;pointer-events:none}
#m2Story,#m3Story{position:absolute;left:510px;right:565px;top:16px;z-index:650;display:none;align-items:center;justify-content:center;gap:8px;pointer-events:none}
.storyBox{min-width:125px;text-align:center;padding:7px 13px;border-radius:18px;color:white;font-size:12px;font-weight:700;box-shadow:0 2px 7px #0001}
.storyBox.s1{background:#557b91}.storyBox.s2{background:#6f8e7c}.storyBox.s3{background:#83778d}.storyArrow{font-size:20px;color:#89979f;font-weight:700}
#entityStory{position:absolute;left:495px;right:555px;top:16px;z-index:660;display:none;align-items:center;justify-content:center;gap:6px;pointer-events:none}
.storyBox.em1{background:#557b91}.storyBox.em2{background:#6f8e7c}.storyBox.em3{background:#9a7d62}
.dot{width:9px;height:9px;border-radius:50%;display:inline-block;margin-right:3px}
.groupTitle{font-size:10px;font-weight:700;color:#31566d;margin:8px 2px 4px;padding:4px 5px;background:#edf3f6;border-radius:4px}
.note{font-size:9.5px;color:#5d6d75;background:#f4f7f8;border-left:3px solid #7f9ead;padding:6px;margin:5px 0}
.origin{display:inline-block;font-size:8.5px;border:1px solid #cfd8dc;background:#f5f7f8;border-radius:9px;padding:1px 5px;margin-left:4px;color:#607078}
.catDetail{margin:5px 0 5px 10px;border-color:#d9e3e8;background:#fbfdfe}.catDetail>summary{font-size:10.5px;padding:6px}.catMeta{font-size:9px;color:#70828c;margin-left:4px}.controlledBanner{font-size:9.5px;color:#31566d;background:#edf3f6;border:1px solid #d6e2e8;border-radius:5px;padding:6px;margin-bottom:6px}.polyRow{font-size:10px;padding:5px;border-bottom:1px solid #edf0f2}.polyRow label{cursor:pointer}.catSearch{margin:2px 0 6px}.categoryList{max-height:350px;overflow:auto}
</style></head>
<body>
<div id="graph"></div>
<div id="left">
<h2>废塑料–FCC 知识图谱</h2>
<div class="small">共享 KG 三模块：M1 原料认知 → M2 技术路线 → M3 反应条件—产品/性能。全部模块使用自由 3D 力导向布局；右键节点可固定，节点颜色按实体类型区分。</div>

<div class="sec"><div class="st">① 选择问题模块</div><div id="modules"></div></div>
<div id="routeSec" class="sec" style="display:none"><div id="routeTitle" class="st">路线筛选（论文级 metadata）</div><div id="routeButtons" class="routeGrid"></div><div id="routeHint" class="routeHint"></div></div>
<div class="sec"><div class="st">② 本模块的关系结构</div><div id="relSummary" class="small">请选择模块。</div></div>
<div class="sec"><div class="st">③ 点开实体类型，选择具体实体</div><div id="types"></div></div>
<div id="selSec" class="sec" style="display:none"><div class="st">已选实体</div><div id="chips" class="chips"></div></div>
<div class="actions"><button id="build">生成关系 KG</button><button id="reset">重置</button><button id="center">居中显示</button></div>
<div id="status">请选择模块。</div>
</div>

<div id="right"><button id="detailClose" type="button" aria-label="关闭详情">×</button><div id="info"></div></div>
<div id="m1Story">
  <div class="storyBox s1">原料身份与组成</div>
  <div class="storyArrow">→</div>
  <div class="storyBox s2">原料性质</div>
  <div class="storyArrow">→</div>
  <div class="storyBox s3">反应倾向</div>
</div>
<div id="m2Story">
  <div class="storyBox s1">加工路线</div>
  <div class="storyArrow">→</div>
  <div class="storyBox s2">进料准备</div>
  <div class="storyArrow">→</div>
  <div class="storyBox s3">反应体系</div>
  <div class="storyArrow">→</div>
  <div class="storyBox em3">产品 / R3 协同</div>
</div>
<div id="m3Story">
  <div class="storyBox em2">路线与反应体系</div>
  <div class="storyArrow">→</div>
  <div class="storyBox em3">操作条件</div>
  <div class="storyArrow">→</div>
  <div class="storyBox s3">产品与性能</div>
</div>
<div id="entityStory">
  <div class="storyBox em1">M1 原料性质 / 反应倾向</div>
  <div class="storyArrow">→</div>
  <div class="storyBox em2">M2 FCC 技术路线</div>
  <div class="storyArrow">→</div>
  <div class="storyBox em3">M3 反应条件 / 产品性能</div>
</div>
<div id="legend"></div>

<script>
let CFG=null,G=null,active=new Set(),selected=new Map(),COL={},entityStoryMode=false,routeFilter="ALL";
const PAL=["#0057B8","#167A3F","#6A1B9A","#D55E00","#C62828","#5D4037","#AD1457","#455A64","#00796B","#3949AB","#00838F","#558B2F","#E65100","#7B1FA2","#37474F"];
const esc=v=>String(v??"").replaceAll("&","&amp;").replaceAll("<","&lt;").replaceAll(">","&gt;");
const idof=x=>(x&&typeof x==="object")?x.id:x;

function initGraph(){
 G=ForceGraph3D()(document.getElementById("graph")).backgroundColor("#fff").showNavInfo(false)
 .nodeThreeObject(n=>{
   let g=new THREE.Group(),s=Math.min(15,5+Math.log2(Math.max(1,+n.paper_count||1))*1.4);
   if(n.is_seed) s=Math.min(23,s*1.55);
   // Node colour is ALWAYS determined by entity type. Module/story layers never override it.
   const entityColor=COL[n.type]||"#555";
   g.add(new THREE.Mesh(new THREE.SphereGeometry(s,18,18),new THREE.MeshPhongMaterial({color:entityColor,opacity:n.is_seed?.98:.85,transparent:true})));
   let t=new SpriteText(n.name);t.color="#111";t.textHeight=4.2;t.position.y=s+4;t.backgroundColor="rgba(255,255,255,.91)";t.padding=1;g.add(t);return g;
 })
 .nodeLabel(n=>`<div style="color:#000;background:rgba(255,255,255,.96);padding:4px 6px;border-radius:4px"><b>${esc(n.name)}</b><br>${esc(n.type)}${n.controlled_category_zh?`<br>受控类别: ${esc(n.controlled_category_zh)}`:""}</div>`)
 .linkThreeObject(l=>{let t=new SpriteText(l.relation);t.color="#202020";t.textHeight=3;t.backgroundColor="rgba(255,255,255,.9)";t.padding=.6;return t})
 .linkThreeObjectExtend(true)
 .linkPositionUpdate((s,{start,end})=>{s.position.x=(start.x+end.x)/2;s.position.y=(start.y+end.y)/2;s.position.z=(start.z+end.z)/2})
 .linkColor(l=>l.edge_origin==="DERIVED_NORMALIZED_TENDENCY"?"rgba(100,82,120,.58)":"rgba(60,60,60,.38)")
 .linkDirectionalArrowLength(4).linkDirectionalArrowRelPos(.9)
 .onNodeClick(showNode).onLinkClick(showEdge)
 .onNodeRightClick(n=>{
   // Right-click fixes the node at its current 3D position.
   n.fx=n.x;n.fy=n.y;n.fz=n.z;
   G.d3ReheatSimulation();
   status.textContent=`已固定节点：${n.name}。重新生成图谱可恢复自由布局。`;
 });
 G.d3Force("charge").strength(-125);
 G.d3Force("link").distance(120).strength(.08);
 // Keep the original free 3D interaction: rotate, zoom and pan the whole graph.
 if(G.controls()){G.controls().enableRotate=true;G.controls().enableZoom=true;G.controls().enablePan=true;}
 document.getElementById("graph").addEventListener("contextmenu",e=>e.preventDefault());
 G.scene().add(new THREE.AmbientLight(0xffffff,1));
}

function currentRouteOptions(){
 if(active.size===1 && active.has("M03")) return CFG.m3_route_options||[];
 return CFG.m2_route_options||[];
}

function currentRouteOption(){
 return currentRouteOptions().find(x=>x.value===routeFilter)||null;
}

function updateRouteSelector(){
 const sec=document.getElementById("routeSec"),box=document.getElementById("routeButtons"),hint=document.getElementById("routeHint"),title=document.getElementById("routeTitle");
 const m2Only=(active.size===1 && active.has("M02"));
 const m3Only=(active.size===1 && active.has("M03"));
 if(!m2Only&&!m3Only){sec.style.display="none";routeFilter="ALL";return}
 sec.style.display="block";box.innerHTML="";
 if(!CFG.m2_route_ready){
   hint.textContent="尚未生成 route provenance layer。请先运行 04_16_build_M2_route_layer_shared_KG_v3_1.py。";
   return;
 }
 const opts=m3Only?(CFG.m3_route_options||[]):(CFG.m2_route_options||[]);
 const valid=new Set(opts.map(x=>x.value));
 if(!valid.has(routeFilter)) routeFilter=m3Only?"TARGET":"ALL";
 title.textContent=m3Only?"M3 路线筛选（默认 R1+R2+R3）":"M2 路线筛选（论文级 metadata）";
 opts.forEach(o=>{
   let b=document.createElement("button");b.className="routeBtn"+(routeFilter===o.value?" active":"");
   b.innerHTML=`<b>${esc(o.short)}</b> ${esc(o.label)} <span class="count">${Number(o.paper_count||0).toLocaleString()}</span>`;
   b.onclick=()=>{
     const hadGraph=!!(G && G.graphData && (G.graphData().nodes||[]).length);
     routeFilter=o.value;
     selected.clear();
     // Everything below the route selector is regenerated from the route-filtered layer:
     // relation signatures, entity-type counts, entity lists, selected entities and graph.
     updateRouteSelector();renderSchema();renderTypes();updateChips();clearGraph();
     if(hadGraph)buildGraph();
   };
   box.appendChild(b);
 });
 hint.textContent=m3Only
   ?"M3 默认仅统计 R1/R2/R3 三类明确 FCC 路线文献；RELATED 不并入反应条件—产品分布统计。"
   :"R1/R2/R3 为三类技术路线；R4 = RELATED（其他相关研究）。这里只筛选同一套共享 KG 的论文证据，不建立新的路线实体。";
}

function renderModules(){
 modules.innerHTML="";
 CFG.modules.forEach(m=>{
   let b=document.createElement("button");b.className="module";b.dataset.id=m.id;
   b.innerHTML=`<b>${m.id}</b> ${esc(m.label)}<div class="small">${esc(m.question)}</div>`;
   b.onclick=()=>{
     active.has(m.id)?active.delete(m.id):active.add(m.id);
     b.classList.toggle("active",active.has(m.id));
     selected.clear();updateRouteSelector();renderSchema();renderTypes();updateChips();clearGraph();updateStoryMode()
   };
   modules.appendChild(b);
 });
}

function updateStoryMode(){
 const m1=document.getElementById("m1Story");
 const m2=document.getElementById("m2Story");
 const m3=document.getElementById("m3Story");
 const all=document.getElementById("entityStory");
 const m2Only=(active.size===1 && active.has("M02"));
 const m3Only=(active.size===1 && active.has("M03"));
 const selectedStory=(selected.size>0 && !m2Only && !m3Only);
 m1.style.display=(!selectedStory && active.size===1 && active.has("M01"))?"flex":"none";
 m2.style.display=m2Only?"flex":"none";
 m3.style.display=m3Only?"flex":"none";
 all.style.display=selectedStory?"flex":"none";
}

function renderSchema(){
 if(!active.size){relSummary.innerHTML="请选择模块。";return}
 let html="";
 CFG.modules.filter(m=>active.has(m.id)).forEach(m=>{
   html+=`<div style="font-weight:700;margin-top:4px">${m.id} ${esc(m.label)}</div>`;
   if(m.note)html+=`<div class="note">${esc(m.note)}</div>`;
   let sigs=m.relation_signatures||[];
   if(active.size===1 && (m.id==="M02"||m.id==="M03")){
     const ro=currentRouteOption();
     if(ro && Array.isArray(ro.relation_signatures))sigs=ro.relation_signatures;
     html+=`<div class="note">当前路线范围：${esc(ro?ro.short+" "+ro.label:"ALL")}</div>`;
   }
   if(!sigs.length){
     html+=`<div class="relrow">当前路线范围下没有符合本模块结构的 canonical edge。</div>`;
     return;
   }
   sigs.forEach(r=>html+=`<div class="relrow"><span class="relname">${esc(r.source_type)} —[${esc(r.relation)}]→ ${esc(r.target_type)}</span> · ${Number(r.canonical_edge_count).toLocaleString()} edges</div>`);
 });
 relSummary.innerHTML=html;
}

function unionTypes(){
 let out=[],seen=new Set();
 CFG.modules.filter(m=>active.has(m.id)).forEach(m=>m.types.forEach(t=>{if(!seen.has(t)){seen.add(t);out.push(t)}}));
 return out;
}

function typeCount(type){
 if(active.size===1 && active.has("M01")){
   if((CFG.controlled_types||[]).includes(type))return Number(CFG.controlled_type_counts[type]||0);
   return Number(CFG.m1_type_counts[type]||0);
 }
 // In M2/M3 the number beside each entity type follows the selected route scope.
 if(active.size===1 && (active.has("M02")||active.has("M03"))){
   const ro=currentRouteOption();
   if(ro && ro.type_counts)return Number(ro.type_counts[type]||0);
 }
 return Number(CFG.type_counts[type]||0);
}

function isControlledType(type){
 return active.size===1 && active.has("M01") && (CFG.controlled_types||[]).includes(type);
}

function addTypeDetail(type,parent){
 let d=document.createElement("details");
 const controlled=isControlledType(type);
 const suffix=controlled?" 类":"";
 d.innerHTML=`<summary><span class="dot" style="background:${COL[type]||'#555'}"></span>${esc(CFG.type_zh[type]||type)} / ${esc(type)} (${typeCount(type).toLocaleString()}${suffix})</summary><div class="body">${controlled?'<div class="controlledBanner">先选择受控类别，再选择具体 canonical entity。类别层仅用于浏览与筛选，不改变底层 KG canonical ID。</div>':''}<input class="q" placeholder="${controlled?'搜索类别或原始实体':'搜索具体实体'}"><div class="list"><div class="small">展开后加载...</div></div></div>`;
 let inp=d.querySelector(".q"),list=d.querySelector(".list"),timer=null;
 d.ontoggle=()=>{if(d.open&&!d.dataset.loaded){controlled?loadCategories(type,inp,list,d):loadEntities(type,inp,list,d)}};
 inp.oninput=()=>{clearTimeout(timer);timer=setTimeout(()=>controlled?loadCategories(type,inp,list,d):loadEntities(type,inp,list,d),250)};
 parent.appendChild(d);
}

async function loadCategories(type,inp,list,d){
 let p=new URLSearchParams({modules:[...active].join(","),type,q:inp.value});
 let cats=await (await fetch("/api/categories?"+p)).json();
 list.innerHTML="";list.classList.add("categoryList");
 if(!cats.length){list.innerHTML='<div class="small">没有匹配的受控类别。</div>';d.dataset.loaded="1";return}
 cats.forEach(c=>{
   // Polymer84 category == final canonical entity, so it can be selected directly.
   if(type==="Polymer"){
     let r=document.createElement("div");r.className="polyRow";
     let display=c.name_zh||c.name_en||c.code;
     r.innerHTML=`<label><input type="checkbox" value="${c.canonical_id}"> <b>${esc(display)}</b> <span class="small">${c.name_en&&c.name_en!==display?`/ ${esc(c.name_en)} `:""}· Rank ${Number(c.rank||0)} · ${Number(c.occurrence_count||0).toLocaleString()} occurrences</span></label>`;
     let cb=r.querySelector("input");cb.checked=selected.has(c.canonical_id);
     cb.onchange=()=>{cb.checked?selected.set(c.canonical_id,{canonical_id:c.canonical_id,type:"Polymer",canonical_name_en:display,display_name:display,controlled_category_zh:c.name_zh,controlled_category_en:c.name_en}):selected.delete(c.canonical_id);updateChips()};
     list.appendChild(r);return;
   }

   let cd=document.createElement("details");cd.className="catDetail";
   const display=c.name_zh||c.name_en||c.code;
   cd.innerHTML=`<summary><b>${esc(display)}</b> <span class="catMeta">${esc(c.code)} · ${Number(c.member_count||0).toLocaleString()} canonical nodes · ${Number(c.occurrence_count||0).toLocaleString()} occurrences</span></summary><div class="body"><input class="q catSearch" placeholder="在此类别中搜索具体实体"><div class="list"><div class="small">展开后加载...</div></div></div>`;
   let cinp=cd.querySelector(".catSearch"),clist=cd.querySelector(".list"),timer=null;
   cd.ontoggle=()=>{if(cd.open&&!cd.dataset.loaded)loadEntities(type,cinp,clist,cd,c.code)};
   cinp.oninput=()=>{clearTimeout(timer);timer=setTimeout(()=>loadEntities(type,cinp,clist,cd,c.code),250)};
   list.appendChild(cd);
 });
 d.dataset.loaded="1";
}

function renderTypes(){
 types.innerHTML="";
 if(!active.size){types.innerHTML='<div class="small">请选择模块。</div>';return}

 // When M01 is viewed alone, preserve the agreed scientific hierarchy in the selector.
 if(active.size===1 && active.has("M01")){
   const m=CFG.modules.find(x=>x.id==="M01");
   m.groups.forEach(g=>{
     let gt=document.createElement("div");gt.className="groupTitle";gt.textContent=g.label;types.appendChild(gt);
     g.types.forEach(type=>addTypeDetail(type,types));
   });
   return;
 }
 // M02/M03 use their approved scientific hierarchy; group labels are display-only, not KG nodes.
 if(active.size===1 && (active.has("M02")||active.has("M03"))){
   const mid=active.has("M02")?"M02":"M03";
   const m=CFG.modules.find(x=>x.id===mid);
   (m.groups||[]).forEach(g=>{
     let gt=document.createElement("div");gt.className="groupTitle";gt.textContent=g.label;types.appendChild(gt);
     g.types.forEach(type=>addTypeDetail(type,types));
   });
   return;
 }
 unionTypes().forEach(type=>addTypeDetail(type,types));
}

async function loadEntities(type,inp,list,d,category=""){
 let p=new URLSearchParams({modules:[...active].join(","),type,q:inp.value,category,route:routeFilter});
 let rs=await (await fetch("/api/entities?"+p)).json();list.innerHTML="";
 rs.forEach(n=>{
   let display=n.display_name||n.canonical_name_en;
   let r=document.createElement("div");r.className="row";
   let cat=n.controlled_category_zh?` · ${esc(n.controlled_category_zh)}`:"";
   r.innerHTML=`<label><input type="checkbox" value="${n.canonical_id}"> ${esc(display)} <span class="small">(${Number(n.paper_count||0).toLocaleString()} papers${cat})</span></label>`;
   let cb=r.querySelector("input");cb.checked=selected.has(n.canonical_id);
   cb.onchange=()=>{cb.checked?selected.set(n.canonical_id,{...n,canonical_name_en:display}):selected.delete(n.canonical_id);updateChips()};
   list.appendChild(r);
 });
 d.dataset.loaded="1";
}

function updateChips(){
 selSec.style.display=selected.size?"block":"none";chips.innerHTML="";
 updateStoryMode();
 selected.forEach((n,id)=>{
   let c=document.createElement("span");c.className="chip";
   c.innerHTML=`${esc(n.canonical_name_en)} <button>×</button>`;
   c.querySelector("button").onclick=()=>{
     selected.delete(id);updateChips();
     document.querySelectorAll('input[type=checkbox]').forEach(x=>{if(x.value===id)x.checked=false})
   };
   chips.appendChild(c)
 });
}

async function buildGraph(){
 if(!active.size){status.textContent="请先选择模块。";return}

 entityStoryMode=selected.size>0 && !(active.size===1 && (active.has("M02")||active.has("M03")));

 let p=new URLSearchParams({
   modules:[...active].join(","),
   seeds:[...selected.keys()].join(","),
   max_edges:entityStoryMode?8000:1200,
   entity_story:entityStoryMode?"1":"0",
   route:routeFilter
 });

 let data=await (await fetch("/api/graph?"+p)).json();

 // Free force-directed 3D layout for every module. Story/group labels are explanatory only
 // and never pin nodes to columns. The only fixed nodes are those explicitly right-clicked
 // by the user after the graph is rendered.
 data.nodes.forEach(n=>{
   delete n.fx;delete n.fy;delete n.fz;
 });

 G.graphData({nodes:data.nodes,links:data.links});G.d3ReheatSimulation();
 setTimeout(()=>G.zoomToFit(entityStoryMode?1000:800,70),550);

 if(entityStoryMode){
   const c=data.story_counts||{};
   const names=[...selected.values()].map(x=>x.canonical_name_en).join(", ");
   status.textContent=`${names} 跨模块故事线：M1 ${c.M01||0}｜M2 ${c.M02||0}｜M3 ${c.M03||0}｜共 ${data.links.length} 条直接关系`;
 }else{
   if(active.size===1 && (active.has("M02")||active.has("M03"))){
     const ro=currentRouteOption();
     const mid=active.has("M02")?"M2":"M3";
     status.textContent=`${mid} · ${ro?ro.short+" "+ro.label:"ALL"}：显示 ${data.nodes.length} 个节点 / ${data.links.length} 条关系。`;
   }else{
     status.textContent=`显示 ${data.nodes.length} 个节点 / ${data.links.length} 条关系。`;
   }
 }

 buildLegend(data.nodes);
 updateStoryMode();
}

function buildLegend(ns){
 legend.innerHTML="";
 const typesShown=[...new Set((ns||[]).map(n=>n.type).filter(Boolean))].sort();
 typesShown.forEach(t=>{
   legend.innerHTML+=`<span><span class="dot" style="background:${COL[t]||'#555'}"></span>${esc(CFG.type_zh[t]||t)}</span>`;
 });
 if(active.size===1 && (active.has("M02")||active.has("M03"))){
   const ro=currentRouteOption();
   legend.innerHTML+=`<span class="small"><b>Route:</b> ${esc(ro?ro.short+" "+ro.label:"ALL")} · 节点颜色按实体类型区分</span>`;
 }else{
   legend.innerHTML+=`<span class="small">节点颜色按实体类型区分</span>`;
 }
}

function clearGraph(){
 if(G)G.graphData({nodes:[],links:[]});legend.innerHTML="";
 status.textContent=active.size?"模块已选择，请选择实体或直接生成模块高支持关系。":"请选择模块。";
 right.style.display="none"
}

function evidenceYear(e){
 const m=String(e?.year??"").match(/(?:19|20)\d{2}/);
 return m?Number(m[0]):0;
}
function evCards(rs){
 const rows=[...(rs||[])].sort((a,b)=>evidenceYear(b)-evidenceYear(a));
 return rows.slice(0,80).map(e=>{
   let raw=e.raw_tendency_original?`<div class="small"><b>Raw tendency:</b> ${esc(e.raw_tendency_original)}</div>`:"";
   const y=evidenceYear(e);
   return `<div class="card"><div class="small"><b>Year:</b> ${y||"—"}${e.title||e.filename?` · ${esc(e.title||e.filename||"")}`:""}${e.page?` · p.${esc(e.page)}`:""}${e.case_id?` · case ${esc(e.case_id)}`:""}</div><div class="evidence">${esc(e.evidence||"")}</div>${raw}</div>`;
 }).join("")||'<div class="small">No evidence.</div>'
}

async function showNode(n){
 right.style.display="block";
 let p=new URLSearchParams({modules:[...active].join(","),story_all:entityStoryMode?"1":"0",route:routeFilter});
 let x=await (await fetch("/api/node/"+encodeURIComponent(idof(n))+"?"+p)).json(),d=x.node;
 let title=d.display_name||d.canonical_name_en;
 let controlled=d.controlled_category_zh?`<br><b>受控类别:</b> ${esc(d.controlled_category_zh)}${d.controlled_category_code?` <span class="origin">${esc(d.controlled_category_code)}</span>`:""}`:"";
 info.innerHTML=`<h2>${esc(title)}</h2><div class="small"><b>Type:</b> ${esc(d.type)}${controlled}<br><b>Papers:</b> ${Number(d.paper_count||0).toLocaleString()}<br><b>Cases:</b> ${Number(d.case_count||0).toLocaleString()}<br><b>Source:</b> ${esc(x.data_source||"")}</div><h3>Evidence <span class="small">（按年份从新到旧）</span></h3>${evCards(x.evidence)}`
}

async function showEdge(l){
 right.style.display="block";
 let p=new URLSearchParams({modules:[...active].join(","),story_all:entityStoryMode?"1":"0",route:routeFilter});
 let x=await (await fetch("/api/edge/"+encodeURIComponent(l.edge_id)+"?"+p)).json(),e=x.edge;
 let tn=e.target_display_name_zh||e.target_canonical_name_en;
 info.innerHTML=`<h2>${esc(e.relation)}</h2><div class="small">${esc(e.source_canonical_name_en)} → ${esc(tn)}<br>${Number(e.paper_count||0).toLocaleString()} papers${e.story_module?` · <b>${esc(e.story_module)}</b>`:""}<br><b>Source:</b> ${esc(x.data_source||"")}${e.edge_origin?` · ${esc(e.edge_origin)}`:""}</div><h3>Evidence</h3>${evCards(x.evidence)}`
}

async function load(){
 CFG=await (await fetch("/api/config")).json();
 const allTypes=new Set([...Object.keys(CFG.type_counts),...Object.keys(CFG.m1_type_counts)]);
 [...allTypes].forEach((t,i)=>COL[t]=PAL[i%PAL.length]);
 initGraph();renderModules();updateRouteSelector();renderTypes();updateStoryMode();
}

build.onclick=buildGraph;
reset.onclick=()=>{
 entityStoryMode=false;active.clear();selected.clear();routeFilter="ALL";
 document.querySelectorAll(".module").forEach(x=>x.classList.remove("active"));
 updateRouteSelector();renderSchema();renderTypes();updateChips();clearGraph();updateStoryMode()
};
center.onclick=()=>G.zoomToFit(650,60);
document.getElementById("detailClose").addEventListener("click",()=>{right.style.display="none";});
load();
</script></body></html>
"""


@app.get("/")
def home():
    return render_template_string(HTML)


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--port", type=int, default=8051)
    p.add_argument("--no-browser", action="store_true")
    args = p.parse_args()

    url = f"http://127.0.0.1:{args.port}"
    print("=" * 96)
    print("WASTE PLASTIC–FCC KG — M1 + M2 ROUTES + M3 CONDITION–PRODUCT v05_13")
    print(f"Core database : {DB}")
    print(f"M1 layer      : {M1_DIR}  [UNCHANGED]")
    print(f"M1 taxonomy   : {CONTROLLED_TAXONOMY_DIR}")
    print(f"Controlled    : WasteSource {CONTROLLED_CATEGORY_COUNTS.get('WasteSource',0)} | WastePlastic {CONTROLLED_CATEGORY_COUNTS.get('WastePlastic',0)} | Polymer {CONTROLLED_CATEGORY_COUNTS.get('Polymer',0)}")
    print(f"Figure-2 sync : DERIVED_FROM occurrence {sum(as_int(e.get('occurrence_count')) for e in M1_CORE_IDENTITY_EDGES if e.get('relation') == 'DERIVED_FROM')} | CONTAINS occurrence {sum(as_int(e.get('occurrence_count')) for e in M1_CORE_IDENTITY_EDGES if e.get('relation') == 'CONTAINS')}")
    print(f"Route DB      : {M2_ROUTE_DB}  [{'READY' if M2_ROUTE_READY else 'MISSING'}]")
    if not M2_ROUTE_READY:
        print("Route note    : run 04_16_build_M2_route_layer_shared_KG_v3_1.py first")
    print(f"Open          : {url}")
    print("=" * 96)

    if not args.no_browser:
        threading.Timer(1.2, lambda: webbrowser.open(url)).start()

    app.run(
        host="127.0.0.1",
        port=args.port,
        debug=False,
        use_reloader=False,
    )


if __name__ == "__main__":
    main()
