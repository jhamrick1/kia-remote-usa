# Detailed setup

The README has the short version. This page covers every step on Windows, macOS and Raspberry Pi,
including the parts that are easy to get wrong.

The computer you install this on is the "server". Your phone talks to it, and it talks to Kia,
so it has to be on whenever you want to use the app.

> Windows is tested daily. macOS and Raspberry Pi are supported but haven't been tried on real
> hardware yet. If something here doesn't match what you see, please open an issue.

## 1. Prepare the computer

### Windows 10 / 11

- **Python 3.12+** from python.org. Tick "Add python.exe to PATH" during install.
- **Git**, to clone the repository.
- **Node.js LTS** from nodejs.org, only if you'll run the tunnel with `npx wrangler` (step 4b).
- **Power:** Settings > System > Power > "When plugged in, put my device to sleep after" >
  **Never**. Turning the screen off is fine.

```powershell
git clone https://github.com/kobkob88/kia-remote-public kia-remote
cd kia-remote
py -m venv .venv
.venv\Scripts\python.exe -m pip install -r requirements.txt
.venv\Scripts\python.exe -m pytest          # should all pass; uses a fake Kia, sends nothing
.venv\Scripts\python.exe -m server --demo   # http://localhost:8765, a fake car
```

### macOS

- **Python 3.12+**: from python.org, or `brew install python@3.13` with
  [Homebrew](https://brew.sh).
- **Git** comes with the Xcode command-line tools (`xcode-select --install`).
- **Power:** a desktop Mac (mini, iMac, Studio) is the best fit. In System Settings > Energy (or
  Battery > Options on a laptop), turn on **Prevent automatic sleeping when the display is off**.
  A closed MacBook lid sleeps unless a display is connected.

```bash
git clone https://github.com/kobkob88/kia-remote-public kia-remote
cd kia-remote
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
.venv/bin/python -m pytest
.venv/bin/python -m server --demo           # http://localhost:8765
```

Secrets go into your login **Keychain**. The first time the service saves them, macOS may ask
whether Python can use the Keychain. Choose **Always Allow**, or the service will keep asking
after each restart.

### Raspberry Pi (or any Linux)

- **A Pi 4 or Pi 5** with Raspberry Pi OS **64-bit**. The current release, based on Debian 13
  "Trixie", ships Python 3.13. The older "Bookworm" release ships 3.11, which is too old: reflash
  with the current release, or install a newer Python with [uv](https://docs.astral.sh/uv/).
- A wired connection or reliable Wi-Fi, and a good power supply. Undervoltage makes a Pi flaky.
- Everything below runs over SSH, so no screen is needed.

```bash
sudo apt update && sudo apt install -y git python3-venv
git clone https://github.com/kobkob88/kia-remote-public kia-remote
cd kia-remote
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt   # a few minutes on a Pi
.venv/bin/python -m pytest
.venv/bin/python -m server --demo           # http://localhost:8765 on the Pi
```

To look at the demo from your laptop, forward the port over SSH:
`ssh -L 8765:127.0.0.1:8765 pi@raspberrypi.local`, then open http://localhost:8765 on the laptop.

**Where the Kia password goes on a Pi:** a headless Pi has no password store that a background
service can unlock, so the secrets go in `~/.local/share/kia-remote/secrets.json`, readable only
by your user (mode 600). That is the same approach Home Assistant takes. Anyone with root on the
Pi, or with the SD card in hand, can read it. Keep the Pi somewhere private, use SSH keys instead
of a password, and don't share the user account.

## 2. Check your Kia account (optional, recommended)

```bash
.venv/bin/python scripts/check_connection.py          # Windows: .venv\Scripts\python.exe scripts\check_connection.py
```

It asks for your email, password and PIN (typed in, never saved), signs in, and prints your car's
status without waking it. On Windows, paste with **right-click**, not Ctrl+V: in some Windows
terminals Ctrl+V adds an invisible character, and a wrong password counts toward Kia's lockout.
The script checks for that before contacting Kia.

## 3. First real sign-in

Start the service without `--demo` and open **http://localhost:8765** on that computer. Use
`localhost`, not `127.0.0.1`, because passkeys need a name. (On a Pi, use the SSH port forward
from step 1.) Sign in with your Kia Connect email, password and PIN. Kia sends a one-time code by
email or SMS because it sees a new device. After that the session is saved and reused, so
restarts don't ask again.

## 4. Cloudflare: domain, tunnel, Access

You need a domain whose DNS is on Cloudflare. A cheap domain bought through Cloudflare Registrar
works, and so does moving an existing domain's nameservers to Cloudflare. The free plan is fine.

### 4a. Zero Trust organisation

In the Cloudflare dashboard, open **Zero Trust**. The first time, it asks you to pick a
**team name** (this gives you `yourteam.cloudflareaccess.com`) and a plan. **Free** covers up to
50 users. It may ask for a payment method even on the free plan.

### 4b. The tunnel

1. **Zero Trust > Networks > Tunnels > Create a tunnel**, type *Cloudflared*, name it
   `kia-remote`.
2. Cloudflare shows install commands for each operating system. They install `cloudflared` as a
   **system service**, which starts at boot, with a token that ties it to this tunnel:
   - **Windows:** download the installer it links, then run in an administrator PowerShell
     `cloudflared.exe service install <token>`.
   - **macOS:** `brew install cloudflared`, then `sudo cloudflared service install <token>`.
   - **Raspberry Pi / Linux (Debian):** pick "Debian" and **arm64**. It adds Cloudflare's apt
     repository and runs `sudo cloudflared service install <token>`.

   Treat the token like a password: it lets anyone run your tunnel.
3. Add a **public hostname**: subdomain `car`, your domain, service type **HTTP**, URL
   **`127.0.0.1:8765`**. Cloudflare creates the DNS record for you.

*Windows alternative:* `scripts\start.ps1` can run the tunnel itself with
`npx wrangler tunnel run kia-remote` (that needs Node.js and a one-time `npx wrangler login`). It
keeps its own copy of cloudflared in `%LOCALAPPDATA%\kia-remote` (refreshed at logon every 30 days),
so a new cloudflared release can't stall the hidden tunnel on wrangler's "download?" question, and
it restarts the tunnel if it runs without a connection for 90 seconds. If
you installed the cloudflared service above, delete the tunnel lines from `start.ps1` so the
tunnel isn't run twice.

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

Create `settings.json` in the data folder:

| | Data folder |
|---|---|
| Windows | `%LOCALAPPDATA%\kia-remote\` (paste that into Explorer's address bar) |
| macOS | `~/Library/Application Support/kia-remote/` |
| Raspberry Pi / Linux | `~/.local/share/kia-remote/` |

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
the password. You can remove any phone later under **Approved devices** in the menu.

## 6. Start automatically

### Windows

```powershell
powershell -ExecutionPolicy Bypass -File scripts\install-startup.ps1
```

This registers a scheduled task called `kia-remote`, for your user, at logon, with no admin
rights needed. It runs `scripts\start.ps1` hidden, which starts the service (and the tunnel, if
you use the wrangler way), checks every 30 seconds and restarts whatever stopped. It logs to
`%LOCALAPPDATA%\kia-remote\start.log`. To remove it:
`Unregister-ScheduledTask -TaskName kia-remote -Confirm:$false`.

The task needs a signed-in user, because Credential Manager is tied to your Windows login. After
Windows Update restarts the PC, the app stays down until someone signs in. Automatic sign-in
solves that, at the cost that anyone holding the PC is signed in.

### macOS

```bash
bash scripts/macos/install-agent.sh
```

This installs a LaunchAgent (`~/Library/LaunchAgents/com.kia-remote.server.plist`) that starts
the service when you log in and restarts it if it stops. Like on Windows, it runs while you're
logged in, because the Keychain unlocks with your login. To remove it:
`launchctl bootout gui/$(id -u)/com.kia-remote.server` and delete the plist.

### Raspberry Pi / Linux

```bash
bash scripts/linux/install-service.sh
```

This installs a systemd service (`kia-remote`) that starts **at boot**, with no login needed, and
restarts it if it stops. It runs as your user, with the rest of the system read-only to it.
Useful commands:

```bash
systemctl status kia-remote          # is it running?
journalctl -u kia-remote -f          # live log
sudo systemctl restart kia-remote    # after a git pull or a settings change
```

To remove it:
`sudo systemctl disable --now kia-remote && sudo rm /etc/systemd/system/kia-remote.service`.

## Updating

```bash
git pull
.venv/bin/pip install -r requirements.txt      # Windows: .venv\Scripts\python.exe -m pip ...
```

Then restart the service:

- **Windows:** end the `python.exe` listening on port 8765 (Task Manager, or
  `Get-NetTCPConnection -LocalPort 8765 | % { Stop-Process -Id $_.OwningProcess }`).
  `start.ps1` brings it back within 30 seconds.
- **macOS:** `launchctl kickstart -k gui/$(id -u)/com.kia-remote.server`.
- **Raspberry Pi / Linux:** `sudo systemctl restart kia-remote`.

Changes to `web/` alone need no restart; the phones pick them up on their next load.
