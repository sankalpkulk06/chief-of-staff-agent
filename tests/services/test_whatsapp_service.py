from app.services.whatsapp_service import WhatsAppService
from app.storage.sqlite_registry import SQLiteRegistry


class _Messages:
    def __init__(self):
        self.created = []

    def create(self, **kwargs):
        self.created.append(kwargs)


class _Client:
    messages = _Messages()

    def __init__(self, account_sid, auth_token):
        self.account_sid = account_sid
        self.auth_token = auth_token
        self.messages = _Messages()


def test_whatsapp_service_records_successful_sends(monkeypatch, tmp_path):
    clients = []

    def fake_client(account_sid, auth_token):
        client = _Client(account_sid, auth_token)
        clients.append(client)
        return client

    monkeypatch.setattr("app.services.whatsapp_service.Client", fake_client)
    registry = SQLiteRegistry(tmp_path / "registry.db")
    try:
        service = WhatsAppService(
            account_sid="sid",
            auth_token="token",
            from_number="whatsapp:+from",
            usage_registry=registry,
            usage_alert_to="whatsapp:+me",
            daily_message_limit=50,
        )

        service.send_message("whatsapp:+to", "hello")

        usage = registry.get_whatsapp_usage_today(daily_limit=50)
        assert usage["sent_count"] == 1
        assert clients[0].messages.created[0]["to"] == "whatsapp:+to"
    finally:
        registry.close()


def test_whatsapp_service_sends_threshold_alert_once(monkeypatch, tmp_path):
    clients = []

    def fake_client(account_sid, auth_token):
        client = _Client(account_sid, auth_token)
        clients.append(client)
        return client

    monkeypatch.setattr("app.services.whatsapp_service.Client", fake_client)
    registry = SQLiteRegistry(tmp_path / "registry.db")
    try:
        for _ in range(24):
            registry.record_whatsapp_message_sent()

        service = WhatsAppService(
            account_sid="sid",
            auth_token="token",
            from_number="whatsapp:+from",
            usage_registry=registry,
            usage_alert_to="whatsapp:+me",
            daily_message_limit=50,
        )

        service.send_message("whatsapp:+to", "message 25")

        usage = registry.get_whatsapp_usage_today(daily_limit=50)
        sent_messages = clients[0].messages.created
        assert usage["sent_count"] == 26
        assert sent_messages[0]["body"] == "message 25"
        assert sent_messages[1]["to"] == "whatsapp:+me"
        assert "25/50" in sent_messages[1]["body"]
        assert registry.has_whatsapp_usage_alert_sent(25) is True
    finally:
        registry.close()


def test_whatsapp_service_49_alert_accounts_for_50th_message(monkeypatch, tmp_path):
    clients = []

    def fake_client(account_sid, auth_token):
        client = _Client(account_sid, auth_token)
        clients.append(client)
        return client

    monkeypatch.setattr("app.services.whatsapp_service.Client", fake_client)
    registry = SQLiteRegistry(tmp_path / "registry.db")
    try:
        for _ in range(48):
            registry.record_whatsapp_message_sent()

        service = WhatsAppService(
            account_sid="sid",
            auth_token="token",
            from_number="whatsapp:+from",
            usage_registry=registry,
            usage_alert_to="whatsapp:+me",
            daily_message_limit=50,
        )

        service.send_message("whatsapp:+to", "message 49")

        usage = registry.get_whatsapp_usage_today(daily_limit=50)
        alert = clients[0].messages.created[1]["body"]
        assert usage["sent_count"] == 50
        assert "49/50" in alert
        assert "now be at 50/50" in alert
        assert registry.has_whatsapp_usage_alert_sent(49) is True
        assert registry.has_whatsapp_usage_alert_sent(50) is True
    finally:
        registry.close()
