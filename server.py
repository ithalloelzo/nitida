"""Nítida — ampliador de imagens local com IA (waifu2x-ncnn-vulkan).

Servidor HTTP só com a biblioteca padrão do Python. Roda em 127.0.0.1 e
processa uma imagem por vez na GPU (Vulkan), com fallback para CPU.

Uso:  python server.py            (abre em http://127.0.0.1:8765)
      NITIDA_PORT=9000 python server.py
"""
import io
import json
import os
import queue
import re
import struct
import subprocess
import sys
import threading
import time
import urllib.parse
import uuid
import webbrowser
import zipfile
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

ROOT = os.path.dirname(os.path.abspath(__file__))
STATIC = os.path.join(ROOT, "static")
DATA = os.path.join(ROOT, "data")
UPLOADS = os.path.join(DATA, "uploads")
RESULTS = os.path.join(DATA, "results")
HISTORY_FILE = os.path.join(DATA, "history.json")
ENGINE_DIR = os.path.join(ROOT, "engine")
ENGINE = os.environ.get("NITIDA_ENGINE") or os.path.join(ENGINE_DIR, "waifu2x-ncnn-vulkan.exe")
PORT = int(os.environ.get("NITIDA_PORT", "8765"))

MODELS = {"art": "models-cunet", "photo": "models-upconv_7_photo"}
SCALES = (2, 4, 8, 16)
NOISES = (-1, 0, 1, 2, 3)
FORMATS = ("default", "webp", "jpg", "png")
MAX_BYTES = 50 * 1024 * 1024
MAX_OUTPUT_SIDE = {"webp": 16383, "jpg": 32000, "png": 32000}
MAX_OUTPUT_PIXELS = 400_000_000
ID_RE = re.compile(r"^[0-9a-f]{32}$")

for d in (UPLOADS, RESULTS):
    os.makedirs(d, exist_ok=True)


# ---------------------------------------------------------------- imagens
def sniff(head: bytes):
    """Retorna a extensão real do arquivo pelo conteúdo (não pelo nome)."""
    if head[:8] == b"\x89PNG\r\n\x1a\n":
        return "png"
    if head[:3] == b"\xff\xd8\xff":
        return "jpg"
    if head[:4] == b"RIFF" and head[8:12] == b"WEBP":
        return "webp"
    return None


def image_size(data: bytes, kind: str):
    """Lê largura e altura do cabeçalho de PNG, JPEG ou WebP."""
    try:
        if kind == "png":
            return struct.unpack(">II", data[16:24])
        if kind == "webp":
            chunk = data[12:16]
            if chunk == b"VP8 ":
                w, h = struct.unpack("<HH", data[26:30])
                return w & 0x3FFF, h & 0x3FFF
            if chunk == b"VP8L":
                v = struct.unpack("<I", data[21:25])[0]
                return (v & 0x3FFF) + 1, ((v >> 14) & 0x3FFF) + 1
            if chunk == b"VP8X":
                w = int.from_bytes(data[24:27], "little") + 1
                h = int.from_bytes(data[27:30], "little") + 1
                return w, h
        if kind == "jpg":
            i = 2
            while i + 9 < len(data):
                if data[i] != 0xFF:
                    i += 1
                    continue
                marker = data[i + 1]
                if marker in (0xD8, 0x01) or 0xD0 <= marker <= 0xD7:
                    i += 2
                    continue
                length = struct.unpack(">H", data[i + 2:i + 4])[0]
                if marker in (0xC0, 0xC1, 0xC2, 0xC3, 0xC5, 0xC6, 0xC7, 0xC9, 0xCA, 0xCB, 0xCD, 0xCE, 0xCF):
                    h, w = struct.unpack(">HH", data[i + 5:i + 9])
                    return w, h
                i += 2 + length
    except struct.error:
        pass
    return None


# ---------------------------------------------------------------- tarefas
class Store:
    """Histórico de tarefas em memória, salvo em data/history.json."""

    def __init__(self):
        self.lock = threading.Lock()
        self.tasks = {}
        if os.path.exists(HISTORY_FILE):
            try:
                with open(HISTORY_FILE, encoding="utf-8") as f:
                    for t in json.load(f):
                        self.tasks[t["id"]] = t
            except (OSError, ValueError, KeyError):
                pass

    def save(self):
        tmp = HISTORY_FILE + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(list(self.tasks.values()), f, ensure_ascii=False)
        os.replace(tmp, HISTORY_FILE)

    def add(self, task):
        with self.lock:
            self.tasks[task["id"]] = task
            self.save()

    def update(self, tid, persist=True, **fields):
        with self.lock:
            t = self.tasks.get(tid)
            if not t:
                return None
            t.update(fields)
            if persist:
                self.save()
            return dict(t)

    def get(self, tid):
        with self.lock:
            t = self.tasks.get(tid)
            return dict(t) if t else None

    def remove(self, tid):
        with self.lock:
            t = self.tasks.pop(tid, None)
            self.save()
            return t

    def all(self):
        with self.lock:
            return sorted((dict(t) for t in self.tasks.values()), key=lambda t: t["created"], reverse=True)


store = Store()
jobs = queue.Queue()
current = {"id": None, "proc": None}
engine_state = {"device": "auto", "last_error": ""}


def output_ext(task):
    return task["in_ext"] if task["format"] == "default" else task["format"]


def input_path(task):
    return os.path.join(UPLOADS, f"{task['id']}.{task['in_ext']}")


def result_path(task):
    return os.path.join(RESULTS, f"{task['id']}.{output_ext(task)}")


def download_name(task):
    base = os.path.splitext(task["name"])[0] or "imagem"
    return f"{base}_{task['scale']}x.{output_ext(task)}"


def run_engine(task, gpu):
    """Executa o waifu2x e atualiza o progresso a partir do stderr."""
    args = [
        ENGINE,
        "-i", input_path(task),
        "-o", result_path(task),
        "-n", str(task["noise"]),
        "-s", str(task["scale"]),
        "-m", os.path.join(ENGINE_DIR, MODELS[task["style"]]),
        "-f", output_ext(task),
    ]
    if gpu is not None:
        args += ["-g", str(gpu)]
    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    proc = subprocess.Popen(
        args, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
        cwd=ENGINE_DIR if os.path.isdir(ENGINE_DIR) else None, creationflags=flags,
    )
    current["proc"] = proc
    passes = max(1, task["scale"].bit_length() - 1)  # 2x=1, 4x=2, 8x=3, 16x=4 passagens
    done_passes, last, log = 0, 0.0, []
    for raw in proc.stderr:
        line = raw.decode("utf-8", "replace").strip()
        m = re.fullmatch(r"(\d+(?:\.\d+)?)%", line)
        if m:
            pct = float(m.group(1))
            if pct < last - 1:
                done_passes += 1
            last = pct
            overall = min(99.0, (min(done_passes, passes - 1) + pct / 100) / passes * 100)
            store.update(task["id"], persist=False, progress=round(overall, 1))
        elif line:
            log.append(line)
    proc.wait()
    current["proc"] = None
    ok = proc.returncode == 0 and os.path.exists(result_path(task)) and os.path.getsize(result_path(task)) > 0
    return ok, "\n".join(log[-6:])


def worker():
    while True:
        tid = jobs.get()
        task = store.get(tid)
        if not task or task["status"] != "queued":
            continue
        current["id"] = tid
        store.update(tid, status="processing", progress=0, started=time.time(), error="")
        if not os.path.exists(ENGINE):
            store.update(tid, status="error", progress=100,
                         error="Motor não encontrado. Coloque o waifu2x-ncnn-vulkan na pasta engine.")
            current["id"] = None
            continue
        try:
            ok, log = run_engine(task, None)
            if not ok and store.get(tid) and store.get(tid)["status"] == "processing":
                # Falha na GPU (driver/memória): tenta de novo só na CPU.
                store.update(tid, persist=False, progress=0, note="GPU falhou, usando CPU (mais lento)")
                engine_state["device"] = "cpu"
                ok, log = run_engine(task, -1)
        except OSError as exc:
            ok, log = False, str(exc)
        current["id"] = None
        latest = store.get(tid)
        if not latest or latest["status"] == "cancelled":
            continue
        if ok:
            data = open(result_path(task), "rb").read(64 * 1024)
            size = image_size(data, output_ext(task)) or (task["width"] * task["scale"], task["height"] * task["scale"])
            store.update(tid, status="done", progress=100, finished=time.time(),
                         out_size=os.path.getsize(result_path(task)), out_width=size[0], out_height=size[1])
        else:
            engine_state["last_error"] = log
            store.update(tid, status="error", progress=100, finished=time.time(),
                         error=log or "O motor terminou sem gerar a imagem.")


def requeue_pending():
    for t in store.all()[::-1]:
        if t["status"] in ("queued", "processing"):
            store.update(t["id"], status="queued", progress=0)
            jobs.put(t["id"])


# ---------------------------------------------------------------- HTTP
class Handler(BaseHTTPRequestHandler):
    server_version = "Nitida/1.0"

    def log_message(self, fmt, *args):
        pass

    # helpers
    def send_json(self, obj, status=200):
        body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def fail(self, status, message):
        self.send_json({"error": message}, status)

    def send_file(self, path, ctype, download_as=None):
        if not os.path.isfile(path):
            return self.fail(404, "Arquivo não encontrado.")
        size = os.path.getsize(path)
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(size))
        if download_as:
            quoted = urllib.parse.quote(download_as)
            self.send_header("Content-Disposition", f"attachment; filename*=UTF-8''{quoted}")
        self.end_headers()
        with open(path, "rb") as f:
            while chunk := f.read(1 << 20):
                self.wfile.write(chunk)

    def task_or_404(self, tid):
        if not ID_RE.match(tid or ""):
            self.fail(404, "Tarefa não encontrada.")
            return None
        t = store.get(tid)
        if not t:
            self.fail(404, "Tarefa não encontrada.")
        return t

    # rotas
    def do_GET(self):
        url = urllib.parse.urlsplit(self.path)
        q = urllib.parse.parse_qs(url.query)
        path = url.path
        static = {"/": ("index.html", "text/html; charset=utf-8"),
                  "/app.js": ("app.js", "text/javascript; charset=utf-8"),
                  "/style.css": ("style.css", "text/css; charset=utf-8")}
        if path in static:
            name, ctype = static[path]
            return self.send_file(os.path.join(STATIC, name), ctype)
        if path == "/api/info":
            return self.send_json({
                "engine_found": os.path.exists(ENGINE),
                "device": engine_state["device"],
                "queue": jobs.qsize() + (1 if current["id"] else 0),
                "limits": {"max_bytes": MAX_BYTES, "max_output_side": MAX_OUTPUT_SIDE,
                           "max_output_pixels": MAX_OUTPUT_PIXELS},
            })
        if path == "/api/tasks":
            items = store.all()
            if "ids" in q:
                wanted = set(q["ids"][0].split(","))
                items = [t for t in items if t["id"] in wanted]
                return self.send_json({"tasks": items})
            offset = int(q.get("offset", ["0"])[0])
            limit = min(100, int(q.get("limit", ["20"])[0]))
            return self.send_json({"tasks": items[offset:offset + limit], "total": len(items)})
        m = re.fullmatch(r"/api/tasks/([0-9a-f]+)/(input|result)", path)
        if m:
            t = self.task_or_404(m.group(1))
            if not t:
                return
            if m.group(2) == "input":
                return self.send_file(input_path(t), f"image/{'jpeg' if t['in_ext'] == 'jpg' else t['in_ext']}")
            if t["status"] != "done":
                return self.fail(409, "A imagem ainda não está pronta.")
            ext = output_ext(t)
            ctype = f"image/{'jpeg' if ext == 'jpg' else ext}"
            return self.send_file(result_path(t), ctype, download_name(t) if "download" in q else None)
        if path == "/api/zip":
            ids = [i for i in q.get("ids", [""])[0].split(",") if ID_RE.match(i)]
            buf = io.BytesIO()
            used = set()
            with zipfile.ZipFile(buf, "w", zipfile.ZIP_STORED) as z:
                for tid in ids:
                    t = store.get(tid)
                    if not t or t["status"] != "done" or not os.path.exists(result_path(t)):
                        continue
                    name, n = download_name(t), 1
                    while name in used:
                        base, ext = os.path.splitext(download_name(t))
                        name, n = f"{base}({n}){ext}", n + 1
                    used.add(name)
                    z.write(result_path(t), name)
            body = buf.getvalue()
            self.send_response(200)
            self.send_header("Content-Type", "application/zip")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Content-Disposition", 'attachment; filename="nitida.zip"')
            self.end_headers()
            self.wfile.write(body)
            return
        self.fail(404, "Rota não encontrada.")

    def do_POST(self):
        url = urllib.parse.urlsplit(self.path)
        q = {k: v[0] for k, v in urllib.parse.parse_qs(url.query).items()}
        if url.path == "/api/tasks":
            return self.create_task(q)
        m = re.fullmatch(r"/api/tasks/([0-9a-f]+)/retry", url.path)
        if m:
            t = self.task_or_404(m.group(1))
            if not t:
                return
            if t["status"] not in ("error", "cancelled"):
                return self.fail(409, "Só dá para tentar de novo uma tarefa que falhou.")
            store.update(t["id"], status="queued", progress=0, error="", note="")
            jobs.put(t["id"])
            return self.send_json(store.get(t["id"]))
        self.fail(404, "Rota não encontrada.")

    def do_DELETE(self):
        m = re.fullmatch(r"/api/tasks/([0-9a-f]+)", urllib.parse.urlsplit(self.path).path)
        if not m:
            return self.fail(404, "Rota não encontrada.")
        t = self.task_or_404(m.group(1))
        if not t:
            return
        store.update(t["id"], status="cancelled")
        if current["id"] == t["id"] and current["proc"]:
            current["proc"].kill()
            time.sleep(0.3)
        store.remove(t["id"])
        for p in (input_path(t), result_path(t)):
            try:
                os.remove(p)
            except OSError:
                pass
        self.send_json({"deleted": t["id"]})

    def create_task(self, q):
        try:
            style = q.get("style", "art")
            scale = int(q.get("scale", "2"))
            noise = int(q.get("noise", "-1"))
            fmt = q.get("format", "default")
        except ValueError:
            return self.fail(400, "Configuração inválida.")
        if style not in MODELS or scale not in SCALES or noise not in NOISES or fmt not in FORMATS:
            return self.fail(400, "Configuração inválida.")
        length = int(self.headers.get("Content-Length") or 0)
        if length <= 0:
            return self.fail(400, "Nenhuma imagem recebida.")
        if length > MAX_BYTES:
            return self.fail(413, "A imagem passa de 50 MB.")
        data = self.rfile.read(length)
        kind = sniff(data[:16])
        if not kind:
            return self.fail(415, "Formato não suportado. Use JPG, PNG ou WebP.")
        size = image_size(data, kind)
        if not size or min(size) < 1:
            return self.fail(415, "Não foi possível ler as dimensões da imagem.")
        w, h = size
        out_ext = kind if fmt == "default" else fmt
        ow, oh = w * scale, h * scale
        if max(ow, oh) > MAX_OUTPUT_SIDE[out_ext]:
            return self.fail(422, f"O resultado teria {ow}×{oh} px, acima do limite de "
                                  f"{MAX_OUTPUT_SIDE[out_ext]} px por lado para {out_ext.upper()}. "
                                  "Use uma ampliação menor ou outro formato.")
        if ow * oh > MAX_OUTPUT_PIXELS:
            return self.fail(422, f"O resultado teria {ow * oh / 1e6:.0f} megapixels. "
                                  "Use uma ampliação menor.")
        name = urllib.parse.unquote(self.headers.get("X-File-Name", "imagem"))
        name = re.sub(r'[\\/:*?"<>|\x00-\x1f]', "_", name)[:180] or "imagem"
        task = {
            "id": uuid.uuid4().hex, "name": name, "in_ext": kind, "size": length,
            "width": w, "height": h, "style": style, "scale": scale, "noise": noise,
            "format": fmt, "status": "queued", "progress": 0, "error": "", "note": "",
            "created": time.time(), "started": None, "finished": None,
            "out_size": None, "out_width": None, "out_height": None,
        }
        with open(input_path(task), "wb") as f:
            f.write(data)
        store.add(task)
        jobs.put(task["id"])
        self.send_json(task, 201)


def main():
    threading.Thread(target=worker, daemon=True).start()
    requeue_pending()
    httpd = ThreadingHTTPServer(("127.0.0.1", PORT), Handler)
    url = f"http://127.0.0.1:{PORT}/"
    print(f"Nítida rodando em {url}  (Ctrl+C para parar)")
    if not os.path.exists(ENGINE):
        print("Aviso: motor não encontrado em", ENGINE)
    if "--no-browser" not in sys.argv:
        threading.Timer(0.8, lambda: webbrowser.open(url)).start()
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
