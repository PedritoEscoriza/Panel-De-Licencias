#!/bin/bash
# Doble clic para encender el bot. Se actualiza solo y abre el panel.
cd "$(dirname "$0")"
echo "🔄 Buscando actualizaciones..."
git pull 2>/dev/null
echo "🌐 Abriendo el panel en el navegador..."
(sleep 3 && open http://127.0.0.1:8000) &
echo "🤖 Encendiendo el bot... (dejá esta ventana abierta)"
python3 bot_trading.py
