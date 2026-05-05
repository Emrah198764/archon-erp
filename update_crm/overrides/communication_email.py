import frappe
from frappe.utils import cint
from frappe.utils.commands import warn

from frappe.core.doctype.communication import email as frappe_communication_email


def _get_preferred_sender_for_current_user() -> str | None:
	"""Return default outgoing sender email only when CRM preference explicitly enables it."""
	pref = frappe.db.get_value(
		"CRM Email Sender Preference",
		{"user": frappe.session.user},
		["enabled", "use_default_email_account_for_crm"],
		as_dict=True,
	)

	if not pref:
		return None

	if not cint(pref.enabled) or not cint(pref.use_default_email_account_for_crm):
		return None

	default_outgoing_email_id = frappe.db.get_value(
		"Email Account", {"default_outgoing": 1}, "email_id"
	)

	if not default_outgoing_email_id:
		return None

	return default_outgoing_email_id


@frappe.whitelist()
def make(
	doctype=None,
	name=None,
	content=None,
	subject=None,
	sent_or_received="Sent",
	sender=None,
	sender_full_name=None,
	recipients=None,
	communication_medium="Email",
	send_email=False,
	print_html=None,
	print_format=None,
	attachments=None,
	send_me_a_copy=False,
	cc=None,
	bcc=None,
	read_receipt=None,
	print_letterhead=True,
	email_template=None,
	communication_type=None,
	send_after=None,
	print_language=None,
	now=False,
	**kwargs,
):
	"""Keep Frappe email.make behavior and only override sender when CRM preference requires it."""
	if kwargs:
		warn(
			f"Options {kwargs} used in frappe.core.doctype.communication.email.make "
			"are deprecated or unsupported",
			category=DeprecationWarning,
		)

	if doctype and name:
		frappe.has_permission(doctype, doc=name, ptype="email", throw=True)

	if cint(send_email):
		preferred_sender = _get_preferred_sender_for_current_user()
		if preferred_sender:
			sender = preferred_sender

	return frappe_communication_email._make(
		doctype=doctype,
		name=name,
		content=content,
		subject=subject,
		sent_or_received=sent_or_received,
		sender=sender,
		sender_full_name=sender_full_name,
		recipients=recipients,
		communication_medium=communication_medium,
		send_email=send_email,
		print_html=print_html,
		print_format=print_format,
		attachments=attachments,
		send_me_a_copy=cint(send_me_a_copy),
		cc=cc,
		bcc=bcc,
		read_receipt=cint(read_receipt),
		print_letterhead=print_letterhead,
		email_template=email_template,
		communication_type=communication_type,
		add_signature=False,
		send_after=send_after,
		print_language=print_language,
		now=now,
	)
