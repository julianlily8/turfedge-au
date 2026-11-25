# TurfEdge AU — $0-Cost Australian Horse Racing Analysis & Profitability App

A 100% free ($0 spend, no credit card, no Blaze billing) Australian Metro & Provincial thoroughbred analysis and POT% tracking web application running autonomously in the cloud via **Firebase Spark Plan** + **GitHub Actions**.

## Architecture
- **Frontend:** React + TypeScript + Vite + Tailwind CSS + Recharts hosted on **Firebase Hosting** (Spark free plan).
- **Database:** **Cloud Firestore** (Spark free plan — public read, Admin-SDK-only writes from GitHub Actions).
- **Cloud Automation (GitHub Actions, off-peak cron minutes):**
  1. `morning-ingest.yml` (`27 21/22 * * *` UTC, Australia/Sydney 08:xx guard): Ingests AUS Metro/Provincial meetings, fields, barriers, weights/claims, jockeys, trainers, rail placement, track ratings, opening prices, and BOM weather/wind (with Open-Meteo BOM ACCESS-G fallback) using ≥3.0s polite rate limiting + exponential backoff.
  2. `pre-jump-analysis.yml` (`7,22,37,52 22-23,0-9 * * *` UTC): Widened **T-5..T-60m** scan window refreshes live odds, computes implied probability firming/drifting %, updates live meeting track bias, fetches configured YouTube preview transcripts (`youtube-transcript-api`), and batch-analyzes by meeting using `gemini-3.1-flash-lite` (≥10s spacing + strict anti-hallucination `5.0/10` neutral fallback for missing text fields).
  3. `post-race-settle.yml` (`13,58 0-13 * * *` UTC + `17 22 * * *` next-morning BSP sync): Pulls official finishing orders, VIC/NSW/QLD tote & exotic dividends, and Betfair daily public BSP CSVs (`dwbfpricesauswin{DDMMYYYY}.csv`) to settle standardized $10 unit stakes (including `0.75x` SRM correlation haircut) and update POT% metrics.
  4. `keep-alive.yml` (`19 3 1 * *` UTC): Monthly GitHub REST API workflow re-enabler so scheduled crons never auto-disable after 60 days of repo inactivity.
  5. `deploy-web.yml`: Automatically rebuilds and deploys the React web app to Firebase Hosting on push to `main`.

## Tuning the 23-Point Weights & Adding YouTube Previews
- Edit [pipeline/config/factor_weights.json](pipeline/config/factor_weights.json) and push to `main` (guided by Tab 2's Factor Attribution diagnostics).
- Add daily YouTube preview URLs in [pipeline/config/youtube_urls.json](pipeline/config/youtube_urls.json) (or Firestore `config/youtube_previews`).

## One-Time Cloud Deployment Commands
```bash
# 1. Authenticate GitHub CLI & Firebase CLI
gh auth login
firebase login

# 2. Initialize Firebase project (Spark free plan) & deploy Firestore rules + indexes
firebase use --add
firebase deploy --only firestore

# 3. Set GitHub Secrets (Service Account JSON, Project ID, Free Gemini API Key, Web SDK config)
gh secret set FIREBASE_PROJECT_ID -b "<your-project-id>"
gh secret set FIREBASE_SERVICE_ACCOUNT < /path/to/serviceAccountKey.json
gh secret set GEMINI_API_KEY -b "<your-aistudio-free-key>"
gh secret set LADBROKES_FROM_EMAIL -b "<your-email>"
# Plus VITE_FIREBASE_* web config values for deploy-web.yml

# 4. Deploy web app & trigger initial cloud run
cd web && npm run build && cd ..
firebase deploy --only hosting
gh workflow run morning-ingest.yml
```
