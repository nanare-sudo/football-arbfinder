"""
Minimaler Client fuer eine kommerzielle Odds-API (Beispiel: The Odds API, v4).

Der Netzwerk-Pfad (``fetch_events``) ist implementiert, aber nur mit gueltigem
Lizenz-Schluessel nutzbar. Dies ist bewusst KEIN Scraper. Echte Daten kommen
ausschliesslich ueber eine lizenzierte API (siehe Leitplanken in CLAUDE.md).

>>> WO KOMMT DER API-KEY REIN? <<<
    Setze die Umgebungsvariable ODDS_API_KEY (z.B. in einer .env-Datei, die NICHT
    eingecheckt wird) oder uebergib ihn an den Konstruktor:
        export ODDS_API_KEY="dein_lizenzierter_schluessel"
        provider = TheOddsApiProvider(sport="soccer_epl")

Der Netzwerk-Pfad (``fetch_events``) ist implementiert, aber erst mit gueltigem
Schluessel/gueltiger Lizenz nutzbar. Das Mapping der dokumentierten JSON-Antwort
auf unsere Modelle steckt in ``parse_response`` und ist OHNE Netzwerk testbar.

Dokumentierte Antwortstruktur (v4 /sports/{sport}/odds):
    [
      {
        "id": "...", "sport_key": "soccer_epl",
        "commence_time": "2026-08-15T15:00:00Z",
        "home_team": "Manchester City", "away_team": "Arsenal",
        "bookmakers": [
          {"key": "pinnacle", "title": "Pinnacle", "markets": [
             {"key": "h2h", "outcomes": [
                {"name": "Manchester City", "price": 2.10},
                {"name": "Draw", "price": 3.60},
                {"name": "Arsenal", "price": 4.00}]}]}]
      }, ...
    ]
"""
from __future__ import annotations

from typing import Any
import logging
import os

from arbfinder.models import Event, Market
from arbfinder.providers.base import (
    OddsProvider,
    ProviderError,
    coerce_float,
    parse_datetime,
)

logger = logging.getLogger("arbfinder.providers.theoddsapi")

_BASE_URL = "https://api.the-odds-api.com/v4"

# Sportarten mit moeglichem Unentschieden -> 3-Wege h2h. Sonst 2-Wege.
_DRAW_SPORTS = ("soccer", "football_aussie", "rugby", "hockey", "cricket")


def _fmt_point(point: object) -> str:
    """Formatiert eine Linie/Handicap stabil fuer den Markttyp-Schluessel."""
    try:
        f = float(point)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return str(point)
    return str(int(f)) if f.is_integer() else str(f)


def _expected_outcomes(market_key: str, sport_key: str, observed: int) -> int:
    """Schaetzt die SOLL-Zahl der Ausgaenge fuer die Vollstaendigkeitspruefung.

    Bewusst konservativ: bei h2h entscheidet die Sportart (3-Wege bei
    Remis-faehigen Sportarten, sonst 2). totals/spreads sind PRO LINIE 2-Wege.
    Bei unbekannten Markttypen geben wir 0 zurueck ("Erwartung unbekannt" gemaess
    Market.is_complete) statt der beobachteten Anzahl — letztere waere eine
    selbsterfuellende present==expected-Tautologie und wuerde den
    Vollstaendigkeitsschutz aushebeln.
    """
    mk = market_key.lower()
    if mk == "h2h":
        return 3 if any(sport_key.lower().startswith(s) for s in _DRAW_SPORTS) else 2
    if mk in ("totals", "spreads"):
        return 2
    return 0


def parse_response(raw: list[dict[str, Any]]) -> list[Event]:
    """Mappt die dokumentierte API-Antwort defensiv auf ``Event``-Objekte.

    Pure Funktion ohne Netzwerk: so testbar mit einem Beispiel-Payload.
    Zeilen mit fehlenden Pflichtfeldern werden geloggt und uebersprungen.
    """
    events: list[Event] = []
    for raw_ev in raw:
        try:
            home = raw_ev["home_team"]
            away = raw_ev["away_team"]
            start_time = parse_datetime(raw_ev["commence_time"])
        except (KeyError, ValueError) as exc:
            logger.warning("Event uebersprungen (Pflichtfeld fehlt/kaputt): %s", exc)
            continue

        sport_key = str(raw_ev.get("sport_key", ""))
        # market_type -> {outcome -> {bookmaker -> price}}; base_of merkt sich den
        # Basis-Markt (z.B. 'totals') je linien-spezifischem Typ ('totals_2.5').
        by_market: dict[str, dict[str, dict[str, float]]] = {}
        base_of: dict[str, str] = {}
        for bm in raw_ev.get("bookmakers", []):
            book = str(bm.get("key") or bm.get("title") or "unknown")
            for m in bm.get("markets", []):
                mkey = str(m.get("key", "")).strip()
                if not mkey:
                    continue
                for oc in m.get("outcomes", []):
                    name = oc.get("name")
                    price = coerce_float(oc.get("price"))
                    if name is None or price is None or price <= 0:
                        continue
                    # Punkt-/Linien-Maerkte (totals/spreads) je Linie TRENNEN.
                    # Sonst kollabieren inkompatible Linien (Over@2.5 + Under@3.5)
                    # in EINEN Markt und erzeugen nicht setzbare Phantom-Arbs.
                    point = oc.get("point")
                    market_type = mkey if point is None else f"{mkey}_{_fmt_point(point)}"
                    base_of[market_type] = mkey
                    by_market.setdefault(market_type, {}).setdefault(str(name), {})[book] = price

        markets = [
            Market(
                market_type=mt,
                odds=odds,
                expected_outcomes=_expected_outcomes(base_of[mt], sport_key, len(odds)),
            )
            for mt, odds in by_market.items()
            if odds
        ]
        if not markets:
            continue
        events.append(
            Event(
                event_id=str(raw_ev.get("id", "")),
                home=str(home),
                away=str(away),
                start_time=start_time,
                sport=sport_key,
                league=str(raw_ev.get("sport_title", "")),
                markets=markets,
            )
        )
    return events


class TheOddsApiProvider(OddsProvider):
    """Live-Provider fuer The Odds API. Erfordert gueltigen Lizenz-Schluessel."""

    name = "theoddsapi"

    def __init__(
        self,
        sport: str = "upcoming",
        *,
        api_key: str | None = None,
        regions: str = "eu",
        markets: str = "h2h",
        odds_format: str = "decimal",
        base_url: str = _BASE_URL,
    ) -> None:
        # API-Key: explizit > Umgebungsvariable. Nie hart im Code.
        self.api_key = api_key or os.environ.get("ODDS_API_KEY", "")
        self.sport = sport
        self.regions = regions
        self.markets = markets
        self.odds_format = odds_format
        self.base_url = base_url.rstrip("/")
        # Zuletzt gemeldeter API-Kontingent-Stand (aus den Antwort-Headern).
        self.last_quota: dict[str, str | None] = {}

    def fetch_events(self) -> list[Event]:
        """Holt Live-Quoten. Erfordert ODDS_API_KEY und installiertes ``requests``."""
        if not self.api_key:
            raise ProviderError(
                "Kein API-Key. Setze ODDS_API_KEY (Umgebungsvariable) oder uebergib "
                "api_key=... — echte Daten nur mit gueltiger Lizenz."
            )
        try:
            import requests  # optionale Abhaengigkeit, erst hier noetig
        except ImportError as exc:  # pragma: no cover - nur ohne 'requests'
            raise ProviderError(
                "Paket 'requests' fehlt. Installiere mit: pip install arbfinder[live]"
            ) from exc

        url = f"{self.base_url}/sports/{self.sport}/odds"
        params = {
            "apiKey": self.api_key,
            "regions": self.regions,
            "markets": self.markets,
            "oddsFormat": self.odds_format,
        }
        try:
            resp = requests.get(url, params=params, timeout=15)
        except requests.exceptions.RequestException as exc:
            # WICHTIG: 'from None' + nur Typname. Der API-Key steckt als Query in
            # der URL; requests-Exceptions enthalten die URL -> der Key darf NICHT
            # ueber die Exception in Logs/Tracebacks gelangen (Leitplanke).
            raise ProviderError(f"Odds-API nicht erreichbar: {type(exc).__name__}") from None
        # Kontingent-Header festhalten (auch bei Fehlern nuetzlich).
        self.last_quota = {
            "remaining": resp.headers.get("x-requests-remaining"),
            "used": resp.headers.get("x-requests-used"),
        }
        if not resp.ok:
            raise ProviderError(f"Odds-API HTTP {resp.status_code}")   # KEINE URL/kein Key
        return parse_response(resp.json())
