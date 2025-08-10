# apps/agent/utils/logging.py
import os, sys, json, time, uuid
import logging as pylog

class JsonFormatter(pylog.Formatter):
    def format(self, record):
        base = {
            "ts": time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(record.created)),
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
        }
        if hasattr(record, "extra"):
            base.update(record.extra)
        return json.dumps(base, ensure_ascii=False)

def setup_logging():
    if getattr(setup_logging, "_done", False):
        return
    level = os.getenv("LOG_LEVEL", "INFO").upper()
    json_mode = os.getenv("LOG_JSON", "0") == "1"
    root = pylog.getLogger()
    root.setLevel(level)
    root.handlers.clear()
    handler = pylog.StreamHandler(sys.stdout)
    handler.setLevel(level)
    handler.setFormatter(JsonFormatter() if json_mode else pylog.Formatter(
        "%(asctime)s %(levelname)s %(name)s - %(message)s"
    ))
    root.addHandler(handler)
    setup_logging._done = True

    for name, level in {
        "httpx": pylog.WARNING,
        "urllib3": pylog.WARNING,
        "sentence_transformers": pylog.WARNING,
        "livekit": pylog.INFO,  # or WARNING if you want it quieter
    }.items():
        lg = pylog.getLogger(name)
        lg.setLevel(level)
        lg.propagate = False

def get_logger(name: str) -> pylog.Logger:
    setup_logging()
    return pylog.getLogger(name)
