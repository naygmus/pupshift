# -*- coding: utf-8 -*-
"""X11-движок pupshift: захват горячей клавиши, чтение выделения (PRIMARY),
замена текста через XTest, переключение группы раскладок через XKB.

Потоковая модель: run() крутится в отдельном потоке и владеет X-соединением;
GTK-поток общается с ним через очередь команд (set_enabled/grab_async/set_group).
"""
import queue
import select
import time

from Xlib import X, XK, display
from Xlib.error import BadAccess, XError
from Xlib.ext import xtest

from .transform import has_cyrillic, to_latin, transform


def _dbg(*a):
    import os
    if os.environ.get("PUPSHIFT_DEBUG"):
        print("[pupshift]", *a, flush=True)


# Символы в US-раскладке, которые в RU дают буквы. Ctrl+Shift+Left
# не включает их в «слово» (это не буквы), поэтому нужно расширять
# выделение вручную, чтобы захватить, например, ведущую точку в `.kf`.
_EXTEND_SYMBOLS = frozenset(".,;'[]`")

# Стандартные PC104 keycodes для US-раскладки. Hardcode надёжнее, чем
# динамический поиск через keycode_to_keysym: на нестандартных сборках
# XKB тот может вернуть keycode «чужой» группы (`.` → 61 вместо 60).
# Кириллица печатается теми же keycode: `ю` = to_latin('.') = 60,
# в RU-группе keycode 60 = `ю`.
_US_KEYCODES = {
    "`": 49, "1": 10, "2": 11, "3": 12, "4": 13, "5": 14,
    "6": 15, "7": 16, "8": 17, "9": 18, "0": 19, "-": 20, "=": 21,
    "q": 24, "w": 25, "e": 26, "r": 27, "t": 28, "y": 29, "u": 30,
    "i": 31, "o": 32, "p": 33, "[": 34, "]": 35, "\\": 51,
    "a": 38, "s": 39, "d": 40, "f": 41, "g": 42, "h": 43, "j": 44,
    "k": 45, "l": 46, ";": 47, "'": 48,
    "z": 52, "x": 53, "c": 54, "v": 55, "b": 56, "n": 57, "m": 58,
    ",": 59, ".": 60, "/": 61,
}
_US_SHIFTED = {
    "~": 49, "!": 10, "@": 11, "#": 12, "$": 13, "%": 14,
    "^": 15, "&": 16, "*": 17, "(": 18, ")": 19, "_": 20, "+": 21,
    "Q": 24, "W": 25, "E": 26, "R": 27, "T": 28, "Y": 29, "U": 30,
    "I": 31, "O": 32, "P": 33, "{": 34, "}": 35, "|": 51,
    "A": 38, "S": 39, "D": 40, "F": 41, "G": 42, "H": 43, "J": 44,
    "K": 45, "L": 46, ":": 47, '"': 48,
    "Z": 52, "X": 53, "C": 54, "V": 55, "B": 56, "N": 57, "M": 58,
    "<": 59, ">": 60, "?": 61,
}



MOD_MASKS = {
    "shift": X.ShiftMask, "ctrl": X.ControlMask,
    "alt": X.Mod1Mask, "super": X.Mod4Mask,
}


def _lock_combinations():
    """Все комбинации Lock (CapsLock), Mod2 (NumLock), Mod5 (ScrollLock)."""
    combs = [0]
    for m in (X.LockMask, X.Mod2Mask, X.Mod5Mask):
        combs += [c | m for c in combs]
    return combs


LOCK_COMBS = _lock_combinations()
_INTERESTING = X.ShiftMask | X.ControlMask | X.Mod1Mask | X.Mod4Mask


class HotkeyBusy(Exception):
    pass


class CatchError:
    """Ловит первое X-ошибку заданного класса; остальные пробрасывает."""

    def __init__(self, exc_type):
        self.exc_type = exc_type
        self._error = None

    def __call__(self, error, request):
        if isinstance(error, self.exc_type):
            if self._error is None:
                self._error = error
            return None
        raise error

    def get_error(self):
        return self._error


class Switcher:
    def __init__(self, cfg, on_error=None):
        self.cfg = cfg
        self.on_error = on_error
        self.dpy = display.Display()
        self.root = self.dpy.screen().root
        self.sel_win = self.root.create_window(
            -100, -100, 1, 1, 0, depth=X.CopyFromParent,
            window_class=X.InputOutput, visual=X.CopyFromParent, event_mask=0)
        self._grabbed = []
        self._busy = False
        self._cmds = queue.Queue()
        self._layouts = []
        self._xkb = None
        # PRIMARY, который мы видели в прошлый раз. Нужен для того, чтобы
        # fallback в _active_selection не принимал один и тот же PRIMARY за
        # свежее выделение (иначе в Chrome будут бесконечные повторы).
        self._last_primary_seen = None



    def _find_keycode_us(self, us_char):
        """(keycode, shift) для us_char по US-группе.

        Перебирает все keycodes и проверяет уровень 0/1 в US-группе.
        Это надёжнее keysym_to_keycode: для символов, которые есть в
        обеих раскладках (`. `, `/`, `,`), тот может вернуть keycode
        «не той» группы (например, 61 для `.`: в US это `/`, в RU — `.`).
        """
        if not us_char or len(us_char) != 1:
            return None
        ks = ord(us_char)
        if ks < 32 or ks > 0xFFFF:
            return None
        us_group = 0
        if self._layouts and "us" in self._layouts:
            us_group = self._layouts.index("us")
        base = us_group * 2
        for kc in range(8, 256):
            try:
                if self.dpy.keycode_to_keysym(kc, base) == ks:
                    return kc, False
                if self.dpy.keycode_to_keysym(kc, base + 1) == ks:
                    return kc, True
            except Exception:
                continue
        return None

    def _group_for_char(self, ch, preferred=0):
        """В какой XKB-группе печатать ch.

        Правила:
        - Кириллическая буква → RU-группа (на своей физической клавише
          она всегда там).
        - US-буква (a-z, A-Z) → US-группа.
        - Цифры, пунктуация, пробелы → preferred (контекст). Например,
          `.` в конце `привет.` — это русская точка (RU, keycode 61),
          а `.` в `.kf` — US-точка (keycode 60). Различить по самому
          U+002E нельзя, только по контексту всего текста.
        """
        if not ch:
            return preferred
        if has_cyrillic(ch):
            if self._layouts and "ru" in self._layouts:
                return self._layouts.index("ru")
            return preferred
        if ch.isalpha() and ch.isascii():
            if self._layouts and "us" in self._layouts:
                return self._layouts.index("us")
            return 0
        return preferred


    def _xkb_conn(self):
        if self._xkb is None:
            from .xkb import Xkb
            self._xkb = Xkb()
        return self._xkb

    def _force_toggle(self):
        """Toggle Alt+Shift с длинной паузой.

        У _combo пауза между keydown/keyup Shift — 0.02 с, этого не
        хватает XKB на XTEST slave, чтобы распознать grp:alt_shift_toggle.
        В результате toggle применяется с задержкой, и первый символ
        после toggle печатается ещё в старой раскладке (`.kf` → `.ла`
        вместо `юла`). Здесь держим Alt и Shift нажатыми дольше и
        ждём после отпускания, пока группа применится.
        """
        before = self.current_group()
        alt_kc = self._kc("Alt_L")
        shift_kc = self._kc("Shift_L")
        xtest.fake_input(self.dpy, X.KeyPress, alt_kc)
        self.dpy.sync()
        time.sleep(0.04)
        xtest.fake_input(self.dpy, X.KeyPress, shift_kc)
        self.dpy.sync()
        time.sleep(0.06)
        xtest.fake_input(self.dpy, X.KeyRelease, shift_kc)
        self.dpy.sync()
        time.sleep(0.04)
        xtest.fake_input(self.dpy, X.KeyRelease, alt_kc)
        self.dpy.sync()
        time.sleep(0.20)
        after = self.current_group()
        _dbg("force_toggle: before=%r after=%r" % (before, after))


    def _switch_group_to(self, target):
        """Переключить XKB-группу в target через XTest Alt+Shift.

        Прямой XkbLockGroup ненадёжен для XInput2-приложений (Chrome):
        они читают XKB-состояние с per-slave-device, и XkbLockGroup
        (XkbUseCoreKbd) не всегда виден Chrome. Симуляция пользовательского
        шортката grp:alt_shift_toggle проходит через нормальный event-путь
        и обновляет group на всех slave-устройствах.
        """
        if target is None:
            return
        current = self.current_group()
        if current is None:
            _dbg("switch_group_to: current_group()=None, skip")
            return
        count = self.group_count()
        if count <= 1:
            return
        tries = 0
        while current != target and tries < count + 1:
            self._combo([X.Mod1Mask], self._kc("Shift_L"))
            time.sleep(0.15)
            current = self.current_group()
            tries += 1
            _dbg("switch_group_to: try %d current=%r target=%r"
                 % (tries, current, target))
        if current != target:
            _dbg("switch_group_to: FAILED after %d tries" % tries)

    def refresh_layouts(self):
        from .xkb import get_layouts
        self._layouts = get_layouts()

    def layout_name(self, group):
        try:
            return self._layouts[group].upper()
        except (IndexError, TypeError):
            return "??"

    def group_count(self):
        return max(1, len(self._layouts))

    def current_group(self):
        try:
            return self._xkb_conn().get_group()
        except Exception:
            return None

    def set_group(self, group):
        self._cmds.put(("set_group", group))

    # ---------- горячая клавиша ----------
    def grab(self, key, mods):
        self.ungrab()
        kc = self._kc(key)
        if not kc:
            raise ValueError("неизвестная клавиша: %s" % key)
        mask = 0
        for m in mods or []:
            mask |= MOD_MASKS.get(m, 0)
        err = CatchError(BadAccess)
        for lock in LOCK_COMBS:
            self.root.grab_key(kc, mask | lock, False,
                               X.GrabModeAsync, X.GrabModeAsync, onerror=err)
        self.dpy.sync()
        if err.get_error() is not None:
            self.ungrab()
            raise HotkeyBusy("сочетание занято")
        self._grabbed = [(kc, mask | lock) for lock in LOCK_COMBS]

    def ungrab(self):
        for kc, m in self._grabbed:
            try:
                self.root.ungrab_key(kc, m)
            except XError:
                pass
        self._grabbed = []
        try:
            self.dpy.sync()
        except XError:
            pass

    def grab_async(self, key, mods):
        reply = queue.Queue()
        self._cmds.put(("grab", key, mods, reply))
        return reply.get(timeout=5)

    def set_enabled(self, on):
        self._cmds.put(("enabled", on))

    def _sync_groups(self):
        """Синхронизировать slave XTest и master двумя Alt+Shift toggle.

        В прошлых версиях XkbLockGroup менял только master, оставляя
        slave в другой группе. Два toggle переключают slave туда-обратно
        (группа та же), master следует за активным slave.
        """
        count = self.group_count()
        if count <= 1:
            return
        for _ in range(2):
            self._combo([X.Mod1Mask], self._kc("Shift_L"))
            time.sleep(0.15)
        _dbg("sync_groups: done, current=%r" % self.current_group())

    def _dump_keymap(self):
        """Диагностика keycodes для 59/60/61 в US/RU (только при PUPSHIFT_DEBUG)."""
        import os
        if not os.environ.get("PUPSHIFT_DEBUG"):
            return
        try:
            d = self.dpy
            _dbg("km check: kc59 US/RU=0x%x/0x%x kc60 US/RU=0x%x/0x%x "
                 "kc61 US/RU=0x%x/0x%x"
                 % (d.keycode_to_keysym(59, 0),
                    d.keycode_to_keysym(59, 2),
                    d.keycode_to_keysym(60, 0),
                    d.keycode_to_keysym(60, 2),
                    d.keycode_to_keysym(61, 0),
                    d.keycode_to_keysym(61, 2)))
        except Exception as e:
            _dbg("km check failed: %s" % e)

    # ---------- цикл событий ----------
    def run(self):
        hk = self.cfg["hotkey"]
        try:
            self.grab(hk["key"], hk["mods"])
        except (HotkeyBusy, ValueError) as e:
            if self.on_error:
                self.on_error("Клавиша %s: %s" % (hk["key"], e))
        self._dump_keymap()
        while True:
            try:
                self._drain_cmds()
                for ev in self._pump(0.15):
                    self._dispatch(ev)
            except XError as e:
                if self.on_error:
                    self.on_error("X11: %s" % e)
                time.sleep(1)

    def _drain_cmds(self):
        while True:
            try:
                cmd = self._cmds.get_nowait()
            except queue.Empty:
                return
            kind = cmd[0]
            if kind == "enabled":
                if cmd[1]:
                    hk = self.cfg["hotkey"]
                    try:
                        self.grab(hk["key"], hk["mods"])
                    except (HotkeyBusy, ValueError) as e:
                        if self.on_error:
                            self.on_error(str(e))
                else:
                    self.ungrab()
            elif kind == "set_group":
                try:
                    self._xkb_conn().lock_group(cmd[1])
                except Exception:
                    pass
            elif kind == "grab":
                _, key, mods, reply = cmd
                try:
                    self.grab(key, mods)
                    ok, err = True, None
                except (HotkeyBusy, ValueError) as e:
                    ok, err = False, str(e)
                reply.put((ok, err))

    def _pump(self, timeout):
        """Неблокирующее чтение событий; pop_event() тут не используется —
        его нет в старых версиях python-xlib, а pending_events() + next_event()
        есть всегда."""
        events = []
        n = self.dpy.pending_events()
        while n > 0:
            events.append(self.dpy.next_event())
            n -= 1
        if events:
            return events
        r, _, _ = select.select([self.dpy.fileno()], [], [], timeout)
        if r:
            events.append(self.dpy.next_event())
            n = self.dpy.pending_events()
            while n > 0:
                events.append(self.dpy.next_event())
                n -= 1
        return events

    def _dispatch(self, ev):
        if ev.type != X.KeyPress or not self._grabbed:
            return
        kc, mask = self._grabbed[0]
        if ev.detail != kc:
            return
        if (ev.state & _INTERESTING) != mask:
            return
        self.do_switch()

    # ---------- переключение ----------
    def do_switch(self):
        if self._busy:
            return
        self._busy = True
        try:
            text = self._active_selection()
            _dbg("do_switch: text=%r last_seen=%r"
                 % (text, self._last_primary_seen))
            if text:
                self._replace_text(text)
            else:
                self._switch_last_word()
            # Обновляем «последний PRIMARY» уже после действий — чтобы
            # fallback в следующем нажатии не принял этот же текст за новое
            # выделение.
            self._last_primary_seen = self.read_selection("PRIMARY", 0.2)
        except XError as e:
            if self.on_error:
                self.on_error("X11: %s" % e)
        finally:
            self._busy = False

    def _looks_like_user_text(self, text):
        """True если текст — пользовательский ввод, а не shell-команда/путь.

        Пользователь может набирать любой текст, в т.ч. с `/` (это
        русская `.` на US-клавиатуре), `,`, `;`, `[` и т.д. Отсеиваем
        только явные маркеры shell-команд, путей, URL и email.
        """
        if not text or len(text) > 128:
            return False
        if "\n" in text or "\r" in text:
            return False
        lower = text.lower()
        # Явные shell-маркеры
        for marker in ("/usr/", "/bin/", "/root/", "/home/", "/etc/",
                       "/opt/", "/var/", "/tmp/", "/dev/", "/proc/",
                       "/sys/", "/lib/", "/sbin/", "/boot/",
                       "sudo ", "apt-get ", "apt ", "pip ",
                       "python3 ", "python ", "--", "&&", "||",
                       "$(", ".sh ", ".py ", ".so ", ".conf ",
                       ".json ", ".yaml ", ".log "):
            if marker in lower:
                return False
        # Слова, начинающиеся с /, ~/, ./ — это пути; email/URL.
        for word in text.split():
            if word.startswith(("/", "~/", "./", "..")):
                return False
            if word.startswith(("http://", "https://", "ftp://")):
                return False
            if "@" in word and "." in word:
                return False
        return True


    def _active_selection(self):
        """Текст активного выделения или None.

        1. owner PRIMARY в одном контексте с фокусом — точно свежее.
        2. Иначе fallback по содержимому, но только если текст изменился
           с прошлого нажатия. Это критично для Chromium: его clipboard-
           процесс постоянно владеет PRIMARY, поэтому «owner есть» не
           значит «есть выделение». Без проверки на изменение fallback
           бесконечно принимал бы один и тот же старый PRIMARY, и слово
           размножалось бы: 'ghbdtn' → 'привет' → 'ghbdtn' → ...
        """
        owner = self._sel_owner()
        if not owner:
            return None
        text = self.read_selection("PRIMARY", 0.4)
        if not text or not text.strip():
            return None
        focused = self._focused()
        if self._same_context(owner, focused):
            _dbg("active_selection: ctx ok owner=%r focused=%r text=%r"
                 % (owner, focused, text))
            return text
        if text == self._last_primary_seen:
            _dbg("active_selection: skip (same as last PRIMARY)")
            return None
        if self._looks_like_user_text(text):
            _dbg("active_selection: fallback (ctx mismatch, changed) text=%r"
                 % text)
            return text
        _dbg("active_selection: skip (ctx mismatch, looks like command) "
             "text=%r" % text)
        return None

    def _extend_selection_left(self, base, max_iters=6):
        """Расширить выделение влево до пробела/начала строки.

        Ctrl+Shift+Left в US выделяет `[` отдельно от `yb` (это пунктуация),
        а `.` отдельно от `kf`. Расширяемся по одному символу влево,
        пока в префиксе нет пробелов.
        """
        for _ in range(max_iters):
            self._combo([X.ShiftMask], self._kc("Left"))
            time.sleep(0.08)
            text = self.read_selection("PRIMARY", 0.3)
            if text is None or text == base:
                return base
            if not text.endswith(base) or len(text) <= len(base):
                self._combo([X.ShiftMask], self._kc("Right"))
                return base
            prefix = text[:len(text) - len(base)]
            if not any(ch.isspace() for ch in prefix):
                base = text
                continue
            self._combo([X.ShiftMask], self._kc("Right"))
            return base
        return base


    def _switch_last_word(self):
        """Выделить Ctrl+Shift+Left и заменить раскладку последнего слова."""
        pre_owner = self._sel_owner()
        pre_text = self.read_selection("PRIMARY", 0.5) if pre_owner else None
        _dbg("last_word: pre_owner=%r pre_text=%r" % (pre_owner, pre_text))

        self._combo([X.ControlMask, X.ShiftMask], self._kc("Left"))
        time.sleep(0.35)

        text = self.read_selection("PRIMARY", 0.5)
        _dbg("last_word: post_text=%r (attempt 1)" % text)

        # Приложение могло не успеть обновить PRIMARY — перечитываем без
        # повторного combo (иначе выделение собьётся).
        if text == pre_text or not text:
            time.sleep(0.30)
            text = self.read_selection("PRIMARY", 0.6)
            _dbg("last_word: post_text=%r (attempt 2)" % text)

        if not text or not text.strip():
            return
        if pre_text == text:
            _dbg("last_word: skip (stale PRIMARY unchanged after retry)")
            return
        if len(text) > 200 or "\n" in text:
            _dbg("last_word: skip (text too long / multi-line)")
            return

        # Расширить выделение на ведущие «символьные» буквы (. , ; ' [ ] `),
        # которые Ctrl+Shift+Left не включает (например `.kf` → выделено
        # только `kf`, а надо `.kf` → `юла`).
        extended = self._extend_selection_left(text)
        if extended != text:
            _dbg("last_word: extended %r -> %r" % (text, extended))
            text = extended

        self._replace_text(text)

    def _replace_text(self, text):
        lead = text[:len(text) - len(text.lstrip())]
        trail = text[len(text.rstrip()):]
        body = text.strip()
        if not body:
            return
        new = transform(body)
        self._press_key(self._kc("Delete"))
        self.type_text(lead + new + trail)

    # ---------- печать текста ----------
    def type_text(self, text):
        """Напечатать текст, переключая XKB-группу по необходимости.

        После печати оставляем группу в соответствии с последним
        напечатанным символом — чтобы пользователь продолжал печатать
        в той же раскладке (после `ghbdtn → привет` активна RU).
        """
        if not text:
            return
        try:
            xkb = self._xkb_conn()
        except Exception:
            xkb = None
        orig = xkb.get_group() if xkb else 0

        preferred = 0
        if xkb and self._layouts:
            name = "ru" if has_cyrillic(text) else "us"
            if name in self._layouts:
                preferred = self._layouts.index(name)

        current = orig

        def _switch_to(g):
            nonlocal current
            if g == current:
                return
            _dbg("type_text: toggle model %r -> %r" % (current, g))
            self._force_toggle()
            current = g

        _dbg("type_text: text=%r orig=%r preferred=%r"
             % (text, orig, preferred))
        try:
            for ch in text:
                g = self._group_for_char(ch, preferred)
                _switch_to(g)
                self._type_char(ch, g)
        finally:
            # Оставить группу по последнему символу — удобно для
            # пользователя (продолжает печатать в той же раскладке).
            if text:
                final = self._group_for_char(text[-1], preferred)
                if current != final:
                    _dbg("type_text: leave at final %r" % final)
                    self._force_toggle()
                    current = final


    def _type_char(self, ch, group=0):
        """Напечатать один символ ch в XKB-группе group.

        Для US-символов (group=0) — печатаем как есть: `.` → kc=60,
        `,` → kc=59, `;` → kc=47. Нельзя делать to_latin: он RU→US,
        и для ASCII `.` вернёт `/` (это маппинг кириллической точки).

        Для кириллицы (group=1) — делаем to_latin, чтобы получить
        US-символ на той же физической клавише. `ю` → `.` → kc=60,
        в RU-группе kc=60 = `ю`. Кириллическая `.` (на keycode 61)
        → to_latin('.')='/' → kc=61, в RU-группе kc=61 = `.`.
        """
        if not ch or len(ch) != 1:
            return
        if group == 0:
            latin = ch
        else:
            latin = to_latin(ch)
        if not latin or len(latin) != 1:
            latin = ch

        kc = _US_KEYCODES.get(latin)
        shift = False
        if kc is None:
            kc = _US_SHIFTED.get(latin)
            shift = True
        if kc is None:
            found = self._find_keycode_us(latin)
            if found is None:
                _dbg("type_char: no keycode for %r (latin=%r) group=%d"
                     % (ch, latin, group))
                return
            kc, shift = found
        _dbg("type_char: %r latin=%r kc=%r shift=%r group=%d"
             % (ch, latin, kc, shift, group))
        if shift:
            xtest.fake_input(self.dpy, X.KeyPress, self._kc("Shift_L"))
        xtest.fake_input(self.dpy, X.KeyPress, kc)
        self.dpy.sync()
        time.sleep(0.004)
        xtest.fake_input(self.dpy, X.KeyRelease, kc)
        if shift:
            xtest.fake_input(self.dpy, X.KeyRelease, self._kc("Shift_L"))
        self.dpy.sync()


    def _shift_level(self, kc, keysym_code):
        for lvl in range(6):
            try:
                ks = self.dpy.keycode_to_keysym(kc, lvl)
            except Exception:
                break
            if ks == keysym_code:
                return lvl in (1, 3)
        return False

    # ---------- чтение выделения ----------
    def read_selection(self, selname="PRIMARY", timeout=0.4):
        sel = self.dpy.intern_atom(selname)
        prop = self.dpy.intern_atom("PUPSHIFT_DATA")
        for tname in ("UTF8_STRING", "STRING", "COMPOUND_TEXT"):
            target = self.dpy.intern_atom(tname)
            try:
                self.sel_win.convert_selection(sel, target, prop, X.CurrentTime)
            except XError:
                return None
            self.dpy.flush()
            data = self._wait_notify(prop, target, timeout)
            if data:
                return data
        return None

    def _wait_notify(self, prop, target, timeout):
        deadline = time.time() + timeout
        while time.time() < deadline:
            for ev in self._pump(0.05):
                if (ev.type == X.SelectionNotify
                        and getattr(ev.requestor, "id", ev.requestor) == self.sel_win.id):
                    if not ev.property:
                        return None
                    return self._read_prop(prop, target)
        return None

    def _read_prop(self, prop, target):
        r = self.sel_win.get_property(prop, target, 0, 1 << 20)
        try:
            self.sel_win.delete_property(prop)
        except XError:
            pass
        if r is None:
            return None
        val = r.value
        if isinstance(val, list):
            val = bytes(bytearray(val))
        if isinstance(val, str):
            return val
        try:
            return val.decode("utf-8")
        except UnicodeDecodeError:
            return val.decode("latin-1")

    # ---------- окна и владельцы ----------
    def _sel_owner(self, selname="PRIMARY"):
        try:
            w = self.dpy.get_selection_owner(self.dpy.intern_atom(selname))
        except XError:
            return None
        if w is None:
            return None
        wid = getattr(w, "id", w)
        if not wid:
            return None
        return wid

    def _focused(self):
        try:
            f = self.dpy.get_input_focus().focus
        except XError:
            return None
        return getattr(f, "id", f)

    def _parent_of(self, wid):
        """id родителя окна wid (или None)."""
        try:
            w = self.dpy.create_resource_object("window", wid)
            parent = w.query_tree().parent
        except XError:
            return None
        if parent is None:
            return None
        return getattr(parent, "id", parent)

    def _chain(self, wid):
        """[wid, parent, ..., root] с ограничением глубины."""
        chain = []
        w = wid
        for _ in range(32):
            if not w:
                break
            chain.append(w)
            if w == self.root.id:
                break
            w = self._parent_of(w)
        return chain

    def _related(self, wid1, wid2):
        """True, если окна связаны (общий предок, кроме корня)."""
        if not wid1 or not wid2:
            return False
        if wid1 == wid2:
            return True
        c1 = set(self._chain(wid1)) - {self.root.id}
        c2 = set(self._chain(wid2)) - {self.root.id}
        return bool(c1 & c2)

    def _wm_class(self, wid):
        """WM_CLASS окна или ближайшего предка (обычно только у top-level)."""
        for w_id in self._chain(wid):
            try:
                w = self.dpy.create_resource_object("window", w_id)
                cls = w.get_wm_class()
                if cls:
                    return cls
            except XError:
                continue
        return None

    def _same_context(self, wid1, wid2):
        """True, если окна в одном контексте (дерево или WM_CLASS).

        PID-проверки здесь нет намеренно: она принимала «служебные» окна
        приложения (например, у Geany) как свои собственные, и тогда
        fallback в _active_selection принимал мусорный PRIMARY от прошлого
        приложения (Chromium) за свежее выделение Geany.
        """
        if not wid1 or not wid2:
            return False
        if wid1 == wid2:
            return True
        related = self._related(wid1, wid2)
        c1 = self._wm_class(wid1)
        c2 = self._wm_class(wid2)
        match = c1 is not None and c2 is not None and c1 == c2
        _dbg("same_context: %r vs %r related=%r wm1=%r wm2=%r match=%r"
             % (wid1, wid2, related, c1, c2, match))
        return related or match

    def _top(self, wid):
        if not wid:
            return None
        try:
            w = self.dpy.create_resource_object("window", wid)
        except XError:
            return None
        for _ in range(20):
            try:
                tree = w.query_tree()
            except XError:
                return None
            parent = tree.parent
            if parent is None or getattr(parent, "id", None) == self.root.id:
                return w.id
            w = parent
        return getattr(w, "id", None)

    # ---------- низкоуровневые нажатия ----------
    def _kc(self, name):
        return self.dpy.keysym_to_keycode(XK.string_to_keysym(name))

    def _mod_kc(self, mask):
        name = {X.ControlMask: "Control_L", X.ShiftMask: "Shift_L",
                X.Mod1Mask: "Alt_L", X.Mod4Mask: "Super_L"}.get(mask)
        return self._kc(name) if name else 0

    def _combo(self, mod_masks, kc):
        for m in mod_masks:
            xtest.fake_input(self.dpy, X.KeyPress, self._mod_kc(m))
        xtest.fake_input(self.dpy, X.KeyPress, kc)
        self.dpy.sync()
        time.sleep(0.02)
        xtest.fake_input(self.dpy, X.KeyRelease, kc)
        for m in reversed(mod_masks):
            xtest.fake_input(self.dpy, X.KeyRelease, self._mod_kc(m))
        self.dpy.sync()

    def _press_key(self, kc):
        xtest.fake_input(self.dpy, X.KeyPress, kc)
        self.dpy.sync()
        time.sleep(0.01)
        xtest.fake_input(self.dpy, X.KeyRelease, kc)
        self.dpy.sync()
