# -*- coding: utf-8 -*-
"""
comfuibot — console-side bot: консольный клиент к API ComfyBot.

Команды:
  comfuibot api status          — жив ли сервер, polling, версия
  comfuibot api auth            — вставить/сменить API-ключ (сохраняется локально)
  comfuibot api usage           — что за ключ: тариф, срок, лимиты, расход
  comfuibot chat                — интерактивный чат с ИИ (или: chat "вопрос")
  comfuibot photo gen "промпт"  — сгенерировать картинку
  comfuibot photo edit ФАЙЛ "что изменить"
  comfuibot coder ai "задача"   — ИИ пишет и ЗАПУСКАЕТ код, показывает вывод
  comfuibot enter               — открыть TG-side бота (ссылка)
"""
import argparse
import base64
import os
import sys
import time

from . import __version__
from .api import (BOT_LINK, OUT_DIR, ApiError, Client, get_key, get_url,
                  load_config, save_config)


class C:
    R = "\033[0m"; B = "\033[1m"; D = "\033[2m"
    CY = "\033[36m"; GR = "\033[32m"; YE = "\033[33m"; RD = "\033[31m"; MA = "\033[35m"


def _ansi():
    if os.name == "nt":
        try:
            import ctypes
            k = ctypes.windll.kernel32
            k.SetConsoleMode(k.GetStdHandle(-11), 7)
        except Exception:
            pass


def _err(msg):
    print(f"{C.RD}✗ {msg}{C.R}")
    return 1


def _need_key():
    k = get_key()
    if not k:
        print(f"{C.YE}Ключа нет. Выполни:{C.R} comfuibot api auth")
        print(f"{C.D}Получить ключ: {BOT_LINK} → /apikey{C.R}")
        return None
    return k


def _open(path):
    try:
        if os.name == "nt":
            os.startfile(path)  # noqa
        elif sys.platform == "darwin":
            os.system(f'open "{path}"')
        else:
            os.system(f'xdg-open "{path}" >/dev/null 2>&1 &')
    except Exception:
        pass


def _wait(cl, jid, label="генерация"):
    """Ждать джоб, печатая прогресс в одну строку."""
    t0 = time.time()
    last = ""
    while time.time() - t0 < 1800:
        try:
            st = cl.status(jid)
        except ApiError as e:
            return None, str(e)
        s = str(st.get("status") or st.get("state") or "")
        bits = [s or "…"]
        q = st.get("queuePosition") or st.get("queue")
        if q not in (None, "", 0):
            bits.append(f"очередь: {q}")
        p = st.get("progress")
        if p not in (None, ""):
            bits.append(f"{p}%")
        line = f"  {C.D}⏳ {label}: {' · '.join(bits)}      {C.R}"
        if line != last:
            sys.stdout.write("\r" + line)
            sys.stdout.flush()
            last = line
        if s in ("done", "completed", "ok", "finished", "success"):
            sys.stdout.write("\r" + " " * 70 + "\r")
            return st, None
        if s in ("error", "failed", "cancelled", "canceled"):
            sys.stdout.write("\r" + " " * 70 + "\r")
            return None, st.get("error") or st.get("message") or s
        time.sleep(2)
    return None, "таймаут"


# ──────────────────────────── api ────────────────────────────
def cmd_api_status(args):
    cl = Client(url=args.url)
    print(f"{C.D}сервер: {cl.url}{C.R}")
    try:
        h = cl.health()
    except ApiError as e:
        return _err(f"{e}")
    ok = h.get("status") == "ok"
    mark = f"{C.GR}✓ жив{C.R}" if ok else f"{C.RD}✗ проблема{C.R}"
    print(f"  состояние : {mark}")
    print(f"  имя       : {h.get('name','—')}")
    print(f"  polling   : {h.get('polling','—')} {C.D}(возраст {h.get('pollingAgeSec','?')}с){C.R}")
    up = h.get("uptime")
    if up:
        print(f"  uptime    : {int(up)//3600}ч {int(up)%3600//60}м")
    print(f"  ключ      : {'есть' if get_key() else f'{C.YE}нет — comfuibot api auth{C.R}'}")
    return 0


def cmd_api_auth(args):
    cfg = load_config()
    if args.key:
        key = args.key.strip()
    else:
        cur = cfg.get("key", "")
        if cur:
            print(f"{C.D}текущий ключ: {cur[:12]}…{C.R}")
        print(f"{C.YE}Вставь API-ключ{C.R} {C.D}(получить: {BOT_LINK} → /apikey){C.R}")
        try:
            key = input("  ключ> ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            return 1
    if not key:
        return _err("пустой ключ")
    cfg["key"] = key
    if args.url:
        cfg["url"] = args.url.rstrip("/")
    save_config(cfg)
    print(f"{C.GR}✓ ключ сохранён{C.R} {C.D}({os.path.join(os.path.expanduser('~'), '.comfuibot', 'config.json')}){C.R}")
    cl = Client()
    try:
        info = cl.keyinfo()
        if isinstance(info, dict) and not info.get("errorCode"):
            print(f"{C.GR}✓ ключ принят сервером{C.R}")
    except ApiError as e:
        print(f"{C.YE}! проверить не удалось: {e}{C.R}")
    return 0


def cmd_api_usage(args):
    if not _need_key():
        return 1
    cl = Client(url=args.url)
    try:
        info = cl.keyinfo()
    except ApiError as e:
        return _err(str(e))
    if not isinstance(info, dict):
        return _err("неожиданный ответ сервера")
    if info.get("errorCode"):
        return _err(info.get("message") or info["errorCode"])
    print(f"{C.B}Ключ{C.R} {C.D}{get_key()[:14]}…{C.R}")
    rows = [
        ("владелец", info.get("owner")),
        ("тариф", (f"{info.get('tariff_rub')}₽" if info.get("tariff_rub") else None)),
        ("доступ (scope)", info.get("scope")),
        ("истекает", info.get("expires_at_human") or info.get("expires_at")),
        ("осталось дней", info.get("days_left")),
        ("лимит в день", info.get("daily_limit")),
        ("использовано сегодня", info.get("used_today") or info.get("today")),
        ("всего запросов", info.get("total") or info.get("requests")),
    ]
    for k, v in rows:
        if v not in (None, "", []):
            print(f"  {k:22}: {v}")
    return 0


# ──────────────────────────── chat ────────────────────────────
def cmd_chat(args):
    if not _need_key():
        return 1
    cl = Client(url=args.url)
    style = args.style or ""
    one = " ".join(args.text or []).strip()
    if one:
        try:
            print(f"{C.CY}{cl.talk(one, style=style)}{C.R}")
            return 0
        except ApiError as e:
            return _err(str(e))
    print(f"{C.MA}{C.B}chat{C.R} {C.D}— пиши сообщение, /exit чтобы выйти"
          f"{' · персона: ' + style if style else ''}{C.R}")
    while True:
        try:
            line = input(f"\n{C.B}{C.CY}›{C.R} ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\nпока!")
            return 0
        if not line:
            continue
        if line in ("/exit", "/quit", "exit"):
            print("пока!")
            return 0
        try:
            print(f"{C.CY}{cl.talk(line, style=style)}{C.R}")
        except ApiError as e:
            _err(str(e))


# ──────────────────────────── photo ────────────────────────────
def cmd_photo_gen(args):
    if not _need_key():
        return 1
    prompt = " ".join(args.prompt or []).strip()
    if not prompt:
        return _err('нужен промпт: comfuibot photo gen "закат над морем"')
    cl = Client(url=args.url)
    params = {}
    if args.steps:
        params["steps"] = args.steps
    if args.size:
        params["size"] = args.size
    try:
        jid = cl.generate("txt2img", prompt, params=params or None)
    except ApiError as e:
        return _err(str(e))
    print(f"{C.D}job {jid}{C.R}")
    st, err = _wait(cl, jid, "рисую")
    if err:
        return _err(err)
    try:
        path = cl.save_result(jid, prompt)
    except ApiError as e:
        return _err(str(e))
    print(f"{C.GR}✓ {path}{C.R}")
    if not args.no_open:
        _open(path)
    return 0


def cmd_photo_edit(args):
    if not _need_key():
        return 1
    src = args.file
    instr = " ".join(args.instruction or []).strip()
    if not src or not instr:
        return _err('нужно: comfuibot photo edit файл.png "сделай зиму"')
    if not os.path.exists(src):
        return _err(f"нет файла: {src}")
    with open(src, "rb") as fh:
        b64 = base64.b64encode(fh.read()).decode()
    cl = Client(url=args.url)
    try:
        jid = cl.generate("img2img", instr, image_b64=b64)
    except ApiError as e:
        return _err(str(e))
    print(f"{C.D}job {jid}{C.R}")
    st, err = _wait(cl, jid, "переделываю")
    if err:
        return _err(err)
    try:
        path = cl.save_result(jid, instr)
    except ApiError as e:
        return _err(str(e))
    print(f"{C.GR}✓ {path}{C.R}")
    if not args.no_open:
        _open(path)
    return 0


# ──────────────────────────── coder ────────────────────────────
def cmd_coder_ai(args):
    if not _need_key():
        return 1
    cl = Client(url=args.url)
    task = " ".join(args.task or []).strip()
    if task:
        return _coder_once(cl, task)
    print(f"{C.MA}{C.B}coder ai{C.R} {C.D}— задача текстом, ИИ напишет и ЗАПУСТИТ код. /exit — выход{C.R}")
    while True:
        try:
            line = input(f"\n{C.B}{C.MA}code›{C.R} ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\nпока!")
            return 0
        if not line:
            continue
        if line in ("/exit", "/quit", "exit"):
            return 0
        _coder_once(cl, line)


def _coder_once(cl, task):
    print(f"{C.D}⏳ ИИ пишет и запускает код…{C.R}")
    try:
        r = cl.code(task)
    except ApiError as e:
        return _err(str(e))
    langs = r.get("languages") or []
    if langs:
        print(f"{C.D}языки на сервере: {', '.join(langs)}{C.R}")
    for ev in r.get("events", []):
        rc = ev.get("rc")
        head = f"{C.MA}▶ {ev.get('tool')}{C.R}"
        head += f" {C.GR}(ok){C.R}" if rc == 0 else f" {C.RD}(код выхода {rc}){C.R}"
        print(f"\n{head}")
        code = ev.get("code") or ""
        for ln in code.splitlines():
            print(f"  {C.D}{ln}{C.R}")
        if ev.get("stdout"):
            print(f"  {C.GR}{ev['stdout']}{C.R}")
        if ev.get("stderr"):
            print(f"  {C.RD}{ev['stderr']}{C.R}")
    if r.get("result"):
        print(f"\n{C.B}{r['result']}{C.R}")
    return 0


# ──────────────────────────── enter ────────────────────────────
def cmd_enter(args):
    print(f"{C.B}TG-side bot:{C.R} {C.CY}{BOT_LINK}{C.R}")
    print(f"{C.D}Там: генерация фото/видео, /apikey для ключа, /talk для чата.{C.R}")
    if not args.no_open:
        try:
            import webbrowser
            webbrowser.open(BOT_LINK)
        except Exception:
            pass
    return 0


# ──────────────────────────── parser ────────────────────────────
def build_parser():
    p = argparse.ArgumentParser(
        prog="comfuibot",
        description="console-side bot — консольный клиент к API ComfyBot")
    p.add_argument("--version", action="version", version=f"comfuibot {__version__}")
    p.add_argument("--url", default="", help="адрес API (по умолчанию из конфига/env)")
    sub = p.add_subparsers(dest="group")

    # api
    api = sub.add_parser("api", help="служебное: status / auth / usage")
    apisub = api.add_subparsers(dest="action")
    s = apisub.add_parser("status", help="жив ли сервер")
    s.set_defaults(func=cmd_api_status)
    a = apisub.add_parser("auth", help="вставить/сменить API-ключ")
    a.add_argument("key", nargs="?", default="", help="ключ (иначе спросит)")
    a.set_defaults(func=cmd_api_auth)
    u = apisub.add_parser("usage", help="тариф, срок, лимиты ключа")
    u.set_defaults(func=cmd_api_usage)

    # chat
    c = sub.add_parser("chat", help="чат с ИИ (интерактивно или одним вопросом)")
    c.add_argument("text", nargs="*")
    c.add_argument("--style", default="", help="персона (tehpod/gazetovich/komandirovich…)")
    c.set_defaults(func=cmd_chat)

    # photo
    ph = sub.add_parser("photo", help="картинки: gen / edit")
    phsub = ph.add_subparsers(dest="action")
    g = phsub.add_parser("gen", help="сгенерировать по тексту")
    g.add_argument("prompt", nargs="*")
    g.add_argument("--steps", type=int, default=0)
    g.add_argument("--size", default="")
    g.add_argument("--no-open", action="store_true", help="не открывать результат")
    g.set_defaults(func=cmd_photo_gen)
    e = phsub.add_parser("edit", help="переделать существующую картинку")
    e.add_argument("file")
    e.add_argument("instruction", nargs="*")
    e.add_argument("--no-open", action="store_true")
    e.set_defaults(func=cmd_photo_edit)

    # coder
    co = sub.add_parser("coder", help="кодер: ai")
    cosub = co.add_subparsers(dest="action")
    ai = cosub.add_parser("ai", help="ИИ пишет и запускает код")
    ai.add_argument("task", nargs="*")
    ai.set_defaults(func=cmd_coder_ai)

    # enter
    en = sub.add_parser("enter", help="открыть TG-side бота")
    en.add_argument("--no-open", action="store_true")
    en.set_defaults(func=cmd_enter)

    return p


def main(argv=None):
    _ansi()
    p = build_parser()
    args = p.parse_args(argv)
    func = getattr(args, "func", None)
    if func is None:
        # группа без действия (напр. просто `comfuibot api`) → покажем помощь группы
        if getattr(args, "group", None):
            for act in p._subparsers._group_actions:
                for name, sp in act.choices.items():
                    if name == args.group:
                        sp.print_help()
                        return 1
        p.print_help()
        return 1
    try:
        return func(args) or 0
    except KeyboardInterrupt:
        print("\nпрервано")
        return 130


if __name__ == "__main__":
    sys.exit(main())
