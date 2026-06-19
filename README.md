# arbfinder — Sportwetten-Arbitrage erkennen & melden

Erkennt risikoarme Quoten-Konstellationen ("Arbitrage") ueber Buchmacher hinweg
und **meldet** sie. Gebaut so, dass Claude Code eigenstaendig Strategien
implementieren, backtesten und plotten kann (siehe `CLAUDE.md`).

> **Leitplanke vorab:** Dieses Tool **platziert niemals Wetten**. Es erkennt und
> meldet. Echte Daten ausschliesslich ueber lizenzierte APIs — kein Scraping,
> keine Erkennungs-Umgehung. Details unten unter [Leitplanken](#leitplanken).

## Setup

```bash
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev,plot]"        # Basis + Tests + Plots
pytest                               # alles gruen?
```

Optionale Extras (werden nur fuer das jeweilige Modul gebraucht):

| Extra    | Paket         | Wofuer                                            |
|----------|---------------|---------------------------------------------------|
| `dev`    | pytest        | Tests                                             |
| `plot`   | matplotlib    | Vergleichs-Charts (`plotting.py`)                |
| `fuzzy`  | rapidfuzz     | Stufe 3 der Normalisierung (Fallback: difflib)   |
| `live`   | requests      | echter Odds-API-Provider (`providers/theoddsapi`)|
| `agent`  | apscheduler   | periodischer Agent (`agent.py`)                  |

```bash
pip install -e ".[dev,plot,fuzzy,agent]"
```

## Schnellstart

```bash
# 1) Offline scannen (Mock-Provider liest fixtures/recorded_odds.jsonl)
arbfinder scan --provider mock

# 2) Baseline-Backtest inkl. Validierungs-Urteil -> results/last_backtest.json
arbfinder backtest --strategy arbitrage

# 3) Periodischer Agent (meldet in Konsole/Logfile; setzt NIE)
python -m arbfinder.agent --interval 60 --logfile alerts.log
python -m arbfinder.agent --once                 # ein einzelner Lauf
```

`scan` zeigt zusaetzlich Datenqualitaets-Zaehler (geprueft / zusammengefuehrt /
verworfen-unvollstaendig / Signale). `backtest` vergleicht mit dem letzten Lauf
und **warnt**, wenn mehr Signale nur daher kommen, dass die
Vollstaendigkeitspruefung aufgeweicht wurde (`skipped_incomplete` gefallen).

## Architektur

```
providers/         Datenquellen -> einheitliche Event-Objekte (defensiv parsen)
  base.py          Provider-Interface + Parse-Helfer
  mock.py          liest aufgezeichnete .jsonl (laeuft offline, end-to-end)
  theoddsapi.py    STUB fuer eine lizenzierte API (Key via ODDS_API_KEY)
models.py          anbieterunabhaengige Event/Market (start_time ist Pflicht)
normalize.py       3-Stufen-Teamnormalisierung + Event-Identitaet (Teams+Zeit)
detector.py        Pipeline: fetch -> normalize/merge -> pro Markt pruefen ->
                   Vollstaendigkeit zaehlen -> Strategie -> Mindest-Profit
strategies/        austauschbare Strategien (Vorbild: arbitrage.py)
arbitrage.py       Mathe-Kern (Marge, Stake-Allokation) — fertig
backtest.py        Eval-Harness (Metriken) + validation.judge()-Urteil
validation.py      dreistufiges Urteil (confirmed/parked/rejected), kein Fallbeil
plotting.py        Vergleichs-Charts
cli.py / agent.py  CLI bzw. periodischer Scanner
```

Die **Bruecke** zwischen den neuen Modulen und dem fertigen Kern ist
`Event.to_snapshots()`: es erzeugt pro Markt genau das Snapshot-Dict, das
`Strategy.evaluate` und `backtest.run` ohnehin konsumieren. So bleibt der Kern
unangetastet.

## Die autonome Schleife (siehe CLAUDE.md)

```
messen -> Hypothese -> implementieren -> erneut messen -> plotten ->
behalten, wenn BELEGT besser (Zahlen in results/), sonst zuruecknehmen
```

Slash-Commands als Helfer: `/backtest`, `/new-strategy`, `/experiment`, `/plot`.
Eine Verbesserung zaehlt nur, wenn sie eine Metrik verbessert, **ohne** eine
andere unzulaessig zu verschlechtern — insbesondere darf `skipped_incomplete`
nicht sinken, weil der Vollstaendigkeitsschutz aufgeweicht wurde.

## Validierung — gegen Selbstbetrug, ohne Gutes zu verwerfen

Jeder Backtest liefert ein dreistufiges Urteil (`validation.judge`):

- **confirmed** — in-sample Signal und out-of-sample robust; *oder* reine
  Arbitrage (mathematische Tatsache, `requires_validation=False`).
- **parked** — vielversprechend, aber zu wenig Belege -> **nicht** verwerfen,
  mehr Daten holen. Das Sicherheitsnetz gegen vorschnelles Aussortieren.
- **rejected** — auch in-sample kein Signal -> raus.

Reine Arbitrage braucht keine Validierung; praediktive Strategien (z.B. Value
Betting) schon. Die Deflationierung bei Mehrfachtests ist nur informativ.

## Ehrliche Grenzen

- **Der Edge kommt aus Daten/Tempo/Normalisierung, nicht aus der Mathematik.**
  Die Arbitrage-Formel ist trivial und ueberall bekannt. Der Unterschied
  entsteht durch Abdeckung, Geschwindigkeit und korrektes Zusammenfuehren
  gleicher Events ueber Anbieter hinweg (`normalize.py`).
- **Ohne historische Quoten UND Ergebnisse misst der Backtest nur Detektion,
  nicht Profit.** `realized_pnl` erscheint nur, wenn die Daten `result` tragen;
  reine Quoten-Snapshots erlauben nur Aussagen ueber Detektions-Korrektheit.
- **Arbs leben Sekunden bis Minuten.** Die Quoten-Latenz je API-Tier
  entscheidet, ob ein erkanntes Arb beim Setzen noch existiert. Die
  Snapshot-Frequenz gehoert dokumentiert.
- **Phantom-Arbs** entstehen durch ungleiche Bookie-Abdeckung (ein Ausgang
  fehlt -> Marge scheinbar < 1). Deshalb wird Vollstaendigkeit geprueft und
  `skipped_incomplete` gezaehlt statt still geschluckt.
- Reale Margen liegen meist bei **1–3 %**.

## Echte Daten anbinden (spaeter, mit Lizenz)

`providers/theoddsapi.py` ist ein **Stub** fuer eine kommerzielle Odds-API
(Beispiel: The Odds API, v4) — **kein** Scraper.

```bash
export ODDS_API_KEY="dein_lizenzierter_schluessel"   # NICHT eincheck! (.env)
pip install -e ".[live]"
```

Der Mapping-Teil (`parse_response`) ist ohne Netzwerk getestet; der Live-Abruf
braucht einen gueltigen Schluessel und eine gueltige Lizenz.

## Leitplanken

- **Nur erkennen & melden, nie automatisch setzen.** Es gibt keine
  Platzierungsfunktion.
- **Keine Erkennungs-Umgehung**, kein Multi-Accounting, kein AGB-widriges
  Scraping, kein undetected-chromedriver o.ae. Echte Daten nur ueber lizenzierte
  APIs.
- **Metriken nicht schoenfrisieren**, indem Schutzmechanismen
  (Vollstaendigkeitspruefung, Markt-Trennung) aufgeweicht werden.
- **Keine stillen Platzhalter**, die falsche Ergebnisse liefern: fehlende
  Pflichtfelder (z.B. Anstosszeit) werden gemeldet/uebersprungen, nicht erfunden.
