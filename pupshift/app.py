# -*- coding: utf-8 -*-
"""pupshift: трей-значок (GTK3 StatusIcon) + меню + диалог клавиши.

Движок (xswitch.Switcher) работает в отдельном потоке; все команды ему
отправляются через очередь (set_enabled/set_group/grab_async).
"""
import os
import threading

import cairo
import gi

gi.require_version("Gtk", "3.0")
from gi.repository import Gtk, Gdk, GLib

from . import config
from .keydialog import KeyDialog, format_hotkey
from .xswitch import Switcher

APP_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ENTRY = os.path.join(APP_DIR, "pupshift.py")
AUTOSTART_PATH = os.path.join(os.path.expanduser("~"), "Startup", "pupshift.sh")

AUTOSTART_TEMPLATE = """#!/bin/sh
# автозапуск pupshift при старте X (Puppy Linux)
[ -n "$DISPLAY" ] || DISPLAY=:0
python3 "%s" >/dev/null 2>&1 &
"""


def _round_rect(ctx, x, y, w, h, r):
    ctx.new_sub_path()
    ctx.arc(x + w - r, y + r, r, -1.5707963, 0)
    ctx.arc(x + w - r, y + h - r, r, 0, 1.5707963)
    ctx.arc(x + r, y + h - r, r, 1.5707963, 3.1415926)
    ctx.arc(x + r, y + r, r, 3.1415926, 4.7123889)
    ctx.close_path()


def _pixbuf_for(text, dim=False):
    """Значок 24x24: скруглённый квадрат с текстом (RU/EN/off)."""
    surf = cairo.ImageSurface(cairo.FORMAT_ARGB32, 24, 24)
    ctx = cairo.Context(surf)
    ctx.set_source_rgb(*(0.55, 0.55, 0.55) if dim else (0.16, 0.36, 0.72))
    _round_rect(ctx, 1, 1, 22, 22, 5)
    ctx.fill()
    ctx.set_source_rgb(1, 1, 1)
    ctx.select_font_face("Sans", cairo.FONT_SLANT_NORMAL, cairo.FONT_WEIGHT_BOLD)
    ctx.set_font_size(10)
    xb, yb, w, h, _dx, _dy = ctx.text_extents(text)
    ctx.move_to((24 - w) / 2.0 - xb, (24 - h) / 2.0 - yb)
    ctx.show_text(text)
    return Gdk.pixbuf_get_from_surface(surf, 0, 0, 24, 24)


class App:
    def __init__(self):
        self.cfg = config.load()
        self.sw = Switcher(self.cfg, on_error=self._engine_error)
        self.enabled = True
        self.dialog = None

        self.icon = Gtk.StatusIcon.new_from_pixbuf(_pixbuf_for("--", dim=True))
        self.icon.set_title("pupshift")
        self.icon.connect("activate", self._on_activate)
        self.icon.connect("popup-menu", self._on_popup)
        self.menu = self._build_menu()

        self.sw.refresh_layouts()
        threading.Thread(target=self.sw.run, daemon=True).start()
        GLib.timeout_add(300, self._poll_group)

    # --- меню ---
    def _build_menu(self):
        m = Gtk.Menu()
        mi_hotkey = Gtk.MenuItem(label="Клавиша переключения…")
        mi_hotkey.connect("activate", self._on_hotkey)
        m.append(mi_hotkey)

        self.mi_autostart = Gtk.CheckMenuItem(label="Автозапуск")
        self.mi_autostart.set_active(self._autostart_installed())
        self.mi_autostart.connect("toggled", self._on_autostart)
        m.append(self.mi_autostart)

        self.mi_enabled = Gtk.CheckMenuItem(label="Переключение активно")
        self.mi_enabled.set_active(True)
        self.mi_enabled.connect("toggled", self._on_enabled)
        m.append(self.mi_enabled)

        m.append(Gtk.SeparatorMenuItem())
        mi_quit = Gtk.MenuItem(label="Выход")
        mi_quit.connect("activate", self._on_quit)
        m.append(mi_quit)
        m.show_all()
        return m

    def _on_popup(self, _icon, button, activate_time):
        self.mi_autostart.set_active(self._autostart_installed())
        self.menu.popup(None, None, None, None, button, activate_time)

    # --- левый клик: сменить группу раскладки ---
    def _on_activate(self, _icon):
        g = self.sw.current_group()
        if g is not None:
            self.sw.set_group((g + 1) % self.sw.group_count())

    # --- значок: опрос текущей группы ---
    def _poll_group(self):
        if not self.enabled:
            self.icon.set_from_pixbuf(_pixbuf_for("off", dim=True))
            return True
        g = self.sw.current_group()
        if g is None:
            self.icon.set_from_pixbuf(_pixbuf_for("??", dim=True))
        else:
            self.icon.set_from_pixbuf(_pixbuf_for(self.sw.layout_name(g)))
        hk = self.cfg["hotkey"]
        self.icon.set_tooltip_text("pupshift: %s" % format_hotkey(hk["key"], hk["mods"]))
        return True

    # --- настройка клавиши ---
    def _on_hotkey(self, _mi):
        if self.dialog is not None:
            self.dialog.present()
            return
        hk = self.cfg["hotkey"]
        # отпускаем текущий захват, чтобы можно было проверить новый
        self.enabled = False
        self.sw.set_enabled(False)
        dlg = KeyDialog(None, hk["key"], hk["mods"], self._apply_hotkey)
        dlg.connect("destroy", self._on_dialog_destroy)
        self.dialog = dlg

    def _apply_hotkey(self, key, mods):
        ok, err = self.sw.grab_async(key, mods)
        if not ok:
            return False  # занято — диалог остаётся открытым
        self.cfg["hotkey"] = {"key": key, "mods": mods}
        config.save(self.cfg)
        return True

    def _on_dialog_destroy(self, _dlg):
        self.dialog = None
        self.enabled = True
        self.sw.set_enabled(True)

    # --- автозапуск (Puppy: ~/Startup, без systemd) ---
    def _autostart_installed(self):
        return os.path.exists(AUTOSTART_PATH)

    def _on_autostart(self, mi):
        want = mi.get_active()
        try:
            if want:
                os.makedirs(os.path.dirname(AUTOSTART_PATH), exist_ok=True)
                with open(AUTOSTART_PATH, "w") as f:
                    f.write(AUTOSTART_TEMPLATE % ENTRY)
                os.chmod(AUTOSTART_PATH, 0o755)
            elif os.path.exists(AUTOSTART_PATH):
                os.remove(AUTOSTART_PATH)
        except OSError as e:
            self._error_dialog("Автозапуск: %s" % e)

    # --- стоп/пуск ---
    def _on_enabled(self, mi):
        self.enabled = mi.get_active()
        self.sw.set_enabled(self.enabled)

    def _on_quit(self, _mi):
        self.sw.set_enabled(False)
        Gtk.main_quit()

    # --- ошибки ---
    def _engine_error(self, msg):
        GLib.idle_add(self._error_dialog, msg)

    def _error_dialog(self, msg):
        d = Gtk.MessageDialog(type=Gtk.MessageType.ERROR,
                              buttons=Gtk.ButtonsType.OK, text=msg)
        d.run()
        d.destroy()


def main():
    App()
    Gtk.main()
