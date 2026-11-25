import frappe
from frappe.tests import IntegrationTestCase


class TestYRPWorkOrderCorrection(IntegrationTestCase):
	def test_merch_user_can_create_and_submit_but_not_cancel(self):
		perms = [
			row for row in frappe.get_meta("YRP Work Order Correction").permissions if row.role == "Merch User"
		]
		self.assertEqual(len(perms), 1)
		granted = {key for key in ("read", "write", "create", "submit", "cancel", "amend", "delete") if perms[0].get(key)}
		self.assertEqual(granted, {"read", "write", "create", "submit"})
