#!/usr/bin/env python3
"""Entry: QQ full-pipeline health check (read-only). See src/qq_pipeline_health.py."""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from qq_pipeline_health import main

raise SystemExit(main())
