"""First check for a new owner or a new region: can this computer reach the car at all?

Run:   .venv\\Scripts\\python.exe scripts\\check_connection.py                     (Canada, Kia)
       .venv\\Scripts\\python.exe scripts\\check_connection.py --region 3          (USA)
       .venv\\Scripts\\python.exe scripts\\check_connection.py --region 1 --brand 2 (Europe, Hyundai)
       add --lock to also send one lock command

Regions (hyundai_kia_connect_api): 1 Europe, 2 Canada, 3 USA, 4 China, 5 Australia, 6 India,
7 New Zealand, 8 Brazil. Brands: 1 Kia, 2 Hyundai, 3 Genesis. Reads status only (Kia's last
known copy, the car isn't woken) unless --lock is given. See PORTING.md.

Your email, password and PIN are typed in and never saved anywhere.
"""

import argparse
import getpass
import sys

from hyundai_kia_connect_api import VehicleManager
from hyundai_kia_connect_api.ApiImpl import OTPRequest
from hyundai_kia_connect_api.const import OTP_NOTIFY_TYPE


def main() -> None:
    parser = argparse.ArgumentParser(description="Can this computer reach the car?")
    parser.add_argument("--region", type=int, default=2, help="1 Europe, 2 Canada, 3 USA, ... (default 2)")
    parser.add_argument("--brand", type=int, default=1, help="1 Kia, 2 Hyundai, 3 Genesis (default 1)")
    parser.add_argument("--lock", action="store_true", help="also send one lock command")
    args = parser.parse_args()

    print("Password and PIN stay blank while you type (no stars). That's normal.")
    print("On Windows, paste with RIGHT-CLICK. Ctrl+V can add an invisible junk character.\n")
    username = input("Email: ").strip()
    password = getpass.getpass("Password (hidden): ")
    pin = getpass.getpass("4-digit PIN (hidden, Enter if your region has none): ").strip()

    # Check what was typed before spending a login attempt (Kia locks after several).
    junk = [c for c in password if not c.isprintable()]
    print(f"\nPassword: {len(password)} characters", end="")
    print(f"  <-- contains {len(junk)} invisible character(s), probably Ctrl+V" if junk else "")
    if password != password.strip():
        print("Password starts or ends with a space. Is that right?")
    if pin and not (pin.isdigit() and len(pin) == 4):
        print(f"PIN: {len(pin)} characters, expected 4 digits")
    if junk or input("Does that look right? (y/n) ").strip().lower() != "y":
        sys.exit("Stopped before contacting Kia. Run it again.")

    vm = VehicleManager(region=args.region, brand=args.brand, username=username,
                        password=password, pin=pin)

    print("\nLogging in...")
    result = vm.login()
    if isinstance(result, OTPRequest):
        print("Kia wants a one-time code for this new device.")
        choice = "SMS" if result.has_sms and not result.has_email else "EMAIL"
        if result.has_email and result.has_sms:
            pick = input("Send the code by (e)mail or (s)ms? ").strip().lower()
            choice = "SMS" if pick.startswith("s") else "EMAIL"
        vm.send_otp(OTP_NOTIFY_TYPE[choice])
        code = input(f"Code sent by {choice.lower()}. Enter it: ").strip()
        vm.verify_otp_and_complete_login(code)  # also loads the vehicles
    elif result is not True and not vm.token:
        sys.exit("Login failed. Check the email and password.")

    print("Logged in.\n")
    if not vm.vehicles:
        vm.initialize_vehicles()
    vm.update_all_vehicles_with_cached_state()  # last known state, does not wake the car

    for vid, v in vm.vehicles.items():
        print(f"Vehicle:      {v.name} ({v.model} {v.year})")
        print(f"Engine type:  {v.engine_type}")
        print(f"Locked:       {v.is_locked}")
        print(f"Fuel level:   {v.fuel_level}")
        print(f"12V battery:  {v.car_battery_percentage}")
        print(f"Engine on:    {v.engine_is_running}")
        print(f"Climate on:   {v.air_control_is_on}  set to {v.air_temperature}")
        print(f"Last update:  {v.last_updated_at}\n")

        if args.lock:
            print("Sending lock...")
            action_id = vm.lock(vid)
            print(f"Lock sent (action {action_id}). Check the car or the Kia app.")

    print("PASSED: this computer can talk to the car.")


if __name__ == "__main__":
    main()
