# ==============================================================================
# config.R
# Parámetros centralizados de la corrida semanal EMOB - República Dominicana.
# Editar SOLO esta sección al inicio de cada semana antes de correr los scripts.
# ==============================================================================

# --- Semana y cosechas --------------------------------------------------------
SEMANA         <- "mayo 8"      # Nombre de la subcarpeta en data_banco/
COSECHA_ACTUAL <- "c13_final"   # Cosecha acumulada a leer
COSECHA_NUEVA  <- "c19"         # Cosecha nueva a escribir
MAX_COSECHA    <- 13            # Número máximo de cosecha a incluir en el acumulado

# --- Archivos del banco -------------------------------------------------------
ARCHIVO_EMISION         <- "/dr1.txt"
ARCHIVO_ACTIVACION      <- "/dr2.txt"
ARCHIVO_CONTACTABILIDAD <- "/clients_20241113_.txt"

# --- Exportación MK5 ----------------------------------------------------------
FECHA_EXPORTACION <- "20250509"   # Fecha del archivo MK5 (formato YYYYMMDD)

# --- País a procesar ----------------------------------------------------------
PAIS <- "DO"

# --- Rutas base ---------------------------------------------------------------
BASE_PATH       <- "/ads_storage/e125316/Scotiabank Caribe"
EMOB_PATH       <- file.path(BASE_PATH, "1.empalme/4.EMOB")
DATA_BANCO_PATH <- file.path(EMOB_PATH, "data_banco")
DOM_CRED_PATH   <- file.path(EMOB_PATH, "DOM/credito")

# --- Rutas derivadas (no editar) ----------------------------------------------
ACUMULADAS_PATH  <- file.path(DOM_CRED_PATH, "bases_acumuladas_emision_activacion")
SQL_OUTPUT_PATH  <- file.path(DOM_CRED_PATH, "base_primer_uso_sql")
SEGUIMIENTO_PATH <- file.path(DOM_CRED_PATH, "seguimiento")
AGENCIA_PATH     <- file.path(DOM_CRED_PATH, "bases_semanales_agencia")
CASHBACK_PATH    <- file.path(DOM_CRED_PATH, "ganadores_cashback")
SQL_PATH         <- file.path(DOM_CRED_PATH, "corrida semanal")
INTERMEDIATE_PATH <- file.path(DOM_CRED_PATH, "intermediate")

DICT_INDUSTRIAS_PATH <- file.path(BASE_PATH, "02. Diccionario de industrias/diccionario_industrias_original.csv")
FECHAS_CORREOS_PATH  <- file.path(BASE_PATH, "fechas_correos_cosechas.xlsx")

# --- SQL: archivos de query ---------------------------------------------------
SQL_PRIMER_USO <- file.path(SQL_PATH, "codigo_primer_uso_directo.sql")
SQL_90_DIAS    <- file.path(SQL_PATH, "codigo_90_dias.sql")

# --- Constantes ---------------------------------------------------------------
EXCEL_EPOCH <- "1899-12-30"   # Origen de fechas en Excel (para as.Date)

# --- Configuración Impala -----------------------------------------------------
IMPALA_HOST    <- "dw.prod.impala.mastercard.int:21000"
IMPALA_CA_CERT <- "/sys_apps_01/security/chain.txt"
IMPALA_POOL    <- "adhoc_small"
