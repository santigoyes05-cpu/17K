-- ==============================================================================
-- primer_uso.sql
-- Para cada tarjeta de crédito obtiene:
--   - Primera compra global (cualquier modalidad)
--   - Primera compra con tarjeta presente (flag_card_present = 1)
--   - Primera compra sin tarjeta presente (flag_card_present = 0)
--   - Gasto total acumulado en el período
--
-- Optimización vs versión anterior: la tabla fuente se lee UNA sola vez en el
-- CTE "base". Las versiones anteriores hacían 4 lecturas separadas de la misma
-- tabla. El resultado es idéntico.
--
-- Parámetros a ajustar cada corrida:
--   - txn_date >= '2024-06-01'
--   - txn_date <= '2024-12-31'
-- ==============================================================================

SET request_pool = adhoc_small;

WITH base AS (
    -- Lectura única de la tabla fuente con todos los rankings necesarios
    SELECT
        card,
        iss_country_cd,
        txn_date,
        amt_usd,
        industry_code,
        flag_card_present,
        CASE
            WHEN aggregate_name LIKE 'NON-AGGREGATED%' THEN cleansed_merch_name
            ELSE aggregate_name
        END AS merch_name,
        -- Ranking global: 1 = primera transacción de la tarjeta
        ROW_NUMBER() OVER (PARTITION BY card ORDER BY txn_date)                        AS rn_any,
        -- Ranking por modalidad: 1 = primera transacción presente / no presente
        ROW_NUMBER() OVER (PARTITION BY card, flag_card_present ORDER BY txn_date)     AS rn_by_type
    FROM
        coe_enc.e112666_scotia_caribe_txn_2024
    WHERE
        flag_credit = 1
        AND txn_date >= '2024-06-01'
        AND txn_date <= '2024-12-31'
),

first_any AS (
    SELECT card, txn_date, amt_usd, industry_code, merch_name
    FROM base
    WHERE rn_any = 1
),

first_present AS (
    SELECT card, txn_date, amt_usd, industry_code, merch_name
    FROM base
    WHERE rn_by_type = 1 AND flag_card_present = 1
),

first_not_present AS (
    SELECT card, txn_date, amt_usd, industry_code, merch_name
    FROM base
    WHERE rn_by_type = 1 AND flag_card_present = 0
),

spend_total AS (
    SELECT card, SUM(amt_usd) AS spend_total_usd
    FROM base
    GROUP BY card
)

SELECT
    fa.card,
    fa.txn_date                 AS fecha_primer_uso,
    fa.amt_usd                  AS monto_primer_uso,
    fa.industry_code            AS first_purchase_category,
    fa.merch_name               AS first_purchase_merch_name,
    fp.txn_date                 AS fecha_primer_uso_present,
    fp.amt_usd                  AS monto_primer_uso_present,
    fp.industry_code            AS first_purchase_category_present,
    fp.merch_name               AS first_purchase_merch_name_present,
    fnp.txn_date                AS fecha_primer_uso_not_present,
    fnp.amt_usd                 AS monto_primer_uso_not_present,
    fnp.industry_code           AS first_purchase_category_not_present,
    fnp.merch_name              AS first_purchase_merch_name_not_present,
    st.spend_total_usd
FROM
    first_any fa
LEFT JOIN first_present     fp  ON fa.card = fp.card
LEFT JOIN first_not_present fnp ON fa.card = fnp.card
LEFT JOIN spend_total       st  ON fa.card = st.card
