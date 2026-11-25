"""Evidence at the subprocess boundary, distinct from model-tool observations."""
import datetime
import hashlib
import json
from pathlib import Path


def invocation(out_dir, argv):
    refs = [str(argv[i + 1]) for i, arg in enumerate(argv[:-1]) if arg == "-i"]
    record = {
        "event": "subprocess_invocation",
        "at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "argv": list(argv), "cwd": str(out_dir),
        "references": [{"path": p, "sha256": hashlib.sha256(Path(p).read_bytes()).hexdigest()
                        if Path(p).is_file() else None} for p in refs],
    }
    with (Path(out_dir) / "invocations.jsonl").open("a", encoding="utf-8") as f:
        f.write(json.dumps(record, ensure_ascii=False) + "\n")
    return record


def tool_references(stdout):
    """Only explicit structured tool arguments count; never infer them from prose."""
    found = []
    def visit(obj):
        if isinstance(obj, dict):
            name = str(obj.get("name") or obj.get("tool_name") or "")
            if "imagegen" in name or "image_gen" in name:
                args = obj.get("arguments", obj.get("input", {}))
                if isinstance(args, str):
                    try: args = json.loads(args)
                    except ValueError: args = {}
                if isinstance(args, dict) and "referenced_image_paths" in args:
                    found.append({"tool": name, "referenced_image_paths": args["referenced_image_paths"]})
            for value in obj.values():
                if isinstance(value, (dict, list)): visit(value)
        elif isinstance(obj, list):
            for value in obj: visit(value)
    for line in stdout.splitlines():
        try: visit(json.loads(line))
        except ValueError: continue
    return {"status": "observed" if found else "not_exposed", "calls": found}
