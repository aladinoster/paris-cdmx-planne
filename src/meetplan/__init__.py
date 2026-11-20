"""meetplan: exact optimiser for a long-distance couple's year of trips."""

from .calendar import YearCalendar, build_calendar
from .config import City, Config, FixedTrip, load_config, parse_config
from .model import Trip
from .solver import Plan, Result, solve

__all__ = [
    "City", "Config", "FixedTrip", "Plan", "Result", "Trip", "YearCalendar",
    "build_calendar", "load_config", "parse_config", "solve",
]
__version__ = "0.1.0"
