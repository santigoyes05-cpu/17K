"""
Merchant Name Deduplication — Star Clustering (v3)
===================================================
Adapted for datasets with columns:
    canonical_name, adquirente, mun_name_final, apv_ma_2025, trx_ma_2025,
    TAMANO_EMPRESA_ASSIGNED, industry, industria

Uses blocked fuzzy matching + greedy star clustering (avoids transitive
chain explosions like "drogueria A" ↔ "drogueria B" ↔ "drogueria C").

Produces three outputs:
- strong_matches.txt: clusters where all links share the same adquirente
- weak_matches.txt: clusters where any link crosses different adquirentes
- corrected_<input_filename>: consolidated dataset with one row per cluster

Usage:
    1. Set the INPUT_FILE variable below to your dataset path.
    2. Run:  python deduplicate_merchants_v3.py

Supported input formats: .csv, .xlsx, .xls
"""

import logging
import os
import re
import sys
import unicodedata
from collections import Counter

import numpy as np
import pandas as pd
from joblib import Parallel, delayed
from rapidfuzz import fuzz, process
from tqdm import tqdm

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------
logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Input file — change this path to point to your dataset
# ---------------------------------------------------------------------------
INPUT_FILE = "datos.xlsx"

# ---------------------------------------------------------------------------
# Parameters
# ---------------------------------------------------------------------------
MATCH_THRESHOLD = 90
BLOCK_PREFIX_LEN = 4
MAX_BLOCK_SIZE = 12_000
MIN_NAME_LEN_FOR_FUZZY = 6
N_JOBS = 4
CLUSTER_SEPARATOR = "--------"


# ---------------------------------------------------------------------------
# Text cleaning
# ---------------------------------------------------------------------------
def build_city_set(df: pd.DataFrame) -> set:
    cities = set()
    if "mun_name_final" in df.columns:
        for c in df["mun_name_final"].dropna().unique():
            cities.add(str(c).strip().lower())
    return cities


def clean_merchant_name(name: str) -> str:
    if pd.isna(name) or not isinstance(name, str):
        return ""
    name = name.lower().strip()

    name = unicodedata.normalize("NFD", name)
    name = "".join(ch for ch in name if unicodedata.category(ch) != "Mn")

    legal_suffixes = [
        r"\bs\.?a\.?s\.?\b", r"\bs\.?a\.?\b", r"\bltda\.?\b", r"\blimitada\b",
        r"\be\.?u\.?\b", r"\bsociedad anonima\b", r"\bsociedad por acciones simplificada\b",
        r"\by cia\b", r"\b& cia\b", r"\bcia\b", r"\bcompania\b", r"\bcompañia\b",
        r"\binc\.?\b", r"\bcorp\.?\b", r"\bcorporation\b", r"\bllc\b",
    ]
    for suffix in legal_suffixes:
        name = re.sub(suffix, "", name, flags=re.IGNORECASE)

    name = re.sub(r"[^a-z0-9\s]", " ", name)
    name = re.sub(r"\s+", " ", name).strip()
    return name


def strip_cities(name: str, cities: set) -> str:
    if not isinstance(name, str) or not name:
        return name
    tokens = name.split()
    filtered = [t for t in tokens if t.lower() not in cities]
    return " ".join(filtered) if filtered else name


# ---------------------------------------------------------------------------
# Blocking
# ---------------------------------------------------------------------------
def build_blocks(unique_names: list[str], name_industrias: dict[str, str]) -> dict[str, list[int]]:
    """Group names by (prefix, industria) blocks."""
    blocks: dict[str, list[int]] = {}
    for idx, name in enumerate(unique_names):
        prefix = name[:BLOCK_PREFIX_LEN] if len(name) >= BLOCK_PREFIX_LEN else name
        industria = name_industrias.get(name, "")
        key = f"{prefix}|{industria}"
        blocks.setdefault(key, []).append(idx)
    return blocks


# ---------------------------------------------------------------------------
# Star clustering
# ---------------------------------------------------------------------------
def cluster_block_star(
    block_indices: list[int],
    unique_names: list[str],
    name_freq: dict[str, int],
    name_acquirers: dict[str, set],
    threshold: int,
) -> list[tuple[int, int, bool]]:
    """Star-cluster a block. Returns (centre_idx, member_idx, is_strong)."""
    block_names = [unique_names[i] for i in block_indices]
    n = len(block_names)

    if n <= 1:
        return []

    freq_order = sorted(range(n), key=lambda k: -name_freq.get(block_names[k], 0))

    score_matrix = process.cdist(
        block_names,
        block_names,
        scorer=fuzz.token_sort_ratio,
        score_cutoff=threshold,
        workers=1,
        dtype=np.uint8,
    )

    assigned = set()
    assignments: list[tuple[int, int, bool]] = []

    for local_centre in freq_order:
        if local_centre in assigned:
            continue

        centre_name = block_names[local_centre]
        assigned.add(local_centre)

        if len(centre_name) < MIN_NAME_LEN_FOR_FUZZY:
            continue

        centre_acq = name_acquirers.get(centre_name, set())

        for local_j in range(n):
            if local_j in assigned:
                continue
            if score_matrix[local_centre, local_j] < threshold:
                continue

            candidate_name = block_names[local_j]
            if len(candidate_name) < MIN_NAME_LEN_FOR_FUZZY:
                if candidate_name != centre_name:
                    continue

            candidate_acq = name_acquirers.get(candidate_name, set())
            is_strong = bool(centre_acq & candidate_acq)

            assignments.append(
                (block_indices[local_centre], block_indices[local_j], is_strong)
            )
            assigned.add(local_j)

    return assignments


def build_star_clusters(
    n_names: int,
    assignments: list[tuple[int, int, bool]],
) -> tuple[np.ndarray, dict[int, bool]]:
    """Convert star assignments into cluster labels + strength tracking.

    Returns (labels_array, {cluster_id: all_strong_bool}).
    """
    labels = np.arange(n_names)
    cluster_strong: dict[int, bool] = {}

    for centre_idx, member_idx, is_strong in assignments:
        labels[member_idx] = labels[centre_idx]
        cid = int(labels[centre_idx])
        if cid not in cluster_strong:
            cluster_strong[cid] = is_strong
        elif not is_strong:
            cluster_strong[cid] = False

    unique_labels, inverse = np.unique(labels, return_inverse=True)

    old_to_new = {old: new for new, old in enumerate(unique_labels)}
    new_cluster_strong: dict[int, bool] = {}
    for old_cid, strong in cluster_strong.items():
        new_cid = old_to_new[old_cid]
        if new_cid not in new_cluster_strong:
            new_cluster_strong[new_cid] = strong
        elif not strong:
            new_cluster_strong[new_cid] = False

    return inverse, new_cluster_strong


# ---------------------------------------------------------------------------
# Canonical name selection — longest name stripped of cities
# ---------------------------------------------------------------------------
def pick_canonical_names(
    unique_names: list[str],
    cluster_labels: np.ndarray,
    cities: set,
) -> dict[int, str]:
    clusters: dict[int, list[str]] = {}
    for idx, cid in enumerate(cluster_labels):
        clusters.setdefault(int(cid), []).append(unique_names[idx])

    canonical: dict[int, str] = {}
    for cid, members in clusters.items():
        stripped = [(strip_cities(m, cities), m) for m in members]
        stripped.sort(key=lambda x: -len(x[0]))
        canonical[cid] = stripped[0][0]
    return canonical


# ---------------------------------------------------------------------------
# I/O helpers
# ---------------------------------------------------------------------------
def load_data(path: str) -> pd.DataFrame:
    ext = os.path.splitext(path)[1].lower()
    if ext == ".csv":
        for enc in ("utf-8", "latin-1", "cp1252"):
            try:
                return pd.read_csv(path, encoding=enc, dtype=str)
            except (UnicodeDecodeError, Exception):
                continue
        raise ValueError(f"Could not read CSV with any common encoding: {path}")
    elif ext in (".xlsx", ".xls"):
        return pd.read_excel(path, dtype=str)
    else:
        raise ValueError(f"Unsupported file format: {ext}. Use .csv, .xlsx, or .xls")


def write_txt(path: str, groups: list[list[str]]):
    with open(path, "w", encoding="utf-8") as f:
        for idx, group in enumerate(groups):
            for name in group:
                f.write(name + "\n")
            if idx < len(groups) - 1:
                f.write(CLUSTER_SEPARATOR + "\n")


def write_corrected_db(corrected: pd.DataFrame, input_path: str) -> str:
    out_dir = os.path.dirname(input_path) or "."
    base = os.path.basename(input_path)
    out_path = os.path.join(out_dir, f"corrected_{base}")
    ext = os.path.splitext(input_path)[1].lower()
    if ext == ".csv":
        corrected.to_csv(out_path, index=False, encoding="utf-8")
    else:
        corrected.to_excel(out_path, index=False)
    return out_path


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
_NUMERIC_COLS = {"apv_ma_2025", "trx_ma_2025"}


def run(input_path: str):
    logger.info("=== Merchant Deduplication (Star Clustering) ===")
    logger.info(f"Input file: {input_path}")

    df = load_data(input_path)
    n_rows = len(df)
    logger.info(f"Loaded {n_rows:,} rows.")

    required = {"canonical_name", "industria", "adquirente"}
    missing = required - set(df.columns)
    if missing:
        sys.exit(f"Error: missing required columns: {missing}")

    cities = build_city_set(df)
    logger.info(f"Built city stopword set with {len(cities)} entries.")

    # ------------------------------------------------------------------
    # 1. Clean merchant names
    # ------------------------------------------------------------------
    logger.info("Cleaning merchant names...")
    df["cleaned_name"] = df["canonical_name"].apply(clean_merchant_name)

    empty_mask = df["cleaned_name"] == ""
    if empty_mask.any():
        logger.info(f"Rows with empty cleaned name (dropped): {empty_mask.sum():,}")
        df = df[~empty_mask].copy()

    # ------------------------------------------------------------------
    # 2. Build unique name list, frequency map, acquirer/industria maps
    # ------------------------------------------------------------------
    name_freq = df["cleaned_name"].value_counts().to_dict()
    unique_names = list(name_freq.keys())
    logger.info(f"Unique cleaned names: {len(unique_names):,}")

    # For each unique name, collect its industria (most common) and acquirers
    name_industrias: dict[str, str] = {}
    name_acquirers: dict[str, set] = {}

    for name in unique_names:
        subset = df[df["cleaned_name"] == name]
        ind_vals = subset["industria"].dropna().str.strip().str.upper()
        name_industrias[name] = ind_vals.mode().iloc[0] if len(ind_vals) > 0 else ""
        acq_vals = subset["adquirente"].dropna().str.strip().str.upper()
        name_acquirers[name] = set(acq_vals.unique())

    # ------------------------------------------------------------------
    # 3. Block by prefix + industria
    # ------------------------------------------------------------------
    logger.info(f"Building blocks (prefix={BLOCK_PREFIX_LEN}, grouped by industria)...")
    blocks = build_blocks(unique_names, name_industrias)
    block_sizes = [len(v) for v in blocks.values()]
    logger.info(f"Total blocks: {len(blocks):,}")
    logger.info(f"Block size — median: {sorted(block_sizes)[len(block_sizes)//2]}, "
                f"max: {max(block_sizes):,}")

    # ------------------------------------------------------------------
    # 4. Star clustering within blocks (parallel)
    # ------------------------------------------------------------------
    blocks_to_process = []
    skipped = 0
    singletons = 0

    for key, indices in blocks.items():
        if len(indices) == 1:
            singletons += 1
        elif len(indices) > MAX_BLOCK_SIZE:
            logger.warning(f'Block "{key}" has {len(indices):,} names — SKIPPED')
            skipped += 1
        else:
            blocks_to_process.append((key, indices))

    logger.info(f"Blocks to match: {len(blocks_to_process):,} "
                f"(singletons: {singletons:,}, skipped: {skipped:,})")
    logger.info(f"Starting parallel matching with {N_JOBS} workers...")

    results = Parallel(n_jobs=N_JOBS, backend="loky")(
        delayed(cluster_block_star)(
            indices, unique_names, name_freq, name_acquirers, MATCH_THRESHOLD
        )
        for _, indices in tqdm(blocks_to_process, desc="Matching blocks", unit="block")
    )

    all_assignments: list[tuple[int, int, bool]] = []
    for block_result in results:
        all_assignments.extend(block_result)

    logger.info(f"Total star assignments: {len(all_assignments):,}")

    # ------------------------------------------------------------------
    # 5. Build clusters with strength tracking
    # ------------------------------------------------------------------
    logger.info("Building clusters...")
    cluster_labels, cluster_strong = build_star_clusters(len(unique_names), all_assignments)
    n_clusters = len(set(cluster_labels))
    logger.info(f"Total clusters: {n_clusters:,}")

    cluster_counts = Counter(cluster_labels)
    multi = {cid: cnt for cid, cnt in cluster_counts.items() if cnt > 1}
    logger.info(f"Multi-name clusters: {len(multi):,}")

    # ------------------------------------------------------------------
    # 6. Canonical names — longest name stripped of cities
    # ------------------------------------------------------------------
    logger.info("Selecting canonical names...")
    canonical_map = pick_canonical_names(unique_names, cluster_labels, cities)

    name_to_cluster = {unique_names[i]: int(cluster_labels[i]) for i in range(len(unique_names))}
    name_to_canonical = {name: canonical_map[cid] for name, cid in name_to_cluster.items()}

    # ------------------------------------------------------------------
    # 7. Map back to original dataframe
    # ------------------------------------------------------------------
    logger.info("Mapping clusters to rows...")
    df["cluster_id"] = df["cleaned_name"].map(name_to_cluster)
    df["cluster_canonical"] = df["cleaned_name"].map(name_to_canonical)

    # ------------------------------------------------------------------
    # 8. Write strong/weak .txt files
    # ------------------------------------------------------------------
    strong_clusters: dict[int, set] = {}
    weak_clusters: dict[int, set] = {}

    for i, name in enumerate(unique_names):
        cid = int(cluster_labels[i])
        orig_names = set(
            df.loc[df["cleaned_name"] == name, "canonical_name"]
            .str.strip()
            .unique()
        )
        is_strong = cluster_strong.get(cid, True)
        if is_strong:
            strong_clusters.setdefault(cid, set()).update(orig_names)
        else:
            weak_clusters.setdefault(cid, set()).update(orig_names)

    strong_groups = [sorted(names) for names in strong_clusters.values() if len(names) > 1]
    weak_groups = [sorted(names) for names in weak_clusters.values() if len(names) > 1]
    strong_groups.sort(key=lambda g: g[0])
    weak_groups.sort(key=lambda g: g[0])

    out_dir = os.path.dirname(input_path) or "."

    strong_path = os.path.join(out_dir, "strong_matches.txt")
    write_txt(strong_path, strong_groups)
    logger.info(f"Strong matches: {len(strong_groups)} clusters -> {strong_path}")

    weak_path = os.path.join(out_dir, "weak_matches.txt")
    write_txt(weak_path, weak_groups)
    logger.info(f"Weak matches:   {len(weak_groups)} clusters -> {weak_path}")

    # ------------------------------------------------------------------
    # 9. Build corrected (consolidated) database
    # ------------------------------------------------------------------
    logger.info("Building corrected database...")
    numeric_cols = [c for c in _NUMERIC_COLS if c in df.columns]

    groups: dict[int, list[int]] = {}
    for i in range(len(df)):
        cid = df.iloc[i]["cluster_id"]
        if pd.notna(cid):
            groups.setdefault(int(cid), []).append(i)

    result_rows = []
    for cid, indices in groups.items():
        if len(indices) == 1:
            row = df.iloc[indices[0]].copy()
            orig = str(row.get("canonical_name", ""))
            row["canonical_name"] = strip_cities(orig, cities) or orig
            result_rows.append(row)
            continue

        sub = df.iloc[indices].copy()

        for col in numeric_cols:
            sub[col] = pd.to_numeric(sub[col], errors="coerce")

        if "apv_ma_2025" in sub.columns:
            dominant_idx = sub["apv_ma_2025"].idxmax()
        else:
            dominant_idx = sub.index[0]

        new_row = sub.loc[dominant_idx].copy()
        new_row["canonical_name"] = canonical_map.get(cid, new_row["canonical_name"])

        for col in numeric_cols:
            new_row[col] = sub[col].sum()

        result_rows.append(new_row)

    out_cols = [c for c in df.columns if c not in ("cleaned_name", "cluster_id", "cluster_canonical")]
    corrected = pd.DataFrame(result_rows, columns=df.columns)[out_cols]
    corrected.reset_index(drop=True, inplace=True)

    corrected_path = write_corrected_db(corrected, input_path)
    logger.info(f"Corrected DB: {n_rows} rows -> {len(corrected)} rows -> {corrected_path}")

    logger.info("=== Pipeline complete ===")


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    if not os.path.isfile(INPUT_FILE):
        sys.exit(
            f"Error: Input file not found: '{INPUT_FILE}'\n"
            f"Please set the INPUT_FILE variable at the top of the script "
            f"to a valid .csv, .xlsx, or .xls file path."
        )
    run(INPUT_FILE)
