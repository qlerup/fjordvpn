"""Run inside the built image with --network none and disposable TLS fixtures."""
import json,ssl,socket,subprocess,tempfile,threading,time
from pathlib import Path
from http.server import BaseHTTPRequestHandler,HTTPServer
from gateway import caddy_config


routes=[{'domain':'app.example.com','host':'127.0.0.1','port':8201,'protocol':'http'},
        {'domain':'other.example.com','host':'127.0.0.1','port':8202,'protocol':'http','fjordflix':True}]
with tempfile.TemporaryDirectory() as folder:
    root=Path(folder)
    token=root/'token';token.write_text('a'*40)
    value=caddy_config(routes,8443,str(token),str(root/'storage'))
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
    process=subprocess.Popen(['caddy','run','--config',str(config)],stdout=subprocess.DEVNULL,stderr=subprocess.PIPE)
    try:
        context=ssl.create_default_context(cafile='/test/cert.pem')
        def get(domain,path='/probe?x=1',host=None):
            with socket.create_connection(('127.0.0.1',8443),timeout=3) as raw:
                with context.wrap_socket(raw,server_hostname=domain) as connection:
                    connection.sendall(f'GET {path} HTTP/1.1\r\nHost: {host or domain}\r\nConnection: close\r\n\r\n'.encode())
                    body=b''
                    while True:
                        chunk=connection.recv(65536)
                        if not chunk:break
                        body+=chunk
                    return body.decode()
        deadline=time.monotonic()+10
        while True:
            try:first=get('app.example.com');break
            except OSError:
                if process.poll() is not None:
                    raise RuntimeError(process.stderr.read().decode())
                if time.monotonic()>deadline:raise
                time.sleep(.1)
        assert 'FIRST /probe?x=1 app.example.com https' in first,first
        second=get('other.example.com',path='/media/probe?x=1',host='other.example.com:8443');assert 'SECOND /media/probe?x=1 other.example.com:8443 https' in second,second
        blocked=get('other.example.com',path='/');assert '404' in blocked.splitlines()[0] and 'SECOND' not in blocked,blocked
        unknown=get('app.example.com',host='unconfigured.example.com')
        assert '404' in unknown.splitlines()[0] or '421' in unknown.splitlines()[0],unknown
        assert 'FIRST' not in unknown and 'SECOND' not in unknown
        print('PASS: built Caddy + Cloudflare module, trusted TLS, hostname routes, original Host/path, forwarded HTTPS, unknown-host rejection.')
    finally:
        process.terminate();process.wait(5)
        for server in servers:server.shutdown()
