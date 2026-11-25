(() => {
	const settings = frappe.listview_settings["Purchase Order"] || {};
	const nativeGetIndicator = settings.get_indicator;
	const nativeOnload = settings.onload;
	const PO_METHOD = "yrp.yrp.doctype.yrp_purchase_order.yrp_purchase_order";
	const requiredFields = ["is_yrp_managed", "yrp_fulfillment_status"];
	settings.add_fields = [...new Set([...(settings.add_fields || []), ...requiredFields])];
	settings.has_indicator_for_draft = true;
	settings.has_indicator_for_cancelled = true;

	settings.get_indicator = function (doc) {
		if (!doc.is_yrp_managed) {
			if (doc.docstatus === 0) return [__("Draft"), "red", "docstatus,=,0"];
			if (doc.docstatus === 2) return [__("Cancelled"), "red", "docstatus,=,2"];
			return nativeGetIndicator?.call(this, doc);
		}

		const status = doc.yrp_fulfillment_status || doc.status;
		const colors = {
			Draft: "orange",
			Ordered: "blue",
			"Partially Received": "yellow",
			Received: "green",
			"Partially Cancelled": "grey",
			Cancelled: "darkgrey",
			Closed: "light-blue",
		};
		return [
			__(status),
			colors[status] || "blue",
			`is_yrp_managed,=,1|yrp_fulfillment_status,=,${status}`,
		];
	};

	settings.onload = function (listview) {
		nativeOnload?.call(this, listview);
		replace_open_status_actions(listview);
	};

	function replace_open_status_actions(listview) {
		// ERPNext's Close/Reopen ignore YRP open_status; managers get the YRP bulk method instead.
		for (const label of ["Close", "Reopen"]) {
			listview.page.actions
				.find(`[data-label="${encodeURIComponent(__(label))}"]`)
				.closest("li")
				.remove();
		}
		frappe.xcall(`${PO_METHOD}.get_close_permission`).then((permission) => {
			if (!permission?.is_close_manager) return;
			listview.page.add_actions_menu_item(__("Close"), () => set_open_status(listview, true));
			listview.page.add_actions_menu_item(__("Reopen"), () => set_open_status(listview, false));
		});
	}

	function set_open_status(listview, close) {
		const names = listview.get_checked_items(true);
		if (!names.length) {
			frappe.msgprint(__("Select at least one Purchase Order."));
			return;
		}
		const verb = close ? __("Close") : __("Reopen");
		frappe.confirm(__("{0} {1} selected Purchase Orders?", [verb, names.length]), () =>
			frappe
				.call({
					method: `${PO_METHOD}.close_or_open_purchase_orders`,
					args: { names, close },
					freeze: true,
				})
				.then(({ message }) => {
					show_open_status_result(message || {}, close);
					listview.refresh();
				})
		);
	}

	function show_open_status_result({ done = [], skipped = [] }, close) {
		const summary = __("{0} {1}; skipped {2}.", [
			close ? __("Closed") : __("Reopened"),
			done.length,
			skipped.length,
		]);
		if (!skipped.length) {
			frappe.show_alert({ message: summary, indicator: "green" });
			return;
		}
		const rows = skipped
			.map(
				(row) =>
					`<li><strong>${frappe.utils.escape_html(row.name)}</strong> — ${frappe.utils.escape_html(row.reason)}</li>`
			)
			.join("");
		frappe.msgprint({
			title: close ? __("Bulk Close Result") : __("Bulk Reopen Result"),
			indicator: done.length ? "orange" : "red",
			message: `<p>${summary}</p><ul>${rows}</ul>`,
		});
	}

	frappe.listview_settings["Purchase Order"] = settings;
})();
