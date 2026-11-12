"""Bundle kompas_sdk + one model into a standalone script: python tools/bundle.py korpus"""

import re
import sys
from pathlib import Path

root = Path(__file__).resolve().parent.parent / "kompas_sdk"
model = sys.argv[1] if len(sys.argv) > 1 else "cylinder"
parts = [
    ("constants", root / "constants.py"),
    ("orientation", root / "orientation.py"),
    ("part", root / "part.py"),
    ("app", root / "app.py"),
    (model, root / "models" / f"{model}.py"),
]
header = (
    f'"""KOMPAS-3D v24: build the «{model}» part (single-file version of kompas_sdk).\n\n'
    "Usage (Windows, KOMPAS-3D v24, 64-bit Python):\n"
    "    pip install pywin32\n"
    f"    python kompas_{model}.py --out {model}.m3d\n"
    "Options: see --help\n"
    '"""\n'
    "from __future__ import annotations\n\n"
    "import argparse\n"
    "import math\n"
    "from contextlib import contextmanager\n"
    "from dataclasses import dataclass, field  # noqa: F401\n"
    "from pathlib import Path\n"
    "from typing import Callable, Iterable, Iterator\n"
)
out = [header]
for name, path in parts:
    src = path.read_text(encoding="utf-8")
    src = re.sub(r'^""".*?"""\n', "", src, count=1, flags=re.S)
    src = re.sub(r"^from \S+ import \(.*?\)\n", "", src, flags=re.S | re.M)
    src = re.sub(r"^(from|import) .*\n", "", src, flags=re.M)
    src = re.sub(r"^\s+from \.+\w* import .*\n", "", src, flags=re.M)
    if name == "constants":
        out.append("\nclass c:  # KOMPAS API5 constants\n")
        out += ["    " + line + "\n" if line.strip() else "\n" for line in src.splitlines()]
    else:
        out.append(f"\n# ---- {name} " + "-" * 60 + "\n" + src)
out_path = root.parent / f"kompas_{model}.py"
out_path.write_text("".join(out), encoding="utf-8")
print(f"wrote {out_path}")
