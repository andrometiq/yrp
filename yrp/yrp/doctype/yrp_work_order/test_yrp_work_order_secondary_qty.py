from contextlib import nullcontext
from unittest.mock import patch

import frappe
from frappe.tests.utils import FrappeTestCase

from yrp.yrp.doctype.yrp_item.yrp_item import get_parent_item
from yrp.yrp.doctype.yrp_purchase_invoice.test_yrp_purchase_invoice import _work_order_for_invoice
from yrp.yrp.doctype.yrp_work_order.yrp_work_order import (
	get_secondary_quantity_rows,
	update_secondary_quantities,
)


def _secondary_uom():
	return frappe.db.get_value(
		"UOM", {"name": ["in", ["Nos", "Roll", "Piece"]]}, "name"
	) or frappe.db.get_value("UOM", {}, "name")


def _host_validator():
	if "essdee_yrp" in frappe.get_installed_apps():
		return patch("essdee_yrp.work_order_hooks.validate_lot_process_selection")
	return nullcontext()


class TestWorkOrderSecondaryQty(FrappeTestCase):
	def setUp(self):
		self.wo = _work_order_for_invoice(qty=4)
		self.uom = _secondary_uom()
		parent_item = get_parent_item(self.wo.receivables[0].item_variant)
		frappe.db.set_value("Item", parent_item, "secondary_unit_of_measure", self.uom)
		frappe.clear_document_cache("Item", parent_item)

	def quantities(self, doc, deliverable, receivable):
		return {doc.deliverables[0].name: deliverable, doc.receivables[0].name: receivable}

	def test_secondary_qty_saves_on_draft(self):
		draft = frappe.copy_doc(self.wo)
		draft.docstatus = 0
		with _host_validator():
			draft.insert(ignore_permissions=True)
			update_secondary_quantities(draft.name, self.quantities(draft, 2.5, 3))

		draft.reload()
		self.assertEqual(draft.docstatus, 0)
		self.assertEqual(draft.deliverables[0].secondary_qty, 2.5)
		self.assertEqual(draft.receivables[0].secondary_qty, 3)
		self.assertEqual(draft.receivables[0].secondary_uom, self.uom)

	def test_secondary_qty_editable_after_submit_and_again(self):
		update_secondary_quantities(self.wo.name, self.quantities(self.wo, 7, 9))
		update_secondary_quantities(self.wo.name, self.quantities(self.wo, 1.5, 0))

		self.wo.reload()
		self.assertEqual(self.wo.docstatus, 1)
		self.assertEqual(self.wo.deliverables[0].secondary_qty, 1.5)
		self.assertEqual(self.wo.receivables[0].secondary_qty, 0)
		self.assertEqual(self.wo.receivables[0].qty, 4)

	def test_secondary_rows_list_both_tables(self):
		update_secondary_quantities(self.wo.name, self.quantities(self.wo, 6, 8))

		rows = get_secondary_quantity_rows(self.wo.name)

		by_name = {row["name"]: row for row in rows}
		self.assertEqual(by_name[self.wo.deliverables[0].name]["table"], "deliverables")
		self.assertEqual(by_name[self.wo.receivables[0].name]["secondary_qty"], 8)
		self.assertEqual(by_name[self.wo.receivables[0].name]["secondary_uom"], self.uom)

	def test_only_secondary_qty_changes_after_submit(self):
		self.wo.reload()
		self.wo.receivables[0].comments = "changed after submit"
		with self.assertRaises(frappe.UpdateAfterSubmitError):
			self.wo.save()

	def test_unknown_row_is_rejected(self):
		with self.assertRaises(frappe.ValidationError):
			update_secondary_quantities(self.wo.name, {"not-a-row": 1})

	def test_cancelled_work_order_is_rejected(self):
		self.wo.reload()
		self.wo.flags.ignore_links = True
		self.wo.cancel()
		with self.assertRaises(frappe.ValidationError):
			update_secondary_quantities(self.wo.name, self.quantities(self.wo, 1, 1))
