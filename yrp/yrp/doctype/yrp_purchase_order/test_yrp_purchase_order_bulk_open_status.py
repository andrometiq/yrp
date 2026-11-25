from unittest.mock import patch

import frappe
from frappe.tests.utils import FrappeTestCase

from yrp.yrp.doctype.yrp_goods_received_note.test_purchase_order_grn import _purchase_order, _warehouse
from yrp.yrp.doctype.yrp_purchase_order.yrp_purchase_order import (
	close_or_open_purchase_orders,
	close_purchase_order,
	get_close_permission,
)


class TestBulkPurchaseOrderOpenStatus(FrappeTestCase):
	"""Bulk Close/Reopen: per-PO handling with skip reasons."""

	def setUp(self):
		self.warehouse = _warehouse(f"_T_Bulk_Close_{frappe.generate_hash(length=6)}")

	def tearDown(self):
		frappe.set_user("Administrator")

	def _status(self, name):
		return frappe.db.get_value("Purchase Order", name, ["open_status", "status"], as_dict=True)

	def test_bulk_close_closes_open_and_skips_ineligible_rows(self):
		first = _purchase_order(qty=5, warehouse=self.warehouse)
		second = _purchase_order(qty=5, warehouse=self.warehouse)
		closed = _purchase_order(qty=5, warehouse=self.warehouse)
		close_purchase_order(closed.name)

		result = close_or_open_purchase_orders(
			[first.name, second.name, closed.name, "PO-MISSING-X"], close=True
		)

		self.assertEqual(result["done"], [first.name, second.name])
		self.assertEqual(
			{row["name"]: row["reason"] for row in result["skipped"]},
			{closed.name: "Already closed.", "PO-MISSING-X": "Purchase Order not found."},
		)
		for name in (first.name, second.name):
			self.assertEqual(self._status(name), {"open_status": "Close", "status": "Closed"})

	def test_bulk_close_skips_unmanaged_and_unsubmitted(self):
		po = _purchase_order(qty=5, warehouse=self.warehouse)
		unmanaged = _purchase_order(qty=5, warehouse=self.warehouse)
		frappe.db.set_value("Purchase Order", unmanaged.name, "is_yrp_managed", 0)
		cancelled = _purchase_order(qty=5, warehouse=self.warehouse)
		cancelled.cancel()

		result = close_or_open_purchase_orders(
			frappe.as_json([po.name, unmanaged.name, cancelled.name]), "true"
		)

		self.assertEqual(result["done"], [po.name])
		self.assertEqual(
			{row["name"]: row["reason"] for row in result["skipped"]},
			{unmanaged.name: "Not YRP managed.", cancelled.name: "Not submitted."},
		)

	def test_bulk_reopen_reopens_closed_and_skips_open(self):
		closed = _purchase_order(qty=5, warehouse=self.warehouse)
		close_purchase_order(closed.name)
		still_open = _purchase_order(qty=5, warehouse=self.warehouse)

		result = close_or_open_purchase_orders([closed.name, still_open.name], close=False)

		self.assertEqual(result["done"], [closed.name])
		self.assertEqual(result["skipped"], [{"name": still_open.name, "reason": "Already open."}])
		self.assertEqual(self._status(closed.name)["open_status"], "Open")

	def test_one_failure_does_not_roll_back_the_others(self):
		failing = _purchase_order(qty=5, warehouse=self.warehouse)
		ok = _purchase_order(qty=5, warehouse=self.warehouse)
		original = frappe.get_doc

		def get_doc(*args, **kwargs):
			doc = original(*args, **kwargs)
			if getattr(doc, "name", None) == failing.name:
				doc.set_open_status = lambda close=True: frappe.throw("Boom")
			return doc

		with patch("frappe.get_doc", side_effect=get_doc):
			result = close_or_open_purchase_orders([failing.name, ok.name], close=True)

		self.assertEqual(result["done"], [ok.name])
		self.assertEqual(result["skipped"], [{"name": failing.name, "reason": "Boom"}])
		self.assertEqual(self._status(failing.name)["open_status"], "Open")
		self.assertEqual(self._status(ok.name)["open_status"], "Close")

	def test_only_purchase_order_managers_may_bulk_close(self):
		po = _purchase_order(qty=5, warehouse=self.warehouse)
		user = _user_without_manager_roles()
		frappe.set_user(user)
		self.assertFalse(get_close_permission()["is_close_manager"])
		with self.assertRaises(frappe.PermissionError):
			close_or_open_purchase_orders([po.name], close=True)
		frappe.set_user("Administrator")
		self.assertTrue(get_close_permission()["is_close_manager"])


def _user_without_manager_roles():
	email = "_test_po_bulk_close_user@example.com"
	if not frappe.db.exists("User", email):
		user = frappe.get_doc({"doctype": "User", "email": email, "first_name": "PO Bulk"})
		user.append("roles", {"role": "Purchase User"})
		user.insert(ignore_permissions=True)
	return email
