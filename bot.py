import os, json, time, base64
from datetime import datetime, timezone
from urllib.parse import urljoin
import requests
from bs4 import BeautifulSoup

WATCHES = [
    {
        "name": "Lille",
        "search_url": "https://trouverunlogement.lescrous.fr/tools/47/search?bounds=2.895507971002203_50.84106942862877_3.2793428098693904_50.52434116637029&locationName=Lille",
        "webhook": os.environ["DISCORD_WEBHOOK_LILLE"],
    },
    {
        "name": "France",
        "search_url": "https://trouverunlogement.lescrous.fr/tools/47/search?bounds=-3.641053695014068_52.908902047770255_8.641661148735935_42.16340342422403&locationName=Lille",
        "webhook": os.environ["DISCORD_WEBHOOK_FRANCE"],
    },
]

GITHUB_TOKEN   = os.environ["GITHUB_TOKEN"]
GITHUB_REPO    = os.environ["GITHUB_REPO"]
GITHUB_BRANCH  = os.environ.get("GITHUB_BRANCH", "main")
POLL_INTERVAL  = int(os.environ.get("POLL_INTERVAL_SECONDS", "20"))
STATE_FILE     = "seen.json"
HEADERS        = {"User-Agent": "Mozilla/5.0 (crous-notifier)"}

FETCH_RETRIES              = int(os.environ.get("FETCH_RETRIES", "2"))
FETCH_RETRY_DELAY_SECONDS  = int(os.environ.get("FETCH_RETRY_DELAY_SECONDS", "2"))
FAILURE_ALERT_SECONDS      = int(os.environ.get("FAILURE_ALERT_MINUTES", "5")) * 60
HEALTHCHECK_INTERVAL_SECONDS = int(os.environ.get("HEALTHCHECK_INTERVAL_HOURS", "3")) * 3600

ALERT_WEBHOOK  = os.environ.get("DISCORD_WEBHOOK_ALERTS", os.environ["DISCORD_WEBHOOK_LILLE"])
GITHUB_API = f"https://api.github.com/repos/{GITHUB_REPO}/contents/{STATE_FILE}"
GITHUB_HEADERS = {
    "Authorization": f"Bearer {GITHUB_TOKEN}",
    "Accept": "application/vnd.github+json",
}

def load_seen():
    r = requests.get(GITHUB_API, headers=GITHUB_HEADERS, params={"ref": GITHUB_BRANCH}, timeout=30)
    if r.status_code == 404:
        return {}, None
    r.raise_for_status()
    data = r.json()
    content = base64.b64decode(data["content"]).decode()
    parsed = json.loads(content)
    if isinstance(parsed, list):
        return {}, data["sha"]
    return parsed, data["sha"]

def save_seen(state, sha):
    content = base64.b64encode(json.dumps(state).encode()).decode()
    payload = {"message": "update state", "content": content, "branch": GITHUB_BRANCH}
    if sha:
        payload["sha"] = sha
    r = requests.put(GITHUB_API, headers=GITHUB_HEADERS, json=payload, timeout=30)
    r.raise_for_status()
    return r.json()["content"]["sha"]

def send_alert(message, color=0xff0000, title="CROUS Bot Alert"):
    payload = {"embeds": [{
        "title": title,
        "description": message,
        "color": color,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }]}
    try:
        requests.post(ALERT_WEBHOOK, json=payload, timeout=10)
    except Exception:
        pass

def send_health_check():
    now = time.time()
    lines = []
    for watch in WATCHES:
        name = watch["name"]
        ts = last_success.get(name)
        if ts is None:
            lines.append(f"⚠️ **{name}**: no successful fetch yet")
        else:
            lines.append(f"✅ **{name}**: last successful fetch {int(now - ts)}s ago")
    send_alert("\n".join(lines), color=0x2ecc71, title="CROUS Bot Health Check")

consecutive_failures = {}
last_success = {}
alerted = {}

def fetch_listings(search_url):
    last_exc = None
    for attempt in range(FETCH_RETRIES + 1):
        try:
            r = requests.get(search_url, headers=HEADERS, timeout=30)
            if r.status_code != 200:
                raise RuntimeError(f"HTTP {r.status_code}: {r.reason}")
            break
        except (RuntimeError, requests.RequestException) as e:
            last_exc = e
            if attempt < FETCH_RETRIES:
                time.sleep(FETCH_RETRY_DELAY_SECONDS)
    else:
        raise last_exc
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

def notify(listing, webhook):
    fields = []
    if listing["address"]:
        fields.append({"name": "Adresse", "value": listing["address"], "inline": False})
    if listing["price"]:
        fields.append({"name": "Loyer", "value": listing["price"], "inline": True})
    fields.append({"name": "Annonce", "value": f"[Voir le logement]({listing['url']})", "inline": True})

    payload = {"embeds": [{
        "title": f"🏠 {listing['title']}" if listing["title"] else "Nouveau logement CROUS",
        "url": listing["url"],
        "color": 0x0f8000,
        "fields": fields,
        "footer": {"text": "CROUS · nouvelle annonce détectée"},
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }]}
    for attempt in range(5):
        r = requests.post(webhook, json=payload, timeout=30)
        if r.status_code == 429:
            wait = r.json().get("retry_after", 1) + 0.5
            print(f"Rate limited, waiting {wait}s")
            time.sleep(wait)
            continue
        r.raise_for_status()
        return
    raise RuntimeError(f"Gave up notifying after retries: {listing['title']}")

def check_watch(watch, state, sha):
    name = watch["name"]
    listings = fetch_listings(watch["search_url"])
    last_success[name] = time.time()
    if alerted.get(name):
        send_alert(f"**[{name}]** recovered — fetching normally again.", color=0x2ecc71)
        alerted[name] = False
    consecutive_failures[name] = 0
    current = {l["id"] for l in listings}
    seen = set(state.get(name, []))
    if not seen:
        state[name] = sorted(current)
        sha = save_seen(state, sha)
        print(f"[{name}] Seeded {len(current)} listings, no pings.")
        return state, sha
    new = current - seen
    if not new:
        print(f"[{name}] No new listings.")
        return state, sha
    for l in listings:
        if l["id"] in new:
            notify(l, watch["webhook"])
            print(f"[{name}] Notified: {l['title']}")
            seen.add(l["id"])
            state[name] = sorted(seen | current)
            sha = save_seen(state, sha)
    return state, sha

def main():
    state, sha = load_seen()
    start = time.time()
    for watch in WATCHES:
        last_success.setdefault(watch["name"], start)
    last_healthcheck = start

    while True:
        for watch in WATCHES:
            try:
                state, sha = check_watch(watch, state, sha)
            except Exception as e:
                name = watch["name"]
                consecutive_failures[name] = consecutive_failures.get(name, 0) + 1
                downtime = time.time() - last_success.get(name, start)
                print(f"[{name}] Error ({consecutive_failures[name]}, down {int(downtime)}s): {e}")
                if downtime >= FAILURE_ALERT_SECONDS and not alerted.get(name):
                    send_alert(f"**[{name}]** down for {int(downtime)}s (no successful fetch since).\nLatest error: `{e}`")
                    alerted[name] = True

        if time.time() - last_healthcheck >= HEALTHCHECK_INTERVAL_SECONDS:
            send_health_check()
            last_healthcheck = time.time()

        time.sleep(POLL_INTERVAL)

if __name__ == "__main__":
    main()
