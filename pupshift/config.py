# -*- coding: utf-8 -*-
"""Конфигурация pupshift: ~/.config/pupshift/config.json"""
import json
import os

CONFIG_DIR = os.path.join(os.path.expanduser("~"), ".config", "pupshift")
CONFIG_PATH = os.path.join(CONFIG_DIR, "config.json")

DEFAULT = {
    "hotkey": {"key": "Pause", "mods": []},  # mods: shift/ctrl/alt/super
    "autostart": True,
}


def load():
    cfg = json.loads(json.dumps(DEFAULT))  # глубокая копия
    try:
        with open(CONFIG_PATH, "r", encoding="utf-8") as f:
            user = json.load(f)
        if isinstance(user, dict):
            if isinstance(user.get("hotkey"), dict):
                cfg["hotkey"].update(user["hotkey"])
            if isinstance(user.get("autostart"), bool):
                cfg["autostart"] = user["autostart"]
    except (OSError, ValueError):
        pass
    return cfg


def save(cfg):
    os.makedirs(CONFIG_DIR, exist_ok=True)
    tmp = CONFIG_PATH + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(cfg, f, ensure_ascii=False, indent=2)
    os.replace(tmp, CONFIG_PATH)
