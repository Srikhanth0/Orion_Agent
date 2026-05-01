"""
tools/google_tools.py — Google Workspace tools for the agent.

Covers: Gmail (read/send/reply), Calendar (list/create events),
Drive (search), and Sheets (read/write cells).

EVERY tool uses a strict Pydantic args_schema with descriptive Fields
to prevent parameter extraction failures from casual natural language.

First-time setup: run the OAuth flow to generate token.json.
"""
import base64
import json
import logging
from datetime import datetime, timedelta
from email.mime.text import MIMEText
from functools import lru_cache
from typing import Optional

from langchain_core.tools import tool
from pydantic import BaseModel, Field
from google.oauth2.credentials import Credentials
from google.auth.transport.requests import Request
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

import config

logger = logging.getLogger(__name__)


# ── Auth helper ───────────────────────────────────────────────────────────────

@lru_cache(maxsize=1)
def _get_credentials() -> Credentials:
    """Load or refresh Google OAuth credentials. Cached after first call."""
    creds = None
    if config.GOOGLE_TOKEN_PATH.exists():
        creds = Credentials.from_authorized_user_file(
            str(config.GOOGLE_TOKEN_PATH), config.GOOGLE_SCOPES
        )
    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            flow = InstalledAppFlow.from_client_secrets_file(
                str(config.GOOGLE_CREDENTIALS_PATH), config.GOOGLE_SCOPES
            )
            creds = flow.run_local_server(port=0)
        config.GOOGLE_TOKEN_PATH.write_text(creds.to_json())
    return creds


def _gmail():
    return build("gmail", "v1", credentials=_get_credentials(), cache_discovery=False)

def _calendar():
    return build("calendar", "v3", credentials=_get_credentials(), cache_discovery=False)

def _drive():
    return build("drive", "v3", credentials=_get_credentials(), cache_discovery=False)

def _sheets():
    return build("sheets", "v4", credentials=_get_credentials(), cache_discovery=False)


# ── Pydantic Args Schemas ─────────────────────────────────────────────────────

class ReadInboxArgs(BaseModel):
    max_results: int = Field(
        default=10,
        description="Number of emails to fetch. Default 10, max 50.",
    )
    query: str = Field(
        default="is:unread",
        description="Gmail search query. Examples: 'is:unread', 'from:boss@company.com', 'subject:invoice', 'after:2024/01/01'.",
    )


class SendEmailArgs(BaseModel):
    to: str = Field(
        description="The exact email address of the recipient. MUST extract this from context."
    )
    subject: str = Field(
        description="The subject line. Generate a short, relevant one if the user did not specify."
    )
    body: str = Field(
        description="The exact message text to send."
    )
    cc: str = Field(
        default="",
        description="Optional CC email address(es), comma-separated.",
    )


class ReplyEmailArgs(BaseModel):
    message_id: str = Field(
        description="The Gmail message ID to reply to (from gmail_read_inbox)."
    )
    reply_body: str = Field(
        description="The reply text content."
    )


class ListEventsArgs(BaseModel):
    days_ahead: int = Field(
        default=7,
        description="How many days ahead to look for events. Default 7, max 30.",
    )


class CreateEventArgs(BaseModel):
    title: str = Field(
        description="Event title/name."
    )
    start_datetime: str = Field(
        description="Start time in ISO 8601 format, e.g. '2025-01-15T10:00:00+05:30'."
    )
    end_datetime: str = Field(
        description="End time in ISO 8601 format, e.g. '2025-01-15T11:00:00+05:30'."
    )
    description: str = Field(
        default="",
        description="Optional event description.",
    )
    location: str = Field(
        default="",
        description="Optional location string.",
    )
    attendees: str = Field(
        default="",
        description="Comma-separated email addresses of attendees (optional).",
    )


class SearchDriveArgs(BaseModel):
    query: str = Field(
        description="Google Drive search query. Examples: 'name contains \"report\"', 'mimeType = \"application/pdf\"'."
    )
    max_results: int = Field(
        default=10,
        description="Maximum number of files to return. Default 10, max 50.",
    )


class ReadSheetArgs(BaseModel):
    spreadsheet_id: str = Field(
        description="The spreadsheet ID from the Google Sheets URL."
    )
    range_notation: str = Field(
        description="A1 notation range, e.g. 'Sheet1!A1:D10' or 'A1:B5'."
    )


class WriteSheetArgs(BaseModel):
    spreadsheet_id: str = Field(
        description="The spreadsheet ID from the Google Sheets URL."
    )
    range_notation: str = Field(
        description="A1 notation for the top-left cell, e.g. 'Sheet1!A1'."
    )
    values: str = Field(
        description="JSON array of arrays, e.g. '[[\"Name\",\"Age\"],[\"Alice\",30]]'."
    )


# ── Gmail tools ───────────────────────────────────────────────────────────────

@tool(args_schema=ReadInboxArgs)
def gmail_read_inbox(max_results: int = 10, query: str = "is:unread") -> str:
    """
    Read emails from Gmail inbox.
    Returns formatted list of emails with sender, subject, date, and snippet.
    """
    svc = _gmail()
    results = svc.users().messages().list(
        userId="me", q=query, maxResults=min(max_results, 50)
    ).execute()
    messages = results.get("messages", [])
    if not messages:
        return f"No emails found for query: '{query}'"

    output = []
    for msg in messages:
        m = svc.users().messages().get(
            userId="me", id=msg["id"], format="metadata",
            metadataHeaders=["From", "Subject", "Date"]
        ).execute()
        headers = {h["name"]: h["value"] for h in m["payload"]["headers"]}
        snippet = m.get("snippet", "")[:120]
        output.append(
            f"ID: {msg['id']}\n"
            f"From: {headers.get('From', 'unknown')}\n"
            f"Subject: {headers.get('Subject', '(no subject)')}\n"
            f"Date: {headers.get('Date', '')}\n"
            f"Preview: {snippet}\n"
        )
    return "\n---\n".join(output)


@tool(args_schema=SendEmailArgs)
def gmail_send_email(to: str, subject: str, body: str, cc: str = "") -> str:
    """
    Send an email via Gmail.
    Returns confirmation with message ID, or error.
    """
    try:
        msg = MIMEText(body)
        msg["to"] = to
        msg["subject"] = subject
        if cc:
            msg["cc"] = cc
        raw = base64.urlsafe_b64encode(msg.as_bytes()).decode()
        result = _gmail().users().messages().send(
            userId="me", body={"raw": raw}
        ).execute()
        return f"Email sent. Message ID: {result['id']}"
    except HttpError as e:
        return f"Failed to send email: {e}"


@tool(args_schema=ReplyEmailArgs)
def gmail_reply_to_email(message_id: str, reply_body: str) -> str:
    """
    Reply to an existing email thread.
    Returns confirmation or error.
    """
    try:
        svc = _gmail()
        original = svc.users().messages().get(
            userId="me", id=message_id, format="metadata",
            metadataHeaders=["From", "Subject", "Message-Id"]
        ).execute()
        headers = {h["name"]: h["value"] for h in original["payload"]["headers"]}
        thread_id = original["threadId"]

        reply = MIMEText(reply_body)
        reply["to"] = headers.get("From", "")
        reply["subject"] = "Re: " + headers.get("Subject", "")
        reply["In-Reply-To"] = headers.get("Message-Id", "")
        reply["References"] = headers.get("Message-Id", "")

        raw = base64.urlsafe_b64encode(reply.as_bytes()).decode()
        result = svc.users().messages().send(
            userId="me", body={"raw": raw, "threadId": thread_id}
        ).execute()
        return f"Reply sent. Message ID: {result['id']}"
    except HttpError as e:
        return f"Failed to reply: {e}"


# ── Calendar tools ────────────────────────────────────────────────────────────

@tool(args_schema=ListEventsArgs)
def calendar_list_events(days_ahead: int = 7) -> str:
    """
    List upcoming calendar events.
    Returns formatted list of events with title, time, and location.
    """
    try:
        now = datetime.utcnow().isoformat() + "Z"
        end = (datetime.utcnow() + timedelta(days=min(days_ahead, 30))).isoformat() + "Z"
        events_result = _calendar().events().list(
            calendarId="primary", timeMin=now, timeMax=end,
            maxResults=20, singleEvents=True, orderBy="startTime"
        ).execute()
        events = events_result.get("items", [])
        if not events:
            return f"No events in the next {days_ahead} days."
        lines = []
        for e in events:
            start = e["start"].get("dateTime", e["start"].get("date", ""))
            lines.append(
                f"• {e.get('summary', 'Untitled')} — {start}"
                + (f" @ {e['location']}" if e.get("location") else "")
            )
        return "\n".join(lines)
    except HttpError as e:
        return f"Calendar error: {e}"


@tool(args_schema=CreateEventArgs)
def calendar_create_event(
    title: str,
    start_datetime: str,
    end_datetime: str,
    description: str = "",
    location: str = "",
    attendees: str = "",
) -> str:
    """
    Create a calendar event.
    Returns confirmation with event ID and link.
    """
    try:
        event_body: dict = {
            "summary": title,
            "start": {"dateTime": start_datetime},
            "end": {"dateTime": end_datetime},
        }
        if description:
            event_body["description"] = description
        if location:
            event_body["location"] = location
        if attendees:
            event_body["attendees"] = [
                {"email": e.strip()} for e in attendees.split(",")
            ]
        result = _calendar().events().insert(
            calendarId="primary", body=event_body, sendUpdates="all"
        ).execute()
        return f"Event created: '{title}'\nID: {result['id']}\nLink: {result.get('htmlLink', '')}"
    except HttpError as e:
        return f"Failed to create event: {e}"


# ── Drive tools ───────────────────────────────────────────────────────────────

@tool(args_schema=SearchDriveArgs)
def drive_search_files(query: str, max_results: int = 10) -> str:
    """
    Search for files in Google Drive.
    Returns file names, IDs, and links.
    """
    results = _drive().files().list(
        q=query, pageSize=min(max_results, 50),
        fields="files(id, name, mimeType, modifiedTime, webViewLink)"
    ).execute()
    files = results.get("files", [])
    if not files:
        return f"No files found for: {query}"
    lines = [
        f"• {f['name']} ({f['mimeType'].split('.')[-1]})\n"
        f"  ID: {f['id']} | Modified: {f.get('modifiedTime', '')[:10]}\n"
        f"  Link: {f.get('webViewLink', 'N/A')}"
        for f in files
    ]
    return "\n".join(lines)


# ── Sheets tools ──────────────────────────────────────────────────────────────

@tool(args_schema=ReadSheetArgs)
def sheets_read_range(spreadsheet_id: str, range_notation: str) -> str:
    """
    Read a range of cells from a Google Sheets spreadsheet.
    Returns tab-separated cell values.
    """
    try:
        result = _sheets().spreadsheets().values().get(
            spreadsheetId=spreadsheet_id, range=range_notation
        ).execute()
        rows = result.get("values", [])
        if not rows:
            return "Range is empty."
        return "\n".join("\t".join(str(c) for c in row) for row in rows)
    except HttpError as e:
        return f"Sheets error: {e}"


@tool(args_schema=WriteSheetArgs)
def sheets_write_range(spreadsheet_id: str, range_notation: str, values: str) -> str:
    """
    Write data to a Google Sheets range.
    Returns confirmation of cells updated.
    """
    try:
        data = json.loads(values)
        result = _sheets().spreadsheets().values().update(
            spreadsheetId=spreadsheet_id,
            range=range_notation,
            valueInputOption="USER_ENTERED",
            body={"values": data},
        ).execute()
        return f"Updated {result.get('updatedCells', '?')} cells in {range_notation}."
    except (HttpError, json.JSONDecodeError) as e:
        return f"Sheets write error: {e}"


# ── Export all Google tools ───────────────────────────────────────────────────

GOOGLE_TOOLS = [
    gmail_read_inbox,
    gmail_send_email,
    gmail_reply_to_email,
    calendar_list_events,
    calendar_create_event,
    drive_search_files,
    sheets_read_range,
    sheets_write_range,
]
