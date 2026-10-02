#!/usr/bin/env python3
"""Regenerate the wiring diagrams in docs/wiring/ from their WireViz YAML sources.

    python docs/wiring/build.py            # all *.yml in docs/wiring/
    python docs/wiring/build.py foo.yml    # just one

For each harness it writes <name>.svg (embedded in the docs) and <name>.bom.tsv (bill of
materials). WireViz's intermediate .gv/.html/.png are not kept. Needs: pip install wireviz
(0.4.x) and Graphviz (`dot`); on Windows the default Graphviz install folder is added to PATH.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
WINDOWS_GRAPHVIZ = [Path(r"C:\Program Files\Graphviz\bin"), Path(r"C:\Program Files (x86)\Graphviz\bin")]


def ensure_dot() -> None:
    if shutil.which("dot"):
        return
    for d in WINDOWS_GRAPHVIZ:
        if (d / "dot.exe").exists():
            os.environ["PATH"] = f"{d}{os.pathsep}{os.environ['PATH']}"
            return
    sys.exit("Graphviz 'dot' not found (Windows: winget install Graphviz.Graphviz; Debian: apt install graphviz)")


def build(src: Path) -> None:
    print(f"==> {src.name}")
    subprocess.run([sys.executable, "-m", "wireviz.wv_cli", "-f", "st", str(src)], check=True, cwd=HERE)
    tsv = src.with_suffix(".tsv")
    if tsv.exists():  # WireViz names the BOM <name>.tsv; make its purpose obvious
        tsv.replace(src.with_suffix(".bom.tsv"))
    svg = src.with_suffix(".svg")
    # Normalise line endings so the committed SVG diffs cleanly (repo is LF)
    svg.write_bytes(svg.read_bytes().replace(b"\r\n", b"\n"))
    print(f"    wrote {svg.name}, {src.with_suffix('.bom.tsv').name}")


def main() -> int:
    ensure_dot()
    files = [HERE / a for a in sys.argv[1:]] or sorted(HERE.glob("*.yml"))
    for f in files:
        build(f)
    return 0


if __name__ == "__main__":
    sys.exit(main())
