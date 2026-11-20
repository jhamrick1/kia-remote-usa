# Security

What protects your car, layer by layer, and what each one does and doesn't stop.

## The path a request takes

```
phone ──HTTPS──> Cloudflare edge ──> Cloudflare Access ──> tunnel ──> your PC 127.0.0.1:8765 ──> Kia
                                      (who are you?)       (outbound      (checks Access again,
                                                            from the PC)   then PIN/passkey for unlock)
```

1. **No open port.** The service listens on `127.0.0.1` only, never `0.0.0.0`. The Cloudflare
   Tunnel (`cloudflared`, started by `wrangler tunnel run`) makes an *outbound* connection from
   your PC to Cloudflare, so nothing on your router is opened and your home IP isn't exposed.
2. **Cloudflare Access** sits in front of the hostname. Before any page loads, Access makes the
   visitor prove who they are, normally with a one-time code sent to an email address your policy
   allows. Visitors who aren't on the list never reach your PC.
3. **The service checks Access again itself.** Every request that came through the tunnel (it
   carries Cloudflare's `Cf-Ray` / `Cf-Connecting-IP` headers) must carry a valid Access JWT. The
   checks are: RS256, signed by your team's current key (from
   `https://<team>.cloudflareaccess.com/cdn-cgi/access/certs`), `aud` matching your application,
   `iss` matching your team, and not expired. If the Access settings are missing, **every tunnel
   request is refused** (fails closed). So a misconfigured Access application or a second
   hostname pointed at the tunnel still gets nothing.
4. **Each new browser must be approved once**, even after Access. A phone or browser the service
   hasn't seen gets only an "Approve this device" screen. To approve it, someone enters the car's
   Kia **email, password and PIN** there. These are checked against what's saved on the PC and
   never sent to Kia. Signing in to Kia from that browser, when the PC isn't signed in, approves
   it too. An approved browser keeps a random token in an `HttpOnly`, `Secure`,
   `SameSite=Strict` cookie, and the PC keeps only its SHA-256 (`devices.json`). Five wrong tries
   in 15 minutes pause approvals for 15 minutes. **Approved devices** in the menu lists every
   device, with who approved it and when it was last used, and removes any of them. If a
   *different* Kia account signs in on the PC, every approval is dropped. A browser on the PC
   itself never needs approval.
5. **Unlock needs a second factor**, even after Access: a passkey (the phone's fingerprint or
   Face ID) or the Kia PIN. Five wrong PINs in 15 minutes close the PIN path for 15 minutes.
   Registering a new passkey also needs the PIN, so an Access session alone can't add one.
   Lock, climate, horn and lights don't ask, on purpose: they can't let anyone into the car.
6. **Kia's own checks.** Kia needs your password, a one-time code to the owner's email or phone
   the first time a new device signs in, and the PIN for commands.

## Who can do what

| An attacker has… | What they can do |
|---|---|
| Only your app's address | Nothing. Access stops them at Cloudflare |
| Your Kia password only | Nothing through this app. Directly with Kia, they still need the one-time code Kia sends to your email or phone for a new device |
| Access to an email inbox your Access policy allows | Pass Access, then get stuck at "Approve this device". **Nothing about the car** |
| That inbox **and** your Kia email, password and PIN | Approve their browser, then everything, including unlock. Removing their device in the menu shuts them out again |
| Your unlocked phone (already approved, Access session still valid) | Lock, climate, lights, status and location. **Unlock still asks** for the fingerprint or PIN. Remove the phone under Approved devices from another device or the PC |
| Admin access to your PC | Everything. The PC holds the Kia session |

**Takeaway: your email account plus your Kia password are the keys.** Access's code and Kia's
new-device code both go to your email, and approving a new browser takes the Kia password and PIN. Turn on two-factor authentication (an authenticator app or a security key, not SMS)
on that email account, and allow only the emails you need in the Access policy.

## Recommended Cloudflare Access settings

- **Policy:** Action *Allow*, Include *Emails*: list each person by address. Don't use
  "Everyone" or a whole email domain like gmail.com.
- **Login method:** *One-time PIN* is fine. Google or GitHub sign-in is better if those accounts
  have two-factor authentication on.
- **Session duration:** the shorter, the safer. 24 hours is a good balance for a phone you use
  daily. A month means a stolen, unlocked phone stays signed in for a month.
- **One hostname per application.** If you add another hostname to the tunnel, give it its own
  Access application. The service refuses a token whose `aud` isn't yours anyway.

## Secrets on the PC

- The Kia email, password, PIN and session token are stored **only in Windows Credential
  Manager** (service name `kia-remote`), encrypted with your Windows login. They are never written
  to a file, never logged and never returned by the API. Every secret is also scrubbed from log
  lines (`[hidden]`), because the underlying library logs whole responses at debug level. The
  library is kept at WARNING.
- `%LOCALAPPDATA%\kia-remote\` holds non-secret files: settings, presets, schedules, the command
  log, passkey *public* keys and the service log.
- There are no `.env` files and no API keys in the repository. The Access values in
  `settings.json` are identifiers, not secrets.
- "Sign out" in the dashboard deletes all saved Kia credentials.

## The browser side

- A strict Content-Security-Policy (`default-src 'self'`, no inline scripts, `frame-ancestors
  'none'`), `nosniff` and `no-referrer` on every response.
- API responses are `no-store`, so the browser doesn't cache them.
- The phone keeps only which passkey is its own (an id) and the last state it saw, for a
  quick start offline. No passwords or PINs.

## Protecting the car and the account

- **Kia account lockout:** if Kia rejects the password, the service stops, forgets the saved
  password and never retries on its own.
- **12 V battery:** a forced refresh wakes the car, so it's allowed once per 10 minutes.
- **One command at a time**, each recorded in the activity log with who sent it (the Access
  email, or `local` for a browser on the PC).

## Reporting a problem

Please open a GitHub issue *without* exploit details and ask for a private contact, or use
GitHub's "Report a vulnerability" button if it's enabled on the repository.
