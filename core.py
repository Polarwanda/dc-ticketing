"""Core ticket logic, shared by the CLI (cli.py) and the web app (app.py).

Nothing in here prints or talks to the web. Functions take plain values,
change the database, and raise TicketError when something isn't allowed.
"""
import sqlite3
from datetime import datetime, timedelta

# ---------- SEGMENT 1: Constants ----------
DB_FILE = "tickets.db"
PRIORITIES = ["low", "medium", "high", "critical"]
TYPES = ["hardware_fault", "install", "decommission",
         "cabling", "remote_hands", "environmental"]
FEEDS = ["A", "B"]  # the two redundant power feeds feeding a rack

# Hours allowed to resolve a ticket, by priority (the SLA)
SLA_HOURS = {"critical": 4, "high": 8, "medium": 24, "low": 72}

# Workflow: which status may move to which
TRANSITIONS = {
    "open": ["assigned", "closed"],
    "assigned": ["in_progress", "on_hold", "open"],
    "in_progress": ["on_hold", "resolved"],
    "on_hold": ["in_progress"],
    "resolved": ["closed", "in_progress"],
    "closed": [],
}


# ---------- SEGMENT 2: Errors ----------
class TicketError(Exception):
    """Something the user should be told about (bad input, illegal move)."""


class RedundancyConflict(TicketError):
    """Starting this work would overlap with work on the opposite power feed."""

    def __init__(self, conflicts):
        self.conflicts = conflicts
        ids = ", ".join(f"#{c['id']}" for c in conflicts)
        super().__init__(
            f"Redundancy conflict: ticket(s) {ids} already in progress on the "
            "opposite feed of this rack. Provide an override reason to continue.")


# ---------- SEGMENT 3: Database ----------
SCHEMA = """
CREATE TABLE IF NOT EXISTS assets (
    asset_tag TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    site TEXT, rack TEXT, feed TEXT
);
CREATE TABLE IF NOT EXISTS tickets (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    title TEXT NOT NULL,
    type TEXT NOT NULL,
    priority TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'open',
    site TEXT, rack TEXT, feed TEXT,
    asset_tag TEXT REFERENCES assets(asset_tag),
    assignee TEXT,
    created TEXT NOT NULL,
    updated TEXT NOT NULL,
    due TEXT NOT NULL,
    resolved_at TEXT
);
CREATE TABLE IF NOT EXISTS history (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ticket_id INTEGER NOT NULL REFERENCES tickets(id),
    time TEXT NOT NULL,
    note TEXT NOT NULL
);
"""


def now():
    return datetime.now().replace(microsecond=0)


def connect(db_file=DB_FILE):
    conn = sqlite3.connect(db_file)
    conn.row_factory = sqlite3.Row  # access columns by name
    conn.executescript(SCHEMA)
    return conn


def _clean(value):
    """Turn None, '' and '   ' into None; strip everything else."""
    return (value or "").strip() or None


# ---------- SEGMENT 4: Assets ----------
def add_asset(conn, asset_tag, name, site=None, rack=None, feed=None):
    asset_tag, name, site, rack, feed = map(_clean, (asset_tag, name, site, rack, feed))
    if not asset_tag or not name:
        raise TicketError("Asset tag and name are required.")
    if feed and feed not in FEEDS:
        raise TicketError("Feed must be A or B.")
    try:
        with conn:
            conn.execute("INSERT INTO assets VALUES (?,?,?,?,?)",
                         (asset_tag, name, site, rack, feed))
    except sqlite3.IntegrityError:
        raise TicketError(f"Asset {asset_tag} already exists.") from None


def get_asset(conn, asset_tag):
    row = conn.execute("SELECT * FROM assets WHERE asset_tag = ?", (asset_tag,)).fetchone()
    if row is None:
        raise TicketError(f"Unknown asset '{asset_tag}'. Add it first.")
    return row


def list_assets(conn):
    """All assets, each with a count of its unresolved tickets."""
    return conn.execute("""
        SELECT a.*, (SELECT COUNT(*) FROM tickets t
                     WHERE t.asset_tag = a.asset_tag
                       AND t.status NOT IN ('resolved', 'closed')) AS active_tickets
        FROM assets a ORDER BY a.asset_tag""").fetchall()


# ---------- SEGMENT 5: Tickets ----------
def get_ticket(conn, ticket_id):
    row = conn.execute("SELECT * FROM tickets WHERE id = ?", (ticket_id,)).fetchone()
    if row is None:
        raise TicketError(f"Ticket #{ticket_id} not found.")
    return row


def get_history(conn, ticket_id):
    return conn.execute("SELECT * FROM history WHERE ticket_id = ? ORDER BY id",
                        (ticket_id,)).fetchall()


def log(conn, ticket_id, note):
    """Record an event and bump the ticket's 'updated' time."""
    ts = now().isoformat()
    conn.execute("INSERT INTO history (ticket_id, time, note) VALUES (?, ?, ?)",
                 (ticket_id, ts, note))
    conn.execute("UPDATE tickets SET updated = ? WHERE id = ?", (ts, ticket_id))


def create_ticket(conn, title, type_, priority="medium",
                  site=None, rack=None, feed=None, asset_tag=None):
    title = _clean(title)
    site, rack, feed, asset_tag = map(_clean, (site, rack, feed, asset_tag))
    if not title:
        raise TicketError("Title is required.")
    if type_ not in TYPES:
        raise TicketError(f"Unknown type '{type_}'. Choose from: {', '.join(TYPES)}")
    if priority not in PRIORITIES:
        raise TicketError(f"Unknown priority '{priority}'. Choose from: {', '.join(PRIORITIES)}")
    if asset_tag:  # link to an asset and inherit its location unless overridden
        asset = get_asset(conn, asset_tag)
        site, rack, feed = site or asset["site"], rack or asset["rack"], feed or asset["feed"]
    if feed and feed not in FEEDS:
        raise TicketError("Feed must be A or B.")

    created = now()
    due = created + timedelta(hours=SLA_HOURS[priority])
    with conn:
        cur = conn.execute(
            """INSERT INTO tickets (title, type, priority, site, rack, feed,
               asset_tag, created, updated, due) VALUES (?,?,?,?,?,?,?,?,?,?)""",
            (title, type_, priority, site, rack, feed, asset_tag,
             created.isoformat(), created.isoformat(), due.isoformat()))
        log(conn, cur.lastrowid, "Ticket created")
    return cur.lastrowid


def assign_ticket(conn, ticket_id, tech):
    t = get_ticket(conn, ticket_id)
    tech = _clean(tech)
    if not tech:
        raise TicketError("Technician name is required.")
    if t["status"] in ("resolved", "closed"):
        raise TicketError(f"Ticket #{ticket_id} is {t['status']}; reopen it first.")
    with conn:
        conn.execute("UPDATE tickets SET assignee = ? WHERE id = ?", (tech, ticket_id))
        log(conn, ticket_id, f"Assigned to {tech}")
        if t["status"] == "open":
            conn.execute("UPDATE tickets SET status = 'assigned' WHERE id = ?", (ticket_id,))
            log(conn, ticket_id, "Status: open -> assigned")


def add_comment(conn, ticket_id, text):
    get_ticket(conn, ticket_id)  # makes sure it exists
    text = _clean(text)
    if not text:
        raise TicketError("Comment can't be empty.")
    with conn:
        log(conn, ticket_id, f"Comment: {text}")


def list_tickets(conn, status=None, priority=None, tech=None, site=None, asset_tag=None):
    query, params = "SELECT * FROM tickets WHERE 1=1", []
    for column, value in [("status", status), ("priority", priority),
                          ("assignee", tech), ("site", site), ("asset_tag", asset_tag)]:
        if value:
            query += f" AND {column} = ?"
            params.append(value)
    # Critical first, then oldest first
    query += """ ORDER BY CASE priority WHEN 'critical' THEN 0 WHEN 'high' THEN 1
                 WHEN 'medium' THEN 2 ELSE 3 END, created"""
    return conn.execute(query, params).fetchall()


# ---------- SEGMENT 6: Redundancy conflict detection ----------
def find_conflicts(conn, ticket):
    """In-progress tickets on the same rack but the OPPOSITE power feed."""
    if not (ticket["site"] and ticket["rack"] and ticket["feed"]):
        return []  # can't judge without a full location
    opposite = "B" if ticket["feed"] == "A" else "A"
    return conn.execute(
        """SELECT * FROM tickets WHERE status = 'in_progress' AND site = ?
           AND rack = ? AND feed = ? AND id != ?""",
        (ticket["site"], ticket["rack"], opposite, ticket["id"])).fetchall()


def change_status(conn, ticket_id, new_status, override_reason=None):
    t = get_ticket(conn, ticket_id)
    allowed = TRANSITIONS[t["status"]]
    if new_status not in allowed:
        raise TicketError(f"Can't go {t['status']} -> {new_status}. "
                          f"Allowed: {', '.join(allowed) or 'none'}")

    note = f"Status: {t['status']} -> {new_status}"
    if new_status == "in_progress":
        conflicts = find_conflicts(conn, t)
        if conflicts:
            reason = _clean(override_reason)
            if not reason:
                raise RedundancyConflict(conflicts)
            ids = ", ".join(f"#{c['id']}" for c in conflicts)
            note += f" | REDUNDANCY OVERRIDE: {reason} (conflicts with {ids})"

    resolved_at = t["resolved_at"]
    if new_status == "in_progress":  # reopened or resumed
        resolved_at = None
    elif new_status in ("resolved", "closed") and not resolved_at:
        resolved_at = now().isoformat()

    with conn:
        conn.execute("UPDATE tickets SET status = ?, resolved_at = ? WHERE id = ?",
                     (new_status, resolved_at, ticket_id))
        log(conn, ticket_id, note)


# ---------- SEGMENT 7: SLA ----------
def sla_state(ticket, at=None):
    """'on_track', 'at_risk' (last 25% of window), 'breached', or 'met'."""
    created = datetime.fromisoformat(ticket["created"])
    due = datetime.fromisoformat(ticket["due"])
    if ticket["resolved_at"]:
        done = datetime.fromisoformat(ticket["resolved_at"])
        return "met" if done <= due else "breached"
    at = at or now()
    if at > due:
        return "breached"
    if (due - at) <= (due - created) * 0.25:
        return "at_risk"
    return "on_track"
