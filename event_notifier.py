"""Durable, best-effort FjordHub Matrix notifications for managed apps.

A monitor never blocks the primary workload on hub network I/O. Its sending
thread retries failed events using the app's *existing* FjordHub API key.
"""
import json
import logging
import re
import threading
import time
import uuid
from pathlib import Path

from store import atomic  # FjordVPN's secure persistent writer.

LOG = logging.getLogger(__name__)
RSS_ACTIVE = {"downloading", "forcedDL", "metaDL", "forcedMetaDL", "stalledDL"}


class EventNotifier:
    def __init__(self, root, hub, app_id):
        self.path = Path(root) / "hub-notification-state.json"
        self.hub = hub
        self.app_id = app_id
        self.lock = threading.RLock()
        try:
            self.state = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError, TypeError):
            self.state = {}
        self.state.setdefault("pending", [])
        self.state.setdefault("vpn", {})
        self.state.setdefault("seed_seen", {})
        self.state.setdefault("seed_initialized", False)

    def _save(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        atomic(self.path, json.dumps(self.state, ensure_ascii=False))

    def _queue(self, kind, **fields):
        # Event ID is persisted before network I/O. FjordHub ignores replays.
        pending = self.state["pending"]
        if len(pending) >= 500:
            LOG.warning("FjordHub notification backlog full; keeping oldest pending events")
            return
        pending.append({"type": kind, "event_id": uuid.uuid4().hex, **fields})

    def flush(self):
        if not self.hub.enabled:
            return
        while True:
            with self.lock:
                if not self.state["pending"]:
                    return
                event = dict(self.state["pending"][0])
            try:
                self.hub.call("/api/hub/apps/notifications", event)
            except Exception:
                # No interruption of VPN or torrent operation if the hub is offline.
                return
            with self.lock:
                self.state["pending"] = [p for p in self.state["pending"]
                                         if p.get("event_id") != event["event_id"]]
                self._save()

    def run(self, stop):
        while not stop.is_set():
            try:
                self.flush()
            except Exception:
                LOG.exception("Notification queue delivery failed")
            stop.wait(10)

    def observe_vpn(self, statuses, now=None):
        """Alert only after a sustained failure; never for a deliberately off profile."""
        if not self.hub.enabled:
            return
        now = time.time() if now is None else now
        failed, recovered = [], []
        with self.lock:
            tracked = self.state["vpn"]
            before = json.dumps(tracked, sort_keys=True)
            active = set()
            for row in statuses:
                ident = row.get("id")
                if not isinstance(ident, str) or not ident or not row.get("desired"):
                    continue
                active.add(ident)
                item = tracked.setdefault(ident, {"first_bad": None, "alerted": False})
                online = row.get("state") == "online"
                if online:
                    if item["alerted"]:
                        recovered.append(str(row.get("name") or ident)[:90])
                    item["alerted"] = False
                    item["first_bad"] = None
                else:
                    if item["first_bad"] is None:
                        item["first_bad"] = now
                    threshold = 12 if row.get("state") == "error" else 60
                    if not item["alerted"] and now - item["first_bad"] >= threshold:
                        item["alerted"] = True
                        failed.append(str(row.get("name") or ident)[:90])
            for ident in list(tracked):
                if ident not in active:
                    del tracked[ident]
            if failed:
                self._queue("vpn_failed", profiles=failed[:20])
            if recovered:
                self._queue("vpn_recovered", profiles=recovered[:20])
            if failed or recovered or json.dumps(tracked, sort_keys=True) != before:
                self._save()

    def observe_seed(self, rows, feeds):
        """One start and one completion per tagged RSS torrent, across restarts."""
        if not self.hub.enabled:
            return
        with self.lock:
            known = self.state["seed_seen"]
            fresh = not self.state["seed_initialized"]
            modified = fresh
            feed_names = {str(p.get("id")): str(p.get("name") or "")[:90] for p in feeds}
            for row in rows:
                hash_id = str(row.get("hash") or "").lower()
                if not re.fullmatch(r"[0-9a-f]{40}|[0-9a-f]{64}", hash_id):
                    continue
                tags = str(row.get("tags") or "").split(",")
                feed_id = next((tag.strip()[len("FjordSeed-RSS-"):] for tag in tags
                                if tag.strip().startswith("FjordSeed-RSS-")), None)
                name = str(row.get("name") or "Ukendt fil").strip()[:200]
                complete = isinstance(row.get("progress"), (int, float)) and row["progress"] >= 0.99999
                started = row.get("state") in RSS_ACTIVE and not complete
                modified = modified or hash_id not in known
                entry = known.setdefault(hash_id, {"started": False, "completed": False, "seen": time.time()})
                # Existing torrents at first activation are not "new" notifications.
                if fresh:
                    entry["started"] = bool(started or complete)
                    entry["completed"] = bool(complete)
                    continue
                if started and not entry["started"]:
                    modified = True
                    entry["started"] = True
                    self._queue("rss_started" if feed_id is not None else "download_started", filename=name, feed=feed_names.get(feed_id, ""))
                if complete and not entry["completed"]:
                    modified = True
                    entry["completed"] = True
                    entry["started"] = True
                    self._queue("rss_completed" if feed_id is not None else "download_completed", filename=name, feed=feed_names.get(feed_id, ""))
            self.state["seed_initialized"] = True
            if len(known) > 3000:
                modified = True
                for key in sorted(known, key=lambda k: known[k].get("seen", 0))[:len(known)-2500]:
                    del known[key]
            if modified:
                self._save()
