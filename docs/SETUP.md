# Detailed setup

The README has the short version. This page covers every step, including the parts that are
easy to get wrong.

## 1. Prepare the PC

The PC is the "server". Your phone talks to it, and it talks to Kia. It has to be on and signed
in whenever you want to use the app.

- **Windows 10 or 11.** Secrets go into Windows Credential Manager (see SECURITY.md).
- **Python 3.12+** from python.org. Tick "Add python.exe to PATH" during install.
- **Node.js LTS** from nodejs.org. It's only used to run `npx wrangler` for the tunnel.
- **Git**, to clone the repository.
- **Power settings:** Settings > System > Power > set "When plugged in, put my device to sleep
  after" to **Never**. A sleeping PC means an app that doesn't answer. Turning the screen off is
  fine.
- **Signing in after a reboot:** the service starts at **logon** (a scheduled task for your
  user), not at boot. After Windows Update restarts the PC, it waits until someone signs in. If
  that matters to you, see "Start without signing in" at the end of this page.

```powershell
git clone <this repo> kia-remote
cd kia-remote
py -m venv .venv
.venv\Scripts\python.exe -m pip install -r requirements.txt
.venv\Scripts\python.exe -m pytest          # should all pass; uses a fake Kia, sends nothing
.venv\Scripts\python.exe -m server --demo   # http://localhost:8765, a fake car
```

## 2. Check your Kia account (optional, recommended)

```powershell
.venv\Scripts\python.exe scripts\check_connection.py
```

It asks for your email, password and PIN (typed in, never saved), signs in, and prints your car's
status without waking it. Paste with **right-click**, not Ctrl+V: in some Windows terminals
Ctrl+V adds an invisible character, and a wrong password counts toward Kia's lockout. The script
checks for that before contacting Kia.

## 3. First real sign-in

```powershell
.venv\Scripts\python.exe -m server
```

Open **http://localhost:8765** on the PC. Use `localhost`, not `127.0.0.1`: passkeys need a
name. Sign in with your Kia Connect email, password and PIN. Kia sends a one-time code by email
or SMS because it sees a new device. After that the session is saved in Credential Manager and
reused, so restarts don't ask again.

## 4. Cloudflare: domain, tunnel, Access

You need a domain whose DNS is on Cloudflare. A cheap domain bought through Cloudflare Registrar
works, and so does moving an existing domain's nameservers to Cloudflare. The free plan is fine.

### 4a. Zero Trust organisation

In the Cloudflare dashboard, open **Zero Trust**. The first time, it asks you to pick a
**team name** (this gives you `yourteam.cloudflareaccess.com`) and a plan. **Free** covers up to
50 users. It may ask for a payment method even on the free plan.

### 4b. The tunnel

1. **Zero Trust > Networks > Tunnels > Create a tunnel**, type *Cloudflared*, and name it
   `kia-remote`. `scripts/start.ps1` runs it by that name.
2. Add a **public hostname**. Subdomain `car`, your domain, service type **HTTP**, URL
   **`127.0.0.1:8765`**. Cloudflare creates the DNS record for you.
3. On the PC, sign wrangler in to your Cloudflare account once:
   `npx --yes wrangler@4 login`. The tunnel then runs with
   `npx --yes wrangler@4 tunnel run kia-remote`, which is what `start.ps1` does.

   *Alternative:* the tunnel page also shows a `cloudflared service install <token>` command.
   That installs cloudflared as a Windows service that runs at boot without anyone signed in.
   If you use it, delete the tunnel lines from `start.ps1`.

### 4c. Access (who may open the app)

1. **Zero Trust > Access > Applications > Add an application > Self-hosted.**
2. Application domain: `car.yourdomain.com`, the same hostname as the tunnel.
3. **Session duration:** 24 hours is a good default (see SECURITY.md).
4. Add a policy: name it "Owners", Action **Allow**, Include **Emails**, and list each person's
   email. Don't use "Everyone".
5. Login method: **One-time PIN** is on by default. Each new phone gets a code by email.
6. Save, then open the application's **Overview** and copy the **Application Audience (AUD)
   Tag**.

### 4d. Tell the service about Access

Create `%LOCALAPPDATA%\kia-remote\settings.json`. Paste that path in Explorer's address bar to
open the folder.

```json
{
  "access_team_domain": "yourteam.cloudflareaccess.com",
  "access_aud": "paste the AUD tag here",
  "app_name": "Sportage"
}
```

Restart the service. Until both Access values are there, the service refuses everything that
comes through the tunnel. That's on purpose.

**Check it:** open `https://car.yourdomain.com` on your phone with Wi-Fi off. You should see
Cloudflare's sign-in page first, then the app. In a private browser window with an email that
isn't on the list, Access should refuse you.

## 5. On the phone

- **iPhone:** Safari > Share > **Add to Home Screen**.
- **Android:** Chrome > menu > **Install app** or **Add to Home screen**.

Open the app from the home screen. It asks you to **approve this device** with the car's Kia
email, password and PIN, once per phone or browser. On an iPhone, the home-screen app has its own
storage, separate from Safari, so do this in the home-screen app. Then: Unlock > enter the PIN >
tick **Use fingerprint next time**.

For a family member's phone, enter the account on their phone yourself. They don't need to know
the password. You can remove any phone later under **Approved devices** in the menu. From then on,
unlocking uses Face ID or your fingerprint.

## 6. Start with Windows

```powershell
powershell -ExecutionPolicy Bypass -File scripts\install-startup.ps1
```

This registers a scheduled task called `kia-remote`, for your user, at logon, with no admin
rights needed. It runs `scripts\start.ps1` hidden, which:

- starts the service (`.venv\Scripts\python.exe -m server`) and the tunnel
  (`npx wrangler tunnel run kia-remote`);
- checks every 30 seconds and restarts whichever one stopped;
- logs to `%LOCALAPPDATA%\kia-remote\start.log`. The service keeps its own `server.log` there.

To **update** after a `git pull`: end the `python.exe` process listening on port 8765 (Task
Manager, or `Get-NetTCPConnection -LocalPort 8765 | % { Stop-Process -Id $_.OwningProcess }`).
`start.ps1` brings it back within 30 seconds. Changes to `web/` alone need no restart; the
phones pick them up on their next load.

To **remove**: `Unregister-ScheduledTask -TaskName kia-remote -Confirm:$false`.

## Start without signing in (optional)

The scheduled task needs a signed-in user, because Credential Manager is tied to your Windows
login. Two options:

- Turn on automatic sign-in for this PC. It's convenient, but anyone with the PC in hand is
  signed in.
- Run cloudflared as a Windows service (4b alternative) and accept that the app is unreachable
  until someone signs in after a reboot.

## Raspberry Pi or Linux

**Not supported yet.** Here's what a port needs, and it would make a welcome pull request:

- **Secrets:** `server/secrets_store.py` uses `keyring`. On a desktop Linux that's the Secret
  Service (GNOME Keyring or KWallet). A headless Pi has neither, so it needs another backend, for
  example an encrypted file whose key isn't stored next to it. Be careful here: this is where
  the Kia password lives.
- **Data folder:** `server/storage.py` uses `%LOCALAPPDATA%`. On Linux it should be
  `~/.local/share/kia-remote` (or `$XDG_DATA_HOME`).
- **Startup:** `scripts/start.ps1` would become two systemd units, one for the service and one
  for `cloudflared` (installed as a service with the tunnel token).
- The Python service and the dashboard have no Windows-only code beyond those two points, so a
  Pi 4 or newer should be plenty. Nobody has tried it yet.
