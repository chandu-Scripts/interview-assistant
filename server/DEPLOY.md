# Deploying the relay to Fly.io

Build context is the **repo root** (`fly.toml` lives there), because `server/Dockerfile` reuses
`src/interview_assistant` (cost pricing, SSE parsing) alongside `server/interview_relay`. Run
every command below from the repo root (`C:\Users\tcs91\Desktop\KCPD`).

## 1. Install the Fly CLI and sign up
```
winget install flyctl
fly auth signup      # or `fly auth login` if you already have an account (e.g. via GitHub)
```
This opens your browser for the actual sign-up/login - nothing happens in the terminal itself.

## 2. Create the app
```
fly launch --no-deploy --copy-config
```
`fly.toml` already exists and is checked in, so `--copy-config` tells `fly launch` to use it as-is
instead of generating a new one. If the app name `interview-assistant-relay` is taken, it'll ask
you to pick another - that's fine, just update `app = "..."` in `fly.toml` to match.

## 3. Create the persistent volume (so relay.db survives restarts/redeploys)
```
fly volumes create relay_data --region iad --size 1
```
Use the same region you set as `primary_region` in `fly.toml` (default `iad`; change both if you
picked somewhere else in step 2's prompts). 1 GB is overkill for this metadata-only database but
it's Fly's minimum.

## 4. Set the real secrets (never go in fly.toml - that file is committed)
```
fly secrets set ADMIN_TOKEN=<a long random string>
fly secrets set OPENAI_API_KEY=<your real key>
```
You can reuse the same `ADMIN_TOKEN` you generated for local testing, or pick a new one - either
way, pick it yourself and don't paste the actual value to Claude.

## 5. Deploy
```
fly deploy
```
This builds the Docker image on Fly's servers (no local Docker needed) and starts the app.

## 6. Verify
```
fly status
curl https://interview-assistant-relay.fly.dev/health
```
Should return `{"ok":true,"missing_config":[]}`. Then open
`https://interview-assistant-relay.fly.dev/admin` (replace with your actual app name) - same
`admin` / `ADMIN_TOKEN` login as the local version.

## 7. Give your friend the URL
In the desktop app's Settings -> "Shared access" tab, the "Relay address" they enter is:
```
https://interview-assistant-relay.fly.dev
```
(your actual `*.fly.dev` URL from `fly status`, or a custom domain if you set one up).

## Notes
- `auto_stop_machines = "stop"` in `fly.toml` scales the app to zero when nobody's using it (free
  -tier friendly) - the first request after idle takes a few extra seconds to cold-start. Set
  `min_machines_running = 1` in `fly.toml` and `fly deploy` again if you'd rather avoid that at
  the cost of the machine always running.
- To update after a code change: just `fly deploy` again from the repo root. `relay.db` on the
  volume is untouched by a redeploy.
- `fly logs` tails the server's logs (useful if something doesn't work as expected). They go
  through the same redaction-free FastAPI/uvicorn access logging - no prompts or answers are ever
  in there (the relay never sees them), but avoid pasting full logs anywhere public regardless.
