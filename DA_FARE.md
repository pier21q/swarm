# Da fare

Niente di aperto dalla revisione del 7 novembre 2026.

Resta da provare con agenti veri (costa, quindi solo quando lo decidi tu): che
`claude -p` legga il compito da stdin e che `--disallowedTools "Bash(git * --output*)"`
blocchi davvero `git diff --output=…`. Lo stesso per il tetto del pianificatore
(`--max-budget-usd`): il nome esatto dell'evento quando lo raggiunge non l'ho visto
con un pianificatore vero.

Dalla revisione esterna dell'8 novembre (30 punti): la lista completa è andata
persa, restano da ricontrollare gli altri punti gravi (segreti nella history su
GitHub, agenti che committano `.env`/`node_modules`, "Ferma" tra due fasi,
branch in conflitto perso, commit fallito, testo con graffe dopo il JSON del piano).

## Fatto

### Iframe, localhost su IPv6 e tetto del pianificatore (13 novembre 2026)

Tre punti della revisione esterna dell'8 novembre, ancora aperti.

- **Dashboard dentro un iframe** (`swarm_web.py`): ogni risposta, anche gli
  eventi SSE e i rifiuti, ha `X-Frame-Options: DENY` e
  `Content-Security-Policy: frame-ancestors 'none'` (più `nosniff` e
  `no-referrer`). Un altro sito non può più incorniciare la pagina e far
  cliccare "Avvia" o "Carica su GitHub" di nascosto.
- **`localhost` su `::1`** (`swarm_web.py`): il server ascoltava solo su
  127.0.0.1, ma `localhost` può risolvere a `::1`, e lì poteva rispondere un
  altro programma. Ora `_listen` apre la stessa porta su 127.0.0.1 e `::1`; se
  uno dei due è occupato passa alla porta dopo. Se il sistema non ha IPv6 resta
  solo 127.0.0.1.
- **Tetto di spesa del pianificatore** (`swarm_advice.py`, `swarm_project.py`,
  `web/index.html`): `planner_budget(model, effort)` = metà della spesa di un
  agente con lo stesso modello ed effort, mai sotto $0,50 (Haiku $0,50, Sonnet
  $1–1,50, Opus $2,50–4). Passato a `claude -p` con `--max-budget-usd`. Se il
  pianificatore lo raggiunge, la pagina lo dice con il tetto in dollari invece
  di un errore generico. Si vede nel riquadro "La squadra" (riga "Piano") e
  nella schermata "Il pianificatore sta preparando le fasi…".
- `README.md`: sicurezza e costi aggiornati.
- Test: nuovo `tests/test_web.py` (intestazioni su `/`, API, 404, SSE e rifiuto
  per Host sbagliato; pagina servita su 127.0.0.1 e `::1`; porta saltata se
  `::1` è occupato) e test del tetto in `test_plan.py` (passato a `claude`,
  messaggio quando è raggiunto) e `test_advice.py`. 37 test, tutti verdi.
- Provato in demo nel browser: riquadro "La squadra" a desktop chiaro, schermata
  del pianificatore a 375px scuro, nessun errore in console; con curl le
  intestazioni ci sono e la porta ascolta su entrambi gli indirizzi. Non
  provato con un pianificatore vero (costa).

### Pulizia delle corse vecchie (11 novembre 2026)

Prima dell'8 novembre swarm non toglieva niente: cartelle `.<progetto>-swarm/` e
branch degli agenti restavano a ogni corsa.

- `swarm_project.py`: `_sweep_old`, all'avvio di ogni corsa vera (prima di aprire
  il branch di lavoro). Toglie le cartelle in `.<progetto>-swarm/` che non hanno
  worktree registrati in git (e la cartella stessa, se resta vuota), e con
  `git branch -d` i branch degli agenti (`swarm/<data-ora>-f<n>/<nome>`) già
  uniti. Restano i branch con lavoro non unito, quelli aperti in un worktree
  (una corsa ancora viva) e i branch di lavoro `swarm/<data-ora>`. Sul bus:
  "tolti da corse precedenti: …".
- `tests/test_pipeline.py`: nuovo test su un repository temporaneo con un branch
  unito, uno non unito, un branch di lavoro, una cartella vecchia e una corsa
  "viva" con il suo worktree. 31 test, tutti verdi.
- Non provato in demo: la demo non scrive su disco, quindi lì non c'è niente da
  togliere e il comportamento non cambia. Non provato su un progetto vero.

### Il resto della revisione (8 novembre 2026)

Gli altri otto punti della revisione del 7 novembre.

- **Bus dal thread sbagliato** (`swarm_project.py`): `_merge`, `_prepare_repo` e
  ora `_open_work_branch` girano in `run_in_executor` e chiamavano `bus`, che
  tocca la storia e le code SSE di asyncio da un altro thread. Ora `bus`, se non
  è nel loop, si rimanda al loop con `call_soon_threadsafe` (l'ordine resta
  quello giusto).
- **Merge nel branch dell'utente** (`swarm_project.py`, `web/index.html`): in un
  progetto esistente il lavoro va in un branch nuovo `swarm/<data-ora>`, aperto
  nella cartella; il branch dell'utente resta com'era. In un progetto nuovo
  (repository creato da swarm) si lavora sul suo unico branch, come prima. Il
  piano ha la riga "Branch"; la pagina finale dice dove è il lavoro e come
  portarlo nel proprio branch. La scheda GitHub dice "il branch aperto ora".
- **La fase dopo partiva anche con agenti falliti** (`swarm_project.py`,
  `swarm_web.py`, `web/index.html`): nuovo stato `paused`. Se in una fase (non
  l'ultima) un agente va in errore, swarm unisce il lavoro degli altri e si
  ferma: la pagina "In pausa" dice chi non ha finito, con "Continua con
  <fase>" (`POST /api/continue`) e "Fermati qui". Un agente fermato a mano
  dall'utente non mette in pausa: l'ha scelto lui.
- **Merge fatto dall'agente visto come "nessuna modifica"** (`swarm_core.py`):
  `commit` e `save_unfinished` guardano anche se il branch ha commit oltre il
  punto di partenza (`Swarm.base`), non solo i file cambiati.
- **Compito che inizia con "-"** (`swarm_core.py`, `swarm_project.py`): compito
  dell'agente e prompt del pianificatore passano su stdin, non come argomento.
- **`git diff`/`git log --output=<file>`** (`swarm_project.py`, `swarm_core.py`):
  sempre vietato con `--disallowedTools "Bash(git * --output*)"` (le regole di
  divieto vincono su quelle permesse).
- **Cartelle e branch che si accumulano** (`swarm_project.py`, `swarm_core.py`):
  a fine fase via worktree, cartella della corsa (e `.<progetto>-swarm/` se resta
  vuota) e branch degli agenti con `git branch -d`, che da solo tiene quelli non
  uniti (non finiti, in conflitto). Un branch in conflitto viene tolto dopo che
  l'agente della fase dopo l'ha unito. Il log della fase si chiude prima.
- **Test** (`tests/`): 30 test con `unittest`, senza dipendenze:
  `extract_json`, `validate_plan`, `resolve_project_path`, `swarm_advice`, il
  confronto di `phaseModel`/`phaseBudget` della pagina con Python, e una pipeline
  vera su un repository temporaneo con un finto `claude` (merge, conflitto
  passato e unito, pausa e ripresa, branch dell'utente intatto, pulizia, compito
  su stdin, divieto di `--output`, bus dal thread del loop). Ho controllato che
  il test del thread fallisca togliendo la correzione.
- Provato: test tutti verdi. In demo, con un lanciatore che fa fallire apposta un
  agente di Sviluppo: riga "Branch" nel piano, pagina "In pausa" (desktop scuro,
  375px chiaro, senza scorrimento orizzontale), "Continua con Integrazione",
  pagina finale con la nota sul branch, nessun errore in console.
  Non provato con agenti veri: che `claude -p` legga il compito da stdin (è il
  modo documentato, ma qui l'ho provato solo con il finto) e che la regola con
  `*` in mezzo blocchi davvero `git diff --output=…` (la sintassi è quella
  dell'aiuto della CLI 2.1.318). "Fermati qui" l'ho provato solo nel test.


### Sicurezza, lavoro salvato e costi onesti (7 novembre 2026)

I primi quattro problemi della revisione.

- **DNS rebinding** (`swarm_web.py`): ogni richiesta, anche GET ed `/events`,
  deve avere Host `localhost`, `127.0.0.1` o `[::1]` sulla porta su cui ascolta
  davvero, altrimenti 403. In più le POST vogliono un token casuale che il server
  scrive nella pagina a ogni avvio (`<meta name="swarm-token">`, header
  `X-Swarm-Token`). Un Content-Length non numerico ora dà 400 invece di lasciare
  la connessione appesa.
- **Lavoro perso** (`swarm_core.py`): un agente in errore o fermato fa un commit
  "NON FINITO" sul suo branch (`save_unfinished`), che non viene unito. La fase
  aspetta che ogni agente abbia chiuso e salvato (`Worker.closed`) prima di
  rimuovere i worktree. "Ferma" chiude tutto il gruppo di processi dell'agente
  (`start_new_session` + `killpg`, SIGKILL dopo 5 s): prima un programma lanciato
  dall'agente poteva restare vivo. All'uscita swarm aspetta fino a 8 s per salvare.
- **Costi** (`swarm_core.py`, `swarm_project.py`, `web/index.html`): chi si chiude
  senza l'evento finale della CLI (fermato, crash, anche il pianificatore annullato)
  ha `cost_missing`. In alto il costo diventa "≥ $…" con la spiegazione, la pagina
  finale dice "per almeno $…" e quali agenti mancano; scheda e dettaglio
  dell'agente dicono "costo non noto" e "lavoro salvato".
- **Build e test** (`web/index.html`, `README.md`): avviso sotto l'interruttore
  (con quei comandi un agente esegue qualsiasi programma con i tuoi permessi) e
  README corretto: anche senza, gli agenti possono scrivere nella cartella dello
  sciame, dove ci sono i worktree degli altri.
- Provato: curl sul server demo (Host estraneo su GET e POST, POST senza token o
  con token sbagliato → 403; localhost e 127.0.0.1 con token → 200; Content-Length
  "abc" → 400). Con un finto `claude` (script) su un repo temporaneo: l'agente
  finito fa il commit normale, quello in crash e quello fermato salvano il lavoro
  sul branch, che sopravvive alla rimozione dei worktree; "Ferma" chiude in 0,2 s,
  l'uscita in 0,5 s, nessun processo rimasto. In demo: fermando un agente il costo
  diventa "≥" e la pagina finale lo dice; avviso visibile a desktop e a 375px.
  Non provato con agenti veri: non so con quale evento chiude la CLI quando
  esaurisce il budget, ma il salvataggio vale per qualsiasi chiusura non riuscita.

### Quanti agenti per fase, nel piano (6 novembre 2026)

Nella schermata del piano ora scegli quanti agenti mettere in ogni fase e vedi
cosa cambia rispetto al consiglio.

- `web/index.html`: riquadro **Agenti consigliati** sotto il riepilogo (per ogni
  fase: consigliati, quanti nel piano, perché; pulsanti "Usa i consigliati" e
  "Ridividi i compiti con questi numeri"). In ogni fase un selettore − n + accanto
  a "consigliati N", e sotto una nota con l'effetto della scelta (diverso per
  fondamenta, sviluppo, revisione, integrazione) e la spesa massima della fase con
  la differenza rispetto ai consigliati. Togliendo un agente il suo compito passa a
  quello prima; un agente nuovo senza compito ha il bordo ambra. Il massimo per
  fase resta "Agenti in parallelo" del modulo.
- `swarm_project.py`: `replan(feedback, agents)` aggiunge al feedback il numero di
  agenti per fase da rispettare; in demo il pianificatore finto lo rispetta.
  `swarm_web.py`: `/api/replan` accetta `agents`.
- Corretto anche: ricaricando la pagina sul piano il riquadro restava nascosto
  perché la tabella dei consigli arrivava dopo.
- Provato in demo su desktop (chiaro e scuro) e a 375px. Non provato con il
  pianificatore vero: la ripianificazione con i numeri passa dal feedback testuale,
  quindi Claude potrebbe non rispettarli sempre.

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
