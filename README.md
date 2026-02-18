# NRL Tipping Competition App

Full local NRL tipping web app with:

- account registration + login (email/password + Facebook OAuth)
- weekly round tipping (tips lock 5 minutes before kickoff)
- round tipsheet view that unlocks everyone's picks after all users submit
- automatic underdog picks for any missed locked fixture
- automatic scoring from match results (auto-check runs 2 hours after kickoff)
- overall + round leaderboard
- profile page with avatar upload (shown on tipsheet)
- admin season sync from The Odds API
- full season raw download saved to `data/nrl_season_<year>.json`
- all user-facing times shown in `Australia/Sydney` (AEST/AEDT)
- login opens directly to current round tipping picks

## Tech

- Python 3.10+ only (no external dependencies)
- SQLite database at `data/nrl_tipping.db`

## Quick Start

0. Place your logo image at:

```text
static/aldo.png
```

1. Start the web app:

```powershell
python app.py
```

Or run on a specific port (example 3200):

```powershell
python app.py 3200
```

2. Open:

```text
http://127.0.0.1:8080
```

If testing on your phone, use your computer's LAN IP, e.g. `http://192.168.x.x:8080`.
Do not use `localhost` on your phone, because that points to the phone itself.

3. Default admin created on first run:

- email: `admin@nrltips.local`
- password: `ChangeMe123!`

Set these env vars before running `app.py` to override defaults:

- `NRL_ADMIN_EMAIL`
- `NRL_ADMIN_NAME`
- `NRL_ADMIN_PASSWORD`

Optional Facebook login config:

- `FACEBOOK_APP_ID`
- `FACEBOOK_APP_SECRET`
- `FACEBOOK_GRAPH_VERSION` (default `v19.0`)
- `FACEBOOK_OAUTH_SCOPES` (default `email,public_profile`)

Facebook config can be provided either as environment variables or in `.env` at the project root.
The OAuth callback URL must match your running app URL, for example:

- `http://127.0.0.1:3200/auth/facebook/callback`
- `http://localhost:3200/auth/facebook/callback`

Avatar uploads:

- max size controlled by `MAX_AVATAR_BYTES` (default `5242880`, 5 MB)
- local uploads are saved under `static/avatars/`

Tip lock window:

- `TIP_LOCK_MINUTES` (default `5`)

Automatic score updater (runs inside web app process):

- `AUTO_SCORE_UPDATER_ENABLED` (default `1`)
- `AUTO_SCORE_MIN_AGE_HOURS` (default `2`)
- `AUTO_SCORE_CHECK_INTERVAL_SECONDS` (default `900`)

## Odds API Key Loading

The sync process loads `ODDS_API_KEY` in this order:

1. `ODDS_API_KEY` environment variable
2. `NFTEAMS_ENV_PATH` file (`.env` style), defaulting to:
   `C:\Users\steoo\.gemini\antigravity\scratch\NFTeams_automation\.env`

So your existing key in `NFTeams_automation` is automatically used unless you override it.

## Sync Entire Season Data

### From admin UI

- log in as admin
- open `/admin`
- click `Sync entire season`

### From CLI

```powershell
python -m scripts.sync_nrl_season --season-year 2026 --days-back 30
```

Use `--keep-other-seasons` if you do not want to remove previous seasons from the DB.

This will:

- fetch upcoming NRL odds
- fetch completed NRL scores
- fetch weekly historical snapshots for the season
- strictly match official NRL Telstra Premiership draw (`competition=111`)
- cap rounds to regular season `1-27` only
- enrich fixtures with official NRL draw round numbers, kickoff times, and team logos
- merge + upsert fixtures
- prune fixtures from other seasons by default
- reassign round numbers
- auto-fill missing locked tips to the underdog (longer decimal odds)
- recalculate tipping points
- write the raw season payload to `data/nrl_season_<year>.json`

## Create/Reset Admin Manually

```powershell
python scripts/create_admin.py --email admin@nrltips.local --name Admin --password "NewStrongPass123!"
```

## Score Update Script

One-time score update:

```powershell
python -m scripts.update_scores --season-year 2026 --min-age-hours 2
```

Continuous loop mode:

```powershell
python -m scripts.update_scores --loop --season-year 2026 --min-age-hours 2 --interval-seconds 900
```

## Notes

- If The Odds API historical endpoint is unavailable for your plan, sync still imports all data available from `/odds` and `/scores`.
- If `/scores` rejects your requested `daysFrom` (422), the app automatically retries with supported values.
- Leaderboard updates automatically after each sync.

## Install As Phone App (PWA)

- The app now includes:
  - `manifest.webmanifest`
  - service worker (`/service-worker.js`)
  - app icons from `static/aldo.png` (`icon-192.png`, `icon-512.png`)
- For real installs, serve the site over HTTPS (localhost is fine for development only).
- Android Chrome: open site -> browser menu -> `Install app`.
- iPhone Safari: open site -> Share -> `Add to Home Screen`.

