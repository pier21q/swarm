# Da fare

Niente in sospeso.

## Fatto

### Modello per fase (6 novembre 2026)

Prima tutte le fasi usavano lo stesso modello. Con "Complessa + rifinita" erano
9 agenti su Opus, con un tetto di $90. Ora Opus resta solo su Fondamenta e
Integrazione, mentre Sviluppo e Revisione passano a Sonnet. Il tetto scende a $48.

- `swarm_advice.py`: `phase_model` (Opus → Sonnet fuori da fondamenta e
  integrazione) e `phase_budget` (la spesa per agente scende in proporzione al
  modello, mai sopra quella scelta). `recommend` mette modello e spesa su ogni
  fase; `assign_efforts` assegna anche il modello, partendo da quello scelto
  nel modulo.
- `swarm_project.py`: il piano accetta `model` per fase e ogni fase parte con il
  suo modello e il suo tetto di spesa.
- `web/index.html`: modello accanto all'effort nei consigli; nel piano un menu
  modello per fase, con riepilogo e spesa massima che si aggiornano.
- Provato in demo, sia su desktop sia a 375px: il modello cambiato a mano viene
  tenuto e la fase su Sonnet parte con $4 per agente. Non l'ho provato con
  agenti veri.

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
- Demo allineata: gli esempi finti passano anch'essi da `assemble()`, con titoli
  "funzione, fatto" generici ("Più schermate e dati salvati, salva ogni modifica
  e avvisa").
- La frase "Quanto è curato" ora è per casella, non per colonna: prima su
  Complessa/Solida poteva citare funzioni di un'altra riga ("la tua lista" di
  città salvate). Ogni frase segue il fatto di cura dello stesso livello, così non
  lo contraddice. Provato su 3 idee (meteo, ricettario, script per le foto):
  nessuna funzione di un'altra riga, nessuna contraddizione, 7-16 secondi,
  $0,0008-0,002.

Possibili ritocchi: vedere l'esempio anche al passaggio del mouse senza cambiare
casella; mostrare i titoli anche su telefono (ora sono nascosti per spazio).
