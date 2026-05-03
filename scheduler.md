# Scheduler setup

Two ways to run this on the Beelink. **systemd timers are recommended** —
better logging via `journalctl`, cleaner failure semantics, no `MAILTO` hacks.
Cron is documented for completeness.

Times below assume the server is on `Australia/Sydney`. Set it once:

```bash
sudo timedatectl set-timezone Australia/Sydney
timedatectl   # verify
```

---

## Option A — systemd timers (recommended)

Adjust the absolute paths inside the four unit files in `./systemd/` to match
where you put the project on the Beelink (default below assumes
`/home/rodean/ads-optimizer/`).

```bash
# 1. Copy the wrapper into the project dir and make it executable
chmod +x run.sh

# 2. Install the units
sudo cp systemd/ads-optimizer-full.service     /etc/systemd/system/
sudo cp systemd/ads-optimizer-full.timer       /etc/systemd/system/
sudo cp systemd/ads-optimizer-light.service    /etc/systemd/system/
sudo cp systemd/ads-optimizer-light.timer      /etc/systemd/system/

# 3. Reload + enable + start
sudo systemctl daemon-reload
sudo systemctl enable --now ads-optimizer-full.timer
sudo systemctl enable --now ads-optimizer-light.timer

# 4. Verify the schedule
systemctl list-timers --all | grep ads-optimizer

# 5. Force a one-off run to confirm everything works:
sudo systemctl start ads-optimizer-full.service
journalctl -u ads-optimizer-full -e --no-pager
```

Logs go to `logs/run.log` (rotated by Python) AND `journalctl -u <unit>`.

### Failure notifications via systemd

The simplest path is `OnFailure=` plus `msmtp` for SMTP. After installing
`msmtp` and configuring it for your Gmail account, drop this in
`/etc/systemd/system/ads-optimizer-failure-email.service`:

```ini
[Unit]
Description=Email Rodean when ads-optimizer fails

[Service]
Type=oneshot
ExecStart=/bin/sh -c 'echo "ads-optimizer failed at $(date) on $(hostname). See journalctl -u ads-optimizer-full -e (or -light)." | msmtp rodean.r@gmail.com'
```

Then add `OnFailure=ads-optimizer-failure-email.service` to the `[Unit]`
section of both `.service` files and `daemon-reload`.

---

## Option B — cron

```bash
# Edit your user crontab
crontab -e
```

Add:

```cron
# OwnersHub Ad & Page Optimiser
MAILTO=rodean.r@gmail.com
PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin

# Full pass — Sunday 20:00 local
0 20 * * 0  flock -n /tmp/ads-optimizer.lock /home/rodean/ads-optimizer/run.sh full

# Light pass — Wednesday 20:00 local
0 20 * * 3  flock -n /tmp/ads-optimizer.lock /home/rodean/ads-optimizer/run.sh light
```

`flock -n` prevents overlapping runs if a job hangs. `MAILTO` mails any stderr
output to you (requires a working local MTA).

---

## Windows Task Scheduler (dev box only)

For local testing on Windows, this works fine instead of cron / systemd.
Open Task Scheduler → Create Task...

* **Trigger:** weekly, Sunday 20:00 (and again Wednesday 20:00 for light)
* **Action:** Start a program
  * Program: `C:\Users\Rodean\Documents\OwnersHub - Ad&Page-optimiser\.venv\Scripts\python.exe`
  * Arguments: `main.py --mode full`  (or `--mode light` for the second task)
  * Start in: `C:\Users\Rodean\Documents\OwnersHub - Ad&Page-optimiser`
* **Settings:** "Run only when user is logged on" is fine for dev. The
  Beelink is the production scheduler.

---

## What the wrapper script does

`run.sh` (project root):

```sh
#!/usr/bin/env sh
set -eu
PROJECT_DIR="$(dirname "$(readlink -f "$0")")"
cd "$PROJECT_DIR"
# shellcheck disable=SC1091
. .venv/bin/activate
export PLAYWRIGHT_BROWSERS_PATH="${PLAYWRIGHT_BROWSERS_PATH:-$HOME/.cache/ms-playwright}"
exec python main.py --mode "$1"
```

Why a wrapper:

* cron has a minimal `PATH`; activating the venv from a script avoids
  fighting the environment.
* `PLAYWRIGHT_BROWSERS_PATH` makes browser lookup deterministic across
  cron / interactive shells.
* `cd` ensures relative paths in `config.yaml` resolve correctly.
