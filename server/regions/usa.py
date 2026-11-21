"""Kia Access USA (owners.kia.com). Not yet run against a real car: tested is False.

Facts this file is built on, from hyundai_kia_connect_api 5.2.1's KiaUvoApiUSA:
- start_climate sends the setpoint in Fahrenheit (airTemp unit 1) and turns anything below
  62 into "LOW" and above 82 into "HIGH" (KiaUvoApiUSA.py, start_climate / temperature_range).
- Status reports come back in °F too (air_temperature gets TEMPERATURE_UNITS[1]).
- The steering wheel has its own option (steering_wheel); the rear window and mirrors follow
  the heating code (rear: 1/2/4, mirrors: 1/4) -- not Canada's one heating code.
- The US API has no horn/lights call, so Region.horn_lights's NotImplementedError stands.
"""

from __future__ import annotations

from typing import Any

from hyundai_kia_connect_api.ApiImpl import ClimateRequestOptions

from . import Region


def _f_to_c(f: float) -> float:
    """A °F number to the dashboard's 0.5-°C dial steps (70 F -> 21.0 C)."""
    return round((f - 32.0) * 5.0 / 9.0 * 2.0) / 2.0


def _c_to_f(c: float) -> int:
    """A °C dial value to a whole °F (22.5 C -> 73 F), inside the car's 62-82 F dial."""
    return min(82, max(62, int(c * 9.0 / 5.0 + 32.0 + 0.5)))


class Usa(Region):
    key = "usa"
    name = "Kia Access USA"
    library_region = 3
    library_brand = 1
    account_site = "owners.kia.com (Kia Access)"
    tested = False

    # The dashboard's °C dial covering the car's 62-82 F dial (the ends map to 63/81 F).
    dial_min = 17.0
    dial_max = 27.0

    def reported_set_temp(self, value: Any) -> Any:
        """Kia Access reports the setpoint in °F; the dashboard shows °C ("HI"/"LO" stay)."""
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            return _f_to_c(float(value))
        return value

    def climate_request(self, options: dict[str, Any], set_temp: float) -> ClimateRequestOptions:
        """The USA request: °F on the wire, and the heating code split into the library's
        heating (rear window + mirrors) and steering_wheel options."""
        heating = options.get("heating", 0)
        return ClimateRequestOptions(
            climate=True,
            set_temp=_c_to_f(set_temp),
            duration=options.get("duration_min", 10),
            defrost=options.get("defrost", False),
            heating=1 if heating in (1, 2) else 0,   # rear window + mirrors (1) or neither
            steering_wheel=1 if heating in (1, 3) else 0,
            front_left_seat=options.get("front_left_seat", 0),
            front_right_seat=options.get("front_right_seat", 0),
            rear_left_seat=options.get("rear_left_seat", 0),
            rear_right_seat=options.get("rear_right_seat", 0),
        )
