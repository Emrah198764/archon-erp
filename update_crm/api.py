import frappe


@frappe.whitelist()
def get_app_status():
    return {
        "status": "ok",
        "app": "update_crm",
        "message": "Update CRM app is running",
    }
