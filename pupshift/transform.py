# -*- coding: utf-8 -*-
"""Трансформация текста между раскладками ЙЦУКЕН (ru) и QWERTY (us).

Чистая логика без зависимостей от X11 — тестируется на хосте через pytest.
Направление определяется автоматически: есть кириллица -> латиница, иначе -> кириллица.
"""

# Физическая клавиша (символ US-раскладки без Shift) -> буква ЙЦУКЕН.
_US_TO_RU = {
    "`": "ё", "q": "й", "w": "ц", "e": "у", "r": "к", "t": "е", "y": "н",
    "u": "г", "i": "ш", "o": "щ", "p": "з", "[": "х", "]": "ъ",
    "a": "ф", "s": "ы", "d": "в", "f": "а", "g": "п", "h": "р", "j": "о",
    "k": "л", "l": "д", ";": "ж", "'": "э",
    "z": "я", "x": "ч", "c": "с", "v": "м", "b": "и", "n": "т", "m": "ь",
    ",": "б", ".": "ю", "/": ".",
}

# Символ US-раскладки с Shift -> символ ЙЦУКЕН с Shift.
_US_SHIFT_TO_RU = {
    "~": "Ё", "!": "!", "@": '"', "#": "№", "$": ";", "%": "%", "^": ":",
    "&": "?", "*": "*", "(": "(", ")": ")", "_": "_", "+": "+",
    "{": "Х", "}": "Ъ", ":": "Ж", '"': "Э", "<": "Б", ">": "Ю", "?": ",",
}

_RU_TO_US = {v: k for k, v in _US_TO_RU.items()}
_RU_SHIFT_TO_US = {v: k for k, v in _US_SHIFT_TO_RU.items()}


def has_cyrillic(text):
    return any("\u0400" <= ch <= "\u04FF" for ch in text)


def to_latin(text):
    """ЙЦУКЕН -> QWERTY (то, что было бы набрано в US-раскладке)."""
    out = []
    for ch in text:
        lower = ch.lower()
        if lower in _RU_TO_US:
            us = _RU_TO_US[lower]
            out.append(us.upper() if ch.isupper() else us)
        elif ch in _RU_SHIFT_TO_US:
            out.append(_RU_SHIFT_TO_US[ch])
        else:
            out.append(ch)
    return "".join(out)


def to_cyrillic(text):
    """QWERTY -> ЙЦУКЕН (то, что было бы набрано в RU-раскладке)."""
    out = []
    for ch in text:
        lower = ch.lower()
        if lower in _US_TO_RU:
            ru = _US_TO_RU[lower]
            out.append(ru.upper() if ch.isupper() else ru)
        elif ch in _US_SHIFT_TO_RU:
            out.append(_US_SHIFT_TO_RU[ch])
        else:
            out.append(ch)
    return "".join(out)


def transform(text):
    """Автоопределение направления и преобразование текста."""
    if has_cyrillic(text):
        return to_latin(text)
    return to_cyrillic(text)
