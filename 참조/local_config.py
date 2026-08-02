import json
import os
import tempfile
import threading

CONFIG_PATH = os.path.join(os.path.dirname(__file__), "data", "local_config.json")

_lock = threading.Lock()


def load_config():
    if not os.path.exists(CONFIG_PATH):
        return {}
    with open(CONFIG_PATH, "r", encoding="utf-8") as f:
        raw = f.read()
    if not raw.strip():
        return {}
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        # Corrupted file (e.g. from a past concurrent-write race) - back it up instead of
        # crashing every load, and start fresh so the app is usable again.
        backup_path = CONFIG_PATH + ".corrupted"
        with open(backup_path, "w", encoding="utf-8") as f:
            f.write(raw)
        return {}


def save_config(**kwargs):
    with _lock:
        cfg = load_config()
        cfg.update(kwargs)
        os.makedirs(os.path.dirname(CONFIG_PATH), exist_ok=True)
        # Write to a temp file and rename over the target so a crash or a race with another
        # writer can never leave the config file half-written / corrupted.
        fd, tmp_path = tempfile.mkstemp(dir=os.path.dirname(CONFIG_PATH), prefix=".local_config_", suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(cfg, f, ensure_ascii=False, indent=2)
            os.replace(tmp_path, CONFIG_PATH)
        except BaseException:
            if os.path.exists(tmp_path):
                os.remove(tmp_path)
            raise
        return cfg
