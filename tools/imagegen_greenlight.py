#!/usr/bin/env python3
"""CLI：本机生图 greenlight。实现见 src/imagegen_greenlight.py。"""
import sys, pathlib
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "src"))
from imagegen_greenlight import main
raise SystemExit(main())
