# 🤖 Bot de Trading — Binance

Base funcional de un bot de trading para Binance con panel web.
Arranca **apagado**; cuando lo prendés, **carga** el mercado, lo **analiza** y
recién ahí **opera**, y sigue re-analizando en loop. Todo se ve en vivo en el panel.

> ⚠️ Por defecto corre en **modo simulado**: usa precios reales de Binance pero
> las órdenes son falsas (no arriesga plata). Empezá siempre así.

## Instalación

```bash
pip install -r requirements.txt
```

## Uso

```bash
python bot_trading.py
```

Después abrí en el navegador: **http://localhost:5060**

- **▶ Encender** → arranca el ciclo (cargar → analizar → operar → repetir).
- **■ Apagar** → detiene el bot y deja la posición como está.
- **🛑 Apagar y cerrar todo** → botón de pánico: detiene y cierra la posición abierta.

## Qué muestra el panel

- **Fases** en vivo: Apagado / Cargando / Analizando / Operando
- **Precio, capital, P&L, comisiones, cantidad de operaciones y % de aciertos**
- **Qué está pensando** el bot (por qué opera o por qué se queda quieto)
- **Posición abierta** con su variación en tiempo real
- **Indicadores** (EMA rápida/lenta, RSI)
- **Registro en vivo** de cada compra y venta

## Los 3 modos

| Modo | Plata | Necesita keys | Para qué |
|------|-------|---------------|----------|
| `simulado` | Falsa | No | Probar la estrategia sin riesgo (por defecto) |
| `testnet`  | De mentira (Binance Testnet) | Sí, [gratis acá](https://testnet.binance.vision) | Probar órdenes reales contra el exchange |
| `real`     | **DE VERDAD** | Sí | Operar en serio (activar a propósito) |

Se cambian en el `.env` (mirá `.env.example`).

## La estrategia: técnico + noticias (opera solo si coinciden)

El bot corre **dos análisis** en cada vuelta y **solo compra si los dos están
de acuerdo** en que es momento alcista. Si se contradicen, o si alguno no es
claro, se queda quieto.

**1. Análisis técnico** — confluencia de tres indicadores sobre el precio:
- **EMA 9 vs EMA 21** → tendencia (alcista si la rápida está arriba)
- **RSI 14** → momentum, evitando comprar en zona sobrecomprada (>70)
- **MACD** → impulso (alcista si el histograma es positivo)

Con eso da un veredicto: **ALCISTA / BAJISTA / NEUTRAL**.

**2. Análisis de noticias** — baja titulares recientes de cripto (CoinTelegraph,
CoinDesk, CryptoPotato vía RSS, sin API key) y mide el **sentimiento** contando
palabras alcistas vs bajistas. Da otro veredicto: **ALCISTA / BAJISTA / NEUTRAL**.

**3. Decisión** — la regla de oro:

| Técnico | Noticias | Acción |
|---------|----------|--------|
| ALCISTA | ALCISTA | ✅ **Compra** |
| ALCISTA | BAJISTA | ❌ Se queda quieto (se contradicen) |
| BAJISTA | ALCISTA | ❌ Se queda quieto (se contradicen) |
| cualquiera | NEUTRAL / sin datos | ❌ Se queda quieto (no hay confirmación) |

**Salida** de una posición: cuando llega al objetivo (`+0.8%`), toca el stop
loss (`-1.0%`), o cuando el técnico **o** las noticias se dan vuelta a bajista.

> Es un punto de partida **razonable**, no una estrategia probada como
> rentable. Con las comisiones (0.1% por lado), cada trade tiene que ganar más
> de ~0.2% solo para no perder. Además el análisis de noticias es por
> palabras clave (simple y transparente), no un modelo de lenguaje. Probá
> mucho en simulado/testnet antes de pensar en plata real.

## Vos solo lo prendés

No hay que configurar nada: el bot ya viene con todo seteado (par, timeframe,
indicadores, fuentes de noticias, objetivos). Abrís el panel y le das **Encender**.
Si querés cambiar algo, está todo en el `.env` (ver `.env.example`), pero no hace falta.

## Seguridad

- Cuando uses testnet o real, creá las API keys **sin permiso de retiro**.
- Nunca subas tus API keys a git (van en el `.env`, que no deberías commitear).
