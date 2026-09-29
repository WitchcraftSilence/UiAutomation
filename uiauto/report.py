"""HTML-Bericht für einen Durchlauf."""
import html
from datetime import datetime

STATUS_LABEL = {
    "ok": ("OK", "#1a7f37"),
    "fail": ("Fehler", "#cf222e"),
    "aborted": ("Abgebrochen", "#9a6700"),
    "skipped": ("Übersprungen", "#6e7781"),
}


def write(result, path):
    rows = []
    for s in result.steps:
        label, color = STATUS_LABEL[s.status]
        shot = (f'<br><a href="{html.escape(s.screenshot)}"><img src="{html.escape(s.screenshot)}"></a>'
                if s.screenshot else "")
        rows.append(
            f"<tr><td>{html.escape(s.phase)}</td><td>{s.index}</td>"
            f"<td><code>{html.escape(s.description)}</code></td>"
            f'<td style="color:{color};font-weight:600">{label}</td>'
            f"<td>{s.duration:.2f} s</td><td>{html.escape(s.detail)}{shot}</td></tr>")

    label, color = STATUS_LABEL[result.status]
    doc = f"""<!doctype html>
<html lang="de"><head><meta charset="utf-8">
<title>{html.escape(result.scenario)}</title>
<style>
body {{ font-family: Segoe UI, sans-serif; margin: 24px; color: #1f2328; }}
table {{ border-collapse: collapse; width: 100%; }}
td, th {{ border-bottom: 1px solid #d0d7de; padding: 6px 10px; text-align: left; vertical-align: top; }}
th {{ background: #f6f8fa; }}
img {{ max-width: 640px; margin-top: 6px; border: 1px solid #d0d7de; }}
.status {{ color: {color}; font-weight: 700; }}
</style></head><body>
<h1>{html.escape(result.scenario)}</h1>
<p>Start: {result.started:%d.%m.%Y %H:%M:%S} · Dauer: {result.duration:.1f} s ·
Status: <span class="status">{label}</span></p>
<p>{html.escape(result.message)}</p>
<table><tr><th>Phase</th><th>#</th><th>Schritt</th><th>Status</th><th>Dauer</th><th>Details</th></tr>
{''.join(rows)}
</table>
<p style="color:#6e7781">Erstellt {datetime.now():%d.%m.%Y %H:%M:%S}</p>
</body></html>"""
    path.write_text(doc, encoding="utf-8")
