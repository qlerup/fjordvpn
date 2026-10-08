import ipaddress
import json
import os
from pathlib import Path
import secrets
import threading
import time

from flask import Flask, jsonify, redirect, render_template, request, session, url_for
from werkzeug.security import check_password_hash, generate_password_hash

from hub import Hub, HubError
from configuration import parse_networks
from runtime import Runtime
from store import Store, atomic
from gateway import GatewayStore


def create_app(root=None, testing=False, runtime_factory=Runtime):
    root = Path(root or os.environ.get('FJORDVPN_DATA','/var/lib/fjordvpn'))
    networks = parse_networks(os.environ.get('LAN_SUBNETS','192.168.1.0/24'))
    store = Store(root, networks)
    hub = Hub()
    auth_path = root/'auth.json'
    if not auth_path.exists():
        password = secrets.token_urlsafe(18)
        atomic(auth_path, json.dumps({'secret':secrets.token_hex(32),'password_hash':generate_password_hash(password) if not hub.enabled else ''}))
        if not hub.enabled:
            atomic(root/'initial-login.txt', 'Brugernavn: admin\nAdgangskode: '+password+'\n')
    auth = json.loads(auth_path.read_text())
    app = Flask(__name__)
    # Hub SSO may arrive from another site; Lax carries the session through its
    # top-level GET redirect. POST actions still require CSRF verification.
    app.config.update(SECRET_KEY=auth['secret'], MAX_CONTENT_LENGTH=131072,
        SESSION_COOKIE_NAME='fjordvpn_session',
        SESSION_COOKIE_HTTPONLY=True, SESSION_COOKIE_SAMESITE='Lax',
        PERMANENT_SESSION_LIFETIME=28800, TESTING=testing)
    hosts = {'127.0.0.1','localhost'} | {h.strip() for h in os.environ.get('UI_ALLOWED_HOSTS','').split(',') if h.strip()}
    runtime = runtime_factory(store)
    app.extensions.update(store=store, runtime=runtime, hub=hub)
    login_failures = {}
    auth_lock = threading.Lock()

    @app.before_request
    def guard():
        host=request.host.split(':')[0]
        allowed=host in hosts
        if not allowed and not os.environ.get('UI_ALLOWED_HOSTS'):
            try:
                allowed=any(ipaddress.IPv4Address(host) in n for n in networks)
            except ValueError:
                pass
        if not allowed:
            return 'Ukendt værtsnavn', 403
        if request.method == 'POST':
            expected = session.get('csrf','')
            actual = request.headers.get('X-CSRF-Token') or request.form.get('csrf','')
            if not expected or not secrets.compare_digest(expected,actual):
                return jsonify(error='Genindlæs siden og prøv igen.'),403
        public=request.endpoint in ('login','hub_login','logout','static','health')
        if not public and hub.enabled and session.get('authenticated'):
            try:
                hub.current(session.get('hub_uid'))
            except HubError as exc:
                if exc.status!=503:
                    session.clear()
                    if exc.status == 401:
                        session['hub_access_revoked'] = True
                        if not request.path.startswith('/api/'):
                            return redirect('/login?access_removed=1')
                        return jsonify(error_code='access_revoked', authenticated=False, error=str(exc)),401
                return jsonify(error=str(exc)),exc.status
        if not public and not session.get('authenticated'):
            if request.endpoint == 'api_hub_access':
                return jsonify(authenticated=False, error_code='access_revoked' if session.get('hub_access_revoked') else None), (401 if session.get('hub_access_revoked') else 200)
            if request.path.startswith('/api/'):
                return jsonify(error='Log ind for at fortsætte.'),401
            return redirect(url_for('login'))

    @app.after_request
    def headers(response):
        response.headers.update({'Cache-Control':'no-store','X-Content-Type-Options':'nosniff',
            'X-Frame-Options':'DENY','Referrer-Policy':'same-origin',
            'Content-Security-Policy':"default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'; form-action 'self'; base-uri 'none'"})
        return response

    @app.get('/api/auth/access')
    def api_hub_access():
        return jsonify(ok=True, authenticated=bool(session.get('authenticated')))

    @app.errorhandler(413)
    def too_large(_):
        return jsonify(error='Anmodningen er for stor. VPN-filer må højst være 16 KB; samlede indstillinger højst 128 KB.'),413

    @app.route('/login', methods=['GET','POST'])
    def login():
        session.setdefault('csrf',secrets.token_urlsafe(32))
        error = ''
        if request.method == 'POST':
            address = request.remote_addr
            with auth_lock:
                recent = [t for t in login_failures.get(address,[]) if time.time()-t<300]
                login_failures[address] = recent
                if len(recent)>=5:
                    return render_template('login.html',error='For mange mislykkede forsøg. Vent fem minutter.'),429
                user=None
                if hub.enabled:
                    try:
                        user=hub.authenticate(request.form.get('username',''),request.form.get('password',''))
                        valid=True
                    except HubError as exc:
                        valid=False
                        error=str(exc)
                else:
                    valid=request.form.get('username')=='admin' and check_password_hash(auth['password_hash'],request.form.get('password',''))
                if valid:
                    session.clear()
                    session.update(authenticated=True, csrf=secrets.token_urlsafe(32))
                    if user:
                        session['hub_uid']=user['id']
                    session.permanent=True
                    login_failures.pop(address,None)
                    return redirect('/')
                recent.append(time.time())
                error=error or 'Forkert brugernavn eller adgangskode.'
        return render_template('login.html',error=error, managed=hub.enabled)

    @app.get('/hub-login')
    def hub_login():
        if not hub.enabled:
            return redirect('/login')
        try:
            user=hub.sso(request.args.get('token',''))
        except HubError as exc:
            session.setdefault('csrf',secrets.token_urlsafe(32))
            return render_template('login.html',error=str(exc),managed=True),exc.status
        session.clear()
        session.update(authenticated=True,hub_uid=user['id'],csrf=secrets.token_urlsafe(32))
        session.permanent=True
        return redirect('/')

    @app.post('/logout')
    def logout():
        session.clear()
        return redirect('/login')

    @app.get('/healthz')
    def health():
        return {'ok':True}

    @app.get('/')
    def index():
        return render_template('index.html', csrf=session['csrf'], networks=', '.join(map(str,networks)))

    @app.get('/api/profiles')
    def profiles():
        with store.lock:
            return jsonify(profiles=[runtime.status(p) for p in store.all()])

    @app.get('/api/gateway')
    def gateway_status():
        with store.lock:
            gateway = getattr(runtime, 'gateway', None)
            return jsonify(gateway.status() if gateway else dict(GatewayStore(store).public(),
                state='off', message='Runtime er ikke startet.'))

    @app.post('/api/gateway')
    def gateway_settings():
        try:
            return jsonify(GatewayStore(store).save(request.get_json(silent=True)))
        except ValueError as exc:
            return jsonify(error=str(exc)), 400

    @app.post('/api/profiles')
    def create():
        try:
            uploaded = request.files.get('config')
            if not uploaded or not uploaded.filename.lower().endswith('.conf'):
                raise ValueError('Vælg en .conf-fil fra Proton VPN.')
            data = json.loads(request.form.get('settings','{}'))
            if not isinstance(data,dict):
                raise ValueError('Ugyldige indstillinger.')
            p=store.create(data,uploaded.read(16385))
            if data.get('start') is True:
                store.power(p['id'],True)
            return jsonify(id=p['id']),201
        except (ValueError,TypeError) as e:
            # Validation messages never contain uploaded values or keys.
            return jsonify(error=str(e) if isinstance(e,ValueError) and not isinstance(e,json.JSONDecodeError) else 'Ugyldige indstillinger.'),400

    @app.post('/api/profiles/<ident>/settings')
    def settings(ident):
        try:
            data=request.get_json(silent=True)
            if not isinstance(data,dict):
                raise ValueError('Ugyldige indstillinger.')
            store.edit(ident,data)
            return {'ok':True}
        except ValueError as e:
            return jsonify(error=str(e)),400

    @app.post('/api/profiles/<ident>/power')
    def power(ident):
        try:
            data=request.get_json(silent=True)
            if not isinstance(data,dict):
                raise ValueError('Ugyldigt valg.')
            store.power(ident,data.get('enabled'))
            return {'ok':True}
        except ValueError as e:
            return jsonify(error=str(e)),400

    stop_event=threading.Event()
    app.extensions['stop_event']=stop_event
    def worker():
        while not stop_event.is_set():
            try:
                runtime.tick()
            except Exception:
                app.logger.error('Driftskontrol fejlede; prøver igen.')
            stop_event.wait(3)

    if not testing:
        threading.Thread(target=worker,daemon=True).start()
    return app


if __name__=='__main__':
    from waitress import serve
    bind=os.environ.get('UI_BIND_IP','127.0.0.1')
    if bind=='auto':
        import subprocess
        info=json.loads(subprocess.check_output(['ip','-j','-4','addr','show','dev','eth0']))
        allowed=[ipaddress.IPv4Network(n) for n in os.environ.get('LAN_SUBNETS','192.168.1.0/24').split(',')]
        bind=next(a['local'] for interface in info for a in interface['addr_info']
                  if any(ipaddress.IPv4Address(a['local']) in n for n in allowed))
        os.environ['UI_ALLOWED_HOSTS']=','.join([bind,'127.0.0.1','localhost'])
    application=create_app()
    if application.extensions['runtime'].project:
        import signal
        def stop_managed(*_):
            application.extensions['stop_event'].set()
            application.extensions['runtime'].shutdown()
            raise SystemExit(0)
        signal.signal(signal.SIGTERM,stop_managed)
        signal.signal(signal.SIGINT,stop_managed)
    serve(application,host=bind,port=8088,threads=6)
