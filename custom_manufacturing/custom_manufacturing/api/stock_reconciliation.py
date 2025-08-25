import frappe
from erpnext.stock.doctype.stock_reconciliation.stock_reconciliation import get_itemwise_batch
from erpnext.stock.utils import get_stock_balance

def before_save(doc, method):
    """
    Optimized batch reconciliation:
    - Works for manual entry, bulk edit, and Data Import
    - Fetches all batches in bulk, not per-row
    - Nullifies missing batches (qty=0), skips empties and duplicates
    - Preserves valuation_rate from stock balance
    """
    existing_batches = {(d.item_code, d.warehouse, d.batch_no) for d in doc.items if d.batch_no}

    item_wh_set = {(d.item_code, d.warehouse) for d in doc.items if d.item_code and d.warehouse}

    for wh in {w for _, w in item_wh_set}:
        itemwise_batch_data = get_itemwise_batch(
            wh,
            doc.posting_date,
            doc.company
        )

        for (item_code, warehouse), batches in itemwise_batch_data.items():
            
            if warehouse != wh:
                continue

            if (item_code, warehouse) not in item_wh_set:
                continue

            _, valuation_rate = get_stock_balance(
                item_code,
                warehouse,
                doc.posting_date,
                doc.posting_time,
                with_valuation_rate=True,
            )

            for row in batches:
                batch_no = row.get("batch_no")
                qty = row.get("qty") or 0

                # Ignore empty stock
                if qty == 0:
                    continue

                # Skip already-entered batches
                if (item_code, warehouse, batch_no) in existing_batches:
                    continue

                #  Append nullified batch row
                new_row = doc.append("items", {})
                new_row.item_code = item_code
                new_row.warehouse = warehouse
                new_row.batch_no = batch_no
                new_row.qty = 0
                new_row.valuation_rate = valuation_rate or 0
                new_row.amount = 0

                existing_batches.add((item_code, warehouse, batch_no))

    frappe.msgprint("Auto-fetched and nullified missing non-empty batches (optimized, valuation preserved).")
