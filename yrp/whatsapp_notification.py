# Copyright (c) 2026, Essdee and contributors
# For license information, please see license.txt

"""Send WhatsApp to a document's supplier through Relay Core Document Actions.

A Relay Document Action names the approved template, the document fields that fill it and
the print format sent as its PDF. yrp checks the user's permission on the document, then
Relay sends as Administrator, so a user does not need a Relay role to send.
"""

import base64
import hashlib
import time
from contextlib import contextmanager

import frappe
from frappe import _

RELAY_APP = "relay_core"
SEND_USER = "Administrator"
SENT = ("Submitted", "Queued")
KEY_SECONDS = 600
TEMPLATE_NAME = "yrp_supplier_document"
TEMPLATE_LANGUAGE = "en"
TEMPLATE_BODY = "Hello {{1}},\n\nPlease find the attached document {{2}} dated {{3}}.\n\nThank you."
# Document -> date field of the template's {{3}}.
SUPPLIER_DOCUMENTS = {
	"Purchase Order": "transaction_date",
	"YRP Goods Received Note": "posting_date",
	"YRP Delivery Challan": "posting_date",
}


def is_relay_installed() -> bool:
	return RELAY_APP in frappe.get_installed_apps()


@contextmanager
def relay_user():
	previous = frappe.session.user
	frappe.set_user(SEND_USER)
	try:
		yield
	finally:
		frappe.set_user(previous)


def get_document_actions(doctype: str | None = None) -> list:
	"""Enabled supplier Document Actions on an enabled WhatsApp channel that the user's roles allow."""
	if not is_relay_installed():
		return []
	from relay_core.settings import value

	channels = frappe.get_all(
		"Relay Channel",
		filters={"channel": "WhatsApp", "enabled": 1, "account": value("relay_hub_account")},
		pluck="name",
	)
	filters = {"enabled": 1, "business_doctype": "Supplier", "channel": ["in", channels or [""]]}
	if doctype:
		filters["document_type"] = doctype
	actions = frappe.get_all(
		"Relay Document Action",
		filters=filters,
		fields=["name", "document_type", "business_field"],
		order_by="name asc",
	)
	roles = set(frappe.get_roles())
	action_roles = {}
	for row in frappe.get_all("Relay Document Action Role", fields=["parent", "role"]):
		action_roles.setdefault(row.parent, set()).add(row.role)
	return [
		action for action in actions if not action_roles.get(action.name) or roles & action_roles[action.name]
	]


@frappe.whitelist()
def get_enabled_whatsapp_doctypes():
	"""{"doctypes": {doctype: supplier field}} of the documents that can send WhatsApp."""
	doctypes = {}
	for action in get_document_actions():
		doctypes.setdefault(action.document_type, action.business_field)
	return {"doctypes": doctypes}


def get_action(doctype: str, action_name: str):
	for action in get_document_actions(doctype):
		if action.name == action_name:
			return action
	frappe.throw(_("WhatsApp message {0} is not available for {1}").format(action_name, _(doctype)))


def get_recipient_numbers(recipients: list) -> dict:
	"""{number: contact}: the number Relay sends to for each recipient contact."""
	from relay_core.identity import phone_number

	numbers = {}
	for row in recipients:
		if not row["numbers"]:
			continue
		mobile = phone_number(frappe.db.get_value("Contact", row["contact"], "mobile_no") or "", "IN")
		numbers.setdefault(mobile if mobile in row["numbers"] else row["numbers"][0], row["contact"])
	return numbers


@frappe.whitelist()
def get_whatsapp_context(doctype, docname, supplier_key="supplier"):
	"""The supplier's WhatsApp numbers and one filled message per Document Action of the doctype."""
	frappe.has_permission(doctype, ptype="read", doc=docname, throw=True)
	actions = get_document_actions(doctype)
	if not actions:
		frappe.throw(_("WhatsApp is not set up for {0} in Relay Document Actions").format(_(doctype)))

	from relay_core.document_actions import preview

	with relay_user():
		previews = [preview(doctype, docname, action.name) for action in actions]
	numbers = get_recipient_numbers(previews[0]["recipients"])
	if not numbers:
		frappe.throw(_("The supplier has no contact with a WhatsApp number"))
	return {
		"supplier": frappe.db.get_value(doctype, docname, actions[0].business_field),
		"contact": next(iter(numbers.values())),
		"mobile": next(iter(numbers)),
		"numbers": list(numbers),
		"templates": [
			{
				"name": row["action"],
				"template_name": row["action"],
				"body_text": row["text"],
				"file_name": row["file_name"],
				"needs_media": False,
				"variables": [],
			}
			for row in previews
		],
		"doc_fields": [],
	}


def get_request_key(dialog_key, *parts) -> str:
	"""One message per dialog. Without a dialog key, one message per 10 minutes, so a double click sends once."""
	content = ":".join(str(part) for part in (*parts, dialog_key or int(time.time() // KEY_SECONDS)))
	return hashlib.sha256(content.encode()).hexdigest()[:64]


@frappe.whitelist()
def send_whatsapp_notification(
	doctype,
	docname,
	template_name,
	language_code=None,
	params=None,
	mobile_no=None,
	header_file=None,
	supplier_key="supplier",
	request_key=None,
):
	"""Send the Document Action `template_name` to the supplier contact of `mobile_no`.

	language_code, params and header_file are accepted for older clients; the action fills
	the message and attaches the PDF. `request_key` comes from the dialog, so a retry from a
	new dialog sends again."""
	frappe.has_permission(doctype, ptype="write", doc=docname, throw=True)
	action = get_action(doctype, template_name)

	from relay_core.document_actions import preview, send
	from relay_core.identity import phone_number

	user = frappe.session.user
	with relay_user():
		numbers = get_recipient_numbers(preview(doctype, docname, action.name)["recipients"])
		number = phone_number(mobile_no or "", "IN") or next(iter(numbers), None)
		if number not in numbers:
			frappe.throw(_("Choose a WhatsApp number of the supplier's contacts"))
		key = get_request_key(request_key, user, doctype, docname, action.name, number)
		results = send(doctype, docname, action.name, [numbers[number]], key)["results"]

	errors = [row.get("error") or row.get("status") for row in results if row.get("status") not in SENT]
	result = {"ok": bool(results) and not errors, "error": "; ".join(errors) or None, "results": results}
	if result["ok"]:
		frappe.msgprint(_("WhatsApp message sent to {0}").format(number))
	else:
		frappe.msgprint(_("WhatsApp to {0} failed: {1}").format(number, result["error"]), indicator="red")
	return result


@frappe.whitelist(methods=["POST"])
def create_supplier_document_template(channel=None):
	"""Submit the supplier document template, with a PDF header, to Meta through Relay."""
	frappe.only_for("System Manager")
	from frappe.utils.pdf import get_pdf
	from relay_core.whatsapp import execute

	sample = get_pdf("<h3>Purchase Order PO-0001</h3><p>Sample supplier document</p>")
	upload = {
		"media_type": "document",
		"filename": "PO-0001.pdf",
		"mime_type": "application/pdf",
		"content_base64": base64.b64encode(sample).decode(),
	}
	handle = execute("templates.media_upload", upload, channel=channel)["handle"]
	arguments = {
		"name": TEMPLATE_NAME,
		"language": TEMPLATE_LANGUAGE,
		"category": "UTILITY",
		"parameter_format": "positional",
		"components": [
			{"type": "HEADER", "format": "DOCUMENT", "example": {"header_handle": [handle]}},
			{
				"type": "BODY",
				"text": TEMPLATE_BODY,
				"example": {"body_text": [["Sample Supplier", "PO-0001", "2026-11-04"]]},
			},
		],
	}
	return execute("templates.create", arguments, channel=channel)


@frappe.whitelist(methods=["POST"])
def setup_supplier_document_actions(channel=None):
	"""Create or update one Relay Document Action per supplier document. Needs the approved template."""
	frappe.only_for("System Manager")
	from relay_core.hub_channels import choose

	channel = channel or choose("WhatsApp")
	account = frappe.db.get_value("Relay Channel", channel, "account")
	template = frappe.db.get_value(
		"Relay WhatsApp Template",
		{
			"account": account,
			"template_name": TEMPLATE_NAME,
			"language": TEMPLATE_LANGUAGE,
			"status": "APPROVED",
		},
		"name",
	)
	if not template:
		frappe.throw(_("Meta has not approved the WhatsApp template {0} yet").format(TEMPLATE_NAME))

	names = []
	for doctype, date_field in SUPPLIER_DOCUMENTS.items():
		print_format = frappe.get_meta(doctype).default_print_format
		if not print_format:
			frappe.throw(_("Set a default print format for {0}").format(_(doctype)))
		title = f"{doctype} to Supplier"
		if frappe.db.exists("Relay Document Action", title):
			action = frappe.get_doc("Relay Document Action", title)
		else:
			action = frappe.new_doc("Relay Document Action")
		action.update(
			{
				"title": title,
				"enabled": 1,
				"document_type": doctype,
				"label": "Send WhatsApp",
				"show_button": 0,
				"business_field": "supplier",
				"business_doctype": "Supplier",
				"channel": channel,
				"template": template,
				"parameter_mapping": frappe.as_json(["supplier_name", "name", date_field]),
				"recipients": "Primary contact",
				"attachment_type": "Print Format",
				"print_format": print_format,
				"file_name": "{{doc.name}}",
			}
		)
		action.save()
		names.append(action.name)
	return names
