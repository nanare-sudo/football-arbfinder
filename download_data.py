#!/usr/bin/env python3
"""
download_data.py — gezielter Download BEKANNTER football-data.co.uk CSV-Dateien.

KEIN Scraping: holt ausschliesslich feste, bekannte Datei-URLs nach dem (ueber die
vorhandene E0-Datei) verifizierten Schema

    https://www.football-data.co.uk/mmz4281/<SAISON>/<LIGA>.csv

Die HTML-Uebersichtsseiten (englandm.php etc.) werden NICHT abgefragt oder
geparst, nichts wird durchsucht, nichts umgangen. Es ist ein gezielter Abruf
offiziell zum Download angebotener Datensaetze (vgl. Leitplanken in CLAUDE.md).

Selbstkorrektur (zwingend — nicht jede Liga x Saison existiert):
- 404 / leere Datei / kein gueltiges CSV  -> ueberspringen, am Ende auflisten.
- Datei OHNE Pinnacle-Spalten (PSH/PSD/PSA oder PSCH/PSCD/PSCA) ist fuer unsere
  Methode nutzlos -> NICHT ablegen bzw. wieder entfernen + als "ohne Pinnacle"
  melden.
- Liga ganz ohne Pinnacle (in KEINER Saison) -> melden (lohnt sich nicht).

Wiederverwendbar: ``python download_data.py`` oder ``from download_data import
download_all``. Skip-if-exists, Timeout je Request, kurzer Delay zwischen
Requests, realistischer User-Agent (manche Server lehnen nackte Requests mit 403
ab — der Header wird daher proaktiv gesetzt).
"""
from __future__ import annotations

import csv
import time
import urllib.error
import urllib.request
from pathlib import Path

BASE_URL = "https://www.football-data.co.uk/mmz4281/{season}/{league}.csv"
OUT_DIR = Path("data/leagues")

# Vierstellige Saison-Codes: 2021 = Saison 2020/21, ... 2425 = 2024/25.
SEASONS: tuple[str, ...] = ("2021", "2122", "2223", "2324", "2425")

# Weniger liquide Ligen (EPL/E0 bewusst weggelassen — zu effizient).
LEAGUES: tuple[str, ...] = (
    "E1", "E2", "E3", "EC",          # England: Championship/League 1/League 2/Conference
    "SC0", "SC1", "SC2", "SC3",      # Schottland: Premiership..League 2
    "D2",                            # Deutschland: 2. Bundesliga
    "I2",                            # Italien: Serie B
    "SP2",                           # Spanien: La Liga 2
    "F2",                            # Frankreich: Ligue 2
    "N1",                            # Niederlande: Eredivisie
    "B1",                            # Belgien: Jupiler Pro League
    "P1",                            # Portugal: Primeira Liga
    "T1",                            # Tuerkei: Sueper Lig
    "G1",                            # Griechenland: Super League
)

USER_AGENT = ("Mozilla/5.0 (X11; Linux x86_64; rv:128.0) Gecko/20100101 Firefox/128.0")
TIMEOUT_S = 30.0
DELAY_S = 0.7

REQUIRED_COLS = ("Date", "HomeTeam", "AwayTeam", "FTR")
PINNACLE_OPEN = ("PSH", "PSD", "PSA")
PINNACLE_CLOSE = ("PSCH", "PSCD", "PSCA")


def _header_cols(text: str) -> list[str]:
    """Erste CSV-Kopfzeile als Spaltenliste (BOM-sicher)."""
    text = text.lstrip("﻿")
    if not text.strip():
        return []
    first_line = text.splitlines()[0]
    return next(csv.reader([first_line])) if first_line else []


def looks_like_csv(cols: list[str]) -> bool:
    """Echtes football-data-CSV? (Kopf enthaelt die Pflichtfelder)."""
    return all(c in cols for c in REQUIRED_COLS)


def is_valid_pinnacle_file(path: Path) -> bool:
    """True, wenn die Datei ein echtes football-data-CSV MIT Pinnacle-Spalten ist.

    Wird fuer die Ordner-Zaehlung genutzt, damit 'gueltige Dateien' wirklich
    validiert ist (nicht bloss ein roher *.csv-Glob, der z.B. eine fremde Datei
    mitzaehlen wuerde).
    """
    try:
        cols = _header_cols(path.read_text(encoding="utf-8-sig", errors="replace"))
    except OSError:
        return False
    return looks_like_csv(cols) and has_pinnacle(cols)


def has_pinnacle(cols: list[str]) -> bool:
    """Pinnacle-Eroeffnung ODER -Schluss vorhanden?"""
    return all(c in cols for c in PINNACLE_OPEN) or all(c in cols for c in PINNACLE_CLOSE)


def fetch(url: str) -> bytes | None:
    """Holt EINE bekannte Datei-URL. None bei 404/Netzfehler/leer (kein Abbruch)."""
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT_S) as resp:
            data = resp.read()
    except urllib.error.HTTPError as exc:                 # 404, 403, ...
        print(f"    HTTP {exc.code} — uebersprungen")
        return None
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        print(f"    Netzfehler: {exc} — uebersprungen")
        return None
    if not data or not data.strip():
        print("    leere Datei — uebersprungen")
        return None
    return data


def download_all(out_dir: Path = OUT_DIR, *,
                 leagues: tuple[str, ...] = LEAGUES,
                 seasons: tuple[str, ...] = SEASONS) -> dict:
    """Laedt LIGA x SAISON, validiert, und gibt eine Status-Uebersicht zurueck."""
    out_dir.mkdir(parents=True, exist_ok=True)
    ok: list[str] = []
    missing: list[str] = []
    no_pinnacle: list[str] = []
    league_has_pinn = {lg: False for lg in leagues}

    for lg in leagues:
        for sea in seasons:
            name = f"{lg}_{sea}.csv"
            dest = out_dir / name
            url = BASE_URL.format(season=sea, league=lg)

            # skip-if-exists: vorhandene Datei nur (re-)validieren, nicht neu laden.
            if dest.exists() and dest.stat().st_size > 0:
                cols = _header_cols(dest.read_text(encoding="utf-8-sig", errors="replace"))
                if looks_like_csv(cols) and has_pinnacle(cols):
                    ok.append(name)
                    league_has_pinn[lg] = True
                    print(f"[skip] {name} (vorhanden, gueltig)")
                elif looks_like_csv(cols):
                    dest.unlink()
                    no_pinnacle.append(name)
                    print(f"[skip->del] {name} (vorhanden, OHNE Pinnacle — entfernt)")
                else:
                    dest.unlink()
                    missing.append(name)
                    print(f"[skip->del] {name} (vorhanden, kein gueltiges CSV — entfernt)")
                continue

            print(f"[get ] {name}  <- {url}")
            data = fetch(url)
            time.sleep(DELAY_S)                           # hoeflich zum Server
            if data is None:
                missing.append(name)
                continue
            cols = _header_cols(data.decode("utf-8-sig", errors="replace"))
            if not looks_like_csv(cols):
                print("    kein gueltiges football-data-CSV (Kopf fehlt) — uebersprungen")
                missing.append(name)
                continue
            if not has_pinnacle(cols):
                print("    OHNE Pinnacle-Spalten — ausgeschlossen (nicht abgelegt)")
                no_pinnacle.append(name)
                continue
            dest.write_bytes(data)                        # Originalbytes (BOM erhalten)
            ok.append(name)
            league_has_pinn[lg] = True
            print(f"    OK ({len(data)} bytes) -> {dest}")

    return {
        "ok": ok,
        "missing": missing,
        "no_pinnacle": no_pinnacle,
        "leagues_without_pinnacle": [lg for lg, h in league_has_pinn.items() if not h],
        # echt validiert (nicht bloss roher Glob) — passt zum Label "gueltig":
        "valid_files_in_dir": sorted(p.name for p in out_dir.glob("*.csv")
                                     if is_valid_pinnacle_file(p)),
    }


def print_overview(result: dict) -> None:
    print("\n" + "=" * 66)
    print("UEBERSICHT")
    print(f"  erfolgreich (mit Pinnacle):    {len(result['ok'])}")
    print(f"  fehlend (404/leer/kein CSV):   {len(result['missing'])}")
    print(f"  ohne Pinnacle ausgeschlossen:  {len(result['no_pinnacle'])}")
    print(f"  gueltige Liga-Saison-Dateien im Ordner: {len(result['valid_files_in_dir'])}")
    if result["missing"]:
        print("  -> fehlend:", ", ".join(result["missing"]))
    if result["no_pinnacle"]:
        print("  -> ohne Pinnacle:", ", ".join(result["no_pinnacle"]))
    if result["leagues_without_pinnacle"]:
        print("  -> LIGEN ganz ohne Pinnacle (lohnt sich nicht):",
              ", ".join(result["leagues_without_pinnacle"]))
    print("=" * 66)


def main() -> int:
    result = download_all()
    print_overview(result)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
