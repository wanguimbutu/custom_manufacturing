import frappe
from erpnext.stock.doctype.stock_reconciliation.stock_reconciliation import get_itemwise_batch
from erpnext.stock.utils import get_stock_balance

def tuple_safe(data):
    """Convert dict with tuple keys to JSON-safe dict."""
    if isinstance(data, dict):
        return {str(k): tuple_safe(v) for k, v in data.items()}
    elif isinstance(data, list):
        return [tuple_safe(v) for v in data]
    else:
        return data

def before_save(doc, method):
    existing_batches = {(d.item_code, d.warehouse, d.batch_no) for d in doc.items if d.batch_no}
    item_wh_set = {(d.item_code, d.warehouse) for d in doc.items if d.item_code and d.warehouse}

    nullified_count = 0
    reassigned_count = 0

    for wh in {w for _, w in item_wh_set}:
        itemwise_batch_data = get_itemwise_batch(wh, doc.posting_date, doc.company)

        for key, batches in itemwise_batch_data.items():
            if isinstance(key, tuple):
                icode, warehouse = key
            else:
                icode = key
                warehouse = wh

            if warehouse != wh or (icode, warehouse) not in item_wh_set:
                continue

            # Get current valuation rate
            _, valuation_rate = get_stock_balance(
                icode, warehouse,
                doc.posting_date, doc.posting_time,
                with_valuation_rate=True
            )

            # --- Update existing rows with valuation_rate ---
            for d in doc.items:
                if d.item_code == icode and d.warehouse == warehouse:
                    d.valuation_rate = valuation_rate or 0
                    # keep amount consistent if qty is present
                    if d.qty:
                        d.amount = d.qty * d.valuation_rate

            # --- Handle general qty rows (no batch_no but qty entered) ---
            for d in doc.items:
                if d.item_code == icode and d.warehouse == warehouse and not d.batch_no and d.qty:
                    if batches:
                        first_batch = batches[0].get("batch_no")
                        d.batch_no = first_batch
                        d.use_serial_batch_fields = 1
                        reassigned_count += 1
                        existing_batches.add((icode, warehouse, first_batch))
                    break  # only handle once per row

            # --- Nullify all other batches ---
            for row in batches:
                batch_no = row.get("batch_no")
                qty = row.get("qty") or 0

                # skip zero qty or already used batch
                if qty == 0 or (icode, warehouse, batch_no) in existing_batches:
                    continue

                new_row = doc.append("items", {})
                new_row.item_code = icode
                new_row.warehouse = warehouse
                new_row.batch_no = batch_no
                new_row.qty = 0
                new_row.valuation_rate = valuation_rate or 0
                new_row.amount = 0
                new_row.use_serial_batch_fields = 1

                existing_batches.add((icode, warehouse, batch_no))
                nullified_count += 1

