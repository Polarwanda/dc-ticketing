import unittest
from datetime import datetime, timedelta
from unittest.mock import patch

import core


class CoreTests(unittest.TestCase):
    def setUp(self):
        self.conn = core.connect(":memory:")  # fresh in-memory DB per test
        core.add_asset(self.conn, "PDU-A", "PDU A", "DC1", "R12", "A")
        core.add_asset(self.conn, "PDU-B", "PDU B", "DC1", "R12", "B")
        core.add_asset(self.conn, "PDU-C", "PDU C", "DC1", "R30", "B")  # other rack

    def tearDown(self):
        self.conn.close()

    # helpers
    def new(self, asset=None, priority="medium"):
        return core.create_ticket(self.conn, "Test ticket", "hardware_fault",
                                  priority, asset_tag=asset)

    def start(self, tid, **kwargs):
        core.assign_ticket(self.conn, tid, "maria")
        core.change_status(self.conn, tid, "in_progress", **kwargs)

    def status(self, tid):
        return core.get_ticket(self.conn, tid)["status"]

    # ----- creating tickets -----
    def test_new_ticket_defaults_and_sla_due_date(self):
        t = core.get_ticket(self.conn, self.new(priority="critical"))
        self.assertEqual(t["status"], "open")
        due = datetime.fromisoformat(t["due"]) - datetime.fromisoformat(t["created"])
        self.assertEqual(due, timedelta(hours=4))

    def test_bad_input_is_rejected(self):
        bad = [dict(title="  ", type_="install"),
               dict(title="x", type_="nope"),
               dict(title="x", type_="install", priority="urgent"),
               dict(title="x", type_="install", feed="C")]
        for kwargs in bad:
            with self.subTest(kwargs=kwargs), self.assertRaises(core.TicketError):
                core.create_ticket(self.conn, **kwargs)

    # ----- linking to assets -----
    def test_linked_asset_fills_in_location(self):
        t = core.get_ticket(self.conn, self.new(asset="PDU-B"))
        self.assertEqual((t["site"], t["rack"], t["feed"]), ("DC1", "R12", "B"))

    def test_unknown_asset_is_rejected(self):
        with self.assertRaises(core.TicketError):
            self.new(asset="NOPE")

    def test_duplicate_asset_is_rejected(self):
        with self.assertRaises(core.TicketError):
            core.add_asset(self.conn, "PDU-A", "Again")

    def test_asset_counts_only_unresolved_tickets(self):
        self.new(asset="PDU-A")
        counts = {a["asset_tag"]: a["active_tickets"] for a in core.list_assets(self.conn)}
        self.assertEqual(counts["PDU-A"], 1)
        self.assertEqual(counts["PDU-B"], 0)

    # ----- workflow -----
    def test_illegal_transition_is_blocked(self):
        tid = self.new()
        with self.assertRaisesRegex(core.TicketError, "open -> resolved"):
            core.change_status(self.conn, tid, "resolved")

    def test_assigning_an_open_ticket_moves_it_to_assigned(self):
        tid = self.new()
        core.assign_ticket(self.conn, tid, "maria")
        self.assertEqual(self.status(tid), "assigned")

    def test_history_records_every_change_in_order(self):
        tid = self.new()
        core.assign_ticket(self.conn, tid, "maria")
        core.add_comment(self.conn, tid, "hi")
        notes = [h["note"] for h in core.get_history(self.conn, tid)]
        self.assertEqual(notes, ["Ticket created", "Assigned to maria",
                                 "Status: open -> assigned", "Comment: hi"])

    def test_list_sorts_critical_first_and_filters(self):
        self.new(priority="low")
        crit = self.new(priority="critical")
        self.assertEqual(core.list_tickets(self.conn)[0]["id"], crit)
        self.assertEqual(len(core.list_tickets(self.conn, priority="low")), 1)

    # ----- redundancy conflict detection -----
    def test_opposite_feed_same_rack_blocks_start(self):
        a, b = self.new("PDU-A"), self.new("PDU-B")
        self.start(a)
        core.assign_ticket(self.conn, b, "devon")
        with self.assertRaises(core.RedundancyConflict) as ctx:
            core.change_status(self.conn, b, "in_progress")
        self.assertEqual([c["id"] for c in ctx.exception.conflicts], [a])
        self.assertEqual(self.status(b), "assigned")  # nothing changed

    def test_override_with_reason_allows_start_and_is_logged(self):
        a, b = self.new("PDU-A"), self.new("PDU-B")
        self.start(a)
        self.start(b, override_reason="Feed A already isolated, approved by shift lead")
        self.assertEqual(self.status(b), "in_progress")
        notes = [h["note"] for h in core.get_history(self.conn, b)]
        self.assertTrue(any("REDUNDANCY OVERRIDE" in n for n in notes))

    def test_blank_override_reason_does_not_count(self):
        a, b = self.new("PDU-A"), self.new("PDU-B")
        self.start(a)
        with self.assertRaises(core.RedundancyConflict):
            self.start(b, override_reason="   ")

    def test_no_conflict_on_same_feed(self):
        a = self.new("PDU-A")
        a2 = core.create_ticket(self.conn, "Second A-side job", "cabling",
                                site="DC1", rack="R12", feed="A")
        self.start(a)
        self.start(a2)  # must not raise
        self.assertEqual(self.status(a2), "in_progress")

    def test_no_conflict_on_different_rack(self):
        a, c = self.new("PDU-A"), self.new("PDU-C")
        self.start(a)
        self.start(c)  # PDU-C is feed B but in rack R30
        self.assertEqual(self.status(c), "in_progress")

    def test_ticket_without_location_never_conflicts(self):
        a, plain = self.new("PDU-A"), self.new()
        self.start(a)
        self.start(plain)
        self.assertEqual(self.status(plain), "in_progress")

    # ----- SLA -----
    def test_sla_states_while_open(self):
        t0 = datetime(2026, 1, 1, 12)
        with patch.object(core, "now", return_value=t0):
            t = core.get_ticket(self.conn, self.new(priority="critical"))  # due 16:00
        cases = [(1, "on_track"), (3.5, "at_risk"), (5, "breached")]
        for hours, expected in cases:
            with self.subTest(hours=hours):
                self.assertEqual(core.sla_state(t, at=t0 + timedelta(hours=hours)), expected)

    def test_resolved_ticket_is_met_or_breached(self):
        t0 = datetime(2026, 1, 1, 12)
        for hours, expected in [(2, "met"), (6, "breached")]:
            with self.subTest(hours=hours):
                with patch.object(core, "now", return_value=t0):
                    tid = self.new(priority="critical")
                    self.start(tid)
                with patch.object(core, "now", return_value=t0 + timedelta(hours=hours)):
                    core.change_status(self.conn, tid, "resolved")
                self.assertEqual(core.sla_state(core.get_ticket(self.conn, tid)), expected)


if __name__ == "__main__":
    unittest.main()
