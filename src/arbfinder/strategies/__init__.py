from arbfinder.strategies.base import Strategy, Signal
from arbfinder.strategies.registry import register, get, all_strategies
import arbfinder.strategies.arbitrage  # noqa: F401  (registriert sich beim Import)
import arbfinder.strategies.value      # noqa: F401  (registriert sich beim Import)
__all__ = ["Strategy", "Signal", "register", "get", "all_strategies"]
