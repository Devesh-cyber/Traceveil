# Traceveil

A private tool to help someone find and respond to **intimate images or videos of themselves that were shared without their consent** (NCII, including leaked MMS content).

You give it a picture or a short video that you are entitled to use. It looks through a researched list of websites for copies, shows you what looks similar, lets **you** decide what is real, keeps evidence, and helps you write removal requests that **you** send yourself.

It is built for the person affected. It is not a general reverse-image-search engine, and it is not a way to find intimate content of other people.

> **Status: hackathon prototype.** It works end to end (see [What works](#what-works-and-what-does-not)), but it is not hardened for the public internet yet. Read [Known limits](#known-limits) before showing it to anyone as "finished".

---

## The rules this project is built on

These came from the project brief and shaped every design decision. If you change the code, please keep them true.

| Rule | How the code keeps it |
| --- | --- |
| **Be honest about coverage.** We search a fixed list, not "the internet". | Every website gets a status: `checked`, `no_images`, `blocked`, `unreachable` or `skipped`. Anything but `checked` is shown in red as **NOT searched**. A scan where nothing could be compared is `failed`, never "completed, no matches". |
| **A similarity score is not proof.** | No invented "97% confidence". A match carries a plain label (`exact`, `near_duplicate`, `structural`, `probable`) and the raw measurements behind it. |
| **Potential, reviewed and verified are different things.** | Only a person can move a match past `potential`. `verified` needs a preserved copy and a written note. Machine video checks say `sequence_confirmed`, never "verified". |
| **A thumbnail match is not a video match.** | For matching thumbnails we find the page's real video, download only the first ~6 MB, and look for the user's scenes in the same order. Players, streams and script-built pages are reported as `not_checked`. |
| **Private by default.** | Every case, file, scan, match and takedown is owner-only (other users get a plain 404). Files are stored under random names with `0600` permissions, deleted after a retention period, and never appear in logs or public reports. Matched pictures are hidden in the UI until the user clicks. |
| **Nothing is sent for the user.** | There is no email or HTTP-sending code in the takedown module (a test checks this). The app writes drafts. The user sends them and records what happened. Nothing is ever reported to the police automatically. |
| **Safe crawling.** | `robots.txt` is obeyed, the crawler identifies itself with a contact address, requests are rate-limited per site, and the fetcher refuses private/internal addresses (SSRF protection), odd ports and unsafe redirects. |

---

## What works, and what does not

**Works (tested):**

- Accounts, private cases, owner-only access everywhere
- Reference picture **or** short video upload (validated, size/length limited)
- Image matching: perceptual hashes (pHash/dHash/aHash) against the original, mirrored and cropped versions, plus SIFT feature matching with a geometry check (survives crops, watermarks, overlays)
- Video: up to 16 distinct keyframes; every web picture is compared to all of them; the page's real video is sampled and checked for the same scenes in order
- Background worker with real progress, cancel, and recovery if the worker restarts
- Review levels, preserved copies of matched pictures (with SHA-256), audit trail
- Evidence report (Markdown or JSON) with a tamper-evident digest, coverage and takedown history
- Takedown drafts, a contacts list with a *verified* flag, status tracking (sent, replied, refused, no response, still online, removed)
- React interface for all of the above

**Not built yet (see [Step 13](#whats-left-step-13)):** rate limiting on the API, automatic deletion job, encryption of stored files, one-command Docker setup, JavaScript-rendered sites.

**Needs your team's research:** the list of websites and their abuse contacts are intentionally **empty/placeholder**. Nothing works usefully against real sites until you fill them in (see [Data files](#data-files-your-team-must-fill-in)).

---

## How it fits together

```
 Browser (React, :5173)
        │  /api  (Vite forwards to the backend in development)
        ▼
 FastAPI backend (:8000) ──────────► PostgreSQL (:5433 in Docker)
        │  queues scans in the database
        ▼
 Worker process  ── polite crawler ──► the websites in data/domains.csv
        │            (netguard, robots.txt, budgets)
        └─ matching: hashes → SIFT + RANSAC → video keyframes / sequence check
```

The API and the worker are **two separate processes**. A scan stays `queued` until the worker picks it up.

### Folder map

```
traceveil/
├── docker-compose.yml          PostgreSQL for development
├── .env.example                copy to .env
├── docs/decisions.md           why things are the way they are
├── backend/
│   ├── app/
│   │   ├── core/               settings, passwords/tokens, netguard (safe fetching), storage, audit
│   │   ├── db/ models/         database connection and tables
│   │   ├── schemas/            shapes of data in and out of the API
│   │   ├── api/                routes (one file per topic) and shared dependencies
│   │   ├── matching/           fingerprints, SIFT geometry, match labels
│   │   ├── crawler/            fetcher, domain list, page/image discovery, scan runner
│   │   ├── video/              keyframes, finding a page's video, sequence check
│   │   ├── takedown/           contacts list and notice drafting
│   │   ├── reporting/          the evidence report
│   │   └── worker/             the background scan worker
│   ├── data/                   domains.csv, contacts.csv, reporting_channels.json
│   ├── dev/                    fake test website and small try-it scripts
│   ├── migrations/             Alembic database migrations
│   └── tests/                  ~290 tests
└── frontend/                   React + TypeScript + Vite
```

---

## Run it on your machine

You need: **Git, Python 3.12+, Node 20+, Docker** (for the database). Works on Linux, macOS and Windows.

### 1. Get the code and configure

```bash
git clone <our repo url> traceveil
cd traceveil
cp .env.example .env          # Windows: copy .env.example .env
```

Open `.env` and check it looks like this (the database port must match `docker-compose.yml`, which uses **5433** so it does not clash with a Postgres you may already run):

```
ENVIRONMENT=development
DATABASE_URL=postgresql+psycopg://traceveil:traceveil_dev@localhost:5433/traceveil
SECRET_KEY=<a long random string, see below>
CORS_ORIGINS=["http://localhost:5173"]
CRAWLER_CONTACT=<an email address or URL that website owners can reach you on>
```

Make a secret key with: `python -c "import secrets; print(secrets.token_urlsafe(48))"`.
`CRAWLER_CONTACT` is sent to websites in the crawler's User-Agent so they know who is visiting. Use a real address before scanning real sites.

### 2. Start the database

```bash
docker compose up -d db
docker compose ps            # wait until it says "healthy"
```

### 3. Backend

```bash
cd backend
python -m venv venv
source venv/bin/activate     # Windows: venv\Scripts\activate
pip install -r requirements.txt -r requirements-dev.txt
alembic upgrade head         # creates the tables
```

Now start **two** terminals (both inside `backend/` with the venv active):

```bash
uvicorn app.main:app --port 8000        # the API
python -m app.worker.main               # the worker (runs the scans)
```

Check http://localhost:8000/api/health/db says `connected`. The API docs are at http://localhost:8000/docs.

### 4. Frontend

```bash
cd frontend
npm install
npm run dev                  # http://localhost:5173
```

Create an account, make a case, add a picture or short video, start a search.

### 5. Try it without any real website (recommended first)

A small fake website that contains copies of your test picture lives in `backend/dev/`. **Use an ordinary photo (a landscape, an object), never a private image.**

```bash
# terminal A (in backend/, venv active)
python -m dev.fake_site path/to/ordinary-photo.jpg

# stop the API and worker, then restart BOTH of them with these two extra settings:
export NETGUARD_ALLOW_LOOPBACK=true
export SCAN_DEV_TARGETS='["http://localhost:8001"]'
uvicorn app.main:app --port 8000
python -m app.worker.main
```

(Windows PowerShell: `$env:NETGUARD_ALLOW_LOOPBACK="true"` and `$env:SCAN_DEV_TARGETS='["http://localhost:8001"]'`.)

Upload that same photo as the reference, start a search, and you will get a real match with a preserved copy, which you can review, draft a removal request for, and put in the report. The loopback switch is refused unless `ENVIRONMENT=development`, so it cannot be left on by accident in production.

### 6. Run the tests

```bash
cd backend && pytest -q
```

- **Stop the worker first.** The tests create queued scans; a running worker could take them.
- The tests need the database running. They create their own throwaway users and delete them afterwards.
- Expect everything to pass. One test needs `ffmpeg` and skips itself if it is not installed (`sudo apt install ffmpeg` on Ubuntu).
- Frontend: `cd frontend && npm run build` type-checks and builds.

---

## Data files your team must fill in

These three files decide whether Traceveil is useful, and they are **research, not code**. Be careful and honest here.

| File | What goes in | Rules |
| --- | --- | --- |
| `backend/data/domains.csv` | Websites to search: `domain, category, media_types, notes, last_checked` | Only add sites you have looked at and understand. Read each site's `robots.txt` and terms. Write the real date you checked. Replace the `example.org` placeholder row. |
| `backend/data/contacts.csv` | Where to send a removal request: `domain, abuse_email, report_url, source_url, verified_on, notes` | A contact is **verified** only if you also give the page that shows it (`source_url`, https) and the date you checked (`verified_on`). Never guess `abuse@site`. Unknown sites correctly get no contact. |
| `backend/data/reporting_channels.json` | Official places a person can report to (India: cybercrime portal, StopNCII, platform grievance officers, police/lawyer) | Written from general knowledge and marked **unchecked**. Open each link, confirm it, fill in `last_checked`. India-specific legal wording (IT Rules, IPC/BNS sections) is deliberately **not** in the drafts: add it only after checking with someone qualified. |

Bad rows in the CSVs are reported as problems and never stop the good rows.

---

## Settings

All settings are environment variables (or lines in `.env`). The most useful ones:

| Setting | Default | Meaning |
| --- | --- | --- |
| `DATABASE_URL` | local Postgres | must match `docker-compose.yml` |
| `SECRET_KEY` | placeholder | signs login tokens; the app refuses to start in production with the placeholder |
| `CRAWLER_CONTACT` | `contact-not-configured` | shown to websites in the User-Agent |
| `RETENTION_DAYS` | 7 | how long uploaded files are kept before they are due for deletion |
| `MAX_UPLOAD_BYTES` | 10 MB | picture size limit |
| `MAX_VIDEO_BYTES`, `MAX_VIDEO_SECONDS` | 100 MB, 600 s | video limits |
| `VIDEO_VERIFY_ENABLED`, `VIDEO_VERIFY_MAX_CANDIDATES` | on, 6 | whether and how many page videos are downloaded per scan |
| `SCAN_MAX_PAGES_PER_DOMAIN`, `SCAN_MAX_TOTAL_IMAGES`, `SCAN_MAX_SECONDS`, `SCAN_PARALLEL_DOMAINS` | 15, 8000, 600, 6 | scan budgets. When one is hit, the scan says so (`truncated`) |
| `CRAWLER_MIN_DELAY_SECONDS` | 0.5 | polite pause between requests to one site |
| `NETGUARD_ENABLED` | true | the safe-fetch guard. **Never switch off outside local experiments** |
| `NETGUARD_ALLOW_LOOPBACK`, `SCAN_DEV_TARGETS` | off, none | development only (fake site) |

---

## API at a glance

All under `/api`, all (except register/login/health) need `Authorization: Bearer <token>`. Full interactive docs at `/docs`.

| Area | Endpoints |
| --- | --- |
| Auth | `POST /auth/register`, `POST /auth/login`, `GET /auth/me` |
| Cases | `GET/POST /cases`, `GET/DELETE /cases/{id}` |
| Files | `POST /cases/{id}/evidence` (picture), `POST /cases/{id}/evidence/video`, `GET /cases/{id}/evidence`, `DELETE /cases/{id}/evidence/{eid}` |
| Scans | `POST/GET /cases/{id}/scans`, `GET /scans/{id}`, `GET /scans/{id}/domains`, `GET /scans/{id}/matches`, `POST /scans/{id}/cancel` |
| Matches | `GET /matches/{id}`, `POST /matches/{id}/review`, `GET /matches/{id}/snapshot` |
| Takedowns | `POST /matches/{id}/takedown`, `GET /cases/{id}/takedowns`, `GET/PATCH/DELETE /takedowns/{id}`, `POST /takedowns/{id}/sent`, `POST /takedowns/{id}/status`, `GET /reporting-channels` |
| Report | `GET /cases/{id}/report?format=markdown (or json)&include_potential=false` |

---

## Known limits

Be upfront about these when you present the project.

- **Coverage is only as good as `domains.csv`.** It is a researched list, not the internet.
- **JavaScript-built pages are not rendered.** A site that only shows pictures after running scripts will be read as having none. Sites behind anti-bot systems are reported as `blocked`.
- **Match thresholds are not calibrated on real data.** They were tuned on synthetic test pictures. Expect false alarms and misses; this is why a person always reviews.
- **Video checks are cheap by design.** Only plain video files are downloaded (first ~6 MB). Embedded players and streams are not checked. A file whose index is at its end cannot be read from a partial download and is reported `not_checked`.
- **Not done:** finding *your picture inside someone else's video* (the reverse case).
- **Not yet hardened:** no rate limiting on the API, no encryption of stored files at rest, the retention date is stored but no job deletes expired files yet, and the login token lives in `sessionStorage` (an HttpOnly cookie would be better). Do not host this publicly until Step 13 is done.
- Run the video decoder (OpenCV) in a locked-down container if you ever accept uploads from strangers.
- This is software, not legal advice. Reporting and legal wording must be checked by the team and, ideally, a lawyer.

---

## What's left (Step 13)

1. Rate limiting on login, uploads and scans
2. A job that deletes expired files and records
3. Encryption of stored files
4. A one-command Docker setup (API, worker, frontend, database)
5. Moving the login token to an HttpOnly cookie
6. A short demo script with the fake site, and screenshots for the README

---

## Working on this as a team

- Branch from `main`, keep changes small, run `pytest -q` and `npm run build` before you push.
- **Never commit** `.env`, `backend/.data/` (uploaded evidence), `node_modules/`, or `venv/`.
- **Never use private or real intimate images** in tests, demos, screenshots, issues or chat. Use ordinary photos only.
- Never add code that sends a takedown, an email or a report to anyone on the user's behalf.
- When you add a website to `domains.csv`, write down what you checked in `notes` and use the real date.
- Decisions and the reasons for them go in `docs/decisions.md`.

## Troubleshooting

| Problem | Fix |
| --- | --- |
| A scan stays "Waiting for the worker" | The worker is not running: `python -m app.worker.main` in `backend/` |
| `connection refused` to the database | `docker compose up -d db`, then check the port in `.env` matches `docker-compose.yml` (5433) |
| Port 5432/5433 already in use | Change the left side of the port mapping in `docker-compose.yml` and the port in `DATABASE_URL` |
| Login works but the page shows "Could not reach the server" | The API is not running on port 8000 |
| A scan says "No websites are configured" | `backend/data/domains.csv` has no valid rows |
| A scan is `failed`: "No pictures could be compared" | Every site was blocked or unreachable. Open the per-site table to see why |
| `alembic` cannot find a revision | Run `alembic upgrade head` from `backend/`; do not edit migration files by hand |
| Tests fail oddly while the worker runs | Stop the worker before running `pytest` |
| Video upload says "cannot be read" | The file is not a standard video, is longer than 10 minutes, or is all black |

## Credits and licence

Inspired by the open-source **Sniffer** project (`icancodefyi/forensics-pipeline`); this codebase was rebuilt from scratch with its own structure, and a teammate's earlier fork (**Traceveil**, `SHIVAMYADAV25/Traceveil`) was used as a design reference, especially for the video approach.

**Licence: not chosen yet.** Decide as a team, and check what the upstream projects' licences require, before publishing. Until then, treat the code as "all rights reserved".