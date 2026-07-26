# -*- coding: utf-8 -*-
"""HTTP-слой: тонкий клиент к API бота + хранение ключа."""
import base64
import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request

DEFAULT_URL = os.environ.get("COMFUIBOT_URL", "http://127.0.0.1:8090")
CONF_DIR = os.path.join(os.path.expanduser("~"), ".comfuibot")
CONF_FILE = os.path.join(CONF_DIR, "config.json")
OUT_DIR = os.environ.get("COMFUIBOT_OUT", os.path.join(os.path.expanduser("~"), "comfuibot-out"))
BOT_LINK = os.environ.get("COMFUIBOT_TG", "https://t.me/comfuibot")


# ─────────────────────────── конфиг (ключ/урл) ───────────────────────────
def load_config() -> dict:
    try:
        with open(CONF_FILE, encoding="utf-8") as fh:
            return json.load(fh)
    except Exception:
        return {}


def save_config(cfg: dict) -> None:
    os.makedirs(CONF_DIR, exist_ok=True)
    with open(CONF_FILE, "w", encoding="utf-8") as fh:
        json.dump(cfg, fh, ensure_ascii=False, indent=2)
    # ключ — приватная штука: на POSIX сузим права
    try:
        if os.name != "nt":
            os.chmod(CONF_FILE, 0o600)
    except Exception:
        pass


def get_key(explicit: str = "") -> str:
    return (explicit or os.environ.get("COMFUIBOT_KEY", "")
            or load_config().get("key", "")).strip()


def get_url(explicit: str = "") -> str:
    return (explicit or load_config().get("url", "") or DEFAULT_URL).rstrip("/")


# ─────────────────────────── низкий уровень ───────────────────────────
class ApiError(Exception):
    def __init__(self, message, code=""):
        super().__init__(message)
        self.code = code


def _request(method, url, payload=None, timeout=300, raw=False):
    data = None
    headers = {}
    if payload is not None:
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=data, method=method, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            body = r.read()
            ct = r.headers.get("Content-Type", "")
            if raw and "application/json" not in ct:
                return body
            return json.loads(body.decode("utf-8", "replace"))
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", "replace")
        try:
            j = json.loads(body)
            raise ApiError(j.get("message") or j.get("errorCode") or f"HTTP {e.code}",
                           j.get("errorCode", ""))
        except ApiError:
            raise
        except Exception:
            raise ApiError(f"HTTP {e.code}: {body[:200]}")
    except urllib.error.URLError as e:
        raise ApiError(f"сервер недоступен: {e.reason}")
    except Exception as e:
        raise ApiError(f"{type(e).__name__}: {e}")


class Client:
    def __init__(self, key="", url="", session=""):
        self.key = get_key(key)
        self.url = get_url(url)
        self.session = session or f"cli-{int(time.time())}"

    # --- служебное ---
    def health(self):
        return _request("GET", f"{self.url}/v1/health", timeout=20)

    def keyinfo(self):
        q = urllib.parse.quote(self.key)
        return _request("GET", f"{self.url}/v1/keyinfo?apiKey={q}", timeout=30)

    def styles(self):
        """Готовые персоны для чата (/v1/talk/styles)."""
        r = _request("GET", f"{self.url}/v1/talk/styles", timeout=30)
        return r if isinstance(r, dict) else {}

    # --- чат ---
    def talk(self, message, style="", system_prompt=""):
        body = {"apiKey": self.key, "message": message, "session_id": self.session}
        if style:
            body["style"] = style
        if system_prompt:
            body["system_prompt"] = system_prompt
        r = _request("POST", f"{self.url}/v1/talk", body, timeout=300)
        return r.get("reply") or r.get("text") or ""

    # --- кодер ---
    def code(self, prompt):
        return _request("POST", f"{self.url}/v1/code",
                        {"apiKey": self.key, "prompt": prompt, "session_id": self.session},
                        timeout=900)

    # --- генерация ---
    def generate(self, kind, prompt, image_b64=None, params=None):
        body = {"apiKey": self.key, "type": kind, "prompt": prompt}
        if params:
            body["params"] = params
        if image_b64:
            body["image_base64"] = image_b64
        r = _request("POST", f"{self.url}/v1/generate", body, timeout=120)
        # Сервер отдаёт идентификатор как request_id/requestId (jobId — легаси).
        jid = (r.get("request_id") or r.get("requestId")
               or r.get("jobId") or r.get("job_id") or r.get("id"))
        if not jid:
            raise ApiError(f"нет request_id в ответе: {str(r)[:150]}")
        return jid

    def status(self, job_id):
        q = urllib.parse.quote(self.key)
        return _request("GET", f"{self.url}/v1/status/{job_id}?apiKey={q}", timeout=60)

    def result_bytes(self, job_id):
        """Забрать готовый файл. Сервер отдаёт ЛИБО сами байты (Content-Type
        image/*), ЛИБО JSON с resultUrl — тогда качаем по ссылке."""
        q = urllib.parse.quote(self.key)
        r = _request("GET", f"{self.url}/v1/result/{job_id}?apiKey={q}", timeout=180, raw=True)
        if isinstance(r, (bytes, bytearray)):
            return bytes(r)
        r = r or {}
        url = r.get("resultUrl") or r.get("result_url") or r.get("url")
        if url:
            # Ссылка может указывать на внутренний хост (API_BASE_URL) — если мы
            # ходим на другой адрес, переклеим путь на наш базовый URL.
            try:
                p = urllib.parse.urlparse(url)
                if p.netloc and p.netloc not in self.url:
                    url = f"{self.url}{p.path}"
            except Exception:
                pass
            sep = "&" if "?" in url else "?"
            data = _request("GET", f"{url}{sep}apiKey={q}", timeout=180, raw=True)
            if isinstance(data, (bytes, bytearray)):
                return bytes(data)
            raise ApiError(f"по resultUrl пришёл не файл: {str(data)[:120]}")
        b64 = r.get("image_base64") or r.get("image") or ""
        if not b64:
            raise ApiError(r.get("message") or f"нет картинки в ответе: {str(r)[:120]}")
        if "," in b64[:64]:
            b64 = b64.split(",", 1)[1]
        return base64.b64decode(b64)

    def save_result(self, job_id, name_hint="img"):
        raw = self.result_bytes(job_id)
        os.makedirs(OUT_DIR, exist_ok=True)
        safe = "".join(c for c in name_hint if c.isalnum() or c in " -_")[:40].strip() or "img"
        path = os.path.join(OUT_DIR, f"{safe}_{int(time.time())}.png")
        with open(path, "wb") as fh:
            fh.write(raw)
        return path
