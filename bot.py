import os, json, time, base64
from datetime import datetime, timezone
from urllib.parse import urljoin
import requests
from bs4 import BeautifulSoup

SEARCH_URL    = os.environ["SEARCH_URL"]
WEBHOOK       = os.environ["DISCORD_WEBHOOK_URL"]
GITHUB_TOKEN  = os.environ["GITHUB_TOKEN"]
GITHUB_REPO   = os.environ["GITHUB_REPO"]  # "owner/repo"
GITHUB_BRANCH = os.environ.get("GITHUB_BRANCH", "main")
STATE_FILE    = "seen.json"
HEADERS       = {"User-Agent": "Mozilla/5.0 (crous-notifier)"}

GITHUB_API = f"https://api.github.com/repos/{GITHUB_REPO}/contents/{STATE_FILE}"
GITHUB_HEADERS = {
    "Authorization": f"Bearer {GITHUB_TOKEN}",
    "Accept": "application/vnd.github+json",
}

def load_seen():
    # Heroku's filesystem is ephemeral, so state lives in the GitHub repo
    # instead of on disk. Returns (seen_ids_or_None, sha_or_None).
    r = requests.get(GITHUB_API, headers=GITHUB_HEADERS, params={"ref": GITHUB_BRANCH}, timeout=30)
    if r.status_code == 404:
        return None, None
    r.raise_for_status()
    data = r.json()
    content = base64.b64decode(data["content"]).decode()
    return set(json.loads(content)), data["sha"]

def save_seen(ids, sha):
    content = base64.b64encode(json.dumps(sorted(ids)).encode()).decode()
    payload = {"message": "update state", "content": content, "branch": GITHUB_BRANCH}
    if sha:
        payload["sha"] = sha
    r = requests.put(GITHUB_API, headers=GITHUB_HEADERS, json=payload, timeout=30)
    r.raise_for_status()
    return r.json()["content"]["sha"]

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

def notify(l):
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
        r = requests.post(WEBHOOK, json=payload, timeout=30)
        if r.status_code == 429:
            wait = r.json().get("retry_after", 1) + 0.5
            print(f"Rate limited, waiting {wait}s")
            time.sleep(wait)
            continue
        r.raise_for_status()
        return
    raise RuntimeError(f"Gave up notifying after retries: {l['title']}")

def main():
    listings = fetch_listings()
    current  = {l["id"] for l in listings}
    seen, sha = load_seen()
    if seen is None:
        save_seen(current, sha)
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
            seen.add(l["id"])
            sha = save_seen(seen | current, sha)
            time.sleep(1.2)

if __name__ == "__main__":
    main()
