<p align="center">
  <picture>
    <source media="(prefers-color-scheme: dark)" srcset="static/brand/fjordvpn-logo-light.png">
    <img src="static/brand/fjordvpn-logo-dark.png" alt="FjordVPN" width="440">
  </picture>
</p>

<p align="center">Flere Proton VPN-forbindelser. Ét lokalt overblik.<br>
Upload WireGuard-konfigurationer, se offentlige adresser og videresend TCP-trafik til dine tjenester.</p>

## Installér i FjordHub

FjordVPN er en app i [FjordHub](https://github.com/qlerup/fjordhub).

1. Opdatér appkataloget, og vælg **FjordVPN → Installér**.
2. Vælg web-port (standard **8098**), lokalnet i CIDR-format og datamappe.
3. Åbn appen fra FjordHub. SSO logger dig ind med din FjordHub-bruger.
4. Vælg **Ny VPN**, og upload en ny Proton WireGuard `.conf` med **NAT-PMP** slået til.
5. Vælg eventuelt et lokalt TCP-mål og start forbindelsen, når du er klar.

Kun FjordVPN-administratorer eller FjordHub-administratorer med appadgang får
adgang. FjordHub er loginmyndighed; lokale konti bruges ikke i en administreret
installation. Fjernet adgang og rolleændringer kontrolleres løbende.

Installationen opretter **ingen VPN-profiler** og starter **ingen VPN-tunnel**.
Eksisterende VPN-installationer på andre servere berøres ikke.

## Krav

- Linux med Docker og Docker Compose v2.
- Docker-værten skal have `/dev/net/tun` og understøtte `NET_ADMIN` i containere.
- Proton VPN-konfiguration med WireGuard, IPv4-server og NAT-PMP/port forwarding.
- Et privat IPv4-lokalnet, som ikke overlapper Protons `10.2.0.0/24`.

I Proxmox LXC skal TUN være givet videre til den LXC, som kører Docker.
På Proxmox-versioner med device passthrough kan en **ledig** `devN`-plads bruges,
fx `pct set <CTID> --dev0 /dev/net/tun`, hvis `dev0` ikke allerede er optaget.
Genstart derefter den berørte LXC på et passende tidspunkt. FjordVPN ændrer
ikke Proxmox-konfigurationen og genstarter ikke andre servere.

## Selvstændig Docker-installation

```sh
git clone https://github.com/qlerup/fjordvpn.git
cd fjordvpn
cp .env.example .env
# Ret DATA_DIR til en absolut sti og LAN_SUBNETS til dit lokalnet.
docker compose up -d --build
```

Åbn `http://SERVER-IP:8098`. Det første login er `admin`; en tilfældig adgangskode
gemmes lokalt i `DATA_DIR/initial-login.txt`. Der er ingen standardadgangskode.
Lad både `FJORDHUB_URL` og `FJORDHUB_API_KEY` være tomme ved selvstændig drift.

| Indstilling | Formål |
|---|---|
| `APP_PORT` | Webport, standard 8098 |
| `DATA_DIR` | Absolut værtssti til private VPN-konfigurationer og tilstand |
| `LAN_SUBNETS` | Tilladte private IPv4-net, kommaadskilt |
| `UI_ALLOWED_HOSTS` | Valgfri liste med domæner og IP'er; tom accepterer lokalnet-IP'er |
| `FJORDHUB_URL`, `FJORDHUB_API_KEY` | Udfyldes automatisk af FjordHub |

## Forbindelser og videresendelse

Hver profil får sin egen Gluetun-container og et TCP-relay i samme
netværksnamespace. Dashboardet forbliver tilgængeligt, selv om en VPN er slukket.
En profil kan sende den offentlige Proton-port til ét lokalt `IP:port`-mål.
En reverse proxy på målet kan fordele HTTPS-trafik mellem flere tjenester.

Relayet lukker ved et mislykket VPN-healthcheck og følger ændringer i den
tildelte port. Den lokale servers øvrige udgående trafik ændres ikke.
IPv6 udelades fra importerede profiler. OpenVPN og UDP-relay indgår ikke.
DNS og HTTPS-certifikater administreres separat; IP/port kan skifte hos Proton.

Brug ikke samme Proton-nøgle i to aktive installationer. Appen afviser dubletter
blandt sine egne profiler, men har ingen adgang til andre VPN-servere. Brug
separate konfigurationer, mens gamle installationer stadig kører.

## Subdomæner med Cloudflare Tunnel

Sektionen **Dine subdomæner** fordeler webtrafik til flere lokale IP-adresser
og porte. Eksempelvis `fjordlens.gleruphub.dk → http://192.168.1.50:3000` og
`fjordbudget.gleruphub.dk → http://192.168.1.60:8080`. Dette bruger en separat
Cloudflare Tunnel, uafhængigt af Proton. Ingen Proton-profil er nødvendig,
og funktionen ændrer ikke lokalservernes udgående trafik.

1. Tilføj dit domæne til Cloudflare. Installér `cloudflared` på din computer,
   kør `cloudflared tunnel login`, og opret en **dedikeret, lokalt administreret**
   tunnel med `cloudflared tunnel create fjordvpn`.
2. Vælg **Opsæt tunnel** i FjordVPN og indlæs den genererede
   `<tunnel-id>.json` fra `.cloudflared`-mappen. Brug ikke `cert.pem` eller
   et token fra en fjernadministreret dashboard-tunnel.
3. Vælg **Tilføj subdomæne**, og indtast hostname, lokal IPv4, port og lokal
   protokol (HTTP/HTTPS). Destinationen skal ligge i `LAN_SUBNETS` og kunne nås
   fra Docker-værten. Ved HTTPS skal tjenesten have et gyldigt, betroet certifikat;
   angiv eventuelt certifikatets domænenavn i det separate felt.
4. Opret en **proxied CNAME** i samme Cloudflare-konto for hvert subdomæne.
   Målet `<tunnel-id>.cfargotunnel.com` vises i FjordVPN. Alternativt kan du køre
   `cloudflared tunnel route dns fjordvpn fjordlens.gleruphub.dk` på computeren,
   hvor du loggede ind. FjordVPN ændrer ikke DNS-poster automatisk.
5. Start tunnelen i appen. Besøg hvert subdomæne for at kontrollere hele forbindelsen.

Cloudflare leverer offentlig HTTPS. Beskyt private tjenester med Cloudflare
Access eller tjenestens eget login, før du aktiverer adgang. Funktionen dækker
HTTP(S), inklusive WebSockets; den er ikke en generel TCP/UDP-gateway.
Cloudflares plan- og uploadgrænser gælder fortsat.

Regler og legitimationsoplysninger gemmes i `DATA_DIR/gateway.json` med samme
beskyttelse som VPN-profiler. Hemmeligheden returneres aldrig i API-svar.
Ukendte værtsnavne får 404. Gemte ændringer anvendes ved at genoprette kun
FjordVPNs egen Cloudflare-container; igangværende forbindelser kan derfor blive
afbrudt kortvarigt. Tilstanden **Container kører** bekræfter Docker-processen,
ikke DNS, Cloudflare-forbindelsen eller den lokale tjenestes tilgængelighed.

Første start downloader det versionsfastlåste `cloudflare/cloudflared:2026.9.3`.
Der offentliggøres ingen ekstra porte på Docker-værten. Stop af tunnelen bevarer
reglerne; sletning af en regel fjerner ikke DNS-posten. I Compose stoppes
Cloudflare-containeren sammen med appen og genstartes efter den gemte tilstand.
Ingen eksisterende profiler skal migreres. Ved rollback skal tunnelen stoppes
i UI, før en ældre FjordVPN-version installeres.

Efter opdatering af kildekoden bygges appen med `docker compose up -d --build`.
Læs også [Cloudflares vejledning til lokale tunneler](https://developers.cloudflare.com/tunnel/features/locally-managed-tunnels/create-local-tunnel/).

## Data, stop og afinstallation

`DATA_DIR` indeholder private nøgler, profiler og loginoplysninger. Beskyt backup
af denne mappe. Den monteres med samme værtssti i de enkelte VPN-containere.
Nøgler vises ikke i API-svar, browseren eller fejlbeskeder. Uploads fortolkes
strengt som konfiguration; shell-hooks og ekstra sektioner afvises.

I Docker/FjordHub stopper appens nedlukning også dens egne VPN-containere.
Den ønskede tænd/sluk-tilstand gemmes, så profiler genoptages ved opstart.
Appens børnecontainere er mærket med dens Compose-projekt. Afinstallation via
FjordHub fjerner runtime med `down --remove-orphans`, men bevarer data og nøgler.
`docker compose down --remove-orphans` gør det samme ved manuel drift.

Dashboardet har adgang til Docker-socket og skal behandles som administration
af Docker-værten. Brug det på et betroet lokalnet; sæt HTTPS og adgangskontrol op
før eventuel ekstern eksponering. Docker-data og UI har ingen Proxmox-nøgle.

Den alternative systemd-installation i `deploy/` kan bruges på en dedikeret LXC.
Her stopper stop af UI-servicen kun UI'et; sluk profiler i appen for også at stoppe
tunnelerne. Brug aldrig systemd og Docker-udgaven på samme datamappe samtidig.

## Ikoner og logoer

[Brandmappen](static/brand/) indeholder originale SVG-logoer til lys/mørk
baggrund, et transparent symbol, PNG-appikoner fra 16 til 1024 px, favicon og
webmanifest. De kan gendannes med `python tools/build_brand.py`.

## Udvikling og test

```sh
python -m venv .venv
.venv/bin/pip install -r requirements.txt
.venv/bin/pip install pytest playwright pillow
.venv/bin/playwright install chromium
.venv/bin/pytest tests -q
.venv/bin/python tests/browser_check.py
```

Tests dækker upload, validering, dubletter, login/CSRF, FjordHub-SSO og
adgangstilbagekaldelse, adskilte Docker-ressourcer og værtssti-mapping.
Browserkontrollen bruger syntetiske profiler ved desktop- og mobilbredde.
Pakkeinstallation og oprydning kontrolleres isoleret uden at starte en VPN.
