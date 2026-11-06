"""Delivering an over-reserved bucket: reservations may exceed actual stock, a delivery may still ship."""

from unittest import TestCase
from unittest.mock import patch

import frappe

from yrp.stock.stock_ledger import is_delivery_note_issue
from yrp.yrp_stock.doctype.yrp_bin.yrp_bin import YRPBin


def _issue_sle(qty=-25, voucher_type="YRP Stock Entry"):
	return frappe._dict(voucher_type=voucher_type, voucher_no="STE-1", qty=qty)


class TestDeliveryNoteIssue(TestCase):
	def test_material_issue_against_delivery_note_is_detected(self):
		with patch("frappe.db.get_value", return_value=("Material Issue", "YRP Delivery Note")):
			self.assertTrue(is_delivery_note_issue(_issue_sle()))

	def test_other_stock_outs_keep_the_reservation_floor(self):
		with patch("frappe.db.get_value", return_value=("Material Issue", None)):
			self.assertFalse(is_delivery_note_issue(_issue_sle()))
		with patch("frappe.db.get_value", return_value=("Material Transfer", "YRP Delivery Note")):
			self.assertFalse(is_delivery_note_issue(_issue_sle()))
		self.assertFalse(is_delivery_note_issue(_issue_sle(qty=25)))
		self.assertFalse(is_delivery_note_issue(_issue_sle(voucher_type="YRP Stock Reconciliation")))


def _bin(actual, stored_reserved):
	return YRPBin(
		{
			"doctype": "YRP Bin",
			"item_code": "ITEM-1",
			"warehouse": "WH-1",
			"actual_qty": actual,
			"reserved_qty": stored_reserved,
		}
	)


class TestBinReservedQty(TestCase):
	def _refresh(self, doc, reserved):
		with (
			patch("yrp.yrp_stock.doctype.yrp_bin.yrp_bin.get_stock_dimensions", return_value=[]),
			patch("yrp.stock.utils.get_sre_reserved_qty", return_value=reserved),
			patch.object(YRPBin, "db_set") as db_set,
		):
			result = doc.update_reserved_stock()
		return result, db_set

	def test_delivery_shrinking_an_over_reserved_bucket_is_allowed(self):
		result, db_set = self._refresh(_bin(actual=150, stored_reserved=415), reserved=390)
		self.assertEqual(result, 390)
		db_set.assert_called_once_with("reserved_qty", 390, update_modified=False)

	def test_growing_reservation_beyond_stock_is_still_blocked(self):
		with self.assertRaises(frappe.ValidationError):
			self._refresh(_bin(actual=150, stored_reserved=140), reserved=160)

	def test_negative_reservation_is_blocked(self):
		with self.assertRaises(frappe.ValidationError):
			self._refresh(_bin(actual=150, stored_reserved=10), reserved=-5)
