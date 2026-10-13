# Opsætning og test af FjordVPN

Guiden gælder versionen med **Ny VPN-gateway**, **Opdater automatisk IP og port**
og **Opdater også Minecraft Java-port (SRV)**. Erstat `hoveddomaene.dk` og
`minecraftdomaene.dk` med dine domæner. IP/port-eksempler skal også tilpasses.

## 1. Opdatér appen først

Opdatér FjordVPN via FjordHub. En genstart af den nuværende manager stopper dens
administrerede VPN'er kort og starter de ønskede profiler igen. Lav derfor selve
opdateringen på et passende tidspunkt. De eksisterende profiler ændres ikke af
opdateringen. Derefter kan nye testprofiler oprettes uden at flytte de gamle.

## 2. Vælg den rigtige type forbindelse

| Tjeneste | Opsætning | Hvad går gennem VPN? |
|---|---|---|
| FjordSeed torrents | Behold den eksisterende Seedbox-VPN, TCP-relay fra | Torrent-download og upload |
| FjordSeed webside og andre webapps | Ny VPN-gateway | Adgang til websiden udefra |
| FjordFlix eksisterende streaming | Behold den eksisterende FjordFlix-VPN | Den nuværende direkte streamingforbindelse |
| Minecraft-hjemmeside | Ny VPN-gateway på Minecraft-domænet | HTTP/HTTPS til hjemmesiden |
| Minecraft Java-server | Ny VPN med TCP-videresendelse og Minecraft-SRV | Spilforbindelsen |

En web-gateway ændrer ikke de lokale serveres almindelige internettrafik.
Torrentklienten og web-gatewayen kan ikke dele den ene tildelte Proton-port.
Minecraft-spiltrafik er ikke HTTP og skal derfor ikke tilføjes som et webmål.
Bedrock/UDP understøttes ikke af det nuværende TCP-relay.

**Den nuværende gateway understøtter én Cloudflare-zone ad gangen.** Saml
webapps under hoveddomænet på én gateway. Minecraft-hjemmesidens andet domæne
kræver en anden gateway og nøgle/Zone ID. De to gateways har hver sin nye
Proton-konfiguration og kan have forskellige offentlige IP-adresser.
Alle subdomæner på samme gateway deler dens offentlige IP.

## 3. Forbered Cloudflare-nøgler

For hvert domæne: find Zone ID i Cloudflares domæneoversigt og opret et
API-token begrænset til den zone med:

- Zone / DNS / Edit
- Zone / Zone / Read
- Zone / Origin Rules / Edit (API-rettigheden hedder Origin Write)

Gem navn, Zone ID og token under **Cloudflare-nøgler** i FjordVPN.
Gamle DNS-only tokens kan mangle de to sidste rettigheder. Brug gerne en ny
nøgle til testen, så gamle profiler fortsat bruger deres hidtidige nøgle.
For en Minecraft Java TCP-profil er DNS Edit tilstrækkeligt.

Kontrollér **SSL/TLS → Full (strict)** i Cloudflare for web-gatewayens zone.
Det er en zoneindstilling, så vurder eksisterende proxied webapps før en ændring.
Brug nye testnavne, som ikke allerede er tildelt en eksisterende VPN.

## 4. Opret første web-gateway

1. Hos Proton: download en **ny** WireGuard-konfiguration fra en P2P-server
   med **NAT-PMP / port forwarding** aktiveret. Genbrug ikke en aktiv nøgle.
2. I FjordVPN: vælg **Ny VPN-gateway**.
3. Navngiv den fx `Webapps test`, upload filen og vælg hoveddomænets nøgle.
4. Tilføj først ét testmål:

   - Subdomæne: `test.hoveddomaene.dk`
   - Forbindelse til målet: `HTTP`
   - Lokal IP: IP'en på din test-webapp
   - Lokal port: webappens faktiske HTTP-port

5. Lad **Opdater automatisk IP og port i Cloudflare** være slået til.
6. Vælg **Start gateway og opsæt de valgte subdomæner i Cloudflare**, og opret.
7. Vent på Online/HTTPS aktiv og ajourført DNS/port. Første certifikatudstedelse
   kan tage ekstra tid. Åbn derefter `https://test.hoveddomaene.dk` uden port.
8. Brug **Indstillinger → Tilføj subdomæne** til flere webapps.

Cloudflare A-records behøver ikke oprettes manuelt for en web-gateway: den
opretter dem, hvis navnene er ledige. De bliver **Proxied / orange sky**.
Eksisterende CNAME/AAAA eller flere records på et navn afvises frem for at blive
fjernet. Brug HTTP til et lokalt HTTP-mål, også selv om den offentlige adresse
er HTTPS. Ved HTTPS-mål kontrolleres det lokale certifikat; angiv et andet
certifikatnavn, hvis det afviger fra det offentlige subdomæne.

En lokal app skal acceptere det nye værtsnavn og være opsat til sin offentlige
adresse. Ukendt værtsnavn/403, fejlredirect eller loginfejl kan kræve ændring
af appens allowed-hosts, proxy eller public-URL-indstillinger. Start derfor med
en ukritisk test-webapp, som allerede accepterer sit testnavn.

## 5. Hvad IP-/portautomatikken gør

| Ændring hos Proton | Automatisk handling |
|---|---|
| Ny offentlig IP | Gatewayens A-records opdateres til den nye IP |
| Ny offentlig port | Gatewayens egen Cloudflare Origin Rule opdateres |
| Ny offentlig port | Gatewayen lytter på den nye port |
| Certifikat udløber | Caddy fornyer det via DNS-01 |

Kontrollen køres cirka hvert 30. sekund. DNS-udbredelse og certifikater kan
tage længere tid. Porten gemmes **ikke** i A-recorden; Cloudflare modtager HTTPS
på 443 og sender videre til Protons port via Origin Rule. Derfor ændres URL'en
ikke. Cloudflares DNS-opslag viser normalt Cloudflare-IP'er, når proxy er slået
til; origin-IP'en ses i DNS-recordens indhold i dashboardet.

Når **Opdater automatisk IP og port** slås fra, foretager FjordVPN ingen
automatiske A-record-/Origin Rule-opdateringer for gatewayen. De eksisterende
records/regler bliver stående og skal opdateres manuelt. Certifikatfornyelse
kan fortsat oprette DNS-01 TXT-records. Gatewayens Sluk-knap stopper profilen.

## 6. Minecraft-hjemmesiden på det andet domæne

Gentag trin 3–4 med Minecraft-domænets Zone ID/nøgle og endnu en ny Proton-fil.
Tilføj fx `www-test.minecraftdomaene.dk → HTTP → lokal Minecraft-web-IP:port`.
Brug hjemmesidens webport, ikke spilserverens port 25565.
Lad automatikken være slået til på denne gateway også.

## 7. Minecraft Java-server med automatisk IP og spilport

Brug en ny testprofil; ændr ikke din nuværende Minecraft-installation under testen.

1. Vælg **Ny VPN** og upload endnu en ny Proton-konfiguration med NAT-PMP.
2. Sæt lokal IP til Java-serverens IP og lokal port til dens faktiske port,
   typisk `25565`. Slå **Aktivér TCP-videresendelse** til, og start profilen.
3. Kopiér dens offentlige VPN-IP. I Minecraft-domænets Cloudflare-zone oprettes
   præcis én **A-record**: `play-test → denne VPN-IP`, **DNS only / grå sky**.
4. Åbn profilens **Indstillinger**. Slå automatisk DNS til, vælg nøgle for
   Minecraft-zonen og indtast `play-test.minecraftdomaene.dk` som domæne.
5. Slå **Opdater også Minecraft Java-port (SRV)** til. FjordFlix-valget skal være fra.
6. Gem, og vent på **DNS og Minecraft Java-port er ajour**.

FjordVPN opdaterer nu A-recordens IP og opretter/opdaterer
`_minecraft._tcp.play-test.minecraftdomaene.dk` med Protons aktuelle offentlige
port og `play-test.minecraftdomaene.dk` som mål. Spillere indtaster selve
`play-test.minecraftdomaene.dk` i Java-klienten. En modstridende SRV-record
afvises; ingen konkurrerende records slettes.

## 8. Test uden at flytte gamle tjenester

1. Kontrollér, at Seedbox- og FjordFlix-profilerne stadig er Online.
2. Åbn test-webadresser fra mobilen på mobildata; kontrollér korrekt app/login.
3. Kontrollér A-records og gatewayens egen Origin Rule i Cloudflare.
4. Sluk/start **kun den nye testprofil**. IP/port ændres ikke nødvendigvis ved
   hver genforbindelse. Hvis de ændres, skal DNS/reglen følge efter automatisk.
5. For Java: kontrollér A og SRV efter genforbindelse og forbind med testnavnet.
6. Slå automatik fra, gem, og kontrollér at indstillingen bevares efter reload.
7. Behold testnavne, til du har verificeret alt. Migrering af rigtige navne og
   den eksisterende FjordFlix-streamadresse er et separat trin.

Ved fejl: kontrollér korrekt Cloudflare-zone, tokenrettigheder, HTTP/HTTPS-valg,
lokalt mål og tilladte værtsnavne. En gateway viser Online før alle certifikater
nødvendigvis er udstedt. Stop af gatewayen sletter ikke DNS eller Origin Rules.

Referencer: [Origin Rules](https://developers.cloudflare.com/rules/origin-rules/),
[destination port](https://developers.cloudflare.com/rules/origin-rules/features/#destination-port),
[SRV-records](https://developers.cloudflare.com/dns/manage-dns-records/reference/dns-record-types/#srv).
