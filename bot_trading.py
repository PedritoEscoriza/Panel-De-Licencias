"""
Bot de trading para Binance — base funcional
=============================================

Arranca APAGADO. Cuando lo prendés desde el panel, el bot:
  1) CARGA   → baja datos del mercado (velas, precio, comisiones)
  2) ANALIZA → corre la estrategia sobre esos datos y decide
  3) OPERA   → recién ahí manda órdenes (en modo simulado, órdenes falsas)

El análisis NO es una sola foto: mientras está encendido, el bot vuelve a
cargar y re-analizar en loop cada X segundos.

Modos (bien visibles en el panel):
  • simulado : precios REALES de Binance, pero las órdenes son FALSAS.
               No necesita API keys. Es el modo por defecto y el más seguro.
  • testnet  : Binance Testnet. Órdenes reales pero con plata de mentira.
               Necesita BINANCE_TESTNET_API_KEY / _SECRET en el .env
  • real     : Binance real. PLATA DE VERDAD. Muy protegido, hay que
               activarlo a propósito con BOT_MODO=real y confirmarlo.

Dejá esta ventana abierta y abrí el panel:  http://localhost:5060
"""

from http.server import HTTPServer, BaseHTTPRequestHandler
from dotenv import load_dotenv
import json, os, threading, time, traceback
from datetime import datetime, timezone

import ccxt

load_dotenv()

# ── Configuración general (se puede cambiar por .env) ───────────────────────
MODO        = os.getenv("BOT_MODO", "simulado").lower()   # simulado | testnet | real
SYMBOL      = os.getenv("BOT_SYMBOL", "BTC/USDT")
TIMEFRAME   = os.getenv("BOT_TIMEFRAME", "1m")            # velas de 1 minuto
INTERVALO   = int(os.getenv("BOT_INTERVALO", "10"))       # segundos entre análisis
CAPITAL_INI = float(os.getenv("BOT_CAPITAL", "1000"))     # capital inicial (USDT)
RIESGO_PCT  = float(os.getenv("BOT_RIESGO_PCT", "20"))    # % del capital por operación

# Estrategia (scalping: muchas operaciones, poca ganancia)
EMA_RAPIDA  = int(os.getenv("BOT_EMA_RAPIDA", "9"))
EMA_LENTA   = int(os.getenv("BOT_EMA_LENTA", "21"))
RSI_PERIODO = int(os.getenv("BOT_RSI_PERIODO", "14"))
TAKE_PROFIT = float(os.getenv("BOT_TAKE_PROFIT", "0.4"))  # % de ganancia objetivo
STOP_LOSS   = float(os.getenv("BOT_STOP_LOSS", "0.6"))    # % de pérdida máxima
COMISION    = float(os.getenv("BOT_COMISION", "0.1"))     # % por operación (taker Binance)

PORT = int(os.getenv("BOT_PUERTO", "5060"))
# ────────────────────────────────────────────────────────────────────────────


def ahora():
    return datetime.now(timezone.utc).astimezone().strftime("%H:%M:%S")


# ── Indicadores técnicos (sin librerías extra) ──────────────────────────────
def ema(valores, periodo):
    """Media móvil exponencial."""
    if len(valores) < periodo:
        return None
    k = 2 / (periodo + 1)
    e = sum(valores[:periodo]) / periodo
    for v in valores[periodo:]:
        e = v * k + e * (1 - k)
    return e


def rsi(valores, periodo):
    """Índice de fuerza relativa (0-100)."""
    if len(valores) < periodo + 1:
        return None
    ganancias, perdidas = [], []
    for i in range(1, periodo + 1):
        cambio = valores[-i] - valores[-i - 1]
        (ganancias if cambio >= 0 else perdidas).append(abs(cambio))
    prom_g = sum(ganancias) / periodo if ganancias else 0
    prom_p = sum(perdidas) / periodo if perdidas else 0
    if prom_p == 0:
        return 100.0
    rs = prom_g / prom_p
    return round(100 - (100 / (1 + rs)), 2)


# ── El bot ──────────────────────────────────────────────────────────────────
class BotTrading:
    def __init__(self):
        self.lock = threading.Lock()
        self.hilo = None
        self.encendido = False
        self.reset_estado()

    def reset_estado(self):
        self.fase = "APAGADO"          # APAGADO | CARGANDO | ANALIZANDO | OPERANDO
        self.modo = MODO
        self.symbol = SYMBOL
        self.precio = None
        self.capital = CAPITAL_INI      # USDT disponible
        self.posicion = None            # {'precio_entrada', 'cantidad', 'valor'}
        self.pnl = 0.0                  # ganancia/pérdida realizada de la sesión
        self.comisiones = 0.0           # comisiones acumuladas
        self.operaciones = 0            # cantidad de trades cerrados
        self.ganadoras = 0
        self.indicadores = {}           # ema_rapida, ema_lenta, rsi
        self.pensamiento = "El bot está apagado."
        self.log = []                   # historial de eventos (los últimos)
        self.error = None

    # ---- utilidades de estado ----
    def registrar(self, tipo, texto):
        with self.lock:
            self.log.insert(0, {"hora": ahora(), "tipo": tipo, "texto": texto})
            self.log = self.log[:60]
        print(f"[{ahora()}] {texto}")

    def snapshot(self):
        """Foto del estado para el panel (JSON)."""
        with self.lock:
            valor_pos = None
            if self.posicion and self.precio:
                valor_pos = round(self.posicion["cantidad"] * self.precio, 2)
            return {
                "encendido": self.encendido,
                "fase": self.fase,
                "modo": self.modo,
                "symbol": self.symbol,
                "precio": self.precio,
                "capital": round(self.capital, 2),
                "posicion": self.posicion,
                "valor_posicion": valor_pos,
                "pnl": round(self.pnl, 2),
                "comisiones": round(self.comisiones, 4),
                "operaciones": self.operaciones,
                "ganadoras": self.ganadoras,
                "win_rate": round(self.ganadoras / self.operaciones * 100, 1) if self.operaciones else 0,
                "indicadores": self.indicadores,
                "pensamiento": self.pensamiento,
                "log": self.log,
                "error": self.error,
                "config": {
                    "timeframe": TIMEFRAME,
                    "intervalo": INTERVALO,
                    "take_profit": TAKE_PROFIT,
                    "stop_loss": STOP_LOSS,
                    "comision": COMISION,
                    "ema_rapida": EMA_RAPIDA,
                    "ema_lenta": EMA_LENTA,
                },
            }

    # ---- conexión al exchange ----
    def crear_exchange(self):
        opciones = {"enableRateLimit": True, "options": {"defaultType": "spot"}}
        if self.modo == "testnet":
            opciones["apiKey"] = os.getenv("BINANCE_TESTNET_API_KEY", "")
            opciones["secret"] = os.getenv("BINANCE_TESTNET_API_SECRET", "")
        elif self.modo == "real":
            opciones["apiKey"] = os.getenv("BINANCE_API_KEY", "")
            opciones["secret"] = os.getenv("BINANCE_API_SECRET", "")
        ex = ccxt.binance(opciones)
        if self.modo in ("testnet",):
            ex.set_sandbox_mode(True)
        return ex

    # ---- ciclo de vida ----
    def encender(self):
        with self.lock:
            if self.encendido:
                return False
            self.reset_estado()
            self.encendido = True
        self.hilo = threading.Thread(target=self.correr, daemon=True)
        self.hilo.start()
        return True

    def apagar(self, cerrar_todo=False):
        with self.lock:
            if not self.encendido:
                return False
            self.encendido = False
        # esperar a que el loop termine su vuelta
        if self.hilo:
            self.hilo.join(timeout=INTERVALO + 5)
        if cerrar_todo and self.posicion:
            self.cerrar_posicion("apagado con cierre (botón de pánico)")
        with self.lock:
            self.fase = "APAGADO"
            self.pensamiento = "El bot está apagado."
        self.registrar("info", "🔴 Bot APAGADO.")
        return True

    # ---- loop principal ----
    def correr(self):
        self.registrar("info", f"🟢 Bot ENCENDIDO en modo {self.modo.upper()} — {self.symbol}")
        try:
            exchange = self.crear_exchange()
        except Exception as e:
            self.error = f"No se pudo conectar al exchange: {e}"
            self.registrar("error", f"❌ {self.error}")
            with self.lock:
                self.encendido = False
                self.fase = "APAGADO"
            return

        fallos = 0
        while self.encendido:
            try:
                self.una_vuelta(exchange)
                fallos = 0
                with self.lock:
                    self.error = None
            except ccxt.NetworkError as e:
                fallos += 1
                msg = (f"No hay conexión con Binance ({fallos}º intento). "
                       f"Revisá tu internet o si Binance está disponible en tu país.")
                with self.lock:
                    self.error = msg
                self.registrar("error", f"⚠️ {msg}")
            except Exception as e:
                with self.lock:
                    self.error = f"Error inesperado: {e}"
                self.registrar("error", f"⚠️ Error en el ciclo: {e}")
                traceback.print_exc()
            # esperar el intervalo, pero revisando si nos apagaron
            for _ in range(INTERVALO):
                if not self.encendido:
                    break
                time.sleep(1)

    def una_vuelta(self, exchange):
        # 1) CARGAR ────────────────────────────────────────────────
        with self.lock:
            self.fase = "CARGANDO"
            self.pensamiento = "Cargando datos del mercado (velas y precio)..."
        velas = exchange.fetch_ohlcv(self.symbol, timeframe=TIMEFRAME, limit=100)
        cierres = [v[4] for v in velas]
        precio = cierres[-1]
        with self.lock:
            self.precio = precio

        # 2) ANALIZAR ──────────────────────────────────────────────
        with self.lock:
            self.fase = "ANALIZANDO"
        ema_r = ema(cierres, EMA_RAPIDA)
        ema_l = ema(cierres, EMA_LENTA)
        rsi_v = rsi(cierres, RSI_PERIODO)
        with self.lock:
            self.indicadores = {
                "ema_rapida": round(ema_r, 2) if ema_r else None,
                "ema_lenta": round(ema_l, 2) if ema_l else None,
                "rsi": rsi_v,
            }

        if ema_r is None or ema_l is None or rsi_v is None:
            with self.lock:
                self.pensamiento = "Todavía no hay suficientes datos para decidir. Esperando más velas."
            return

        # 3) DECIDIR / OPERAR ──────────────────────────────────────
        with self.lock:
            self.fase = "OPERANDO"

        tendencia_alcista = ema_r > ema_l
        sobrecomprado = rsi_v > 70
        sobrevendido = rsi_v < 30

        if self.posicion:
            self.gestionar_posicion(precio, tendencia_alcista)
        else:
            # Entrada: tendencia alcista + no sobrecomprado (margen para subir)
            if tendencia_alcista and not sobrecomprado:
                self.abrir_posicion(precio,
                    f"EMA{EMA_RAPIDA} ({ema_r:.2f}) por encima de EMA{EMA_LENTA} ({ema_l:.2f}) "
                    f"y RSI {rsi_v} no está sobrecomprado → hay margen para subir.")
            else:
                razon = "RSI sobrecomprado, espero que baje" if sobrecomprado else \
                        "sin tendencia alcista clara"
                with self.lock:
                    self.pensamiento = f"Me quedo QUIETO: {razon}. (No operar también es una decisión.)"

    # ---- operaciones (modo simulado: se simulan en memoria) ----
    def abrir_posicion(self, precio, razon):
        monto = self.capital * (RIESGO_PCT / 100)
        if monto < 10:
            with self.lock:
                self.pensamiento = "Capital insuficiente para abrir una posición."
            return
        cantidad = monto / precio
        comision = monto * (COMISION / 100)
        with self.lock:
            self.capital -= (monto + comision)
            self.comisiones += comision
            self.posicion = {
                "precio_entrada": precio,
                "cantidad": cantidad,
                "valor": round(monto, 2),
                "hora": ahora(),
            }
            self.pensamiento = f"COMPRÉ porque {razon}"
        self.registrar("compra",
            f"🟩 COMPRA {cantidad:.6f} {self.symbol.split('/')[0]} a ${precio:,.2f} "
            f"(-${comision:.2f} comisión) — {razon}")

    def gestionar_posicion(self, precio, tendencia_alcista):
        entrada = self.posicion["precio_entrada"]
        cambio_pct = (precio - entrada) / entrada * 100
        with self.lock:
            self.pensamiento = (f"Tengo una posición abierta desde ${entrada:,.2f}. "
                                f"Ahora está en {cambio_pct:+.2f}%. "
                                f"Objetivo +{TAKE_PROFIT}% / corte -{STOP_LOSS}%.")
        if cambio_pct >= TAKE_PROFIT:
            self.cerrar_posicion(f"llegó al objetivo de ganancia (+{cambio_pct:.2f}%)", precio)
        elif cambio_pct <= -STOP_LOSS:
            self.cerrar_posicion(f"tocó el stop loss ({cambio_pct:.2f}%)", precio)
        elif not tendencia_alcista:
            self.cerrar_posicion(f"la tendencia se dio vuelta (resultado {cambio_pct:+.2f}%)", precio)

    def cerrar_posicion(self, razon, precio=None):
        if not self.posicion:
            return
        precio = precio or self.precio
        cantidad = self.posicion["cantidad"]
        entrada = self.posicion["precio_entrada"]
        valor_venta = cantidad * precio
        comision = valor_venta * (COMISION / 100)
        ganancia = (precio - entrada) * cantidad - comision
        with self.lock:
            self.capital += (valor_venta - comision)
            self.comisiones += comision
            self.pnl += ganancia
            self.operaciones += 1
            if ganancia > 0:
                self.ganadoras += 1
            self.posicion = None
            self.pensamiento = f"VENDÍ porque {razon}"
        signo = "🟢" if ganancia >= 0 else "🔴"
        self.registrar("venta",
            f"{signo} VENTA a ${precio:,.2f} — {razon}. "
            f"Resultado: ${ganancia:+.2f} (comisión ${comision:.2f})")


bot = BotTrading()


# ── Servidor web (panel + API) ──────────────────────────────────────────────
class Handler(BaseHTTPRequestHandler):

    def _cors(self):
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")

    def _json(self, obj, code=200):
        self.send_response(code)
        self._cors()
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(json.dumps(obj).encode())

    def do_OPTIONS(self):
        self.send_response(200); self._cors(); self.end_headers()

    def do_GET(self):
        if self.path == "/" or self.path == "/panel":
            try:
                with open(os.path.join(os.path.dirname(__file__), "panel_trading.html"), "rb") as f:
                    html = f.read()
                self.send_response(200)
                self._cors()
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.end_headers()
                self.wfile.write(html)
            except FileNotFoundError:
                self.send_response(404); self.end_headers()
        elif self.path == "/estado":
            self._json(bot.snapshot())
        else:
            self.send_response(404); self.end_headers()

    def do_POST(self):
        if self.path == "/encender":
            ok = bot.encender()
            self._json({"ok": ok, "mensaje": "Bot encendido" if ok else "Ya estaba encendido"})
        elif self.path == "/apagar":
            length = int(self.headers.get("Content-Length", 0))
            body = json.loads(self.rfile.read(length)) if length else {}
            cerrar = bool(body.get("cerrar_todo", False))
            ok = bot.apagar(cerrar_todo=cerrar)
            self._json({"ok": ok, "mensaje": "Bot apagado" if ok else "Ya estaba apagado"})
        else:
            self.send_response(404); self.end_headers()

    def log_message(self, format, *args):
        pass  # silenciamos el log HTTP para no ensuciar la consola del bot


if __name__ == "__main__":
    print("=" * 60)
    print("  🤖 BOT DE TRADING — Binance")
    print("=" * 60)
    print(f"  Modo      : {MODO.upper()}", "  ⚠️  PLATA REAL" if MODO == "real" else "")
    print(f"  Par       : {SYMBOL}")
    print(f"  Estrategia: EMA{EMA_RAPIDA}/EMA{EMA_LENTA} + RSI{RSI_PERIODO} "
          f"(scalping, TP +{TAKE_PROFIT}% / SL -{STOP_LOSS}%)")
    print(f"  Panel     : http://localhost:{PORT}")
    print("-" * 60)
    print("  El bot arranca APAGADO. Prendelo desde el panel.")
    print("  Ctrl+C para cerrar el servidor.\n")
    if MODO == "real":
        print("  ⚠️  ⚠️  ⚠️  ESTÁS EN MODO REAL — OPERA CON PLATA DE VERDAD  ⚠️  ⚠️  ⚠️\n")
    HTTPServer(("localhost", PORT), Handler).serve_forever()
