"""Federal Equity in Athletics (EADA) data: every school receiving federal aid reports each varsity sport.
Writes data/eada_baseball.json  {unitid: {name, city, state, classification, men}}  for schools with men's baseball."""
import io, json, sys, zipfile, requests
from pathlib import Path
import openpyxl

ROOT = Path(__file__).resolve().parent.parent
API = "https://ope.ed.gov/athletics/api/dataFiles/"
S = requests.Session(); S.headers["User-Agent"] = "RecruitingProjectBot/1.0 (+https://coach-muirhead.github.io/recruiting-project/)"


def latest():
    files = [f for f in S.get(API + "fileList", timeout=60).json() if f.get("Format") == "Excel"]
    return max(files, key=lambda f: f["Year"])


def main(inspect=False):
    f = latest(); print("file:", f["FileName"], f["Year"], f.get("Description"), file=sys.stderr)
    z = zipfile.ZipFile(io.BytesIO(S.get(API + "file", params={"fileName": f["FileName"]}, timeout=300).content))
    print("contents:", z.namelist(), file=sys.stderr)
    out = {}
    for name in z.namelist():
        if not name.lower().endswith((".xlsx", ".xls")):
            continue
        wb = openpyxl.load_workbook(io.BytesIO(z.read(name)), read_only=True)
        ws = wb.worksheets[0]
        rows = ws.iter_rows(values_only=True)
        head = [str(h or "").strip() for h in next(rows)]
        print(name, len(head), "columns:", head[:60], file=sys.stderr)
        low = [h.lower() for h in head]
        if "sports" not in low and "sport" not in low:
            continue
        col = {h: i for i, h in enumerate(low)}
        sport = col.get("sports", col.get("sport"))
        n = 0
        for r in rows:
            if inspect and n < 3:
                print("  sample:", dict(zip(head, r)), file=sys.stderr); n += 1
            if str(r[sport] or "").strip().lower() != "baseball":
                continue
            g = lambda k: r[col[k]] if k in col else None
            men = g("partic_men") or g("sum_partic_men") or 0
            if not men:
                continue
            out[str(g("unitid"))] = {"name": g("institution_name"), "city": g("city_txt"), "state": g("state_cd"),
                                     "classification": g("classification_name"), "men": men}
    (ROOT / "data" / "eada_baseball.json").write_text(json.dumps(out, indent=0, sort_keys=True))
    print("schools with men's baseball:", len(out), file=sys.stderr)


if __name__ == "__main__":
    main(inspect="--inspect" in sys.argv)
