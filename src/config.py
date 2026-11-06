import json, pathlib
ROOT = pathlib.Path(__file__).resolve().parent.parent
def load():
    return json.loads((ROOT / "config.json").read_text())
