from __future__ import annotations

import re
from collections.abc import Iterable
from html import escape, unescape
from importlib import import_module
from typing import Any

import frappe
from frappe.utils import get_datetime


LOGGER = frappe.logger("update_crm", allow_site=True)

CRM_LEAD = "CRM Lead"
COMMUNICATION = "Communication"
RECEIVED = "Received"
EMAIL = "Email"


@frappe.whitelist()
def get_activities(name: str):
	"""
	Wrap crm.api.activities.get_activities for CRM Lead only.

	Problem 1:
	- CRM API returns a received Communication activity.
	- But if communication_date is much older than creation, the CRM Emails tab
	  may place it very far down or outside the expected visible area.

	Problem 2:
	- Some incoming reply emails store the real plain text body in text_content,
	  while Communication.content / activity.data.content is empty HTML like:
	  <div dir="ltr"><br></div>

	Solution:
	- Do not write to DB.
	- Do not modify CRM/Frappe source.
	- Only normalize response-level data for Received Email Communications
	  already linked to the CRM Lead.
	"""
	base_get_activities = _get_base_get_activities()

	response = base_get_activities(name)

	if not name or not frappe.db.exists(CRM_LEAD, name):
		return response

	try:
		received_communications = _get_received_email_communications_for_lead(name)
		if not received_communications:
			LOGGER.info(
				"activities_visibility noop reason=NO_RECEIVED_EMAILS lead=%s",
				name,
			)
			return response

		result = _normalize_response_for_received_communications(
			response=response,
			received_communications=received_communications,
		)

		if result["date_normalized_count"] or result["content_fallback_count"]:
			LOGGER.info(
				"activities_visibility normalized lead=%s date_normalized_count=%s content_fallback_count=%s",
				name,
				result["date_normalized_count"],
				result["content_fallback_count"],
			)
		else:
			LOGGER.info(
				"activities_visibility noop reason=NO_RESPONSE_CHANGE lead=%s",
				name,
			)

	except Exception:
		# Fail-open: CRM activities must still load even if our normalization fails.
		LOGGER.exception(
			"activities_visibility normalize_failed lead=%s",
			name,
		)

	return response


def _get_base_get_activities():
	"""
	Return original CRM get_activities function.

	Important:
	Frappe whitelisted override resolves external calls to this wrapper,
	but importing crm.api.activities.get_activities here gives the original
	CRM function in normal Frappe runtime.
	"""
	crm_activities = import_module("crm.api.activities")
	return getattr(crm_activities, "get_activities")


def _get_received_email_communications_for_lead(lead_name: str) -> dict[str, dict[str, Any]]:
	"""
	Get Received Email Communications linked to the given CRM Lead.

	Returns a dict keyed by normalized creation datetime string.
	We intentionally use creation as the stable matching key because CRM activity
	payload may not include Communication.name at top level.
	"""
	rows = frappe.get_all(
		COMMUNICATION,
		filters={
			"reference_doctype": CRM_LEAD,
			"reference_name": lead_name,
			"sent_or_received": RECEIVED,
			"communication_medium": EMAIL,
		},
		fields=[
			"name",
			"creation",
			"communication_date",
			"subject",
			"sender",
			"content",
			"text_content",
		],
		order_by="creation desc",
		limit_page_length=200,
	)

	by_creation: dict[str, dict[str, Any]] = {}

	for row in rows:
		creation_key = _datetime_key(row.get("creation"))
		if not creation_key:
			continue

		by_creation[creation_key] = row

	return by_creation


def _normalize_response_for_received_communications(
	response: Any,
	received_communications: dict[str, dict[str, Any]],
) -> dict[str, int]:
	date_normalized_count = 0
	content_fallback_count = 0

	for activities in _iter_activity_lists(response):
		for activity in activities:
			if not isinstance(activity, dict):
				continue

			if not _is_communication_activity(activity):
				continue

			creation_key = _datetime_key(activity.get("creation"))
			if not creation_key:
				continue

			communication = received_communications.get(creation_key)
			if not communication:
				continue

			if _should_use_creation_as_communication_date(activity):
				activity["communication_date"] = activity.get("creation")
				date_normalized_count += 1

			if _apply_text_content_fallback(activity, communication):
				content_fallback_count += 1

	return {
		"date_normalized_count": date_normalized_count,
		"content_fallback_count": content_fallback_count,
	}


def _iter_activity_lists(response: Any) -> Iterable[list[Any]]:
	"""
	Support CRM get_activities response shapes.

	In this CRM version, get_activities returns a tuple of lists:
	(
	  communications,
	  comments,
	  tasks,
	  events,
	  ...
	)

	But we also support list/dict for forward compatibility.
	"""
	if isinstance(response, tuple):
		for item in response:
			if isinstance(item, list):
				yield item
		return

	if isinstance(response, list):
		yield response
		return

	if isinstance(response, dict):
		activities = response.get("activities")
		if isinstance(activities, list):
			yield activities

		for value in response.values():
			if isinstance(value, list):
				yield value


def _is_communication_activity(activity: dict[str, Any]) -> bool:
	"""
	CRM activity payload for Communication usually looks like:

	{
	  "activity_type": "communication",
	  "communication_type": "Communication",
	  "communication_date": ...,
	  "creation": ...,
	  "data": {...}
	}

	Do not depend on sent_or_received here because CRM API does not expose it
	at top level in the observed response.
	"""
	return (
		activity.get("activity_type") == "communication"
		and activity.get("communication_type") == COMMUNICATION
	)


def _should_use_creation_as_communication_date(activity: dict[str, Any]) -> bool:
	activity_creation = activity.get("creation")
	activity_communication_date = activity.get("communication_date")

	if not activity_creation:
		return False

	if not activity_communication_date:
		return True

	try:
		activity_creation_dt = get_datetime(activity_creation)
		activity_communication_dt = get_datetime(activity_communication_date)
	except Exception:
		LOGGER.warning(
			"activities_visibility invalid_activity_date",
		)
		return True

	if not activity_creation_dt or not activity_communication_dt:
		return True

	return activity_communication_dt < activity_creation_dt


def _apply_text_content_fallback(
	activity: dict[str, Any],
	communication: dict[str, Any],
) -> bool:
	"""
	If activity.data.content is blank but Communication.text_content exists,
	fill response-level content from text_content.

	Important:
	- Do not write to DB.
	- Escape text_content before converting newlines to <br>.
	- Do not log the actual email content.
	"""
	data = activity.get("data")
	if not isinstance(data, dict):
		return False

	current_content = data.get("content")
	if not _is_blank_html_content(current_content):
		return False

	text_content = communication.get("text_content")
	if not text_content or not str(text_content).strip():
		return False

	data["content"] = _text_to_safe_html(str(text_content))
	return True


def _is_blank_html_content(content: Any) -> bool:
	if content is None:
		return True

	content_text = str(content).strip()
	if not content_text:
		return True

	# Remove common HTML tags and whitespace-like entities to detect empty HTML.
	without_tags = re.sub(r"<[^>]*>", "", content_text)
	without_tags = unescape(without_tags)
	without_tags = without_tags.replace("\xa0", " ")
	without_tags = without_tags.strip()

	return not bool(without_tags)


def _text_to_safe_html(text: str) -> str:
	"""
	Convert plain text email body to safe HTML.

	We escape HTML first to prevent raw HTML/script injection, then preserve
	line breaks with <br>.
	"""
	normalized = text.replace("\r\n", "\n").replace("\r", "\n").strip()
	escaped = escape(normalized)
	return escaped.replace("\n", "<br>\n")


def _datetime_key(value: Any) -> str | None:
	if not value:
		return None

	try:
		dt = get_datetime(value)
	except Exception:
		return None

	if not dt:
		return None

	return dt.isoformat(sep=" ", timespec="microseconds")