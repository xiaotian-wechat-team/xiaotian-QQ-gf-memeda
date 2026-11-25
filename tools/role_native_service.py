#!/usr/bin/env python3
"""Run a deployment-configured native gateway; defaults to loopback only."""
import argparse
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from native_gateway import Gateway, server


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=3920)
    args = parser.parse_args()
    gateway = Gateway(args.config)
    gateway.accounts()  # Validate before opening the service socket.
    with server((args.host, args.port), gateway) as httpd:
        httpd.serve_forever()


if __name__ == "__main__":
    main()
