"""Run inside the built image with --network none and disposable TLS fixtures."""
import json,ssl,socket,socketserver,subprocess,tempfile,threading,time
from pathlib import Path
from http.server import BaseHTTPRequestHandler,HTTPServer
from gateway import caddy_config
from minecraft_gateway import probe,read_varint,read_exact,varint


routes=[{'domain':'app.example.com','host':'127.0.0.1','port':8201,'protocol':'http'},
        {'domain':'other.example.com','host':'127.0.0.1','port':8202,'protocol':'http','fjordflix':True}]
with tempfile.TemporaryDirectory() as folder:
    root=Path(folder)
    token=root/'token';token.write_text('a'*40)
    value=caddy_config(routes,8443,str(token),str(root/'storage'),cloudflare_ranges=['127.0.0.2/32'],minecraft={'enabled':True,'host':'127.0.0.1','port':8255})
    config=root/'caddy.json';config.write_text(json.dumps(value))
    subprocess.run(['caddy','validate','--config',str(config)],check=True,capture_output=True)
    # Replace public ACME with a short-lived fixture cert for the isolated test.
    value['apps']['tls']={'certificates':{'load_files':[{'certificate':'/test/cert.pem','key':'/test/key.pem'}]}}
    value['apps']['http']['servers']['gateway']['automatic_https']={'disable':True}
    config.write_text(json.dumps(value))
    servers=[]
    for port,label in [(8201,'FIRST'),(8202,'SECOND')]:
        class Handler(BaseHTTPRequestHandler):
            identity=label
            def do_GET(self):
                body=(self.identity+' '+self.path+' '+self.headers.get('Host','')+' '+self.headers.get('X-Forwarded-Proto','')).encode()
                self.send_response(200);self.send_header('Content-Length',str(len(body)));self.end_headers();self.wfile.write(body)
            def log_message(self,*args):pass
        server=HTTPServer(('127.0.0.1',port),Handler);servers.append(server)
        threading.Thread(target=server.serve_forever,daemon=True).start()
    class MinecraftHandler(socketserver.BaseRequestHandler):
        def handle(self):
            self.request.settimeout(3)
            packet=read_exact(self.request,read_varint(self.request))
            assert packet[0]==0 and b'mc.nordprison.dk' in packet
            assert read_exact(self.request,read_varint(self.request))==b'\x00'
            body=json.dumps({'version':{'name':'Test Paper 1.16.5','protocol':754}}).encode()
            response=b'\x00'+varint(len(body))+body
            self.request.sendall(varint(len(response))+response)
    minecraft_server=socketserver.ThreadingTCPServer(('127.0.0.1',8255),MinecraftHandler)
    threading.Thread(target=minecraft_server.serve_forever,daemon=True).start()
    process=subprocess.Popen(['caddy','run','--config',str(config)],stdout=subprocess.DEVNULL,stderr=subprocess.PIPE)
    try:
        context=ssl.create_default_context(cafile='/test/cert.pem')
        def get(domain,path='/probe?x=1',host=None,source='127.0.0.1',headers=''):
            with socket.create_connection(('127.0.0.1',8443),timeout=3,source_address=(source,0)) as raw:
                with context.wrap_socket(raw,server_hostname=domain) as connection:
                    connection.sendall(f'GET {path} HTTP/1.1\r\nHost: {host or domain}\r\nConnection: close\r\n{headers}\r\n'.encode())
                    body=b''
                    while True:
                        chunk=connection.recv(65536)
                        if not chunk:break
                        body+=chunk
                    return body.decode()
        deadline=time.monotonic()+10
        while True:
            try:first=get('app.example.com',source='127.0.0.2');break
            except OSError:
                if process.poll() is not None:
                    raise RuntimeError(process.stderr.read().decode())
                if time.monotonic()>deadline:raise
                time.sleep(.1)
        assert 'FIRST /probe?x=1 app.example.com https' in first,first
        denied=get('app.example.com',headers='X-Forwarded-For: 127.0.0.2\r\nCF-Connecting-IP: 127.0.0.2\r\nTrue-Client-IP: 127.0.0.2\r\n')
        assert '403' in denied.splitlines()[0] and 'FIRST' not in denied,denied
        mixed=get('other.example.com',host='app.example.com',headers='X-Forwarded-For: 127.0.0.2\r\n')
        assert ('403' in mixed.splitlines()[0] or '421' in mixed.splitlines()[0]) and 'FIRST' not in mixed,mixed
        second=get('other.example.com',path='/media/probe?x=1',host='other.example.com:8443');assert 'SECOND /media/probe?x=1 other.example.com:8443 https' in second,second
        blocked=get('other.example.com',path='/');assert '404' in blocked.splitlines()[0] and 'SECOND' not in blocked,blocked
        unknown=get('app.example.com',host='unconfigured.example.com')
        assert '404' in unknown.splitlines()[0] or '421' in unknown.splitlines()[0],unknown
        assert 'FIRST' not in unknown and 'SECOND' not in unknown
        assert probe('127.0.0.1',8443,'mc.nordprison.dk')=={'name':'Test Paper 1.16.5','protocol':754}
        print('PASS: built Caddy + Cloudflare module, trusted TLS, hostname routes, original Host/path, forwarded HTTPS, unknown-host rejection; web source-IP restriction, forged-header/SNI bypass rejection, direct media preserved; Minecraft Java on the same port.')
    finally:
        process.terminate();process.wait(5)
        minecraft_server.shutdown();minecraft_server.server_close()
        for server in servers:server.shutdown()
