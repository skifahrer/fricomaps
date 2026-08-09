#!/usr/bin/env python3
"""
Prihlásenie na Google Drive ako VLASTNÍK dát – jedno miesto, ktoré vie „kto
som a aký mám token".

PREČO TO EXISTUJE. DMR 5.0 sa z Drive čítalo cez verejný odkaz („ktokoľvek
s odkazom"). Taký odkaz má DENNÝ LIMIT SŤAHOVANIA na súbor a nezdieľajú ho
len naše behy – zdieľa ho každý, kto na ten odkaz siahne. Keď sa vyčerpá,
Drive prestane dávať dáta, a keďže je to JEDINÁ cesta k DMR 5.0, v tom behu
sa model nedoplní vôbec (beh 31315890474: namiesto 32 kB dát prišlo 2 009 B
HTML stránky a job visel 2 h 16 min).

Prihlásený vlastník má na svoje vlastné súbory oveľa vyšší strop a nedelí sa
o neho s cudzími klientmi. Preto: keď je v prostredí token vlastníka, číta sa
cez Drive API s `Authorization: Bearer`; keď nie je, ostáva verejný odkaz ako
doteraz. Ktorý z tých dvoch režimov beh použil, sa VŽDY vypíše – tichý návrat
do verejného limitu (zmazaný secret, preklep v mene) je presne ten druh
omylu, na ktorý sa potom pol dňa čaká.

ČO TREBA RAZ NASTAVIŤ. Potom sa na to nesiaha, kým sa token neodvolá:

  1. Google Cloud Console → projekt → „APIs & Services" → povoľ
     **Google Drive API**.
  2. „OAuth consent screen": typ **External**, publishing status
     **In production**. Status „Testing" je tá pasca – refresh token v ňom
     platí **7 dní** a pipeline potom raz do týždňa spadne na
     `invalid_grant`. Overenie appky Google nežiada, kým si jej jediným
     používateľom ty; pri prihlásení preklikáš „Advanced → Go to … (unsafe)".
  3. „Credentials" → „Create credentials" → „OAuth client ID" → typ
     **Desktop app**. Vypadne `client_id` a `client_secret`.
  4. Na svojom počítači (nie na runneri – treba prehliadač):

         python3 workers/drive-auth.py --login \\
             --client-id=… --client-secret=…

     Vypíše JSON, ktorý už obsahuje `refresh_token`.
  5. Ten JSON vlož do repozitára ako secret **GDRIVE_CREDENTIALS**
     (Settings → Secrets and variables → Actions → New repository secret).
     Viac netreba – workflowy si ho podávajú samy a `dmr5-drive.py` si ho
     vyzdvihne z prostredia.

BEZ POČÍTAČA (z telefónu). `--login` potrebuje prehliadač a loopback server na
tom istom stroji, takže na telefóne nepobeží. Na to je workflow
`.github/workflows/drive-login.yml`, ktorý tú rolu rozdelí: **prehliadač je
telefón, shell je runner**. Token sa v ňom nikde nevypíše – v public
repozitári vidí log behu ktokoľvek – ide z `--exchange` do súboru s právami
600 a z neho rovno do secretu. Celý postup je v hlavičke toho workflowu.
Secrety sa tam volajú `DRIVE_CLIENT` / `DRIVE_SECRET` / `DRIVE_REFRESH`, čiže
druhá trojica z `TRIOS` nižšie.

Do secretov patria údaje TOHO klienta, ktorým bol token vyrobený: refresh
token platí len pre pár, ktorý ho vydal.

ROZSAH PRÁV je `drive.readonly`: pipeline z Drive iba číta. Zapisovať tam
nemá čo a token, ktorý nemôže nič zmazať, sa dá aj oveľa pokojnejšie nechať
v secrets.

Použitie:
    python3 workers/drive-auth.py --login --client-id=… --client-secret=…
    python3 workers/drive-auth.py --login --manual      # bez localhostu
    python3 workers/drive-auth.py --check               # kto som
    python3 workers/drive-auth.py --check --file=<id>   # a vidím naň?
    python3 workers/drive-auth.py --print-token         # do curlu, lokálne
"""
import argparse
import importlib.util
import json
import os
import sys
import threading
import time
import urllib.parse

_HERE = os.path.dirname(os.path.abspath(__file__))


def load(name, path):
    """workers/*.py sa kvôli pomlčke v mene nedajú `import`-núť normálne.

    Cez `sys.modules` preto, aby sa ten istý modul nevykonal dvakrát, keď si
    ho vypýta viac súborov (`dmr5-drive.py` aj tento).
    """
    if name in sys.modules:
        return sys.modules[name]
    spec = importlib.util.spec_from_file_location(name, os.path.join(_HERE, path))
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


# Spojenie na Google (aj cez firemné proxy a s vlastným CA) vie `drive-serve.py`
# – je to ten súbor, ktorý po HTTP hovorí. Opisovať ho sem by znamenalo dve
# miesta, ktoré si tú istú vec (proxy, CA) počítajú samy.
drive = load("drive_serve", "drive-serve.py")

AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN_HOST = "oauth2.googleapis.com"
TOKEN_PATH = "/token"
API_HOST = "www.googleapis.com"

# Pipeline z Drive len číta. `drive.readonly` je síce „restricted" rozsah
# (Google ho pri neoverenej appke ukáže s varovaním), ale žiadny menší
# nestačí: `drive.file` vidí len súbory, ktoré appka sama vytvorila, a DMR 5.0
# tam ležalo dávno pred ňou.
SCOPE = "https://www.googleapis.com/auth/drive.readonly"

# Koľko pred vypršaním si token obnovíme. Access token platí hodinu, čítanie
# blokov trvá aj dve – bez obnovy by beh spadol v polovici na 401.
RENEW_BEFORE_S = 300

KEYS = ("client_id", "client_secret", "refresh_token")

# Tá istá trojica, dve pomenovania. Prvé hovorí samo za seba; druhé je to,
# ktoré už leží v secrets tohto repozitára – prepisovať ho z telefónu by bola
# zbytočná robota a `client_secret` Google druhýkrát neukáže, takže by sa
# nemusel mať odkiaľ vziať.
TRIOS = (
    ("GDRIVE_CLIENT_ID", "GDRIVE_CLIENT_SECRET", "GDRIVE_REFRESH_TOKEN"),
    ("DRIVE_CLIENT", "DRIVE_SECRET", "DRIVE_REFRESH"),
)


class AuthError(RuntimeError):
    """Chyba prihlásenia. Text už nesie, čo s tým – vypíš ho ako `::error::`."""


# ---------- HTTP ----------

def request_json(method, host, path, body=None, headers=None, tries=4):
    """Jedna JSON požiadavka na Google. Vracia (stav, dict).

    Vlastná, a nie `urllib`, z toho istého dôvodu ako v `drive-serve.py`:
    runner nemá nič doinštalované a proxy s vlastným CA treba obslúžiť tak
    ako tam.
    """
    last = None
    for attempt in range(tries):
        conn = drive.connect(host, timeout=60)
        try:
            hdr = {"User-Agent": drive.UA, "Accept-Encoding": "identity",
                   **(headers or {})}
            if body is not None:
                hdr["Content-Type"] = "application/x-www-form-urlencoded"
            conn.request(method, path, body=body, headers=hdr)
            resp = conn.getresponse()
            raw = resp.read()
            try:
                data = json.loads(raw or b"{}")
            except ValueError:
                data = {"_telo": raw[:400].decode("utf-8", "replace")}
            if not isinstance(data, dict):
                data = {"_telo": str(data)[:400]}
            return resp.status, data
        except Exception as exc:                    # noqa: BLE001
            last = exc
            time.sleep(min(1.5 ** attempt, 10))
        finally:
            try:
                conn.close()
            except Exception:                       # noqa: BLE001
                pass
    raise AuthError(f"{host}{path.split('?')[0]} neodpovedal ani na {tries}. "
                    f"pokus ({last}). Skontroluj sieť alebo proxy.")


def project_of(client_id):
    """Číslo projektu z client_id (`<projekt>-<hash>.apps.googleusercontent.com`)."""
    head = (client_id or "").split("-", 1)[0]
    return head if head.isdigit() else ""


def api_hint(status, data, creds, path):
    """Hláška k chybe z Drive API – aj s tým, čo s ňou."""
    reason = api_reason(data) or f"HTTP {status}"
    err = data.get("error") if isinstance(data, dict) else None
    detail = str(err.get("message") or "") if isinstance(err, dict) else ""
    if reason == "accessNotConfigured":
        # Toto NIE JE chyba tokenu: token je platný, len projekt nemá zapnuté
        # Drive API, tak ho API neobslúži. Bez odkazu s číslom projektu sa to
        # hľadá po Console zbytočne dlho (beh 31332232209).
        proj = project_of(creds.client_id)
        where = ("https://console.cloud.google.com/apis/library/"
                 "drive.googleapis.com" + (f"?project={proj}" if proj else ""))
        return (f"V projekte na Google Cloud{f' (číslo {proj})' if proj else ''} "
                f"nie je zapnuté **Google Drive API**, takže token síce platí, "
                f"ale API ho neobslúži. Zapni ho tu: {where} – a potom to spusti "
                f"znova." + (f" Google k tomu píše: {detail}" if detail else ""))
    if reason in ("insufficientPermissions", "forbidden") and "scope" in detail.lower():
        return (f"Token nemá rozsah {SCOPE} ({detail}). Vyrob ho znova – pri "
                "prihlásení musí byť zaškrtnuté právo čítať Drive.")
    return (f"Drive API vrátilo HTTP {status} na {path.split('?')[0]}: {reason}"
            + (f" – {detail}" if detail else ""))


def api_get(creds, path):
    """GET na Drive API s tokenom. Vracia dict; chybu prekladá do hlášky."""
    status, data = request_json("GET", API_HOST, path,
                                headers={"Authorization": "Bearer " + creds.token()})
    if status == 401:
        # Token mohol vypršať práve teraz – skús ho raz vymeniť.
        status, data = request_json(
            "GET", API_HOST, path,
            headers={"Authorization": "Bearer " + creds.renew(None)})
    if status != 200:
        raise AuthError(api_hint(status, data, creds, path))
    return data


def api_reason(data):
    """Prvý `reason` z chybovej odpovede Drive API, alebo None."""
    err = data.get("error") if isinstance(data, dict) else None
    if isinstance(err, dict):
        for e in err.get("errors") or []:
            if e.get("reason"):
                return e["reason"]
        return err.get("status") or err.get("message")
    if isinstance(err, str):                        # OAuth chyby sú `{"error": "…"}`
        return err
    return None


# ---------- prihlasovacie údaje ----------

class Credentials:
    """Refresh token vlastníka + access token, ktorý sa sám obnovuje.

    OBNOVA JE TU, NIE U VOLAJÚCEHO. Čítanie blokov beží v desiatkach vlákien
    a trvá aj hodiny; keby si každé vlákno riešilo vypršanie samo, buď by sa
    token obnovoval desaťkrát za sebou, alebo by beh spadol v polovici na 401.
    """

    def __init__(self, client_id, client_secret, refresh_token, source="?"):
        self.client_id = client_id
        self.client_secret = client_secret
        self.refresh_token = refresh_token
        self.source = source
        self.email = None            # doplní `whoami()`
        self._token = None
        self._until = 0.0
        self._lock = threading.Lock()

    # Aby sa dal objekt bezpečne vypísať do logu.
    def __repr__(self):
        return f"<Credentials {self.email or 'neznámy účet'} z {self.source}>"

    def token(self):
        """Platný access token; obnoví ho, keď dochádza."""
        with self._lock:
            if not self._token or time.time() > self._until - RENEW_BEFORE_S:
                self._fetch()
            return self._token

    def renew(self, stale):
        """Vymeň token, ktorý dostal 401 – ale raz, nie raz za vlákno.

        `stale` je token, s ktorým to zlyhalo. Keď medzitým obnovil niekto
        iný, vráti sa ten nový a druhá obnova sa nekoná.
        """
        with self._lock:
            if stale is None or self._token == stale:
                self._fetch()
            return self._token

    def _fetch(self):
        body = urllib.parse.urlencode({
            "grant_type": "refresh_token",
            "refresh_token": self.refresh_token,
            "client_id": self.client_id,
            "client_secret": self.client_secret,
        })
        status, data = request_json("POST", TOKEN_HOST, TOKEN_PATH, body)
        if status != 200 or not data.get("access_token"):
            raise AuthError(token_error(status, data, self.source))
        self._token = data["access_token"]
        self._until = time.time() + float(data.get("expires_in") or 3600)


def token_error(status, data, source):
    """Hláška k neúspešnej obnove tokenu – aj s tým, čo s ňou."""
    reason = str(data.get("error") or f"HTTP {status}")
    detail = data.get("error_description") or ""
    if reason == "invalid_grant":
        return (f"Drive odmietol refresh token z {source} ({detail}). "
                "Najčastejšie je to publishing status „Testing“ na OAuth "
                "consent screene – v ňom token platí len 7 dní. Prepni ho na "
                "„In production“, vygeneruj nový token cez "
                "`python3 workers/drive-auth.py --login` a prepíš secret "
                "GDRIVE_CREDENTIALS. Rovnako to vyzerá po zmene hesla účtu "
                "alebo po odvolaní prístupu.")
    if reason == "invalid_client":
        return (f"Drive nepozná OAuth klienta z {source} ({detail}). "
                "`client_id` a `client_secret` musia byť z TOHO ISTÉHO "
                "klienta, ktorým bol refresh token vyrobený – token platí len "
                "pre pár, ktorý ho vydal. Keď vznikol v OAuth Playgrounde, "
                "patria sem údaje toho web klienta, nie desktopového.")
    if reason == "invalid_scope":
        return (f"Rozsah práv {SCOPE} nie je pre tento OAuth projekt povolený "
                "– skontroluj, či je v projekte zapnuté Google Drive API.")
    return (f"Obnova access tokenu z {source} zlyhala: {reason} {detail} "
            "(HTTP {status}). Nový token: "
            "`python3 workers/drive-auth.py --login`.").replace("{status}", str(status))


def _flatten(data, source):
    """JSON z Google Console má údaje pod `installed`/`web` – rozbaľ ich."""
    if not isinstance(data, dict):
        raise AuthError(f"{source} nie je JSON objekt s {', '.join(KEYS)}.")
    out = dict(data)
    for key in ("installed", "web"):
        if isinstance(data.get(key), dict):
            out = {**data[key], **{k: v for k, v in data.items() if k != key}}
    return out


def parse_creds(raw, source):
    """Text secretu → dict. Berie JSON **aj riadky `kľúč=hodnota`**.

    Tie riadky nie sú rozmar: secret sa dá vyplniť aj z telefónu, kde token
    nevzniká cez `--login` (loopback server nie je kde spustiť), ale sa
    prepisuje z Google OAuth Playgroundu. Skladať pritom na mobilnej
    klávesnici JSON so zátvorkami a úvodzovkami je presne ten druh roboty,
    v ktorej sa spraví preklep a hodinu sa hľadá:

        client_id=…apps.googleusercontent.com
        client_secret=GOCSPX-…
        refresh_token=1//…

    Oddeľovač smie byť `=` alebo `:`, prázdne riadky a `#` sa preskočia.
    """
    raw = raw.strip()
    if raw.startswith("{"):
        try:
            return _flatten(json.loads(raw), source)
        except ValueError as exc:
            raise AuthError(
                f"{source} vyzerá ako JSON, ale nie je platný ({exc}). Skús "
                "namiesto neho tri riadky `client_id=…`, `client_secret=…`, "
                "`refresh_token=…` – to sa píše bez zátvoriek.") from None
    out = {}
    for line in raw.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        # Delí sa na PRVOM oddeľovači: v tokene sa `=` aj `:` môžu vyskytovať
        # (`1//0gAbC…`), a keby sa delilo na poslednom, prišli by sme o kus.
        cut = min((line.find(c) for c in "=:" if c in line), default=-1)
        if cut <= 0:
            raise AuthError(
                f"{source}: riadok {line[:24]!r}… nemá tvar `kľúč=hodnota`. "
                f"Čakám {', '.join(KEYS)} – každé na vlastnom riadku.")
        key = line[:cut].strip().lower().replace("-", "_")
        out[key] = line[cut + 1:].strip().strip('"').strip("'").rstrip(",")
    return out


def from_env(env=None):
    """Prihlasovacie údaje z prostredia, alebo None, keď v ňom nie sú.

    ČIASTOČNÉ ÚDAJE SÚ CHYBA, nie „tak teda verejne". Kto nastavil polovicu,
    čaká prihlásený beh – a ticho spadnúť na verejný denný limit je presne
    ten omyl, čo sa nájde až vtedy, keď Drive po pol dni prestane púšťať.
    """
    env = os.environ if env is None else env
    raw = (env.get("GDRIVE_CREDENTIALS") or "").strip()
    path = (env.get("GDRIVE_CREDENTIALS_FILE") or "").strip()
    data, source = None, None
    # Čo chýba, sa má pomenovať tak, ako to treba doplniť v Settings → Secrets
    # – nie vnútorným kľúčom. „Chýba refresh_token" pošle človeka hľadať, „chýba
    # DRIVE_REFRESH" povie, čo pridať.
    as_named = None
    if raw:
        source = "secret GDRIVE_CREDENTIALS"
        data = parse_creds(raw, source)
    elif path:
        source = f"súbor {path} (GDRIVE_CREDENTIALS_FILE)"
        if not os.path.exists(path):
            raise AuthError(f"{source} neexistuje.")
        with open(path) as f:
            data = _flatten(json.load(f), source)
    else:
        # Hľadá sa ÚPLNÁ trojica; nekompletná sa zapamätá len na to, aby sa
        # dalo povedať, čo v nej chýba. Inak by nastavená polovica jedného
        # pomenovania zatienila celé druhé.
        partial = None
        for names in TRIOS:
            vals = {k: (env.get(n) or "").strip() for k, n in zip(KEYS, names)}
            named = dict(zip(KEYS, names))
            label = "secrets " + " / ".join(names)
            if all(vals.values()):
                source, data, as_named = label, vals, named
                break
            if any(vals.values()) and partial is None:
                partial = (label, vals, named)
        else:
            if partial:
                source, data, as_named = partial
    if data is None:
        return None
    missing = [(as_named or {}).get(k, k) for k in KEYS if not data.get(k)]
    if missing:
        extra = ""
        if missing == ["DRIVE_REFRESH"] or missing == ["refresh_token"]:
            # Toto je ten najčastejší stav: `client_id`/`client_secret` sú
            # identita APLIKÁCIE, nie povolenie k dátam. Refresh token vzniká
            # jedine tým, že sa vlastník v prehliadači prihlási a povolí to –
            # v pipeline sa vyrobiť nedá ani teoreticky.
            extra = (" Refresh token je to, čo z klienta robí prihlásenie: "
                     "client_id a client_secret sú len identita aplikácie. "
                     "Vyrobí sa `python3 workers/drive-auth.py --login`, "
                     "alebo z telefónu cez Google OAuth Playground (postup "
                     "v hlavičke workers/drive-auth.py).")
        raise AuthError(
            f"{source} nemá {', '.join(missing)}. Prihlásenie s polovicou "
            "údajov nefunguje a verejný odkaz použiť nemôžem – to by bol tichý "
            f"návrat k dennému limitu.{extra}")
    return Credentials(data["client_id"], data["client_secret"],
                       data["refresh_token"], source)


# ---------- kto som a čo vidím ----------

def client_from_env(env=None):
    """Len `client_id` a `client_secret` – to, čo je známe PRED tokenom.

    `from_env()` sa na to použiť nedá: ten (správne) žiada úplnú trojicu, kým
    tu sa refresh token práve vyrába a ešte neexistuje.
    """
    env = os.environ if env is None else env
    raw = (env.get("GDRIVE_CREDENTIALS") or "").strip()
    pair, source = ("", ""), "prostredie"
    if raw:
        data = parse_creds(raw, "secret GDRIVE_CREDENTIALS")
        pair = (data.get("client_id", ""), data.get("client_secret", ""))
        source = "secret GDRIVE_CREDENTIALS"
    else:
        for names in TRIOS:
            got = ((env.get(names[0]) or "").strip(),
                   (env.get(names[1]) or "").strip())
            if any(got):
                pair, source = got, f"secrets {names[0]} / {names[1]}"
            if all(got):
                break
    if not all(pair):
        raise AuthError(
            f"Chýba client_id alebo client_secret ({source}). Vlož ich do "
            f"secrets ako {TRIOS[1][0]} a {TRIOS[1][1]} – vyrobia sa v Google "
            "Cloud Console → Credentials → OAuth client ID.")
    return pair[0], pair[1], source


def code_from(text):
    """Kód z toho, čo sa dá skopírovať z prehliadača.

    Berie aj celú adresu, na ktorej prehliadač skončil – po potvrdení totiž
    Google presmeruje na `http://127.0.0.1:…/?code=…`, čo sa nemá kde otvoriť
    a v telefóne z toho ostane len adresný riadok. Prilepený `#` alebo
    medzery z kopírovania sú bežné, tak sa odstrihnú.
    """
    text = (text or "").strip().strip("<>\"'")
    if "code=" in text:
        q = urllib.parse.parse_qs(urllib.parse.urlsplit(text).query
                                  or text.split("?", 1)[-1])
        got = (q.get("code") or [""])[0]
        if got:
            return got
        raise AuthError(f"V tom, čo si vložil, je `code=`, ale prázdne. "
                        f"Skopíruj celú adresu, na ktorej prehliadač skončil.")
    if text.startswith("http"):
        raise AuthError(
            "V tej adrese nie je `code=`. Keď je v nej `error=access_denied`, "
            "prihlásenie si zamietol; keď `error=admin_policy_enforced`, účet "
            "nie je v tej istej organizácii ako OAuth appka.")
    if not text or " " in text:
        raise AuthError("Nedostal som kód. Vlož celú adresu, na ktorej "
                        "prehliadač po potvrdení skončil (je v nej `?code=…`).")
    return text


def exchange_to_file(code, redirect_uri, out, env=None):
    """Kód → refresh token do SÚBORU, nie na výstup.

    Do súboru zámerne: v public repozitári vidí log behu ktokoľvek, takže
    token sa nesmie ocitnúť ani v `echo`. Volajúci ho z toho súboru pošle
    rovno do secretu a súbor zmaže.

    TOKEN SA ULOŽÍ SKÔR, NEŽ SA ČOKOĽVEK OVERUJE. Kód od Googlu je
    JEDNORAZOVÝ a na jeho získanie treba človeka v prehliadači – je to
    najdrahšia vec v celom postupe. Kým sa ukladal až po `whoami`, stačilo,
    aby v projekte nebolo zapnuté Drive API (`accessNotConfigured`), a hotový
    platný token sa zahodil aj s kódom: beh 31332232209. Overenie je od toho
    odteraz oddelené a token prežije aj to, keď zlyhá.
    """
    client_id, client_secret, source = client_from_env(env)
    data = exchange(client_id, client_secret, code_from(code), redirect_uri)
    with open(out, "w") as f:
        f.write(data["refresh_token"])
    os.chmod(out, 0o600)
    creds = Credentials(client_id, client_secret, data["refresh_token"], source)
    try:
        return creds, whoami(creds)
    except AuthError as exc:
        # Token je uložený a platný; nefunguje len otázka „kto som". Nech to
        # volajúci dokončí (zapíše secret) a padne až na overení – inak by sa
        # celé prihlásenie robilo odznova pre nastavenie v Console.
        print(f"::warning::Token je vyrobený a uložený, ale overenie účtu "
              f"neprešlo: {exc}")
        return creds, {}


def whoami(creds):
    """Účet, ktorým sme prihlásení. Vyplní aj `creds.email`."""
    data = api_get(creds, "/drive/v3/about?fields=user(displayName,emailAddress),"
                          "storageQuota(limit,usage)")
    user = data.get("user") or {}
    creds.email = user.get("emailAddress")
    return user


FILE_FIELDS = ("id,name,size,mimeType,ownedByMe,owners(emailAddress),"
               "capabilities(canDownload)")


def file_info(creds, file_id):
    """Metadáta jedného súboru – hlavne či ho prihlásený účet VLASTNÍ.

    Vlastníctvo je tu tá podstatná informácia: na cudzí súbor, ktorý je len
    zdieľaný odkazom, sa vzťahuje ten istý denný limit ako na verejný
    prístup, takže prihlásenie by nič neriešilo a nemá sa to tváriť, že áno.
    """
    return api_get(creds, f"/drive/v3/files/{file_id}"
                          f"?fields={FILE_FIELDS}&supportsAllDrives=true")


def describe(creds):
    """Krátky popis režimu do logu – jedna veta, ktorá platí aj bez tokenu."""
    if creds is None:
        return ("verejný odkaz (neprihlásený) – platí denný limit sťahovania "
                "na súbor, zdieľaný so všetkými klientmi")
    return f"prihlásený ako {creds.email or 'vlastník (účet nezistený)'}"


# ---------- jednorazové prihlásenie ----------

def auth_url(client_id, redirect_uri):
    return AUTH_URL + "?" + urllib.parse.urlencode({
        "client_id": client_id,
        "redirect_uri": redirect_uri,
        "response_type": "code",
        "scope": SCOPE,
        # `offline` + `consent` sú to, čo vyrobí refresh token. Bez `consent`
        # ho Google pri druhom prihlásení toho istého účtu už nepošle a z
        # `--login` vypadne JSON bez `refresh_token`.
        "access_type": "offline",
        "prompt": "consent",
    })


def exchange(client_id, client_secret, code, redirect_uri):
    body = urllib.parse.urlencode({
        "code": code,
        "client_id": client_id,
        "client_secret": client_secret,
        "redirect_uri": redirect_uri,
        "grant_type": "authorization_code",
    })
    status, data = request_json("POST", TOKEN_HOST, TOKEN_PATH, body)
    if status != 200 or not data.get("refresh_token"):
        if status == 200:
            raise AuthError(
                "Google poslal access token, ale nie refresh token. Odvolaj "
                "prístup appky v https://myaccount.google.com/permissions "
                "a spusti `--login` znova.")
        raise AuthError(token_error(status, data, "prihlásenie"))
    return data


def wait_for_code(port):
    """Loopback server, ktorý zachytí `?code=…` z presmerovania.

    Google zrušil „out of band" tok (kopírovanie kódu z jeho stránky), takže
    presmerovanie musí prísť na `http://127.0.0.1:<port>`. Preto sa `--login`
    spúšťa na počítači s prehliadačom; na runneri nemá čo robiť.
    """
    import http.server

    got = {}

    class Handler(http.server.BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, *a):
            pass

        def do_GET(self):
            q = urllib.parse.parse_qs(urllib.parse.urlsplit(self.path).query)
            got.update({k: v[0] for k, v in q.items()})
            body = ("<h2>Hotovo</h2><p>Prihlásenie prebehlo. Vráť sa do "
                    "terminálu – JSON s tokenom je tam.</p>"
                    if got.get("code") else
                    f"<h2>Nepodarilo sa</h2><p>{got.get('error', 'chýba code')}"
                    "</p>").encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    with http.server.HTTPServer(("127.0.0.1", port), Handler) as httpd:
        httpd.timeout = 300
        while not got:
            httpd.handle_request()
    if not got.get("code"):
        raise AuthError(f"Prihlásenie neprešlo: {got.get('error', 'bez kódu')}")
    return got["code"]


def do_login(args):
    client_id = args.client_id or os.environ.get("GDRIVE_CLIENT_ID", "")
    client_secret = args.client_secret or os.environ.get("GDRIVE_CLIENT_SECRET", "")
    if not client_id or not client_secret:
        raise AuthError(
            "Chýba --client-id / --client-secret. Vyrobia sa v Google Cloud "
            "Console → Credentials → OAuth client ID, typ „Desktop app“ "
            "(rozpis je v hlavičke tohto súboru).")

    port = args.port or 8731
    redirect_uri = f"http://127.0.0.1:{port}"
    url = auth_url(client_id, redirect_uri)
    print("Otvor v prehliadači (prihlás sa účtom, ktorý DMR 5.0 vlastní):\n")
    print("  " + url + "\n")
    print("Neoverenú appku preklikáš cez „Advanced → Go to … (unsafe)“.\n")
    if args.manual:
        print("Po potvrdení skončíš na adrese, ktorá sa nedá otvoriť "
              f"({redirect_uri}/?code=…).\nSkopíruj z nej celú adresu alebo "
              "len kód a vlož sem:")
        raw = input("code: ").strip()
        code = raw
        if "code=" in raw:
            q = urllib.parse.parse_qs(urllib.parse.urlsplit(raw).query)
            code = (q.get("code") or [""])[0]
        if not code:
            raise AuthError("Nedostal som kód.")
    else:
        try:
            import webbrowser
            webbrowser.open(url)
        except Exception:                           # noqa: BLE001
            pass
        print(f"Čakám na presmerovanie na {redirect_uri} … (Ctrl-C zruší; "
              "bez prehliadača na tomto stroji použi --manual)")
        code = wait_for_code(port)

    data = exchange(client_id, client_secret, code, redirect_uri)
    creds = Credentials(client_id, client_secret, data["refresh_token"],
                        "prihlásenie")
    user = whoami(creds)
    print(f"\nPrihlásený ako {user.get('emailAddress')} "
          f"({user.get('displayName')}).")
    print("\nVlož TOTO ako secret GDRIVE_CREDENTIALS "
          "(Settings → Secrets and variables → Actions):\n")
    print(json.dumps({"client_id": client_id, "client_secret": client_secret,
                      "refresh_token": data["refresh_token"]}, indent=1))
    print("\nPotom to over behom `DMR 5.0 z Drive` v režime „len sonda“ – "
          "krok „Prihlásenie na Drive“ vypíše, ktorým účtom beh číta.")
    return 0


# ---------- CLI ----------

def do_check(args):
    creds = from_env()
    if creds is None:
        print("::warning::Bez prihlásenia – DMR 5.0 sa číta verejným odkazom, "
              "na ktorý platí denný limit sťahovania zdieľaný so všetkými "
              "klientmi. Nastav secret GDRIVE_CREDENTIALS (rozpis: "
              "workers/drive-auth.py --login).")
        print(f"Režim čítania z Drive: {describe(None)}")
        return 0
    user = whoami(creds)
    print(f"Režim čítania z Drive: {describe(creds)}")
    print(f"  účet   {user.get('emailAddress')} ({user.get('displayName')})")
    print(f"  údaje  z {creds.source}")
    bad = 0
    for file_id in args.file or []:
        try:
            info = file_info(creds, file_id)
        except AuthError as exc:
            print(f"::error::Súbor {file_id}: {exc}")
            bad += 1
            continue
        size = int(info.get("size") or 0)
        owned = bool(info.get("ownedByMe"))
        owner = ", ".join(o.get("emailAddress", "?")
                          for o in info.get("owners") or []) or "neznámy"
        print(f"  {info.get('name', file_id)}: {size / 2**30:.2f} GiB, "
              f"vlastník {owner}"
              + (" – tento účet ✓" if owned else " – NIE tento účet"))
        if not info.get("capabilities", {}).get("canDownload", True):
            print(f"::error::Na {info.get('name', file_id)} tento účet nemá "
                  "právo sťahovať.")
            bad += 1
        elif not owned:
            # Zdieľaný cudzí súbor má ten istý denný limit ako verejný odkaz,
            # takže prihlásenie na ňom nič nezískalo – a nemá sa tváriť, že áno.
            print("::warning::Ten súbor tento účet nevlastní, len naň vidí. "
                  "Denný limit sťahovania sa tým neposunie – prihlás sa účtom "
                  "vlastníka, alebo si nahraj vlastnú kópiu a prepíš "
                  "TIF_ID/OVR_ID vo workers/dmr5-drive.py.")
    return 1 if bad else 0


def main():
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--login", action="store_true",
                    help="jednorazové prihlásenie; vypíše JSON do secretu")
    ap.add_argument("--manual", action="store_true",
                    help="pri --login: kód vložíš ručne (stroj bez prehliadača)")
    ap.add_argument("--client-id", default="")
    ap.add_argument("--client-secret", default="")
    ap.add_argument("--port", type=int, default=0,
                    help="port loopbacku pri --login (predvolene 8731)")
    ap.add_argument("--auth-url", action="store_true",
                    help="vypíš odkaz na prihlásenie (client_id z prostredia)")
    ap.add_argument("--exchange", action="store_true",
                    help="kód z prehliadača → refresh token do --out")
    ap.add_argument("--code", default="",
                    help="pri --exchange: kód, alebo celá adresa s `?code=…`")
    ap.add_argument("--redirect-uri", default="",
                    help="musí byť rovnaké ako pri --auth-url "
                         "(predvolene http://127.0.0.1:8731)")
    ap.add_argument("--out", default="",
                    help="pri --exchange: súbor pre refresh token")
    ap.add_argument("--check", action="store_true",
                    help="povedz, ktorým účtom sa číta (a či naň vidí)")
    ap.add_argument("--file", action="append", metavar="ID",
                    help="Drive file id na overenie; dá sa opakovať")
    ap.add_argument("--print-token", action="store_true",
                    help="vypíš access token (len lokálne, do curlu)")
    args = ap.parse_args()

    redirect_uri = args.redirect_uri or f"http://127.0.0.1:{args.port or 8731}"
    try:
        if args.auth_url:
            client_id, _secret, source = client_from_env()
            print(f"client_id z {source}", file=sys.stderr)
            print(auth_url(client_id, redirect_uri))
            return 0
        if args.exchange:
            if not args.out:
                print("::error::--exchange potrebuje --out: token sa nevypisuje "
                      "na výstup, aby neskončil v logu behu.")
                return 2
            creds, user = exchange_to_file(args.code, redirect_uri, args.out)
            print(f"Prihlásené ako {user.get('emailAddress')} "
                  f"({user.get('displayName')}).")
            print(f"Refresh token je v {args.out} – nikde inde a nevypisuje sa.")
            return 0
        if args.login:
            return do_login(args)
        if args.print_token:
            # V Actions by token skončil v logu behu, ktorý si vie prečítať
            # každý, kto vidí repozitár. To sa nestane.
            if os.environ.get("GITHUB_ACTIONS") == "true":
                print("::error::--print-token je len na lokálne ladenie; "
                      "v Actions by token ostal v logu.")
                return 2
            creds = from_env()
            if creds is None:
                print("::error::V prostredí nie sú prihlasovacie údaje.")
                return 2
            print(creds.token())
            return 0
        return do_check(args)
    except AuthError as exc:
        print(f"::error::{exc}")
        return 2


if __name__ == "__main__":
    sys.exit(main())
