"""Run: python -m unittest -v   (needs only Flask)"""
import unittest

import mvp_app as m


class MvpTests(unittest.TestCase):
    def setUp(self):
        m.seed()
        self.c = m.app.test_client()
        self.c.get("/as/ops")

    def as_user(self, u):
        self.c.get(f"/as/{u}")

    def run_detect(self):
        return self.c.post("/run", follow_redirects=True).get_data(as_text=True)

    def test_detection_creates_seven(self):
        self.assertIn("7 new exception(s)", self.run_detect())
        self.assertEqual({e["exception_type"] for e in m.STATE["exceptions"]}, set(m.TYPES))

    def test_rerun_adds_nothing(self):
        self.run_detect()
        self.assertIn("0 new exception(s)", self.run_detect())

    def test_priorities(self):
        self.run_detect()
        p = {e["dedupe_key"]: e["priority"] for e in m.STATE["exceptions"]}
        self.assertEqual((p["low:SKU-106"], p["low:SKU-103"], p["delay:O-2001"], p["delay:O-2003"]),
                         ("High", "Medium", "High", "Medium"))

    def test_impact_names_affected_orders(self):
        self.run_detect()
        e = next(x for x in m.STATE["exceptions"] if x["dedupe_key"] == "low:SKU-103")
        self.assertIn("O-2008", e["impact_description"])
        self.assertIn("110 units", e["impact_description"])

    def test_role_visibility(self):
        self.run_detect()
        for user, expected in (("ops", 7), ("inventory", 3), ("service", 4), ("admin", 0)):
            self.assertEqual(len(m.listing(m.USERS[user][1])), expected, user)

    def test_only_ops_can_run_detection(self):
        self.as_user("service")
        self.assertEqual(self.c.post("/run").status_code, 403)

    def test_hidden_exception_is_404(self):
        self.run_detect()
        self.as_user("inventory")
        order_exc = next(e for e in m.STATE["exceptions"] if e["related_order_id"])
        self.assertEqual(self.c.get(f"/exceptions/{order_exc['id']}").status_code, 404)

    def test_full_lifecycle_through_forms(self):
        self.run_detect()
        url = "/exceptions/1/change"
        self.c.post(url, data={"to": "Assigned", "owner": "inventory"})
        self.c.post(url, data={"to": "In Progress"})
        self.c.post(url, data={"to": "Resolved", "note": "Reorder placed"})
        self.assertEqual(m.STATE["exceptions"][0]["status"], "Resolved")
        self.assertEqual(len(m.STATE["exceptions"][0]["follow_ups"]), 4)

    def test_rules_are_enforced(self):
        self.run_detect()
        post = lambda **d: self.c.post("/exceptions/1/change", data=d, follow_redirects=True).get_data(as_text=True)
        self.assertIn("Cannot move from Open to Resolved.", post(to="Resolved", note="x"))
        self.assertIn("Choose a responsible user first.", post(to="Assigned"))
        post(to="Assigned", owner="inventory")
        post(to="In Progress")
        self.assertIn("Add resolution notes", post(to="Resolved", note=" "))

    def test_escalation_goes_to_ops(self):
        self.run_detect()
        self.c.post("/exceptions/1/change", data={"to": "Escalated"})
        self.assertEqual(m.STATE["exceptions"][0]["responsible_user"], "ops")

    def test_editing_stock_creates_new_exception(self):
        self.run_detect()
        self.c.post("/data/stock", data={"sku": "SKU-104", "qty": "5"})
        self.assertIn("1 new exception(s)", self.run_detect())

    def test_bad_stock_input_is_refused(self):
        page = self.c.post("/data/stock", data={"sku": "SKU-104", "qty": "-3"}, follow_redirects=True)
        self.assertIn("whole number", page.get_data(as_text=True))

    def test_resolved_problem_that_persists_opens_again(self):
        self.run_detect()
        url = "/exceptions/1/change"
        self.c.post(url, data={"to": "Assigned", "owner": "inventory"})
        self.c.post(url, data={"to": "In Progress"})
        self.c.post(url, data={"to": "Resolved", "note": "done"})
        self.assertIn("1 new exception(s)", self.run_detect())

    def test_reset_restores_detected_exceptions(self):
        self.c.post("/reset")
        self.assertEqual(len(m.STATE["exceptions"]), 7)
        for user, expected in (("inventory", 3), ("service", 4)):
            self.assertEqual(len(m.listing(m.USERS[user][1])), expected)

    def test_admin_empty_page_explains_why(self):
        self.run_detect()
        self.as_user("admin")
        self.assertIn("manages accounts", self.c.get("/").get_data(as_text=True))

    def test_pages_render(self):
        self.run_detect()
        for path in ("/", "/data", "/exceptions/1", "/?status=Open&type=Low+Inventory"):
            self.assertEqual(self.c.get(path).status_code, 200, path)
        self.assertEqual(self.c.get("/?status=Nope").status_code, 400)


if __name__ == "__main__":
    unittest.main()
