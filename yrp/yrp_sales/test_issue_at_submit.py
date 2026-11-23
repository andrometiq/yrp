from unittest import TestCase
from unittest.mock import MagicMock, patch

import frappe

from yrp.yrp_sales import invoicing


class TestIssueAtSubmitTime(TestCase):
	def test_issue_entry_is_stamped_now_not_at_the_invoice_time(self):
		entry = MagicMock()
		row = frappe._dict(name="row-1")
		with (
			patch.object(invoicing.frappe, "get_doc", return_value=entry) as get_doc,
			patch.object(invoicing, "get_issue_row", return_value={}),
			patch.object(invoicing, "validate_issued_qty"),
		):
			invoicing.make_issue_entry(frappe._dict(name="DN-1"), "WH-1", [(row, 5)])
		values = get_doc.call_args.args[0]
		self.assertNotIn("posting_date", values)
		self.assertNotIn("edit_posting_date_and_time", values)
		entry.submit.assert_called_once()
