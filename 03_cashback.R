# ==============================================================================
# 03_cashback.R
# Identifica los clientes que ganaron cashback según el momento de su primer uso
# en relación a los mails de campaña (mail 5, 6 y 7), y exporta la base final.
#
# Prerequisito: correr 02_genera_bases.R primero.
#
# Inputs:
#   - intermediate/emisiones_acumuladas_2.rds  — output de 02_genera_bases.R
#   - fechas_correos_cosechas.xlsx             — fechas de los mails por cosecha
#
# Outputs:
#   - ganadores_cashback/{fecha}_Ganadores_cashback_EMOB_DR.csv
# ==============================================================================

library(dplyr)
library(readxl)
library(lubridate)
library(stringr)

source("config.R")
source("utils.R")


# ---------- 1. Cargar datos ----------------------------------------------------

emisiones_acumuladas_2 <- readRDS(file.path(INTERMEDIATE_PATH, "emisiones_acumuladas_2.rds"))

# Fechas de envío de cada mail por cosecha (join por cosecha)
fechas_correos <- read_excel(
  FECHAS_CORREOS_PATH,
  col_types = c("numeric", "date", "date", "date", "date", "date", "date", "date")
)

# Agregar columna de últimos 4 dígitos (necesaria para el reporte)
emisiones_acumuladas_2 <- emisiones_acumuladas_2 %>%
  mutate(last4_dig = substr(card_number, 13, 16))

# Unir fechas de correos (join por cosecha, columna común entre ambas tablas)
emisiones_acumuladas_2 <- left_join(emisiones_acumuladas_2, fechas_correos)


# ---------- 2. Marcar ventanas de cashback -------------------------------------
# Regla: el cliente gana cashback si hizo su primer uso entre la fecha del
# mail 5 y 15 días después del mail 7. Cada mail define una ventana distinta.

emisiones_acumuladas_2 <- emisiones_acumuladas_2 %>%
  mutate(
    # Ventana mail 5: entre fecha_mail_5 y fecha_mail_6
    prim_uso_m5 = as.integer(!is.na(fecha_primer_uso) &
                               fecha_primer_uso > fecha_mail_5 &
                               fecha_primer_uso < fecha_mail_6),

    # Ventana mail 6: entre fecha_mail_6 y fecha_mail_7
    prim_uso_m6 = as.integer(!is.na(fecha_primer_uso) &
                               fecha_primer_uso > fecha_mail_6 &
                               fecha_primer_uso < fecha_mail_7),

    # Ventana mail 7: entre fecha_mail_7 y 15 días después
    prim_uso_m7 = as.integer(!is.na(fecha_primer_uso) &
                               fecha_primer_uso > fecha_mail_7 &
                               fecha_primer_uso < fecha_mail_7 + days(15)),

    # Gana cashback si cayó en cualquiera de las tres ventanas
    recibe_cashback = as.integer(prim_uso_m5 == 1 | prim_uso_m6 == 1 | prim_uso_m7 == 1)
  )

message(sprintf("Total ganadores de cashback: %d", sum(emisiones_acumuladas_2$recibe_cashback)))


# ---------- 3. Asignar monto de cashback según producto -----------------------

emisiones_acumuladas_2 <- emisiones_acumuladas_2 %>%
  mutate(
    product_name      = standardize_product(product_name),
    monto_cashback_RD = case_when(
      product_name %in% c("MASTERCARD GOLD", "MASTERCARD STANDARD") ~ 500,
      product_name == "MASTERCARD PLATINUM"                          ~ 1000
    )
  )


# ---------- 4. Exportar ganadores ---------------------------------------------

ganadores_cashback <- emisiones_acumuladas_2 %>%
  filter(recibe_cashback == 1) %>%
  select(
    countr.country, cust_id, card_number, product_name,
    fecha_primer_uso, fecha_mail_5, fecha_mail_6, fecha_mail_7,
    prim_uso_m5, prim_uso_m6, prim_uso_m7, monto_cashback_RD
  )

# Vista de verificación antes de limpiar
message("Preview ganadores (primeras filas):")
print(head(ganadores_cashback))

# Base final limpia para enviar al banco
ganadores_cashback_envio <- ganadores_cashback %>%
  select(countr.country, cust_id, card_number, product_name, monto_cashback_RD)

# Nombre del archivo con fecha actual para trazabilidad
fecha_archivo <- format(Sys.Date(), "%Y%m%d")
write.csv(
  ganadores_cashback_envio,
  file      = file.path(CASHBACK_PATH, paste0(fecha_archivo, "_Ganadores_cashback_EMOB_DR.csv")),
  row.names = FALSE
)

message(sprintf("Cashback exportado: %d ganadores", nrow(ganadores_cashback_envio)))
message("Script 03 completado.")
