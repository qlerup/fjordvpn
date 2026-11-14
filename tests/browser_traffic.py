"""Isolated live-traffic UI checks, without VPNs or notification delivery."""
import sys,tempfile,threading,time,os
from pathlib import Path
root=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(root),str(root/'tests')]
from app import create_app
from test_app import FakeRuntime,config
from playwright.sync_api import sync_playwright,expect
from werkzeug.serving import make_server
class DemoRuntime(FakeRuntime):
 def status(self,p):
  now=time.time();history=[dict(at=now-2*(29-i),download_mbps=1+i%4,upload_mbps=15+i%12,delivered_mbps=14+i%10) for i in range(30)]
  return dict(super().status(p),state='online',public_ip='185.111.109.2',public_port=58591,traffic=dict(history[-1],state='ready',history=history))
(root/'test-results').mkdir(exist_ok=True)
with tempfile.TemporaryDirectory() as data:
 app=create_app(data,testing=True,runtime_factory=DemoRuntime);store=app.extensions['store'];p=store.create({'name':'Plex - VPN'},config());store.power(p['id'],True)
 server=make_server('127.0.0.1',0,app,threaded=True);threading.Thread(target=server.serve_forever,daemon=True).start()
 try:
  with sync_playwright() as pw:
   browser=pw.chromium.launch(headless=True,executable_path=os.environ.get("FJORDVPN_BROWSER_EXECUTABLE"),args=["--no-sandbox"]);page=browser.new_page();errors=[];page.on('pageerror',lambda e:errors.append(str(e)))
   page.goto(f'http://127.0.0.1:{server.server_port}/')
   password=(Path(data)/'initial-login.txt').read_text().splitlines()[1].split(': ',1)[1]
   page.locator('[name=username]').fill('admin');page.locator('[name=password]').fill(password);page.locator('form button').click()
   expect(page.locator('.traffic-values')).to_be_visible();expect(page.locator('.traffic-chart polyline')).to_have_count(3)
   expect(page.locator('.traffic-panel')).to_contain_text('Leveret i snit:')
   for width in [1440,390]:
    page.set_viewport_size({'width':width,'height':1000});assert page.evaluate('document.documentElement.scrollWidth<=innerWidth');page.screenshot(path=str(root/'test-results'/f'traffic-{width}.png'),full_page=True)
   store.power(p['id'],False);page.evaluate('refresh()');expect(page.locator('.traffic-panel')).to_contain_text('VPN er slukket.');expect(page.locator('.traffic-chart')).to_have_count(0)
   assert not errors,errors
   browser.close();print('Desktop/mobile live rates, graph, offline state and JavaScript: OK')
 finally:server.shutdown()
