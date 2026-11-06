#!/usr/bin/env python3
"""CLI wrapper — QQ pipeline health. Implementation: src/qq_pipeline_health.py."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from qq_pipeline_health import main

raise SystemExit(main())
