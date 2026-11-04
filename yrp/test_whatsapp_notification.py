# Copyright (c) 2026, Essdee and contributors
# For license information, please see license.txt

from unittest.mock import patch

import frappe
from frappe.tests import IntegrationTestCase

from yrp import whatsapp_notification
from yrp.yrp.doctype.yrp_goods_received_note.test_purchase_order_grn import _supplier
from yrp.yrp.doctype.yrp_supplier.test_yrp_supplier_notification import _contact_for

PREVIEW = "relay_core.document_actions.preview"
SEND = "relay_core.document_actions.send"
NUMBER = "919876543210"


class TestWhatsAppNotification(IntegrationTestCase):
	def setUp(self):
		if not whatsapp_notification.is_relay_installed():
			self.skipTest("relay_core is not installed on this site")
		from relay_core.hub_channels import choose

		self.channel = choose("WhatsApp")
		frappe.db.delete("Relay Document Action", {"document_type": ["in", ["Purchase Order", "Supplier"]]})

	def _approved_template(self):
		from relay_core.whatsapp_templates import store

		account = frappe.db.get_value("Relay Channel", self.channel, "account")
		row = {
			"id": "990000000000001",
			"name": whatsapp_notification.TEMPLATE_NAME,
			"language": whatsapp_notification.TEMPLATE_LANGUAGE,
			"category": "UTILITY",
			"status": "APPROVED",
		}
		store(account, [row])

	def _setup_purchase_order_action(self):
		self._approved_template()
		with patch.object(
			whatsapp_notification, "SUPPLIER_DOCUMENTS", {"Purchase Order": "transaction_date"}
		):
			name = whatsapp_notification.setup_supplier_document_actions(self.channel)[0]
		return frappe.get_doc("Relay Document Action", name)

	def _action(self, roles=()):
		"""An action on Supplier itself, so the tests need no purchase document."""
		self._approved_template()
		template = frappe.db.get_value(
			"Relay WhatsApp Template", {"template_name": whatsapp_notification.TEMPLATE_NAME}, "name"
		)
		return frappe.get_doc(
			{
				"doctype": "Relay Document Action",
				"title": "_T WA Supplier Action",
				"enabled": 1,
				"document_type": "Supplier",
				"show_button": 0,
				"business_field": "name",
				"business_doctype": "Supplier",
				"channel": self.channel,
				"template": template,
				"parameter_mapping": frappe.as_json(["supplier_name"]),
				"recipients": "Primary contact",
				"roles": [{"role": role} for role in roles],
			}
		).insert()

	def _supplier(self, mobile="98765 43210"):
		supplier = _supplier(f"_T WA Supplier {frappe.generate_hash(length=6)}")
		return frappe.get_doc("Supplier", supplier), _contact_for(supplier, mobile=mobile)

	def _user(self, email):
		if not frappe.db.exists("User", email):
			frappe.get_doc(
				{"doctype": "User", "email": email, "first_name": "WA Test", "send_welcome_email": 0}
			).insert(ignore_permissions=True)
		return email

	def _preview(self, action, contact):
		return {
			"action": action.name,
			"label": action.label,
			"template": whatsapp_notification.TEMPLATE_NAME,
			"text": "Hello Supplier",
			"file_name": "PO.pdf",
			"all_numbers": False,
			"recipients": [{"contact": contact.name, "name": contact.name, "numbers": [NUMBER]}],
		}

	def test_without_relay_no_doctype_can_send(self):
		self._action()
		with patch("frappe.get_installed_apps", return_value=["frappe", "yrp"]):
			self.assertEqual(whatsapp_notification.get_enabled_whatsapp_doctypes(), {"doctypes": {}})

	def test_document_action_enables_its_doctype(self):
		self._action()
		doctypes = whatsapp_notification.get_enabled_whatsapp_doctypes()["doctypes"]
		self.assertEqual(doctypes.get("Supplier"), "name")

	def test_action_roles_limit_who_can_send(self):
		self._action(roles=["Accounts Manager"])
		with patch("frappe.get_roles", return_value=["Purchase User"]):
			self.assertNotIn("Supplier", whatsapp_notification.get_enabled_whatsapp_doctypes()["doctypes"])

	def test_setup_needs_an_approved_template(self):
		with self.assertRaises(frappe.ValidationError):
			whatsapp_notification.setup_supplier_document_actions(self.channel)

	def test_setup_attaches_the_default_print_format(self):
		action = self._setup_purchase_order_action()
		self.assertEqual(action.print_format, frappe.get_meta("Purchase Order").default_print_format)
		self.assertEqual(
			frappe.parse_json(action.parameter_mapping), ["supplier_name", "name", "transaction_date"]
		)
		self.assertFalse(action.show_button)

	def test_context_lists_actions_and_supplier_numbers(self):
		action = self._action()
		po, contact = self._supplier()
		with patch(PREVIEW, return_value=self._preview(action, contact)):
			context = whatsapp_notification.get_whatsapp_context("Supplier", po.name)
		self.assertEqual(context["supplier"], po.name)
		self.assertEqual(context["numbers"], [NUMBER])
		self.assertEqual(context["templates"][0]["template_name"], action.name)
		self.assertEqual(context["templates"][0]["body_text"], "Hello Supplier")

	def test_context_without_action_throws(self):
		po, _contact = self._supplier()
		with self.assertRaises(frappe.ValidationError):
			whatsapp_notification.get_whatsapp_context("Supplier", po.name)

	def test_send_goes_to_the_chosen_contact_as_relay_user(self):
		action = self._action()
		po, contact = self._supplier()
		users = []

		def send(doctype, name, action_name, contacts, request_key):
			users.append(frappe.session.user)
			return {"results": [{"contact": contacts[0], "status": "Submitted"}]}

		with (
			patch(PREVIEW, return_value=self._preview(action, contact)),
			patch(SEND, side_effect=send) as mock,
		):
			first = whatsapp_notification.send_whatsapp_notification(
				"Supplier", po.name, action.name, mobile_no="98765 43210"
			)
			whatsapp_notification.send_whatsapp_notification("Supplier", po.name, action.name)
		self.assertTrue(first["ok"])
		self.assertEqual(mock.call_args_list[0].args[3], [contact.name])
		self.assertEqual(mock.call_args_list[0].args[4], mock.call_args_list[1].args[4])
		self.assertEqual(users, ["Administrator", "Administrator"])
		self.assertEqual(frappe.session.user, "Administrator")

	def test_request_key_is_per_user_and_per_dialog(self):
		action = self._action()
		po, contact = self._supplier()
		ok = {"results": [{"contact": contact.name, "status": "Submitted"}]}
		with (
			patch(PREVIEW, return_value=self._preview(action, contact)),
			patch(SEND, return_value=ok) as mock,
		):
			for request_key in ("dialog-1", "dialog-1", "dialog-2"):
				whatsapp_notification.send_whatsapp_notification(
					"Supplier", po.name, action.name, request_key=request_key
				)
			frappe.set_user(self._user("yrp-wa-other@example.com"))
			try:
				with (
					patch("frappe.has_permission", return_value=True),
					patch.object(whatsapp_notification, "get_action", return_value=action),
				):
					whatsapp_notification.send_whatsapp_notification(
						"Supplier", po.name, action.name, request_key="dialog-2"
					)
			finally:
				frappe.set_user("Administrator")
		keys = [call.args[4] for call in mock.call_args_list]
		self.assertEqual(keys[0], keys[1])
		self.assertEqual(len(set(keys)), 3)

	def test_send_rejects_a_number_outside_the_supplier_contacts(self):
		action = self._action()
		po, contact = self._supplier()
		with patch(PREVIEW, return_value=self._preview(action, contact)), patch(SEND) as mock:
			with self.assertRaises(frappe.ValidationError):
				whatsapp_notification.send_whatsapp_notification(
					"Supplier", po.name, action.name, mobile_no="9000000000"
				)
		mock.assert_not_called()

	def test_failed_send_returns_not_ok(self):
		action = self._action()
		po, contact = self._supplier()
		rejected = {"results": [{"contact": contact.name, "status": "Rejected", "error": "Outside window"}]}
		with patch(PREVIEW, return_value=self._preview(action, contact)), patch(SEND, return_value=rejected):
			result = whatsapp_notification.send_whatsapp_notification("Supplier", po.name, action.name)
		self.assertFalse(result["ok"])
		self.assertEqual(result["error"], "Outside window")

	def test_send_needs_write_permission(self):
		action = self._action()
		po, _contact = self._supplier()
		frappe.set_user(self._user("yrp-wa-no-access@example.com"))
		try:
			with patch(SEND) as mock, self.assertRaises(frappe.PermissionError):
				whatsapp_notification.send_whatsapp_notification("Supplier", po.name, action.name)
		finally:
			frappe.set_user("Administrator")
		mock.assert_not_called()

	def test_unknown_action_throws(self):
		self._action()
		po, _contact = self._supplier()
		with patch(SEND) as mock, self.assertRaises(frappe.ValidationError):
			whatsapp_notification.send_whatsapp_notification("Supplier", po.name, "Missing Action")
		mock.assert_not_called()

	def test_template_creation_is_for_system_managers(self):
		frappe.set_user("Guest")
		try:
			with self.assertRaises(frappe.PermissionError):
				whatsapp_notification.create_supplier_document_template()
		finally:
			frappe.set_user("Administrator")

	def test_template_creation_submits_a_pdf_header_template(self):
		calls = []

		def execute(operation, arguments, channel=None):
			calls.append((operation, arguments))
			return {"handle": "4:sample"} if operation == "templates.media_upload" else {"id": "1"}

		with patch("relay_core.whatsapp.execute", side_effect=execute):
			whatsapp_notification.create_supplier_document_template(self.channel)
		self.assertEqual([call[0] for call in calls], ["templates.media_upload", "templates.create"])
		header = calls[1][1]["components"][0]
		self.assertEqual(header["format"], "DOCUMENT")
		self.assertEqual(header["example"]["header_handle"], ["4:sample"])
