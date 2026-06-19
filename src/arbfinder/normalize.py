"""
Normalisierung — HIER liegt der eigentliche Vorteil ggue. naiven Tools.

Zwei Aufgaben:

1. Teamnamen kanonisieren (3-stufig), damit "Man City" und "Manchester City"
   als DASSELBE Team erkannt werden — ``lower()`` allein reicht nicht.
       Stufe 1  Alias   : kuratierte Abkuerzungen -> kanonischer Name.
       Stufe 2  Kanonik : saeubern (Diakritika, Satzzeichen), Club-Suffixe
                          (FC/AFC/...) entfernen, Title-Case.
       Stufe 3  Fuzzy   : gegen ein Gazetteer bekannter Namen abgleichen
                          (rapidfuzz, optional; sonst difflib-Fallback) — faengt
                          Tippfehler wie "Manchestr City".

2. Event-Identitaet = TEAMS *und* ANSTOSSZEIT. Zwei Spiele derselben Teams an
   verschiedenen Tagen sind VERSCHIEDENE Events und duerfen NICHT
   zusammengefuehrt werden. ``merge_events`` fasst nur echte Duplikate (gleiche
   Teams, Anstosszeit innerhalb einer Toleranz) ueber Anbieter/Maerkte hinweg
   zusammen — und vereinheitlicht dabei auch die Ausgangs-Namen.

Designgrundsatz: lieber konservativ NICHT zusammenfuehren als faelschlich
verschmelzen (ein faelschlich verschmolzenes Event erzeugt Phantom-Quoten).
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Iterable
import re
import unicodedata

from arbfinder.models import Event, Market

try:  # Stufe 3 bevorzugt rapidfuzz; ohne Paket sauberer Fallback auf difflib.
    from rapidfuzz import fuzz, process

    _HAVE_RAPIDFUZZ = True
except ImportError:  # pragma: no cover - nur ohne rapidfuzz
    _HAVE_RAPIDFUZZ = False
    from difflib import SequenceMatcher


# --------------------------------------------------------------------------- #
# Stufe 1: Alias-Tabelle (kuratiert). Schluessel werden "gesaeubert" verglichen.
# Die WERTE sind bereits in kanonischer Form (invariant unter _canonicalize,
# siehe test_normalize) — so konvergieren Stufe 1 und Stufe 2 auf denselben Namen.
# --------------------------------------------------------------------------- #
ALIASES: dict[str, str] = {
    "man city": "Manchester City",
    "mancity": "Manchester City",
    "man utd": "Manchester United",
    "man united": "Manchester United",
    "man u": "Manchester United",
    "spurs": "Tottenham Hotspur",
    "tottenham": "Tottenham Hotspur",
    "wolves": "Wolverhampton Wanderers",
    "wolverhampton": "Wolverhampton Wanderers",
    "newcastle": "Newcastle United",
    "leeds": "Leeds United",
    "west ham": "West Ham United",
    "brighton": "Brighton Hove Albion",
    "psg": "Paris Saint Germain",
    "bayern": "Bayern Munich",
    "barca": "Barcelona",
}

# Reine Club-Typ-Tokens, die fuer die Identitaet bedeutungslos sind. Bewusst
# KLEIN gehalten (nur eindeutige Suffixe), um echte Namensbestandteile nicht zu
# verschlucken (z.B. "AC"/"AS" sind hier NICHT drin -> "AC Milan" bleibt heil).
_NOISE_TOKENS = {"fc", "afc", "cf", "sc", "ssc"}


def _clean(name: str) -> str:
    """Kleinschreibung, Diakritika weg, Satzzeichen weg, Whitespace normalisiert."""
    s = unicodedata.normalize("NFKD", str(name))
    s = "".join(c for c in s if not unicodedata.combining(c))
    s = s.lower()
    s = re.sub(r"[._'`]", "", s)          # Apostrophe/Punkte ersatzlos entfernen
    s = re.sub(r"[^a-z0-9 ]+", " ", s)    # uebrige Sonderzeichen -> Leerzeichen
    s = re.sub(r"\s+", " ", s).strip()
    return s


def _canonicalize(cleaned: str) -> str:
    """Stufe 2: Club-Suffixe entfernen, Title-Case. Erwartet bereits ``_clean``."""
    tokens = [t for t in cleaned.split() if t and t not in _NOISE_TOKENS]
    if not tokens:                         # Name bestand nur aus Noise -> nicht leeren
        tokens = cleaned.split()
    return " ".join(w.capitalize() for w in tokens)


def default_gazetteer() -> list[str]:
    """Bekannte kanonische Namen fuer den Fuzzy-Abgleich (aus den Alias-Werten)."""
    return sorted(set(ALIASES.values()))


def _fuzzy_match(name: str, candidates: Iterable[str], threshold: float) -> str | None:
    """Stufe 3: bester Treffer >= threshold (0-100) oder None."""
    cands = list(candidates)
    if not cands:
        return None
    if _HAVE_RAPIDFUZZ:
        match = process.extractOne(name, cands, scorer=fuzz.token_sort_ratio)
        if match and match[1] >= threshold:
            return match[0]
        return None
    best, best_score = None, 0.0          # difflib-Fallback
    for c in cands:
        score = SequenceMatcher(None, name.lower(), c.lower()).ratio() * 100.0
        if score > best_score:
            best, best_score = c, score
    return best if best_score >= threshold else None


def canonical_team(
    name: str,
    known: Iterable[str] | None = None,
    *,
    fuzzy_threshold: float = 88.0,
) -> str:
    """Fuehrt einen Teamnamen ueber alle 3 Stufen zum kanonischen Namen.

    Args:
        name: Roh-Teamname.
        known: optionales Gazetteer fuer Stufe 3. Wird keins uebergeben, wird
            ``default_gazetteer()`` genutzt. Leere Liste -> Stufe 3 aus.
        fuzzy_threshold: Mindest-Score (0-100) fuer einen Fuzzy-Treffer.
    """
    cleaned = _clean(name)
    if not cleaned:
        return str(name).strip()
    if cleaned in ALIASES:                                  # Stufe 1
        return ALIASES[cleaned]
    canon = _canonicalize(cleaned)                          # Stufe 2
    gaz = default_gazetteer() if known is None else list(known)
    if gaz:                                                 # Stufe 3
        hit = _fuzzy_match(canon, gaz, fuzzy_threshold)
        if hit:
            return hit
    return canon


# --------------------------------------------------------------------------- #
# Event-Identitaet: Teams UND Anstosszeit
# --------------------------------------------------------------------------- #
def _as_utc(dt: datetime) -> datetime:
    return dt.astimezone(timezone.utc) if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def _team_key(event: Event, known: Iterable[str] | None) -> frozenset[str]:
    """Reihenfolge-unabhaengiger Schluessel aus den beiden kanonischen Teams."""
    h = canonical_team(event.home, known)
    a = canonical_team(event.away, known)
    return frozenset({h, a})


def _round_to_hour(dt: datetime) -> datetime:
    """Rundet auf die NAECHSTE volle Stunde (UTC) — daempft Minuten-Drift."""
    d = _as_utc(dt) + timedelta(minutes=30)
    return d.replace(minute=0, second=0, microsecond=0)


def event_identity(event: Event, known: Iterable[str] | None = None) -> tuple:
    """Hashbarer Identitaetsschluessel: (Teams, auf Stunde gerundete Anstosszeit).

    Verschiedene Tage/Stunden -> verschiedene Identitaet. So werden zwei Spiele
    derselben Teams an verschiedenen Tagen NICHT verwechselt.
    """
    return (_team_key(event, known), _round_to_hour(event.start_time).isoformat())


def same_event(
    e1: Event,
    e2: Event,
    known: Iterable[str] | None = None,
    *,
    time_tolerance_minutes: float = 90.0,
) -> bool:
    """True, wenn gleiche (kanonische) Teams UND Anstosszeit innerhalb Toleranz."""
    if _team_key(e1, known) != _team_key(e2, known):
        return False
    delta = abs((_as_utc(e1.start_time) - _as_utc(e2.start_time)).total_seconds()) / 60.0
    return delta <= time_tolerance_minutes


def _norm_outcome(name: str, ch: str, ca: str, known: Iterable[str] | None) -> str:
    """Vereinheitlicht team-artige Ausgangsnamen; Draw/Over/Under bleiben unberuehrt."""
    c = canonical_team(name, known)
    if c == ch:
        return ch
    if c == ca:
        return ca
    return name


def normalize_event(event: Event, known: Iterable[str] | None = None) -> Event:
    """Gibt eine Kopie des Events mit kanonischen Team- und Ausgangsnamen zurueck."""
    ch = canonical_team(event.home, known)
    ca = canonical_team(event.away, known)
    markets = []
    for m in event.markets:
        odds: dict[str, dict[str, float]] = {}
        for outcome, books in m.odds.items():
            key = _norm_outcome(outcome, ch, ca, known)
            odds.setdefault(key, {}).update({bk: float(p) for bk, p in books.items()})
        markets.append(Market(m.market_type, odds, m.expected_outcomes))
    result = _norm_outcome(event.result, ch, ca, known) if event.result else None
    return Event(
        event_id=event.event_id,
        home=ch,
        away=ca,
        start_time=event.start_time,
        sport=event.sport,
        league=event.league,
        markets=markets,
        result=result,
        snapshot_ts=event.snapshot_ts,
    )


def _merge_cluster(cluster: list[Event], known: Iterable[str] | None) -> Event:
    """Verschmilzt echte Duplikate zu EINEM Event (Maerkte/Quoten vereinigt)."""
    base = cluster[0]
    ch = canonical_team(base.home, known)
    ca = canonical_team(base.away, known)
    start = min(_as_utc(e.start_time) for e in cluster)

    # Aelteste zuerst -> bei Quoten-Konflikt (gleicher Bookie/Ausgang) gewinnt
    # der juengere Snapshot (update ueberschreibt).
    epoch = datetime.min.replace(tzinfo=timezone.utc)
    ordered = sorted(cluster, key=lambda e: _as_utc(e.snapshot_ts) if e.snapshot_ts else epoch)

    by_type: dict[str, Market] = {}
    for e in ordered:
        for m in e.markets:
            tgt = by_type.get(m.market_type)
            if tgt is None:
                tgt = Market(m.market_type, {}, m.expected_outcomes)
                by_type[m.market_type] = tgt
            tgt.expected_outcomes = max(tgt.expected_outcomes, m.expected_outcomes)
            for outcome, books in m.odds.items():
                key = _norm_outcome(outcome, ch, ca, known)
                dst = tgt.odds.setdefault(key, {})
                dst.update({bk: float(p) for bk, p in books.items()})

    result = next((e.result for e in cluster if e.result), None)
    if result:
        result = _norm_outcome(result, ch, ca, known)
    sport = next((e.sport for e in cluster if e.sport), "")
    league = next((e.league for e in cluster if e.league), "")
    snaps = [_as_utc(e.snapshot_ts) for e in cluster if e.snapshot_ts]
    return Event(
        event_id=base.event_id,
        home=ch,
        away=ca,
        start_time=start,
        sport=sport,
        league=league,
        markets=list(by_type.values()),
        result=result,
        snapshot_ts=max(snaps) if snaps else None,
    )


def merge_events(
    events: list[Event],
    known: Iterable[str] | None = None,
    *,
    time_tolerance_minutes: float = 90.0,
) -> list[Event]:
    """Fuehrt Duplikate ueber Anbieter/Maerkte hinweg zusammen.

    Gruppiert nach kanonischem Team-Set und clustert innerhalb einer Gruppe nur
    Events, deren Anstosszeit innerhalb der Toleranz liegt. Verschiedene Tage
    bleiben getrennt. Rueckgabe stabil sortiert nach Anstosszeit, dann Name.
    """
    groups: dict[frozenset[str], list[Event]] = {}
    for ev in events:
        groups.setdefault(_team_key(ev, known), []).append(ev)

    merged: list[Event] = []
    for evs in groups.values():
        evs_sorted = sorted(evs, key=lambda e: _as_utc(e.start_time))
        clusters: list[list[Event]] = []
        for ev in evs_sorted:
            for cl in clusters:
                anchor = _as_utc(cl[0].start_time)
                if abs((_as_utc(ev.start_time) - anchor).total_seconds()) / 60.0 <= time_tolerance_minutes:
                    cl.append(ev)
                    break
            else:
                clusters.append([ev])
        merged.extend(_merge_cluster(cl, known) for cl in clusters)

    merged.sort(key=lambda e: (_as_utc(e.start_time), e.name))
    return merged
