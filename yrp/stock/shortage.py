from urllib.parse import quote

import frappe
from frappe import _
from frappe.utils import escape_html, flt, get_datetime


def get_stock_qty_precision() -> int:
	"""Decimal places stock quantities are compared at: the ledger qty field precision."""
	return frappe.get_precision("YRP Stock Ledger Entry", "qty")


def get_stock_shortage(available, required, precision=None) -> float:
	"""How much ``required`` exceeds ``available`` at stock precision; 0 when stock suffices.

	Both sides are rounded first, so float residue such as 16.949999 vs 16.95 is not a shortage."""
	if precision is None:
		precision = get_stock_qty_precision()
	shortage = flt(flt(required, precision) - flt(available, precision), precision)
	return shortage if shortage > 0 else 0.0


def get_reservation_shortage(balance_after, reserved, precision=None) -> float:
	"""How much a stock-out leaving ``balance_after`` digs into ``reserved``; 0 when reservations stay covered."""
	if flt(reserved) <= 0:
		return 0.0
	return get_stock_shortage(balance_after, reserved, precision)


def get_dimension_text(dimensions):
	"""`` (Lot X, Received Type Y)`` for the non-empty stock dimensions."""
	from yrp.stock.dimensions import get_stock_dimensions

	labels = {dimension["fieldname"]: dimension["label"] for dimension in get_stock_dimensions()}
	parts = [
		f"{_(labels.get(fieldname) or fieldname)} {value}" for fieldname, value in dimensions.items() if value
	]
	return f" ({', '.join(parts)})" if parts else ""


class StockShortageReport:
	"""Collects every short stock bucket of one voucher and raises them in a single message."""

	def __init__(self, precision=None):
		self.precision = get_stock_qty_precision() if precision is None else precision
		self.rows = []

	def add(
		self,
		item,
		warehouse,
		available,
		required,
		details="",
		hint="",
		shortage=None,
		*,
		dimensions=None,
		as_of=None,
		exclude_entry=None,
		ledger_item=None,
	) -> bool:
		"""Record the bucket when it is short; returns whether it was.

		``shortage`` overrides the available-vs-required rule, e.g. for a reservation shortage.
		``dimensions`` lets the message name the ledger entry that last reduced the bucket up to ``as_of``;
		``ledger_item`` is the stock item when ``item`` is only its display name."""
		if shortage is None:
			shortage = get_stock_shortage(available, required, self.precision)
		if shortage:
			self.rows.append(
				frappe._dict(
					item=item,
					warehouse=warehouse,
					details=details,
					hint=hint,
					available=flt(available, self.precision),
					required=flt(required, self.precision),
					shortage=shortage,
					last_reduced=get_last_reducing_entry_text(
						ledger_item or item, warehouse, dimensions, as_of=as_of, exclude_entry=exclude_entry
					)
					if dimensions
					else "",
				)
			)
		return bool(shortage)

	@property
	def has_shortages(self) -> bool:
		return bool(self.rows)

	def get_message(self) -> str:
		lines = "".join(f"<li>{self.get_row_text(row)}</li>" for row in self.rows)
		if len(self.rows) == 1:
			heading = _("Insufficient stock for 1 item:")
		else:
			heading = _("Insufficient stock for {0} items:").format(len(self.rows))
		return f"{heading}<ul>{lines}</ul>"

	def get_row_text(self, row) -> str:
		text = _("{0}{1} at {2}: balance {3}, requested {4}, short {5}").format(
			escape_html(row.item),
			escape_html(row.details),
			escape_html(row.warehouse),
			row.available,
			row.required,
			row.shortage,
		)
		text += f". {escape_html(row.hint.strip())}" if row.hint else ""
		return text + (f"<br>{row.last_reduced}" if row.last_reduced else "")

	def throw(self):
		if not self.rows:
			return
		from yrp.stock.stock_ledger import NegativeStockError

		frappe.throw(self.get_message(), exc=NegativeStockError, title=_("Insufficient Stock"))


def get_last_reducing_entry(item, warehouse, dimensions, as_of=None, exclude_entry=None):
	"""Latest live ledger row that took stock out of the (item, warehouse, dimensions) bucket."""
	filters = {"item": item, "warehouse": warehouse, "is_cancelled": 0, "qty": ("<", 0)}
	filters.update({fieldname: value for fieldname, value in dimensions.items() if value})
	if as_of:
		filters["posting_datetime"] = ("<=", as_of)
	if exclude_entry:
		filters["name"] = ("!=", exclude_entry)
	entries = frappe.get_all(
		"YRP Stock Ledger Entry",
		filters=filters,
		fields=["voucher_type", "voucher_no", "posting_datetime", "qty"],
		order_by="posting_datetime desc, creation desc",
		limit=1,
	)
	return entries[0] if entries else None


def get_last_reducing_entry_text(item, warehouse, dimensions, as_of=None, exclude_entry=None) -> str:
	"""HTML line naming the entry that last reduced the bucket, linked so the user can open and cancel it."""
	entry = get_last_reducing_entry(item, warehouse, dimensions, as_of=as_of, exclude_entry=exclude_entry)
	if not entry:
		return escape_html(_("No earlier entry reduced this stock."))
	return _("Last reduced by {0} on {1} (qty {2})").format(
		get_document_link(entry.voucher_type, entry.voucher_no),
		get_datetime(entry.posting_datetime).strftime("%d-%m-%Y %H:%M"),
		flt(entry.qty),
	)


def get_document_link(doctype, name) -> str:
	"""Desk link labelled "<doctype> <name>", which /web maps to its own document page."""
	href = f"/app/{frappe.scrub(doctype).replace('_', '-')}/{quote(str(name), safe='')}"
	return f'<a href="{escape_html(href)}">{escape_html(doctype)} {escape_html(name)}</a>'


def validate_voucher_stock(sl_entries, allow_negative_stock=False):
	"""Pre-screen a voucher's outgoing ledger rows and report every short bucket at once.

	Nets each full-dimension bucket within the voucher and checks it against the balance at
	posting time. The valuation engine still validates each row, including future balances."""
	from yrp.stock.dimensions import get_dimension_fieldnames
	from yrp.stock.stock_ledger import _item_allows_negative_stock
	from yrp.stock.utils import get_combine_datetime, get_stock_balance

	if allow_negative_stock:
		return
	dimension_fields = get_dimension_fieldnames()
	report = StockShortageReport()
	for bucket in get_voucher_stock_buckets(sl_entries, dimension_fields).values():
		if bucket.qty >= 0 or _item_allows_negative_stock(bucket.item):
			continue
		available = get_stock_balance(
			bucket.item, bucket.warehouse, bucket.posting_date, bucket.posting_time, **bucket.dimensions
		)
		report.add(
			bucket.item,
			bucket.warehouse,
			available,
			-bucket.qty,
			get_dimension_text(bucket.dimensions),
			dimensions=bucket.dimensions,
			as_of=get_combine_datetime(bucket.posting_date, bucket.posting_time),
		)
	report.throw()


def get_voucher_stock_buckets(sl_entries, dimension_fields):
	"""Net qty per (item, warehouse, dimensions) for rows whose dimensions are all set."""
	buckets = {}
	for row in sl_entries:
		row = frappe._dict(row)
		if row.voucher_type == "YRP Stock Reconciliation" or not row.item or not row.warehouse:
			continue
		dimensions = {fieldname: row.get(fieldname) for fieldname in dimension_fields}
		if not all(dimensions.values()):
			continue
		key = (row.item, row.warehouse, tuple(dimensions.values()))
		bucket = buckets.setdefault(
			key,
			frappe._dict(
				item=row.item,
				warehouse=row.warehouse,
				dimensions=dimensions,
				posting_date=row.posting_date,
				posting_time=row.posting_time,
				qty=0.0,
			),
		)
		bucket.qty += flt(row.qty)
	return buckets
