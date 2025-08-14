import frappe

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


def on_submit(doc, method):
    """
    Process production_items in a 'repack' style for tinted manufacturing.
    For each group of tinted items ending with a final product row:
    - Create Material Issue (tinted + is_finished_item with produced_qty from final row)
    - Create Material Receipt (final product)
    Only runs if:
        - Stock Entry Type = Manufacture
        - custom_is_tinted is checked
    """

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
            
            tinted_items_group.append(row)

        else:
            if tinted_items_group or finished_item_info:
                consumption_se = frappe.new_doc("Stock Entry")
                consumption_se.stock_entry_type = "Material Issue"
                consumption_se.company = doc.company
                consumption_se.posting_date = doc.posting_date
                consumption_se.posting_time = doc.posting_time
                consumption_se.set_posting_time = 1
                consumption_se.from_bom = 0
                consumption_se.custom_linked_production_entry = doc.name

                # Add tinted items
                for t_row in tinted_items_group:
                    consumption_se.append("items", {
                        "item_code": t_row.tint_item,
                        "qty": t_row.tint_qty,
                        "uom": frappe.db.get_value("Item", t_row.tint_item, "stock_uom"),
                        "s_warehouse": t_row.source_warehouse,
                        "conversion_factor": 1
                    })

                # Add produced item consumption from final row's produced_qty
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

            production_se = frappe.new_doc("Stock Entry")
            production_se.stock_entry_type = "Material Receipt"
            production_se.company = doc.company
            production_se.posting_date = doc.posting_date
            production_se.posting_time = doc.posting_time
            production_se.set_posting_time = 1
            production_se.from_bom = 0
            production_se.custom_linked_production_entry = doc.name

            production_se.append("items", {
                "item_code": row.final_product,
                "qty": row.final_qty,
                "uom": frappe.db.get_value("Item", row.final_product, "stock_uom"),
                "t_warehouse": row.target_warehouse,
                "conversion_factor": 1
            })

            if production_se.items:
                production_se.insert()
                production_se.submit()

            tinted_items_group = []
