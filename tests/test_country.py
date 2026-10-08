import io
import json
from unittest.mock import Mock
from relay.observer import PublicLocation


def test_country_matches_actual_egress_and_refreshes_when_ip_changes(monkeypatch):
    opener=Mock(side_effect=[io.BytesIO(json.dumps({'public_ip':'203.0.113.1','country':'France'}).encode()),
                            io.BytesIO(json.dumps({'public_ip':'203.0.113.1','country':'Germany'}).encode())])
    monkeypatch.setattr('relay.observer.urllib.request.urlopen',opener)
    location=PublicLocation()
    assert location.get('203.0.113.1')=='France'
    assert location.get('203.0.113.1')=='France'
    assert opener.call_count==1
    assert location.get('203.0.113.2')==''  # stale Gluetun result must not be reused
    assert location.get('203.0.113.2')==''
    assert opener.call_count==2


def test_location_outage_does_not_break_status_and_retries_are_bounded(monkeypatch):
    opener=Mock(side_effect=OSError('unavailable'))
    monkeypatch.setattr('relay.observer.urllib.request.urlopen',opener)
    location=PublicLocation()
    assert location.get('203.0.113.1')==''
    assert location.get('203.0.113.1')==''
    assert opener.call_count==1
