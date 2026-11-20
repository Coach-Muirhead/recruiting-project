#!/usr/bin/env python3
"""Injects data/schools.json into template.html -> site/index.html."""
import json
from pathlib import Path
root = Path(__file__).resolve().parent
data = json.loads((root / "data" / "schools.json").read_text())
blob = json.dumps(data, ensure_ascii=False).replace("</", "<\\/")
html = (root / "template.html").read_text().replace("/*__DATA__*/", blob)
(root / "site").mkdir(exist_ok=True)
(root / "site" / "index.html").write_text(html)
print(f"built site/index.html with {len(data)} schools")
