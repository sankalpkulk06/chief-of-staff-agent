import logging
from typing import Optional

from twilio.rest import Client

from app.storage.sqlite_registry import SQLiteRegistry

logger = logging.getLogger(__name__)


class WhatsAppService:
    USAGE_ALERT_THRESHOLDS = (25, 45, 49, 50)

    def __init__(
        self,
        account_sid: str,
        auth_token: str,
        from_number: str,
        usage_registry: Optional[SQLiteRegistry] = None,
        usage_alert_to: str = "",
        daily_message_limit: int = 50,
    ):
        self._client = Client(account_sid, auth_token)
        self._from_number = from_number
        self._usage_registry = usage_registry
        self._usage_alert_to = usage_alert_to
        self._daily_message_limit = daily_message_limit

    def send_message(self, to: str, body: str) -> None:
        for chunk in self.split_message(body):
            self._send_chunk(to=to, body=chunk, allow_usage_alerts=True)

    def send_media(self, to: str, media_url: str, caption: str = "") -> None:
        self._client.messages.create(
            from_=self._from_number,
            to=to,
            media_url=[media_url],
            body=caption,
        )
        count = self._record_usage()
        if count is not None:
            self._maybe_send_usage_alert(count)

    def split_message(self, text: str, limit: int = 1600) -> list[str]:
        if len(text) <= limit:
            return [text]

        chunks = []
        while len(text) > limit:
            boundary = text.rfind(". ", 0, limit)
            if boundary != -1:
                chunks.append(text[: boundary + 2].strip())
                text = text[boundary + 2 :].strip()
            else:
                word_boundary = text.rfind(" ", 0, limit)
                cut = word_boundary if word_boundary != -1 else limit
                chunks.append(text[:cut].strip())
                text = text[cut:].strip()

        if text:
            chunks.append(text)

        return chunks

    def _send_chunk(self, to: str, body: str, allow_usage_alerts: bool) -> None:
        self._client.messages.create(
            from_=self._from_number,
            to=to,
            body=body,
        )
        count = self._record_usage()
        if allow_usage_alerts and count is not None:
            self._maybe_send_usage_alert(count)

    def _record_usage(self) -> Optional[int]:
        if not self._usage_registry:
            return None
        return self._usage_registry.record_whatsapp_message_sent()

    def _maybe_send_usage_alert(self, count: int) -> None:
        if not self._usage_registry or not self._usage_alert_to:
            return
        if count not in self.USAGE_ALERT_THRESHOLDS:
            return
        if self._usage_registry.has_whatsapp_usage_alert_sent(count):
            return

        message = self._format_usage_alert(count)
        try:
            self._send_chunk(
                to=self._usage_alert_to,
                body=message,
                allow_usage_alerts=False,
            )
        except Exception:
            logger.exception("Failed to send WhatsApp usage alert at %s messages", count)
            return
        self._usage_registry.mark_whatsapp_usage_alert_sent(count)
        if count == self._daily_message_limit - 1:
            self._usage_registry.mark_whatsapp_usage_alert_sent(self._daily_message_limit)

    def _format_usage_alert(self, count: int) -> str:
        remaining = max(self._daily_message_limit - count, 0)
        if count == self._daily_message_limit - 1:
            return (
                f"Twilio WhatsApp usage alert: {count}/{self._daily_message_limit} "
                "messages used today before this alert. This alert uses one more "
                f"message, so Twilio should now be at {self._daily_message_limit}/"
                f"{self._daily_message_limit}."
            )
        return (
            f"Twilio WhatsApp usage alert: {count}/{self._daily_message_limit} "
            f"messages used today before this alert. {remaining} remaining."
        )
