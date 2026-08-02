import json
import os

PRESETS_PATH = os.path.join(os.path.dirname(__file__), "data", "tag_presets.json")


def load_presets():
    if os.path.exists(PRESETS_PATH):
        with open(PRESETS_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    return {}


def save_preset(name, tags):
    name = name.strip()
    presets = load_presets()
    presets[name] = tags
    os.makedirs(os.path.dirname(PRESETS_PATH), exist_ok=True)
    with open(PRESETS_PATH, "w", encoding="utf-8") as f:
        json.dump(presets, f, ensure_ascii=False, indent=2)
    return presets


def delete_preset(name):
    presets = load_presets()
    presets.pop(name, None)
    with open(PRESETS_PATH, "w", encoding="utf-8") as f:
        json.dump(presets, f, ensure_ascii=False, indent=2)
    return presets
