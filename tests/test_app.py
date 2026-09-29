import os
import tempfile
import unittest

from app import create_app


class WebTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        app = create_app(os.path.join(tmp.name, "test.db"))
        app.config["TESTING"] = True
        self.client = app.test_client()

    def make_ticket(self, title, feed):
        r = self.client.post("/new", data={
            "title": title, "type": "hardware_fault", "priority": "high",
            "site": "DC1", "rack": "R12", "feed": feed})
        self.assertEqual(r.status_code, 302)
        return int(r.headers["Location"].rsplit("/", 1)[1])

    def test_dashboard_loads(self):
        r = self.client.get("/")
        self.assertEqual(r.status_code, 200)
        self.assertIn(b"No tickets found", r.data)

    def test_create_ticket_shows_on_dashboard(self):
        self.make_ticket("Dead fan tray", "A")
        self.assertIn(b"Dead fan tray", self.client.get("/").data)

    def test_blank_title_shows_error(self):
        r = self.client.post("/new", data={"title": " ", "type": "install", "priority": "low"})
        self.assertIn(b"Title is required", r.data)

    def test_missing_ticket_is_404(self):
        self.assertEqual(self.client.get("/ticket/999").status_code, 404)

    def test_asset_can_be_added_and_listed(self):
        r = self.client.post("/assets", data={"asset_tag": "PDU-1", "name": "Rack PDU",
                                              "site": "DC1", "rack": "R1", "feed": "A"})
        self.assertIn(b"PDU-1", r.data)

    def test_redundancy_conflict_and_override_through_the_ui(self):
        a, b = self.make_ticket("PSU on feed A", "A"), self.make_ticket("PSU on feed B", "B")
        for tid in (a, b):
            self.client.post(f"/ticket/{tid}/assign", data={"tech": "maria"})
        self.client.post(f"/ticket/{a}/status", data={"new_status": "in_progress"})

        # The warning banner appears before anyone tries to start B
        self.assertIn(b"Redundancy risk", self.client.get(f"/ticket/{b}").data)

        blocked = self.client.post(f"/ticket/{b}/status", data={"new_status": "in_progress"},
                                   follow_redirects=True)
        self.assertIn(b"Redundancy conflict", blocked.data)

        allowed = self.client.post(f"/ticket/{b}/status", follow_redirects=True, data={
            "new_status": "in_progress", "override_reason": "approved by shift lead"})
        self.assertIn(b"Status updated", allowed.data)
        self.assertIn(b"REDUNDANCY OVERRIDE", allowed.data)


if __name__ == "__main__":
    unittest.main()
