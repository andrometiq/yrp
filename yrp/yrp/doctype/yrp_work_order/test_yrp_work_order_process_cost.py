from unittest.mock import patch

import frappe
from frappe.tests.utils import FrappeTestCase

from yrp.yrp.doctype.yrp_process_cost.yrp_process_cost import is_flat_process_cost
from yrp.yrp.doctype.yrp_work_order.yrp_work_order import WorkOrder, get_process_cost_rate

MODULE = "yrp.yrp.doctype.yrp_work_order.yrp_work_order"


class TestWorkOrderProcessCost(FrappeTestCase):
	def test_finished_item_attribute_rate_is_spread_over_receivable_units(self):
		work_order = _work_order(
			calculated_items=[("GARMENT-BLACK", 10)],
			receivables=[("PANEL-FRONT", 10), ("PANEL-SLEEVE", 20)],
		)
		process_cost = _process_cost("Colour", [("Black", 6)])
		attributes = {
			"GARMENT-BLACK": {"Colour": "Black"},
			"PANEL-FRONT": {"Colour": "Black", "Panel": "Front"},
			"PANEL-SLEEVE": {"Colour": "Black", "Panel": "Sleeve"},
		}

		with patch(
			"yrp.yrp.doctype.yrp_work_order.yrp_work_order.get_variant_attributes",
			side_effect=lambda item_variant: attributes[item_variant],
		):
			work_order.apply_receivable_process_costs(process_cost)

		self.assertEqual([row.cost for row in work_order.receivables], [2, 2])
		self.assertEqual([row.total_cost for row in work_order.receivables], [20, 40])
		self.assertEqual(sum(row.total_cost for row in work_order.receivables), 60)

	def test_output_only_attribute_keeps_direct_receivable_rate(self):
		work_order = _work_order(
			calculated_items=[("GARMENT-BLACK", 10)],
			receivables=[("PANEL-FRONT", 10), ("PANEL-SLEEVE", 20)],
		)
		process_cost = _process_cost("Panel", [("Front", 1), ("Sleeve", 0.5)])
		attributes = {
			"GARMENT-BLACK": {"Colour": "Black"},
			"PANEL-FRONT": {"Colour": "Black", "Panel": "Front"},
			"PANEL-SLEEVE": {"Colour": "Black", "Panel": "Sleeve"},
		}

		with patch(
			"yrp.yrp.doctype.yrp_work_order.yrp_work_order.get_variant_attributes",
			side_effect=lambda item_variant: attributes[item_variant],
		):
			work_order.apply_receivable_process_costs(process_cost)

		self.assertEqual([row.cost for row in work_order.receivables], [1, 0.5])
		self.assertEqual([row.total_cost for row in work_order.receivables], [10, 10])

	def test_other_item_receivables_get_no_process_cost(self):
		work_order = _work_order(
			calculated_items=[("GARMENT-BLACK", 10)],
			receivables=[("PANEL-FRONT", 10), ("CLOTH-BLACK", 2.5)],
		)
		process_cost = frappe._dict(
			name="TEST-PROCESS-COST",
			item="GARMENT",
			depends_on_attribute=0,
			attribute=None,
			process_cost_values=[frappe._dict(attribute_value=None, min_order_qty=0, price=2)],
		)
		# Item names are case-insensitive, so a variant may store its parent in another case.
		items = {"GARMENT-BLACK": "GARMENT", "PANEL-FRONT": "Garment", "CLOTH-BLACK": "CLOTH"}

		with (
			patch(MODULE + ".get_variant_attributes", return_value={}),
			patch(
				MODULE + ".get_variant_items", side_effect=lambda variants: {v: items[v] for v in variants}
			),
		):
			work_order.apply_receivable_process_costs(process_cost)

		self.assertEqual([row.cost for row in work_order.receivables], [2, 0])
		self.assertEqual([row.total_cost for row in work_order.receivables], [20, 0])
		self.assertEqual([row.process_cost for row in work_order.receivables], ["TEST-PROCESS-COST"] * 2)

	def test_dependent_cost_without_attribute_rates_like_flat_cost(self):
		def costs(depends_on_attribute, attribute, attribute_value):
			work_order = _work_order(
				calculated_items=[("GARMENT-BLACK", 10)],
				receivables=[("PANEL-FRONT", 10), ("PANEL-SLEEVE", 20)],
			)
			process_cost = frappe._dict(
				name="TEST-PROCESS-COST",
				depends_on_attribute=depends_on_attribute,
				attribute=attribute,
				process_cost_values=[
					frappe._dict(attribute_value=attribute_value, min_order_qty=0, price=2)
				],
			)
			with patch(MODULE + ".get_variant_attributes", return_value={"Colour": "Black"}):
				self.assertEqual(get_process_cost_rate("GARMENT-BLACK", 10, process_cost), 2)
				work_order.apply_receivable_process_costs(process_cost)
			return [(row.cost, row.total_cost) for row in work_order.receivables]

		flat = costs(0, None, None)
		self.assertEqual(flat, [(2, 20), (2, 40)])
		for attribute, attribute_value in ((None, None), ("", ""), (None, "")):
			self.assertEqual(costs(1, attribute, attribute_value), flat)
		self.assertTrue(is_flat_process_cost(frappe._dict(depends_on_attribute=1, attribute="")))
		self.assertFalse(is_flat_process_cost(frappe._dict(depends_on_attribute=1, attribute="Colour")))


def _work_order(calculated_items, receivables):
	work_order = WorkOrder({"doctype": "YRP Work Order"})
	for item_variant, quantity in calculated_items:
		work_order.append(
			"work_order_calculated_items",
			{"item_variant": item_variant, "quantity": quantity, "set_combination": "{}"},
		)
	for item_variant, quantity in receivables:
		work_order.append(
			"receivables",
			{"item_variant": item_variant, "qty": quantity, "set_combination": "{}"},
		)
	return work_order


def _process_cost(attribute, values):
	return frappe._dict(
		name="TEST-PROCESS-COST",
		depends_on_attribute=1,
		attribute=attribute,
		process_cost_values=[
			frappe._dict(attribute_value=value, min_order_qty=0, price=price) for value, price in values
		],
	)
