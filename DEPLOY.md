# Deploying to DigitalOcean

`bot.py` now runs as a long-lived process that polls `SEARCH_URL` every
`POLL_INTERVAL_SECONDS` (default 5s) instead of relying on GitHub Actions'
cron, which can't go below 1-minute granularity and is often several
minutes late in practice. Running it on a droplet gets you real seconds-level
notifications.

The workload is tiny (one HTTP GET + HTML parse per poll), so the cheapest
droplet is enough — see the cost breakdown at the bottom.

## 1. Create the droplet

Via the web UI (Create → Droplets):
- Image: Ubuntu 24.04 LTS
- Plan: Basic → Regular → $4/mo (512MB/1vCPU) or $6/mo (1GB/1vCPU) if you want headroom
- Region: closest to you (doesn't affect the bot's function, just SSH latency)
- Auth: SSH key (recommended over password)

Or with `doctl`:
```bash
doctl compute droplet create crous-bot \
  --region fra1 \
  --image ubuntu-24-04-x64 \
  --size s-1vcpu-512mb-10gb \
  --ssh-keys <your-ssh-key-fingerprint>
```

## 2. Option A — Docker (recommended, easiest to update/restart)

SSH in, install Docker, then deploy:
```bash
ssh root@<droplet-ip>

curl -fsSL https://get.docker.com | sh

git clone <your-repo-url> /opt/crous-bot
cd /opt/crous-bot
cp .env.example .env
nano .env   # fill in SEARCH_URL, DISCORD_WEBHOOK_URL, POLL_INTERVAL_SECONDS

docker compose up -d --build
```

Check it's running / watch logs:
```bash
docker compose logs -f
```

Update later (after pulling new code):
```bash
git pull
docker compose up -d --build
```

## 2. Option B — systemd (no Docker, slightly leaner)

```bash
ssh root@<droplet-ip>

apt update && apt install -y python3-venv git
useradd -r -m -s /bin/false crous-bot

git clone <your-repo-url> /opt/crous-bot
cd /opt/crous-bot
python3 -m venv venv
./venv/bin/pip install -r requirements.txt

cp .env.example .env
nano .env   # fill in SEARCH_URL, DISCORD_WEBHOOK_URL, POLL_INTERVAL_SECONDS

chown -R crous-bot:crous-bot /opt/crous-bot

cp deploy/crous-bot.service /etc/systemd/system/
systemctl daemon-reload
systemctl enable --now crous-bot
```

Check status / logs:
```bash
systemctl status crous-bot
journalctl -u crous-bot -f
```

## 3. Firewall

The bot only makes outbound requests (to the CROUS site and Discord) — it
doesn't need any inbound ports open. Lock the droplet down to SSH only:
```bash
ufw allow OpenSSH
ufw enable
```

## 4. Tuning the poll interval

`POLL_INTERVAL_SECONDS` in `.env` controls how often it checks. 5s is a
reasonable floor — going much lower increases the chance of tripping
Cloudflare/anti-bot protection on the CROUS site and getting temporarily
blocked, which would cost you more latency than it saves. If you get
blocked, back off to 10-15s.

## Cost

At $4-6/mo for a droplet this size, and bandwidth usage well under the
500GB-1000GB included in that plan even at a 5s poll interval, your $200
credit lasts roughly 33-50 months (2.5-4 years) of continuous operation —
not days, not months. Just avoid attaching extras you don't need (managed
databases, load balancers, Kubernetes, backups/snapshots) since those are
billed separately.

## GitHub Actions

The old `.github/workflows/crous.yml` cron trigger has been disabled to
avoid double notifications (it kept its own separate `seen.json` state in
the repo, so it would re-detect and re-ping the same listings the droplet
already caught). `workflow_dispatch` is still available if you want to
trigger it manually, e.g. as a one-off backup check.
