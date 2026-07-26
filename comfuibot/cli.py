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


def _getkey():
    """Прочитать одну клавишу (стрелки/Enter/Esc). Возвращает 'up','down',
    'enter','esc','back' или сам символ. Фоллбэк на input(), если терминал
    не поддерживает посимвольное чтение (пайп, IDE-консоль)."""
    # Не-терминал (пайп, CI, IDE-консоль): посимвольное чтение зависнет —
    # сразу уходим в нумерованный фоллбэк.
    try:
        if not sys.stdin.isatty():
            return "fallback"
    except Exception:
        return "fallback"
    try:
        if os.name == "nt":
            import msvcrt
            ch = msvcrt.getch()
            if ch in (b"\x00", b"\xe0"):          # префикс спецклавиш
                c2 = msvcrt.getch()
                return {b"H": "up", b"P": "down", b"K": "back", b"M": "enter"}.get(c2, "")
            if ch in (b"\r", b"\n"):
                return "enter"
            if ch == b"\x1b":
                return "esc"
            if ch == b"\x03":
                raise KeyboardInterrupt
            try:
                return ch.decode("utf-8", "ignore").lower()
            except Exception:
                return ""
        import termios
        import tty
        fd = sys.stdin.fileno()
        old = termios.tcgetattr(fd)
        try:
            tty.setraw(fd)
            ch = sys.stdin.read(1)
            if ch == "\x1b":
                nxt = sys.stdin.read(2)
                return {"[A": "up", "[B": "down", "[D": "back", "[C": "enter"}.get(nxt, "esc")
            if ch in ("\r", "\n"):
                return "enter"
            if ch == "\x03":
                raise KeyboardInterrupt
            return ch.lower()
        finally:
            termios.tcsetattr(fd, termios.TCSADRAIN, old)
    except (ImportError, OSError, AttributeError):
        return "fallback"


def _menu(title, items, footer=""):
    """Карточка-меню со стрелками. items = [(ключ, подпись)].
    Возвращает ключ выбранного пункта или None (Esc / ← назад).
    В каждой карточке есть выход на предыдущую страницу."""
    items = list(items) + [("__back__", "← назад")]
    idx = 0
    first = True
    while True:
        if not first:
            # поднимаемся на высоту меню и перерисовываем
            sys.stdout.write(f"\033[{len(items) + 2}A")
        first = False
        print(f"\n  {C.B}{title}{C.R}" + (f"  {C.D}{footer}{C.R}" if footer else ""))
        for i, (_k, label) in enumerate(items):
            if i == idx:
                print(f"  {C.GR}❯{C.R} {C.B}{C.CY}{label:<48}{C.R}")
            else:
                print(f"    {C.D}{label:<48}{C.R}")
        k = _getkey()
        if k == "fallback":                      # терминал без raw-режима
            print(f"  {C.D}(стрелки недоступны — введи номер){C.R}")
            for i, (_kk, label) in enumerate(items, 1):
                print(f"    {i}) {label}")
            try:
                ans = input("  номер: ").strip()
            except (EOFError, KeyboardInterrupt):
                return None
            if ans.isdigit() and 1 <= int(ans) <= len(items):
                key = items[int(ans) - 1][0]
                return None if key == "__back__" else key
            return None
        if k == "up":
            idx = (idx - 1) % len(items)
        elif k == "down":
            idx = (idx + 1) % len(items)
        elif k in ("esc", "back", "q"):
            print()
            return None
        elif k == "enter":
            key = items[idx][0]
            print()
            return None if key == "__back__" else key
        elif k.isdigit() and 1 <= int(k) <= len(items):
            key = items[int(k) - 1][0]
            print()
            return None if key == "__back__" else key


def _after_gen_menu(cl, path, prompt, params, kind="txt2img", src_b64=None):
    """Меню после генерации: что делаем с картинкой. Работает стрелками,
    в каждой карточке есть «← назад» (выход в консоль)."""
    while True:
        act = _menu(
            f"Готово: {os.path.basename(path)}",
            [
                ("open", "👁  открыть картинку"),
                ("again", "🔁 сгенерировать ещё раз (новый seed)"),
                ("edit", "✏️  переделать эту картинку"),
                ("tweak", "🎨 поменять промпт и перерисовать"),
                ("folder", "📂 открыть папку с результатами"),
                ("copy", "📋 показать путь к файлу"),
            ],
            footer="↑↓ выбор · Enter · Esc выход",
        )
        if act is None:
            return 0
        if act == "open":
            _open(path)
        elif act == "folder":
            _open(os.path.dirname(path))
        elif act == "copy":
            print(f"  {C.CY}{path}{C.R}")
        elif act in ("again", "tweak", "edit"):
            new_prompt = prompt
            if act == "tweak":
                try:
                    new_prompt = input(f"  новый промпт {C.D}[{prompt[:40]}…]{C.R}: ").strip() or prompt
                except (EOFError, KeyboardInterrupt):
                    continue
            b64 = None
            new_kind = "txt2img"
            if act == "edit":
                try:
                    instr = input("  что изменить: ").strip()
                except (EOFError, KeyboardInterrupt):
                    continue
                if not instr:
                    continue
                with open(path, "rb") as fh:
                    b64 = base64.b64encode(fh.read()).decode()
                new_prompt, new_kind = instr, "img2img"
            try:
                jid = cl.generate(new_kind, new_prompt, image_b64=b64, params=params)
            except ApiError as e:
                _err(str(e))
                continue
            st, err = _wait(cl, jid, "рисую")
            if err:
                _err(err)
                continue
            try:
                path = cl.save_result(jid, new_prompt)
            except ApiError as e:
                _err(str(e))
                continue
            print(f"{C.GR}✓ {path}{C.R}")
            prompt = new_prompt


def _size_param(val):
    """Привести размер к тому, что понимает API: число (квадрат) или «WxH».

    Соотношения сторон («9:16») API НЕ понимает — он парсит size как int или WxH,
    поэтому «9:16» молча превращался в квадрат. Разворачиваем сами. 2026-07-26.
    """
    if not val:
        return None
    s = str(val).strip().lower().replace(" ", "")
    ratios = {
        "9:16": "768x1344",   # вертикаль (сторис)
        "16:9": "1344x768",   # горизонт (обои)
        "3:4": "896x1200",
        "4:3": "1200x896",
        "2:3": "832x1248",
        "3:2": "1248x832",
        "1:1": 1024,
    }
    if s in ratios:
        return ratios[s]
    if "x" in s:                       # уже WxH — отдаём как есть
        return s
    try:
        return int(float(s))           # число = квадрат
    except ValueError:
        return None


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
            # Показываем, ЧТО за ключ: тариф, срок, лимиты — сразу, без лишней команды.
            scope = info.get("scope", "")
            if scope:
                print(f"  {C.D}доступ: {scope} · истекает {info.get('expires_at_human','—')} "
                      f"· лимит {info.get('daily_limit','—')}/день{C.R}")
    except ApiError as e:
        print(f"{C.YE}! проверить не удалось: {e}{C.R}")
    # Сразу открываем меню — человек не должен гадать, что набирать дальше.
    # (Пропускаем, если ключ передали аргументом: значит вызов скриптовый.)
    if not getattr(args, "no_menu", False) and not args.key:
        return cmd_menu(args)
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
    print(f"\n{C.B}🔑 Ключ{C.R} {C.D}{get_key()[:14]}…{C.R}\n")
    rows = [
        ("владелец", info.get("owner")),
        ("тариф", (f"{info.get('tariff_rub')}₽" if info.get("tariff_rub") else None)),
        ("доступ (scope)", info.get("scope")),
        ("тип ключа", info.get("use_case")),
        ("истекает", info.get("expires_at_human") or info.get("expires_at")),
    ]
    for k, v in rows:
        if v not in (None, "", []):
            print(f"  {k:22}: {v}")

    # Срок: сколько дней прожито из купленных + полоска.
    total_days = info.get("tariff_days")
    left = info.get("days_left")
    try:
        total_days = int(total_days)
    except (TypeError, ValueError):
        total_days = None
    if left == "∞" or (total_days and total_days >= 3650):
        print(f"  {'срок':22}: {C.GR}♾ навсегда{C.R}")
    elif total_days and isinstance(left, int):
        used_days = max(0, total_days - left)
        print(f"  {'дней использовано':22}: {used_days} из {total_days} "
              f"{C.D}(осталось {left}){C.R}")
        print(f"  {'срок':22}: {_bar(used_days, total_days)}")
    elif left not in (None, ""):
        print(f"  {'осталось дней':22}: {left}")

    # Дневная квота: расход с полоской.
    lim = info.get("daily_limit")
    lim = 0 if lim in ("без лимита", None, "") else lim
    used = info.get("used_today") or 0
    print(f"  {'сегодня запросов':22}: {_bar(used, lim)}")
    if info.get("admin_grant"):
        print(f"  {'':22}  {C.MA}🛡 админский ключ{C.R}")
    print()
    return 0


# ──────────────────────────── chat ────────────────────────────
def cmd_chat(args):
    if not _need_key():
        return 1
    cl = Client(url=args.url)
    # Персона: флаг --style важнее, иначе берём сохранённую (comfuibot style <имя>).
    style = args.style or load_config().get("style", "")
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
    _sz = _size_param(args.size)
    if _sz is not None:
        params["size"] = _sz
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
        # После генерации — карточка с действиями (стрелки, «← назад»).
        return _after_gen_menu(cl, path, prompt, params)
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


# ──────────────────────────── hud ────────────────────────────
def _bar(used, limit, width=22):
    """Полоска расхода лимита."""
    try:
        used, limit = int(used), int(limit)
    except (TypeError, ValueError):
        return ""
    if limit <= 0:
        return f"{C.GR}безлимит{C.R}"
    frac = min(1.0, used / limit)
    fill = int(frac * width)
    col = C.GR if frac < 0.6 else (C.YE if frac < 0.9 else C.RD)
    return f"{col}{'█' * fill}{C.D}{'░' * (width - fill)}{C.R} {used}/{limit}"


def cmd_hud(args):
    """Консольный HUD: одним экраном — сервер, ключ, лимиты, очередь."""
    cl = Client(url=args.url)
    W = 54
    def line(l, r=""):
        pad = W - len(_strip(l)) - len(_strip(r))
        print(f"  {l}{' ' * max(1, pad)}{r}")

    print(f"\n{C.MA}{C.B}┌{'─' * (W + 2)}┐{C.R}")
    print(f"{C.MA}{C.B}│{C.R}  {C.B}comfuibot HUD{C.R}{C.D} · console-side{C.R}"
          f"{' ' * (W - 29)}{C.MA}{C.B}│{C.R}")
    print(f"{C.MA}{C.B}└{'─' * (W + 2)}┘{C.R}")

    try:
        h = cl.health()
        ok = h.get("status") == "ok"
        line(f"{'✓' if ok else '✗'} сервер",
             f"{C.GR if ok else C.RD}{'жив' if ok else 'недоступен'}{C.R}")
        line("  polling", f"{C.D}{h.get('polling','—')} ({h.get('pollingAgeSec','?')}с){C.R}")
        up = int(h.get("uptime") or 0)
        if up:
            line("  uptime", f"{C.D}{up // 3600}ч {up % 3600 // 60}м{C.R}")
    except ApiError as e:
        line("✗ сервер", f"{C.RD}{str(e)[:28]}{C.R}")
    line("  адрес", f"{C.D}{cl.url}{C.R}")

    if not cl.key:
        line("✗ ключ", f"{C.YE}нет — comfuibot api auth{C.R}")
        print()
        return 0
    try:
        i = cl.keyinfo()
        line("✓ ключ", f"{C.D}{cl.key[:12]}…{C.R}")
        line("  доступ", f"{C.CY}{i.get('scope','?')}{C.R}")
        line("  истекает", f"{C.D}{i.get('expires_at_human','—')} "
                           f"(осталось {i.get('days_left','?')}д){C.R}")
        lim = i.get("daily_limit")
        lim = 0 if lim in ("без лимита", None, "") else lim
        line("  сегодня", _bar(i.get("used_today", 0), lim))
    except ApiError as e:
        line("! ключ", f"{C.YE}{str(e)[:30]}{C.R}")
    print(f"\n  {C.D}команды: chat · photo gen · coder ai · help{C.R}\n")
    return 0


def _strip(s):
    """Длина строки без ANSI-кодов."""
    import re
    return re.sub(r"\033\[[0-9;]*m", "", s)


# ──────────────────────────── style ────────────────────────────
def cmd_style(args):
    """Выбор персоны чата: список, установка, сброс."""
    cfg = load_config()
    cl = Client(url=args.url)
    if args.name:
        if args.name in ("off", "-", "нет", "reset"):
            cfg.pop("style", None)
            save_config(cfg)
            print(f"{C.GR}✓ персона сброшена (обычный Газетович){C.R}")
        else:
            cfg["style"] = args.name
            save_config(cfg)
            print(f"{C.GR}✓ персона: {args.name}{C.R} {C.D}(применится в chat){C.R}")
        return 0
    try:
        items = _fetch_styles(cl)
    except ApiError as e:
        return _err(str(e))
    if not items:
        return _err("сервер не вернул список персон")
    cur = cfg.get("style", "")
    print(f"\n{C.B}🎭 Персоны чата{C.R} {C.D}(сейчас: {cur or 'по умолчанию'}){C.R}\n")
    for n, (code, label) in enumerate(items, 1):
        mark = f"{C.GR}●{C.R}" if code == cur else " "
        print(f"  {mark} {C.CY}{n:>2}{C.R}) {label:<34} {C.D}{code}{C.R}")
    print(f"\n  {C.D} 0) сбросить на обычного Газетовича{C.R}")
    # Визард выбора: сразу спрашиваем номер, как «квадратики» в боте.
    try:
        ans = input(f"\n  {C.B}выбери номер{C.R} {C.D}(Enter — оставить как есть){C.R}: ").strip()
    except (EOFError, KeyboardInterrupt):
        print()
        return 0
    if not ans:
        print(f"{C.D}без изменений{C.R}")
        return 0
    if ans == "0":
        cfg.pop("style", None)
        save_config(cfg)
        print(f"{C.GR}✓ персона сброшена{C.R}")
        return 0
    code = None
    if ans.isdigit() and 1 <= int(ans) <= len(items):
        code = items[int(ans) - 1][0]
    else:
        for c, _l in items:
            if c == ans.lower():
                code = c
                break
    if not code:
        return _err(f"нет такой персоны: {ans}")
    cfg["style"] = code
    save_config(cfg)
    label = next((l for c, l in items if c == code), code)
    print(f"{C.GR}✓ персона: {label}{C.R} {C.D}(применится в comfuibot chat){C.R}")
    return 0


def _fetch_styles(cl):
    """Список персон с сервера → [(code, label)]. Формат /v1/talk/styles:
    {"styles":[{"code":"tehpod","label":"🛜 Саппортович (техподдержка)"}, …]}"""
    st = cl.styles()
    raw = st.get("styles") or st.get("data") or []
    out = []
    if isinstance(raw, dict):
        for k, v in raw.items():
            out.append((str(k), str(v)))
    else:
        for it in raw:
            if isinstance(it, dict):
                code = it.get("code") or it.get("id") or it.get("name") or ""
                label = it.get("label") or it.get("title") or it.get("description") or code
                if code:
                    out.append((str(code), str(label)))
            elif it:
                out.append((str(it), str(it)))
    return out


# ──────────────────────────── wizard ────────────────────────────
def _ask(prompt, default="", options=None):
    """Шаг визарда. Если есть варианты — выбор СТРЕЛКАМИ (как в меню), иначе
    обычный ввод текста. Раньше варианты печатались списком и надо было
    набирать номер — теперь везде единая навигация."""
    if options:
        sel = _menu(
            prompt,
            [(val, f"{val:<16} {C.D}{desc}{C.R}") for val, desc in options],
            footer=(f"по умолчанию: {default}" if default else "↑↓ Enter"),
        )
        return sel if sel is not None else default
    hint = f" {C.D}[{default}]{C.R}" if default else ""
    try:
        ans = input(f"  {prompt}{hint}: ").strip()
    except (EOFError, KeyboardInterrupt):
        print()
        raise SystemExit(130)
    return ans or default


def cmd_wizard(args):
    """Визард как в боте: пошагово собираем запрос вместо голого промпта."""
    if not _need_key():
        return 1
    cl = Client(url=args.url)
    kind = args.what or ""
    if not kind:
        print(f"\n{C.MA}{C.B}Визард{C.R} — что делаем?\n")
        kind = _ask("выбери", "photo", [
            ("photo", "сгенерировать картинку по шагам"),
            ("edit", "переделать своё фото"),
            ("coder", "задача для ИИ-кодера"),
            ("style", "выбрать персону чата"),
        ])

    if kind in ("style", "стиль", "4"):
        return cmd_style(argparse.Namespace(name="", url=args.url))

    if kind in ("photo", "фото", "1"):
        print(f"\n{C.MA}{C.B}Визард фото{C.R} {C.D}(Enter — оставить значение){C.R}\n")
        subject = _ask("что рисуем (главный объект)")
        if not subject:
            return _err("без описания никак")
        style = _ask("стиль", "фотореализм", [
            ("фотореализм", "как фото, детально"),
            ("кино", "кинокадр, драматичный свет"),
            ("аниме", "аниме/манга"),
            ("масло", "живопись маслом"),
            ("3d", "3D-рендер"),
            ("киберпанк", "неон, дождь, будущее"),
        ])
        light = _ask("свет/время", "золотой час", [
            ("золотой час", "тёплый закатный свет"),
            ("ночь", "ночь, огни"),
            ("студия", "ровный студийный свет"),
            ("пасмурно", "мягкий рассеянный"),
        ])
        ratio = _ask("формат", "1:1", [
            ("1:1", "квадрат 1024×1024"),
            ("9:16", "вертикаль 768×1344 (сторис)"),
            ("16:9", "горизонт 1344×768 (обои)"),
            ("3:4", "портрет 896×1200"),
            ("4:3", "альбом 1200×896"),
        ])
        steps = _ask("качество", "25", [
            ("12", "быстро — черновик"),
            ("25", "обычное — баланс"),
            ("35", "лучше — дольше ждать"),
        ])
        extra = _ask("детали (необязательно)")
        prompt = f"{subject}, {style}, {light}"
        if extra:
            prompt += f", {extra}"
        _sz = _size_param(ratio) or 1024
        _wiz_params = {"size": _sz}
        print(f"\n{C.B}Промпт:{C.R} {C.CY}{prompt}{C.R}")
        print(f"{C.D}формат {ratio} → {_sz} · шаги {steps}{C.R}")
        if _ask("рисуем?", "go", [("go", "да, запускай"), ("no", "отмена")]) != "go":
            print("отменено")
            return 0
        params = {"size": _sz}
        try:
            params["steps"] = max(1, min(40, int(steps)))
        except ValueError:
            pass
        try:
            jid = cl.generate("txt2img", prompt, params=params)
        except ApiError as e:
            return _err(str(e))
        print(f"{C.D}job {jid}{C.R}")
        st, err = _wait(cl, jid, "рисую")
        if err:
            return _err(err)
        path = cl.save_result(jid, subject)
        print(f"{C.GR}✓ {path}{C.R}")
        _open(path)
        return _after_gen_menu(cl, path, prompt, params)

    if kind in ("edit", "правка", "2"):
        print(f"\n{C.MA}{C.B}Визард правки фото{C.R}\n")
        src = _ask("путь к файлу")
        if not src or not os.path.exists(src):
            return _err(f"нет файла: {src}")
        what = _ask("что изменить (напр. «сделай зиму», «добавь дождь»)")
        if not what:
            return _err("нужно описание правки")
        print(f"\n{C.B}Инструкция:{C.R} {C.CY}{what}{C.R}")
        if _ask("делаем?", "go", [("go", "да, переделывай"), ("no", "отмена")]) != "go":
            print("отменено")
            return 0
        with open(src, "rb") as fh:
            b64 = base64.b64encode(fh.read()).decode()
        try:
            jid = cl.generate("img2img", what, image_b64=b64)
        except ApiError as e:
            return _err(str(e))
        st, err = _wait(cl, jid, "переделываю")
        if err:
            return _err(err)
        path = cl.save_result(jid, what)
        print(f"{C.GR}✓ {path}{C.R}")
        _open(path)
        return 0

    if kind in ("coder", "код", "3"):
        print(f"\n{C.MA}{C.B}Визард кодера{C.R}\n")
        task = _ask("задача")
        if not task:
            return _err("нужна задача")
        lang = _ask("язык", "любой", [
            ("любой", "ИИ выберет сам"),
            ("python", "Python 3"),
            ("cpp", "C++17 (MSVC)"),
            ("javascript", "JS/TS через Deno"),
            ("powershell", "PowerShell"),
        ])
        gui = _ask("сделать ГУИ для скрипта?", "no", [
            ("no", "нет, консольный скрипт"),
            ("yes", "да, с окошком (tkinter)"),
        ])
        full = task
        if lang and lang != "любой":
            full += f". Используй язык: {lang}."
        if gui in ("yes", "y", "да"):
            full += (" Сделай для скрипта простой графический интерфейс (GUI) — "
                     "на Python это tkinter — и запусти проверку, что он собирается.")
        return _coder_once(cl, full)

    return _err(f"не знаю режим: {kind}")


# ──────────────────────────── help ────────────────────────────
HELP_TEXT = f"""
{C.MA}{C.B}comfuibot — console-side bot{C.R}  {C.D}бот в терминале: чат, фото, кодер{C.R}

{C.B}СТАРТ{C.R}
  {C.CY}comfuibot api auth{C.R}                вставить ключ (получить: бот → /apikey)
  {C.CY}comfuibot hud{C.R}                     панель: сервер, ключ, лимиты
  {C.CY}comfuibot wizard{C.R}                  пошаговый мастер (фото / правка / кодер)

{C.B}API{C.R}
  {C.CY}comfuibot api status{C.R}              жив ли сервер, polling, uptime
  {C.CY}comfuibot api auth [КЛЮЧ]{C.R}         вставить/сменить ключ
  {C.CY}comfuibot api usage{C.R}               тариф, срок, лимиты, расход

{C.B}ЧАТ{C.R}
  {C.CY}comfuibot chat{C.R}                    интерактивный чат ({C.D}/exit — выход{C.R})
  {C.CY}comfuibot chat "вопрос"{C.R}           одним сообщением
  {C.CY}comfuibot style{C.R}                   список персон
  {C.CY}comfuibot style tehpod{C.R}            выбрать персону ({C.D}style off — сброс{C.R})

{C.B}ФОТО{C.R}
  {C.CY}comfuibot photo gen "промпт"{C.R}      сгенерировать ({C.D}--steps 30 --size 9:16{C.R})
  {C.CY}comfuibot photo edit ф.png "что"{C.R}  переделать своё фото
  {C.CY}comfuibot wizard photo{C.R}            мастер: объект → стиль → свет → формат

{C.B}КОДЕР{C.R}
  {C.CY}comfuibot coder ai "задача"{C.R}       ИИ пишет и {C.B}ЗАПУСКАЕТ{C.R} код, показывает вывод
  {C.CY}comfuibot coder ai{C.R}                интерактивный режим
  {C.CY}comfuibot wizard coder{C.R}            мастер: задача → язык → нужен ли ГУИ

{C.B}ПРОЧЕЕ{C.R}
  {C.CY}comfuibot enter{C.R}                   открыть TG-side бота
  {C.CY}comfuibot help{C.R}                    эта справка
  {C.D}--url http://адрес:8090{C.R}            если API не на этой машине

{C.D}Картинки сохраняются в ~/comfuibot-out. Ключ — в ~/.comfuibot/config.json.
Гео-фильтр: API не работает из Африки, Китая, Ирана, Ирака, Сирии, КНДР,
Палестины, Мексики, Венесуэлы (403 REGION_BLOCKED).{C.R}
"""


def cmd_help(args):
    """Справка. В терминале — навигируемая по категориям (стрелки), а не
    простыня текста: выбрал раздел → выбрал действие → оно сразу запускается.
    В пайпе/скрипте печатаем обычный текст."""
    try:
        interactive = sys.stdin.isatty()
    except Exception:
        interactive = False
    if not interactive or getattr(args, "plain", False):
        print(HELP_TEXT)
        return 0
    return _help_wizard(args)


# Разделы справки: (ключ, заголовок, [(действие, подпись, пояснение)])
_HELP_SECTIONS = [
    ("photo", "🖼  Фото", [
        ("wiz_photo", "мастер генерации", "объект → стиль → свет → формат"),
        ("gen", "photo gen «промпт»", "быстрая генерация одной строкой"),
        ("wiz_edit", "переделать своё фото", "файл → что изменить"),
    ]),
    ("chat", "💬 Чат", [
        ("chat", "начать чат", "интерактивно, /exit — выход"),
        ("style", "выбрать персону", "9 персон: Газетович, Саппортович…"),
    ]),
    ("coder", "👨‍💻 Кодер", [
        ("wiz_coder", "мастер задачи", "задача → язык → нужен ли ГУИ"),
        ("coder", "coder ai «задача»", "ИИ пишет и ЗАПУСКАЕТ код"),
    ]),
    ("api", "🔑 Ключ и сервер", [
        ("hud", "HUD", "сервер, ключ, лимиты одним экраном"),
        ("usage", "расход по ключу", "тариф, дни, дневная квота"),
        ("status", "статус сервера", "жив ли, polling, uptime"),
        ("auth", "сменить ключ", "вставить новый API-ключ"),
    ]),
    ("misc", "📱 Прочее", [
        ("tg", "открыть TG-бота", "там /apikey и генерация в Telegram"),
        ("plain", "показать текстовую шпаргалку", "все команды списком"),
        ("geo", "география API", "откуда API не работает"),
    ]),
]


def _help_wizard(args):
    ns = lambda **kw: argparse.Namespace(url=args.url, **kw)  # noqa: E731
    while True:
        sec = _menu(
            "❓ Справка — выбери раздел",
            [(k, title) for k, title, _items in _HELP_SECTIONS],
            footer="↑↓ Enter · Esc выход",
        )
        if sec is None:
            return 0
        items = next(i for k, _t, i in _HELP_SECTIONS if k == sec)
        title = next(t for k, t, _i in _HELP_SECTIONS if k == sec)
        act = _menu(
            title,
            [(a, f"{label:<28} {C.D}{hint}{C.R}") for a, label, hint in items],
            footer="Enter — выполнить · Esc — к разделам",
        )
        if act is None:
            continue
        if act == "wiz_photo":
            cmd_wizard(ns(what="photo"))
        elif act == "wiz_edit":
            cmd_wizard(ns(what="edit"))
        elif act == "wiz_coder":
            cmd_wizard(ns(what="coder"))
        elif act == "gen":
            try:
                p = input("  промпт: ").strip()
            except (EOFError, KeyboardInterrupt):
                continue
            if p:
                cmd_photo_gen(ns(prompt=[p], steps=0, size="", no_open=False))
        elif act == "chat":
            cmd_chat(ns(text=[], style=""))
        elif act == "style":
            cmd_style(ns(name=""))
        elif act == "coder":
            try:
                t = input("  задача: ").strip()
            except (EOFError, KeyboardInterrupt):
                continue
            if t:
                cmd_coder_ai(ns(task=[t]))
        elif act == "hud":
            cmd_hud(ns())
        elif act == "usage":
            cmd_api_usage(ns())
        elif act == "status":
            cmd_api_status(ns())
        elif act == "auth":
            cmd_api_auth(ns(key="", no_menu=True))
        elif act == "tg":
            cmd_enter(ns(no_open=False))
        elif act == "plain":
            print(HELP_TEXT)
        elif act == "geo":
            print(f"\n  {C.B}🌍 География API{C.R}\n"
                  f"  {C.RD}не работает{C.R}: Африка (все страны), Китай, Иран, Ирак,\n"
                  f"                Сирия, КНДР, Палестина, Мексика, Венесуэла\n"
                  f"  {C.GR}работает{C.R}:    Россия, Европа, СНГ, США и остальной мир\n"
                  f"  {C.D}из заблокированного региона запрос вернёт 403 REGION_BLOCKED{C.R}\n")
        try:
            input(f"\n  {C.D}Enter — назад{C.R} ")
        except (EOFError, KeyboardInterrupt):
            return 0


# ──────────────────────────── главное меню ────────────────────────────
def cmd_menu(args):
    """Интерактивное меню — гуляешь стрелками, как в сервис-менеджерах.
    Запускается само, если вызвать `comfuibot` без аргументов."""
    ns = lambda **kw: argparse.Namespace(url=args.url, **kw)  # noqa: E731
    while True:
        # шапка со статусом — чтобы было видно, жив ли сервер и есть ли ключ
        cl = Client(url=args.url)
        try:
            h = cl.health()
            srv = f"{C.GR}online{C.R}" if h.get("status") == "ok" else f"{C.RD}offline{C.R}"
        except ApiError:
            srv = f"{C.RD}offline{C.R}"
        keyst = f"{C.GR}ключ есть{C.R}" if cl.key else f"{C.YE}нет ключа{C.R}"

        act = _menu(
            "comfuibot — console-side bot",
            [
                ("photo", "🖼  Сгенерировать фото"),
                ("edit", "✏️  Переделать своё фото"),
                ("chat", "💬 Чат с ИИ"),
                ("style", "🎭 Выбрать персону чата"),
                ("coder", "👨‍💻 Кодер: ИИ пишет и запускает код"),
                ("hud", "📊 HUD — сервер, ключ, лимиты"),
                ("usage", "📈 Расход по ключу"),
                ("auth", "🔑 Вставить/сменить ключ"),
                ("tg", "📱 Открыть TG-side бота"),
                ("help", "❓ Справка по командам"),
            ],
            footer=f"{srv} · {keyst} · ↑↓ Enter · Esc выход",
        )
        if act is None:
            print(f"{C.D}пока!{C.R}")
            return 0
        if act == "photo":
            cmd_wizard(ns(what="photo"))
        elif act == "edit":
            cmd_wizard(ns(what="edit"))
        elif act == "coder":
            cmd_wizard(ns(what="coder"))
        elif act == "chat":
            cmd_chat(ns(text=[], style=""))
        elif act == "style":
            cmd_style(ns(name=""))
        elif act == "hud":
            cmd_hud(ns())
        elif act == "usage":
            cmd_api_usage(ns())
        elif act == "auth":
            cmd_api_auth(ns(key=""))
        elif act == "tg":
            cmd_enter(ns(no_open=False))
        elif act == "help":
            print(HELP_TEXT)
        try:
            input(f"\n  {C.D}Enter — назад в меню{C.R} ")
        except (EOFError, KeyboardInterrupt):
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
    a.add_argument("--no-menu", action="store_true",
                   help="не открывать меню после сохранения (для скриптов)")
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

    # hud
    hd = sub.add_parser("hud", help="панель: сервер, ключ, лимиты, расход")
    hd.set_defaults(func=cmd_hud)

    # style
    stl = sub.add_parser("style", help="персона чата: список / выбрать / off")
    stl.add_argument("name", nargs="?", default="")
    stl.set_defaults(func=cmd_style)

    # wizard
    wz = sub.add_parser("wizard", help="пошаговый мастер: photo / edit / coder")
    wz.add_argument("what", nargs="?", default="")
    wz.set_defaults(func=cmd_wizard)

    # help
    hp = sub.add_parser("help", help="подробная справка")
    hp.set_defaults(func=cmd_help)

    # menu — интерактивное меню со стрелками
    mn = sub.add_parser("menu", help="интерактивное меню (стрелки ↑↓)")
    mn.set_defaults(func=cmd_menu)

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
        # Голая команда `comfuibot` → интерактивное меню со стрелками.
        if not getattr(args, "group", None):
            return cmd_menu(args)
        # Группа без действия (напр. `comfuibot api`) → помощь по группе.
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
