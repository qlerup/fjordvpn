import ipaddress
import json
import os
from pathlib import Path
import secrets
import threading
import time

from flask import Flask, jsonify, redirect, render_template, request, session, url_for
from werkzeug.security import check_password_hash, generate_password_hash

from runtime import Runtime
from store import Store, atomic


def create_app(root=None, testing=False, runtime_factory=Runtime):
    root = Path(root or os.environ.get('FJORDVPN_DATA','/var/lib/fjordvpn'))
    networks = [ipaddress.IPv4Network(n) for n in os.environ.get('LAN_SUBNETS','192.168.1.0/24').split(',')]
    store = Store(root, networks)
    auth_path = root/'auth.json'
    if not auth_path.exists():
        password = secrets.token_urlsafe(18)
        atomic(auth_path, json.dumps({'secret':secrets.token_hex(32),'password_hash':generate_password_hash(password)}))
        atomic(root/'initial-login.txt', 'Brugernavn: admin\nAdgangskode: '+password+'\n')
    auth = json.loads(auth_path.read_text())
    app = Flask(__name__)
    app.config.update(SECRET_KEY=auth['secret'], MAX_CONTENT_LENGTH=32768,
        SESSION_COOKIE_HTTPONLY=True, SESSION_COOKIE_SAMESITE='Strict',
        PERMANENT_SESSION_LIFETIME=28800, TESTING=testing)
    hosts = set(os.environ.get('UI_ALLOWED_HOSTS','127.0.0.1,localhost').split(','))
    runtime = runtime_factory(store)
    app.extensions.update(store=store, runtime=runtime)
    login_failures = {}
    auth_lock = threading.Lock()

    @app.before_request
    def guard():
        if request.host.split(':')[0] not in hosts:
            return 'Ukendt værtsnavn', 403
        if request.method == 'POST':
            expected = session.get('csrf','')
            actual = request.headers.get('X-CSRF-Token') or request.form.get('csrf','')
            if not expected or not secrets.compare_digest(expected,actual):
                return jsonify(error='Genindlæs siden og prøv igen.'),403
        if request.endpoint not in ('login','static','health') and not session.get('authenticated'):
            if request.path.startswith('/api/'):
                return jsonify(error='Log ind for at fortsætte.'),401
            return redirect(url_for('login'))

    @app.after_request
    def headers(response):
        response.headers.update({'Cache-Control':'no-store','X-Content-Type-Options':'nosniff',
            'X-Frame-Options':'DENY','Referrer-Policy':'same-origin',
            'Content-Security-Policy':"default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'; form-action 'self'; base-uri 'none'"})
        return response

    @app.errorhandler(413)
    def too_large(_):
        return jsonify(error='Konfigurationsfilen er for stor. Maksimum er 16 KB.'),413

    @app.route('/login', methods=['GET','POST'])
    def login():
        session.setdefault('csrf',secrets.token_urlsafe(32))
        error = ''
        if request.method == 'POST':
            address = request.remote_addr
            with auth_lock:
                recent = [t for t in login_failures.get(address,[]) if time.time()-t<300]
                login_failures[address] = recent
                if len(recent)>=8:
                    return render_template('login.html',error='For mange forsøg. Vent fem minutter.'),429
                valid = check_password_hash(auth['password_hash'],request.form.get('password',''))
                if request.form.get('username')=='admin' and valid:
                    session.clear()
                    session.update(authenticated=True, csrf=secrets.token_urlsafe(32))
                    session.permanent=True
                    login_failures.pop(address,None)
                    return redirect('/')
                recent.append(time.time())
                error='Forkert brugernavn eller adgangskode.'
        return render_template('login.html',error=error)

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

    def worker():
        while True:
            try:
                runtime.tick()
            except Exception:
                app.logger.error('Driftskontrol fejlede; prøver igen.')
            time.sleep(3)

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
    serve(create_app(),host=bind,port=8088,threads=6)
