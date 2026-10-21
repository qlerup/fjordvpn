"""Isolated gateway UI checks; never starts a VPN or writes DNS."""
from pathlib import Path
import copy
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
    # Original profile format, before lists existed. Editing must preserve it.
    legacy=store.create({'name':'Existing gateway','gateway':{'enabled':True,'key_id':key,
        'routes':[{'domain':'existing.example.com','host':'192.168.1.110','port':8097,'protocol':'http','fjordflix':True}],
        'minecraft':{'enabled':True,'domain':'old.nordprison.dk','host':'192.168.1.152','port':25566,'key_id':mc_key},
        'tcp':{'enabled':True,'host':'192.168.1.96','port':32400}}},config(3))
    server=make_server('127.0.0.1',0,app,threaded=True);threading.Thread(target=server.serve_forever,daemon=True).start()
    try:
        with sync_playwright() as p:
            browser=p.chromium.launch();page=browser.new_page(viewport={'width':1440,'height':1000})
            errors=[];page.on('pageerror',lambda e:errors.append(str(e)))
            page.goto(f'http://127.0.0.1:{server.server_port}')
            password=(Path(folder)/'initial-login.txt').read_text().splitlines()[1].split(': ',1)[1]
            page.locator('[name=username]').fill('admin');page.locator('[name=password]').fill(password)
            page.get_by_role('button',name='Log ind').click()
            page.wait_for_function("() => document.querySelector('#new-gateway')?.onclick != null")
            dialog=page.locator('#gateway-dialog');picker=page.locator('#gateway-target-dialog')
            def add(kind):
                page.locator('#gateway-add-target').click();expect(picker).to_be_visible()
                page.locator('#gateway-add-'+kind).click();expect(picker).not_to_be_visible()
            def save():
                page.locator('#gateway-save').click();expect(dialog).not_to_be_visible()
            existing=page.locator('.vpn-card').filter(has_text='Existing gateway')
            existing.get_by_role('button',name='Indstillinger',exact=True).click()
            expect(page.locator('.gateway-web-route')).to_have_count(1)
            expect(page.locator('.gateway-tcp-route')).to_have_count(1)
            expect(page.locator('.gateway-game-route')).to_have_count(1)
            expect(page.locator('.gateway-game-route')).to_contain_text('beholder den delte VPN-port')
            initial=copy.deepcopy(store.get(legacy['id'])['gateway'])
            save()
            assert store.get(legacy['id'])['gateway']==initial
            page.locator('#new-gateway').click()
            expect(page.locator('#gateway-routes .gateway-route')).to_have_count(0)
            expect(page.locator('#gateway-start')).not_to_be_checked()
            page.locator('#gateway-name').fill('Mine webapps')
            page.locator('#gateway-file').set_input_files({'name':'gateway.conf','mimeType':'text/plain','buffer':config(2)})
            page.locator('#gateway-key').select_option(key)
            page.locator('#gateway-auto-sync').uncheck()
            # Canceling the chooser must leave all cards untouched.
            page.locator('#gateway-add-target').click()
            for width in [1440,390]:
                page.set_viewport_size({'width':width,'height':1000})
                assert picker.evaluate('el=>el.scrollWidth<=el.clientWidth')
                (root/'test-results').mkdir(exist_ok=True)
                page.screenshot(path=str(root/'test-results'/f'gateway-chooser-{width}.png'),full_page=True)
            page.set_viewport_size({'width':1440,'height':1000})
            picker.get_by_role('button',name='Luk valg af mål').click()
            expect(page.locator('#gateway-routes .gateway-route')).to_have_count(0)
            add('web');web=page.locator('.gateway-web-route')
            web.first.locator('[data-gateway-field=domain]').fill('app.example.com')
            web.first.locator('[data-gateway-field=host]').fill('192.168.1.110')
            web.first.locator('[data-gateway-field=port]').fill('8099')
            web.first.locator('[data-gateway-field=fjordflix]').check()
            web.first.locator('[data-gateway-field=direct]').check()
            expect(web.first.locator('[data-gateway-field=fjordflix]')).not_to_be_checked()
            add('tcp');tcp=page.locator('.gateway-tcp-route')
            tcp.first.locator('[data-tcp-field=name]').fill('Plex')
            tcp.first.locator('[data-tcp-field=host]').fill('192.168.1.96')
            expect(tcp.first.locator('[data-tcp-field=port]')).to_have_value('32400')
            add('game');games=page.locator('.gateway-game-route')
            games.first.locator('[data-game-field=domain]').fill('mc.nordprison.dk')
            games.first.locator('[data-game-field=host]').fill('192.168.1.152')
            games.first.locator('[data-game-field=port]').fill('25566')
            games.first.locator('[data-game-field=key_id]').select_option(mc_key)
            add('web')
            web.nth(1).locator('[data-gateway-field=domain]').fill('other.example.com')
            web.nth(1).locator('[data-gateway-field=host]').fill('192.168.1.111')
            web.nth(1).locator('[data-gateway-field=protocol]').select_option('https')
            web.nth(1).locator('[data-gateway-field=tls_server_name]').fill('backend.example.com')
            expect(web.nth(1).locator('[data-gateway-field=port]')).to_have_value('443')
            add('tcp')
            tcp.nth(1).locator('[data-tcp-field=name]').fill('Another service')
            tcp.nth(1).locator('[data-tcp-field=host]').fill('192.168.1.97')
            tcp.nth(1).locator('[data-tcp-field=port]').fill('8080')
            add('game')
            games.nth(1).locator('[data-game-field=domain]').fill('mc2.nordprison.dk')
            games.nth(1).locator('[data-game-field=host]').fill('192.168.1.153')
            games.nth(1).locator('[data-game-field=key_id]').select_option(mc_key)
            output=root/'test-results';output.mkdir(exist_ok=True)
            for width in [1440,390]:
                page.set_viewport_size({'width':width,'height':1000})
                tcp.first.scroll_into_view_if_needed()
                assert dialog.evaluate('el=>el.scrollWidth<=el.clientWidth')
                page.screenshot(path=str(output/f'gateway-targets-{width}.png'),full_page=True)
            save()
            profile=next(v for v in store.all() if v['name']=='Mine webapps')
            saved=copy.deepcopy(profile['gateway'])
            assert len(saved['routes'])==len(saved['tcp']['targets'])==len(saved['gameservers'])==2
            assert saved['target_order']==['web:app.example.com','tcp:0','game:0','web:other.example.com','tcp:1','game:1']
            assert saved['minecraft']=={'enabled':False}
            assert saved['routes'][1]['tls_server_name']=='backend.example.com'
            card=page.locator('.vpn-card').filter(has_text='Mine webapps')
            expect(card).to_contain_text('6 mål')
            card.get_by_role('button',name='Indstillinger',exact=True).click()
            expect(page.locator('#gateway-upload')).not_to_be_visible()
            expect(page.locator('#gateway-auto-sync')).not_to_be_checked()
            assert page.locator('#gateway-routes .gateway-route').evaluate_all("rows=>rows.map(row=>row.dataset.targetType)")==['web','tcp','game','web','tcp','game']
            save()
            assert store.get(profile['id'])['gateway']==saved
            # Removing one target of each extra-port type preserves all siblings.
            card.get_by_role('button',name='Indstillinger',exact=True).click()
            tcp.first.get_by_role('button',name='Fjern TCP-mål').click()
            games.first.get_by_role('button',name='Fjern gameserver').click()
            save();remaining=store.get(profile['id'])['gateway']
            assert remaining['routes']==saved['routes']
            assert remaining['tcp']['targets']==saved['tcp']['targets'][1:]
            assert remaining['gameservers']==saved['gameservers'][1:]
            assert store.get(legacy['id'])['gateway']==initial
            assert (store.directory(old['id'])/'profile.json').read_bytes()==before
            # Independent copy controls, readiness and responsive card rendering.
            example=copy.deepcopy(profile)
            example.update(desired=True,state='online',relay_active=True,public_ip='1.1.1.1',public_port=40000)
            example['gateway_status']={'state':'ready','message':'Alle domæner er online.','routes':{
                row['domain']:{'state':'ready','message':'Forbindelse kontrolleret.'}
                for row in saved['routes']+saved['gameservers']}}
            example['tcp_status']={'0':{'state':'ready','public_port':62000,'message':'TCP aktiv.'},
                '1':{'state':'error','public_port':None,'message':'Afventer port.'},
                'g0':{'state':'ready','public_port':62002,'message':'TCP aktiv.'},
                'g1':{'state':'ready','public_port':62003,'message':'TCP aktiv.'}}
            page.evaluate("profile=>document.querySelector('#managed-grid').replaceChildren(card(profile))",example)
            expect(page.get_by_role('button',name='Kopiér TCP-adresse for Plex',exact=True)).to_have_count(1)
            expect(page.get_by_role('button',name='Kopiér TCP-adresse for Another service',exact=True)).to_have_count(0)
            expect(page.locator('#managed-grid')).to_contain_text('1.1.1.1:62002')
            for width in [390,1440]:
                page.set_viewport_size({'width':width,'height':1000})
                assert page.evaluate('document.documentElement.scrollWidth<=innerWidth')
                page.screenshot(path=str(output/f'gateway-status-{width}.png'),full_page=True)
            assert not errors,errors
            browser.close()
    finally:server.shutdown()
print('PASS: mixed target chooser, multiple TCP/game targets, legacy preservation, no-op saves, independent removal, mobile/desktop.')
