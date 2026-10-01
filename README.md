# FjordVPN

Lokal webapp til flere uafhængige Proton WireGuard-forbindelser. Upload en `.conf`,
vælg navn og eventuelt et lokalt TCP-mål, og start forbindelsen fra UI'et.
Hver profil har sin egen Gluetun-container og relay i samme netværksnamespace.
UI'et kører uden for VPN-forbindelserne og er tilgængeligt, når de er slukket.

## Installation på denne server

- Proxmox `192.168.1.250`, ny unprivileged LXC **1014**, `fjordvpn`.
- UI: `http://192.168.1.182:8088` (DHCP; appen finder LAN-adressen ved opstart).
- 2 CPU, 2 GB RAM, 16 GB disk, TUN og Docker nesting.
- App: `/opt/fjordvpn`; drift: `systemctl status fjordvpn`.
- Indstillinger: `/etc/fjordvpn.env`.
- Ved aflevering er listen tom. Ingen nye VPN'er er startet.
- Eksisterende VM1012 og LXC1013 er ikke integreret eller ændret.

## Brug

1. Log ind med `admin` og adgangskoden i den separat afleverede loginfil.
2. Hent en ny WireGuard-konfiguration fra Proton med **NAT-PMP** aktiveret.
3. Vælg **Ny VPN**, navn og fil. Vælg eventuelt lokal IPv4 og TCP-port.
4. Vælg om VPN'en skal starte med det samme. Den offentlige IP/port vises,
   når tunnelen er klar. Hvis NAT-PMP ikke er klar, vises det som afventende.
5. **Indstillinger** ændrer målet, **Sluk** lukker tunnelen og videresendelsen.
   Konfigurationen bevares. Flere profiler kan være aktive samtidig.

Genbrug ikke samme Proton-nøgle i to aktive installationer. Appen afviser
dubletter inden for sine egne profiler; den har ikke adgang til gamle servere.
Brug nye konfigurationer, mens de eksisterende installationer stadig kører.

Denne version understøtter Proton WireGuard med IPv4-serveradresse, ét TCP-mål
pr. VPN og lokalnet angivet i `LAN_SUBNETS`. IPv6 fjernes fra importerede profiler.
OpenVPN, UDP-relay, DNS-opdatering og certifikatstyring indgår ikke. En reverse
proxy på det lokale mål kan fordele TCP/HTTPS-trafik mellem flere tjenester.
Videresendelse ændrer ikke den lokale servers øvrige udgående trafik.

## Drift og sikkerhed

Filer i `/var/lib/fjordvpn` indeholder VPN-nøgler og loginoplysninger. Bevar denne
mappe ved opdatering. Mapper har mode 0700, konfigurationer mode 0600. Nøgler
sendes ikke tilbage til browseren og medtages ikke i fejlbeskeder. Uploads
fortolkes strengt som konfiguration; hooks, scripts og ukendte felter afvises.

Dashboardet bruger login, CSRF, Host-kontrol, sessionscookie og loginbegrænsning.
Det bindes til containerens LAN-IPv4, ikke VPN-porten. Appbrugeren har Docker-adgang
i den dedikerede LXC, men ingen Proxmox-nøgle eller adgang til gamle VPN-servere.
Eksponer ikke dette HTTP-administrationsinterface via offentlig portforwarding.

Profilernes ønskede tænd/sluk-tilstand og lokale mål gemmes atomisk og overlever
genstart. Relay stopper, når VPN-healthcheck fejler, og følger den tildelte port.
Docker-ressourcer skal matche appens ejerskabslabels, før de ændres.
Gluetun bruger firewall/kill switch. Images hentes på forhånd ved installation.

```sh
systemctl status fjordvpn
journalctl -u fjordvpn -n 50 --no-pager
docker ps --filter label=dk.fjordvpn.managed=1
systemctl restart fjordvpn
```

`systemctl stop fjordvpn` stopper administrationen, men lader aktive VPN'er køre.
Sluk profiler fra UI'et, hvis selve tunnelerne også skal stoppes. Tag en beskyttet
backup af `/var/lib/fjordvpn`, før appkode ændres. Rollback af denne første
installation: stop den nye LXC1014; andre VPN-installationer er uafhængige.

## Udvikling og kontrol

```sh
python -m venv .venv
.venv/bin/pip install -r requirements.txt
pip install pytest playwright
playwright install chromium
pytest tests -q
python tests/browser_check.py
```

Testpakken bruger syntetiske konfigurationer og en simuleret Docker-klient.
Browserkontrollen afprøver login, oprettelse af to profiler, dubletafvisning,
målvalidering og redigering ved desktop- og mobilbredde uden at starte VPN'er.
Live-kontrol efter deployment dækker login, tom profiloversigt, API og genstart.
En rigtig tunnel/portvideresendelse skal afprøves med en ny brugeruploadet profil;
der er bevidst ikke startet en ny tunnel ved aflevering.
