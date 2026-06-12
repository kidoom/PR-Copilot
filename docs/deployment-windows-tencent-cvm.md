# Deploy to Tencent Cloud Windows CVM

This guide deploys PR Copilot to the Windows CVM at `140.143.209.138`.
The first stage uses HTTP on the public IP. Add a domain and HTTPS before
opening the service to real users.

## 1. Configure the Tencent Cloud firewall

In the CVM security group, allow inbound TCP ports:

- `80` from `0.0.0.0/0`
- `3389` only from your own public IP

Do not expose port `8000`. Caddy reaches FastAPI through `127.0.0.1`.

Also allow port `80` through Windows Defender Firewall:

```powershell
New-NetFirewallRule -DisplayName "PR Copilot HTTP" -Direction Inbound -Protocol TCP -LocalPort 80 -Action Allow
```

## 2. Install prerequisites

Install Git, Python 3.10 or later, Node.js LTS, and Caddy for Windows.
Then clone or update the project at `D:\PR-Copilot`.

From an Administrator PowerShell window:

```powershell
cd D:\PR-Copilot
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r backend\requirements.txt
npm ci --prefix frontend
npm run build --prefix frontend
```

## 3. Configure environment variables

Copy `deploy\env.local.ip.example` to `.env.local`, then fill in the GitHub App
and model credentials. Never commit `.env.local`.

In the GitHub App settings, set the callback URL to:

```text
http://140.143.209.138/api/auth/github/callback
```

## 4. Start and verify FastAPI

Start FastAPI from the repository root:

```powershell
cd D:\PR-Copilot
.\.venv\Scripts\python.exe -m uvicorn backend.main:app --host 127.0.0.1 --port 8000
```

In a second PowerShell window, verify the health endpoint:

```powershell
Invoke-RestMethod http://127.0.0.1:8000/api/health
```

## 5. Start Caddy

Copy `deploy\Caddyfile.ip.example` to `C:\caddy\Caddyfile`. Place `caddy.exe`
in `C:\caddy`, then start it:

```powershell
cd C:\caddy
.\caddy.exe run --config .\Caddyfile
```

Open `http://140.143.209.138` in a browser and test GitHub sign-in and one PR
review run.

## 6. Run services after restart

After the manual verification succeeds, register both FastAPI and Caddy as
Windows services with NSSM or WinSW. Use these commands as the service
processes:

```text
D:\PR-Copilot\.venv\Scripts\python.exe -m uvicorn backend.main:app --host 127.0.0.1 --port 8000
C:\caddy\caddy.exe run --config C:\caddy\Caddyfile
```

Set the FastAPI service working directory to `D:\PR-Copilot`.

## 7. Add a domain and HTTPS

Point a domain A record at `140.143.209.138`, allow inbound TCP port `443`,
and replace the first line of the Caddyfile:

```text
your-domain.example {
```

Caddy will request and renew the TLS certificate automatically. Update
`.env.local`:

```text
GITHUB_APP_CALLBACK_URL=https://your-domain.example/api/auth/github/callback
PR_COPILOT_FRONTEND_URL=https://your-domain.example/
PR_COPILOT_CORS_ORIGINS=https://your-domain.example
PR_COPILOT_COOKIE_SECURE=true
```

Update the callback URL in the GitHub App settings at the same time, rebuild
the frontend if needed, and restart the FastAPI service.
