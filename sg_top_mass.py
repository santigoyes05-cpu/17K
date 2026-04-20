# ============================================================
# SECTION 1 | Libraries
# ============================================================

import re
import numpy as np
import pandas as pd

# Suppress scientific notation in printed output (equivalent to options(scipen=999))
pd.set_option("display.float_format", lambda x: f"{x:.4f}")


# ============================================================
# Helper function used by both credit and debit sections
# ============================================================

def format_currency(row, col, decimals=0):
    """
    Returns a formatted currency string like 'TTD$ 1,200' or 'RD$ 60.85'
    based on the country code in the row and the numeric value in `col`.
    """
    prefix_map = {
        "TT": "TTD$", "BB": "BBD$", "GY": "GYD$",
        "BS": "BSD$", "JM": "JMD$", "TC": "USD$",
        "KY": "KYD$", "DO": "RD$",
    }
    prefix = prefix_map.get(row["country"], "USD$")
    value = round(row[col], decimals)
    if decimals == 0:
        return f"{prefix} {value:,.0f}"
    return f"{prefix} {value:,.2f}"


# ============================================================
# SECTION 2 | CREDIT (CRÉDITO)
# ============================================================

# ---- 2.1 | Load Data ----

# Segmented credit base: pipe-delimited CSV, card_number kept as string to preserve leading zeros
base_credito = pd.read_csv(
    "2025/00.Planeacion/02.Segmentacion_total_base/results/Base_total_credito_segmentada_final.csv",
    sep="|",
    dtype={"card_number": str},
    skipinitialspace=True,
)

# Increment table for credit (multipliers per segment/country)
incrementos = pd.read_excel(
    "2025/00.Planeacion/04.Metas/S&G_top_mass/input/incrementos.xlsx",
    sheet_name="incremento_credit",
)

# Test/control group assignments for credit customers
grupos_test = pd.read_excel(
    "2025/00.Planeacion/04.Metas/S&G_top_mass/input/Grupos_test_control_SG_Top_mass.xlsx",
    sheet_name="S&G_credit",
)


# ---- 2.2 | Final S&G Credit Base ----

# Keep only the required columns; rename behavioural pre-campaign columns for clarity
base_sg = base_credito[
    [
        "cust_id", "country", "card_number", "limite_credito_usd_total", "cards",
        "product_code", "amt_6m", "trx_6m", "trx_6m_cof", "trx_6m_cnp", "trx_6m_xb",
        "delinq_flag", "campaign", "segment_name", "SubSegment",
        "uso", "digital", "primacy",
    ]
].rename(columns={
    "uso": "uso_pre_camp",
    "digital": "digital_pre_camp",
    "primacy": "primacy_pre_camp",
})

# Keep only S&G campaign rows for the five target countries with no delinquency
base_sg = base_sg[
    base_sg["country"].isin(["DO", "BB", "TT", "BS", "JM"])
    & (base_sg["campaign"] == "S&G")
    & (base_sg["delinq_flag"] == 0)
].copy()

# Attach test/control group flag (left join preserves all credit rows)
base_sg = pd.merge(base_sg, grupos_test, on="cust_id", how="left")

# Rename merged 'group' column to 'test' for clarity
base_sg = base_sg.rename(columns={"group": "test"})

# Attach increment multipliers per segment
base_sg = pd.merge(base_sg, incrementos, how="left")

# Minimum spend goals (USD) per country for the credit campaign
metas_minimas = pd.DataFrame({
    "country":     ["BB",  "BS",  "JM",  "TT",  "DO"],
    "meta_minima": [390,   530,   400,   330,   190],
})
base_sg = pd.merge(base_sg, metas_minimas, on="country", how="left")

# Drop aggregation helper columns that may have been carried over from the source file
base_sg.drop(columns=["Count_of_cust_id", "Average_of_amt_6m"], errors="ignore", inplace=True)

# USD goal = max(6-month spend × increment, minimum goal floor)
base_sg["meta_usd"] = np.maximum(
    base_sg["amt_6m"] * base_sg["Incremento"],
    base_sg["meta_minima"],
)

# Exchange rates (USD → local currency) for each country
tasas_cambio = pd.DataFrame({
    "country":      ["BB",      "BS",     "JM",   "TT",     "DO"],
    "currency":     ["BBD",     "BSD",    "JMD",  "TTD",    "DOP"],
    "tasa_cambio":  [2.02768,   1.0125,   161.9,  6.7793,   60.60],
})
base_sg = pd.merge(base_sg, tasas_cambio, on="country", how="left")

# Local-currency goal: convert from USD then round UP to the nearest 100
base_sg["meta"] = np.ceil(base_sg["meta_usd"] * base_sg["tasa_cambio"] / 100) * 100

# Agency-facing formatted columns
base_sg["meta_agencia"] = base_sg.apply(lambda r: format_currency(r, "meta", 0), axis=1)
base_sg["tasa_agencia"] = base_sg.apply(lambda r: format_currency(r, "tasa_cambio", 2), axis=1)
# Last 4 digits of the card number for masked display
base_sg["last4"] = base_sg["card_number"].str[-4:]


# ---- Reload corrected S&G base (goal calculation error was discovered) ----

base_sg = pd.read_excel(
    "2025/00.Planeacion/04.Metas/S&G_top_mass/results/20250605_Base_S&G_Credito_Top_of_Mass_final.xlsx"
)

# North countries (BS, JM) receive an extra +1 on their increment multiplier
base_sg["Incremento"] = np.where(
    base_sg["country"].isin(["BS", "JM"]),
    base_sg["Incremento"] + 1,
    base_sg["Incremento"],
)

# Recalculate USD goal with updated increments
base_sg["meta_usd"] = np.maximum(
    base_sg["amt_6m"] * base_sg["Incremento"],
    base_sg["meta_minima"],
)

# Overwrite exchange rates with corrected official values
exchange_rate_map = {
    "BB": 2.02768, "BS": 1.01250, "JM": 161.9, "TT": 6.77930, "DO": 60.60,
}
base_sg["tasa_cambio"] = base_sg["country"].map(exchange_rate_map)

# Recompute local-currency goal with corrected rates (round UP to nearest 100)
base_sg["meta"] = np.ceil(base_sg["meta_usd"] * base_sg["tasa_cambio"] / 100) * 100

# Rebuild agency columns with corrected values
base_sg["meta_agencia"] = base_sg.apply(lambda r: format_currency(r, "meta", 0), axis=1)
base_sg["tasa_agencia"] = base_sg.apply(lambda r: format_currency(r, "tasa_cambio", 2), axis=1)
base_sg["last4"] = base_sg["card_number"].str[-4:]

# Summary: average spend and average USD goal by country and segment
promedios = (
    base_sg
    .groupby(["country", "segment_name"])
    .agg(
        promedio_gasto=("amt_6m", "mean"),
        meta_promedio=("meta_usd", "mean"),
    )
    .reset_index()
)

# Export the complete corrected S&G credit base
base_sg.to_excel(
    "/ads_storage/e125316/Scotiabank Caribe/2025/00.Planeacion/04.Metas/S&G_top_mass/results/"
    "20250626_Base_S&G_Credito_Top_of_Mass_final.xlsx",
    index=False,
)


# ---- 2.3 | Agency Base for Credit ----

# Only the columns the agency needs to communicate with clients
base_agencia = base_sg[["cust_id", "country", "test", "meta_agencia", "tasa_agencia", "last4"]].copy()

# Keep only Test group (exclude Control group customers)
base_agencia = base_agencia[base_agencia["test"] == "Test"].copy()

# Sort clients from highest to lowest goal so the agency prioritises the best opportunities
base_agencia["meta_valor"] = base_agencia["meta_agencia"].apply(
    lambda x: float(re.sub(r"[^0-9.]", "", x)) if pd.notna(x) else 0.0
)
base_agencia = (
    base_agencia
    .sort_values("meta_valor", ascending=False)
    .drop(columns=["meta_valor"])
)

# Export agency credit base
base_agencia.to_excel(
    "/ads_storage/e125316/Scotiabank Caribe/2025/00.Planeacion/04.Metas/S&G_top_mass/results/"
    "20250627_Base_Agencia_S&G_Credito_Top_of_Mass_v5.xlsx",
    index=False,
)


# ============================================================
# SECTION 3 | DEBIT (DÉBITO)
# ============================================================

# ---- 3.1 | Load Data ----

# Segmented debit base: same pipe-delimited format, card_number kept as string
base_debito = pd.read_csv(
    "2025/00.Planeacion/02.Segmentacion_total_base/results/Base_total_debito_segmentada_final.csv",
    sep="|",
    dtype={"card_number": str},
    skipinitialspace=True,
)

# Increment table for debit
incrementos = pd.read_excel(
    "2025/00.Planeacion/04.Metas/S&G_top_mass/input/incrementos.xlsx",
    sheet_name="incremento_debit",
)

# Test/control group assignments for debit customers
grupos_test = pd.read_excel(
    "2025/00.Planeacion/04.Metas/S&G_top_mass/input/Grupos_test_control_SG_Top_mass.xlsx",
    sheet_name="S&G_debit",
)


# ---- 3.2 | Final S&G Debit Base ----

# Keep required columns; rename behavioural pre-campaign columns
# Note: debit base does NOT include limite_credito_usd_total; uses debit_and_credit_card_flag instead
base_sg = base_debito[
    [
        "cust_id", "country", "card_number", "cards",
        "product_code", "amt_6m", "trx_6m", "trx_6m_cof", "trx_6m_cnp", "trx_6m_xb",
        "debit_and_credit_card_flag", "campaign", "segment_name", "SubSegment",
        "uso", "digital", "primacy",
    ]
].rename(columns={
    "uso": "uso_pre_camp",
    "digital": "digital_pre_camp",
    "primacy": "primacy_pre_camp",
})

# Filter S&G campaign; flag == 0 means pure debit customers (no linked credit card)
base_sg = base_sg[
    (base_sg["campaign"] == "S&G")
    & (base_sg["debit_and_credit_card_flag"] == 0)
].copy()

# Attach test/control group flag
base_sg = pd.merge(base_sg, grupos_test, on="cust_id", how="left")
base_sg = base_sg.rename(columns={"group": "test"})

# Attach increment multipliers
base_sg = pd.merge(base_sg, incrementos, how="left")

# Minimum AND maximum spend goals (USD) per country for debit — debit goals are capped
metas_minimas = pd.DataFrame({
    "country":     ["BB",  "BS",  "DO"],
    "meta_minima": [180,   280,   60],
    "meta_maxima": [3500,  4300,  1600],
})
base_sg = pd.merge(base_sg, metas_minimas, on="country", how="left")

# Drop aggregation helper columns if present
base_sg.drop(columns=["Count_of_cust_id", "Average_of_amt_6m"], errors="ignore", inplace=True)

# USD goal: clamped between meta_minima and meta_maxima (debit has a spending cap)
base_sg["meta_usd"] = np.minimum(
    base_sg["meta_maxima"],
    np.maximum(base_sg["amt_6m"] * base_sg["Incremento"], base_sg["meta_minima"]),
)

# Debit uses slightly different exchange rates (JMD 160.5 vs credit's 161.9; DOP 60.85 vs 60.60)
tasas_cambio = pd.DataFrame({
    "country":      ["BB",      "BS",     "JM",   "TT",     "DO"],
    "currency":     ["BBD",     "BSD",    "JMD",  "TTD",    "DOP"],
    "tasa_cambio":  [2.02768,   1.0125,   160.5,  6.7793,   60.85],
})
base_sg = pd.merge(base_sg, tasas_cambio, on="country", how="left")

# Convert goal to local currency (round UP to nearest 100)
base_sg["meta"] = np.ceil(base_sg["meta_usd"] * base_sg["tasa_cambio"] / 100) * 100

# Agency-facing formatted columns
base_sg["meta_agencia"] = base_sg.apply(lambda r: format_currency(r, "meta", 0), axis=1)
base_sg["tasa_agencia"] = base_sg.apply(lambda r: format_currency(r, "tasa_cambio", 2), axis=1)
base_sg["last4"] = base_sg["card_number"].str[-4:]


# ---- Reload corrected S&G debit base (same goal correction as credit) ----

base_sg = pd.read_excel(
    "2025/00.Planeacion/04.Metas/S&G_top_mass/results/20250605_Base_S&G_Debito_Top_of_Mass_final.xlsx"
)

# North countries increment adjustment (+1)
base_sg["Incremento"] = np.where(
    base_sg["country"].isin(["BS", "JM"]),
    base_sg["Incremento"] + 1,
    base_sg["Incremento"],
)

# Recalculate clamped USD goal with updated increments
base_sg["meta_usd"] = np.minimum(
    base_sg["meta_maxima"],
    np.maximum(base_sg["amt_6m"] * base_sg["Incremento"], base_sg["meta_minima"]),
)

# Overwrite exchange rates with corrected official values (same map reused from credit section)
base_sg["tasa_cambio"] = base_sg["country"].map(exchange_rate_map)

# Recompute local-currency goal (round UP to nearest 100)
base_sg["meta"] = np.ceil(base_sg["meta_usd"] * base_sg["tasa_cambio"] / 100) * 100

# Rebuild agency columns with corrected values
base_sg["meta_agencia"] = base_sg.apply(lambda r: format_currency(r, "meta", 0), axis=1)
base_sg["tasa_agencia"] = base_sg.apply(lambda r: format_currency(r, "tasa_cambio", 2), axis=1)
base_sg["last4"] = base_sg["card_number"].str[-4:]

# Export the complete corrected S&G debit base
base_sg.to_excel(
    "/ads_storage/e125316/Scotiabank Caribe/2025/00.Planeacion/04.Metas/S&G_top_mass/results/"
    "20250626_Base_S&G_Debito_Top_of_Mass_final.xlsx",
    index=False,
)


# ---- 3.3 | Agency Base for Debit ----

# Select only the columns the agency needs
base_agencia = base_sg[["cust_id", "country", "test", "meta_agencia", "tasa_agencia", "last4"]].copy()

# Keep only Test group customers
base_agencia = base_agencia[base_agencia["test"] == "Test"].copy()

# Sort from highest to lowest goal value
base_agencia["meta_valor"] = base_agencia["meta_agencia"].apply(
    lambda x: float(re.sub(r"[^0-9.]", "", x)) if pd.notna(x) else 0.0
)
base_agencia = (
    base_agencia
    .sort_values("meta_valor", ascending=False)
    .drop(columns=["meta_valor"])
)

# Export agency debit base
base_agencia.to_excel(
    "/ads_storage/e125316/Scotiabank Caribe/2025/00.Planeacion/04.Metas/S&G_top_mass/results/"
    "20250627_Base_Agencia_S&G_Debito_Top_of_Mass_v3.xlsx",
    index=False,
)
