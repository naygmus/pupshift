# -*- coding: utf-8 -*-
"""Диалог выбора горячей клавиши/сочетания для pupshift."""
import gi

gi.require_version("Gtk", "3.0")
from gi.repository import Gtk, Gdk

_MOD_LABELS = {"ctrl": "Ctrl", "alt": "Alt", "super": "Super", "shift": "Shift"}
_MOD_ORDER = ["ctrl", "alt", "super", "shift"]
_PURE_MODS = {
    "Shift_L", "Shift_R", "Control_L", "Control_R", "Alt_L", "Alt_R",
    "Meta_L", "Meta_R", "Super_L", "Super_R", "Hyper_L", "Hyper_R",
    "ISO_Level3_Shift", "ISO_Level5_Shift", "Num_Lock", "Caps_Lock",
}


def format_hotkey(key, mods):
    parts = [_MOD_LABELS[m] for m in _MOD_ORDER if m in mods]
    parts.append(key)
    return " + ".join(parts)


class KeyDialog(Gtk.Dialog):
    """Показывает нажатое сочетание; on_apply(key, mods) -> bool.

    on_apply пытается захватить клавишу; False означает «занято» —
    диалог остаётся открытым с сообщением об ошибке.
    """

    def __init__(self, parent, current_key, current_mods, on_apply):
        super().__init__(
            title="pupshift — клавиша переключения",
            transient_for=parent, modal=True)
        self.set_default_size(420, 180)
        self.on_apply = on_apply
        self.key = current_key
        self.mods = list(current_mods)

        area = self.get_content_area()
        area.set_spacing(8)
        area.set_border_width(10)

        area.add(Gtk.Label(label=(
            "Нажмите клавишу или сочетание для переключения раскладки.\n"
            "Занятые системой сочетания будут отклонены при применении.\n"
            "Esc — отмена.")))
        self.capture = Gtk.Label(label="")
        self._update_label()
        area.add(self.capture)
        self.error = Gtk.Label(label="")
        area.add(self.error)

        self.add_buttons("Отмена", Gtk.ResponseType.CANCEL,
                         "Применить", Gtk.ResponseType.OK)
        self.connect("key-press-event", self._on_key)
        self.connect("response", self._on_response)
        self.show_all()

    def _update_label(self):
        self.capture.set_markup("<b>%s</b>" % format_hotkey(self.key, self.mods))

    def _on_key(self, _w, ev):
        name = Gdk.keyval_name(ev.keyval)
        if name == "Escape":
            self.response(Gtk.ResponseType.CANCEL)
            return True
        if not name or name in _PURE_MODS:
            return True  # чистый модификатор — ждём основную клавишу
        state = ev.state
        mods = []
        if state & Gdk.ModifierType.CONTROL_MASK:
            mods.append("ctrl")
        if state & Gdk.ModifierType.MOD1_MASK:
            mods.append("alt")
        if state & Gdk.ModifierType.MOD4_MASK:
            mods.append("super")
        if state & Gdk.ModifierType.SHIFT_MASK:
            mods.append("shift")
        self.key, self.mods = name, mods
        self.error.set_text("")
        self._update_label()
        return True

    def _on_response(self, _w, resp):
        if resp == Gtk.ResponseType.OK:
            if self.on_apply(self.key, self.mods):
                self.destroy()
                return
            self.error.set_text(
                "Это сочетание занято системой или другой программой — выберите другое.")
        else:
            self.destroy()
