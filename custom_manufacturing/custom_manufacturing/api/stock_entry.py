import frappe

def validate(doc, method):
    """
    Ensure total produced_qty in production_items does not exceed
    the qty of the is_finished_item in the main Stock Entry Items table.
    """
    if doc.stock_entry_type != "Manufacture" or not doc.custom_is_tinted:
        return

    finished_item_qty = frappe.get_value(
        "Stock Entry Detail",
        {"parent": doc.name, "is_finished_item": 1},
        "qty"
    ) or 0

    total_produced_qty = sum(
        (row.produced_qty or 0) for row in doc.custom_tinting_items if row.final_product
    )

    if total_produced_qty > finished_item_qty:
        frappe.throw(
            f"Total Produced Qty ({total_produced_qty}) cannot exceed "
            f"the Finished Item Qty ({finished_item_qty}) in the main Stock Entry Items."
        )


def sync_tinting_to_filling(doc, method):
    existing_bulk = {f.bulk_item for f in doc.custom_filling_details}

    for t_row in doc.custom_tinting_items:
        if t_row.final_product and t_row.final_product not in existing_bulk:
            f_row = doc.append("custom_filling_details", {})
            f_row.bulk_item = t_row.final_product
            f_row.target_warehouse = t_row.target_warehouse

def validate_filling_vs_tinting(doc, method):
    """
    Ensure that filling totals match tinting final_qty for each bulk color.
    """
    # build a map of final_qty per final_product
    tinting_map = {}
    for t_row in doc.custom_tinting_items:
        if t_row.final_product and t_row.final_qty:
            tinting_map[t_row.final_product] = t_row.final_qty

    # build a map of filled qty per bulk_item
    filling_map = {}
    for f_row in doc.custom_filling_details:
        if f_row.bulk_item and f_row.total_qty:
            filling_map[f_row.bulk_item] = filling_map.get(f_row.bulk_item, 0) + f_row.total_qty

    # compare totals
    for bulk_item, final_qty in tinting_map.items():
        filled_qty = filling_map.get(bulk_item, 0)
        if round(filled_qty, 3) != round(final_qty, 3):
            frappe.throw(
                f"Filling for {bulk_item} does not match Tinting Final Qty. "
                f"Tinted: {final_qty}, Filled: {filled_qty}"
            )

def on_submit(doc, method):
    if doc.stock_entry_type != "Manufacture" or not doc.custom_is_tinted:
        return

    tinted_items_group = []

    finished_item_info = frappe.get_value(
        "Stock Entry Detail",
        {"parent": doc.name, "is_finished_item": 1},
        ["item_code", "s_warehouse"],
        as_dict=True
    )

    for row in doc.custom_tinting_items:
        is_final_product = bool(row.final_product and row.final_product.strip())

        if not is_final_product:
            # collect tinting ingredients
            tinted_items_group.append(row)

        else:
            # --- MATERIAL ISSUE: Tinting ingredients + Base bulk ---
            if tinted_items_group or finished_item_info:
                consumption_se = frappe.new_doc("Stock Entry")
                consumption_se.stock_entry_type = "Material Issue"
                consumption_se.company = doc.company
                consumption_se.posting_date = doc.posting_date
                consumption_se.posting_time = doc.posting_time
                consumption_se.set_posting_time = 1
                consumption_se.from_bom = 0
                consumption_se.custom_linked_production_entry = doc.name

                # Add tinting items
                for t_row in tinted_items_group:
                    consumption_se.append("items", {
                        "item_code": t_row.tint_item,
                        "qty": t_row.tint_qty,
                        "uom": frappe.db.get_value("Item", t_row.tint_item, "stock_uom"),
                        "s_warehouse": t_row.source_warehouse,
                        "conversion_factor": 1
                    })

                # Consume base bulk (proportional to produced_qty)
                if finished_item_info and row.produced_qty:
                    consumption_se.append("items", {
                        "item_code": finished_item_info.item_code,
                        "qty": row.produced_qty,
                        "uom": frappe.db.get_value("Item", finished_item_info.item_code, "stock_uom"),
                        "s_warehouse": row.source_warehouse or finished_item_info.s_warehouse,
                        "conversion_factor": 1
                    })

                if consumption_se.items:
                    consumption_se.insert()
                    consumption_se.submit()

            # --- MATERIAL RECEIPT: Bulk tinted product itself ---
            if row.final_product and row.final_qty:
                bulk_receipt = frappe.new_doc("Stock Entry")
                bulk_receipt.stock_entry_type = "Material Receipt"
                bulk_receipt.company = doc.company
                bulk_receipt.posting_date = doc.posting_date
                bulk_receipt.posting_time = doc.posting_time
                bulk_receipt.set_posting_time = 1
                bulk_receipt.from_bom = 0
                bulk_receipt.custom_linked_production_entry = doc.name

                bulk_receipt.append("items", {
                    "item_code": row.final_product,
                    "qty": row.final_qty,
                    "uom": frappe.db.get_value("Item", row.final_product, "stock_uom"),
                    "t_warehouse": row.target_warehouse,
                    "conversion_factor": 1
                })

                bulk_receipt.insert()
                bulk_receipt.submit()

            # --- FILLING: Repack bulk into SKUs ---
            if hasattr(doc, "custom_filling_details"):
                filling_rows = [f for f in doc.custom_filling_details if f.bulk_item == row.final_product]

                for f_row in filling_rows:
                    # 1. Material Issue (reduce bulk)
                    issue_se = frappe.new_doc("Stock Entry")
                    issue_se.stock_entry_type = "Material Issue"
                    issue_se.company = doc.company
                    issue_se.posting_date = doc.posting_date
                    issue_se.posting_time = doc.posting_time
                    issue_se.set_posting_time = 1
                    issue_se.from_bom = 0
                    issue_se.custom_linked_production_entry = doc.name

                    issue_se.append("items", {
                        "item_code": f_row.bulk_item,
                        "qty": f_row.total_qty,   
                        "uom": frappe.db.get_value("Item", f_row.bulk_item, "stock_uom"),
                        "s_warehouse": f_row.target_warehouse,
                        "conversion_factor": 1
                    })
                    issue_se.insert()
                    issue_se.submit()

                    # 2. Material Receipt (filled packs)
                    pack_se = frappe.new_doc("Stock Entry")
                    pack_se.stock_entry_type = "Material Receipt"
                    pack_se.company = doc.company
                    pack_se.posting_date = doc.posting_date
                    pack_se.posting_time = doc.posting_time
                    pack_se.set_posting_time = 1
                    pack_se.from_bom = 0
                    pack_se.custom_linked_production_entry = doc.name

                    pack_se.append("items", {
                        "item_code": f_row.filled_item,
                        "qty": f_row.filled,  
                        "uom": frappe.db.get_value("Item", f_row.filled_item, "stock_uom"),
                        "t_warehouse": f_row.target_warehouse,
                        "conversion_factor": 1
                    })
                    pack_se.insert()
                    pack_se.submit()

            tinted_items_group = []
