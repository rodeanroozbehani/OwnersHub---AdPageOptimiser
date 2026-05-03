# OwnersHub Ad & Page Optimiser

Local Python automation that pulls Google Ads performance, scrapes
+ screenshots [https://ownershub.com.au/](https://ownershub.com.au/), and uses
Claude (Anthropic Python SDK, multimodal) to produce joint **ads + landing
page** optimisation recommendations on a cron schedule.

Designed to run on a Beelink Ubuntu server, developed locally on Windows first.

**Read-only.** Never mutates the Ads account or the website — it only writes
Markdown reports + a JSON history file + per-run snapshots.

---

## What it does

| Mode  | Cadence            | Lookback | Behaviour                                                                                        |
| ----- | ------------------ | -------- | ------------------------------------------------------------------------------------------------ |
| full  | Sun 20:00 local    | 14 days  | Full Claude analysis. Always runs, always produces a report.                                     |
| light | Wed 20:00 local    | 3 days   | Threshold check only. Calls Claude **only** when spend / conversion / CTR move > configured Δ.   |

Output per run:

* `reports/YYYY-MM-DD-{full,light}.md` — human-readable
* `data/history.json` — append-only structured log (atomic writes)
* `data/site-snapshots/YYYY-MM-DD/` — html + desktop.png + mobile.png
* `logs/run.log` — rotating, 5 MB × 5 backups

---

## Quick start (Windows dev box)

```powershell
cd "C:\Users\Rodean\Documents\OwnersHub - Ad&Page-optimiser"

python -m venv .venv
.venv\Scripts\activate

pip install -r requirements.txt
python -m playwright install chromium

copy .env.example .env
# Edit .env and set ANTHROPIC_API_KEY

# Plumbing test (no API spend):
python main.py --mode full --dry-run

# Real Claude call:
python main.py --mode full

# Midweek light check (will likely log "no significant change" first time):
python main.py --mode light
```

## Quick start (Beelink Ubuntu)

```bash
cd ~ && git clone <your-repo-or-rsync> ads-optimizer && cd ads-optimizer
# Or: rsync -av "from-windows" rodean@beelink:~/ads-optimizer/

sudo timedatectl set-timezone Australia/Sydney

python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python -m playwright install --with-deps chromium

cp .env.example .env
chmod 600 .env
$EDITOR .env   # set ANTHROPIC_API_KEY

# Plumbing test:
python main.py --mode full --dry-run

# Schedule it: see scheduler.md
```

---

## Configuration

All knobs live in `config.yaml`. See inline comments. Key fields:

* `website.url` — target site to analyse
* `ads.mode` — `mock` (default) or `live`
* `ads.daily_budget_aud` — ~$67 currently; threshold and prompt anchor to this
* `thresholds.{spend,conversion,ctr}_change` — light-mode trigger sensitivity
* `claude.model` — defaults to `claude-sonnet-4-6` (multimodal)
* `storage.snapshot_retention_days` — old snapshots are pruned automatically

Secrets (never in `config.yaml`):

* `ANTHROPIC_API_KEY` — in `.env`, loaded by `python-dotenv` at startup
* `google-ads.yaml` — only required when `ads.mode: live`

---

## Switching to live Google Ads (currently stubbed)

`ads.mode: mock` is the default. To enable real data:

1. **Apply for a Google Ads developer token** in your manager (MCC) account.
   Approval takes 1–3 business days; basic-access tokens are usually granted
   immediately for read access.
2. **Create OAuth2 credentials** in Google Cloud Console (Desktop app type)
   and run a one-shot OAuth flow to obtain a `refresh_token`. The
   `google-ads` library docs walk through this end-to-end.
3. **Create `google-ads.yaml`** beside the project root with:

   ```yaml
   developer_token: "YOUR_DEV_TOKEN"
   client_id: "YOUR_CLIENT_ID.apps.googleusercontent.com"
   client_secret: "YOUR_CLIENT_SECRET"
   refresh_token: "YOUR_REFRESH_TOKEN"
   login_customer_id: "1234567890"   # MCC id, no dashes; omit if direct account
   use_proto_plus: true
   ```

   Then `chmod 600 google-ads.yaml`.
4. **Edit `config.yaml`**: set `ads.mode: live` and `ads.customer_id` to the
   account whose data you want pulled (no dashes).
5. **Implement `_fetch_live()` in `ads_optimizer/ads_client.py`**. It is
   currently a `NotImplementedError` stub with the exact integration points
   marked. Use `GoogleAdsService.search_stream` with GAQL — read-only.
6. Run `python main.py --mode full --dry-run` to confirm the live fetch works
   without paying for a Claude call.

---

## Operations

### Re-run the most recent full pass

```bash
python main.py --mode full
```

### Force a light pass that *will* call Claude (for testing)

Lower `thresholds.{spend,conversion,ctr}_change` in `config.yaml`
temporarily, run `--mode light`, then restore.

### Inspect history

```bash
cat data/history.json | python -m json.tool | less
```

### Clean up old screenshots manually (normally pruned automatically)

```bash
find data/site-snapshots/ -mindepth 1 -maxdepth 1 -type d -mtime +90 -exec rm -rf {} +
```

---

## Troubleshooting

**Playwright errors on Beelink** — you forgot `--with-deps`:

```bash
python -m playwright install --with-deps chromium
```

**Cron runs the script but nothing happens** — cron's `PATH` is minimal. Use
the `run.sh` wrapper described in `scheduler.md` rather than calling
`python main.py` directly from cron.

**Claude returns malformed JSON** — `claude_client.py` retries once with a
strict re-prompt before failing. If it still fails, the run still produces a
Markdown report describing the failure and appends a history entry; nothing is
silently dropped.

**Timezone weirdness in reports** — all stored timestamps are UTC. Date-stamped
filenames (`reports/2026-05-03-full.md`) use *local* date. If your Beelink
isn't on Australia/Sydney, run `sudo timedatectl set-timezone Australia/Sydney`.

**`history.json` corrupted** — the reporter writes atomically (`tmp` + rename).
If a run was killed mid-write, the on-disk file should still be the previous
valid version. If it isn't, delete `history.json` — the next full run will
recreate it.

---

## Project layout

```
.
├── main.py                       # CLI entry point
├── config.yaml                   # User config (no secrets)
├── .env / .env.example           # ANTHROPIC_API_KEY
├── requirements.txt
├── scheduler.md                  # Cron + systemd timer setup
├── run.sh                        # Wrapper used by cron / systemd
├── ads_optimizer/                # Python package
│   ├── ads_client.py             # mock | live router
│   ├── ads_mock.py               # Realistic mock dataset
│   ├── claude_client.py          # Anthropic SDK wrapper, multimodal
│   ├── config_loader.py
│   ├── logging_setup.py
│   ├── reporter.py               # Markdown + JSON + retention
│   ├── runner.py                 # full + light orchestration
│   ├── thresholds.py             # Period-over-period evaluator
│   └── website_analyzer.py       # requests + BeautifulSoup + Playwright
├── prompts/
│   ├── optimizer.txt             # Full mode
│   └── light_check.txt           # Threshold-breach diagnosis
├── data/
│   ├── history.json
│   └── site-snapshots/<date>/
├── reports/<date>-{full,light}.md
├── logs/run.log
├── systemd/                      # Service + timer units (see scheduler.md)
└── tests/
```

---

## Safety constraints

* No `Mutate` operations against the Google Ads account — read-only GAQL.
* Claude key never written to disk except in `.env` (chmod 600).
* `--dry-run` flag for full plumbing tests without Claude API spend.
* Wall-clock cap (default 600s) so a hung run can't sit indefinitely.
* `flock` lockfile in cron to prevent overlapping runs.
* Snapshots auto-prune after `snapshot_retention_days` (default 90).

---

## Contact

Maintainer: rodean.r@gmail.com
