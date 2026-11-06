# swarm

Crea progetti con più agenti Claude Code che lavorano in parallelo. Parti da
un'idea: un pianificatore la divide in fasi e compiti, tu rivedi il piano, poi
gli agenti lavorano, ognuno nel suo git worktree, mentre li guardi in una
dashboard dal vivo e puoi scrivergli.

## Installazione (una volta)

```bash
python3 -m venv .venv
.venv/bin/pip install textual
```

Serve anche Claude Code installato e con il login fatto. Per controllare:

```bash
claude -p "rispondi solo: ok"
```

Se risponde con un errore di login, apri `claude` nel terminale e fai `/login`.

## Creare un progetto

```bash
.venv/bin/python swarm.py
```

Si apre il browser su http://localhost:8765 (o sulla prima porta libera dopo).

1. **L'idea**: descrivi cosa vuoi costruire e scegli la cartella (es.
   `~/Progetti/app-meteo`). Se non esiste viene creata con un repository git.
   Se è già un progetto git, il pianificatore lo legge e ci lavora sopra.
   Nel riquadro **Consiglio** scegli quanto è difficile il progetto
   (semplice / media / complessa) e quanto vuoi curato il risultato
   (prototipo / solida / rifinita): swarm ti suggerisce per ogni fase quanti
   agenti e che effort usare, più modello, spesa per agente e tetto di spesa,
   e li applica al modulo. Con "rifinita" aggiunge una fase di Revisione
   (test + rilettura del codice) prima dell'integrazione.
   Sotto la matrice, **Cosa ottengo in ogni casella?** prende l'idea che hai
   scritto e mostra per ognuna delle 9 caselle un esempio concreto del
   risultato: un titolo dentro la casella e, cliccandola, cosa ottieni, cosa
   non aspettarti e quanto è curato. Lo prepara Claude Haiku con effort basso,
   senza strumenti, in 10-20 secondi e per meno di un centesimo; la richiesta
   si rifà solo se cambi l'idea. In demo gli esempi sono finti e gratis.
   Puoi comunque cambiare modello, spesa massima per agente, quanti agenti in
   parallelo e se possono lanciare comandi di build/test.
   Se scegli Opus, swarm lo usa solo per Fondamenta e Integrazione, dove si
   decide la struttura e si ricuce tutto. Sviluppo e Revisione usano Sonnet,
   che basta per compiti già definiti, e per quegli agenti la spesa massima
   scende in proporzione (con Opus a $10, gli agenti su Sonnet hanno $4).
2. **Il piano**: il pianificatore propone di solito tre fasi.
   - *Fondamenta* (1 agente): struttura del progetto e un file
     `INTERFACCE.md` che descrive come si parlano i pezzi.
   - *Sviluppo* (più agenti in parallelo): ognuno su file diversi.
   - *Integrazione* (1 agente): collega tutto e scrive come avviarlo.
   Accanto a ogni fase ci sono il suo modello, il suo effort (quanto ragionano
   gli agenti: basso, medio, alto, molto alto, massimo) e quanti agenti sono
   consigliati. Puoi cambiare il modello di ogni fase: la spesa massima in
   alto si aggiorna.
   Puoi cambiare ogni compito, aggiungere o togliere agenti, oppure scrivere
   cosa non ti convince e premere "Rifai il piano".
3. **Avvia**: le fasi partono una dopo l'altra. A fine fase swarm unisce i
   branch degli agenti nel tuo progetto, così la fase dopo parte dal lavoro
   fatto. Se due agenti vanno in conflitto, il merge viene annullato e il
   branch viene passato all'agente della fase successiva, che lo unisce a mano.
4. **Mentre lavorano**:
   - clic su un agente: vedi compito, piano spuntato dal vivo, costo e ultimo
     messaggio; puoi **scrivergli** o fermarlo
   - in basso nel bus: **scrivi a tutti** gli agenti insieme
   - gli agenti leggono i messaggi dopo ogni passo del loro piano, non a metà
5. **Fine**: vedi le fasi, i costi e i file cambiati. Il progetto è nella
   cartella scelta; ogni agente ha lasciato il suo branch `swarm/…`, quindi
   con git puoi sempre tornare indietro.

`Ctrl-C` nel terminale ferma tutto ed esce.

### Provarlo senza spendere niente

```bash
.venv/bin/python swarm.py --web --demo
```

Stesso flusso con pianificatore e agenti finti: non crea niente su disco.

## Altri modi d'uso

- `swarm.py config.json --web`: esegue un piano scritto a mano (vedi
  `swarm.example.json`) in una sola fase, con la dashboard nel browser.
- `swarm.py config.json`: come sopra, ma con la dashboard nel terminale.
- `swarm.py --demo`: demo nel terminale (`1`–`8` dettagli, `x` ferma, `q` esci).

## File

- `swarm.py`: comando principale e dashboard da terminale
- `swarm_core.py`: il motore di una fase (agenti, eventi, messaggi, commit)
- `swarm_project.py`: pianificatore e pipeline a fasi con i merge
- `swarm_advice.py`: i consigli su agenti, effort e modello
- `swarm_examples.py`: gli esempi di risultato per ogni casella della matrice
- `swarm_web.py`: server web locale (solo libreria standard)
- `web/index.html`: la pagina

## Sicurezza e costi

- La dashboard ascolta solo su `localhost` e accetta comandi solo dalla
  pagina stessa. La cartella del progetto deve stare nella tua home.
- Gli agenti girano con `acceptEdits`: modificano file solo nel loro worktree.
  Possono lanciare solo comandi git di lettura e, se lo scegli, i comandi di
  build/test.
- La spesa massima vale per ogni agente: con 5 agenti a $2 il tetto è $10
  (più pochi centesimi per il pianificatore e meno di un centesimo per gli
  esempi della matrice). Una fase con un modello più economico di quello del
  progetto ha un tetto più basso, mai più alto. Con un abbonamento Claude il
  costo mostrato è una stima e i limiti d'uso si consumano più in fretta.
