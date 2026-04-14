import pandas as pd
from datetime import datetime
from twilio.rest import Client
from dotenv import load_dotenv
import os

# Cargar credenciales desde .env
load_dotenv()

ACCOUNT_SID = os.getenv("TWILIO_ACCOUNT_SID")
AUTH_TOKEN  = os.getenv("TWILIO_AUTH_TOKEN")
TWILIO_FROM = os.getenv("TWILIO_WHATSAPP_FROM")  # ej: whatsapp:+14155238886

client = Client(ACCOUNT_SID, AUTH_TOKEN)

# ── Configuración ──────────────────────────────────────────────
ARCHIVO_EXCEL = "clientes.xlsx"
DIAS_AVISO    = 7   # avisar cuando faltan 7 días o menos
# ───────────────────────────────────────────────────────────────

def armar_mensaje(nombre, fecha_vencimiento, dias_restantes):
    fecha_str = fecha_vencimiento.strftime("%d/%m/%Y")
    if dias_restantes == 0:
        urgencia = "¡Vence HOY!"
    elif dias_restantes == 1:
        urgencia = "¡Vence MAÑANA!"
    else:
        urgencia = f"Vence en {dias_restantes} días ({fecha_str})"
    return (
        f"Hola {nombre}! 👋\n\n"
        f"Te avisamos que tu licencia está por vencer.\n"
        f"📅 {urgencia}\n\n"
        f"Contactanos para renovarla y no perder el acceso.\n"
        f"¡Gracias! 🙌"
    )

def main():
    print("📂 Leyendo archivo Excel...")
    df = pd.read_excel(ARCHIVO_EXCEL)

    # Verificar columnas necesarias
    columnas_requeridas = ["Nombre", "Teléfono", "Fecha Vencimiento"]
    for col in columnas_requeridas:
        if col not in df.columns:
            print(f"❌ Falta la columna '{col}' en el Excel.")
            return

    hoy = datetime.today().replace(hour=0, minute=0, second=0, microsecond=0)
    enviados = 0
    omitidos = 0

    for _, row in df.iterrows():
        nombre     = str(row["Nombre"]).strip()
        telefono   = str(row["Teléfono"]).strip()
        vencimiento = pd.to_datetime(row["Fecha Vencimiento"])
        dias_restantes = (vencimiento - hoy).days

        if 0 <= dias_restantes <= DIAS_AVISO:
            # Formatear número argentino: agregar código de país si falta
            if not telefono.startswith("+"):
                telefono = "+549" + telefono  # código Argentina

            numero_wa = f"whatsapp:{telefono}"
            mensaje   = armar_mensaje(nombre, vencimiento, dias_restantes)

            try:
                client.messages.create(
                    body=mensaje,
                    from_=TWILIO_FROM,
                    to=numero_wa
                )
                print(f"✅ Mensaje enviado a {nombre} ({telefono})")
                enviados += 1
            except Exception as e:
                print(f"❌ Error enviando a {nombre}: {e}")
        else:
            omitidos += 1

    print(f"\n📊 Resultado: {enviados} mensajes enviados, {omitidos} clientes sin aviso pendiente.")

if __name__ == "__main__":
    main()
