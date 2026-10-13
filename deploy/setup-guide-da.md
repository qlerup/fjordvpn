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
| Minecraft Java-server | Gatewayens Minecraft Java-indstilling eller separat TCP-profil | Spilforbindelsen |

En web-gateway ændrer ikke de lokale serveres almindelige internettrafik.
Torrentklienten og web-gatewayen kan ikke dele den ene tildelte Proton-port.
Minecraft-spiltrafik er ikke HTTP og skal derfor ikke tilføjes som et webmål.
Bedrock/UDP understøttes ikke af det nuværende TCP-relay.

**Gatewayens webmål bruger én Cloudflare-zone ad gangen.** Minecraft Java kan
bruge en anden zone og nøgle på samme gateway; se trin 13. Saml
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

## 7. Alternativ: separat Minecraft Java-VPN med automatisk IP og spilport

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

## 9. Saml FjordFlix-web og direkte video på gatewayens VPN

Gatewayindstillinger har nu et valg på hvert subdomæne:
**Direkte FjordFlix-streaming og automatisk streamport**. Brug det kun på
media-domænet. Der kan kun være én profil, som opdaterer FjordFlix-streamadressen.

For den eksisterende installation:

1. Opdatér FjordVPN via FjordHub til udgaven med streaming i gatewayen.
   Manageropdateringen kan kort afbryde administrerede VPN-forbindelser.
2. Behold `film.gleruphub.dk → HTTP → 192.168.1.110:8097` i gatewayen med
   streamingvalget slået fra. Den bruger Cloudflare-proxy og HTTPS uden port.
3. Åbn **Indstillinger** på den gamle FjordFlix-VPN og slå **Hold dette domænes
   IP opdateret** fra. Gem. Lad selve VPN'en køre til rollback; den må ikke
   konkurrere med gatewayen om DNS eller FjordFlix-streamadressen.
4. Tilføj `media.gleruphub.dk → HTTP → 192.168.1.110:8097` i gatewayen,
   og slå **Direkte FjordFlix-streaming og automatisk streamport** til på dette
   domæne. Lad gatewayens automatiske IP/portopdatering være slået til. Gem.
5. Gatewayen ændrer media-domænets eksisterende A-record til sin egen VPN-IP
   med **DNS only / grå sky**, udsteder/fornyer HTTPS-certifikatet og tester
   `/media/connection-check` med FjordFlix' midlertidige nonce. Når testen lykkes,
   sættes streamadressen til `https://media.gleruphub.dk:<gatewayens VPN-port>`.
   Hjemmesidens adresse beholdes. DNS/cache og certifikater kan give en kort
   afbrydelse; fejlet test beholder den hidtidige FjordFlix-streamadresse.
6. Vent på grøn status ved både film og media samt **Alle domæner er verificeret
   og online**, genindlæs
   FjordFlix, og test login og videoafspilning fra mobildata. Først derefter kan
   den gamle FjordFlix-VPN slukkes. Seedbox-VPN'en er en separat forbindelse.

Begge FjordFlix-domæner deler nu én VPN-konfiguration og én indgående port.
Kun webdomæner indgår i Cloudflares Origin Rule. Direkte video går uden om
Cloudflare-proxyen og bruger portnummer i streamadressen; porten gemmes ikke
i A-recorden. Gatewayen tillader kun `/media/*` på streamingdomænet.
Lokalt mål er FjordFlix-appen på port 8097, ikke den gamle HTTPS-relay på 443.

Ved genforbindelse opdateres DNS og streamport automatisk efter en ny verificeret
HTTPS-test. Hvis automatikken slås fra, stopper disse opdateringer, mens
certifikatfornyelse fortsætter. FjordFlix skal allerede have det valgte
media-domæne og en HTTPS-webadresse opsat; gatewayen overtager ikke et andet
streamdomæne uden at du først ændrer opsætningen i FjordHub.

Rollback: fjern media-ruten fra gatewayen, genaktivér den gamle FjordFlix-VPNs
automatiske DNS/streamsynkronisering, og vent på dens godkendte forbindelsestest.
Fjernede gatewayruter får deres ejede DNS-record slettet ved næste automatiske
synkronisering. Ved rollback kan den gamle DNS-only profil kræve, at du først
genskaber én A-record for media-domænet; den profil opretter ikke selv records.

## 10. Status på hvert gatewaydomæne

- Grøn prik: webdomænets offentlige HTTPS-adresse svarer, eller media-domænets
  HTTPS/nonce-test er godkendt og FjordFlix-streamadressen er opdateret.
- Orange prik: VPN, DNS, certifikat eller forbindelsestest er endnu ikke klar.
  Nye forbindelser får op til fem minutter til at falde på plads.
- Rød prik: opsætningsfejl eller en vedvarende forbindelsesfejl. Et tidligere
  fungerende domæne bliver rødt, hvis den næste kontrol fejler.

Forklaringen vises under orange/røde domæner og ved fokus/hover på prikken.
Gatewaykortet viser først Online, når alle domæner er grønne; den underliggende
VPN kan godt være online, mens domænerne stadig opsættes. Kontrollen sker
cirka hvert 30. sekund. En ændret opsætning eller VPN-IP/port kræver en ny
godkendt kontrol, og gamle grønne resultater skjules efter 90 sekunder.
Webkontrollen validerer HTTPS og kontrollerer svarets status uden at hente siden
eller følge login-redirects. Et login-svar (HTTP 401) regnes som tilgængeligt;
den tester ikke login eller fuld afspilning. Ved deaktiveret automatisk
synkronisering vises orange med forklaring om, at opsætningen ikke er verificeret.

Ved fejl: kontrollér korrekt Cloudflare-zone, tokenrettigheder, HTTP/HTTPS-valg,
lokalt mål og tilladte værtsnavne. Gatewaykortet viser først Online, når alle domæner er verificeret. Stop af gatewayen sletter ikke DNS eller Origin Rules.

Referencer: [Origin Rules](https://developers.cloudflare.com/rules/origin-rules/),
[destination port](https://developers.cloudflare.com/rules/origin-rules/features/#destination-port),
[SRV-records](https://developers.cloudflare.com/dns/manage-dns-records/reference/dns-record-types/#srv).


## 11. Fjern mål og ryd Cloudflare op

Efter opdateringen: vent på grøn status, så eksisterende aktive DNS-records også
bliver registreret med gatewayens ejerskab. Klik derefter **Fjern mål** på
`test-a.gleruphub.dk` og `test-b.gleruphub.dk`, og tryk **Gem gateway**.
Behold film/media. Når gatewayen kører med automatisk synkronisering slået til,
fjerner næste kontrol de to ejede A-records og deres navne fra gatewayens egen
Origin Rule. Andre regler og DNS-records ændres ikke. Stop af gatewayen alene
sletter fortsat ikke DNS. Cloudflares DNS-cache kan kort vise gamle svar.

Oprydningen gemmes lokalt og genoptages efter genstart eller API-fejl. En
DNS-record slettes kun, hvis record-ID, A-type, navn, IP, proxystatus og
Cloudflare-kommentar stadig matcher gatewayens registrering. Hvis en record er
ændret, overtaget af en anden profil eller endnu ikke registreret som ejet,
bevares den, og kortet forklarer det. Et mål, der tilføjes igen før oprydningen,
får annulleret sin sletning. Gatewayen skal fortsat have mindst ét mål.

## 12. Webdomæner accepterer kun Cloudflare-forbindelser

Gatewayens almindelige webdomæner accepterer kun HTTP-anmodninger, hvis
forbindelsens faktiske afsender-IP tilhører Cloudflares offentliggjorte IPv4/IPv6-
netværk. Direkte HTTPS-adgang til VPN-IP/port med webdomænets Host/SNI giver
HTTP 403. Headers som `X-Forwarded-For` eller `CF-Connecting-IP` kan ikke give
adgang. Reglen gælder også efter portskift og genoprettelse af observeren.

Media-domænet bruger samme port og skal fortsat kunne forbindes direkte.
Derfor kontrolleres afsender-IP på webdomænet i Caddy, ikke med en generel
blokering af hele VPN-porten. Media videresender fortsat kun `/media/*`, og
FjordFlix' eksisterende adgangskontrol til videoer gælder. Ukendte domæner
og media-domænets webroot giver ingen adgang til hjemmesiden.

Listen er indbygget fra [Cloudflares IPv4-liste](https://www.cloudflare.com/ips-v4)
og [IPv6-liste](https://www.cloudflare.com/ips-v6), kontrolleret 2026-10-13.
Listen skal opdateres i appen, hvis Cloudflare ændrer sine origin-netværk.
Det er en afgrænsning af webadgang, ikke en netværksmæssig blokering af direkte
trafik til media-porten.


## 13. Minecraft Java på samme VPN som web og media

Åbn gatewayens **Indstillinger**, og aktivér **Gameserver → Aktivér gameserver-videresendelse** (Minecraft Java).
Minecraft skal ikke tilføjes som et HTTP/HTTPS-webmål.

For denne installation:

- Domæne: `mc.nordprison.dk`.
- Lokal IP: `192.168.1.152`.
- Lokal port: `25566` (Paper 1.16.5).
- Cloudflare-nøgle: `NordPrison Minecraft`, med nordprison.dk's Zone ID og
  rettighederne Zone / DNS / Edit og Zone / Zone / Read.

Behold **Opdater automatisk IP og port** slået til, og tryk **Gem gateway**.
Slå den gamle Minecraft-VPNs automatiske DNS-opdatering fra før overtagelsen,
så de to forbindelser ikke ændrer samme domæne. Den gamle VPN kan beholdes som
fallback, mens du tester.

Gatewayen opretter/opdaterer en DNS-only A-record for `mc.nordprison.dk` og
SRV-recorden `_minecraft._tcp.mc.nordprison.dk`. SRV peger på `mc.nordprison.dk`
med gatewayens aktuelle Proton-port. Ved IP/portændring opdateres begge automatisk.
Nordprison-hjemmesiden og andre DNS-navne berøres ikke. Minecraft kræver ingen
Origin Rule eller HTTPS-certifikat. Film og media beholder deres HTTPS-ruter.

Vent på grøn prik. Java-klienten skal derefter forbinde til **mc.nordprison.dk**
uden portnummer; klienten finder porten via SRV. Statuskontrollen tester Java-
serverens offentlige svar, men ikke login eller spil i verdenen. Test derfor
selv med en klient til 1.16.5, før den gamle VPN slukkes.

Der understøttes én Java-server pr. gateway. Bedrock/UDP understøttes ikke.
Serveren ser gatewayens lokale afsenderadresse; PROXY protocol sendes ikke.
Webdomænernes kontrol af Cloudflare-afsenderen gælder fortsat.

Hvis Minecraft fravælges og gatewayen gemmes, rydder automatisk synkronisering
dens uændrede ejede A- og SRV-records op. Stop alene beholder DNS. Ved rollback
skal den gamle DNS-opsætning gendannes og dens updater genaktiveres, efter at
gatewayens Minecraft-del er deaktiveret.
