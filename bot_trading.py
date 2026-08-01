"""
Bot de trading para Binance — Técnico + Noticias
=================================================

Arranca APAGADO y se controla desde el panel web. Vos solo lo prendés:
el bot se configura todo solo. Cuando lo encendés, en cada vuelta hace:

  1) CARGA   → baja velas del mercado + titulares de noticias de cripto
  2) ANALIZA → corre DOS análisis en paralelo:
        • Técnico  : confluencia de EMA + RSI + MACD sobre el precio
        • Noticias : mide el sentimiento (alcista/bajista) de los titulares,
                     con pesos por intensidad, frases y negaciones
  3) DECIDE  → SOLO opera si los dos coinciden y NO se contradicen.

El análisis corre en loop mientras el bot está encendido (no es una foto).

Además: podés pedir un RESUMEN del mercado y enviártelo al WhatsApp
(reusa las credenciales de Twilio que ya tenés en el .env).

Modos:
  • simulado : precios y noticias REALES, órdenes FALSAS. Sin API keys. (por defecto)
  • testnet  : Binance Testnet, manda órdenes REALES con plata de mentira.
               Necesita BINANCE_TESTNET_API_KEY / _SECRET.
  • real     : Binance real (PLATA DE VERDAD). Activar a propósito.

Panel:  http://localhost:8000
"""

from http.server import HTTPServer, BaseHTTPRequestHandler
from dotenv import load_dotenv
import json, os, threading, time, traceback, urllib.request, re, unicodedata
from datetime import datetime, timezone
from xml.etree import ElementTree as ET

import ccxt

load_dotenv()

# ── Configuración (el bot funciona sin tocar nada de esto) ──────────────────
MODO        = os.getenv("BOT_MODO", "simulado").lower()   # simulado | testnet | real
SYMBOL      = os.getenv("BOT_SYMBOL", "BTC/USDT")
TIMEFRAME   = os.getenv("BOT_TIMEFRAME", "5m")
INTERVALO   = int(os.getenv("BOT_INTERVALO", "8"))       # segundos entre análisis
CAPITAL_INI = float(os.getenv("BOT_CAPITAL", "1000"))
RIESGO_PCT  = float(os.getenv("BOT_RIESGO_PCT", "20"))
NOTICIAS_CADA = int(os.getenv("BOT_NOTICIAS_CADA", "300"))

EMA_RAPIDA  = int(os.getenv("BOT_EMA_RAPIDA", "9"))
EMA_LENTA   = int(os.getenv("BOT_EMA_LENTA", "21"))
RSI_PERIODO = int(os.getenv("BOT_RSI_PERIODO", "14"))
TAKE_PROFIT = float(os.getenv("BOT_TAKE_PROFIT", "0.8"))
STOP_LOSS   = float(os.getenv("BOT_STOP_LOSS", "1.0"))
COMISION    = float(os.getenv("BOT_COMISION", "0.1"))

PORT = int(os.getenv("BOT_PUERTO", "8000"))  # 8000: puerto seguro para navegadores (5060 lo bloquean)

# WhatsApp (reusa el Twilio que ya está en el proyecto)
WHATSAPP_TO = os.getenv("BOT_WHATSAPP_TO", "")  # ej: +5491122334455

FEEDS = [
    "https://cointelegraph.com/rss",
    "https://www.coindesk.com/arc/outboundfeeds/rss/",
    "https://cryptopotato.com/feed/",
]

# Léxico con PESOS por intensidad. Frases de varias palabras van aparte.
FRASES = {
    "all-time high": 3, "all time high": 3, "record high": 3, "record highs": 3,
    "etf approval": 3, "etf approved": 3, "bull market": 2, "bull run": 2,
    "sell-off": -2, "sell off": -2, "bear market": -2, "death cross": -2,
    "golden cross": 2, "short squeeze": 2,
}
PALABRAS = {
    # alcistas fuertes (+2/+3)
    "surge": 2, "surges": 2, "soar": 2, "soars": 2, "rally": 2, "rallies": 2,
    "breakout": 2, "bullish": 2, "adoption": 2, "institutional": 2, "inflows": 2,
    "inflow": 2, "approved": 2, "approval": 2, "boom": 2, "moon": 2,
    # alcistas leves (+1)
    "gains": 1, "gain": 1, "rise": 1, "rises": 1, "climb": 1, "climbs": 1,
    "jump": 1, "jumps": 1, "recovery": 1, "rebound": 1, "partnership": 1,
    "upgrade": 1, "optimism": 1, "buy": 1, "buying": 1, "accumulate": 1,
    "support": 1, "milestone": 1, "positive": 1, "outperform": 1, "green": 1,
    # bajistas fuertes (-2/-3)
    "crash": -3, "crashes": -3, "plunge": -3, "plunges": -3, "collapse": -3,
    "hack": -3, "hacked": -3, "exploit": -3, "bankruptcy": -3, "fraud": -3,
    "scam": -3, "dump": -2, "dumps": -2, "selloff": -2, "liquidation": -2,
    "liquidations": -2, "lawsuit": -2, "ban": -2, "banned": -2, "bearish": -2,
    "crackdown": -2, "outflows": -2, "outflow": -2, "crisis": -2,
    # bajistas leves (-1)
    "falls": -1, "fall": -1, "drop": -1, "drops": -1, "decline": -1,
    "declines": -1, "tumble": -1, "slump": -1, "warning": -1, "fear": -1,
    "fine": -1, "downgrade": -1, "loss": -1, "losses": -1, "negative": -1,
    "risk": -1, "sell": -1, "selling": -1, "red": -1,
    # ── ESPAÑOL (para el resumen del profe; guardadas SIN acento) ──
    # alcistas
    "sube": 1, "subir": 1, "suba": 1, "subiendo": 1, "alcista": 2, "alcistas": 2,
    "compra": 1, "comprar": 1, "comprando": 1, "largo": 1, "largos": 1,
    "ruptura": 2, "rompe": 1, "acumular": 1, "acumulando": 1, "rebote": 1,
    "recuperacion": 1, "recupera": 1, "optimismo": 1, "maximos": 2, "maximo": 1,
    "toro": 1, "gana": 1, "ganancia": 1, "impulso": 1, "despegue": 2, "despega": 2,
    "dispara": 2, "disparan": 2, "sostiene": 1, "fortaleza": 1, "verdes": 1,
    # bajistas
    "baja": -1, "bajar": -1, "bajando": -1, "bajista": -2, "bajistas": -2,
    "cae": -1, "caen": -1, "caida": -1, "desplome": -3, "desploma": -3,
    "vender": -1, "venta": -1, "vende": -1, "corto": -1, "cortos": -1,
    "correccion": -1, "panico": -2, "miedo": -1, "liquidacion": -2,
    "estafa": -3, "hackeo": -3, "prohibicion": -2, "demanda": -2, "riesgo": -1,
    "perdida": -1, "pierde": -1, "debilidad": -1, "rojos": -1, "derrumbe": -3,
}
NEGADORES = {"no", "not", "without", "denies", "denied", "never", "fails", "fail",
             "avoids", "sin", "nunca", "tampoco", "evita"}


def _sin_acentos(s):
    """Quita acentos (á→a) para que el léxico en español matchee siempre."""
    return "".join(c for c in unicodedata.normalize("NFD", s)
                   if unicodedata.category(c) != "Mn")
# ────────────────────────────────────────────────────────────────────────────


def ahora():
    return datetime.now(timezone.utc).astimezone().strftime("%H:%M:%S")


# ── Indicadores técnicos ────────────────────────────────────────────────────
def ema(valores, periodo):
    if len(valores) < periodo:
        return None
    k = 2 / (periodo + 1)
    e = sum(valores[:periodo]) / periodo
    for v in valores[periodo:]:
        e = v * k + e * (1 - k)
    return e


def serie_ema(valores, periodo):
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
    if len(valores) < lenta + senal:
        return None, None, None
    ema_r = serie_ema(valores, rapida)
    ema_l = serie_ema(valores, lenta)
    n = min(len(ema_r), len(ema_l))
    macd_line = [ema_r[-n + i] - ema_l[-n + i] for i in range(n)]
    if len(macd_line) < senal:
        return None, None, None
    signal = serie_ema(macd_line, senal)
    return round(macd_line[-1], 2), round(signal[-1], 2), round(macd_line[-1] - signal[-1], 2)


# ── Datos de mercado con respaldo (si Binance está bloqueado, usa otro) ──────
# Para los DATOS (precios/velas) sirve cualquier exchange. Probamos varios y
# usamos el primero que responda. Las órdenes reales siguen yendo a Binance.
EXCHANGES_DATOS = ["binance", "kucoin", "kraken", "okx", "coinbase"]
_cache_ex_datos = {}
_ultimo_ok = None   # último exchange que respondió (se prueba primero para ir rápido)


def _exchange_datos(ex_id):
    if ex_id not in _cache_ex_datos:
        _cache_ex_datos[ex_id] = getattr(ccxt, ex_id)({"enableRateLimit": True})
    return _cache_ex_datos[ex_id]


def bajar_velas(symbol, timeframe, limit):
    """Baja velas probando varios exchanges. Devuelve (velas, nombre_exchange).
    Recuerda cuál funcionó y lo prueba primero para no demorar en cada vuelta."""
    global _ultimo_ok
    orden = ([_ultimo_ok] if _ultimo_ok else []) + [e for e in EXCHANGES_DATOS if e != _ultimo_ok]
    ultimo_error = None
    for ex_id in orden:
        try:
            velas = _exchange_datos(ex_id).fetch_ohlcv(symbol, timeframe=timeframe, limit=limit)
            if velas:
                _ultimo_ok = ex_id
                return velas, ex_id
        except Exception as e:
            ultimo_error = e
            continue
    raise ccxt.NetworkError(f"Ningún exchange respondió (último error: {ultimo_error})")


# ── Análisis de noticias (más inteligente) ──────────────────────────────────
def bajar_titulares(limite=14):
    titulares = []
    for url in FEEDS:
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
            with urllib.request.urlopen(req, timeout=8) as r:
                raiz = ET.fromstring(r.read())
            for item in raiz.iter("item"):
                t = item.findtext("title")
                if t and t.strip() not in titulares:
                    titulares.append(t.strip())
        except Exception:
            continue
    return titulares[:limite]


def sentimiento_texto(texto):
    """Puntaje del titular con pesos, frases y negaciones (inglés y español)."""
    t = _sin_acentos(texto.lower())
    puntaje = 0
    # 1) Frases de varias palabras (se sacan del texto para no contar doble)
    for frase, peso in FRASES.items():
        if frase in t:
            puntaje += peso
            t = t.replace(frase, " ")
    # 2) Palabras sueltas, con negación (si antes hay un negador, se invierte)
    tokens = re.findall(r"[a-z']+", t)
    for i, w in enumerate(tokens):
        if w in PALABRAS:
            peso = PALABRAS[w]
            ventana = tokens[max(0, i - 3):i]
            if any(neg in ventana for neg in NEGADORES):
                peso = -peso  # "not bullish" → bajista
            puntaje += peso
    return puntaje


def analizar_noticias():
    """Veredicto + ánimo (-100..100) + confianza + resumen para el chat."""
    titulares = bajar_titulares()
    if not titulares:
        return {"veredicto": "SIN DATOS", "animo": 0, "confianza": 0, "titulares": [],
                "resumen": "No se pudieron bajar noticias (sin conexión o feeds caídos).",
                "top_alcista": None, "top_bajista": None}
    detalle, total, con_senal = [], 0, 0
    for t in titulares:
        s = sentimiento_texto(t)
        total += s
        if s != 0:
            con_senal += 1
        etiqueta = "alcista" if s > 0 else "bajista" if s < 0 else "neutral"
        detalle.append({"titulo": t, "sent": etiqueta, "score": s})

    n = len(titulares)
    animo = max(-100, min(100, round(total / n * 25)))
    confianza = round(con_senal / n * 100)
    if animo >= 15:
        veredicto = "ALCISTA"
    elif animo <= -15:
        veredicto = "BAJISTA"
    else:
        veredicto = "NEUTRAL"

    ordenados = sorted(detalle, key=lambda d: d["score"])
    top_bajista = ordenados[0] if ordenados and ordenados[0]["score"] < 0 else None
    top_alcista = ordenados[-1] if ordenados and ordenados[-1]["score"] > 0 else None

    return {"veredicto": veredicto, "animo": animo, "confianza": confianza,
            "titulares": detalle,
            "resumen": f"{n} titulares analizados · ánimo {animo:+d}/100 · {confianza}% con señal",
            "top_alcista": top_alcista["titulo"] if top_alcista else None,
            "top_bajista": top_bajista["titulo"] if top_bajista else None}


def analizar_texto_manual(texto, bias="auto"):
    """Analiza el resumen que carga el usuario (ej: el del profe de trading).
    bias: 'auto' = lo decide el análisis; o forzado a 'alcista'/'bajista'/'neutral'."""
    texto = (texto or "").strip()
    raw = sentimiento_texto(texto) if texto else 0
    animo = max(-100, min(100, raw * 8))
    bias = (bias or "auto").lower()
    if bias == "alcista":
        veredicto, animo = "ALCISTA", max(animo, 50)
    elif bias == "bajista":
        veredicto, animo = "BAJISTA", min(animo, -50)
    elif bias == "neutral":
        veredicto, animo = "NEUTRAL", 0
    else:
        veredicto = "ALCISTA" if animo >= 15 else "BAJISTA" if animo <= -15 else "NEUTRAL"
    return {"activo": True, "texto": texto, "veredicto": veredicto, "animo": animo,
            "bias": bias, "hora": ahora()}


def texto_resumen_mercado(noticias):
    """Arma un resumen listo para mandar al chat/WhatsApp."""
    v = noticias.get("veredicto", "—")
    emoji = {"ALCISTA": "🟢", "BAJISTA": "🔴", "NEUTRAL": "🟡"}.get(v, "⚪")
    lineas = [
        "📊 *Resumen del mercado cripto*",
        f"Clima general: {emoji} *{v}*  (ánimo {noticias.get('animo', 0):+d}/100)",
        noticias.get("resumen", ""),
        "",
    ]
    if noticias.get("top_alcista"):
        lineas.append(f"🟢 Lo más positivo: {noticias['top_alcista']}")
    if noticias.get("top_bajista"):
        lineas.append(f"🔴 Lo más negativo: {noticias['top_bajista']}")
    lineas += ["", f"🕒 {datetime.now().strftime('%d/%m/%Y %H:%M')}"]
    return "\n".join(l for l in lineas if l is not None)


# ── Backtesting: probar estrategias sobre datos históricos ──────────────────
# Cada estrategia es una combinación de parámetros. El backtest las corre todas
# sobre el histórico real y mide cuál habría rendido mejor.
GRID_ESTRATEGIAS = [
    {"nombre": "Scalping rápido",  "ema_r": 5,  "ema_l": 13, "rsi": 14, "tp": 0.4, "sl": 0.6},
    {"nombre": "Clásica",          "ema_r": 9,  "ema_l": 21, "rsi": 14, "tp": 0.8, "sl": 1.0},
    {"nombre": "Tendencia media",  "ema_r": 12, "ema_l": 26, "rsi": 14, "tp": 1.2, "sl": 1.5},
    {"nombre": "Conservadora",     "ema_r": 9,  "ema_l": 21, "rsi": 14, "tp": 1.5, "sl": 1.0},
    {"nombre": "Agresiva TP corto","ema_r": 5,  "ema_l": 13, "rsi": 14, "tp": 0.3, "sl": 0.5},
    {"nombre": "Swing",            "ema_r": 20, "ema_l": 50, "rsi": 14, "tp": 2.0, "sl": 2.5},
]


def _veredicto_tecnico(er, el, r, hist):
    """Misma lógica de confluencia que usa el bot en vivo (para el backtest)."""
    puntos = 0
    puntos += 1 if er > el else -1
    if r > 70 or r < 30:
        puntos -= 1
    elif r > 50:
        puntos += 1
    puntos += 1 if hist > 0 else -1
    return "ALCISTA" if puntos >= 2 else "BAJISTA" if puntos <= -1 else "NEUTRAL"


def simular_estrategia(cierres, cfg, comision=COMISION, riesgo=20.0):
    """Corre una estrategia sobre el histórico y devuelve sus métricas."""
    capital = 1000.0
    pos = None
    trades, wins = [], 0
    peak, max_dd = capital, 0.0
    minreq = max(cfg["ema_l"], cfg["rsi"], 35) + 2
    for i in range(minreq, len(cierres)):
        ventana = cierres[:i + 1]
        precio = ventana[-1]
        er = ema(ventana, cfg["ema_r"]); el = ema(ventana, cfg["ema_l"])
        r = rsi(ventana, cfg["rsi"]); _, _, hist = macd(ventana)
        if None in (er, el, r, hist):
            continue
        ver = _veredicto_tecnico(er, el, r, hist)
        if pos is None:
            if ver == "ALCISTA":
                monto = capital * (riesgo / 100); com = monto * comision / 100
                pos = {"entrada": precio, "cant": monto / precio}
                capital -= (monto + com)
        else:
            cambio = (precio - pos["entrada"]) / pos["entrada"] * 100
            if cambio >= cfg["tp"] or cambio <= -cfg["sl"] or ver == "BAJISTA":
                vv = pos["cant"] * precio; com = vv * comision / 100
                gan = (precio - pos["entrada"]) * pos["cant"] - com
                capital += (vv - com); trades.append(gan)
                if gan > 0:
                    wins += 1
                pos = None
        eq = capital + (pos["cant"] * precio if pos else 0)
        peak = max(peak, eq)
        max_dd = max(max_dd, (peak - eq) / peak * 100 if peak else 0)
    if pos:  # cerrar lo que quede abierto al final
        vv = pos["cant"] * cierres[-1]; com = vv * comision / 100
        gan = (cierres[-1] - pos["entrada"]) * pos["cant"] - com
        capital += (vv - com); trades.append(gan)
        if gan > 0:
            wins += 1
    n = len(trades)
    return {"retorno": round((capital - 1000.0) / 1000.0 * 100, 2), "trades": n,
            "ganadoras": wins, "win_rate": round(wins / n * 100, 1) if n else 0,
            "max_dd": round(max_dd, 2)}


def lectura_grafico(velas, indic, tecnico):
    """Lee el gráfico en palabras: tendencia, soporte/resistencia, momentum y señal."""
    cierres = [v[4] for v in velas]
    highs = [v[2] for v in velas]
    lows = [v[3] for v in velas]
    precio = cierres[-1]
    vent = min(40, len(velas))
    resistencia = max(highs[-vent:])
    soporte = min(lows[-vent:])
    dist_res = (resistencia - precio) / precio * 100 if precio else 0
    dist_sop = (precio - soporte) / precio * 100 if precio else 0

    er, el = indic.get("ema_rapida"), indic.get("ema_lenta")
    rsi_v, hist = indic.get("rsi"), indic.get("macd_hist")
    tendencia = "—"
    if er and el:
        tendencia = "alcista" if er > el else "bajista"

    puntos = tecnico.get("puntos", 0)
    if puntos >= 2:
        senal = "🟢 Señal alcista detectada en el gráfico"
    elif puntos == 1:
        senal = "🔎 Señal alcista formándose (falta confirmación)"
    elif puntos <= -1:
        senal = "🔴 Gráfico bajista — sin señal de compra"
    else:
        senal = "⚪ Buscando señal... sin nada claro por ahora"

    frases = [f"Precio ${precio:,.0f}, tendencia {tendencia}."]
    frases.append(f"Resistencia en ${resistencia:,.0f} (a +{dist_res:.2f}%) y "
                  f"soporte en ${soporte:,.0f} (a -{dist_sop:.2f}%).")
    if rsi_v is not None:
        estado = "sobrecomprado" if rsi_v > 70 else "sobrevendido" if rsi_v < 30 else "neutral"
        frases.append(f"RSI {rsi_v} ({estado}).")
    if hist is not None:
        frases.append("MACD con impulso " + ("alcista." if hist > 0 else "bajista."))
    return {"tendencia": tendencia, "soporte": round(soporte, 2), "resistencia": round(resistencia, 2),
            "dist_res": round(dist_res, 2), "dist_sop": round(dist_sop, 2),
            "senal": senal, "texto": " ".join(frases)}


def buscar_mejor_estrategia(cierres):
    """Prueba todas las estrategias del grid y las ordena por resultado."""
    resultados = []
    for cfg in GRID_ESTRATEGIAS:
        m = simular_estrategia(cierres, cfg)
        resultados.append({**cfg, **m})
    # Ordena por retorno, pero exige al menos 3 operaciones para ser confiable
    resultados.sort(key=lambda r: (r["trades"] >= 3, r["retorno"]), reverse=True)
    return resultados


# ── Config de conexión a Binance (se guarda para no reescribirla cada vez) ───
CONFIG_BINANCE = os.path.join(os.path.dirname(__file__), "binance_config.json")


def cargar_config_binance():
    try:
        with open(CONFIG_BINANCE) as f:
            return json.load(f)
    except Exception:
        return {}


def guardar_config_binance(datos):
    with open(CONFIG_BINANCE, "w") as f:
        json.dump(datos, f)


# ── El bot ──────────────────────────────────────────────────────────────────
class BotTrading:
    def __init__(self):
        self.lock = threading.Lock()
        self.hilo = None
        self.encendido = False
        self.exchange = None
        # Conexión a Binance (modo y keys), guardada para no reescribirla
        _cfgb = cargar_config_binance()
        self._modo = _cfgb.get("modo", MODO)
        self.api_key = _cfgb.get("api_key", "")
        self.api_secret = _cfgb.get("api_secret", "")
        # El resumen manual persiste aunque prendas/apagues el bot
        self.noticias_manual = {"activo": False, "texto": "", "veredicto": "—",
                                "animo": 0, "bias": "auto", "hora": ""}
        self.noticias_fuente = "auto"   # "auto" (RSS) o "manual"
        # Estrategia activa (la puede cambiar el backtest). También persiste.
        self.cfg = {"nombre": "Clásica", "ema_r": EMA_RAPIDA, "ema_l": EMA_LENTA,
                    "rsi": RSI_PERIODO, "tp": TAKE_PROFIT, "sl": STOP_LOSS}
        self.backtest = {"resultados": [], "hora": "", "velas": 0}
        self.reset_estado()

    def reset_estado(self):
        self.fase = "APAGADO"
        self.modo = self._modo
        self.symbol = SYMBOL
        self.precio = None
        self.capital = CAPITAL_INI
        self.posicion = None
        self.pnl = 0.0
        self.comisiones = 0.0
        self.operaciones = 0
        self.ganadoras = 0
        self.indicadores = {}
        self.historial = []            # últimos cierres, para el gráfico
        self.fuente_datos = "—"        # de qué exchange salieron los datos
        self.lectura = {}              # lectura del gráfico en palabras
        self.tecnico = {"veredicto": "—", "motivos": []}
        self.noticias = {"veredicto": "—", "animo": 0, "confianza": 0,
                         "titulares": [], "resumen": "", "top_alcista": None, "top_bajista": None}
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
                "tiene_keys": bool(self.api_key),
                "symbol": self.symbol, "precio": self.precio,
                "capital": round(self.capital, 2), "posicion": self.posicion,
                "valor_posicion": valor_pos, "pnl": round(self.pnl, 2),
                "comisiones": round(self.comisiones, 4), "operaciones": self.operaciones,
                "ganadoras": self.ganadoras,
                "win_rate": round(self.ganadoras / self.operaciones * 100, 1) if self.operaciones else 0,
                "indicadores": self.indicadores, "historial": self.historial,
                "fuente_datos": self.fuente_datos, "lectura": self.lectura,
                "tecnico": self.tecnico, "noticias": self.noticias,
                "noticias_manual": self.noticias_manual, "noticias_fuente": self.noticias_fuente,
                "cfg": self.cfg, "backtest": self.backtest,
                "decision": self.decision, "pensamiento": self.pensamiento,
                "log": self.log, "error": self.error,
                "config": {"timeframe": TIMEFRAME, "intervalo": INTERVALO,
                           "take_profit": TAKE_PROFIT, "stop_loss": STOP_LOSS,
                           "comision": COMISION},
            }

    def crear_exchange(self):
        opciones = {"enableRateLimit": True, "options": {"defaultType": "spot"}}
        if self.modo in ("testnet", "real"):
            # Preferimos las keys cargadas desde el panel; si no, las del .env
            env_k = "BINANCE_TESTNET_API_KEY" if self.modo == "testnet" else "BINANCE_API_KEY"
            env_s = "BINANCE_TESTNET_API_SECRET" if self.modo == "testnet" else "BINANCE_API_SECRET"
            opciones["apiKey"] = self.api_key or os.getenv(env_k, "")
            opciones["secret"] = self.api_secret or os.getenv(env_s, "")
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
            self.exchange = self.crear_exchange()
        except Exception as e:
            self.error = f"No se pudo conectar al exchange: {e}"
            self.registrar("error", f"❌ {self.error}")
            with self.lock:
                self.encendido = False
                self.fase = "APAGADO"
            return

        # En testnet/real, arrancamos el capital desde el balance real de USDT
        if self.modo in ("testnet", "real"):
            try:
                bal = self.exchange.fetch_balance()
                usdt = bal.get("USDT", {}).get("free", None)
                if usdt is not None:
                    with self.lock:
                        self.capital = float(usdt)
                    self.registrar("info", f"💰 Balance {self.modo}: {usdt:.2f} USDT")
            except Exception as e:
                self.registrar("error", f"⚠️ No pude leer el balance ({e}). Uso capital por defecto.")

        fallos = 0
        while self.encendido:
            try:
                self.una_vuelta(self.exchange)
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

    # ---- una vuelta ----
    def una_vuelta(self, exchange):
        with self.lock:
            self.fase = "CARGANDO"
            self.pensamiento = "Cargando velas del mercado y titulares de noticias..."
        velas, fuente = bajar_velas(self.symbol, TIMEFRAME, 120)
        cierres = [v[4] for v in velas]
        precio = cierres[-1]
        with self.lock:
            self.precio = precio
            self.historial = [round(c, 2) for c in cierres[-60:]]
            self.fuente_datos = fuente

        if time.time() - self._ultima_noticia > NOTICIAS_CADA or not self.noticias["titulares"]:
            noticias = analizar_noticias()
            with self.lock:
                self.noticias = noticias
                self._ultima_noticia = time.time()

        with self.lock:
            self.fase = "ANALIZANDO"
        tecnico = self.analizar_tecnico(cierres)
        with self.lock:
            self.tecnico = tecnico
            self.lectura = lectura_grafico(velas, self.indicadores, tecnico)

        # Noticias efectivas: si cargaste un resumen manual (del profe), ese manda
        with self.lock:
            if self.noticias_manual.get("activo"):
                veredicto_noticias = self.noticias_manual["veredicto"]
                self.noticias_fuente = "manual"
            else:
                veredicto_noticias = self.noticias["veredicto"]
                self.noticias_fuente = "auto"

        with self.lock:
            self.fase = "OPERANDO"
        self.decidir(precio, tecnico["veredicto"], veredicto_noticias)

    def analizar_tecnico(self, cierres):
        cfg = self.cfg
        ema_r = ema(cierres, cfg["ema_r"])
        ema_l = ema(cierres, cfg["ema_l"])
        rsi_v = rsi(cierres, cfg["rsi"])
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
        if ema_r > ema_l:
            puntos += 1; motivos.append(f"EMA{cfg['ema_r']} > EMA{cfg['ema_l']} (tendencia alcista)")
        else:
            puntos -= 1; motivos.append(f"EMA{cfg['ema_r']} < EMA{cfg['ema_l']} (tendencia bajista)")
        if rsi_v > 70:
            puntos -= 1; motivos.append(f"RSI {rsi_v} sobrecomprado (riesgo de caída)")
        elif rsi_v < 30:
            puntos -= 1; motivos.append(f"RSI {rsi_v} sobrevendido (sin fuerza compradora)")
        elif rsi_v > 50:
            puntos += 1; motivos.append(f"RSI {rsi_v} con momentum alcista")
        else:
            motivos.append(f"RSI {rsi_v} neutro")
        if hist > 0:
            puntos += 1; motivos.append("MACD por encima de su señal (impulso alcista)")
        else:
            puntos -= 1; motivos.append("MACD por debajo de su señal (impulso bajista)")

        veredicto = "ALCISTA" if puntos >= 2 else "BAJISTA" if puntos <= -1 else "NEUTRAL"
        return {"veredicto": veredicto, "motivos": motivos, "puntos": puntos}

    def decidir(self, precio, tec, noti):
        if self.posicion:
            self.gestionar_posicion(precio, tec, noti)
            return
        if tec == "ALCISTA" and noti == "ALCISTA":
            self.abrir_posicion(precio,
                "el análisis técnico Y las noticias coinciden en ALCISTA (no se contradicen)")
        else:
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

    # ---- ejecución de órdenes ----
    def _orden_real(self, lado, cantidad):
        """Manda una orden de mercado real (testnet/real). Devuelve precio de fill."""
        cantidad = float(self.exchange.amount_to_precision(self.symbol, cantidad))
        if lado == "buy":
            orden = self.exchange.create_market_buy_order(self.symbol, cantidad)
        else:
            orden = self.exchange.create_market_sell_order(self.symbol, cantidad)
        return orden.get("average") or orden.get("price") or self.precio, cantidad

    def abrir_posicion(self, precio, razon):
        monto = self.capital * (RIESGO_PCT / 100)
        if monto < 10:
            with self.lock:
                self.pensamiento = "Capital insuficiente para abrir una posición."
            return
        cantidad = monto / precio
        # En testnet/real mandamos la orden de verdad
        if self.modo in ("testnet", "real"):
            try:
                precio, cantidad = self._orden_real("buy", cantidad)
                monto = cantidad * precio
            except Exception as e:
                self.registrar("error", f"❌ No se pudo COMPRAR en {self.modo}: {e}")
                return
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
        tp, sl = self.cfg["tp"], self.cfg["sl"]
        entrada = self.posicion["precio_entrada"]
        cambio_pct = (precio - entrada) / entrada * 100
        with self.lock:
            self.decision = "EN POSICIÓN"
            self.pensamiento = (f"Posición abierta desde ${entrada:,.2f}, ahora {cambio_pct:+.2f}%. "
                                f"Objetivo +{tp}% / corte -{sl}%.")
        if cambio_pct >= tp:
            self.cerrar_posicion(f"llegó al objetivo de ganancia (+{cambio_pct:.2f}%)", precio)
        elif cambio_pct <= -sl:
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
        if self.modo in ("testnet", "real"):
            try:
                precio, cantidad = self._orden_real("sell", cantidad)
            except Exception as e:
                self.registrar("error", f"❌ No se pudo VENDER en {self.modo}: {e}")
                return
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


# ── Envío del resumen por WhatsApp (Twilio, opcional) ───────────────────────
def enviar_whatsapp(texto, destino):
    """Envía el resumen por WhatsApp usando el Twilio del proyecto."""
    sid = os.getenv("TWILIO_ACCOUNT_SID")
    token = os.getenv("TWILIO_AUTH_TOKEN")
    desde = os.getenv("TWILIO_WHATSAPP_FROM")
    if not (sid and token and desde):
        return False, "Falta configurar Twilio en el .env (TWILIO_ACCOUNT_SID / _AUTH_TOKEN / _WHATSAPP_FROM)."
    if not destino:
        return False, "Falta el número destino. Poné BOT_WHATSAPP_TO en el .env o mandalo desde el panel."
    try:
        from twilio.rest import Client
        if not destino.startswith("+"):
            destino = "+549" + destino
        Client(sid, token).messages.create(
            body=texto, from_=desde, to=f"whatsapp:{destino}")
        return True, "Resumen enviado por WhatsApp ✅"
    except Exception as e:
        return False, f"Error al enviar: {e}"


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
        elif self.path == "/resumen":
            # genera (o reusa) el resumen del mercado como texto
            noticias = bot.noticias if bot.noticias.get("titulares") else analizar_noticias()
            self._json({"texto": texto_resumen_mercado(noticias), "noticias": noticias})
        else:
            self.send_response(404); self.end_headers()

    def do_POST(self):
        if self.path == "/encender":
            ok = bot.encender()
            self._json({"ok": ok, "mensaje": "Bot encendido" if ok else "Ya estaba encendido"})
        elif self.path == "/apagar":
            body = self._body()
            ok = bot.apagar(cerrar_todo=bool(body.get("cerrar_todo", False)))
            self._json({"ok": ok, "mensaje": "Bot apagado" if ok else "Ya estaba apagado"})
        elif self.path == "/enviar_resumen":
            body = self._body()
            noticias = bot.noticias if bot.noticias.get("titulares") else analizar_noticias()
            texto = texto_resumen_mercado(noticias)
            destino = body.get("destino") or WHATSAPP_TO
            ok, msg = enviar_whatsapp(texto, destino)
            self._json({"ok": ok, "mensaje": msg, "texto": texto})
        elif self.path == "/noticias_manual":
            body = self._body()
            manual = analizar_texto_manual(body.get("texto", ""), body.get("bias", "auto"))
            with bot.lock:
                bot.noticias_manual = manual
            bot.registrar("info",
                f"📥 Información cargada → veredicto {manual['veredicto']} "
                f"(ánimo {manual['animo']:+d}, fuente: manual).")
            self._json({"ok": True, "mensaje": f"Resumen cargado: {manual['veredicto']}",
                        "noticias_manual": manual})
        elif self.path == "/borrar_noticias_manual":
            with bot.lock:
                bot.noticias_manual = {"activo": False, "texto": "", "veredicto": "—",
                                       "animo": 0, "bias": "auto", "hora": ""}
                bot.noticias_fuente = "auto"
            bot.registrar("info", "📥 Información borrada → vuelve a noticias automáticas.")
            self._json({"ok": True, "mensaje": "Resumen manual borrado"})
        elif self.path == "/conectar":
            body = self._body()
            modo = (body.get("modo", "simulado") or "simulado").lower()
            api_key = (body.get("api_key", "") or "").strip()
            api_secret = (body.get("api_secret", "") or "").strip()
            # Si dejó las casillas vacías pero ya había keys guardadas, reusarlas
            if modo == bot._modo:
                api_key = api_key or bot.api_key
                api_secret = api_secret or bot.api_secret
            if modo not in ("simulado", "testnet", "real"):
                self._json({"ok": False, "mensaje": "Modo inválido"}); return
            if modo in ("testnet", "real") and not (api_key and api_secret):
                self._json({"ok": False, "mensaje": "Para testnet/real tenés que poner la API Key y el Secret."}); return
            if bot.encendido:
                self._json({"ok": False, "mensaje": "Apagá el bot antes de cambiar la conexión."}); return

            if modo == "simulado":
                self._aplicar_conexion("simulado", "", "")
                bot.registrar("info", "🔌 Modo cambiado a SIMULADO (demo, sin keys).")
                self._json({"ok": True, "mensaje": "Modo simulado (demo). Ya podés encender el bot."})
                return
            # testnet/real: probamos la conexión ANTES de aplicar, para no quedar trabados
            try:
                opts = {"enableRateLimit": True, "apiKey": api_key, "secret": api_secret,
                        "options": {"defaultType": "spot"}}
                prueba = ccxt.binance(opts)
                if modo == "testnet":
                    prueba.set_sandbox_mode(True)
                usdt = prueba.fetch_balance().get("USDT", {}).get("free", 0)
            except Exception as e:
                self._json({"ok": False, "mensaje": f"No pude conectar: {e}. "
                            f"Revisá las keys o si Binance está disponible en tu país. "
                            f"(Seguís en el modo anterior, tranquilo.)"})
                return
            self._aplicar_conexion(modo, api_key, api_secret)
            bot.registrar("info", f"🔌 Conectado a Binance {modo.upper()} — balance {usdt:.2f} USDT.")
            self._json({"ok": True, "mensaje": f"¡Conectado a {modo}! Balance: {usdt:.2f} USDT. "
                        f"Ya podés encender el bot."})
        elif self.path == "/backtest":
            try:
                velas, fuente = bajar_velas(SYMBOL, TIMEFRAME, 1000)
                cierres = [v[4] for v in velas]
                resultados = buscar_mejor_estrategia(cierres)
                mejor = resultados[0]
                with bot.lock:
                    bot.cfg = {"nombre": mejor["nombre"], "ema_r": mejor["ema_r"],
                               "ema_l": mejor["ema_l"], "rsi": mejor["rsi"],
                               "tp": mejor["tp"], "sl": mejor["sl"]}
                    bot.backtest = {"resultados": resultados, "hora": ahora(), "velas": len(cierres)}
                bot.registrar("info",
                    f"🔎 Backtest sobre {len(cierres)} velas ({fuente}): mejor estrategia "
                    f"'{mejor['nombre']}' ({mejor['retorno']:+.2f}%) — aplicada automáticamente.")
                self._json({"ok": True, "resultados": resultados, "aplicada": bot.cfg,
                            "velas": len(cierres), "fuente": fuente})
            except Exception as e:
                self._json({"ok": False, "mensaje": f"No se pudo correr el backtest: {e}"})
        else:
            self.send_response(404); self.end_headers()

    def _aplicar_conexion(self, modo, api_key, api_secret):
        with bot.lock:
            bot._modo = modo
            bot.api_key = api_key
            bot.api_secret = api_secret
            bot.modo = modo
        guardar_config_binance({"modo": modo, "api_key": api_key, "api_secret": api_secret})

    def _body(self):
        length = int(self.headers.get("Content-Length", 0))
        return json.loads(self.rfile.read(length)) if length else {}

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
