import io
from unittest.mock import Mock
from relay.observer import PublicLocation


def test_proton_geofeed_identifies_palestine_and_reuses_cache_after_ip_change(monkeypatch):
    feed=b'# prefix,country,region,city,postal\n205.147.0.0/16,FR,,,\n205.147.31.0/24,PS,PS-NBS,Nablus,\n185.159.157.0/24,CH,,,\n'
    opener=Mock(return_value=io.BytesIO(feed))
    monkeypatch.setattr('relay.observer.urllib.request.urlopen',opener)
    location=PublicLocation()
    assert location.get('205.147.31.2')=='PS'
    assert location.get('205.147.31.3')=='PS'
    assert location.get('185.159.157.5')=='CH'
    assert location.get('8.8.8.8')==''
    assert opener.call_count==1
    assert opener.call_args.args[0]==PublicLocation.URL


def test_location_outage_does_not_break_status_and_retries_are_bounded(monkeypatch):
    opener=Mock(side_effect=OSError('unavailable'))
    monkeypatch.setattr('relay.observer.urllib.request.urlopen',opener)
    location=PublicLocation()
    assert location.get('205.147.31.2')==''
    assert location.get('205.147.31.3')==''
    assert opener.call_count==1


def test_cache_expires_and_malformed_feed_never_reuses_old_country(monkeypatch):
    clock=Mock(return_value=100)
    opener=Mock(side_effect=[io.BytesIO(b'205.147.31.0/24,PS,,,\n'),io.BytesIO(b'bad,France,,,\n')])
    monkeypatch.setattr('relay.observer.time.monotonic',clock)
    monkeypatch.setattr('relay.observer.urllib.request.urlopen',opener)
    location=PublicLocation()
    assert location.get('205.147.31.2')=='PS'
    clock.return_value=22000
    assert location.get('205.147.31.2')==''
    assert location.get('127.0.0.1')==''
    assert opener.call_count==2
