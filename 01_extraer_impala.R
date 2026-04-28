# ==============================================================================
# 01_extraer_impala.R
# Lanza las queries de Impala en paralelo y espera a que ambas terminen.
#
# Inputs:  SQL_PRIMER_USO  (config.R) — query de primer uso
#          SQL_90_DIAS     (config.R) — query de facto 90 días
# Outputs: {SQL_OUTPUT_PATH}/primer_uso.csv
#          {SQL_OUTPUT_PATH}/fact_90_dias.csv
#
# NOTA: Este script debe correrse ANTES de 02_genera_bases.R.
#       Las queries pueden tardar varios minutos dependiendo del volumen.
# ==============================================================================

source("config.R")
source("utils.R")

out_primer_uso <- file.path(SQL_OUTPUT_PATH, "primer_uso.csv")
out_90_dias    <- file.path(SQL_OUTPUT_PATH, "fact_90_dias.csv")

# Lanzar ambas queries en paralelo (nohup &)
message("Lanzando query primer uso...")
run_impala(SQL_PRIMER_USO, out_primer_uso)

message("Lanzando query 90 días...")
run_impala(SQL_90_DIAS, out_90_dias)

# Esperar resultados (máx 10 minutos cada una)
message("Esperando resultados de Impala...")
wait_for_file(out_primer_uso)
message("primer_uso.csv listo.")
wait_for_file(out_90_dias)
message("fact_90_dias.csv listo.")

message("Extracción completada. Puede continuar con 02_genera_bases.R")
