# context.md — Fachwissen (vom Agenten bei Bedarf nachzuladen)

## Domaene in einem Absatz
Buchmacher uebersetzen ihre Meinung ueber einen Ausgang in eine Dezimalquote o;
1/o ist die implizite Wahrscheinlichkeit. Summieren sich die besten 1/o ueber
ALLE Ausgaenge auf < 1, existiert eine risikoarme Aufteilung ("Arbitrage").
Der Vorteil entsteht NICHT durch die Mathematik (trivial, ueberall bekannt),
sondern durch Datenqualitaet, Abdeckung, Geschwindigkeit und korrektes
Zusammenfuehren gleicher Events ueber Anbieter hinweg.

## Glossar
- Dezimalquote: Auszahlung pro 1 Einsatz inkl. Einsatz (2.50 = 1.50 Gewinn).
- Marge / Overround: sum(1/o). > 1 Buchmacher-Vorteil, < 1 Arbitrage.
- h2h / 1X2: Sieg-Markt. 2-Wege (Tennis) oder 3-Wege (Fussball mit Remis).
- totals / spreads: Ueber-Unter bzw. Handicap — NIE mit h2h mischen.
- Vig: eingebaute Buchmacher-Marge.

## Daten-Realitaeten (fuer ehrliche Backtests wichtig)
- Reine Quoten-Snapshots ohne Ergebnis -> nur Detektions-Korrektheit messbar,
  KEINE Profitabilitaet. Echte Profit-Backtests brauchen Quoten UND Ausgaenge
  ueber Zeit. Diese Daten sind der eigentliche Engpass.
- Quoten-Latenz je API-Tier (Sekunden bis Minuten) bestimmt, ob ein erkanntes
  Arb beim Setzen noch lebt. Im Backtest die Snapshot-Frequenz dokumentieren.
- Ungleiche Bookie-Abdeckung erzeugt Phantom-Arbs (Marge scheinbar < 1, weil
  ein Ausgang fehlt). Immer Vollstaendigkeit pruefen.

## Ideen-Backlog fuer neue Strategien (Agent darf vorschlagen & testen)
- value: setze, wenn beste Quote eine fairere (devigte) Wahrscheinlichkeit
  unterbietet. Braucht ein faires-Wahrscheinlichkeits-Modell (z.B. Quoten
  mehrerer Bookies mitteln und Vig herausrechnen). Traegt echtes Risiko.
- middling / line-shopping: nur beste Quote je Ausgang, ohne Hedge.
- 2-Wege vs 3-Wege getrennt behandeln (expected_outcomes!).

## Metriken, an denen "besser" gemessen wird
signals, avg_edge_pct, skipped_incomplete (Datenqualitaet!), realized_pnl
(nur mit Ergebnissen). "Mehr Signale" allein ist KEIN Fortschritt, wenn dabei
skipped_incomplete sinkt, weil die Vollstaendigkeitspruefung aufgeweicht wurde.
