"""Isolated browser verification; never connects to Docker or starts a VPN."""
import io
import json
from pathlib import Path
import sys
import tempfile
import threading

from playwright.sync_api import sync_playwright
from werkzeug.serving import make_server

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from app import create_app
from test_app import FakeRuntime, config
from test_gateway import credentials

root=Path(__file__).resolve().parents[1]
(root/'test-results').mkdir(exist_ok=True)
with tempfile.TemporaryDirectory() as data:
    app=create_app(data,testing=True,runtime_factory=FakeRuntime)
    server=make_server('127.0.0.1',0,app,threaded=True)
    threading.Thread(target=server.serve_forever,daemon=True).start()
    password=(Path(data)/'initial-login.txt').read_text(encoding='utf-8').splitlines()[1].split(': ',1)[1]
    with sync_playwright() as p:
        browser=p.chromium.launch()
        page=browser.new_page(viewport={'width':1440,'height':1000})
        errors=[]
        page.on('pageerror',lambda e:errors.append(str(e)))
        page.goto(f'http://127.0.0.1:{server.server_port}')
        page.locator('[name=password]').fill(password)
        page.get_by_role('button',name='Log ind →').click()
        page.locator('#empty-state').wait_for(state='visible')
        page.screenshot(path=str(root/'test-results/desktop-empty.png'),full_page=True)
        page.locator('#new-button').click()
        page.locator('#profile-name').fill('Medieserver')
        page.locator('#profile-file').set_input_files({'name':'film.conf','mimeType':'text/plain','buffer':config()})
        page.locator('#profile-host').fill('192.168.1.110')
        page.locator('#profile-port').fill('443')
        page.locator('#relay-enabled').check()
        page.locator('#start-enabled').uncheck()
        page.locator('#save-button').click()
        page.get_by_role('heading',name='Medieserver',exact=True).wait_for()
        assert len(app.extensions['store'].all())==1
        assert app.extensions['store'].all()[0]['desired'] is False
        page.get_by_role('button',name='Indstillinger',exact=True).click()
        page.locator('#profile-host').fill('8.8.8.8')
        page.locator('#save-button').click()
        page.locator('#form-error').wait_for(state='visible')
        assert 'lokalnet' in page.locator('#form-error').inner_text()
        page.locator('#profile-host').fill('192.168.1.110')
        page.locator('#save-button').click()
        page.locator('#profile-dialog').wait_for(state='hidden')
        # A second profile and an attempted duplicate exercise multi-profile upload feedback.
        page.locator('#new-button').click()
        page.locator('#profile-name').fill('Minecraft')
        page.locator('#profile-file').set_input_files({'name':'minecraft.conf','mimeType':'text/plain','buffer':config()})
        page.locator('#save-button').click()
        page.locator('#form-error').wait_for(state='visible')
        assert 'allerede' in page.locator('#form-error').inner_text()
        page.locator('#profile-file').set_input_files({'name':'minecraft.conf','mimeType':'text/plain','buffer':config(2)})
        page.locator('#start-enabled').uncheck()
        page.locator('#save-button').click()
        page.get_by_role('heading',name='Minecraft',exact=True).wait_for()
        assert len(app.extensions['store'].all())==2
        # Cloudflare ingress: create two destinations, reject bad targets,
        # upload synthetic credentials without starting an external connection.
        page.locator('#route-new').click()
        page.locator('#route-hostname').fill('fjordlens.gleruphub.dk')
        page.locator('#route-host').fill('8.8.8.8')
        page.locator('#route-port').fill('3000')
        page.locator('#route-save').click()
        page.locator('#route-form-error').wait_for(state='visible')
        page.locator('#route-host').fill('192.168.1.50')
        page.locator('#route-save').click()
        page.locator('#route-dialog').wait_for(state='hidden')
        page.locator('#gateway-settings').click()
        page.locator('#gateway-file').set_input_files({'name':'tunnel.json','mimeType':'application/json','buffer':json.dumps(credentials()).encode()})
        page.locator('#gateway-save').click()
        page.locator('#gateway-dialog').wait_for(state='hidden')
        page.locator('#gateway-dns').wait_for(state='visible')
        assert credentials()['TunnelSecret'] not in page.content()
        page.locator('#route-new').click()
        page.locator('#route-hostname').fill('fjordbudget.gleruphub.dk')
        page.locator('#route-host').fill('192.168.1.60')
        page.locator('#route-port').fill('8080')
        page.locator('#route-save').click()
        page.locator('#route-dialog').wait_for(state='hidden')
        assert page.locator('.gateway-route').count()==2
        page.locator('#gateway-power').click()
        page.get_by_role('button',name='Stop tunnel',exact=True).wait_for()
        assert app.extensions['store'].root.joinpath('gateway.json').exists()
        page.locator('#gateway-power').click()
        page.get_by_role('button',name='Start tunnel',exact=True).wait_for()
        page.get_by_role('button',name='Redigér fjordlens.gleruphub.dk',exact=True).click()
        page.locator('#route-scheme').select_option('https')
        page.locator('#route-tls').fill('internal.gleruphub.dk')
        page.locator('#route-port').fill('443')
        page.locator('#route-save').click()
        page.locator('#route-dialog').wait_for(state='hidden')
        assert 'https://192.168.1.50:443' in page.locator('#gateway-routes').inner_text()
        page.screenshot(path=str(root/'test-results/desktop-profiles.png'),full_page=True)
        page.set_viewport_size({'width':390,'height':844})
        assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
        page.screenshot(path=str(root/'test-results/mobile-profiles.png'),full_page=True)
        page.get_by_role('button',name='Fjern fjordbudget.gleruphub.dk',exact=True).click()
        page.locator('#route-delete-confirm').click()
        page.locator('#route-delete-dialog').wait_for(state='hidden')
        assert page.locator('.gateway-route').count()==1
        page.locator('#route-new').click()
        assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
        page.screenshot(path=str(root/'test-results/mobile-route.png'),full_page=True)
        page.keyboard.press('Escape')
        page.locator('#mobile-help').click()
        page.locator('#help-dialog').wait_for(state='visible')
        page.keyboard.press('Escape')
        page.locator('#new-button').click()
        page.screenshot(path=str(root/'test-results/mobile-upload.png'),full_page=True)
        page.keyboard.press('Escape')
        assert not errors,errors
        browser.close()
    server.shutdown()
print('Desktop/mobile, upload, validation, duplicate protection and profile edits passed.')
