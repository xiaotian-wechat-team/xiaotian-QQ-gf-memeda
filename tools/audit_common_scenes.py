#!/usr/bin/env python3
"""Read-only inventory for visual review. Never approves from a search query."""
import argparse
import hashlib
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
import common_scenes as cs


def audit(root):
    cfg = {'imagegen': {'common_scenes_dir': str(root)}}
    packs = {}
    for pid in cs.PACKS:
        folder = cs.pack_dir(pid, cfg)
        images = folder / 'images'
        paths = sorted(p for p in images.glob('*') if p.is_file() and p.suffix.lower() in {'.jpg', '.jpeg', '.png', '.webp'})
        approved = set(cs.list_pack_images(pid, cfg))
        packs[pid] = {'total': len(paths), 'usable': len(approved), 'images': [
            {'file': p.name, 'path': str(p), 'sha256': hashlib.sha256(p.read_bytes()).hexdigest(),
             'usable': p in approved} for p in paths]}
    return {'root': str(root), 'packs': packs}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, default=cs.DEFAULT_COMMON_DIR)
    args = parser.parse_args()
    print(json.dumps(audit(args.root.expanduser()), ensure_ascii=False, indent=2))
