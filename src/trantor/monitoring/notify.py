"""Operator notifications (Telegram) with deduplication and failure isolation.

- Delivery runs on a background thread with a bounded queue. A slow or failing
  Telegram API can never block the trading loop, risk enforcement or position
  monitoring.
- Every message has a dedup key reserved in the runtime database first, so the
  same event is not sent twice, including across restarts.
- The bot token never appears in messages, logs or stored errors.
- Notifications are one-way. Nothing received from Telegram is acted on.
"""

from __future__ import annotations

import json
import logging
import queue
import threading
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Protocol

from ..safety import redact_secrets

__all__ = ["Notifier", "TelegramTransport", "Transport"]

LOGGER = logging.getLogger("trantor.notify")


class Transport(Protocol):
    name: str

    def send(self, text: str) -> None: ...


class TelegramTransport:
    name = "telegram"

    def __init__(
        self,
        token: str,
        chat_id: str,
        *,
        timeout: float = 8.0,
        opener: Callable[..., Any] = urllib.request.urlopen,
    ) -> None:
        if not token or not chat_id:
            raise ValueError("Telegram token and chat id are required")
        self._token = token
        self._chat_id = chat_id
        self._timeout = timeout
        self._opener = opener

    def send(self, text: str) -> None:
        body = urllib.parse.urlencode(
            {"chat_id": self._chat_id, "text": text[:4000], "disable_web_page_preview": "true"}
        ).encode()
        request = urllib.request.Request(
            f"https://api.telegram.org/bot{self._token}/sendMessage",
            data=body,
            method="POST",
        )
        try:
            with self._opener(request, timeout=self._timeout) as response:
                payload = json.loads(response.read().decode("utf-8") or "{}")
        except (urllib.error.URLError, TimeoutError, OSError, ValueError) as exc:
            raise RuntimeError(redact_secrets(f"telegram delivery failed: {exc}", [self._token])) from None
        if not payload.get("ok"):
            raise RuntimeError(
                redact_secrets(f"telegram rejected message: {payload.get('description')}", [self._token])
            )


@dataclass
class _Message:
    key: str
    kind: str
    text: str


class Notifier:
    """Queue-backed, deduplicated notifier. Safe to call from any thread."""

    def __init__(
        self,
        store: Any,
        transport: Transport | None,
        *,
        secrets: list[str] | None = None,
        prefix: str = "TRANTOR",
        max_queue: int = 200,
    ) -> None:
        self.store = store
        self.transport = transport
        self.secrets = [item for item in (secrets or []) if item]
        self.prefix = prefix
        self._queue: queue.Queue[_Message | None] = queue.Queue(maxsize=max_queue)
        self._thread: threading.Thread | None = None
        self.dropped = 0
        self.failures = 0
        self.sent = 0

    @property
    def enabled(self) -> bool:
        return self.transport is not None

    def start(self) -> None:
        if self.transport is None or self._thread is not None:
            return
        self._thread = threading.Thread(target=self._run, name="trantor-notify", daemon=True)
        self._thread.start()

    def stop(self, timeout: float = 5.0) -> None:
        if self._thread is None:
            return
        try:
            self._queue.put_nowait(None)
        except queue.Full:
            pass
        self._thread.join(timeout)
        self._thread = None

    def notify(self, kind: str, key: str, text: str) -> str:
        """Queue a message. Returns 'queued', 'duplicate', 'disabled' or 'dropped'."""

        clean = redact_secrets(f"[{self.prefix}] {text}", self.secrets)
        try:
            claimed = self.store.claim_notification(f"{kind}:{key}", kind)
        except Exception:  # storage failure must not raise into trading code
            LOGGER.exception("notification dedup store failed")
            return "dropped"
        if not claimed:
            return "duplicate"
        if self.transport is None:
            self._finish(f"{kind}:{key}", "disabled", None)
            return "disabled"
        try:
            self._queue.put_nowait(_Message(f"{kind}:{key}", kind, clean))
        except queue.Full:
            self.dropped += 1
            self._finish(f"{kind}:{key}", "dropped_queue_full", None)
            return "dropped"
        return "queued"

    def deliver_pending_now(self) -> None:
        """Synchronously drain the queue (tests and shutdown)."""

        while True:
            try:
                message = self._queue.get_nowait()
            except queue.Empty:
                return
            if message is not None:
                self._deliver(message)

    def _run(self) -> None:
        while True:
            message = self._queue.get()
            if message is None:
                return
            self._deliver(message)

    def _deliver(self, message: _Message) -> None:
        assert self.transport is not None
        try:
            self.transport.send(message.text)
        except Exception as exc:
            self.failures += 1
            error = redact_secrets(str(exc), self.secrets)
            LOGGER.warning("notification %s failed: %s", message.kind, error)
            self._finish(message.key, "failed", error)
            return
        self.sent += 1
        self._finish(message.key, "sent", None)

    def _finish(self, key: str, status: str, error: str | None) -> None:
        try:
            self.store.finish_notification(key, status, error)
        except Exception:
            LOGGER.exception("notification status could not be stored")
