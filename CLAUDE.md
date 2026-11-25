Pravidla projektu (spolupráce více autorů, kontroly, vydávání, co neměnit) jsou v souboru AGENTS.md:

@AGENTS.md

## GitHub Actions – limit 2 000 minut měsíčně

Účet vlastníka má 2 000 minut GitHub Actions měsíčně pro všechny repozitáře dohromady. Pravidlo platí
trvale, pro každou relaci:

1. **Kontroly běží šetrně.** Jen u pull requestu do `main` (případně po sloučení do `main`), nikdy při
   každém odeslání libovolné větve. Po sloučení se znovu nekontroluje obsah, který už prošel
   v pull requestu (stejný strom souborů). Každé workflow má `timeout-minutes` a `concurrency`
   s `cancel-in-progress`. Změny jen v poznámkách (`CLAUDE.md`, `AGENTS.md`, `UKOLY.md`, `README.md`
   a další `*.md`) kontrolu nespouštějí.
2. **Bez souhlasu vlastníka nepřidávat** nové workflow, plánované běhy (`schedule`), matice ani
   opakování. V popisu pull requestu, který se workflow dotýká, vždy uvést odhad spotřeby minut za měsíc.
3. **Neúspěšný běh opakovat nejvýš jednou** a jen kvůli výpadku sítě nebo serveru. Nikdy prázdné
   commity ani opakované spouštění, aby kontrola „prošla“.
4. **Testovat hlavně v relaci před odesláním** (příkazy v `AGENTS.md`). GitHub je jen pojistka.
5. **Změny workflow** jen ve vlastní větvi přes pull request; do `main` je slučuje vlastník, nebo
   se slučují na jeho pokyn.

Jak je to nastavené teď (při změně workflow tento odstavec upravte):

- `tests.yml` – pull request do `main` a odeslání do `main`; jedna úloha s Pythonem 3.11 i 3.13
  (každá úloha se účtuje nejméně jednou minutou, proto ne matice).
- `android.yml` – pull request do `main` a odeslání do `main`, jen když se změnila aplikace, sdílená
  logika, testy nebo workflow samo. Na `main` vydává aplikaci; běh na `main` se proto jako jediný
  neruší novějším odesláním (přerušené nahrávání by nechalo vydání bez souboru).
- `.github/scripts/overeno-v-pr.sh` – po sloučení zjistí, jestli stejný strom souborů už prošel
  v pull requestu. Pak se testy přeskočí, a když navíc není co vydat, i sestavení aplikace.
- `windows.yml` – přenosná verze pro Windows (souhlas vlastníka 25. 11. 2026): pull request do `main`
  a odeslání do `main`, jen při změně ve `windows/` nebo v souboru workflow. Běží na Windows (počítá se
  dvojnásob): jeden běh asi 5 minut = 10 minut; po sloučení se nic nesestavuje, když stejný strom prošel
  v pull requestu a vydání `win-v<verze>` už existuje.
- Odeslání pracovní větve (`claude/…`, `codex/…`) nespouští nic: co má GitHub zkontrolovat, musí mít
  otevřený pull request do `main`.

Pro orientaci: repozitář je veřejný a běhy na standardních strojích se u veřejných repozitářů do limitu
nepočítají (GitHub u nich vykazuje 0 účtovaných minut). Pravidlo platí i tak – kdyby se repozitář
změnil na soukromý, počítal by se každý běh. Jeden běh `tests.yml` odpovídá 1 minutě, `android.yml`
se sestavením 3 minutám; sestavení stránek GitHub Pages po každé změně `main` (spravuje GitHub,
hostuje Kodi repozitář) 1 minutě.
