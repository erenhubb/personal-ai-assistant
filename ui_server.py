"""Jarvis'in yüzü: ekrandaki animasyonlu arayüzü yerel bir web sayfası olarak sunar.

main.py durum değiştikçe send(...) çağırır; sayfa bunları anında alıp animasyonu günceller.
Sayfa Edge'in uygulama modunda, adres çubuğu olmayan ayrı bir pencere olarak açılır.
"""

import json
import queue
import subprocess
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

PORT = 8765
URL = f"http://127.0.0.1:{PORT}/"
PAGE = Path(__file__).parent / "ui" / "index.html"

_clients = []
_clients_lock = threading.Lock()
_last = {"state": "sleeping"}  # Sayfa sonradan açılırsa son durumu görsün


def send(**message):
    """Arayüze bir olay gönderir: state, level, you, reply, action."""
    if "state" in message:
        _last["state"] = message["state"]
    with _clients_lock:
        for client in _clients:
            client.put(message)


class Server(ThreadingHTTPServer):
    # Windows'ta adres paylaşımı açıkken ikinci bir Jarvis aynı adresi alabiliyor;
    # kapatıyoruz ki "Jarvis zaten açık" kontrolü çalışsın.
    allow_reuse_address = False
    daemon_threads = True


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        path = self.path.split("?")[0]
        if path in ("/", "/index.html"):
            body = PAGE.read_bytes()
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        elif path == "/events":
            self.stream_events()
        else:
            self.send_error(404)

    def stream_events(self):
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-cache")
        # Duvar kağıdı sayfa dosyadan açıldığı için başka "adresten" bağlanıyor sayılır
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        events = queue.Queue()
        events.put(dict(_last))
        with _clients_lock:
            _clients.append(events)
        try:
            while True:
                try:
                    message = events.get(timeout=15)
                    self.wfile.write(f"data: {json.dumps(message, ensure_ascii=False)}\n\n".encode())
                except queue.Empty:
                    self.wfile.write(b": ping\n\n")  # Bağlantı kopmasın diye
                self.wfile.flush()
        except (ConnectionError, OSError):
            pass
        finally:
            with _clients_lock:
                _clients.remove(events)

    def log_message(self, *args):
        pass  # Her istekte ekrana yazı basmasın


def start(open_window=True):
    """Arayüz sunucusunu arka planda başlatır ve pencereyi açar.
    Başka bir Jarvis zaten açıksa (adres dolu) False döndürür."""
    try:
        server = Server(("127.0.0.1", PORT), Handler)
    except OSError:
        return False
    threading.Thread(target=server.serve_forever, daemon=True).start()
    if open_window:
        subprocess.Popen(f'start "" msedge --app={URL} --window-size=900,820', shell=True)
    return True


if __name__ == "__main__":
    # Sadece arayüzü denemek için: python ui_server.py, sonra tarayıcıda /?demo=1 aç
    print(f"Arayüz: {URL}?demo=1")
    Server(("127.0.0.1", PORT), Handler).serve_forever()
