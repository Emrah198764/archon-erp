import frappe


def execute():
	doctype = "CRM Email Sender Preference"
	old_field = "use_default_email_account_for_crm"
	new_field = "use_default_email_account_for_email"
	enabled_field = "enabled"

	if not frappe.db.table_exists(doctype):
		return

	has_old = frappe.db.has_column(doctype, old_field)
	has_new = frappe.db.has_column(doctype, new_field)
	has_enabled = frappe.db.has_column(doctype, enabled_field)

	if not has_new:
		frappe.db.sql(
			f"""
			ALTER TABLE `tab{doctype}`
			ADD COLUMN `{new_field}` TINYINT(1) NOT NULL DEFAULT 0
			"""
		)
		has_new = True

	if has_old:
		if has_enabled:
			frappe.db.sql(
				f"""
				UPDATE `tab{doctype}`
				SET `{new_field}` =
					CASE
						WHEN COALESCE(`{enabled_field}`, 1) = 1
							AND COALESCE(`{old_field}`, 0) = 1
						THEN 1
						ELSE 0
					END
				"""
			)
		else:
			frappe.db.sql(
				f"""
				UPDATE `tab{doctype}`
				SET `{new_field}` = COALESCE(`{old_field}`, 0)
				"""
			)