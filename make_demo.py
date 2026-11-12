#!/usr/bin/env python3
"""Write the deterministic demo snapshot."""

from pathlib import Path

from weread_site.demo import write_demo


if __name__ == "__main__":
    write_demo(Path(__file__).resolve().parent / "data" / "demo-snapshot.json")
