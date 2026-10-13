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
    mc_key=store.save_key({'name':'Minecraft zone','zone_id':'c'*32,'token':'c'*40})
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
            expect(page.locator('#gateway-auto-sync')).to_be_checked()
            page.locator('#gateway-auto-sync').uncheck()
            page.locator('#gateway-name').fill('Mine webapps')
            page.locator('#gateway-file').set_input_files({'name':'gateway.conf','mimeType':'text/plain','buffer':config(2)})
            page.locator('#gateway-key').select_option(key)
            expect(page.locator('#gateway-minecraft-fields')).not_to_be_visible()
            page.locator('#gateway-minecraft-enabled').check()
            page.locator('#gateway-minecraft-domain').fill('mc.nordprison.dk')
            page.locator('#gateway-minecraft-host').fill('192.168.1.152')
            page.locator('#gateway-minecraft-port').fill('25566')
            page.locator('#gateway-minecraft-key').select_option(mc_key)
            rows=page.locator('.gateway-route');rows.first.locator('[data-gateway-field=domain]').fill('app.example.com')
            rows.first.locator('[data-gateway-field=host]').fill('192.168.1.110')
            rows.first.locator('[data-gateway-field=port]').fill('8099')
            page.locator('#gateway-add-route').click()
            rows.nth(1).locator('[data-gateway-field=domain]').fill('other.example.com')
            rows.nth(1).locator('[data-gateway-field=host]').fill('192.168.1.111')
            rows.nth(1).locator('[data-gateway-field=protocol]').select_option('https')
            expect(rows.nth(1).locator('[data-gateway-field=port]')).to_have_value('443')
            rows.nth(1).locator('[data-gateway-field=fjordflix]').check()
            output=root/'test-results';output.mkdir(exist_ok=True)
            page.screenshot(path=str(output/'gateway-desktop.png'),full_page=True)
            page.set_viewport_size({'width':390,'height':844})
            assert dialog.evaluate('el=>el.scrollWidth<=el.clientWidth')
            page.screenshot(path=str(output/'gateway-mobile.png'),full_page=True)
            page.locator('#gateway-save').click();expect(dialog).not_to_be_visible()
            gateways=[v for v in store.all() if v.get('gateway',{}).get('enabled')]
            assert len(gateways)==1 and not gateways[0]['desired'] and len(gateways[0]['gateway']['routes'])==2
            assert gateways[0]['gateway']['minecraft']=={'enabled':True,'domain':'mc.nordprison.dk','host':'192.168.1.152','port':25566,'key_id':mc_key}
            assert gateways[0]['gateway']['auto_sync'] is False
            assert gateways[0]['gateway']['routes'][1]['fjordflix'] is True
            assert (store.directory(old['id'])/'profile.json').read_bytes()==before
            card=page.locator('.vpn-card').filter(has_text='Mine webapps')
            expect(card).to_contain_text('app.example.com')
            card.get_by_role('button',name='Indstillinger',exact=True).click()
            expect(page.locator('#gateway-upload')).not_to_be_visible()
            expect(page.locator('#gateway-auto-sync')).not_to_be_checked()
            expect(page.locator('#gateway-minecraft-key')).to_have_value(mc_key)
            page.locator('#gateway-auto-sync').check()
            expect(rows).to_have_count(2)
            expect(rows.nth(1).locator('[data-gateway-field=fjordflix]')).to_be_checked()
            rows.nth(1).get_by_role('button',name='Fjern mål',exact=True).click()
            page.locator('#gateway-save').click();expect(dialog).not_to_be_visible()
            assert len(store.get(gateways[0]['id'])['gateway']['routes'])==1
            assert store.get(gateways[0]['id'])['gateway']['auto_sync'] is True
            page.reload();expect(card).to_contain_text('2 subdomæner')
            expect(card).to_contain_text('mc.nordprison.dk · Minecraft Java')
            assert page.evaluate('document.documentElement.scrollWidth<=innerWidth')
            # Display three independent route states, with accessible explanations.
            example=store.get(gateways[0]['id'])
            example['gateway']['minecraft']={'enabled':False}
            example.update(desired=True,state='online',relay_active=True,public_ip='1.1.1.1',public_port=40000)
            example['gateway']['routes']=[{**example['gateway']['routes'][0],'domain':name}
                for name in ['ready.example.com','pending.example.com','error.example.com']]
            example['gateway_status']={'state':'error','message':'Et domæne har en fejl.','routes':{
                'ready.example.com':{'state':'ready','message':'HTTPS svarer.'},
                'pending.example.com':{'state':'pending','message':'Afventer certifikat.'},
                'error.example.com':{'state':'error','message':'Cloudflare afviste nøglen.'}}}
            page.evaluate("profile=>document.querySelector('#managed-grid').replaceChildren(card(profile))",example)
            demo=page.locator('#managed-grid .vpn-card')
            for state in ['ready','pending','error']:
                expect(demo.locator('.route-status-dot.is-'+state)).to_have_count(1)
                assert demo.locator('.route-status-dot.is-'+state).bounding_box()['width']==8
            expect(demo.locator('.badge')).to_have_text('Fejl')
            expect(demo).to_contain_text('Opsætter: Afventer certifikat.')
            expect(demo).to_contain_text('Fejl: Cloudflare afviste nøglen.')
            expect(demo.locator('.route-status-dot.is-ready')).to_have_attribute('aria-label','Online: HTTPS svarer.')
            assert page.evaluate('document.documentElement.scrollWidth<=innerWidth')
            page.screenshot(path=str(output/'gateway-status-mobile.png'),full_page=True)
            page.set_viewport_size({'width':1440,'height':1000})
            page.screenshot(path=str(output/'gateway-status-desktop.png'),full_page=True)
            # Stopped profile deletion: cancel is harmless, confirm removes only it.
            app.extensions['runtime'].remove=lambda profile:None
            page.reload()
            old_card=page.locator('.vpn-card').filter(has_text='Seedbox')
            expect(old_card.get_by_role('button',name='Slet',exact=True)).to_be_disabled()
            card=page.locator('.vpn-card').filter(has_text='Mine webapps')
            card.get_by_role('button',name='Slet',exact=True).click()
            expect(page.locator('#delete-dialog')).to_contain_text('Cloudflare-nøgler, DNS-records og regler beholdes')
            page.locator('#delete-dialog').get_by_role('button',name='Annuller',exact=True).click()
            assert store.get(gateways[0]['id'])
            page.set_viewport_size({'width':390,'height':844})
            card.get_by_role('button',name='Slet',exact=True).click()
            page.locator('#delete-confirm').click()
            expect(page.locator('#delete-dialog')).not_to_be_visible()
            expect(page.locator('.vpn-card').filter(has_text='Mine webapps')).to_have_count(0)
            assert (store.directory(old['id'])/'profile.json').read_bytes()==before
            assert len(store.keys())==2
            assert page.evaluate('document.documentElement.scrollWidth<=innerWidth')
            assert not errors,errors
            browser.close()
    finally:server.shutdown()
print('PASS: gateway create/edit, multiple targets, TLS fields, mobile, persistence; legacy VPN untouched.')
