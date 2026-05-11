"""
Merchant Name Deduplication Script (v2)
========================================
Adapted for datasets with columns:
    canonical_name, adquirente, mun_name_final, apv_ma_2025, trx_ma_2025,
    TAMANO_EMPRESA_ASSIGNED, industry, industria

Produces three outputs:
- strong_matches.txt: high-confidence clusters (similarity >= 0.78, same industria, same adquirente)
- weak_matches.txt: lower-confidence clusters (similarity 0.65–0.78, same industria, same adquirente)
- corrected_<input_filename>: consolidated dataset with one row per merchant group

Usage:
    1. Set the INPUT_FILE variable below to your dataset path.
    2. Run:  python deduplicate_merchants_v2.py

Supported input formats: .csv, .xlsx, .xls
"""

import sys
import os
import re
from difflib import SequenceMatcher

import pandas as pd


# ---------------------------------------------------------------------------
# Input file — change this path to point to your dataset
# ---------------------------------------------------------------------------
INPUT_FILE = "datos.xlsx"


# ---------------------------------------------------------------------------
# Parameters
# ---------------------------------------------------------------------------
LOOKBACK_WINDOW = 10
STRONG_THRESHOLD = 0.78
WEAK_THRESHOLD = 0.65
MIN_LENGTH_FOR_FUZZY = 5
CLUSTER_SEPARATOR = "--------"


# ---------------------------------------------------------------------------
# Union-Find with link-strength tracking
# ---------------------------------------------------------------------------
class UnionFind:
    def __init__(self, n):
        self.parent = list(range(n))
        self.rank = [0] * n
        self.all_strong = [True] * n

    def find(self, x):
        while self.parent[x] != x:
            self.parent[x] = self.parent[self.parent[x]]
            x = self.parent[x]
        return x

    def union(self, a, b, is_strong: bool):
        ra, rb = self.find(a), self.find(b)
        if ra == rb:
            if not is_strong:
                self.all_strong[ra] = False
            return
        if self.rank[ra] < self.rank[rb]:
            ra, rb = rb, ra
        self.parent[rb] = ra
        if self.rank[ra] == self.rank[rb]:
            self.rank[ra] += 1
        self.all_strong[ra] = self.all_strong[ra] and self.all_strong[rb] and is_strong

    def is_component_strong(self, x):
        return self.all_strong[self.find(x)]


# ---------------------------------------------------------------------------
# Text normalisation helpers
# ---------------------------------------------------------------------------
def build_city_set(df: pd.DataFrame) -> set:
    """Extract unique municipality names from mun_name_final to use as stopwords."""
    cities = set()
    if "mun_name_final" in df.columns:
        for c in df["mun_name_final"].dropna().unique():
            cities.add(str(c).strip().upper())
    return cities


_BUSINESS_SUFFIXES = {
    "SA", "SAS", "LTDA", "EU", "CIA", "CO", "INC", "CORP", "LLC",
    "NIT", "EAT", "ESP", "SUCURSAL",
}


def normalize(name: str, cities: set) -> str:
    if not isinstance(name, str):
        return ""
    text = name.upper()
    text = re.sub(r"[^\w\s]", " ", text)
    text = re.sub(r"\s+", " ", text).strip()

    tokens = text.split()
    filtered = []
    for tok in tokens:
        if tok in cities:
            continue
        if tok in _BUSINESS_SUFFIXES:
            continue
        if tok.isdigit():
            continue
        filtered.append(tok)
    return " ".join(filtered) if filtered else text


# ---------------------------------------------------------------------------
# Similarity computation
# ---------------------------------------------------------------------------
def compute_similarity(norm_a: str, norm_b: str) -> float:
    if not norm_a or not norm_b:
        return 0.0

    if norm_a == norm_b:
        return 1.0

    shorter, longer = (norm_a, norm_b) if len(norm_a) <= len(norm_b) else (norm_b, norm_a)

    if longer.startswith(shorter):
        prefix_ratio = len(shorter) / len(longer)
        if prefix_ratio >= 0.55:
            return max(prefix_ratio, 0.80)

    tokens_a = set(norm_a.split())
    tokens_b = set(norm_b.split())
    if tokens_a and tokens_b:
        jaccard = len(tokens_a & tokens_b) / len(tokens_a | tokens_b)
    else:
        jaccard = 0.0

    seq_ratio = SequenceMatcher(None, norm_a, norm_b).ratio()

    return max(jaccard, seq_ratio)


# ---------------------------------------------------------------------------
# Acquirer helpers
# ---------------------------------------------------------------------------
def parse_acquirer(value) -> set:
    if pd.isna(value) or value is None:
        return set()
    text = str(value).strip().upper()
    if not text:
        return set()
    parts = re.split(r"[;,|/]", text)
    return {p.strip() for p in parts if p.strip()}


# ---------------------------------------------------------------------------
# Main logic
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


def run(input_path: str):
    print(f"Loading data from: {input_path}")
    df = load_data(input_path)
    n = len(df)
    print(f"Loaded {n} rows.")

    required = {"canonical_name", "industria", "adquirente"}
    missing = required - set(df.columns)
    if missing:
        sys.exit(f"Error: missing required columns: {missing}")

    cities = build_city_set(df)
    print(f"Built city stopword set with {len(cities)} entries.")

    norm_names = []
    acquirers = []
    industrias = []

    for _, row in df.iterrows():
        norm_names.append(normalize(str(row.get("canonical_name", "")), cities))
        industrias.append(str(row.get("industria", "")).strip().upper())
        acquirers.append(parse_acquirer(row.get("adquirente")))

    # Union-Find clustering
    uf = UnionFind(n)
    matches_found = 0

    for i in range(n):
        start = max(0, i - LOOKBACK_WINDOW)
        for j in range(start, i):
            if industrias[i] != industrias[j]:
                continue

            sim = compute_similarity(norm_names[i], norm_names[j])

            shorter_len = min(len(norm_names[i]), len(norm_names[j]))
            if shorter_len < MIN_LENGTH_FOR_FUZZY:
                if sim < 0.95:
                    continue

            same_acquirer = bool(acquirers[i] & acquirers[j])

            if sim >= STRONG_THRESHOLD:
                if same_acquirer:
                    uf.union(i, j, is_strong=True)
                    matches_found += 1
                else:
                    # High similarity but different acquirer → weak match
                    uf.union(i, j, is_strong=False)
                    matches_found += 1
            elif sim >= WEAK_THRESHOLD:
                if same_acquirer:
                    uf.union(i, j, is_strong=False)
                    matches_found += 1

        if (i + 1) % 5000 == 0:
            print(f"  Processed {i + 1}/{n} rows...")

    print(f"Finished comparisons. Total union operations: {matches_found}")

    # Build clusters
    clusters_strong = {}
    clusters_weak = {}

    for i in range(n):
        root = uf.find(i)
        name_val = str(df.iloc[i].get("canonical_name", "")).strip()
        if not name_val:
            continue
        if uf.is_component_strong(root):
            clusters_strong.setdefault(root, set()).add(name_val)
        else:
            clusters_weak.setdefault(root, set()).add(name_val)

    strong_groups = [sorted(names) for names in clusters_strong.values() if len(names) > 1]
    weak_groups = [sorted(names) for names in clusters_weak.values() if len(names) > 1]

    strong_groups.sort(key=lambda g: g[0])
    weak_groups.sort(key=lambda g: g[0])

    # Write .txt output files
    out_dir = os.path.dirname(input_path) or "."

    strong_path = os.path.join(out_dir, "strong_matches.txt")
    write_output(strong_path, strong_groups)
    print(f"Strong matches: {len(strong_groups)} clusters -> {strong_path}")

    weak_path = os.path.join(out_dir, "weak_matches.txt")
    write_output(weak_path, weak_groups)
    print(f"Weak matches:   {len(weak_groups)} clusters -> {weak_path}")

    # Build and write corrected database
    corrected = build_corrected_df(df, uf, norm_names, n, cities)
    corrected_path = write_corrected_db(corrected, input_path)
    print(f"Corrected DB:   {n} rows -> {len(corrected)} rows -> {corrected_path}")


def write_output(path: str, groups: list):
    with open(path, "w", encoding="utf-8") as f:
        for idx, group in enumerate(groups):
            for name in group:
                f.write(name + "\n")
            if idx < len(groups) - 1:
                f.write(CLUSTER_SEPARATOR + "\n")


# ---------------------------------------------------------------------------
# Corrected database helpers
# ---------------------------------------------------------------------------
_NUMERIC_COLS = {"apv_ma_2025", "trx_ma_2025"}


def strip_locations(name: str, cities: set) -> str:
    """Remove city/location tokens from a name while preserving the rest."""
    if not isinstance(name, str):
        return ""
    text = name.strip()
    tokens = text.split()
    filtered = [t for t in tokens if t.upper() not in cities]
    # If stripping removed everything, keep the original
    return " ".join(filtered) if filtered else text


def pick_longest_name(indices: list, df: pd.DataFrame, cities: set) -> str:
    """Pick the longest original name after stripping cities/locations."""
    best = ""
    for i in indices:
        original = str(df.iloc[i].get("canonical_name", "")).strip()
        cleaned = strip_locations(original, cities)
        if len(cleaned) > len(best):
            best = cleaned
    return best


def build_corrected_df(df: pd.DataFrame, uf, norm_names: list, n: int,
                       cities: set) -> pd.DataFrame:
    roots = [uf.find(i) for i in range(n)]

    groups: dict[int, list[int]] = {}
    for i, r in enumerate(roots):
        groups.setdefault(r, []).append(i)

    numeric_cols_present = [c for c in _NUMERIC_COLS if c in df.columns]

    result_rows = []
    for root, indices in groups.items():
        if len(indices) == 1:
            row = df.iloc[indices[0]].copy()
            row["canonical_name"] = strip_locations(
                str(row["canonical_name"]), cities
            ) or row["canonical_name"]
            result_rows.append(row)
            continue

        sub = df.iloc[indices].copy()

        for col in numeric_cols_present:
            sub[col] = pd.to_numeric(sub[col], errors="coerce")

        if "apv_ma_2025" in sub.columns:
            dominant_idx = sub["apv_ma_2025"].idxmax()
        else:
            dominant_idx = sub.index[0]

        new_row = sub.loc[dominant_idx].copy()
        new_row["canonical_name"] = pick_longest_name(indices, df, cities)

        for col in numeric_cols_present:
            new_row[col] = sub[col].sum()

        result_rows.append(new_row)

    corrected = pd.DataFrame(result_rows, columns=df.columns)
    corrected.reset_index(drop=True, inplace=True)
    return corrected


def write_corrected_db(corrected: pd.DataFrame, input_path: str):
    out_dir = os.path.dirname(input_path) or "."
    base = os.path.basename(input_path)
    out_name = f"corrected_{base}"
    out_path = os.path.join(out_dir, out_name)

    ext = os.path.splitext(input_path)[1].lower()
    if ext == ".csv":
        corrected.to_csv(out_path, index=False, encoding="utf-8")
    else:
        corrected.to_excel(out_path, index=False)

    return out_path


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
