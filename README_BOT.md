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

## La estrategia (scalping)

Es una estrategia **simple de ejemplo** pensada para el estilo "muchas
operaciones, poca ganancia":

- **Compra** cuando la EMA rápida (9) está por encima de la EMA lenta (21)
  y el RSI no está sobrecomprado (hay margen para subir).
- **Vende** cuando llega al objetivo de ganancia (`+0.4%`), toca el stop loss
  (`-0.6%`), o la tendencia se da vuelta.

> Esto es un punto de partida, **no** una estrategia probada como rentable.
> Con las comisiones (0.1% por lado), cada trade tiene que ganar más de ~0.2%
> solo para no perder. Ajustá los parámetros y probá mucho en simulado/testnet
> antes de pensar en plata real.

## Seguridad

- Cuando uses testnet o real, creá las API keys **sin permiso de retiro**.
- Nunca subas tus API keys a git (van en el `.env`, que no deberías commitear).
