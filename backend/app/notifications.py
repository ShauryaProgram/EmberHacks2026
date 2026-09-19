from __future__ import annotations

import asyncio
import base64
import json
import os
import uuid
from pathlib import Path
from typing import Any, Dict

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec
from pywebpush import WebPushException, webpush

from .config import Settings
from .db import Database
from .utils import iso, utc_now


class PushService:
    def __init__(self, db: Database, settings: Settings):
        self.db = db
        self.settings = settings
        self.private_key, self.public_key = self._load_or_create_key(settings.vapid_key_path)

    @staticmethod
    def _load_or_create_key(path: Path) -> tuple[str, str]:
        path.parent.mkdir(parents=True, exist_ok=True)
        if path.exists():
            private = serialization.load_pem_private_key(path.read_bytes(), password=None)
        else:
            private = ec.generate_private_key(ec.SECP256R1())
            pem = private.private_bytes(
                serialization.Encoding.PEM,
                serialization.PrivateFormat.PKCS8,
                serialization.NoEncryption(),
            )
            temporary = path.with_suffix(".tmp")
            temporary.write_bytes(pem)
            os.chmod(temporary, 0o600)
            temporary.replace(path)
        numbers = private.public_key().public_numbers()
        point = b"\x04" + numbers.x.to_bytes(32, "big") + numbers.y.to_bytes(32, "big")
        public = base64.urlsafe_b64encode(point).rstrip(b"=").decode("ascii")
        return str(path), public

    def subscribe(self, endpoint: str, p256dh: str, auth: str, user_agent: str = "") -> Dict[str, Any]:
        subscription_id = str(uuid.uuid4())
        created = iso(utc_now())
        with self.db.transaction() as connection:
            connection.execute(
                """INSERT INTO push_subscriptions(id,endpoint,p256dh,auth,user_agent,created_at)
                   VALUES(?,?,?,?,?,?) ON CONFLICT(endpoint) DO UPDATE SET p256dh=excluded.p256dh,
                   auth=excluded.auth,user_agent=excluded.user_agent,failure_count=0""",
                (subscription_id, endpoint, p256dh, auth, user_agent, created),
            )
            row = connection.execute("SELECT id,endpoint,created_at FROM push_subscriptions WHERE endpoint=?", (endpoint,)).fetchone()
        return dict(row)

    async def dispatch_due(self) -> Dict[str, int]:
        now = iso(utc_now())
        reminders = self.db.fetch_all(
            """SELECT * FROM reminders WHERE status='pending'
               AND COALESCE(snoozed_until, notify_at) <= ? ORDER BY notify_at LIMIT 100""",
            (now,),
        )
        subscriptions = self.db.fetch_all("SELECT * FROM push_subscriptions")
        if not subscriptions:
            return {"due": len(reminders), "delivered": 0, "subscriptions": 0}
        delivered = 0
        for reminder in reminders:
            payload = json.dumps({
                "title": reminder["title"],
                "body": reminder["body"][:500],
                "tag": reminder["source_key"],
                "data": {"eventId": reminder.get("event_id"), "reminderId": reminder["id"], "url": "/calendar"},
            })
            successes = 0
            for subscription in subscriptions:
                ok = await asyncio.to_thread(self._send, subscription, payload)
                successes += int(ok)
            if successes:
                delivered += 1
                self.db.execute(
                    "UPDATE reminders SET status='delivered',delivered_at=?,updated_at=? WHERE id=?",
                    (now, now, reminder["id"]),
                )
        return {"due": len(reminders), "delivered": delivered, "subscriptions": len(subscriptions)}

    def _send(self, subscription: Dict[str, Any], payload: str) -> bool:
        info = {
            "endpoint": subscription["endpoint"],
            "keys": {"p256dh": subscription["p256dh"], "auth": subscription["auth"]},
        }
        try:
            webpush(
                subscription_info=info,
                data=payload,
                vapid_private_key=self.private_key,
                vapid_claims={"sub": self.settings.vapid_subject},
                ttl=24 * 3600,
            )
        except WebPushException as exc:
            status = getattr(getattr(exc, "response", None), "status_code", None)
            if status in {404, 410}:
                self.db.execute("DELETE FROM push_subscriptions WHERE id=?", (subscription["id"],))
            else:
                self.db.execute(
                    "UPDATE push_subscriptions SET failure_count=failure_count+1 WHERE id=?",
                    (subscription["id"],),
                )
            return False
        self.db.execute(
            "UPDATE push_subscriptions SET last_success_at=?,failure_count=0 WHERE id=?",
            (iso(utc_now()), subscription["id"]),
        )
        return True

