# Porting to another region or brand

Car Remote was built and tested on **Kia Connect Canada**. The library underneath,
[hyundai_kia_connect_api](https://github.com/Hyundai-Kia-Connect/hyundai_kia_connect_api),
supports Kia, Hyundai and Genesis in most regions, so porting means teaching this app the
differences for your **region and brand**. Everything else stays as it is: the dashboard,
schedules, presets, passkeys and Cloudflare Access.

| Region | Brand | Status | Notes |
|---|---|---|---|
| Canada | Kia | Tested | `server/regions/canada.py` |
| Canada | Hyundai, Genesis | Not started | Same backend software as Kia Canada (mybluelink.ca, genesisconnect.ca): likely a small file |
| USA | Kia | Started | `server/regions/usa.py` (`tested: False` — climate runs through Kia Access in °F, no horn/lights yet) |
| USA | Hyundai, Genesis | Not started | |
| Europe | Kia, Hyundai, Genesis | Not started | |
| Australia, NZ, India, Brazil, China | | Not started | |

When your port works, add your row here (and your car and model year) in the pull request.

## Before you touch the car

**Kia and Hyundai lock accounts after a few failed sign-ins.** Before running anything, sign in
once on your region's official website in a browser. If a sign-in fails, stop, and check it on
the website, not by retrying.

## Step 1: can this computer reach your car?

```powershell
.venv\Scripts\python.exe scripts\check_connection.py --region 3 --brand 1
```

Regions: 1 Europe, 2 Canada, 3 USA, 4 China, 5 Australia, 6 India, 7 New Zealand, 8 Brazil.
Brands: 1 Kia, 2 Hyundai, 3 Genesis.

It signs in (asking for a one-time code if your region wants one), lists your cars and prints
the status. It **reads only**: the car isn't woken and nothing is saved. If this fails, the
problem is in the library or your account, not in this app. Check the library's issues for
your region.

## Step 2: write the region file

Make `server/regions/<your_region>.py` with a subclass of `Region` (`server/regions/__init__.py`),
then add it to `available()` in that file. Start by copying `canada.py`.

| What | Required | Meaning |
|---|---|---|
| `key` | yes | What goes in `settings.json`, e.g. `"usa-hyundai"` |
| `name` | yes | Shown in logs, e.g. `"Hyundai Bluelink USA"` |
| `library_region`, `library_brand` | yes | The two numbers that worked in step 1 |
| `account_site` | yes | Where an owner signs in by hand, named in error messages |
| `tested` | yes | `False` until someone has run the whole test order below on a real car |
| `dial_min`, `dial_max` | check | The car's own temperature dial in °C. HI and LO are the ends |
| `climate_request()` | maybe | Builds the library's climate request; override if your region takes other options |
| `end_temp()` | maybe | What to send for HI / LO. Canada's car misbehaves if sent the word "HI", so it sends 27 / 17 |
| `horn_lights()` | maybe | The default uses the library's `hornlight` if your region has it |
| `vehicle_entry()` | optional | `{"model", "year", "paint"}` for the car picture. Default: none |
| `photo_url()` | optional | One picture of the car. Default: the dashboard's drawing |
| `spin_frames()` | optional | The 360 turntable (below). Default: none |

Run settings like this:

```json
{ "region": "usa-hyundai" }
```

Some notes:

- **Temperatures.** The dashboard works in °C. If your region's API takes °F, convert in
  `climate_request()`. The library may already do it, so check its code for your region.
- **The PIN.** Canada needs the PIN for every command. Some regions don't use one at all. The
  unlock second factor still uses whatever PIN is saved, so a region without a PIN should rely
  on passkeys.
- **Wording.** Error messages say "Kia". For Hyundai and Genesis, adding a brand name to the
  region (and using it in `server/errors.py`) is a welcome part of a port.

## Step 3: the car picture (optional, but it's the best part)

The dashboard turns a 36-frame turntable to show what's open. The frame convention:

- Frame 1 is the **front three-quarter view of the driver's side**.
- Frames are 10° apart, turning so the front swings toward the viewer. Frame 5 is the front,
  13 the passenger side, 23 the rear and 31 the driver's side.
- Transparent PNG or WebP, about 1280 × 567.

Canada gets these from kia.ca's Build & Price page (`server/regions/kia_ca_360.py`), matched by
model, year and **paint code** (Kia Connect reports the same codes the configurator uses). Most
brands' official configurators have a 360 view, so look in the page's data or network requests
before giving up. If your frames start at a different angle, rotate the list. Return `None` and
the app falls back to `photo_url()`, then to the drawing.

## Step 4: test on the real car, in this order

Stop at the first step that fails and fix it before going on. Watch the car, or have someone
there.

1. **Demo first:** `python -m server --demo` must still work, and `pytest` must pass.
2. **Sign in and status:** the dashboard shows the right car, fuel, locks and doors.
3. **Refresh:** a normal refresh, then one forced refresh. A forced refresh wakes the car.
4. **Lock** while the car is locked (harmless), then **unlock and lock again** while standing
   next to it.
5. **Climate:** start for 1 minute at a middle temperature with nothing else, then stop.
   Then try each option on its own: defrost, steering wheel, each seat, HI and LO.
6. **Flash lights**, then **horn and lights** (warn the neighbours).
7. **Schedules:** one climate start a few minutes ahead.

Write down anything odd your car did. It's the most useful part of a pull request.

## A prompt for your AI coding assistant

Paste this into Claude Code (or a similar tool) opened in this repo:

```text
I want to run this app with my car: <brand> in <country>, a <year> <model>.
Read README.md, PORTING.md, server/regions/__init__.py, server/regions/canada.py,
server/kia.py and docs/api-contract.md first.

Then:
1. Find my region and brand numbers in hyundai_kia_connect_api (in .venv) and read
   that region's API class: login, OTP, status, lock, climate (°C or °F? which options?),
   horn/lights, and whether it uses a PIN.
2. Have me run scripts/check_connection.py with those numbers. Don't run it yourself,
   because it needs my password. Use what it prints.
3. Write server/regions/<region>.py as a Region subclass, register it in available(),
   and add tests in the style of tests/test_real_mode.py. Run pytest.
4. Look for real 360° pictures of my model on the brand's official configurator for my
   country (page data or network requests) and implement spin_frames() if they exist.
5. Walk me through PORTING.md's real-car test order one step at a time. Never send a
   command to my car without asking me first.

Rules: never log, print or save my password, PIN or tokens. Never retry a failed sign-in
(the account locks). Keep Canada working.
```
