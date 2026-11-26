"""`yrp_partner_read_references` hooks widen reads only; fictional records roll back."""

import unittest
from unittest.mock import patch

import frappe

from yrp.yrp_partner import customer_access as access
from yrp.yrp_partner.permissions import _condition
from yrp.yrp_retail.tests.fixtures import customer, sales_person, set_member

HOOK = "yrp_partner_read_references"
EXTRA = {}


def extra_references(doctype, user):
	"""Test hook: one extra record per DocType, as a subquery."""
	name = EXTRA.get(doctype)
	return f"SELECT {frappe.db.escape(name)}" if name else None


class TestReadReferences(unittest.TestCase):
	def setUp(self):
		point = "read_references_" + frappe.generate_hash(length=10)
		frappe.db.savepoint(point)
		self.addCleanup(frappe.db.rollback, save_point=point)
		self.addCleanup(frappe.set_user, frappe.session.user)
		self.addCleanup(EXTRA.clear)
		frappe.set_user("Administrator")
		self.own_customer, self.team_customer = customer(), customer()
		self.own_person = sales_person(self.own_customer)
		self.team_person = sales_person(self.team_customer)
		self.user = frappe.get_doc({
			"doctype": "User", "email": "read-references-" + frappe.generate_hash(length=12) + "@example.invalid",
			"first_name": "Fictional Read Reference", "send_welcome_email": 0,
			"roles": [{"role": "YRP Partner"}, {"role": "YRP Sales Person"}],
		}).insert().name
		set_member(self.user, "Sales Person", self.own_person.name)
		EXTRA.update({"Sales Person": self.team_person.name, "Customer": self.team_customer.name})

	def visible(self, doctype):
		condition = _condition(doctype, self.user, "scope", include_customer_scope=False)
		if doctype in {"Customer", "YRP Retailer"}:
			condition = access._customer_query_condition(doctype, self.user, "scope")
		return set(frappe.db.sql(f"SELECT scope.name FROM `tab{doctype}` scope WHERE {condition}", pluck=True))

	def with_hook(self):
		original = frappe.get_hooks

		def get_hooks(hook=None, *args, **kwargs):
			return [f"{__name__}.extra_references"] if hook == HOOK else original(hook, *args, **kwargs)

		return patch("frappe.get_hooks", side_effect=get_hooks)

	def test_without_hook_reads_only_own_records(self):
		self.assertEqual(self.visible("Sales Person") & {self.own_person.name, self.team_person.name}, {self.own_person.name})
		self.assertIn(self.own_customer.name, self.visible("Customer"))
		self.assertNotIn(self.team_customer.name, self.visible("Customer"))

	def test_hook_adds_sales_person_and_customer_reads(self):
		with self.with_hook():
			people, customers = self.visible("Sales Person"), self.visible("Customer")
		self.assertTrue({self.own_person.name, self.team_person.name} <= people)
		self.assertTrue({self.own_customer.name, self.team_customer.name} <= customers)

	def test_hook_adds_retailers_of_extra_customers(self):
		retailer = frappe.get_doc({
			"doctype": "YRP Retailer", "name": "Fictional Team Retailer " + frappe.generate_hash(length=8),
			"retailer_name": "Fictional Team Retailer " + frappe.generate_hash(length=8),
			"customer": self.team_customer.name,
		})
		retailer.db_insert()
		self.assertNotIn(retailer.name, self.visible("YRP Retailer"))
		with self.with_hook():
			self.assertIn(retailer.name, self.visible("YRP Retailer"))

	def test_hook_does_not_widen_sales_orders(self):
		with self.with_hook():
			condition = access._customer_query_condition("YRP Sales Order", self.user, "scope")
		self.assertNotIn(frappe.db.escape(self.team_customer.name), condition)

	def test_hook_customers_do_not_widen_records_linked_to_them(self):
		with self.with_hook():
			condition = _condition("YRP Visit", self.user, "scope", include_customer_scope=False)
		self.assertNotIn(frappe.db.escape(self.team_customer.name), condition)
