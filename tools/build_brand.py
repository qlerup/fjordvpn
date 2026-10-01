"""Build the original vector identity and export app/browser assets.

Run with Playwright Chromium and Pillow installed. No network/fonts required.
"""
from pathlib import Path
import json

ROOT=Path(__file__).resolve().parents[1]
OUT=ROOT/'static/brand'
OUT.mkdir(parents=True,exist_ok=True)

def mark(color='#67dec2',node='#e9fff8'):
    return f'''<path d="M128 35 211 72v55c0 45-33 78-83 99-50-21-83-54-83-99V72Z" fill="none" stroke="{color}" stroke-width="12" stroke-linejoin="round"/>
<path d="M88 174V84h84v23h-59v21h40v23h-40v23Z" fill="{color}"/>
<circle cx="169" cy="140" r="12" fill="{node}"/>'''

def svg(body,width=256,height=256):
    return f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}" role="img" aria-label="FjordVPN">{body}</svg>'

assets={
    'fjordvpn-mark.svg':svg(mark(node='#67dec2')),
    'fjordvpn-mark-dark.svg':svg(mark('#176e60','#176e60')),
    'fjordvpn-icon.svg':svg('<rect width="256" height="256" rx="58" fill="#101c25"/>'+mark()),
}
for theme,ink in [('light','#edf3f7'),('dark','#172c36')]:
    assets[f'fjordvpn-logo-{theme}.svg']=svg('<g transform="translate(4 0) scale(.65)">'+mark('#67dec2' if theme=='light' else '#176e60',ink)+'</g>'+f'<text x="190" y="116" fill="{ink}" font-family="Segoe UI,Arial,sans-serif" font-size="82" font-weight="650" letter-spacing="-3">Fjord<tspan fill="'+('#67dec2' if theme=='light' else '#176e60')+'">VPN</tspan></text>',580,170)
for name,source in assets.items():
    (OUT/name).write_text(source,encoding='utf-8')
(OUT/'site.webmanifest').write_text(json.dumps({'name':'FjordVPN','short_name':'FjordVPN','start_url':'/',
    'display':'standalone','background_color':'#0c1016','theme_color':'#101c25','icons':[
        {'src':'/static/brand/fjordvpn-icon-192.png','sizes':'192x192','type':'image/png'},
        {'src':'/static/brand/fjordvpn-icon-512.png','sizes':'512x512','type':'image/png'}]},indent=2))

if __name__=='__main__':
    from playwright.sync_api import sync_playwright
    from PIL import Image
    with sync_playwright() as p:
        browser=p.chromium.launch()
        page=browser.new_page()
        for size in (16,32,48,180,192,256,512,1024):
            page.set_viewport_size({'width':size,'height':size})
            page.set_content('<style>html,body{margin:0}svg{display:block;width:100vw;height:100vh}</style>'+assets['fjordvpn-icon.svg'])
            page.screenshot(path=str(OUT/f'fjordvpn-icon-{size}.png'),omit_background=True)
        for theme in ('light','dark'):
            page.set_viewport_size({'width':1160,'height':340})
            page.set_content('<style>html,body{margin:0}svg{display:block;width:100vw;height:100vh}</style>'+assets[f'fjordvpn-logo-{theme}.svg'])
            page.screenshot(path=str(OUT/f'fjordvpn-logo-{theme}.png'),omit_background=True)
        browser.close()
    with Image.open(OUT/'fjordvpn-icon-256.png') as image:
        image.save(OUT/'favicon.ico',sizes=[(16,16),(32,32),(48,48),(64,64),(128,128),(256,256)])
    print('Exported SVG logos, PNG icons and wordmarks, favicon and web manifest.')
