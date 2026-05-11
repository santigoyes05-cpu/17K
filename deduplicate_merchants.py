"""
Merchant Name Deduplication Script
===================================
Groups similar merchant names from a dataset, producing three outputs:
- strong_matches.txt: high-confidence clusters (similarity >= 0.78, same industria, same adquirente_principal)
- weak_matches.txt: lower-confidence clusters (similarity 0.65–0.78, same industria, shared acquirer via fallback)
- corrected_<input_filename>: consolidated dataset with one row per merchant group

Usage:
    1. Set the INPUT_FILE variable below to your dataset path.
    2. Run:  python deduplicate_merchants.py

Supported input formats: .csv, .xlsx, .xls
"""

import sys
import os
import re
import string
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
        # Track the weakest link strength per component root.
        # True = all strong, False = at least one weak link.
        self.all_strong = [True] * n

    def find(self, x):
        while self.parent[x] != x:
            self.parent[x] = self.parent[self.parent[x]]  # path compression
            x = self.parent[x]
        return x

    def union(self, a, b, is_strong: bool):
        ra, rb = self.find(a), self.find(b)
        if ra == rb:
            # Already in the same set — but if this link is weak, mark it.
            if not is_strong:
                self.all_strong[ra] = False
            return
        # Union by rank
        if self.rank[ra] < self.rank[rb]:
            ra, rb = rb, ra
        self.parent[rb] = ra
        if self.rank[ra] == self.rank[rb]:
            self.rank[ra] += 1
        # Propagate weakness: if either component had a weak link, or this
        # link itself is weak, the merged component is weak.
        self.all_strong[ra] = self.all_strong[ra] and self.all_strong[rb] and is_strong

    def is_component_strong(self, x):
        return self.all_strong[self.find(x)]


# ---------------------------------------------------------------------------
# Text normalisation helpers
# ---------------------------------------------------------------------------
def build_city_set(df: pd.DataFrame) -> set:
    """Extract unique city names from ciudad_principal to use as stopwords."""
    cities = set()
    if "ciudad_principal" in df.columns:
        for c in df["ciudad_principal"].dropna().unique():
            cities.add(str(c).strip().upper())
    return cities


# Common business suffixes to strip
_BUSINESS_SUFFIXES = {
    "SA", "SAS", "LTDA", "EU", "CIA", "CO", "INC", "CORP", "LLC",
    "NIT", "EAT", "ESP", "SUCURSAL",
}


def normalize(name: str, cities: set) -> str:
    """Normalize a merchant name for comparison."""
    if not isinstance(name, str):
        return ""
    text = name.upper()
    # Remove punctuation (keep spaces)
    text = re.sub(r"[^\w\s]", " ", text)
    # Collapse whitespace
    text = re.sub(r"\s+", " ", text).strip()

    tokens = text.split()
    # Remove trailing city names
    filtered = []
    for tok in tokens:
        if tok in cities:
            continue
        if tok in _BUSINESS_SUFFIXES:
            continue
        # Remove purely numeric tokens (e.g. NIT numbers, zip codes)
        if tok.isdigit():
            continue
        filtered.append(tok)
    # If stripping removed everything, fall back to the collapsed version
    return " ".join(filtered) if filtered else text


# ---------------------------------------------------------------------------
# Similarity computation
# ---------------------------------------------------------------------------
def compute_similarity(norm_a: str, norm_b: str) -> float:
    """
    Return a similarity score in [0, 1] between two normalised names.
    Uses a combination of prefix matching, token Jaccard, and SequenceMatcher.
    """
    if not norm_a or not norm_b:
        return 0.0

    # Exact match after normalisation
    if norm_a == norm_b:
        return 1.0

    shorter, longer = (norm_a, norm_b) if len(norm_a) <= len(norm_b) else (norm_b, norm_a)

    # --- Prefix match ---
    if longer.startswith(shorter):
        # Score based on how much of the longer string the prefix covers
        prefix_ratio = len(shorter) / len(longer)
        if prefix_ratio >= 0.55:
            return max(prefix_ratio, 0.80)  # Boost: strong signal

    # --- Token-level Jaccard ---
    tokens_a = set(norm_a.split())
    tokens_b = set(norm_b.split())
    if tokens_a and tokens_b:
        jaccard = len(tokens_a & tokens_b) / len(tokens_a | tokens_b)
    else:
        jaccard = 0.0

    # --- SequenceMatcher ratio ---
    seq_ratio = SequenceMatcher(None, norm_a, norm_b).ratio()

    # Combine: take the best signal
    return max(jaccard, seq_ratio)


# ---------------------------------------------------------------------------
# Acquirer overlap helpers
# ---------------------------------------------------------------------------
def parse_acquirers(value) -> set:
    """Parse an acquirer field (may be comma/semicolon-separated or single)."""
    if pd.isna(value) or value is None:
        return set()
    text = str(value).strip().upper()
    if not text:
        return set()
    # Split on common delimiters
    parts = re.split(r"[;,|/]", text)
    return {p.strip() for p in parts if p.strip()}


def acquirers_overlap(principal_a: set, secondary_a: set,
                      principal_b: set, secondary_b: set) -> bool:
    """Check if two merchants share any acquiring bank (principal or secondary)."""
    all_a = principal_a | secondary_a
    all_b = principal_b | secondary_b
    return bool(all_a & all_b)


# ---------------------------------------------------------------------------
# Main logic
# ---------------------------------------------------------------------------
def load_data(path: str) -> pd.DataFrame:
    ext = os.path.splitext(path)[1].lower()
    if ext == ".csv":
        # Try common encodings
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

    # Validate required columns
    required = {"comercio", "industria", "adquirente_principal"}
    missing = required - set(df.columns)
    if missing:
        sys.exit(f"Error: missing required columns: {missing}")

    # Build city stopword set
    cities = build_city_set(df)
    print(f"Built city stopword set with {len(cities)} entries.")

    # Precompute normalised names and acquirer sets
    norm_names = []
    principals = []
    secondaries = []
    industrias = []

    for _, row in df.iterrows():
        norm_names.append(normalize(str(row.get("comercio", "")), cities))
        industrias.append(str(row.get("industria", "")).strip().upper())
        principals.append(parse_acquirers(row.get("adquirente_principal")))
        secondaries.append(parse_acquirers(row.get("adquirente_secundario")))

    # Union-Find clustering
    uf = UnionFind(n)
    matches_found = 0

    for i in range(n):
        # Lookback window
        start = max(0, i - LOOKBACK_WINDOW)
        for j in range(start, i):
            # Must share industria
            if industrias[i] != industrias[j]:
                continue

            # Compute similarity
            sim = compute_similarity(norm_names[i], norm_names[j])

            # Short-name guard: require near-exact for short names
            shorter_len = min(len(norm_names[i]), len(norm_names[j]))
            if shorter_len < MIN_LENGTH_FOR_FUZZY:
                if sim < 0.95:
                    continue

            # Determine match type
            if sim >= STRONG_THRESHOLD:
                # Strong match: same adquirente_principal
                if principals[i] & principals[j]:
                    uf.union(i, j, is_strong=True)
                    matches_found += 1
                # Even with high similarity, if principals differ, try fallback
                elif acquirers_overlap(principals[i], secondaries[i],
                                       principals[j], secondaries[j]):
                    uf.union(i, j, is_strong=False)
                    matches_found += 1
            elif sim >= WEAK_THRESHOLD:
                # Borderline: require acquirer overlap (any)
                if acquirers_overlap(principals[i], secondaries[i],
                                     principals[j], secondaries[j]):
                    uf.union(i, j, is_strong=False)
                    matches_found += 1

        if (i + 1) % 5000 == 0:
            print(f"  Processed {i + 1}/{n} rows...")

    print(f"Finished comparisons. Total union operations: {matches_found}")

    # Build clusters
    clusters_strong = {}  # root -> set of original comercio values
    clusters_weak = {}

    for i in range(n):
        root = uf.find(i)
        comercio_val = str(df.iloc[i].get("comercio", "")).strip()
        if not comercio_val:
            continue
        if uf.is_component_strong(root):
            clusters_strong.setdefault(root, set()).add(comercio_val)
        else:
            clusters_weak.setdefault(root, set()).add(comercio_val)

    # Filter: only keep clusters with size > 1
    strong_groups = [sorted(names) for names in clusters_strong.values() if len(names) > 1]
    weak_groups = [sorted(names) for names in clusters_weak.values() if len(names) > 1]

    # Sort groups by first merchant name for deterministic output
    strong_groups.sort(key=lambda g: g[0])
    weak_groups.sort(key=lambda g: g[0])

    # Write output files
    out_dir = os.path.dirname(input_path) or "."

    strong_path = os.path.join(out_dir, "strong_matches.txt")
    write_output(strong_path, strong_groups)
    print(f"Strong matches: {len(strong_groups)} clusters -> {strong_path}")

    weak_path = os.path.join(out_dir, "weak_matches.txt")
    write_output(weak_path, weak_groups)
    print(f"Weak matches:   {len(weak_groups)} clusters -> {weak_path}")

    # Build and write corrected database
    corrected = build_corrected_df(df, uf, norm_names, n)
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
_NUMERIC_COLS = {"num_sucursales", "fact_mc_2025", "trx_2025"}


def build_corrected_df(df: pd.DataFrame, uf, norm_names: list, n: int) -> pd.DataFrame:
    """Consolidate clustered rows into one row per merchant group."""
    # Map each row to its cluster root
    roots = [uf.find(i) for i in range(n)]

    # Group row indices by root
    groups: dict[int, list[int]] = {}
    for i, r in enumerate(roots):
        groups.setdefault(r, []).append(i)

    numeric_cols_present = [c for c in _NUMERIC_COLS if c in df.columns]
    text_cols = [c for c in df.columns if c not in _NUMERIC_COLS and c != "comercio"]

    result_rows = []
    for root, indices in groups.items():
        if len(indices) == 1:
            row = df.iloc[indices[0]].copy()
            row["comercio"] = norm_names[indices[0]] or row["comercio"]
            result_rows.append(row)
            continue

        sub = df.iloc[indices].copy()

        # Convert numeric columns for aggregation
        for col in numeric_cols_present:
            sub[col] = pd.to_numeric(sub[col], errors="coerce")

        # Find dominant row (largest fact_mc_2025) for text columns
        if "fact_mc_2025" in sub.columns:
            dominant_idx = sub["fact_mc_2025"].idxmax()
        else:
            dominant_idx = sub.index[0]

        new_row = sub.loc[dominant_idx].copy()
        new_row["comercio"] = norm_names[root]

        # Sum numeric columns
        for col in numeric_cols_present:
            new_row[col] = sub[col].sum()

        result_rows.append(new_row)

    corrected = pd.DataFrame(result_rows, columns=df.columns)
    corrected.reset_index(drop=True, inplace=True)
    return corrected


def write_corrected_db(corrected: pd.DataFrame, input_path: str):
    """Write the corrected database in the same format as the input."""
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
