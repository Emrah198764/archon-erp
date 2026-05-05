from __future__ import annotations

from email.utils import parseaddr

import frappe


LOGGER = frappe.logger("update_crm", allow_site=True)
RECEIVED = "Received"
EMAIL = "Email"
CRM_LEAD = "CRM Lead"
COMM_LINK_DTYPE = "Communication Link"
LEAD_EMAIL_FIELDS = ("email", "email_id")


def link_incoming_email_to_crm_lead(doc, method=None):
	"""Link incoming email Communications to a unique CRM Lead."""
	try:
		if not _is_incoming_email(doc):
			return

		if _already_linked_to_crm_lead(doc):
			LOGGER.info(
				"communication_linking noop already_linked communication=%s",
				doc.name,
			)
			return

		sender_email = _extract_sender_email(doc)
		if not sender_email:
			LOGGER.warning(
				"communication_linking skipped reason=NO_SENDER communication=%s",
				doc.name,
			)
			return

		matches = _find_matching_leads(sender_email)
		match_count = len(matches)

		if match_count == 0:
			LOGGER.warning(
				"communication_linking skipped reason=NO_MATCH communication=%s",
				doc.name,
			)
			return

		if match_count > 1:
			LOGGER.warning(
				"communication_linking skipped reason=AMBIGUOUS_MATCH communication=%s match_count=%s",
				doc.name,
				match_count,
			)
			return

		lead_name = matches[0]
		_link_communication(doc, lead_name)
		LOGGER.info(
			"communication_linking linked communication=%s lead=%s",
			doc.name,
			lead_name,
		)
	except Exception:
		LOGGER.exception(
			"communication_linking failed communication=%s",
			getattr(doc, "name", "<unknown>"),
		)
		# Do not block Communication insert flow on linking failures.
		return


def _is_incoming_email(doc) -> bool:
	return (
		getattr(doc, "sent_or_received", None) == RECEIVED
		and getattr(doc, "communication_medium", None) == EMAIL
	)


def _extract_sender_email(doc) -> str | None:
	raw_sender = (getattr(doc, "sender", None) or "").strip()
	if not raw_sender:
		return None

	_, email = parseaddr(raw_sender)
	email = (email or "").strip().lower()
	return email or None


def _find_matching_leads(sender_email: str) -> list[str]:
	lead_meta = frappe.get_meta(CRM_LEAD)
	fields = [fieldname for fieldname in LEAD_EMAIL_FIELDS if lead_meta.has_field(fieldname)]
	if not fields:
		return []

	seen = set()
	matches = []
	for fieldname in fields:
		names = frappe.get_all(
			CRM_LEAD,
			filters={fieldname: sender_email},
			pluck="name",
			limit_page_length=2,
		)
		for name in names:
			if name not in seen:
				seen.add(name)
				matches.append(name)

	return matches


def _already_linked_to_crm_lead(doc) -> bool:
	if getattr(doc, "reference_doctype", None) == CRM_LEAD and getattr(doc, "reference_name", None):
		return True

	if not frappe.db.exists("DocType", COMM_LINK_DTYPE):
		return False

	return bool(
		frappe.db.exists(
			COMM_LINK_DTYPE,
			{
				"parent": doc.name,
				"parenttype": "Communication",
				"link_doctype": CRM_LEAD,
			},
		)
	)


def _link_communication(doc, lead_name: str) -> None:
	changed = False

	if getattr(doc, "reference_doctype", None) != CRM_LEAD or getattr(doc, "reference_name", None) != lead_name:
		doc.db_set("reference_doctype", CRM_LEAD, update_modified=False)
		doc.db_set("reference_name", lead_name, update_modified=False)
		changed = True

	if _add_communication_link_row(doc, lead_name):
		changed = True

	if changed:
		doc.reload()


def _add_communication_link_row(doc, lead_name: str) -> bool:
	if not frappe.db.exists("DocType", COMM_LINK_DTYPE):
		return False

	exists = frappe.db.exists(
		COMM_LINK_DTYPE,
		{
			"parent": doc.name,
			"parenttype": "Communication",
			"link_doctype": CRM_LEAD,
			"link_name": lead_name,
		},
	)
	if exists:
		return False

	parentfield = _get_communication_link_parentfield()
	if not parentfield:
		return False

	link_doc = frappe.get_doc(
		{
			"doctype": COMM_LINK_DTYPE,
			"parent": doc.name,
			"parenttype": "Communication",
			"parentfield": parentfield,
			"link_doctype": CRM_LEAD,
			"link_name": lead_name,
		}
	)
	link_doc.insert(ignore_permissions=True)
	return True


def _get_communication_link_parentfield() -> str | None:
	communication_meta = frappe.get_meta("Communication")
	for field in communication_meta.fields:
		if field.fieldtype == "Table" and field.options == COMM_LINK_DTYPE:
			return field.fieldname
	return None
