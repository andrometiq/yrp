import frappe
from frappe.tests import UnitTestCase

from yrp.stock.dimension_rename import escape_like, replace_dimension_key, replace_dimension_value


class TestDimensionRename(UnitTestCase):
	def test_value_replaced_only_under_dimension_key_and_exact(self):
		data = {"lot": "L1", "item": "L1", "rows": [{"lot": "L1"}, {"lot": "L1-2"}]}
		self.assertEqual(
			replace_dimension_value(data, "lot", "L1", "L9"),
			{"lot": "L9", "item": "L1", "rows": [{"lot": "L9"}, {"lot": "L1-2"}]},
		)

	def test_keys_renamed_at_any_depth(self):
		data = {"S": {"Old": 2, "Accepted": 1}, "Old": 3}
		self.assertEqual(
			replace_dimension_key(data, "Old", "New"), {"S": {"New": 2, "Accepted": 1}, "New": 3}
		)

	def test_key_clash_fails_loudly(self):
		with self.assertRaises(frappe.ValidationError):
			replace_dimension_key({"Old": 1, "New": 2}, "Old", "New")

	def test_like_wildcards_escaped(self):
		self.assertEqual(escape_like('"A_1%"'), '"A\\_1\\%"')
