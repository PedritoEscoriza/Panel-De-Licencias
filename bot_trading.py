"""
Bot de trading para Binance — Técnico + Noticias
=================================================

Arranca APAGADO y se controla desde el panel web. Vos solo lo prendés:
el bot se configura todo solo. Cuando lo encendés, en cada vuelta hace:

  1) CARGA   → baja velas del mercado + titulares de noticias de cripto
  2) ANALIZA → corre DOS análisis en paralelo:
        • Técnico  : confluencia de EMA + RSI + MACD sobre el precio
        • Noticias : mide el sentimiento (alcista/bajista) de los titulares
  3) DECIDE  → SOLO opera si los dos coinciden y NO se contradicen.
               Si el técnico dice una cosa y las noticias la contraria,
               o si alguno no está claro → se queda QUIETO.

El análisis corre en loop mientras el bot está encendido (no es una foto).

Modos (bien visibles en el panel):
  • simulado : precios y noticias REALES, pero órdenes FALSAS. Sin API keys.
               Es el modo por defecto y el más seguro. Empezá siempre acá.
  • testnet  : Binance Testnet (plata de mentira, necesita keys de testnet).
  • real     : Binance real (PLATA DE VERDAD). Activar a propósito.

Panel:  http://localhost:5060
"""

from http.server import HTTPServer, BaseHTTPRequestHandler
from dotenv import load_dotenv
import json, os, threading, time, traceback, urllib.request
from datetime import datetime, timezone
from xml.etree import ElementTree as ET

import ccxt

load_dotenv()

# ── Configuración (el bot funciona sin tocar nada de esto) ──────────────────
MODO        = os.getenv("BOT_MODO", "simulado").lower()   # simulado | testnet | real
SYMBOL      = os.getenv("BOT_SYMBOL", "BTC/USDT")
TIMEFRAME   = os.getenv("BOT_TIMEFRAME", "5m")
INTERVALO   = int(os.getenv("BOT_INTERVALO", "15"))       # segundos entre análisis
CAPITAL_INI = float(os.getenv("BOT_CAPITAL", "1000"))     # capital simulado (USDT)
RIESGO_PCT  = float(os.getenv("BOT_RIESGO_PCT", "20"))    # % del capital por operación
NOTICIAS_CADA = int(os.getenv("BOT_NOTICIAS_CADA", "300"))  # refrescar noticias cada X seg

# Estrategia técnica
EMA_RAPIDA  = int(os.getenv("BOT_EMA_RAPIDA", "9"))
EMA_LENTA   = int(os.getenv("BOT_EMA_LENTA", "21"))
RSI_PERIODO = int(os.getenv("BOT_RSI_PERIODO", "14"))
TAKE_PROFIT = float(os.getenv("BOT_TAKE_PROFIT", "0.8"))  # % ganancia objetivo
STOP_LOSS   = float(os.getenv("BOT_STOP_LOSS", "1.0"))    # % pérdida máxima
COMISION    = float(os.getenv("BOT_COMISION", "0.1"))     # % por operación

PORT = int(os.getenv("BOT_PUERTO", "5060"))

# Fuentes de noticias (RSS público, sin API key)
FEEDS = [
    "https://cointelegraph.com/rss",
    "https://www.coindesk.com/arc/outboundfeeds/rss/",
    "https://cryptopotato.com/feed/",
]

# Léxico de sentimiento (las noticias suelen estar en inglés)
PALABRAS_ALCISTAS = {
    "surge", "surges", "rally", "rallies", "soar", "soars", "gains", "gain",
    "bull", "bullish", "breakout", "adoption", "approval", "approved", "etf",
    "record", "high", "jump", "jumps", "rise", "rises", "climb", "climbs",
    "upgrade", "partnership", "institutional", "buy", "buying", "accumulate",
    "recovery", "rebound", "boom", "optimism", "greed", "inflow", "inflows",
    "milestone", "support", "positive", "outperform", "surge",
}
PALABRAS_BAJISTAS = {
    "crash", "crashes", "plunge", "plunges", "dump", "dumps", "drop", "drops",
    "fall", "falls", "bear", "bearish", "selloff", "sell-off", "hack", "hacked",
    "exploit", "ban", "banned", "lawsuit", "charges", "decline", "declines",
    "fear", "liquidation", "liquidations", "collapse", "warning", "fraud",
    "scam", "tumble", "slump", "downgrade", "fine", "crackdown", "outflow",
    "outflows", "sell", "selling", "loss", "losses", "negative", "risk", "crisis",
}
# ────────────────────────────────────────────────────────────────────────────


def ahora():
    return datetime.now(timezone.utc).astimezone().strftime("%H:%M:%S")


# ── Indicadores técnicos (sin librerías extra) ──────────────────────────────
def ema(valores, periodo):
    if len(valores) < periodo:
        return None
    k = 2 / (periodo + 1)
    e = sum(valores[:periodo]) / periodo
    for v in valores[periodo:]:
        e = v * k + e * (1 - k)
    return e


def serie_ema(valores, periodo):
    """Devuelve la serie completa de EMA (para calcular el MACD)."""
    if len(valores) < periodo:
        return []
    k = 2 / (periodo + 1)
    e = sum(valores[:periodo]) / periodo
    serie = [e]
    for v in valores[periodo:]:
        e = v * k + e * (1 - k)
        serie.append(e)
    return serie


def rsi(valores, periodo):
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


def macd(valores, rapida=12, lenta=26, senal=9):
    """Devuelve (macd_line, signal_line, histograma) o (None, None, None)."""
    if len(valores) < lenta + senal:
        return None, None, None
    ema_r = serie_ema(valores, rapida)
    ema_l = serie_ema(valores, lenta)
    n = min(len(ema_r), len(ema_l))
    macd_line = [ema_r[-n + i] - ema_l[-n + i] for i in range(n)]
    if len(macd_line) < senal:
        return None, None, None
    signal = serie_ema(macd_line, senal)
    hist = macd_line[-1] - signal[-1]
    return round(macd_line[-1], 2), round(signal[-1], 2), round(hist, 2)


# ── Análisis de noticias ────────────────────────────────────────────────────
def bajar_titulares(limite=12):
    """Baja titulares recientes de los feeds RSS. Devuelve lista de textos."""
    titulares = []
    for url in FEEDS:
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
            with urllib.request.urlopen(req, timeout=8) as r:
                raiz = ET.fromstring(r.read())
            for item in raiz.iter("item"):
                t = item.findtext("title")
                if t:
                    titulares.append(t.strip())
                if len(titulares) >= limite * len(FEEDS):
                    break
        except Exception:
            continue  # si un feed falla, seguimos con los otros
    return titulares[: limite]


def sentimiento_texto(texto):
    """Puntaje de un titular: (+) alcista, (-) bajista, 0 neutral."""
    palabras = "".join(c.lower() if c.isalnum() or c == "-" else " " for c in texto).split()
    a = sum(1 for p in palabras if p in PALABRAS_ALCISTAS)
    b = sum(1 for p in palabras if p in PALABRAS_BAJISTAS)
    return a - b


def analizar_noticias():
    """Devuelve dict con veredicto, puntaje y titulares con su sentimiento."""
    titulares = bajar_titulares()
    if not titulares:
        return {"veredicto": "SIN DATOS", "puntaje": 0, "titulares": [],
                "resumen": "No se pudieron bajar noticias (sin conexión o feeds caídos)."}
    detalle, total = [], 0
    for t in titulares:
        s = sentimiento_texto(t)
        total += s
        etiqueta = "alcista" if s > 0 else "bajista" if s < 0 else "neutral"
        detalle.append({"titulo": t, "sent": etiqueta})
    if total >= 2:
        veredicto = "ALCISTA"
    elif total <= -2:
        veredicto = "BAJISTA"
    else:
        veredicto = "NEUTRAL"
    return {"veredicto": veredicto, "puntaje": total, "titulares": detalle,
            "resumen": f"{len(titulares)} titulares analizados, puntaje neto {total:+d}."}


# ── El bot ──────────────────────────────────────────────────────────────────
class BotTrading:
    def __init__(self):
        self.lock = threading.Lock()
        self.hilo = None
        self.encendido = False
        self.reset_estado()

    def reset_estado(self):
        self.fase = "APAGADO"
        self.modo = MODO
        self.symbol = SYMBOL
        self.precio = None
        self.capital = CAPITAL_INI
        self.posicion = None
        self.pnl = 0.0
        self.comisiones = 0.0
        self.operaciones = 0
        self.ganadoras = 0
        self.indicadores = {}
        self.tecnico = {"veredicto": "—", "motivos": []}
        self.noticias = {"veredicto": "—", "puntaje": 0, "titulares": [], "resumen": ""}
        self.decision = "—"
        self.pensamiento = "El bot está apagado."
        self.log = []
        self.error = None
        self._ultima_noticia = 0

    def registrar(self, tipo, texto):
        with self.lock:
            self.log.insert(0, {"hora": ahora(), "tipo": tipo, "texto": texto})
            self.log = self.log[:60]
        print(f"[{ahora()}] {texto}")

    def snapshot(self):
        with self.lock:
            valor_pos = None
            if self.posicion and self.precio:
                valor_pos = round(self.posicion["cantidad"] * self.precio, 2)
            return {
                "encendido": self.encendido, "fase": self.fase, "modo": self.modo,
                "symbol": self.symbol, "precio": self.precio,
                "capital": round(self.capital, 2), "posicion": self.posicion,
                "valor_posicion": valor_pos, "pnl": round(self.pnl, 2),
                "comisiones": round(self.comisiones, 4), "operaciones": self.operaciones,
                "ganadoras": self.ganadoras,
                "win_rate": round(self.ganadoras / self.operaciones * 100, 1) if self.operaciones else 0,
                "indicadores": self.indicadores, "tecnico": self.tecnico,
                "noticias": self.noticias, "decision": self.decision,
                "pensamiento": self.pensamiento, "log": self.log, "error": self.error,
                "config": {"timeframe": TIMEFRAME, "intervalo": INTERVALO,
                           "take_profit": TAKE_PROFIT, "stop_loss": STOP_LOSS,
                           "comision": COMISION},
            }

    def crear_exchange(self):
        opciones = {"enableRateLimit": True, "options": {"defaultType": "spot"}}
        if self.modo == "testnet":
            opciones["apiKey"] = os.getenv("BINANCE_TESTNET_API_KEY", "")
            opciones["secret"] = os.getenv("BINANCE_TESTNET_API_SECRET", "")
        elif self.modo == "real":
            opciones["apiKey"] = os.getenv("BINANCE_API_KEY", "")
            opciones["secret"] = os.getenv("BINANCE_API_SECRET", "")
        ex = ccxt.binance(opciones)
        if self.modo == "testnet":
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
        if self.hilo:
            self.hilo.join(timeout=INTERVALO + 12)
        if cerrar_todo and self.posicion:
            self.cerrar_posicion("apagado con cierre (botón de pánico)")
        with self.lock:
            self.fase = "APAGADO"
            self.pensamiento = "El bot está apagado."
        self.registrar("info", "🔴 Bot APAGADO.")
        return True

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
            except ccxt.NetworkError:
                fallos += 1
                msg = (f"No hay conexión con Binance ({fallos}º intento). "
                       f"Revisá tu internet o si Binance opera en tu país.")
                with self.lock:
                    self.error = msg
                self.registrar("error", f"⚠️ {msg}")
            except Exception as e:
                with self.lock:
                    self.error = f"Error inesperado: {e}"
                self.registrar("error", f"⚠️ Error en el ciclo: {e}")
                traceback.print_exc()
            for _ in range(INTERVALO):
                if not self.encendido:
                    break
                time.sleep(1)

    # ---- una vuelta: cargar → analizar → decidir ----
    def una_vuelta(self, exchange):
        # 1) CARGAR ─────────────────────────────────────────────
        with self.lock:
            self.fase = "CARGANDO"
            self.pensamiento = "Cargando velas del mercado y titulares de noticias..."
        velas = exchange.fetch_ohlcv(self.symbol, timeframe=TIMEFRAME, limit=120)
        cierres = [v[4] for v in velas]
        precio = cierres[-1]
        with self.lock:
            self.precio = precio

        # Noticias: se refrescan cada NOTICIAS_CADA segundos (no en cada vuelta)
        if time.time() - self._ultima_noticia > NOTICIAS_CADA or not self.noticias["titulares"]:
            noticias = analizar_noticias()
            with self.lock:
                self.noticias = noticias
                self._ultima_noticia = time.time()

        # 2) ANALIZAR ───────────────────────────────────────────
        with self.lock:
            self.fase = "ANALIZANDO"
        tecnico = self.analizar_tecnico(cierres)
        with self.lock:
            self.tecnico = tecnico
        veredicto_noticias = self.noticias["veredicto"]

        # 3) DECIDIR / OPERAR ───────────────────────────────────
        with self.lock:
            self.fase = "OPERANDO"
        self.decidir(precio, tecnico["veredicto"], veredicto_noticias)

    def analizar_tecnico(self, cierres):
        """Confluencia de EMA + RSI + MACD → ALCISTA / BAJISTA / NEUTRAL."""
        ema_r = ema(cierres, EMA_RAPIDA)
        ema_l = ema(cierres, EMA_LENTA)
        rsi_v = rsi(cierres, RSI_PERIODO)
        macd_l, signal_l, hist = macd(cierres)
        with self.lock:
            self.indicadores = {
                "ema_rapida": round(ema_r, 2) if ema_r else None,
                "ema_lenta": round(ema_l, 2) if ema_l else None,
                "rsi": rsi_v, "macd": macd_l, "macd_signal": signal_l, "macd_hist": hist,
            }
        if None in (ema_r, ema_l, rsi_v, hist):
            return {"veredicto": "SIN DATOS", "motivos": ["Faltan velas para calcular indicadores."]}

        puntos, motivos = 0, []
        # EMA (tendencia)
        if ema_r > ema_l:
            puntos += 1; motivos.append(f"EMA{EMA_RAPIDA} > EMA{EMA_LENTA} (tendencia alcista)")
        else:
            puntos -= 1; motivos.append(f"EMA{EMA_RAPIDA} < EMA{EMA_LENTA} (tendencia bajista)")
        # RSI (momentum, evitando extremos)
        if rsi_v > 70:
            puntos -= 1; motivos.append(f"RSI {rsi_v} sobrecomprado (riesgo de caída)")
        elif rsi_v < 30:
            puntos -= 1; motivos.append(f"RSI {rsi_v} sobrevendido (sin fuerza compradora)")
        elif rsi_v > 50:
            puntos += 1; motivos.append(f"RSI {rsi_v} con momentum alcista")
        else:
            motivos.append(f"RSI {rsi_v} neutro")
        # MACD (impulso)
        if hist > 0:
            puntos += 1; motivos.append("MACD por encima de su señal (impulso alcista)")
        else:
            puntos -= 1; motivos.append("MACD por debajo de su señal (impulso bajista)")

        veredicto = "ALCISTA" if puntos >= 2 else "BAJISTA" if puntos <= -1 else "NEUTRAL"
        return {"veredicto": veredicto, "motivos": motivos, "puntos": puntos}

    def decidir(self, precio, tec, noti):
        """Regla central: operar SOLO si técnico y noticias coinciden."""
        # Si ya hay posición abierta, gestionar salida
        if self.posicion:
            self.gestionar_posicion(precio, tec, noti)
            return

        coinciden_alcista = (tec == "ALCISTA" and noti == "ALCISTA")

        if coinciden_alcista:
            self.abrir_posicion(precio,
                "el análisis técnico Y las noticias coinciden en ALCISTA (no se contradicen)")
        else:
            # explicar por qué NO opera
            if tec == "ALCISTA" and noti == "BAJISTA":
                razon = "el técnico dice ALCISTA pero las noticias dicen BAJISTA → se contradicen"
            elif tec == "BAJISTA" and noti == "ALCISTA":
                razon = "las noticias dicen ALCISTA pero el técnico dice BAJISTA → se contradicen"
            elif noti in ("NEUTRAL", "SIN DATOS"):
                razon = f"las noticias no dan una señal clara ({noti.lower()})"
            elif tec in ("NEUTRAL", "SIN DATOS"):
                razon = f"el técnico no da una señal clara ({tec.lower()})"
            else:
                razon = f"técnico {tec} y noticias {noti} no coinciden en ALCISTA"
            with self.lock:
                self.decision = "NO OPERAR"
                self.pensamiento = (f"Me quedo QUIETO porque {razon}. "
                                    f"Solo compro cuando técnico y noticias están de acuerdo.")

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
            self.posicion = {"precio_entrada": precio, "cantidad": cantidad,
                             "valor": round(monto, 2), "hora": ahora()}
            self.decision = "COMPRAR"
            self.pensamiento = f"COMPRÉ porque {razon}."
        self.registrar("compra",
            f"🟩 COMPRA {cantidad:.6f} {self.symbol.split('/')[0]} a ${precio:,.2f} "
            f"(-${comision:.2f} comisión) — {razon}.")

    def gestionar_posicion(self, precio, tec, noti):
        entrada = self.posicion["precio_entrada"]
        cambio_pct = (precio - entrada) / entrada * 100
        with self.lock:
            self.decision = "EN POSICIÓN"
            self.pensamiento = (f"Posición abierta desde ${entrada:,.2f}, ahora {cambio_pct:+.2f}%. "
                                f"Objetivo +{TAKE_PROFIT}% / corte -{STOP_LOSS}%.")
        if cambio_pct >= TAKE_PROFIT:
            self.cerrar_posicion(f"llegó al objetivo de ganancia (+{cambio_pct:.2f}%)", precio)
        elif cambio_pct <= -STOP_LOSS:
            self.cerrar_posicion(f"tocó el stop loss ({cambio_pct:.2f}%)", precio)
        elif tec == "BAJISTA" or noti == "BAJISTA":
            quien = "el técnico" if tec == "BAJISTA" else "las noticias"
            self.cerrar_posicion(f"{quien} se dio vuelta a BAJISTA (resultado {cambio_pct:+.2f}%)", precio)

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
            self.decision = "VENDER"
            self.pensamiento = f"VENDÍ porque {razon}."
        signo = "🟢" if ganancia >= 0 else "🔴"
        self.registrar("venta",
            f"{signo} VENTA a ${precio:,.2f} — {razon}. "
            f"Resultado: ${ganancia:+.2f} (comisión ${comision:.2f}).")


bot = BotTrading()


# ── Servidor web (panel + API) ──────────────────────────────────────────────
class Handler(BaseHTTPRequestHandler):

    def _cors(self):
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")

    def _json(self, obj, code=200):
        self.send_response(code); self._cors()
        self.send_header("Content-Type", "application/json"); self.end_headers()
        self.wfile.write(json.dumps(obj).encode())

    def do_OPTIONS(self):
        self.send_response(200); self._cors(); self.end_headers()

    def do_GET(self):
        if self.path in ("/", "/panel"):
            try:
                with open(os.path.join(os.path.dirname(__file__), "panel_trading.html"), "rb") as f:
                    html = f.read()
                self.send_response(200); self._cors()
                self.send_header("Content-Type", "text/html; charset=utf-8"); self.end_headers()
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
            ok = bot.apagar(cerrar_todo=bool(body.get("cerrar_todo", False)))
            self._json({"ok": ok, "mensaje": "Bot apagado" if ok else "Ya estaba apagado"})
        else:
            self.send_response(404); self.end_headers()

    def log_message(self, format, *args):
        pass


if __name__ == "__main__":
    print("=" * 62)
    print("  🤖 BOT DE TRADING — Binance (Técnico + Noticias)")
    print("=" * 62)
    print(f"  Modo      : {MODO.upper()}", "  ⚠️  PLATA REAL" if MODO == "real" else "")
    print(f"  Par       : {SYMBOL}  ({TIMEFRAME})")
    print(f"  Estrategia: confluencia EMA+RSI+MACD + sentimiento de noticias")
    print(f"  Regla     : opera SOLO si técnico y noticias coinciden")
    print(f"  Panel     : http://localhost:{PORT}")
    print("-" * 62)
    print("  El bot arranca APAGADO y se configura solo. Prendelo desde el panel.")
    print("  Ctrl+C para cerrar el servidor.\n")
    if MODO == "real":
        print("  ⚠️  ⚠️  ⚠️  MODO REAL — OPERA CON PLATA DE VERDAD  ⚠️  ⚠️  ⚠️\n")
    HTTPServer(("localhost", PORT), Handler).serve_forever()
