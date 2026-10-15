"""Notifying FjordHub must not interrupt primary app workloads."""
from types import SimpleNamespace
from event_notifier import EventNotifier


class Hub:
    enabled = True

    def __init__(self):
        self.calls = []
        self.fail = False

    def call(self, endpoint, event):
        if self.fail:
            raise ConnectionError("FjordHub offline")
        self.calls.append((endpoint, event))
        return {"ok": True}


def test_vpn_reports_sustained_multiple_failures_once_and_recovery(tmp_path):
    hub = Hub()
    notifier = EventNotifier(tmp_path, hub, "fjordvpn")
    bad = [{"id": "a", "name": "Proton A", "desired": True, "state": "error"},
           {"id": "b", "name": "Proton B", "desired": True, "state": "error"},
           {"id": "c", "name": "Off", "desired": False, "state": "error"}]
    notifier.observe_vpn(bad, now=1000)
    notifier.observe_vpn(bad, now=1005)
    assert not notifier.state["pending"]
    notifier.observe_vpn(bad, now=1013)
    assert len(notifier.state["pending"]) == 1
    assert notifier.state["pending"][0]["profiles"] == ["Proton A", "Proton B"]
    hub.fail = True
    notifier.flush()
    assert len(notifier.state["pending"]) == 1
    hub.fail = False
    notifier.flush()
    assert len(hub.calls) == 1
    again = EventNotifier(tmp_path, hub, "fjordvpn")
    again.observe_vpn(bad, now=1030)
    again.flush()
    assert len(hub.calls) == 1
    recovered = [dict(p, state="online") for p in bad]
    again.observe_vpn(recovered, now=1032)
    again.flush()
    assert hub.calls[-1][1]["type"] == "vpn_recovered"


def test_rss_notifications_fire_only_on_real_download_and_completion(tmp_path):
    hub = Hub()
    notifier = EventNotifier(tmp_path, hub, "fjordseed")
    rss = "FjordSeed-RSS-" + "a" * 32
    feeds = [{"id": "a"*32, "name": "My RSS"}]
    old = {"hash": "1"*40, "name": "old", "progress": 1, "state": "uploading", "tags": rss}
    notifier.observe_seed([old], feeds)  # Baseline, no flood on first startup.
    assert not notifier.state["pending"]
    new = {"hash": "2"*40, "name": "new Linux.iso", "progress": 0.1,
           "state": "downloading", "tags": rss}
    notifier.observe_seed([old, new], feeds)
    assert notifier.state["pending"][0]["type"] == "rss_started"
    notifier.flush()
    notifier.observe_seed([old, new], feeds)
    notifier.flush()
    assert len(hub.calls) == 1
    new["progress"] = 1
    new["state"] = "uploading"
    notifier.observe_seed([old, new], feeds)
    notifier.flush()
    assert [call[1]["type"] for call in hub.calls] == ["rss_started", "rss_completed"]
    assert hub.calls[1][1]["filename"] == "new Linux.iso"
    assert hub.calls[1][1]["feed"] == "My RSS"


def test_manual_downloads_recorded_for_today_query_without_rss_tag(tmp_path):
    hub = Hub()
    notifier = EventNotifier(tmp_path, hub, "fjordseed")
    notifier.observe_seed([], [])
    new = {"hash": "f"*40, "name": "manual", "progress": 1, "state": "uploading", "tags": ""}
    notifier.observe_seed([new], [])
    notifier.flush()
    assert hub.calls[-1][1]["type"] == "download_completed"
