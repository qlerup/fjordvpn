"""Isolated gateway UI checks; never starts a VPN or writes DNS."""
from pathlib import Path
import sys,tempfile,threading
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from playwright.sync_api import sync_playwright,expect
from werkzeug.serving import make_server
from app import create_app
from test_app import FakeRuntime,config

root=Path(__file__).resolve().parents[1]
with tempfile.TemporaryDirectory() as folder:
    app=create_app(folder,testing=True,runtime_factory=FakeRuntime);store=app.extensions['store']
    old=store.create({'name':'Seedbox'},config(1));store.power(old['id'],True)
    before=(store.directory(old['id'])/'profile.json').read_bytes()
    key=store.save_key({'name':'Example zone','zone_id':'a'*32,'token':'a'*40})
    server=make_server('127.0.0.1',0,app,threaded=True);threading.Thread(target=server.serve_forever,daemon=True).start()
    try:
        with sync_playwright() as p:
            browser=p.chromium.launch();page=browser.new_page(viewport={'width':1440,'height':1000})
            errors=[];page.on('pageerror',lambda e:errors.append(str(e)))
            page.goto(f'http://127.0.0.1:{server.server_port}')
            password=(Path(folder)/'initial-login.txt').read_text().splitlines()[1].split(': ',1)[1]
            page.locator('[name=username]').fill('admin');page.locator('[name=password]').fill(password)
            page.get_by_role('button',name='Log ind').click()
            page.locator('#new-gateway').click();dialog=page.locator('#gateway-dialog')
            expect(dialog).to_be_visible();expect(page.locator('#gateway-start')).not_to_be_checked()
            page.locator('#gateway-name').fill('Mine webapps')
            page.locator('#gateway-file').set_input_files({'name':'gateway.conf','mimeType':'text/plain','buffer':config(2)})
            page.locator('#gateway-key').select_option(key)
            rows=page.locator('.gateway-route');rows.first.locator('[data-gateway-field=domain]').fill('app.example.com')
            rows.first.locator('[data-gateway-field=host]').fill('192.168.1.110')
            rows.first.locator('[data-gateway-field=port]').fill('8099')
            page.locator('#gateway-add-route').click()
            rows.nth(1).locator('[data-gateway-field=domain]').fill('other.example.com')
            rows.nth(1).locator('[data-gateway-field=host]').fill('192.168.1.111')
            rows.nth(1).locator('[data-gateway-field=protocol]').select_option('https')
            expect(rows.nth(1).locator('[data-gateway-field=port]')).to_have_value('443')
            output=root/'test-results';output.mkdir(exist_ok=True)
            page.screenshot(path=str(output/'gateway-desktop.png'),full_page=True)
            page.set_viewport_size({'width':390,'height':844})
            assert dialog.evaluate('el=>el.scrollWidth<=el.clientWidth')
            page.screenshot(path=str(output/'gateway-mobile.png'),full_page=True)
            page.locator('#gateway-save').click();expect(dialog).not_to_be_visible()
            gateways=[v for v in store.all() if v.get('gateway',{}).get('enabled')]
            assert len(gateways)==1 and not gateways[0]['desired'] and len(gateways[0]['gateway']['routes'])==2
            assert (store.directory(old['id'])/'profile.json').read_bytes()==before
            card=page.locator('.vpn-card').filter(has_text='Mine webapps')
            expect(card).to_contain_text('app.example.com')
            card.get_by_role('button',name='Indstillinger',exact=True).click()
            expect(page.locator('#gateway-upload')).not_to_be_visible()
            expect(rows).to_have_count(2)
            rows.nth(1).get_by_role('button',name='Fjern mål',exact=True).click()
            page.locator('#gateway-save').click();expect(dialog).not_to_be_visible()
            assert len(store.get(gateways[0]['id'])['gateway']['routes'])==1
            page.reload();expect(card).to_contain_text('1 subdomæne')
            assert page.evaluate('document.documentElement.scrollWidth<=innerWidth')
            assert not errors,errors
            browser.close()
    finally:server.shutdown()
print('PASS: gateway create/edit, multiple targets, TLS fields, mobile, persistence; legacy VPN untouched.')
