# -*- coding: utf-8 -*-
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from pupshift.transform import transform, to_cyrillic, to_latin, has_cyrillic


def test_russian_word_to_latin():
    assert transform("привет") == "ghbdtn"


def test_latin_word_to_cyrillic():
    assert transform("ghbdtn") == "привет"


def test_mixed_case():
    assert transform("Привет") == "Ghbdtn"
    assert transform("Ghbdtn") == "Привет"
    assert transform("ПРИВЕТ") == "GHBDTN"


def test_digits_and_punct_unchanged():
    assert transform("1234") == "1234"
    # физическая клавиша US "." в ru даёт "ю"
    assert transform("ghbdtn.") == "приветю"
    # кириллическая точка живёт на клавише "/", в us это "/"
    assert transform("привет.") == "ghbdtn/"


def test_russian_punctuation():
    # "ж" на клавише ";", "б" на ",", "ю" на "."
    assert transform("жбю") == ";,."
    assert transform(";,.") == "жбю"


def test_shift_symbols():
    # @ -> " (shift+2); m -> ь, a -> ф, i -> ш, l -> д; "." -> ю
    assert transform("user@mail.ru") == 'гыук"ьфшдюкг'
    assert transform('гыук"ьфшдюкг') == "user@mail.ru"


def test_mixed_text_direction_by_cyrillic():
    # латинская часть не трогается, кириллическая переводится
    assert transform("hello мир") == "hello vbh"


def test_empty_and_no_letters():
    assert transform("") == ""
    assert transform("   ") == "   "


def test_has_cyrillic():
    assert has_cyrillic("мир")
    assert not has_cyrillic("world")
