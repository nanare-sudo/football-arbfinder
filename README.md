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
  theoddsapi.py    Live-Client lizenzierte API: fetch_events + fetch_historical (Key via ODDS_API_KEY)
  footballdata.py  Loader fuer football-data.co.uk CSV-Dateien (Schlussquoten + Ergebnis)
models.py          anbieterunabhaengige Event/Market (start_time ist Pflicht)
normalize.py       3-Stufen-Teamnormalisierung + Event-Identitaet (Teams+Zeit)
backfill.py        historischer Quoten-Backfill (Kosten-Guards: 10x Credits)
recorder.py        zeichnet echte Quoten-Snapshots auf (append-only, lizenz. API)
results.py         traegt echte Ergebnisse nach (ResultSource, scores-Endpoint)
detector.py        Pipeline: fetch -> normalize/merge -> pro Markt pruefen ->
                   Vollstaendigkeit zaehlen -> Strategie -> Mindest-Profit
strategies/        austauschbare Strategien (arbitrage, value; Vorbild: arbitrage.py)
fair_probability.py austauschbares Modell der fairen Wkt. (Konsens-Devig, fuer value)
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

## Strategien

| Strategie   | Idee                                              | Risiko        | `requires_validation` |
|-------------|---------------------------------------------------|---------------|-----------------------|
| `arbitrage` | beste Quote je Ausgang, Marge < 1 -> Hedge        | risikoarm     | `False` (Mathe)       |
| `value`     | beste Quote schlaegt geschaetzte faire Wkt.       | **echt** (kein Hedge) | `True` (praediktiv) |

```bash
arbfinder scan --provider mock --strategy value          # Value-Gelegenheiten melden
arbfinder backtest --strategy value                       # -> Urteil i.d.R. "parked"
```

**Value Betting trägt echtes Risiko.** Anders als Arbitrage gibt es keinen
Hedge — du setzt auf eine einzelne Quote und kannst verlieren. Alles haengt an
der Qualitaet des fairen-Wahrscheinlichkeits-Modells (`fair_probability.py`,
austauschbar). Der erste Schaetzer **Konsens-Devig** ist bewusst grob: er nimmt
an, der Markt sei im Mittel fair (Vig herausgerechnet) — was er nicht immer ist.
Zur Vermeidung von Zirkularitaet wird die faire Wahrscheinlichkeit fuer die beste
Quote per **Leave-one-out** OHNE genau diesen Bookie geschaetzt; es braucht
danach **mindestens zwei** unabhaengige Bookies (Default `min_books=2`) — ein
"Konsens" aus einer einzigen Quelle ist keiner und erzeugt kein Signal. Weil `value`
praediktiv ist, durchlaeuft sie `validation.judge`: ohne Out-of-Sample-Beleg
lautet das Urteil **"parked"** (vielversprechend, aber noch nicht bestaetigt) —
nicht "confirmed".

Ein In-/Out-of-Sample-Split (`validation.purged_split`) ist **verdrahtet**
(`backtest.run_validated`): auf den Test-Folds wird das *realisierte* Ergebnis
der Signale mit bekanntem Ausgang berechnet und an `judge` gegeben. Damit ist
der `confirmed`-Pfad fuer praediktive Strategien grundsaetzlich erreichbar —
**aber ehrlich:** der Split ist nur ein Mechanismus. Aussagekraeftig wird er
erst mit ausreichend historischen Quoten UND Ergebnissen. Bei zu wenigen
belegten OOS-Wetten (oder ganz ohne Ergebnisse) bleibt das Urteil bewusst
`parked` — es wird **nicht faelschlich** `confirmed`. Mit der winzigen
Beispiel-Fixture ist somit nur der Mechanismus getestet, keine inhaltliche
Bestaetigung.

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
- **Value Betting ist KEINE Arbitrage:** es traegt echtes Risiko (kein Hedge),
  und seine Signale sind nur so gut wie das faire-Wahrscheinlichkeits-Modell.
  Deshalb parkt `validation.judge` Value-Signale ohne Out-of-Sample-Beleg, statt
  sie zu bestaetigen.

## Echte Daten anbinden (spaeter, mit Lizenz)

`providers/theoddsapi.py` ist ein minimaler Client fuer eine kommerzielle
Odds-API (Beispiel: The Odds API, v4) — **kein** Scraper. Der Netzwerk-Pfad ist
implementiert, braucht aber einen gueltigen Schluessel und eine gueltige Lizenz.

```bash
export ODDS_API_KEY="dein_lizenzierter_schluessel"   # NICHT eincheck! (.env)
pip install -e ".[live]"
```

Der Mapping-Teil (`parse_response`) ist ohne Netzwerk getestet; der Live-Abruf
braucht einen gueltigen Schluessel und eine gueltige Lizenz.

## Daten sammeln — der eigentliche Engpass

Der Backtest misst nur **Detektion**, solange keine historischen Quoten MIT
Ergebnissen vorliegen. Das schliesst der Recorder (alles ueber die lizenzierte
API, **kein Scraping**):

```bash
export ODDS_API_KEY="dein_lizenzierter_schluessel"   # nie committen (.env)
pip install -e ".[live,agent]"

# 1) Quoten ueber Tage/Wochen aufzeichnen (append-only)
arbfinder record --interval 10 --sport soccer_epl --out data/epl.jsonl

# 2) Nach den Spielen die tatsaechlichen Ausgaenge nachtragen (scores-Endpoint)
arbfinder fetch-results --data data/epl.jsonl --sport soccer_epl --days-from 3

# 3) Backtesten — mit Ergebnissen misst der Backtest jetzt auch PnL, nicht nur Detektion
arbfinder backtest --strategy value --data data/epl.jsonl
```

Realistischer Ablauf: Recorder ueber Tage/Wochen laufen lassen, regelmaessig
`fetch-results` ziehen (der scores-Endpoint reicht nur begrenzt zurueck, siehe
`--days-from`), dann backtesten. Erst mit **genuegend** Snapshots MIT Ergebnissen
wird der `confirmed`-Pfad fuer praediktive Strategien aussagekraeftig — vorher
bleibt es bewusst `parked`.

Ehrlich zur **Quoten-Latenz**: je API-Tier liegen Sekunden bis Minuten zwischen
echter Quotenaenderung und Abruf. Ein im Snapshot erkanntes Signal war also nicht
zwingend real setzbar — dokumentiere die Snapshot-Frequenz (`--interval`), wenn du
spaeter Profitabilitaet bewertest. Das API-**Kontingent** ist begrenzt (der Recorder
loggt den Verbrauch); `--interval` ist die primaere Rate-Kontrolle.

Die Ergebnis-Quelle ist **austauschbar** (`results.ResultSource`): deckt The Odds
API eine Sportart/Periode nicht ab, kann eine andere Quelle eingehaengt werden.
Fehlt ein Ergebnis, bleibt `result` offen — es wird **nichts erfunden**.

### Sofort: historischer Backfill (Arbitrage) — statt wochenlang vorwaerts sammeln

```bash
# ACHTUNG: der historische Endpoint kostet ~10x Credits pro Abruf.
arbfinder backfill --sport soccer_epl \
  --from 2024-08-17T11:00:00Z --to 2024-08-17T17:00:00Z --interval 10 \
  --out data/epl_hist.jsonl --max-snapshots 100
arbfinder backtest --strategy arbitrage --data data/epl_hist.jsonl
```

`--from/--to/--interval` sind **Pflicht** (kein versehentliches Verbrennen des
Kontingents); die geschaetzten Credits werden vorher laut geloggt, und
`--max-snapshots` bricht ab, bevor ein zu grosser Lauf startet.

**Ehrlich:** 5-10-Minuten-Snapshots zeigen, dass zu diesem Zeitpunkt eine Arb
**existierte** — NICHT, dass sie lange genug **real setzbar** war. Der historische
Arbitrage-Backtest liefert damit eine **Obergrenze** gefundener Arbs (Detektion),
nicht "so viel haetten wir verdient" (Ausfuehrbarkeit). Und ein aussagekraeftiger
Arb-Backtest braucht das **volle Bookie-Set** (z.B. Pinnacle), das nur hoehere
API-Plaene (Business) liefern — sonst fehlen schlicht die Arbs.

### Sofort: kostenlose Schlussquoten + Ergebnisse (Value)

[football-data.co.uk](https://www.football-data.co.uk/) bietet CSV-Dateien zum
**Download** an (Schlussquoten mehrerer Bookies PLUS Ergebnis je Spiel). Datei
laden (kein Scraping!), konvertieren, backtesten:

```python
from arbfinder.providers.footballdata import to_jsonl
to_jsonl("E0.csv", "data/epl.jsonl")     # CSV -> JSONL (durch normalize.py)
```
```bash
arbfinder backtest --strategy value --data data/epl.jsonl
```

Weil hier **echte Ergebnisse** vorliegen, ist das der erste Lauf, bei dem das
`confirmed`-Urteil inhaltlich greifen KANN.

**Echtes Beispiel (3 PL-Saisons, 2022/23–2024/25, 1140 Spiele):** 678 Value-
Signale, behaupteter In-Sample-Edge ~6.9 %, **realisierter PnL +2061** auf 678
Wetten (Einsatz je 100 → +3.0 %), Urteil **confirmed**. **Aber ehrlich einordnen:**
- Konsens-Devig ist nur ein **grober** Schaetzer; +3 % auf Schlussquoten ist viel
  und kommt wesentlich aus dem Wetten der **grosszuegigsten** Quote je Ausgang
  (Line-Shopping) — genau das, was Buchmacher mit **Limitierung** von
  Gewinner-Konten unterbinden (im Backtest nicht modelliert).
- **Kosten/Provision/Limits** sind NICHT abgezogen; "confirmed" heisst hier
  "realisierter Edge ueber die Stichprobe war positiv (≥30 Wetten)", nicht
  "garantierter Profit".
- Der Out-of-Sample-Split ist fuer dieses **nicht lernende** Modell rechnerisch
  gleich dem In-Sample (das Modell lernt nichts aus dem Train-Fold; siehe
  `run_validated`). Ein echter Holdout/Walk-Forward kaeme erst mit einer lernenden
  Strategie — bis dahin ist "confirmed" als **in-sample-positiv** zu lesen.

### Signal oder Rauschen? `arbfinder diagnose`

`diagnostics.py` prueft den bestehenden Lauf mit realistischem Bankroll-Management
(100 EUR Startkapital, Konto als bindende Grenze, Ruin-Stopp) und drei
Realitaets-Checks — **nie nur absolute PnL**:

```bash
arbfinder diagnose --data data/epl_3seasons.jsonl --plot results/bankroll.png
```
berichtet je Einsatzregel (Flat 1 % vs. 1/4-Kelly, compoundet) Endkapital, ROI
auf den Umsatz, max. Drawdown, Trefferquote, Ruin; dazu (1) Preis-Abschlag
0/1/2/3 %, (2) Konzentration nach Bookmaker & Saison, (3) Konzentration nach
Quoten-Bereich.

**Befund auf den 3 echten PL-Saisons (678 Wetten):** aus 100 EUR werden flat
120.61 EUR (ROI +3.0 %) — aber bei **51.9 % Drawdown**, und ein **3 %-Abschlag**
(realistische Slippage auf Schlussquoten) kippt es ins Minus. Der Gewinn haengt
an **einer Saison** (2022/23 +51 €, 2023/24 **−39 €**) und an wenigen
grosszuegigen Bookies; **62 % aller Wetten** sind extreme Aussenseiter (Quote
>6.0), die in Summe **verlieren** (Devig-/Favorite-Longshot-Bias). **Fazit: die
+PnL ist mit hoher Wahrscheinlichkeit Artefakt/Rauschen, kein echter Edge — kein
lernendes Modell bauen, der Edge scheitert schon an der Realitaet.**

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
