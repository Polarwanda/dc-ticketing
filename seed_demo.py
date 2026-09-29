"""Fill an empty database with demo data (handy for screenshots).

Run once:  python seed_demo.py
"""
from datetime import timedelta

import core


def backdate(conn, ticket_id, hours_ago):
    """Pretend a ticket was created earlier, so SLA badges show variety."""
    t = core.get_ticket(conn, ticket_id)
    created = core.now() - timedelta(hours=hours_ago)
    due = created + timedelta(hours=core.SLA_HOURS[t["priority"]])
    with conn:
        conn.execute("UPDATE tickets SET created = ?, due = ? WHERE id = ?",
                     (created.isoformat(), due.isoformat(), ticket_id))


conn = core.connect()
if core.list_tickets(conn):
    raise SystemExit("Database already has tickets. Delete tickets.db first.")

for tag, name, rack, feed in [("PDU-12A", "Rack 12 PDU (feed A)", "R12", "A"),
                              ("PDU-12B", "Rack 12 PDU (feed B)", "R12", "B"),
                              ("SRV-DB07", "db-07 database server", "R12", "A"),
                              ("SW-CORE1", "Core switch 1", "R05", "A")]:
    core.add_asset(conn, tag, name, "DC1", rack, feed)

psu = core.create_ticket(conn, "Replace failed PSU on db-07", "hardware_fault",
                         "critical", asset_tag="SRV-DB07")
core.assign_ticket(conn, psu, "maria")
core.change_status(conn, psu, "in_progress")

pdu = core.create_ticket(conn, "Replace PDU-12B", "hardware_fault", "high",
                         asset_tag="PDU-12B")
core.assign_ticket(conn, pdu, "devon")
try:
    core.change_status(conn, pdu, "in_progress")
except core.RedundancyConflict as e:
    print("Blocked as expected:", e)

switches = core.create_ticket(conn, "Install 2 top-of-rack switches", "install",
                              "medium", site="DC1", rack="R05")
backdate(conn, switches, 30)  # 24h SLA, 30h old -> breached

cables = core.create_ticket(conn, "Re-cable uplinks", "cabling", "low", asset_tag="SW-CORE1")
backdate(conn, cables, 60)    # 72h SLA, 60h old -> at risk

print("Demo data loaded. Start the web app with: flask --app app run")
