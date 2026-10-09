"""One unit system for the whole software: thickness / diameter / length in mm, pressure in bar,
temperature in degrees Celsius. (The client's sheets are made in these units.)

Every value is stored and calculated in these units. A request that says a different unit is refused with a
clear message instead of being silently treated as if it were mm / bar / degC.
"""
from typing import Optional

_MM = {"mm", "millimeter", "millimeters", "millimetre", "millimetres"}
_BAR = {"bar"}
_CELSIUS = {"°c", "c", "celsius", "degc", "deg c", "degree c", "�c"}   # "�c" = the same sign saved with a wrong encoding

LENGTH_UNIT, PRESSURE_UNIT, TEMPERATURE_UNIT = "mm", "bar", "°C"

# Length units that may be written next to readings in a workbook; anything in here is refused (not mm)
NON_MM_LENGTH_UNITS = {"in", "in.", "inch", "inches", "cm", "m", "meter", "meters", "metre", "metres",
                       "ft", "ft.", "foot", "feet", "um", "µm", "micron", "microns", "mil", "mils"}


def _check(value: Optional[str], accepted: set, canonical: str, what: str) -> Optional[str]:
    if value is None or str(value).strip() == "":
        return None
    if str(value).strip().lower() in accepted:
        return canonical
    raise ValueError(
        f"{what} must be in {canonical}. This software works in mm, bar and °C only; "
        f"'{value}' is not supported. Please convert the value first."
    )


def length_unit(value: Optional[str], what: str = "Thickness / length") -> Optional[str]:
    return _check(value, _MM, LENGTH_UNIT, what)


def pressure_unit(value: Optional[str], what: str = "Pressure") -> Optional[str]:
    return _check(value, _BAR, PRESSURE_UNIT, what)


def temperature_unit(value: Optional[str], what: str = "Temperature") -> Optional[str]:
    return _check(value, _CELSIUS, TEMPERATURE_UNIT, what)
