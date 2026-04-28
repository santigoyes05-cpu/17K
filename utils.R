# ==============================================================================
# utils.R
# Funciones compartidas entre los scripts de la corrida semanal EMOB.
# Cargar con: source("utils.R")
# ==============================================================================

library(dplyr)
library(stringr)

# Estandariza product_name a las tres categorías del programa.
# Input:  vector de caracteres con nombre de producto
# Output: vector con "MASTERCARD GOLD", "MASTERCARD PLATINUM", "MASTERCARD STANDARD",
#         o el valor original si no hay match.
standardize_product <- function(x) {
  case_when(
    str_detect(x, "GOLD")     ~ "MASTERCARD GOLD",
    str_detect(x, "PLATINUM") ~ "MASTERCARD PLATINUM",
    str_detect(x, "STANDARD") ~ "MASTERCARD STANDARD",
    TRUE                      ~ x
  )
}

# Convierte un número entero de Excel a fecha R.
# Excel cuenta días desde 1899-12-30; read_excel a veces deja fechas como numeric.
excel_to_date <- function(x) {
  as.Date(x, origin = EXCEL_EPOCH)   # EXCEL_EPOCH viene de config.R
}

# Construye y lanza un comando impala-shell en background (nohup &).
# Input:  sql_file    — ruta al archivo .sql
#         output_file — ruta donde se guardará el CSV resultado
# Output: retorna el código de salida del system()
run_impala <- function(sql_file, output_file) {
  cmd <- sprintf(
    paste0(
      'nohup impala-shell --ssl -k -i %s -B --print_header',
      ' --ca_cert %s -f "%s" --output_file "%s"',
      ' -Q "REQUEST_POOL=%s" -Q "REPLICA_PREFERENCE=CACHE_LOCAL"',
      ' --output_delimiter "|" > nohup.out &'
    ),
    IMPALA_HOST, IMPALA_CA_CERT, sql_file, output_file, IMPALA_POOL
  )
  system(cmd)
}

# Bloquea la ejecución hasta que el archivo exista y tenga contenido.
# Útil para esperar resultados de queries lanzadas con nohup.
# Input:  path         — ruta del archivo a esperar
#         max_wait     — segundos máximos de espera (default: 600)
#         poll_interval — segundos entre chequeos (default: 10)
wait_for_file <- function(path, max_wait = 600, poll_interval = 10) {
  elapsed <- 0
  while (elapsed < max_wait) {
    if (file.exists(path) && file.size(path) > 0) return(invisible(TRUE))
    Sys.sleep(poll_interval)
    elapsed <- elapsed + poll_interval
  }
  stop(sprintf("Timeout: '%s' no se generó en %d segundos.", path, max_wait))
}
