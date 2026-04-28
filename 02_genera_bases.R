# ==============================================================================
# 02_genera_bases.R
# Pipeline principal semanal: carga datos del banco, actualiza acumulados,
# marca activación y primer uso, cruza industrias, y exporta todos los outputs.
#
# Prerequisito: correr 01_extraer_impala.R primero.
#
# Inputs:
#   - data_banco/{SEMANA}/dr1.txt                     — emisiones de la semana
#   - data_banco/{SEMANA}/dr2.txt                     — activaciones de la semana
#   - bases_acumuladas/emisiones_acumuladas_{COSECHA}  — acumulado previo
#   - bases_acumuladas/activaciones_acumuladas_{COSECHA}
#   - base_primer_uso_sql/primer_uso.csv              — output de Impala
#   - diccionario_industrias_original.csv
#   - data_banco/{SEMANA}/contacts.txt                — contactabilidad
#
# Outputs:
#   - emisiones_acumuladas_{COSECHA_NUEVA}.xlsx
#   - activaciones_acumuladas_{COSECHA_NUEVA}.xlsx
#   - {SEMANA}_seguimiento.xlsx
#   - DO_EMOB_credit_{FECHA}.xlsx  (base MK5 para agencia)
#   - intermediate/emisiones_acumuladas_2.rds  (para 03_cashback.R)
# ==============================================================================

library(data.table)
library(dplyr)
library(readxl)
library(readr)
library(openxlsx)
library(stringr)
library(lubridate)

source("config.R")
source("utils.R")


# ---------- 1. Cargar archivos del banco ---------------------------------------

emisiones_semana <- read_delim(
  file.path(DATA_BANCO_PATH, SEMANA, ARCHIVO_EMISION),
  delim = "\t", escape_double = FALSE, trim_ws = TRUE
)

activaciones_semana <- read_delim(
  file.path(DATA_BANCO_PATH, SEMANA, ARCHIVO_ACTIVACION),
  delim = "\t", escape_double = FALSE, trim_ws = TRUE
)

# Diagnóstico: verificar qué países y productos vienen
message("Países en emisiones:")
print(count(emisiones_semana, countr.country))
message("Países en activaciones:")
print(count(activaciones_semana, countr.country))
message("Productos en emisiones:")
print(table(emisiones_semana$product_name))

# Filtrar solo República Dominicana
emisiones_semana    <- filter(emisiones_semana,    countr.country == PAIS)
activaciones_semana <- filter(activaciones_semana, countr.country == PAIS)


# ---------- 2. Cargar acumulados previos ---------------------------------------

emisiones_acumuladas   <- read_excel(file.path(ACUMULADAS_PATH, paste0("emisiones_acumuladas_",   COSECHA_ACTUAL, ".xlsx")))
activaciones_acumuladas <- read_excel(file.path(ACUMULADAS_PATH, paste0("activaciones_acumuladas_", COSECHA_ACTUAL, ".xlsx")))

# Las fechas del Excel vienen como número entero; convertir al formato correcto
emisiones_acumuladas <- emisiones_acumuladas %>%
  mutate(
    semana              = excel_to_date(semana),
    tcp.activation_date = excel_to_date(tcp.activation_date)
  )

activaciones_acumuladas <- activaciones_acumuladas %>%
  mutate(
    tcp.activation_date = excel_to_date(tcp.activation_date),
    card_issue_date     = excel_to_date(card_issue_date)
  )

# Diagnóstico: rangos de fecha para validar solapamiento
message(sprintf("Acumulado emisiones:  %s  a  %s", min(emisiones_acumuladas$card_issue_date), max(emisiones_acumuladas$card_issue_date)))
message(sprintf("Semana nueva:         %s  a  %s", min(emisiones_semana$card_issue_date),      max(emisiones_semana$card_issue_date)))
message(sprintf("País en acumulado: %s", paste(unique(emisiones_acumuladas$countr.country), collapse = ", ")))


# ---------- 3. Agregar semana nueva al acumulado -------------------------------

# Asignar número de cosecha y fecha de ejecución
emisiones_semana <- emisiones_semana %>%
  rename(delivery_date = tcp.delivery_date) %>%
  mutate(
    cosecha = max(emisiones_acumuladas$cosecha) + 1,
    semana  = Sys.Date()
  )

# Quitar tarjetas que ya existen en el acumulado (duplicados entre semanas)
n_repetidos <- sum(emisiones_semana$card_number %in% emisiones_acumuladas$card_number)
if (n_repetidos > 0) message(sprintf("Advertencia: %d tarjetas repetidas eliminadas.", n_repetidos))
emisiones_semana <- filter(emisiones_semana, !card_number %in% emisiones_acumuladas$card_number)

# Unir nueva semana con acumulados
emisiones_acumuladas    <- bind_rows(emisiones_acumuladas,    emisiones_semana)
activaciones_acumuladas <- bind_rows(activaciones_acumuladas, activaciones_semana)

# Aplicar corte: solo mantener cosechas dentro del rango definido
emisiones_acumuladas <- filter(emisiones_acumuladas, cosecha %in% seq_len(MAX_COSECHA))

message(sprintf("Cosechas en acumulado tras corte: %s", paste(sort(unique(emisiones_acumuladas$cosecha)), collapse = ", ")))


# ---------- 4. Calcular semanas en campaña, filtrar staff, marcar activación ---

emisiones_acumuladas <- emisiones_acumuladas %>%
  mutate(semanas_en_campaña = as.numeric(difftime(Sys.Date(), semana, units = "weeks")))

# Excluir tarjetas de empleados
emisiones_acumuladas <- filter(emisiones_acumuladas, staff_flag == 0)

# Marcar si la tarjeta aparece en la base de activaciones
emisiones_acumuladas <- emisiones_acumuladas %>%
  mutate(activado = as.integer(card_number %in% activaciones_acumuladas$card_number))

message(sprintf("Tarjetas activadas (base activaciones): %d", sum(emisiones_acumuladas$activado)))


# ---------- 5. Cruzar primer uso transaccional ---------------------------------

df_primer_uso <- read_delim(
  file.path(SQL_OUTPUT_PATH, "primer_uso.csv"),
  delim = "|", escape_double = FALSE,
  col_types = cols(card = col_character()),
  trim_ws = TRUE
)

# El número de tarjeta en Impala tiene sufijo "000" adicional
emisiones_acumuladas <- emisiones_acumuladas %>%
  mutate(
    card      = paste0(card_number, "000"),
    primer_uso = as.integer(card %in% df_primer_uso$card),
    # Si tiene primer uso registrado, considerarlo activado también
    activado  = as.integer(activado == 1 | primer_uso == 1)
  ) %>%
  left_join(df_primer_uso, by = "card")

message(sprintf("Tarjetas con primer uso: %d", sum(emisiones_acumuladas$primer_uso)))


# ---------- 6. Cruzar diccionario de industrias --------------------------------

industrias <- fread(DICT_INDUSTRIAS_PATH) %>%
  select(industry_cd, super_industry, industry) %>%
  distinct()

# Pegar nombre de super_industry e industry para cada una de las tres categorías
emisiones_acumuladas_2 <- emisiones_acumuladas %>%
  left_join(industrias, by = c("first_purchase_category" = "industry_cd")) %>%
  select(-first_purchase_category) %>%
  rename(first_purchase_category = super_industry, first_purchase_subcategory = industry) %>%

  left_join(industrias, by = c("first_purchase_category_present" = "industry_cd")) %>%
  select(-first_purchase_category_present) %>%
  rename(first_purchase_category_present = super_industry, first_purchase_subcategory_present = industry) %>%

  left_join(industrias, by = c("first_purchase_category_not_present" = "industry_cd")) %>%
  select(-first_purchase_category_not_present) %>%
  rename(first_purchase_category_not_present = super_industry, first_purchase_subcategory_not_present = industry)


# ---------- 7. Guardar acumulados actualizados ---------------------------------

write.xlsx(emisiones_acumuladas,    file.path(ACUMULADAS_PATH, paste0("emisiones_acumuladas_",    COSECHA_NUEVA, ".xlsx")))
write.xlsx(activaciones_acumuladas, file.path(ACUMULADAS_PATH, paste0("activaciones_acumuladas_", COSECHA_NUEVA, ".xlsx")))

# Copia con nombre fijo (convención histórica del proyecto)
write.xlsx(emisiones_acumuladas,    file.path(ACUMULADAS_PATH, "emisiones_acumuladas_c13_final.xlsx"))
write.xlsx(activaciones_acumuladas, file.path(ACUMULADAS_PATH, "activaciones_acumuladas_c13_final.xlsx"))

# Archivo de seguimiento semanal completo
dq::save_to_excel(
  emisiones_acumuladas_2,
  filename = file.path(SEGUIMIENTO_PATH, paste0(SEMANA, "_seguimiento"))
)


# ---------- 8. Base para agencia MK5 ------------------------------------------

base_banco <- emisiones_acumuladas %>%
  filter(countr.country == PAIS) %>%
  mutate(
    last4_dig         = substr(card_number, 13, 16),
    product_name_real = product_name,
    product_name      = standardize_product(product_name)
  ) %>%
  select(countr.country, cust_id, cosecha, activado, primer_uso,
         product_name, product_name_real, last4_dig)

if (nrow(base_banco) > 0) {
  carpeta_mk5 <- file.path(AGENCIA_PATH, SEMANA)
  dir.create(carpeta_mk5, showWarnings = FALSE, recursive = TRUE)
  write.xlsx(
    base_banco,
    file     = file.path(carpeta_mk5, paste0("DO_EMOB_credit_", FECHA_EXPORTACION, ".xlsx")),
    rowNames = FALSE
  )
  message(sprintf("Base MK5 guardada: %d filas", nrow(base_banco)))
}


# ---------- 9. Check de contactabilidad (diagnóstico) -------------------------

contactabilidad <- read_csv(file.path(DATA_BANCO_PATH, SEMANA, ARCHIVO_CONTACTABILIDAD))

ids_ultima_cosecha <- filter(base_banco, cosecha == MAX_COSECHA)$cust_id
n_match <- sum(contactabilidad$cust_id %in% ids_ultima_cosecha)

message(sprintf(
  "Contactabilidad: %d en base banco última cosecha | %d con match | %d sin match",
  length(ids_ultima_cosecha), n_match, length(ids_ultima_cosecha) - n_match
))


# ---------- 10. Guardar intermedio para script de cashback ---------------------

dir.create(INTERMEDIATE_PATH, showWarnings = FALSE, recursive = TRUE)
saveRDS(emisiones_acumuladas_2, file.path(INTERMEDIATE_PATH, "emisiones_acumuladas_2.rds"))

message("Script 02 completado. Puede continuar con 03_cashback.R")
