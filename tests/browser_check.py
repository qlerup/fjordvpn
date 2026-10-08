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
        app.extensions['runtime'].status=lambda profile:dict(profile,state='online',public_ip='203.0.113.5',public_port=45001,country='PS',message='VPN klar')
        page.reload()
        page.get_by_text('VPN-land: Palæstina',exact=True).first.wait_for()
        assert page.locator('.country-location img').first.evaluate('(img)=>img.complete && img.naturalWidth>0')
        assert page.locator('.country-location img').first.get_attribute('src')=='/static/flags/ps.svg'
        page.screenshot(path=str(root/'test-results/desktop-profiles.png'),full_page=True)
        page.set_viewport_size({'width':390,'height':844})
        assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
        page.screenshot(path=str(root/'test-results/mobile-profiles.png'),full_page=True)
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
