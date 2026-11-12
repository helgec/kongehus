"""Overvåker RSS-feeden til kongehuset.no og varsler i Slack ved nye pressemeldinger."""

import html
import json
import logging
import os
import re
import sys
import tempfile
import time
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path

import feedparser
import requests

try:
    from zoneinfo import ZoneInfo

    OSLO = ZoneInfo("Europe/Oslo")
except Exception:  # Python < 3.9 eller mangler tz-data: bruk lokal tid
    OSLO = None

# Status-hjelperen for dashbordet finnes bare på serveren
sys.path.append("/home/nrknyheter")
try:
    from status_helper import update_status as _update_status
except ImportError:
    _update_status = None

log = logging.getLogger("kongehuset")

RSS_URL = "https://www.kongehuset.no/for-pressen/rss"
STATE_FILE = Path(__file__).resolve().with_name("seen_press_releases.json")
MAX_SEEN = 500  # Hvor mange sette saker vi husker (må være større enn feeden, ~90)
MAX_AGE_HOURS = 48  # Eldre saker markeres som sett uten varsel (vern mot massevarsling)
STATE_VERSION = 2

CHECK_INTERVAL = 60  # Sekunder mellom hver sjekk
DEFAULT_RATE_LIMIT_WAIT = 1800  # Ventetid ved 429 uten Retry-After
MAX_BACKOFF = 900  # Lengste ventetid ved gjentatte feil

STATUS_ID = "kongehuset"
STATUS_NAME = "Kongehuset-overvåker"

HEADERS = {
    "User-Agent": os.environ.get(
        "USER_AGENT",
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    ),
    "Accept": "application/rss+xml, application/xml; q=0.9, */*; q=0.8",
}

# ETag/Last-Modified fra siste vellykkede runde, for betingede forespørsler
_validators = {}


class RateLimited(Exception):
    def __init__(self, wait):
        super().__init__(f"Rate-limited (429), venter {wait} s")
        self.wait = wait


# --- Konfigurasjon -----------------------------------------------------------

def get_webhooks():
    """Henter webhooks fra SLACK_WEBHOOKS (kommaseparert), SLACK_WEBHOOK_URL
    og SLACK_WEBHOOK_URL_1 ... SLACK_WEBHOOK_URL_9. Duplikater fjernes."""
    urls = os.environ.get("SLACK_WEBHOOKS", "").split(",")
    names = ["SLACK_WEBHOOK_URL"] + [f"SLACK_WEBHOOK_URL_{i}" for i in range(1, 10)]
    urls += [os.environ.get(name, "") for name in names]
    return list(dict.fromkeys(u.strip() for u in urls if u.strip()))


# --- Henting og parsing -------------------------------------------------------

def parse_retry_after(value):
    if not value:
        return DEFAULT_RATE_LIMIT_WAIT
    try:
        return max(int(value), CHECK_INTERVAL)
    except ValueError:
        pass
    try:
        until = parsedate_to_datetime(value)
        return max(int((until - datetime.now(timezone.utc)).total_seconds()), CHECK_INTERVAL)
    except (TypeError, ValueError):
        return DEFAULT_RATE_LIMIT_WAIT


def format_date(entry):
    published = entry.get("published_parsed")
    if not published:
        return entry.get("published", ""), None
    dt = datetime(*published[:6], tzinfo=timezone.utc)
    local = dt.astimezone(OSLO) if OSLO else dt.astimezone()
    return local.strftime("%d.%m.%Y kl. %H:%M"), dt.timestamp()


def parse_entry(entry):
    url = entry.get("link", "").strip()
    title = entry.get("title", "").strip() or "Pressemelding"
    date, timestamp = format_date(entry)
    return {
        # URL er nøkkelen (som før), med id/tittel som reserve hvis lenke mangler
        "key": url or entry.get("id") or title,
        "title": title,
        "url": url,
        "date": date,
        "timestamp": timestamp,
        "ingress": entry.get("summary", ""),
    }


def fetch_feed():
    """Returnerer (saker eldst først, validators), eller None hvis feeden er uendret."""
    headers = dict(HEADERS)
    if "etag" in _validators:
        headers["If-None-Match"] = _validators["etag"]
    if "last_modified" in _validators:
        headers["If-Modified-Since"] = _validators["last_modified"]

    response = requests.get(RSS_URL, headers=headers, timeout=30)

    if response.status_code == 304:
        return None
    if response.status_code == 429:
        raise RateLimited(parse_retry_after(response.headers.get("Retry-After")))
    response.raise_for_status()

    feed = feedparser.parse(response.content)
    if feed.bozo and not feed.entries:
        raise ValueError(f"Klarte ikke å tolke RSS-feeden: {feed.bozo_exception}")

    # Feeden er normalt nyest først; snu så Slack-meldingene kommer i riktig rekkefølge
    items = [parse_entry(e) for e in reversed(feed.entries)]
    if items and all(i["timestamp"] is not None for i in items):
        items.sort(key=lambda i: i["timestamp"])

    validators = {}
    if response.headers.get("ETag"):
        validators["etag"] = response.headers["ETag"]
    if response.headers.get("Last-Modified"):
        validators["last_modified"] = response.headers["Last-Modified"]
    return items, validators


# --- Slack --------------------------------------------------------------------

def slack_escape(text):
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def clean_ingress(raw):
    text = re.sub(r"(?i)<br\s*/?>|</p>", "\n", raw)
    text = html.unescape(re.sub(r"<[^>]+>", "", text))
    lines = [" ".join(line.split()) for line in text.splitlines()]
    return "\n".join(f">{slack_escape(line)}" for line in lines if line)


def build_message(item):
    lines = ["👑 *Ny pressemelding fra Kongehuset!*"]
    if item["date"]:
        lines.append(f"🗓️ _{slack_escape(item['date'])}_")

    title = slack_escape(item["title"])
    lines.append(f"📌 *<{item['url']}|{title}>*" if item["url"] else f"📌 *{title}*")

    ingress = clean_ingress(item["ingress"])
    if ingress:
        lines.append(ingress)
    return "\n".join(lines)


def send_to_slack(item, webhooks):
    """Sender til alle webhooks. Returnerer True hvis minst én lyktes."""
    payload = {"text": build_message(item)}
    success = False
    for n, webhook in enumerate(webhooks, start=1):
        try:
            res = requests.post(webhook, json=payload, timeout=10)
            res.raise_for_status()
            log.info("Sendt til Slack (webhook #%d)", n)
            success = True
        except requests.RequestException as e:
            log.error("Feil ved sending til Slack (webhook #%d): %s", n, e)
    return success


# --- Tilstand -----------------------------------------------------------------

def load_state():
    """Returnerer liste med sette nøkler, eller None hvis feeden skal registreres
    på nytt uten varsling (ingen fil, korrupt fil eller gammelt format)."""
    if not STATE_FILE.exists():
        return None
    try:
        data = json.loads(STATE_FILE.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as e:
        log.warning("Tilstandsfilen kunne ikke leses (%s). Registrerer feeden på nytt uten å varsle.", e)
        return None
    if isinstance(data, list):
        # Gammelt format: kjenner bare de 3 øverste sakene, og ville ellers gitt massevarsling
        log.warning("Gammelt tilstandsformat. Registrerer feeden på nytt uten å varsle.")
        return None
    if not isinstance(data, dict) or not isinstance(data.get("seen"), list):
        log.warning("Uventet format i tilstandsfilen. Registrerer feeden på nytt uten å varsle.")
        return None
    return data["seen"]


def save_state(seen):
    """Skriver atomisk: først til midlertidig fil, deretter os.replace."""
    fd, tmp = tempfile.mkstemp(dir=STATE_FILE.parent, prefix=".seen-", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump({"version": STATE_VERSION, "seen": seen[-MAX_SEEN:]}, f, ensure_ascii=False, indent=2)
        os.replace(tmp, STATE_FILE)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


# --- Hovedlogikk --------------------------------------------------------------

def report_status(status, **kwargs):
    if _update_status is None:
        return
    try:
        _update_status(STATUS_ID, STATUS_NAME, status=status, **kwargs)
    except Exception:
        log.exception("Kunne ikke oppdatere statusdashbordet")


def check(webhooks):
    """Én runde. Returnerer tittelen på nyeste sak, eller None hvis feeden er uendret."""
    seen_list = load_state()
    first_run = seen_list is None
    seen_list = seen_list or []
    seen = set(seen_list)

    result = fetch_feed()
    if result is None:
        log.info("Ingen endring i feeden (304)")
        return None
    items, validators = result

    if first_run and not items:
        # Ikke lagre tom tilstand, ellers varsler vi alt neste gang feeden har innhold
        return "Ingen pressemeldinger funnet i feeden"

    cutoff = time.time() - MAX_AGE_HOURS * 3600
    failed = []
    silent = 0
    for item in items:
        if item["key"] in seen:
            continue
        if first_run:
            silent += 1
        elif item["timestamp"] is not None and item["timestamp"] < cutoff:
            log.info("Hopper over gammel sak uten å varsle: %s (%s)", item["title"], item["date"])
            silent += 1
        else:
            log.info("Ny pressemelding funnet: %s", item["title"])
            if not send_to_slack(item, webhooks):
                failed.append(item["title"])
                continue  # Ikke marker som sett, prøv igjen neste runde
        seen.add(item["key"])
        seen_list.append(item["key"])

    save_state(seen_list)
    if first_run:
        log.info("Registrerte %d saker uten å varsle", silent)

    if failed:
        # Ikke lagre ETag, slik at neste runde henter hele feeden og prøver på nytt
        raise RuntimeError(f"Klarte ikke å varsle om: {', '.join(failed)}")

    _validators.clear()
    _validators.update(validators)
    return items[-1]["title"] if items else "Ingen pressemeldinger funnet i feeden"


def main():
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    webhooks = get_webhooks()
    if webhooks:
        log.info("Starter med %d Slack-webhook(s)", len(webhooks))
    else:
        log.warning("Ingen Slack-webhooks er konfigurert! Nye saker blir ikke markert som sett.")
    if _update_status is None:
        log.warning("status_helper ble ikke funnet, rapporterer ikke til dashbordet")

    last_message = None
    failures = 0

    while True:
        delay = CHECK_INTERVAL
        try:
            title = check(webhooks)
            if title is not None:
                last_message = title
            failures = 0
            report_status("OK", last_message=last_message or "Ingen endring")
        except RateLimited as e:
            log.warning("%s", e)
            delay = e.wait
            report_status("ERROR", error_msg=str(e))
        except Exception as e:
            failures += 1
            delay = min(CHECK_INTERVAL * 2 ** (failures - 1), MAX_BACKOFF)
            log.exception("Feil under kjøring (forsøk %d), venter %d s", failures, delay)
            report_status("ERROR", error_msg=str(e))

        time.sleep(delay)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        log.info("Avslutter")
