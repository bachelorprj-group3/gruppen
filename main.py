


# main.py
"""
Gruppenzuteilung (1..G) – automatisch & möglichst gleichmäßig
Teilnehmer-Seite:  /
Admin/Host:        /admin

Local Start:
  uvicorn main:app --reload --host 127.0.0.1 --port 8000

Render Start Command:
  uvicorn main:app --host 0.0.0.0 --port $PORT

Hinweis zu SQLite auf Render:
- Ohne Persistent Disk kann die DB nach Restart/Deploy verloren gehen.
- Optional: setze Umgebungsvariable DB_PATH auf z.B. /var/data/assignments.sqlite
"""

import os
import sqlite3
import secrets
from datetime import datetime
from typing import List, Optional

from fastapi import FastAPI, Request, Form
from fastapi.responses import HTMLResponse, RedirectResponse

# ---------------------------
# Konfiguration
# ---------------------------
DB_PATH = os.environ.get("DB_PATH", "assignments.sqlite")

DEFAULT_TOTAL = int(os.environ.get("DEFAULT_TOTAL", "1000"))
DEFAULT_GROUPS = int(os.environ.get("DEFAULT_GROUPS", "7"))

COOKIE_NAME = "ga_token"

app = FastAPI()


# ---------------------------
# Datenbank
# ---------------------------
def db_connect() -> sqlite3.Connection:
    con = sqlite3.connect(DB_PATH, check_same_thread=False)
    con.row_factory = sqlite3.Row
    return con


@app.on_event("startup")
def init_db() -> None:
    con = db_connect()
    try:
        cur = con.cursor()
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS assignments (
                token TEXT PRIMARY KEY,
                grp INTEGER NOT NULL,
                created_at TEXT NOT NULL
            );
            """
        )
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS settings (
                key TEXT PRIMARY KEY,
                value INTEGER NOT NULL
            );
            """
        )

        cur.execute(
            "INSERT OR IGNORE INTO settings(key, value) VALUES ('total', ?);",
            (DEFAULT_TOTAL,),
        )
        cur.execute(
            "INSERT OR IGNORE INTO settings(key, value) VALUES ('groups', ?);",
            (DEFAULT_GROUPS,),
        )
        con.commit()
    finally:
        con.close()


def get_setting(con: sqlite3.Connection, key: str) -> int:
    row = con.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
    return int(row["value"]) if row else 0


def set_setting(con: sqlite3.Connection, key: str, value: int) -> None:
    con.execute(
        """
        INSERT INTO settings(key, value) VALUES (?, ?)
        ON CONFLICT(key) DO UPDATE SET value=excluded.value;
        """,
        (key, int(value)),
    )
    con.commit()


def reset_assignments(con: sqlite3.Connection) -> None:
    con.execute("DELETE FROM assignments;")
    con.commit()


# ---------------------------
# Algorithmus
# ---------------------------
def compute_capacities(total: int, g: int) -> List[int]:
    """
    Kapazität pro Gruppe basierend auf total & g.
    Beispiel: 1000 / 7 -> 143,143,143,143,143,143,142
    """
    base = total // g
    rest = total % g
    caps = [base] * g
    for i in range(rest):
        caps[i] += 1
    return caps


def get_counts(con: sqlite3.Connection, g: int) -> List[int]:
    counts = [0] * g
    rows = con.execute(
        "SELECT grp, COUNT(*) AS n FROM assignments GROUP BY grp"
    ).fetchall()
    for r in rows:
        grp = int(r["grp"])
        if 1 <= grp <= g:
            counts[grp - 1] = int(r["n"])
    return counts


def choose_group_fair(counts: List[int], caps: List[int]) -> int:
    """
    Fairer Algorithmus:

    remaining = cap - assigned
      - Wenn es freie Plätze gibt: wähle Gruppe mit MAX remaining (am leersten)
      - Wenn alle voll/übervoll: wähle Gruppe mit MAX remaining (am wenigsten übervoll)

    Ergebnis: möglichst gleichmäßige Verteilung, auch wenn Einstellungen später geändert werden.
    """
    remaining = [caps[i] - counts[i] for i in range(len(caps))]
    best = max(remaining)
    kandidaten = [i for i, r in enumerate(remaining) if r == best]
    return secrets.choice(kandidaten) + 1  # 1..G


def assign_group(con: sqlite3.Connection, token: str) -> int:
    """
    Thread-sicher: BEGIN IMMEDIATE sperrt kurz die DB für parallele Writes.
    """
    con.execute("BEGIN IMMEDIATE;")
    try:
        # Token schon vorhanden? Dann gleiche Gruppe zurückgeben.
        row = con.execute("SELECT grp FROM assignments WHERE token=?", (token,)).fetchone()
        if row:
            con.execute("COMMIT;")
            return int(row["grp"])

        total = get_setting(con, "total")
        g = get_setting(con, "groups")
        if total < 1 or g < 1:
            raise ValueError("Ungültige Einstellungen (Teilnehmerzahl / Gruppen).")

        caps = compute_capacities(total, g)
        counts = get_counts(con, g)

        grp = choose_group_fair(counts, caps)

        con.execute(
            "INSERT INTO assignments(token, grp, created_at) VALUES (?,?,?)",
            (token, grp, datetime.utcnow().isoformat(timespec="seconds")),
        )
        con.execute("COMMIT;")
        return grp
    except Exception:
        con.execute("ROLLBACK;")
        raise


# ---------------------------
# Cookie / Token
# ---------------------------
def ensure_token(request: Request, response: HTMLResponse) -> str:
    token = request.cookies.get(COOKIE_NAME)
    if not token:
        token = secrets.token_urlsafe(24)
        response.set_cookie(
            COOKIE_NAME,
            token,
            max_age=30 * 24 * 3600,
            httponly=True,
            samesite="lax",
        )
    return token


# ---------------------------
# HTML
# ---------------------------
def participant_html(group: Optional[int] = None, error: Optional[str] = None) -> str:
    g_html = ""
    if group is not None:
        g_html = f"""
        <div class="big">Deine Gruppe ist: {group}</div>
        <p>Bitte gehe im Spiel in <b>Gruppe {group}</b>.</p>
        """
    e_html = f"""<p class="err"><b>Fehler:</b> {error}</p>""" if error else ""

    return f"""
<!doctype html>
<html lang="de">
<head>
  <meta charset="utf-8">
  <title>Gruppenzuteilung</title>
  <style>
    body {{ font-family: Arial, sans-serif; margin: 24px; background:#fff; }}
    h1 {{ margin-bottom: 14px; }}
    .wrap {{ display:flex; gap:24px; align-items:flex-start; flex-wrap:wrap; }}
    .card {{ border: 1px solid #ddd; border-radius: 10px; padding: 18px; width: 760px; max-width: 100%; }}
    .btn {{ padding: 10px 14px; border-radius: 6px; border: 0; background: #2f5d9b; color: white; cursor: pointer; }}
    .btn:hover {{ opacity: .92; }}
    .big {{ font-size: 44px; margin: 18px 0 10px; }}
    .muted {{ color: #666; font-size: 13px; margin-top: 10px; }}
    .err {{ color:#c0392b; }}
    a {{ color:#2f5d9b; text-decoration:none; }}
    a:hover {{ text-decoration:underline; }}
  </style>
</head>
<body>
  <h1>Gruppenzuteilung (1–{get_display_groups()}) — automatisch & gleichmäßig</h1>

  <div class="wrap">
    <div class="card">
      <h2>Teilnehmer</h2>
      <p>1) Link/QR-Code öffnen</p>
      <p>2) Button klicken → du bekommst deine Gruppennummer</p>

      <form method="post" action="/assign">
        <button class="btn" type="submit">Meine Gruppennummer anzeigen</button>
      </form>

      {g_html}
      {e_html}

      <p class="muted">Hinweis: Reload behält die gleiche Gruppe (Token im Browser).</p>
      <p class="muted">Admin/Host: <a href="/admin">/admin</a></p>
    </div>
  </div>
</body>
</html>
"""


def admin_html(status_rows, total: int, groups: int, note: Optional[str] = None) -> str:
    note_html = f"""<div class="note">{note}</div>""" if note else ""

    rows = ""
    for r in status_rows:
        remaining = r["remaining"]
        over = ""
        if remaining < 0:
            over = f' <span class="over">(überbucht {abs(remaining)})</span>'
        rows += f"""
        <tr>
          <td>{r['group']}</td>
          <td>{r['assigned']}</td>
          <td>{r['capacity']}</td>
          <td>{remaining}{over}</td>
        </tr>
        """

    return f"""
<!doctype html>
<html lang="de">
<head>
  <meta charset="utf-8">
  <title>Admin / Host</title>
  <meta http-equiv="refresh" content="3">
  <style>
    body {{ font-family: Arial, sans-serif; margin: 24px; background:#fff; }}
    h1 {{ margin-bottom: 14px; }}
    .row {{ display:flex; gap:24px; align-items:flex-start; flex-wrap:wrap; }}
    .card {{ border: 1px solid #ddd; border-radius: 10px; padding: 18px; width: 520px; max-width: 100%; }}
    .cardWide {{ border: 1px solid #ddd; border-radius: 10px; padding: 18px; width: 760px; max-width: 100%; }}
    input {{ width: 100%; padding: 8px; margin: 6px 0 12px; }}
    .btn {{ padding: 10px 14px; border-radius: 6px; border: 0; background: #2f5d9b; color: white; cursor: pointer; }}
    .danger {{ background:#c0392b; }}
    table {{ width:100%; border-collapse: collapse; margin-top: 10px; }}
    th, td {{ border-bottom:1px solid #eee; padding:8px; text-align:left; }}
    .note {{ background:#f6f8fa; border:1px solid #e6e6e6; padding:10px; border-radius:8px; margin-bottom:14px; }}
    .muted {{ color:#666; font-size:13px; }}
    .over {{ color:#c0392b; font-size:12px; }}
    code {{ background:#f1f1f1; padding:2px 6px; border-radius:6px; }}
    a {{ color:#2f5d9b; text-decoration:none; }}
    a:hover {{ text-decoration:underline; }}
  </style>
</head>
<body>
  <h1>Admin / Host — Live-Status</h1>
  {note_html}

  <div class="row">
    <div class="card">
      <h2>Einstellungen</h2>
      <form method="post" action="/admin/save">
        <label>Erwartete Teilnehmerzahl (kannst du jederzeit ändern)</label>
        <input name="total" type="number" min="1" value="{total}" />

        <label>Anzahl Gruppen</label>
        <input name="groups" type="number" min="1" max="50" value="{groups}" />

        <button class="btn" type="submit">Einstellungen speichern</button>
      </form>

      <form method="post" action="/admin/reset" style="margin-top:10px;">
        <button class="btn danger" type="submit">Reset: alle Zuweisungen löschen</button>
      </form>

      <p class="muted" style="margin-top:12px;">
        Auto-Refresh alle 3 Sekunden.
      </p>
      <p class="muted"><a href="/">Zur Teilnehmer-Seite</a></p>
    </div>

    <div class="cardWide">
      <h2>Live-Status (pro Gruppe)</h2>
      <table>
        <thead>
          <tr>
            <th>Gruppe</th>
            <th>Zugewiesen</th>
            <th>Kapazität</th>
            <th>Übrig</th>
          </tr>
        </thead>
        <tbody>
          {rows}
        </tbody>
      </table>
      <p class="muted">
        Hinweis: Wenn du die Teilnehmerzahl nachträglich kleiner stellst als schon zugeteilt wurde,
        wird „Übrig“ negativ (= überbucht). Die Zuweisung läuft trotzdem fair weiter.
      </p>
    </div>
  </div>
</body>
</html>
"""


def get_display_groups() -> int:
    # Für die Überschrift auf der Startseite. Falls DB noch nicht verfügbar ist, fallback auf DEFAULT_GROUPS.
    try:
        con = db_connect()
        try:
            g = get_setting(con, "groups")
            return g if g > 0 else DEFAULT_GROUPS
        finally:
            con.close()
    except Exception:
        return DEFAULT_GROUPS


# ---------------------------
# Routes
# ---------------------------
@app.get("/", response_class=HTMLResponse)
def participant_page():
    return HTMLResponse(participant_html())


@app.post("/assign", response_class=HTMLResponse)
def participant_assign(request: Request):
    response = HTMLResponse()
    token = ensure_token(request, response)

    con = db_connect()
    try:
        grp = assign_group(con, token)
        html = participant_html(group=grp)
    except Exception as e:
        html = participant_html(error=str(e))
    finally:
        con.close()

    response.body = html.encode("utf-8")
    response.media_type = "text/html"
    return response


@app.get("/admin", response_class=HTMLResponse)
def admin_page(request: Request):
    note = "Einstellungen gespeichert." if request.query_params.get("note") else None

    con = db_connect()
    try:
        total = get_setting(con, "total")
        g = get_setting(con, "groups")
        caps = compute_capacities(total, g)
        cnt = get_counts(con, g)

        status = []
        for i in range(g):
            status.append(
                {
                    "group": i + 1,
                    "assigned": cnt[i],
                    "capacity": caps[i],
                    "remaining": caps[i] - cnt[i],
                }
            )
    finally:
        con.close()

    return HTMLResponse(admin_html(status, total, g, note=note))


@app.post("/admin/save")
def admin_save(total: int = Form(...), groups: int = Form(...)):
    con = db_connect()
    try:
        set_setting(con, "total", int(total))
        set_setting(con, "groups", int(groups))
    finally:
        con.close()

    return RedirectResponse("/admin?note=1", status_code=303)


@app.post("/admin/reset")
def admin_reset():
    con = db_connect()
    try:
        reset_assignments(con)
    finally:
        con.close()
    return RedirectResponse("/admin?note=1", status_code=303)
