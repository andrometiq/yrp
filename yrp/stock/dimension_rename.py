import json

import frappe
from frappe import _
from yrp.stock.dimensions import assert_safe_fieldname, get_stock_dimensions

VALUE_FIELDS_HOOK = "stock_dimension_json_fields"
KEYED_FIELDS_HOOK = "stock_dimension_keyed_json_fields"


def rename_dimension_json_values(doc, method, old, new, merge=False):
	"""after_rename: rewrite a renamed stock-dimension value inside the JSON fields registered in hooks.

	`stock_dimension_json_fields` = {doctype: [fieldname]}: values under the dimension key are replaced.
	`stock_dimension_keyed_json_fields` = {dimension fieldname: {doctype: [fieldname]}}: dict keys are replaced.
	"""
	dimension = next((d for d in get_stock_dimensions() if d["dimension_doctype"] == doc.doctype), None)
	if not dimension or old == new:
		return
	key = dimension["fieldname"]
	for doctype, fieldnames in frappe.get_hooks(VALUE_FIELDS_HOOK).items():
		for fieldname in fieldnames:
			rewrite_json_field(
				doctype, fieldname, old, lambda data: replace_dimension_value(data, key, old, new)
			)
	for doctype, fieldnames in frappe.get_hooks(KEYED_FIELDS_HOOK).get(key, {}).items():
		for fieldname in fieldnames:
			rewrite_json_field(doctype, fieldname, old, lambda data: replace_dimension_key(data, old, new))


def rewrite_json_field(doctype, fieldname, old, rewrite):
	"""Apply `rewrite` to every row whose JSON `fieldname` contains `old` as a JSON string token."""
	assert_safe_fieldname(fieldname)
	if not frappe.get_meta(doctype).has_field(fieldname):
		frappe.throw(_("Stock dimension JSON field {0}.{1} does not exist").format(doctype, fieldname))
	rows = frappe.db.sql(
		f"select name, `{fieldname}` from `tab{doctype}` where `{fieldname}` like %s",
		("%" + escape_like(json.dumps(old)) + "%",),
	)
	updates = {}
	for name, raw in rows:
		data = json.loads(raw)
		rewritten = rewrite(data)
		if rewritten != data:
			updates[name] = {fieldname: frappe.as_json(rewritten)}
	if updates:
		frappe.db.bulk_update(doctype, updates, update_modified=False)


def replace_dimension_value(data, key, old, new):
	"""Replace `old` with `new` wherever it is the exact value of `key`, at any depth."""
	if isinstance(data, dict):
		return {
			field: new if field == key and value == old else replace_dimension_value(value, key, old, new)
			for field, value in data.items()
		}
	if isinstance(data, list):
		return [replace_dimension_value(item, key, old, new) for item in data]
	return data


def replace_dimension_key(data, old, new):
	"""Rename dict keys equal to `old` to `new`, at any depth; a clash with an existing `new` key fails."""
	if isinstance(data, dict):
		if old in data and new in data:
			frappe.throw(
				_("Cannot rename {0} to {1}: both are already used as keys in stored quantities").format(
					old, new
				)
			)
		return {
			new if field == old else field: replace_dimension_key(value, old, new)
			for field, value in data.items()
		}
	if isinstance(data, list):
		return [replace_dimension_key(item, old, new) for item in data]
	return data


def escape_like(value):
	return value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
