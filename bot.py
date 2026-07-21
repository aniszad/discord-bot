import os, json, time, signal
from datetime import datetime, timezone
from urllib.parse import urljoin
import requests
from bs4 import BeautifulSoup

SEARCH_URL    = os.environ["SEARCH_URL"]
WEBHOOK       = os.environ["DISCORD_WEBHOOK_URL"]
STATE_FILE    = os.environ.get("STATE_FILE", "seen.json")
POLL_INTERVAL = float(os.environ.get("POLL_INTERVAL_SECONDS", "5"))
RUN_ONCE      = os.environ.get("RUN_ONCE", "false").lower() == "true"
HEADERS       = {"User-Agent": "Mozilla/5.0 (crous-notifier)"}

running = True

def handle_stop(signum, frame):
    global running
    running = False

signal.signal(signal.SIGTERM, handle_stop)
signal.signal(signal.SIGINT, handle_stop)

def load_seen():
    try:
        with open(STATE_FILE) as f:
            return set(json.load(f))
    except (FileNotFoundError, json.JSONDecodeError):
        return None

def save_seen(seen):
    json.dump(sorted(seen), open(STATE_FILE, "w"))

def fetch_listings(session):
    r = session.get(SEARCH_URL, headers=HEADERS, timeout=15)
    r.raise_for_status()
    soup = BeautifulSoup(r.text, "html.parser")
    out = []
    for card in soup.find_all("div", class_="fr-card"):
        a = card.select_one("h3.fr-card__title a") or card.find("a")
        if not a:
            continue
        href = a.get("href", "")
        url  = urljoin(r.url, href)
        listing_id = href.rstrip("/").split("/")[-1] or url
        price = card.find("p", class_="fr-badge")
        desc  = card.find("p", class_="fr-card__desc")
        out.append({
            "id": listing_id,
            "title": a.get_text(strip=True),
            "url": url,
            "price": price.get_text(strip=True) if price else None,
            "address": desc.get_text(strip=True) if desc else None,
        })
    return out

def notify(session, l):
    fields = []
    if l["address"]:
        fields.append({"name": "Adresse", "value": l["address"], "inline": False})
    if l["price"]:
        fields.append({"name": "Loyer", "value": l["price"], "inline": True})
    fields.append({"name": "Annonce", "value": f"[Voir le logement]({l['url']})", "inline": True})

    payload = {"embeds": [{
        "title": f"🏠 {l['title']}" if l["title"] else "Nouveau logement CROUS",
        "url": l["url"],
        "color": 0x0f8000,
        "fields": fields,
        "footer": {"text": "CROUS · nouvelle annonce détectée"},
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }]}
    for attempt in range(5):
        r = session.post(WEBHOOK, json=payload, timeout=15)
        if r.status_code == 429:
            wait = r.json().get("retry_after", 1) + 0.5
            log(f"Rate limited, waiting {wait}s")
            time.sleep(wait)
            continue
        r.raise_for_status()
        return
    raise RuntimeError(f"Gave up notifying after retries: {l['title']}")

def log(msg):
    print(f"[{datetime.now(timezone.utc).isoformat(timespec='seconds')}] {msg}", flush=True)

def poll_once(session, seen):
    listings = fetch_listings(session)
    current  = {l["id"] for l in listings}

    if seen is None:
        save_seen(current)
        log(f"Seeded {len(current)} listings, no pings.")
        return current

    new = current - seen
    if new:
        for l in listings:
            if l["id"] in new:
                notify(session, l)
                log(f"Notified: {l['title']}")
                seen.add(l["id"])
                save_seen(seen | current)
                time.sleep(1.2)
    return seen | current

def main():
    session = requests.Session()
    seen = load_seen()

    if RUN_ONCE:
        poll_once(session, seen)
        return

    log(f"Starting CROUS watcher, polling every {POLL_INTERVAL}s")

    backoff = POLL_INTERVAL
    while running:
        start = time.monotonic()
        try:
            seen = poll_once(session, seen)
            backoff = POLL_INTERVAL
        except requests.RequestException as e:
            log(f"Fetch error: {e}")
            backoff = min(backoff * 2, 60)
        except Exception as e:
            log(f"Unexpected error: {e}")
            backoff = min(backoff * 2, 60)

        elapsed = time.monotonic() - start
        sleep_for = max(0.0, backoff - elapsed)
        for _ in range(int(sleep_for * 10)):
            if not running:
                break
            time.sleep(0.1)

    log("Shutting down.")

if __name__ == "__main__":
    main()
