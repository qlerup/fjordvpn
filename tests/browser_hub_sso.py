"""Real browser navigation from FjordHub to FjordVPN, with isolated state."""
import os
import sys
import tempfile
import threading
from pathlib import Path
from unittest.mock import Mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from flask import Flask
from playwright.sync_api import sync_playwright, expect
from werkzeug.serving import make_server, WSGIRequestHandler
from app import create_app
from test_app import FakeRuntime


class QuietHandler(WSGIRequestHandler):
    def log(self, *args, **kwargs):
        pass


os.environ.update(FJORDHUB_URL='http://unused.test', FJORDHUB_API_KEY='test-only-key')
with tempfile.TemporaryDirectory() as tmp:
    vpn = create_app(tmp, testing=True, runtime_factory=FakeRuntime)
    admin = {'id':7, 'username':'Test Admin', 'role':'admin'}
    vpn.extensions['hub'].sso = Mock(return_value=admin)
    vpn.extensions['hub'].current = Mock(return_value=admin)
    vpn_server = make_server('127.0.0.1', 0, vpn, threaded=True, request_handler=QuietHandler)
    vpn_url = f'http://127.0.0.1:{vpn_server.server_port}'
    hub = Flask('test-hub')
    @hub.get('/')
    def open_button():
        return f'''<button onclick="const p=window.open('about:blank');p.opener=null;p.location.href='{vpn_url}/hub-login?token=test-only-token'">Åbn</button>'''
    hub_server = make_server('127.0.0.1', 0, hub, threaded=True, request_handler=QuietHandler)
    for server in (vpn_server, hub_server):
        threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        with sync_playwright() as pw:
            browser = pw.chromium.launch(headless=True)
            context = browser.new_context()
            page = context.new_page()
            # localhost and 127.0.0.1 are different sites, as with a Hub domain
            # opening a VPN LAN address. Use the real popup/redirect/cookie flow.
            page.goto(f'http://localhost:{hub_server.server_port}/')
            with context.expect_page() as opened:
                page.get_by_role('button', name='Åbn').click()
            popup = opened.value
            expect(popup).to_have_url(vpn_url + '/')
            expect(popup.locator('.login-panel')).to_have_count(0)
            assert popup.evaluate("fetch('/api/profiles').then(r=>r.status)") == 200
            # Another Flask app on the same host uses the generic session cookie.
            context.add_cookies([{'name':'session', 'value':'another-app-session',
                                 'url':vpn_url, 'sameSite':'Lax'}])
            popup.reload()
            expect(popup).to_have_url(vpn_url + '/')
            assert popup.evaluate("fetch('/api/profiles').then(r=>r.status)") == 200
            vpn.extensions['hub'].sso.assert_called_once_with('test-only-token')
            browser.close()
    finally:
        for server in (vpn_server, hub_server):
            server.shutdown(); server.server_close()
print('FjordHub popup SSO, redirect and independent session cookie passed.')
