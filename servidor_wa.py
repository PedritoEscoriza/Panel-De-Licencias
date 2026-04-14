from http.server import HTTPServer, BaseHTTPRequestHandler
from twilio.rest import Client
from dotenv import load_dotenv
import json, os, urllib.parse

load_dotenv()

ACCOUNT_SID = os.getenv("TWILIO_ACCOUNT_SID")
AUTH_TOKEN  = os.getenv("TWILIO_AUTH_TOKEN")
FROM_NUMBER = os.getenv("TWILIO_WHATSAPP_FROM")

class Handler(BaseHTTPRequestHandler):

    def do_OPTIONS(self):
        self.send_response(200)
        self._cors()
        self.end_headers()

    def do_GET(self):
        if self.path == '/config':
            resp = {'sid': ACCOUNT_SID or '', 'ok': bool(ACCOUNT_SID and AUTH_TOKEN)}
            self.send_response(200)
            self._cors()
            self.send_header('Content-Type', 'application/json')
            self.end_headers()
            self.wfile.write(json.dumps(resp).encode())
        else:
            self.send_response(404); self.end_headers()

    def do_POST(self):
        if self.path != '/enviar':
            self.send_response(404); self.end_headers(); return

        length = int(self.headers.get('Content-Length', 0))
        body   = json.loads(self.rfile.read(length))

        telefono = str(body.get('telefono', '')).replace(' ', '')
        if not telefono.startswith('+'):
            telefono = '+549' + telefono
        mensaje = body.get('mensaje', '')

        try:
            client = Client(ACCOUNT_SID, AUTH_TOKEN)
            client.messages.create(
                body=mensaje,
                from_=FROM_NUMBER,
                to=f'whatsapp:{telefono}'
            )
            resp = {'ok': True, 'mensaje': 'Enviado correctamente'}
        except Exception as e:
            resp = {'ok': False, 'error': str(e)}

        self.send_response(200)
        self._cors()
        self.send_header('Content-Type', 'application/json')
        self.end_headers()
        self.wfile.write(json.dumps(resp).encode())

    def _cors(self):
        self.send_header('Access-Control-Allow-Origin', '*')
        self.send_header('Access-Control-Allow-Methods', 'POST, OPTIONS')
        self.send_header('Access-Control-Allow-Headers', 'Content-Type')

    def log_message(self, format, *args):
        print(f"  → {args[0]} {args[1]}")

if __name__ == '__main__':
    port = 5050
    print(f"✅ Servidor WhatsApp corriendo en http://localhost:{port}")
    print(f"   Dejá esta ventana abierta mientras usás el panel.")
    print(f"   Presioná Ctrl+C para detenerlo.\n")
    HTTPServer(('localhost', port), Handler).serve_forever()
