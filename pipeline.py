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
# 3b. First-usage, channel, and industry analytics  (§6, §7, §8)
# ---------------------------------------------------------------------------

def _first_transaction_per_customer(enriched_tx: pd.DataFrame) -> pd.DataFrame:
    """
    One row per customer carrying the attributes of their first transaction.
    Used by §6.3, §7, §8.
    """
    cols = [
        "id_cliente", "PERIODO", "Tipo_Transaccion",
        "COD_ESTABLECIMIENTO", "industry", "super_industry", "MONTO_TX",
    ]
    base = enriched_tx.dropna(subset=["id_cliente", "PERIODO"])[cols].copy()
    base = base.sort_values(["id_cliente", "PERIODO"], kind="mergesort")
    first = base.drop_duplicates(subset=["id_cliente"], keep="first").reset_index(drop=True)

    return first.rename(columns={
        "PERIODO":             "first_tx_date",
        "Tipo_Transaccion":    "first_tx_Tipo_Transaccion",
        "COD_ESTABLECIMIENTO": "first_tx_COD_ESTABLECIMIENTO",
        "industry":            "first_tx_industry",
        "super_industry":      "first_tx_super_industry",
        "MONTO_TX":            "first_tx_MONTO_TX",
    })


def compute_first_usage_metrics(enriched_tx: pd.DataFrame) -> dict:
    """
    §6.1 + §6.2 — customer-level (not transaction-level) usage by channel.
    """
    print("\n--- Computing first-usage metrics (§6.1, §6.2) ---")

    df = enriched_tx.dropna(subset=["id_cliente"])
    by_cust = df.groupby("id_cliente")["Tipo_Transaccion"].agg(set)

    has_pos = by_cust.apply(lambda s: "POS" in s)
    has_atm = by_cust.apply(lambda s: "ATM" in s)

    metrics = {
        "customers_with_first_usage": int(by_cust.shape[0]),
        "pos_customers":              int(has_pos.sum()),
        "atm_customers":              int(has_atm.sum()),
        "pos_only_customers":         int((has_pos & ~has_atm).sum()),
        "atm_only_customers":         int((~has_pos & has_atm).sum()),
        "both_pos_and_atm_customers": int((has_pos & has_atm).sum()),
    }
    for k, v in metrics.items():
        print(f"  {k}: {v:,}")
    return metrics


def compute_first_usage_channel(first_tx_df: pd.DataFrame) -> dict:
    """§6.3 — channel of each customer's chronologically first transaction."""
    print("\n--- Computing first-usage channel (§6.3) ---")
    counts = first_tx_df["first_tx_Tipo_Transaccion"].value_counts(dropna=False)
    metrics = {
        "first_usage_pos_customers": int(counts.get("POS", 0)),
        "first_usage_atm_customers": int(counts.get("ATM", 0)),
    }
    for k, v in metrics.items():
        print(f"  {k}: {v:,}")
    return metrics


def compute_first_purchase_industry(first_tx_df: pd.DataFrame) -> pd.DataFrame:
    """§7 — industries where customers made their first transaction."""
    print("\n--- Computing first-purchase industry (§7) ---")
    table = (
        first_tx_df.groupby("first_tx_industry", dropna=False)
        .agg(
            num_customers = ("id_cliente",        "nunique"),
            avg_ticket    = ("first_tx_MONTO_TX", "mean"),
        )
        .reset_index()
        .rename(columns={"first_tx_industry": "industry"})
        .sort_values("num_customers", ascending=False)
        .reset_index(drop=True)
    )
    print(f"  Industries: {len(table):,}")
    return table


def compute_first_pos_purchase(first_tx_df: pd.DataFrame) -> tuple[dict, pd.DataFrame]:
    """§8 — average ticket of the first POS purchase (overall + by industry)."""
    print("\n--- Computing first POS purchase (§8) ---")
    pos_first = first_tx_df[first_tx_df["first_tx_Tipo_Transaccion"] == "POS"].copy()

    overall = {
        "first_pos_customers":      int(pos_first["id_cliente"].nunique()),
        "avg_first_pos_ticket":     float(pos_first["first_tx_MONTO_TX"].mean()) if len(pos_first) else float("nan"),
    }
    print(f"  first_pos_customers: {overall['first_pos_customers']:,}")
    print(f"  avg_first_pos_ticket: {overall['avg_first_pos_ticket']:.2f}")

    by_industry = (
        pos_first.groupby("first_tx_industry", dropna=False)
        .agg(
            num_customers = ("id_cliente",        "nunique"),
            avg_ticket    = ("first_tx_MONTO_TX", "mean"),
        )
        .reset_index()
        .rename(columns={"first_tx_industry": "industry"})
        .sort_values("num_customers", ascending=False)
        .reset_index(drop=True)
    )
    return overall, by_industry


# ---------------------------------------------------------------------------
# 3c. POS analytics  (§10)
# ---------------------------------------------------------------------------

def compute_pos_total_metrics(pos_tx: pd.DataFrame) -> dict:
    """§10.1 — portfolio-wide POS metrics."""
    print("\n--- Computing POS total metrics (§10.1) ---")
    total_revenue  = float(pos_tx["MONTO_TX"].sum())
    total_tx       = int(len(pos_tx))
    n_customers    = int(pos_tx["id_cliente"].nunique())

    metrics = {
        "total_pos_revenue":              total_revenue,
        "total_pos_transactions":         total_tx,
        "pos_customers":                  n_customers,
        "avg_ticket_pos":                 total_revenue / total_tx     if total_tx     else float("nan"),
        "avg_revenue_per_customer_pos":   total_revenue / n_customers  if n_customers  else float("nan"),
        "avg_transactions_per_customer_pos": total_tx / n_customers    if n_customers  else float("nan"),
    }
    for k, v in metrics.items():
        print(f"  {k}: {v:,.2f}" if isinstance(v, float) else f"  {k}: {v:,}")
    return metrics


def compute_pos_customer_level_metrics(pos_tx: pd.DataFrame) -> tuple[pd.DataFrame, dict]:
    """§10.2 — per-customer POS behavior + aggregate means."""
    print("\n--- Computing POS customer-level metrics (§10.2) ---")

    per_customer = (
        pos_tx.groupby("id_cliente")
        .agg(
            pos_tx_count = ("MONTO_TX", "size"),
            pos_spend    = ("MONTO_TX", "sum"),
            avg_ticket   = ("MONTO_TX", "mean"),
        )
        .reset_index()
    )

    aggregates = {
        "avg_transactions_per_customer_pos": float(per_customer["pos_tx_count"].mean()) if len(per_customer) else float("nan"),
        "avg_spend_per_customer_pos":        float(per_customer["pos_spend"].mean())    if len(per_customer) else float("nan"),
    }
    print(f"  customers: {len(per_customer):,}")
    for k, v in aggregates.items():
        print(f"  {k}: {v:,.2f}")
    return per_customer, aggregates


def compute_pos_industry_rankings(
    pos_tx: pd.DataFrame,
    top_n: int | None = None,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """§10.3 — POS rankings by revenue and by transaction count."""
    print("\n--- Computing POS industry rankings (§10.3) ---")

    grouped = (
        pos_tx.groupby("industry", dropna=False)
        .agg(
            total_pos_revenue      = ("MONTO_TX",   "sum"),
            total_pos_transactions = ("MONTO_TX",   "size"),
            avg_pos_ticket         = ("MONTO_TX",   "mean"),
        )
        .reset_index()
    )

    revenue_ranking = grouped.sort_values("total_pos_revenue",      ascending=False).reset_index(drop=True)
    frequency_ranking = grouped.sort_values("total_pos_transactions", ascending=False).reset_index(drop=True)

    if top_n is not None:
        revenue_ranking   = revenue_ranking.head(top_n)
        frequency_ranking = frequency_ranking.head(top_n)

    print(f"  Industries: {len(grouped):,}")
    return revenue_ranking, frequency_ranking


def _validate_pos_consistency(pos_total: dict, customer_level: pd.DataFrame) -> None:
    """§12 — total POS revenue must equal sum of customer-level POS spend."""
    total_from_aggregate = pos_total["total_pos_revenue"]
    total_from_customers = float(customer_level["pos_spend"].sum())
    diff = abs(total_from_aggregate - total_from_customers)
    print(
        f"  [POS validation] total_pos_revenue={total_from_aggregate:,.2f}  "
        f"sum(pos_spend)={total_from_customers:,.2f}  diff={diff:,.6f}"
    )
    assert diff < 1e-3, "POS revenue inconsistency between aggregate and customer-level totals"


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

    # First-usage / channel / industry analytics (§6, §7, §8)
    first_tx_df             = _first_transaction_per_customer(enriched_transactions)
    first_usage_metrics     = compute_first_usage_metrics(enriched_transactions)
    first_usage_channel     = compute_first_usage_channel(first_tx_df)
    first_purchase_industry = compute_first_purchase_industry(first_tx_df)
    first_pos_purchase_overall, first_pos_purchase_by_industry = compute_first_pos_purchase(first_tx_df)

    # POS analytics (§10) — single shared filter
    pos_tx = enriched_transactions[enriched_transactions["Tipo_Transaccion"] == "POS"].copy()
    pos_total_metrics                              = compute_pos_total_metrics(pos_tx)
    pos_customer_level_metrics, pos_customer_aggs  = compute_pos_customer_level_metrics(pos_tx)
    pos_industry_revenue_ranking, pos_industry_frequency_ranking = compute_pos_industry_rankings(pos_tx)

    # §12 consistency check
    print("\n--- POS consistency validation (§12) ---")
    _validate_pos_consistency(pos_total_metrics, pos_customer_level_metrics)

    print("\n" + "=" * 60)
    print("PIPELINE COMPLETE")
    print("=" * 60)

    return {
        "enriched_transactions":            enriched_transactions,
        "enriched_demographics":            enriched_demographics,
        "card_metrics":                     card_metrics,
        "department_distribution_table":    department_distribution,
        "top_municipalities_table":         top_municipalities_table,
        "ttfu_distribution_table":          ttfu_distribution_table,
        "first_tx_df":                      first_tx_df,
        "first_usage_metrics":              first_usage_metrics,
        "first_usage_channel":              first_usage_channel,
        "first_purchase_industry":          first_purchase_industry,
        "first_pos_purchase_overall":       first_pos_purchase_overall,
        "first_pos_purchase_by_industry":   first_pos_purchase_by_industry,
        "pos_total_metrics":                pos_total_metrics,
        "pos_customer_level_metrics":       pos_customer_level_metrics,
        "pos_customer_aggregates":          pos_customer_aggs,
        "pos_industry_revenue_ranking":     pos_industry_revenue_ranking,
        "pos_industry_frequency_ranking":   pos_industry_frequency_ranking,
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

    print("\n--- first_purchase_industry (top 10) ---")
    print(results["first_purchase_industry"].head(10).to_string(index=False))

    print("\n--- first_pos_purchase_by_industry (top 10) ---")
    print(results["first_pos_purchase_by_industry"].head(10).to_string(index=False))

    print("\n--- pos_industry_revenue_ranking (top 10) ---")
    print(results["pos_industry_revenue_ranking"].head(10).to_string(index=False))

    print("\n--- pos_industry_frequency_ranking (top 10) ---")
    print(results["pos_industry_frequency_ranking"].head(10).to_string(index=False))
