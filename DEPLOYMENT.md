# Deployment Runbook

A step-by-step checklist for shipping this app to **Neon + Render + Vercel**
(all free tiers). Follow the steps **in order** — each one needs a value
produced by the step before it. Tick boxes as you go.

```
Git → Neon (DB) → Render (API) → Vercel (frontend) → Google OAuth (optional)
    → Tavily (optional) → close the CORS loop → verify everything
```

---

## 0. Pre-flight

Do these once, before touching any dashboard.

- [ ] **Run the backend test suite** — 47 tests, no network calls:
  ```bash
  cd backend
  pip install -r requirements-dev.txt
  python -m pytest -q
  ```
- [ ] **Confirm the frontend builds clean**:
  ```bash
  cd frontend
  npx tsc --noEmit && npm run lint && npm run build
  ```
- [ ] **Re-run the migrations locally** against a throwaway DB to confirm
  `alembic upgrade head` applies cleanly from scratch (catches a migration
  that only "works" because your dev DB already has stray manual fixes):
  ```bash
  cd backend
  DATABASE_URL='postgresql+psycopg://rag:rag@localhost:5432/rag' alembic current
  # should print: 0004_semantic_cache (head)
  ```
- [ ] **Secret audit** — confirm nothing real is about to be committed:
  ```bash
  git add -A --dry-run 2>/dev/null | sed "s/^add '//;s/'$//" > /tmp/committable.txt
  grep -InE 'tvly-[A-Za-z0-9_-]{10,}|AIza[0-9A-Za-z_-]{20,}|sk-[A-Za-z0-9_-]{20,}|GOCSPX-[A-Za-z0-9_-]{10,}|AKIA[0-9A-Z]{16}|-----BEGIN [A-Z ]*PRIVATE KEY-----' \
    $(cat /tmp/committable.txt) 2>/dev/null && echo "STOP — review above" || echo "clean"
  ```
- [ ] Confirm `backend/.env` and `frontend/.env.local` are **not** in that
  committable list (they hold your real local keys) and that both
  `.env.example` files **are** in it (they're the templates, safe to commit).

---

## 1. Push to GitHub

**This repo currently has no commits and no remote.** Render and Vercel both
deploy by connecting to a GitHub repo, so this has to happen first.

- [ ] Create an empty repo on GitHub (no README/license — this repo already
  has one): `gh repo create <name> --private --source=. --remote=origin`, or
  create it in the web UI and add the remote yourself.
- [ ] Commit and push:
  ```bash
  git add -A
  git commit -m "Initial commit"
  git branch -M main
  git remote add origin <your-repo-url>   # skip if `gh repo create` already did this
  git push -u origin main
  ```
- [ ] Sanity-check on GitHub's web UI that `backend/.env`, `frontend/.env.local`,
  and no API keys appear anywhere in the pushed tree.

---

## 2. Neon — Postgres with pgvector

- [ ] Create a project at [neon.tech](https://neon.tech). Pick a region near
  where Render will run (Render `oregon` → Neon `us-west-2`).
- [ ] **Dashboard → Connect** → copy the connection string. Take the
  **direct** host, *not* the `-pooler` one — SQLAlchemy's psycopg v3 driver
  uses prepared statements, which break against PgBouncer's transaction
  pooling mode, and this app's traffic is far too low to need pooling anyway.
- [ ] Rewrite the scheme for SQLAlchemy + psycopg v3 (keep `sslmode=require`):
  ```
  # Neon gives you:
  postgresql://USER:PASSWORD@ep-xxx.us-west-2.aws.neon.tech/neondb?sslmode=require

  # You need (note the +psycopg):
  postgresql+psycopg://USER:PASSWORD@ep-xxx.us-west-2.aws.neon.tech/neondb?sslmode=require
  ```
- [ ] Run all four migrations against it from your machine (Neon is publicly
  reachable; Render's free tier has no pre-deploy hook to do this for you):
  ```bash
  cd backend
  source .venv/bin/activate
  DATABASE_URL='postgresql+psycopg://...neon.tech/neondb?sslmode=require' alembic upgrade head
  ```
  Migration `0002` runs `CREATE EXTENSION IF NOT EXISTS vector`, so pgvector
  is enabled as part of this — no separate step needed.
- [ ] Verify:
  ```bash
  psql 'postgresql://...neon.tech/neondb?sslmode=require' \
    -c "SELECT extname FROM pg_extension WHERE extname='vector';" \
    -c "\dt"
  ```
  Expect the `vector` extension plus seven tables: `users`, `notebooks`,
  `sources`, `chunks`, `chat_messages`, `llm_calls`, `llm_cache`.
- [ ] Save the `postgresql+psycopg://...` string — Render needs it next.

---

## 3. Render — FastAPI backend

The repo ships [`render.yaml`](render.yaml) as a Blueprint, so Render mostly
configures itself.

- [ ] Render Dashboard → **New → Blueprint** → select the repo you pushed in
  step 1. Render reads `render.yaml` and proposes a `notebooklm-rag-api` web
  service.
- [ ] Render prompts for every variable marked `sync: false`. Fill in:

  | Variable | Value | Required? |
  |---|---|---|
  | `DATABASE_URL` | the Neon connection string from step 2 | **yes** |
  | `GEMINI_API_KEY` | from [aistudio.google.com/apikey](https://aistudio.google.com/apikey) | **yes** |
  | `CORS_ORIGINS` | any placeholder for now (e.g. `http://localhost:3000`) — you'll set the real Vercel URL in step 6 | **yes** |
  | `GROQ_API_KEY` | from [console.groq.com/keys](https://console.groq.com/keys) — genuinely free, no card | optional |
  | `GOOGLE_CLIENT_ID` | leave empty for now; comes back in step 5 if you want sign-in | optional |
  | `TAVILY_API_KEY` | from [app.tavily.com](https://app.tavily.com/) if you want the "search the web" source-add feature | optional |

  > **`GROQ_API_KEY` fills two hops in the fallback chain** — it's actually
  > free, no card, resets daily (unlike an earlier Kimi/Moonshot AI attempt
  > at this slot, which needed a paid recharge, and `llama-3.3-70b-versatile`
  > before it, which Groq later dropped from its free tier). Current models:
  > `openai/gpt-oss-120b` then `openai/gpt-oss-20b`, each 30 RPM / 1K RPD /
  > 8K TPM / 200K TPD. This account's Gemini Flash models are tighter still —
  > 5 RPM / 20 RPD each — so `app/rate_limits.py` proactively skips any hop
  > that's out of quota rather than waiting for a 429. If the Groq key is
  > invalid, that specific hop raises a fatal (not retryable) error, which
  > per the current design aborts the *whole* chain rather than continuing
  > to the next hop — same failure mode as before, just now inside a 5-hop
  > chain instead of a single fallback.

- [ ] Deploy. Build installs `requirements-ingest.txt` — no local ML model,
  no PyTorch (embeddings are a hosted Gemini API call now — see
  `app/embeddings.py` — so nothing to pre-download or warm at boot).
- [ ] Verify: `curl https://<your-service>.onrender.com/health` → `{"status":"ok"}`
- [ ] Note your Render URL — Vercel needs it next.

**If the build or boot fails:**

| Symptom | Cause | Fix |
|---|---|---|
| `429`/rate-limited chat responses | This account's Gemini Flash models cap at 20 requests/**day** each — genuinely easy to exhaust with a 5-hop chain on a busy day | Check `/stats` for `by_model` counts, or wait for the daily reset; not a bug |
| First request after idle is slow | Expected — free services spin down after ~15 min idle | None needed; cold start is 30–60 s |
| Source ingestion OOMs (`Ran out of memory`, exit 137) | Parsing (not embedding, which is hosted now) still runs in-process — a burst of concurrent large sources can exceed 512MB | `MAX_CONCURRENT_INGESTIONS` (default 2) already bounds this; lower it further, or upgrade to the 1GB Starter instance |

---

## 4. Vercel — Next.js frontend

- [ ] Vercel → **Add New → Project** → import the same GitHub repo.
- [ ] **Set Root Directory to `frontend`.** This can't live in `vercel.json` —
  Vercel resolves the root before reading that file. Skip this and the build
  fails, having found no Next.js app at the repo root.
- [ ] Framework preset auto-detects as Next.js; [`frontend/vercel.json`](frontend/vercel.json)
  supplies the build/install commands.
- [ ] Add environment variable, for **Production, Preview, and Development**:

  | Variable | Value |
  |---|---|
  | `NEXT_PUBLIC_API_BASE_URL` | `https://<your-render-service>.onrender.com` — no trailing slash |

  This is inlined at **build** time (that's what `NEXT_PUBLIC_` means) —
  changing it later needs a redeploy, not just a restart.
- [ ] Deploy. Note your Vercel URL (`https://<your-app>.vercel.app`).
- [ ] Sanity check: open the URL. The notebook list should load (it will be
  empty — CORS isn't closed yet, so watch for a fetch error in the console;
  that's expected until step 6).

---

## 5. Google sign-in (optional)

Skip this whole section to ship without auth — the API then treats every
request as the built-in `local-dev` user and the UI shows no sign-in screen.
Nothing else in the app depends on this being set up.

- [ ] [Google Cloud Console → Credentials](https://console.cloud.google.com/apis/credentials)
  → **Create Credentials → OAuth client ID → Web application**.
- [ ] Add both your local and production URLs:

  | Field | Value |
  |---|---|
  | Authorized JavaScript origins | `http://localhost:3000` **and** `https://<your-app>.vercel.app` |
  | Authorized redirect URIs | `http://localhost:3000/api/auth/callback/google` **and** `https://<your-app>.vercel.app/api/auth/callback/google` |

- [ ] Set the resulting credentials in **both** Vercel and Render — they must
  agree, because the backend checks that the ID token's `aud` claim equals
  its own `GOOGLE_CLIENT_ID`:

  | Where | Variable | Value |
  |---|---|---|
  | Vercel | `AUTH_GOOGLE_ID` | the OAuth client ID |
  | Vercel | `AUTH_GOOGLE_SECRET` | the OAuth client secret |
  | Vercel | `AUTH_SECRET` | generate with `npx auth secret` |
  | Vercel | `AUTH_URL` | `https://<your-app>.vercel.app` |
  | Render | `GOOGLE_CLIENT_ID` | **the same client ID** as `AUTH_GOOGLE_ID` |

- [ ] Redeploy both Render and Vercel so the new env vars take effect.
- [ ] Sign in once and confirm `GET /auth/me` (or the header avatar) shows
  your real Google account, not `Local Dev`.
- [ ] **Claim any pre-existing notebooks.** Everything created before auth was
  turned on is owned by the sentinel `local-dev` user and won't show up under
  your Google account — that's per-user isolation working correctly, not a
  bug. To move them over, run once against Neon:
  ```sql
  UPDATE notebooks SET user_id = (SELECT id FROM users WHERE email = 'you@gmail.com')
  WHERE user_id = (SELECT id FROM users WHERE google_sub = 'local-dev');
  ```

> Google ID tokens expire after ~1 hour and Auth.js does not refresh the
> `id_token` on its own — a long-idle tab may need a re-sign-in. Acceptable
> for a portfolio demo; a refresh-token exchange would be the production fix.

---

## 6. Web search (optional)

Skip to ship without it — the "search the web" box in the Sources panel just
won't render, and everything else works unaffected.

- [ ] Create a free key at [app.tavily.com](https://app.tavily.com/) (keys
  look like `tvly-...`).
- [ ] Set `TAVILY_API_KEY` in Render's environment (only Render needs it —
  this is backend-only).
- [ ] Redeploy Render, then confirm `GET https://<service>.onrender.com/search/status`
  returns `{"configured": true}`.

---

## 7. Close the CORS loop

Now that you have the real Vercel URL:

- [ ] Render → your service → **Environment** → set:
  ```
  CORS_ORIGINS=https://<your-app>.vercel.app
  ```
  No trailing slash — origins are matched exactly, no wildcards. To also
  allow Vercel preview deployments, comma-separate them:
  ```
  CORS_ORIGINS=https://your-app.vercel.app,https://your-app-git-main-you.vercel.app
  ```
- [ ] Save — Render redeploys automatically.

---

## 8. Full verification

- [ ] **API, end to end:**
  ```bash
  API=https://<your-service>.onrender.com

  curl $API/health   # {"status":"ok"}

  NB=$(curl -s -X POST $API/notebooks -H 'Content-Type: application/json' \
    -d '{"title":"Deploy check"}' | python3 -c 'import json,sys;print(json.load(sys.stdin)["id"])')

  curl -X POST $API/notebooks/$NB/sources -H 'Content-Type: application/json' \
    -d '{"url":"https://en.wikipedia.org/wiki/Retrieval-augmented_generation"}'

  # poll until status flips to "ready"
  curl -s $API/notebooks/$NB/sources | python3 -m json.tool

  curl -s -X POST $API/notebooks/$NB/chat -H 'Content-Type: application/json' \
    -d '{"query":"What is RAG?"}' | python3 -m json.tool
  ```
  Expect a real `answer`, at least one entry in `citations`, and populated
  `provider` / `model` / `latency_ms` / `cost_usd` / `cache_hit`.
- [ ] **Stats endpoint:** `curl $API/stats | python3 -m json.tool` — after the
  chat call above, `total_calls` should be ≥ 1.
- [ ] **Frontend walkthrough**, on the real Vercel URL:
  - [ ] Create a notebook, rename it inline, delete it — confirm each persists
    across a page reload.
  - [ ] Add a source (URL and/or file), watch it go `pending → processing → ready`.
  - [ ] Ask a question, confirm the answer streams token-by-token, citations
    are clickable, and the transparency panel shows real provider/cost data.
  - [ ] Ask the *same* question again — latency should drop to single-digit
    ms and `cache_hit` should flip to `true`.
  - [ ] Click **Stop** mid-answer on a long question — confirm it cuts off
    cleanly and the input re-enables.
  - [ ] Visit `/stats` and confirm the dashboard renders with real numbers.
  - [ ] Toggle dark mode; reload; confirm it persisted.
  - [ ] If you set up Google sign-in: sign out, confirm you're gated back to
    the sign-in screen; sign back in.
- [ ] **Browser console** — open devtools on the deployed frontend and
  confirm no CORS errors, no failed requests, no React hydration warnings.

---

## Ongoing maintenance

- **Rotating a key** (Gemini, Tavily, OAuth secret): update it in the Render
  or Vercel dashboard only. Nothing to rebuild or re-commit — this is the
  entire point of keeping secrets out of the repo.
- **New migration**: run `alembic upgrade head` against the Neon
  `DATABASE_URL` from your machine before or right after deploying the
  backend change that depends on it. Render's free tier has no pre-deploy
  hook, so this stays a manual step.
- **Cost**: all three tiers are free at this scale. The only real spend is
  Gemini API usage — the gateway's cache and the `/stats` cost ledger exist
  specifically so that stays visible instead of a surprise.
