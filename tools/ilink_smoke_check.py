#!/usr/bin/env python3
import sys, pathlib
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "src"))
from channels.ilink_smoke import main
raise SystemExit(main())
