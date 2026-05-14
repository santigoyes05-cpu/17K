"""
End-to-end data processing and analytics pipeline.
Transactional + Demographic datasets with MCC and geographic enrichment.
"""

import pandas as pd
import numpy as np
from pathlib import Path


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

TRANSACTIONAL_PATH = (
    r"C:\Users\e183609\OneDrive - Mastercard\Documents\Aleja"
    r"\Transaccional davivienda v2.xlsx"
)

# Adjust these paths to wherever the demographic and dictionary files live.
DEMOGRAPHIC_PATH = "demographic.xlsx"   # or .csv
MCC_DICT_PATH    = "mcc_dictionary.xlsx"
GEO_DICT_PATH    = "geo_dictionary.xlsx"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _clean_str_col(series: pd.Series, zfill: int | None = None) -> pd.Series:
    """
    Trim whitespace, convert to string, optionally zero-pad.
    Nulls become pd.NA so left-joins still work.
    """
    out = series.astype(str).str.strip()
    out = out.replace({"nan": pd.NA, "None": pd.NA, "": pd.NA})
    if zfill:
        out = out.str.zfill(zfill)
    return out


def _row_count_report(label: str, before: int, after: int) -> None:
    diff = after - before
    sign = "+" if diff >= 0 else ""
    print(f"  [{label}] rows before={before:,}  after={after:,}  delta={sign}{diff:,}")


def _null_match_pct(left: pd.Series, merged_col: pd.Series, label: str) -> None:
    total      = len(left)
    matched    = merged_col.notna().sum()
    unmatched  = total - matched
    pct_match  = matched / total * 100 if total else 0
    print(
        f"  [{label}] total={total:,}  matched={matched:,}  "
        f"unmatched={unmatched:,}  match_rate={pct_match:.1f}%"
    )


# ---------------------------------------------------------------------------
# 1. Loaders
# ---------------------------------------------------------------------------

def load_transactional(path: str) -> pd.DataFrame:
    print("\n--- Loading transactional dataset ---")
    df = pd.read_excel(path, dtype=str)
    print(f"  Raw rows: {len(df):,}  cols: {list(df.columns)}")

    # Standardise join / key columns
    df["id_cliente"]                = _clean_str_col(df["id_cliente"])
    df["COD_ESTABLECIMIENTO"]       = _clean_str_col(df["COD_ESTABLECIMIENTO"])
    df["CIUDAD_UBICACION_COMERCIO"] = _clean_str_col(df["CIUDAD_UBICACION_COMERCIO"])

    # Numeric columns
    df["MONTO_TX"]    = pd.to_numeric(df["MONTO_TX"],    errors="coerce")
    df["TRANSACCION"] = pd.to_numeric(df["TRANSACCION"], errors="coerce")

    # Date column
    df["PERIODO"] = pd.to_datetime(df["PERIODO"], errors="coerce", dayfirst=False)
    n_bad_dates   = df["PERIODO"].isna().sum()
    print(f"  PERIODO parse errors: {n_bad_dates:,}")

    return df


def load_demographic(path: str) -> pd.DataFrame:
    print("\n--- Loading demographic dataset ---")
    ext = Path(path).suffix.lower()
    if ext == ".csv":
        df = pd.read_csv(path, dtype=str)
    else:
        df = pd.read_excel(path, dtype=str)
    print(f"  Raw rows: {len(df):,}  cols: {list(df.columns)}")

    df["id_cliente"] = _clean_str_col(df["id_cliente"])
    df["COD_CIUDAD"] = _clean_str_col(df["COD_CIUDAD"])
    df["IND_ESTADO"] = pd.to_numeric(df["IND_ESTADO"], errors="coerce")

    df["FECHA_ACTIVACION"] = pd.to_datetime(
        df["FECHA_ACTIVACION"], errors="coerce", dayfirst=False
    )
    n_bad_dates = df["FECHA_ACTIVACION"].isna().sum()
    print(f"  FECHA_ACTIVACION parse errors: {n_bad_dates:,}")

    return df


def load_mcc_dict(path: str) -> pd.DataFrame:
    print("\n--- Loading MCC dictionary ---")
    ext = Path(path).suffix.lower()
    df = pd.read_csv(path, dtype=str) if ext == ".csv" else pd.read_excel(path, dtype=str)
    df["mcc_cd"] = _clean_str_col(df["mcc_cd"])
    df = df.drop_duplicates(subset=["mcc_cd"])
    print(f"  MCC entries: {len(df):,}")
    return df


def load_geo_dict(path: str) -> pd.DataFrame:
    print("\n--- Loading geographic dictionary ---")
    ext = Path(path).suffix.lower()
    df = pd.read_csv(path, dtype=str) if ext == ".csv" else pd.read_excel(path, dtype=str)
    df["codigo_municipio"] = _clean_str_col(df["codigo_municipio"])
    df = df.drop_duplicates(subset=["codigo_municipio"])
    print(f"  Geo entries: {len(df):,}")
    return df


# ---------------------------------------------------------------------------
# 2. Enrichment joins
# ---------------------------------------------------------------------------

def enrich_transactions(
    df: pd.DataFrame,
    mcc: pd.DataFrame,
) -> pd.DataFrame:
    """Left-join MCC dictionary onto transactional data."""
    print("\n--- Enriching transactions ---")
    before = len(df)

    mcc_slim = mcc[["mcc_cd", "industry", "super_industry"]].copy()
    enriched = df.merge(
        mcc_slim,
        left_on  = "COD_ESTABLECIMIENTO",
        right_on = "mcc_cd",
        how      = "left",
    )

    _row_count_report("transactions + MCC", before, len(enriched))
    _null_match_pct(df["COD_ESTABLECIMIENTO"], enriched["industry"], "MCC match")

    # Drop any duplicates introduced by the merge (shouldn't happen; mcc is deduped)
    enriched = enriched.drop_duplicates()
    return enriched


def enrich_demographics(
    df: pd.DataFrame,
    geo: pd.DataFrame,
) -> pd.DataFrame:
    """Left-join geographic dictionary onto demographic data."""
    print("\n--- Enriching demographics ---")
    before = len(df)

    geo_slim = geo[["codigo_municipio", "departamento", "municipio"]].copy()
    enriched = df.merge(
        geo_slim,
        left_on  = "COD_CIUDAD",
        right_on = "codigo_municipio",
        how      = "left",
    )

    _row_count_report("demographics + Geo", before, len(enriched))
    _null_match_pct(df["COD_CIUDAD"], enriched["departamento"], "Geo match")

    enriched = enriched.drop_duplicates()
    return enriched


# ---------------------------------------------------------------------------
# 3. Business metrics
# ---------------------------------------------------------------------------

def compute_card_metrics(
    enriched_demo: pd.DataFrame,
    enriched_tx: pd.DataFrame,
) -> dict:
    """Scalar card-level KPIs."""
    customers_with_tx = set(enriched_tx["id_cliente"].dropna().unique())

    metrics = {
        "approved_cards":   len(enriched_demo),
        "accepted_cards":   len(enriched_demo),   # same as approved per spec
        "delivered_cards":  int((enriched_demo["IND_ESTADO"] == 1).sum()),
        "cards_with_usage": int(
            enriched_demo["id_cliente"].isin(customers_with_tx).sum()
        ),
    }

    print("\n--- Card Metrics ---")
    for k, v in metrics.items():
        print(f"  {k}: {v:,}")
    return metrics


def compute_department_distribution(
    enriched_demo: pd.DataFrame,
    enriched_tx: pd.DataFrame,
) -> pd.DataFrame:
    """
    One row per departamento with approved/accepted/delivered/usage counts.
    Sorted descending by cards_with_usage.
    """
    print("\n--- Computing department distribution ---")

    customers_with_tx = set(enriched_tx["id_cliente"].dropna().unique())

    demo = enriched_demo.copy()
    demo["has_usage"]    = demo["id_cliente"].isin(customers_with_tx)
    demo["is_delivered"] = demo["IND_ESTADO"] == 1

    dept_table = (
        demo.groupby("departamento", dropna=False)
        .agg(
            approved_cards   = ("id_cliente",   "count"),
            accepted_cards   = ("id_cliente",   "count"),
            delivered_cards  = ("is_delivered", "sum"),
            cards_with_usage = ("has_usage",    "sum"),
        )
        .reset_index()
        .sort_values("cards_with_usage", ascending=False)
        .reset_index(drop=True)
    )

    dept_table["delivered_cards"]  = dept_table["delivered_cards"].astype(int)
    dept_table["cards_with_usage"] = dept_table["cards_with_usage"].astype(int)

    print(f"  Departments found: {len(dept_table):,}")
    return dept_table


def compute_top_municipalities(
    enriched_demo: pd.DataFrame,
    department_distribution: pd.DataFrame,
    top_n_depts: int = 4,
    top_n_munis: int = 4,
) -> pd.DataFrame:
    """
    Top-N departments by cards_with_usage, then top-N municipios inside each,
    with approved and delivered card counts.
    """
    print("\n--- Computing top municipalities ---")

    top_depts = (
        department_distribution
        .nlargest(top_n_depts, "cards_with_usage")["departamento"]
        .tolist()
    )
    print(f"  Top {top_n_depts} departments: {top_depts}")

    demo_filtered = enriched_demo[
        enriched_demo["departamento"].isin(top_depts)
    ].copy()
    demo_filtered["is_delivered"] = demo_filtered["IND_ESTADO"] == 1

    muni_table = (
        demo_filtered.groupby(["departamento", "municipio"], dropna=False)
        .agg(
            approved_cards  = ("id_cliente",   "count"),
            delivered_cards = ("is_delivered", "sum"),
        )
        .reset_index()
    )

    # Top-N municipios per department by approved cards
    muni_table = (
        muni_table
        .sort_values(["departamento", "approved_cards"], ascending=[True, False])
        .groupby("departamento", group_keys=False)
        .head(top_n_munis)
        .reset_index(drop=True)
    )

    muni_table["delivered_cards"] = muni_table["delivered_cards"].astype(int)
    print(f"  Municipality rows returned: {len(muni_table):,}")
    return muni_table


def compute_ttfu_distribution(
    enriched_demo: pd.DataFrame,
    enriched_tx: pd.DataFrame,
    max_day: int = 180,
) -> pd.DataFrame:
    """
    Time-to-first-use distribution.
    Returns a spine of day_number 1–180 with number_of_customers per day.
    """
    print("\n--- Computing TTFU distribution ---")

    # Earliest transaction date per customer
    first_tx = (
        enriched_tx.dropna(subset=["id_cliente", "PERIODO"])
        .groupby("id_cliente")["PERIODO"]
        .min()
        .reset_index()
        .rename(columns={"PERIODO": "first_tx_date"})
    )

    demo = enriched_demo[["id_cliente", "FECHA_ACTIVACION"]].dropna().copy()
    merged = demo.merge(first_tx, on="id_cliente", how="inner")

    merged["days_between_first_use"] = (
        merged["first_tx_date"] - merged["FECHA_ACTIVACION"]
    ).dt.days

    # Keep non-negative values within the 180-day window
    valid = merged[
        (merged["days_between_first_use"] >= 0) &
        (merged["days_between_first_use"] <= max_day)
    ]

    # Count customers per day then join to the full 1–180 spine
    counts = (
        valid["days_between_first_use"]
        .value_counts()
        .rename_axis("day_number")
        .rename("number_of_customers")
        .reset_index()
    )

    spine = pd.DataFrame({"day_number": range(1, max_day + 1)})
    ttfu = (
        spine.merge(counts, on="day_number", how="left")
        .fillna({"number_of_customers": 0})
        .sort_values("day_number")
        .reset_index(drop=True)
    )
    ttfu["number_of_customers"] = ttfu["number_of_customers"].astype(int)

    total_customers = valid["id_cliente"].nunique()
    print(f"  Customers with valid TTFU: {total_customers:,}")
    print(f"  Days with at least one customer: {(ttfu['number_of_customers'] > 0).sum()}")
    return ttfu


# ---------------------------------------------------------------------------
# 4. Validation utilities
# ---------------------------------------------------------------------------

def validate_date_parsing(df: pd.DataFrame, col: str, label: str) -> None:
    total  = len(df)
    parsed = df[col].notna().sum()
    failed = total - parsed
    pct    = parsed / total * 100 if total else 0
    print(
        f"  [Date – {label}] total={total:,}  parsed={parsed:,}  "
        f"failed={failed:,}  success={pct:.1f}%"
    )
    if parsed > 0:
        print(f"    min={df[col].min()}  max={df[col].max()}")


# ---------------------------------------------------------------------------
# 5. Main orchestrator
# ---------------------------------------------------------------------------

def run_pipeline(
    transactional_path: str = TRANSACTIONAL_PATH,
    demographic_path:   str = DEMOGRAPHIC_PATH,
    mcc_dict_path:      str = MCC_DICT_PATH,
    geo_dict_path:      str = GEO_DICT_PATH,
) -> dict:
    print("=" * 60)
    print("DATA PIPELINE START")
    print("=" * 60)

    # Load
    raw_tx   = load_transactional(transactional_path)
    raw_demo = load_demographic(demographic_path)
    mcc_dict = load_mcc_dict(mcc_dict_path)
    geo_dict = load_geo_dict(geo_dict_path)

    # Date validation
    print("\n--- Date validation ---")
    validate_date_parsing(raw_tx,   "PERIODO",          "PERIODO (transactional)")
    validate_date_parsing(raw_demo, "FECHA_ACTIVACION", "FECHA_ACTIVACION (demographic)")

    # Enrich
    enriched_transactions = enrich_transactions(raw_tx,   mcc_dict)
    enriched_demographics = enrich_demographics(raw_demo, geo_dict)

    # Metrics
    card_metrics             = compute_card_metrics(enriched_demographics, enriched_transactions)
    department_distribution  = compute_department_distribution(enriched_demographics, enriched_transactions)
    top_municipalities_table = compute_top_municipalities(enriched_demographics, department_distribution)
    ttfu_distribution_table  = compute_ttfu_distribution(enriched_demographics, enriched_transactions)

    print("\n" + "=" * 60)
    print("PIPELINE COMPLETE")
    print("=" * 60)

    return {
        "enriched_transactions":         enriched_transactions,
        "enriched_demographics":         enriched_demographics,
        "card_metrics":                  card_metrics,
        "department_distribution_table": department_distribution,
        "top_municipalities_table":      top_municipalities_table,
        "ttfu_distribution_table":       ttfu_distribution_table,
    }


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    results = run_pipeline()

    print("\n--- department_distribution_table ---")
    print(results["department_distribution_table"].to_string(index=False))

    print("\n--- top_municipalities_table ---")
    print(results["top_municipalities_table"].to_string(index=False))

    print("\n--- ttfu_distribution_table (first 20 days) ---")
    print(results["ttfu_distribution_table"].head(20).to_string(index=False))
