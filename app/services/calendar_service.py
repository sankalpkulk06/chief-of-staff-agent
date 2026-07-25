"""Google Calendar + Google Tasks service for the daily planner.

Auth mirrors ``EmailService`` (per-user OAuth tokens stored in the registry), but
requests the Calendar and Tasks scopes together so one consent covers both.

Provenance: every event Sage writes carries
``extendedProperties.private = {"sage_managed": "true", "sage_block_id": <uuid>}``.
This lets Sage server-side query *only its own* events and refuse to ever mutate a
user-owned event. Deletes are soft — Google's own ``status: "cancelled"`` via patch,
never ``events().delete``.
"""
import json
from typing import Any, Dict, List, Optional, Tuple

from pydantic import BaseModel

# Calendar read+write, Tasks read+write (write is used to mark a task complete when
# its scheduled block is removed) — requested in a single consent.
GOOGLE_SCOPES = [
    "https://www.googleapis.com/auth/calendar.events",
    "https://www.googleapis.com/auth/tasks",
]

SAGE_MANAGED_KEY = "sage_managed"
SAGE_BLOCK_ID_KEY = "sage_block_id"
SAGE_MANAGED_FILTER = f"{SAGE_MANAGED_KEY}=true"


class CalendarNotConnectedError(Exception):
    """Raised when no OAuth token exists for the user, or it can't be refreshed."""


class CalendarConflictError(Exception):
    """Raised when a write is rejected because the event changed since it was read (etag/If-Match 412)."""


class CalendarEventGoneError(Exception):
    """Raised when the target event no longer exists (404/410) — e.g. user deleted it in Google."""


class CalendarPermissionError(Exception):
    """Raised when Sage refuses to mutate an event it did not create (missing sage_managed tag)."""


class CalEvent(BaseModel):
    id: str
    etag: Optional[str] = None
    title: str = ""
    start: Optional[str] = None  # RFC3339 (dateTime) or date (all-day)
    end: Optional[str] = None
    status: Optional[str] = None
    all_day: bool = False
    sage_managed: bool = False
    sage_block_id: Optional[str] = None


class GoogleTask(BaseModel):
    id: str
    title: str
    due: Optional[str] = None      # RFC3339 date (date-only; no time component)
    notes: Optional[str] = None
    tasklist_id: str
    tasklist_title: str = ""


class CalendarService:
    """Google Calendar + Tasks backed by per-user OAuth tokens (account_type='google_calendar')."""

    def __init__(self, client_secrets: Dict[str, Any]) -> None:
        self._client_secrets = client_secrets

    # ------------------------------------------------------------------
    # Auth (mirrors EmailService)
    # ------------------------------------------------------------------

    def _credentials(self, token_json: Dict) -> Tuple[Any, Optional[Dict]]:
        """Return (creds, refreshed_token_or_None). Refresh transparently if expired."""
        from google.auth.transport.requests import Request
        from google.oauth2.credentials import Credentials

        creds = Credentials.from_authorized_user_info(token_json, GOOGLE_SCOPES)
        refreshed: Optional[Dict] = None
        if not creds.valid:
            if creds.expired and creds.refresh_token:
                creds.refresh(Request())
                refreshed = json.loads(creds.to_json())
            else:
                raise CalendarNotConnectedError(
                    "Calendar token is invalid. Please reconnect Google Calendar."
                )
        return creds, refreshed

    def build_service(self, token_json: Dict) -> Tuple[Any, Optional[Dict]]:
        """Build a Google Calendar v3 client. Returns (service, refreshed_token_or_None)."""
        from googleapiclient.discovery import build

        creds, refreshed = self._credentials(token_json)
        return build("calendar", "v3", credentials=creds, cache_discovery=False), refreshed

    def build_tasks_service(self, token_json: Dict) -> Tuple[Any, Optional[Dict]]:
        """Build a Google Tasks v1 client. Returns (service, refreshed_token_or_None)."""
        from googleapiclient.discovery import build

        creds, refreshed = self._credentials(token_json)
        return build("tasks", "v1", credentials=creds, cache_discovery=False), refreshed

    def _web_config(self) -> Dict:
        return self._client_secrets.get("web", self._client_secrets)

    def get_oauth_url(self, redirect_uri: str, state: str) -> str:
        """Generate a Google OAuth consent URL for the combined Calendar+Tasks scopes."""
        from requests_oauthlib import OAuth2Session

        cfg = self._web_config()
        session = OAuth2Session(
            client_id=cfg["client_id"],
            scope=GOOGLE_SCOPES,
            redirect_uri=redirect_uri,
            state=state,
        )
        auth_url, _ = session.authorization_url(
            "https://accounts.google.com/o/oauth2/auth",
            access_type="offline",
            prompt="consent",
            # Google may add the granted scopes; tolerate scope-order differences.
            include_granted_scopes="true",
        )
        return auth_url

    def exchange_code(self, code: str, redirect_uri: str, state: str) -> Dict:
        """Exchange an auth code for a normalized authorized-user token dict."""
        import os
        from requests_oauthlib import OAuth2Session

        os.environ.setdefault("OAUTHLIB_INSECURE_TRANSPORT", "1")
        # Google often returns a superset/reordered scope list; don't fail on that.
        os.environ.setdefault("OAUTHLIB_RELAX_TOKEN_SCOPE", "1")

        cfg = self._web_config()
        session = OAuth2Session(client_id=cfg["client_id"], redirect_uri=redirect_uri, state=state)
        token = session.fetch_token(
            "https://oauth2.googleapis.com/token",
            client_secret=cfg["client_secret"],
            code=code,
        )
        return {
            "token": token.get("access_token"),
            "refresh_token": token.get("refresh_token"),
            "token_uri": "https://oauth2.googleapis.com/token",
            "client_id": cfg["client_id"],
            "client_secret": cfg["client_secret"],
            "scopes": GOOGLE_SCOPES,
        }

    # ------------------------------------------------------------------
    # Calendar reads
    # ------------------------------------------------------------------

    def get_calendar_timezone(self, token_json: Dict) -> Tuple[Optional[str], Optional[Dict]]:
        """Read the user's primary-calendar IANA timezone from the events.list response.

        The `timeZone` field is returned on every events.list response under the
        calendar.events scope — no extra scope or metadata call needed.
        """
        service, refreshed = self.build_service(token_json)
        resp = service.events().list(calendarId="primary", maxResults=1, singleEvents=True).execute()
        return resp.get("timeZone"), refreshed

    def list_events(
        self,
        token_json: Dict,
        *,
        time_min_rfc3339: str,
        time_max_rfc3339: str,
        calendar_id: str = "primary",
    ) -> Tuple[List[CalEvent], Optional[Dict]]:
        """All events in the window (fixed user commitments + Sage's own). Single-instance, time-ordered."""
        service, refreshed = self.build_service(token_json)
        resp = (
            service.events()
            .list(
                calendarId=calendar_id,
                timeMin=time_min_rfc3339,
                timeMax=time_max_rfc3339,
                singleEvents=True,
                orderBy="startTime",
                showDeleted=False,
            )
            .execute()
        )
        events = [_to_cal_event(item) for item in resp.get("items", [])]
        return events, refreshed

    def list_managed_events(
        self,
        token_json: Dict,
        *,
        time_min_rfc3339: str,
        time_max_rfc3339: str,
        calendar_id: str = "primary",
    ) -> Tuple[List[CalEvent], Optional[Dict]]:
        """Only Sage-created events in the window (server-side filtered by the provenance tag)."""
        service, refreshed = self.build_service(token_json)
        resp = (
            service.events()
            .list(
                calendarId=calendar_id,
                timeMin=time_min_rfc3339,
                timeMax=time_max_rfc3339,
                singleEvents=True,
                orderBy="startTime",
                showDeleted=False,
                privateExtendedProperty=SAGE_MANAGED_FILTER,
            )
            .execute()
        )
        events = [_to_cal_event(item) for item in resp.get("items", [])]
        return events, refreshed

    # ------------------------------------------------------------------
    # Calendar writes (all provenance-tagged; deletes are soft)
    # ------------------------------------------------------------------

    def insert_event(
        self,
        token_json: Dict,
        *,
        block_id: str,
        title: str,
        start_rfc3339: str,
        end_rfc3339: str,
        tz: str,
        calendar_id: str = "primary",
    ) -> Tuple[CalEvent, Optional[Dict]]:
        """Create a Sage-tagged timed event."""
        service, refreshed = self.build_service(token_json)
        body = {
            "summary": title,
            "start": {"dateTime": start_rfc3339, "timeZone": tz},
            "end": {"dateTime": end_rfc3339, "timeZone": tz},
            "extendedProperties": {
                "private": {SAGE_MANAGED_KEY: "true", SAGE_BLOCK_ID_KEY: block_id}
            },
        }
        created = service.events().insert(calendarId=calendar_id, body=body).execute()
        return _to_cal_event(created), refreshed

    def patch_event(
        self,
        token_json: Dict,
        *,
        google_event_id: str,
        etag: Optional[str],
        fields: Dict[str, Any],
        tz: str,
        calendar_id: str = "primary",
    ) -> Tuple[CalEvent, Optional[Dict]]:
        """Patch a Sage-managed event. Verifies provenance, then applies with an etag precondition.

        ``fields`` may contain ``title``, ``start_rfc3339``, ``end_rfc3339``.
        Raises CalendarPermissionError if the target isn't Sage-managed,
        CalendarConflictError on etag mismatch (412), CalendarEventGoneError on 404/410.
        """
        service, refreshed = self.build_service(token_json)
        self._assert_managed(service, calendar_id, google_event_id)

        body: Dict[str, Any] = {}
        if "title" in fields:
            body["summary"] = fields["title"]
        if "start_rfc3339" in fields:
            body["start"] = {"dateTime": fields["start_rfc3339"], "timeZone": tz}
        if "end_rfc3339" in fields:
            body["end"] = {"dateTime": fields["end_rfc3339"], "timeZone": tz}

        updated = self._execute_with_etag(
            service.events().patch(calendarId=calendar_id, eventId=google_event_id, body=body),
            etag,
        )
        return _to_cal_event(updated), refreshed

    def soft_cancel_event(
        self,
        token_json: Dict,
        *,
        google_event_id: str,
        etag: Optional[str],
        calendar_id: str = "primary",
    ) -> Tuple[CalEvent, Optional[Dict]]:
        """Soft-delete a Sage-managed event via Google's own status='cancelled' (never hard delete)."""
        service, refreshed = self.build_service(token_json)
        self._assert_managed(service, calendar_id, google_event_id)
        updated = self._execute_with_etag(
            service.events().patch(
                calendarId=calendar_id, eventId=google_event_id, body={"status": "cancelled"}
            ),
            etag,
        )
        return _to_cal_event(updated), refreshed

    # ------------------------------------------------------------------
    # Google Tasks (read-only)
    # ------------------------------------------------------------------

    def list_open_tasks(self, token_json: Dict) -> Tuple[List[GoogleTask], Optional[Dict]]:
        """All incomplete tasks across every task list. Read-only."""
        service, refreshed = self.build_tasks_service(token_json)
        tasks: List[GoogleTask] = []
        lists_resp = service.tasklists().list(maxResults=100).execute()
        for tl in lists_resp.get("items", []):
            tl_id = tl["id"]
            tl_title = tl.get("title", "")
            page_token: Optional[str] = None
            while True:
                resp = (
                    service.tasks()
                    .list(
                        tasklist=tl_id,
                        showCompleted=False,
                        showHidden=False,
                        maxResults=100,
                        pageToken=page_token,
                    )
                    .execute()
                )
                for item in resp.get("items", []):
                    # Defensive: showCompleted=False should already exclude these.
                    if item.get("status") == "completed":
                        continue
                    tasks.append(
                        GoogleTask(
                            id=item["id"],
                            title=item.get("title", "").strip() or "(untitled task)",
                            due=item.get("due"),
                            notes=item.get("notes"),
                            tasklist_id=tl_id,
                            tasklist_title=tl_title,
                        )
                    )
                page_token = resp.get("nextPageToken")
                if not page_token:
                    break
        return tasks, refreshed

    def complete_task(
        self, token_json: Dict, *, tasklist_id: str, task_id: str
    ) -> Tuple[Any, Optional[Dict]]:
        """Mark a Google Task complete. Requires the read-write tasks scope."""
        service, refreshed = self.build_tasks_service(token_json)
        result = service.tasks().patch(
            tasklist=tasklist_id or "@default",
            task=task_id,
            body={"status": "completed"},
        ).execute()
        return result, refreshed

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _assert_managed(self, service: Any, calendar_id: str, google_event_id: str) -> None:
        """Fetch the event and refuse to proceed unless it carries the Sage provenance tag."""
        from googleapiclient.errors import HttpError

        try:
            event = service.events().get(calendarId=calendar_id, eventId=google_event_id).execute()
        except HttpError as exc:
            status = getattr(exc.resp, "status", None)
            if status in (404, 410):
                raise CalendarEventGoneError(str(exc)) from exc
            raise
        cal = _to_cal_event(event)
        if not cal.sage_managed:
            raise CalendarPermissionError(
                f"Refusing to modify event {google_event_id}: not created by Sage."
            )

    @staticmethod
    def _execute_with_etag(request: Any, etag: Optional[str]) -> Dict:
        """Execute a patch request with an If-Match etag precondition, mapping HTTP errors."""
        from googleapiclient.errors import HttpError

        if etag:
            # google-api-python-client forwards custom headers on the underlying http request.
            request.headers["If-Match"] = etag
        try:
            return request.execute()
        except HttpError as exc:
            status = getattr(exc.resp, "status", None)
            if status == 412:
                raise CalendarConflictError(str(exc)) from exc
            if status in (404, 410):
                raise CalendarEventGoneError(str(exc)) from exc
            raise


def _to_cal_event(item: Dict[str, Any]) -> CalEvent:
    """Normalize a Google Calendar event resource into a CalEvent."""
    start_obj = item.get("start", {}) or {}
    end_obj = item.get("end", {}) or {}
    all_day = "date" in start_obj
    private = ((item.get("extendedProperties") or {}).get("private")) or {}
    return CalEvent(
        id=item.get("id", ""),
        etag=item.get("etag"),
        title=item.get("summary", "") or "",
        start=start_obj.get("dateTime") or start_obj.get("date"),
        end=end_obj.get("dateTime") or end_obj.get("date"),
        status=item.get("status"),
        all_day=all_day,
        sage_managed=str(private.get(SAGE_MANAGED_KEY, "")).lower() == "true",
        sage_block_id=private.get(SAGE_BLOCK_ID_KEY),
    )
