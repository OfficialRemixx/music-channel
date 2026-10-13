# RUNBOOK

## 0. What's already built (P0 scaffold)

- `config.yaml` -- every tunable (niche, cadence, provider, thresholds) lives
  here, nothing hard-coded. `dry_run: true` by default.
- `common/` -- config loader, atomic idempotent state (`state.json`), logging
  with secret masking.
- `generate/` -- `MusicProvider` interface + two stub backends
  (`kaggle_acestep` default, `cc_library` fallback). Both fail safe (raise or
  return empty) until their TODOs are done.
- `trend.py`, `qc.py`, `mix.py`, `visuals.py`, `seo.py`, `upload.py`,
  `analytics.py`, `policy_watch.py`, `notify.py` -- one module per pipeline
  stage, each a documented stub that is safe to run in `dry_run` and refuses
  (raises `NotImplementedError`) rather than guessing once `dry_run` is off.
- `.github/workflows/` -- `ci.yml` runs lint + tests on every push (real,
  working today). The pipeline workflows (A1/A2/B/B2/B3/C/D/keepalive) exist
  with pinned action SHAs, concurrency, timeouts and least-privilege
  permissions, but their `schedule:` triggers are commented out until the
  phase that wires them for real is signed off.
- `tests/test_scaffold.py` -- smoke tests proving every stub fails safe.
- `.devcontainer/` -- so this repo is buildable and testable from GitHub
  Codespaces with zero local setup.

Nothing above talks to YouTube, Kaggle, Telegram, Gemini, or Pexels yet. That
is intentional: the master prompt's rule 0.2 says never guess an API field,
endpoint, or repo -- each of those gets wired only after its official docs are
read at build time and its account exists.

## 1. One-time manual steps (do these yourself; nobody else can)

Work through these in order. Each one needs your own account/credentials,
which nothing automated should ever type in for you.

1. **GitHub**: this repo should be **public** (recommended by the master
   prompt itself, Section 5.0) -- standard runners are free and unlimited on
   public repos, vs. 2,000 min/month on a private Free-plan repo, and a single
   long render can eat a big share of that. If you'd rather keep it private,
   say so and the CI/workflow files don't need to change, just your repo
   visibility setting.
   - Push this scaffold to a new repo.
   - Settings -> Environments -> New environment `production` -> add yourself
     as a required reviewer (this is the approval gate in `b-build.yml`).
   - Open this repo in a Codespace at least once to confirm `.devcontainer`
     builds cleanly.
2. **YouTube channel**: create it (a Brand Account is recommended so it isn't
   tied to your personal Google identity), then phone-verify it in YouTube
   Studio -> Settings -> Channel -> Feature eligibility (needed for custom
   thumbnails and videos over 15 min). Turn on 2-step verification on the
   Google account that owns it.
3. **Kaggle account**: sign up, verify your phone number (required for GPU +
   internet access on notebooks), then Account -> Create New API Token to get
   `kaggle.json` (this becomes `KAGGLE_USERNAME` / `KAGGLE_KEY` secrets).
4. **Google Cloud project** (for the YouTube Data API v3 + YouTube Analytics
   API):
   - console.cloud.google.com -> new project -> enable "YouTube Data API v3"
     and "YouTube Analytics API" (and "Google Drive API" if you keep the
     default `storage: gdrive`).
   - APIs & Services -> OAuth consent screen -> fill it in, then **click
     "Publish App"** (In production) -- while it's in "Testing" status,
     refresh tokens expire after 7 days, which will silently break the
     pipeline a week after setup.
   - APIs & Services -> Credentials -> Create OAuth client ID (Desktop app) ->
     download the client secret.
   - A one-time local script (added in Phase P3) exchanges that for a
     refresh token -- you run it once on your own machine and paste the
     refresh token into GitHub Secrets, never into this repo.
5. **GitHub Pages privacy policy + the API compliance audit**: once the repo
   exists, enable Pages (Settings -> Pages) and we'll add a minimal privacy
   policy + terms page (Phase P6). Then submit Google's API compliance audit
   / quota extension form, answering truthfully: single-owner automation for
   your own channel. Until it passes, uploaded videos stay private regardless
   of what's requested, and you publish manually from the Studio app --
   `upload.py`'s audit-lock detection (Section 5.7) is what notices this.
6. **Telegram bot**: message @BotFather -> `/newbot` -> save the token. Then
   message your new bot once and fetch your chat id (e.g. via
   `https://api.telegram.org/bot<token>/getUpdates`) -> save as
   `TELEGRAM_CHAT_ID`.
   **Pexels API key**: pexels.com/api -> sign up -> copy the key.
   **Gemini API key**: aistudio.google.com -> Get API key (free tier).
7. **(Optional) Modal account** -- only needed if you want the failover
   generation path; skip for now, it's disabled by default in config.

Store every secret in GitHub Settings -> Secrets and variables -> Actions,
using the exact names in `.env.example`. Never commit `.env`.

## 2. Build phases (P0 done; what's next)

See the master prompt (Section 10) for the full phase list. The short version
of what's left, in order: P1 wires real Kaggle+ACE-Step generation and sends
you preview tracks to approve the sound; P2 adds real mixing/visuals/ffmpeg
and produces one finished video; P3 wires OAuth + a real (private) upload and
proves the disclosure/claim-gate/audit-lock fields actually work; P4 turns on
the scheduled workflows and the approval Environment; P5 adds the analytics
feedback loop and policy-change alerts; P6 is the README/RUNBOOK polish, the
privacy-policy page, the audit submission, and a 14-day manual-publish
burn-in before anything auto-publishes.

Each phase should end with a dry-run you can see the output of before the
next phase starts -- don't skip ahead just because the scaffold runs clean.

## 3. Troubleshooting

- **A workflow's schedule never fires**: it's commented out on purpose until
  its phase is signed off (see `.github/workflows/*.yml` comments). Use
  "Run workflow" (`workflow_dispatch`) to test manually first.
- **CI fails on a fresh clone**: check it's not a network-dependent step --
  `pytest` with `FORCE_DRY_RUN=true` should never need real credentials.
- **A stub raises `NotImplementedError`**: that's intentional (rule 0.2 /
  R9 -- fail safe rather than guess). The module's docstring says exactly
  what manual step and which doc to read before it can be filled in.
- **Swapping a broken free tool**: every external dependency is isolated
  behind an interface (`MusicProvider` for generation, `storage:` in config
  for the file pool) specifically so a dead free tier can be swapped for
  another without touching the rest of the pipeline.
