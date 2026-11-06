# Da fare

Niente in sospeso.

## Fatto

### Esempi di risultato per ogni casella della matrice (6 novembre 2026)

Nella schermata "Cosa costruiamo?", sotto la matrice "La squadra", il pulsante
**Cosa ottengo in ogni casella?** mostra per ognuna delle 9 combinazioni
difficoltà × qualità un esempio concreto del risultato, partendo dall'idea scritta:
un titolo dentro ogni casella (solo su schermi larghi) e, nel riquadro accanto,
per la casella scelta: cosa ottieni, cosa non aspettarti, quanto è curato.

- `swarm_examples.py`: prompt e chiamata a `claude -p` (Haiku, `--effort low`,
  nessuno strumento, system prompt corto, cartella temporanea), controllo del JSON
  (le caselle mancanti si riempiono a regole) ed esempi finti per la demo.
- `swarm_web.py`: `POST /api/examples {idea}`, stessa origine come le altre POST;
  una richiesta alla volta e cache per idea.
- `web/index.html`: pulsante, stato (in corso, costo, errore, idea cambiata) ed
  esempi in matrice e nel riquadro. Anche la pagina tiene una cache per idea: la
  richiesta si rifà solo se l'idea cambia.
- Provato in demo (desktop e 375px, chiaro e scuro) e in reale: circa 11 secondi,
  circa $0,001 a richiesta.
- Prompt rivisto lo stesso giorno: con il primo prompt 15 titoli su 36 erano
  aggettivi ("completo e curato") e nella stessa riga le funzioni cambiavano con la
  cura. Ora Haiku dà solo le funzioni per grandezza (righe), tre fatti di cura per
  riga e una frase per livello di cura (colonne); `assemble()` compone le 9 caselle,
  quindi una riga ha per forza le stesse funzioni. Titolo = "funzione, fatto
  concreto" (es. "Calcolare scadenze abbonamento, gestisce il 29 febbraio").
  Provato su 4 idee: nessun titolo-aggettivo, 6-17 secondi, $0,0007-0,002.
  Attenzione: chiedere a Haiku di scrivere le 9 caselle con tanti vincoli lo fa
  ragionare a lungo (circa 45 s); per questo la risposta è compatta.

Possibili ritocchi: vedere l'esempio anche al passaggio del mouse senza cambiare
casella; mostrare i titoli anche su telefono (ora sono nascosti per spazio).
