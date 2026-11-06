# Da fare

Niente in sospeso.

## Fatto

### Correzioni di bug (6 novembre 2026)

- **Schermata iniziale vuota dopo l'interruttore "Lanciare build e test"**
  (`web/index.html`). L'input nascosto dell'interruttore era `position: absolute`
  senza un genitore posizionato: finiva circa 1.500px più in basso, fuori dal
  riquadro che scorre, e allungava la pagina. Al clic il browser faceva scorrere
  tutta la finestra fino a lui e restava solo lo sfondo. Ora `.switch` e `.scroll`
  hanno `position: relative`. Provato in demo e in reale, su desktop e a 375px.
- **Riconnessione che cancellava il modulo** (`web/index.html`): se la connessione
  live cadeva e ripartiva, la pagina rileggeva le impostazioni dal server e
  rimetteva a "no" build e test mentre stavi compilando. Ora il modulo resta com'è.
- **"Riparti da questa idea" teneva i dati della corsa di prima**
  (`swarm_project.py`, `back_to_form`). La nuova corsa sommava i costi vecchi, il
  tempo in alto era sbagliato (restava la fine della corsa precedente) e, se la
  nuova pianificazione falliva, si apriva la pagina finale vecchia. Ora fasi,
  costi, tempi, risultato e stato GitHub ripartono da zero. Provato in demo: la
  seconda corsa parte da $0,04 e il tempo è giusto.
- **Annullare una ripianificazione riportava al modulo vuoto**
  (`swarm_project.py`, `stop`). Ora torni al piano di prima.
- **Annulla e poi subito "Prepara il piano"** (`swarm_project.py`): il
  pianificatore vecchio, terminato in ritardo, poteva segnare "pianificazione
  fallita" su quello nuovo. Ora ogni pianificazione ha un numero e quelle vecchie
  non toccano più lo stato. Provato in demo. In reale non l'ho riprodotto: il
  problema l'ho trovato leggendo il codice.
- Scheda GitHub senza `gh`: il pulsante "Ricontrolla" era fuori dalla sua riga.

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
