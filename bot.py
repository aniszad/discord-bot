import os, json
from urllib.parse import urljoin
import requests
from bs4 import BeautifulSoup

SEARCH_URL = os.environ["SEARCH_URL"]
WEBHOOK    = os.environ["DISCORD_WEBHOOK_URL"]
STATE_FILE = "seen.json"
HEADERS    = {"User-Agent": "Mozilla/5.0 (crous-notifier)"}

def load_seen():
    try:
        with open(STATE_FILE) as f:
            return set(json.load(f))
    except (FileNotFoundError, json.JSONDecodeError):
        return None   # None = first run ever

def fetch_listings():
    r = requests.get(SEARCH_URL, headers=HEADERS, timeout=30)
    r.raise_for_status()
    soup = BeautifulSoup(r.text, "html.parser")
    out = []
    for card in soup.find_all("div", class_="fr-card"):
        a = card.select_one("h3.fr-card__title a") or card.find("a")
        if not a:
            continue
        href = a.get("href", "")
        url  = urljoin(r.url, href)
        listing_id = href.rstrip("/").split("/")[-1] or url  # the /logements/<id>
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

def notify(l):
    lines = [x for x in (l["address"], f"**{l['price']}**" if l["price"] else None) if x]
    requests.post(WEBHOOK, json={"embeds": [{
        "title": l["title"] or "Nouveau logement CROUS",
        "url": l["url"],
        "description": "\n".join(lines) or "Nouveau logement disponible",
        "color": 0x0f8000,
    }]}, timeout=30).raise_for_status()

def main():
    listings = fetch_listings()
    current  = {l["id"] for l in listings}
    seen     = load_seen()
    if seen is None:                       # first run: remember what's there, ping nothing
        json.dump(sorted(current), open(STATE_FILE, "w"))
        print(f"Seeded {len(current)} listings, no pings.")
        return
    new = current - seen
    if not new:
        print("No new listings.")
        return
    for l in listings:
        if l["id"] in new:
            notify(l)
            print("Notified:", l["title"])
    json.dump(sorted(seen | current), open(STATE_FILE, "w"))

if __name__ == "__main__":
    main()