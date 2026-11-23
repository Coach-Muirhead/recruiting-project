"""Saves raw copies of the URLs listed in tools/sample_urls.txt (name<TAB>url per line)."""
import json, time
from pathlib import Path
import requests
OUT = Path("samples"); OUT.mkdir(exist_ok=True)
S = requests.Session(); S.headers["User-Agent"] = "RecruitingProjectBot/1.0 (discovery sampling)"
log = []
for line in Path("tools/sample_urls.txt").read_text().splitlines():
    if not line.strip(): continue
    name, url = line.split("\t")
    try:
        r = S.get(url, timeout=30); text, st, final = r.text, r.status_code, r.url
    except Exception as e:
        text, st, final = f"ERROR {e}", 0, url
    (OUT / name).write_text(text); log.append({"name": name, "url": url, "final": final, "status": st, "bytes": len(text)}); time.sleep(1)
(OUT / "index.json").write_text(json.dumps(log, indent=1))
