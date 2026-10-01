"""FjordHub is the identity authority for managed installations."""
import os
import threading
import time

import requests


class HubError(Exception):
    def __init__(self, message, status=403):
        self.status = status
        super().__init__(message)


class Hub:
    def __init__(self):
        self.url = os.environ.get('FJORDHUB_URL','').rstrip('/')
        self.key = os.environ.get('FJORDHUB_API_KEY','')
        self.app_id = os.environ.get('FJORDHUB_APP_ID','fjordvpn')
        self.enabled = bool(self.url or self.key)
        self.lock = threading.Lock()
        self.users = []
        self.expires = 0

    def call(self, path, payload=None, method='POST'):
        if not self.url or not self.key or self.app_id != 'fjordvpn':
            raise HubError('FjordHub-forbindelsen er ikke korrekt konfigureret.',503)
        data = dict(payload or {},app_id=self.app_id)
        try:
            with requests.Session() as client:
                client.trust_env = False
                response=client.request(method,self.url+path,headers={'X-Hub-Key':self.key},
                    timeout=5,allow_redirects=False,**({'params':data} if method=='GET' else {'json':data}))
            if response.status_code in (401,403):
                raise HubError('Login er udløbet, eller du mangler adgang i FjordHub.',401)
            result=response.json()
            if response.status_code != 200 or not isinstance(result,dict) or result.get('ok') is not True:
                raise HubError('FjordHub kunne ikke godkende forespørgslen.',503)
            return result
        except (requests.RequestException,ValueError):
            raise HubError('FjordHub kan ikke kontaktes. Prøv igen om lidt.',503) from None

    @staticmethod
    def administrator(user):
        if not isinstance(user,dict) or type(user.get('id')) is not int or user['id']<1:
            raise HubError('FjordHub returnerede en ugyldig bruger.',503)
        if user.get('must_change_password'):
            raise HubError('Skift først din midlertidige adgangskode i FjordHub.')
        if user.get('role')!='admin' and user.get('hub_role')!='admin':
            raise HubError('Kun administratorer har adgang til FjordVPN.')
        return user

    def current(self, uid, force=False):
        with self.lock:
            if force or time.monotonic() >= self.expires:
                response=self.call('/api/hub/apps/users',method='GET')
                if not isinstance(response.get('items'),list):
                    raise HubError('FjordHub returnerede en ugyldig brugerliste.',503)
                self.users=response['items']
                self.expires=time.monotonic()+5
            for user in self.users:
                if user.get('id')==uid:
                    return self.administrator(user)
        raise HubError('Din adgang til FjordVPN er fjernet i FjordHub.',401)

    def authenticate(self, username, password):
        user=self.administrator(self.call('/api/hub/apps/authenticate',{'username':username,'password':password}).get('user'))
        return self.current(user['id'],force=True)

    def sso(self, token):
        if not token or len(token)>256:
            raise HubError('Ugyldigt FjordHub-login.',401)
        user=self.administrator(self.call('/api/hub/sso-verify',{'token':token},method='GET'))
        return self.current(user['id'],force=True)
