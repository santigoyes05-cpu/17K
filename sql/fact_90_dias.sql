-- ==============================================================================
-- fact_90_dias.sql
-- Para cada tarjeta de crédito calcula el gasto y transacciones acumulados
-- durante los primeros 90 días desde su primera compra.
--
-- Parámetros a ajustar cada corrida:
--   - txn_date BETWEEN '2024-10-01' AND '2026-06-30'
-- ==============================================================================

SET request_pool = adhoc_small;

WITH first_purchase AS (
    -- Primera fecha de uso por tarjeta (mínimo de txn_date)
    SELECT
        card,
        iss_country_cd,
        MIN(txn_date)  AS txn_date,
        SUM(amt_usd)   AS amt_usd
    FROM
        coe_enc.e125316_scotia_cab_all_data_final
    WHERE
        flag_credit = 1
        AND txn_date BETWEEN '2024-10-01' AND '2026-06-30'
    GROUP BY
        card, iss_country_cd
),

spend_90_dias AS (
    -- Gasto acumulado en los 90 días posteriores a la primera compra
    SELECT
        t.card,
        t.iss_country_cd,
        SUM(t.amt_usd)              AS spend_total_usd,
        COUNT(*)                    AS trx,
        SUM(t.iss_pref_txn_amt)     AS spend_total_monedaL,
        COUNT(DISTINCT t.txn_date)  AS dias_transaccionados
    FROM
        coe_enc.e125316_scotia_cab_all_data_final t
    INNER JOIN
        first_purchase fp ON t.card = fp.card
    WHERE
        t.txn_date >= fp.txn_date
        AND t.txn_date < DATE_ADD(fp.txn_date, 90)
    GROUP BY
        t.card, t.iss_country_cd
)

SELECT
    fp.card,
    fp.iss_country_cd,
    fp.txn_date     AS fecha_primer_uso,
    fp.amt_usd      AS monto_primer_uso,
    sa.spend_total_usd      AS spend_usd_90_days,
    sa.trx                  AS trx_90_days,
    sa.spend_total_monedaL  AS spend_monedaL_90_days
FROM
    first_purchase fp
LEFT JOIN
    spend_90_dias sa ON fp.card = sa.card
