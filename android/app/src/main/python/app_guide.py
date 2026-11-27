# -*- coding: utf-8 -*-
"""Návod k aplikaci (dlaždice „Návod“ v Nástrojích): co aplikace umí a jak se ovládá.

Každé téma je karta, která otevře text v okně (cíl typu ``info``; listuje se šipkami). Texty popisují
to, co je na obrazovkách opravdu vidět – názvy dlaždic a tlačítek jsou stejné jako v aplikaci, a když
se něco přejmenuje, patří to změnit i tady (hlídá to test).
"""

import app_api as api

# (název karty, krátký popis na kartu – nejvýš 24 znaků, víc se na kartu nevejde –, ikona, text)
TOPICS = (
    ("Ovládání", "Ovladač a nabídka karty", "settings", """\
Aplikace se ovládá ovladačem televize: šipkami se vybírá, OK potvrzuje, Zpět vrací o krok. \
Podržením tlačítka Zpět se z kterékoli obrazovky vrátíte rovnou do hlavní nabídky.

Vlevo je pět sekcí: Oblíbené, Rozkoukané, Objevuj, iVysílání a Nástroje. Nahoře vlevo je lupa (hledání) \
a vedle ní profil, který se právě dívá. Hledat hlasem: v Hledat vyberte tlačítko mikrofonu vedle pole \
a řekněte název. Tlačítko mikrofonu přímo na ovladači si na Google TV bere Asistent Google; \
na ovladačích s tlačítkem Hledat to tlačítko otevře hledání v aplikaci a rovnou poslouchá.

Podržení OK na kartě otevře její nabídku. Podle toho, co je to za kartu, v ní je: Přehrát od začátku, \
Stáhnout na disk, přidat do oblíbených nebo odebrat, Označit jako zhlédnuté, Odebrat z Rozkoukaných \
a Opravit přiřazení.

Štítky na plakátech říkají, kde titul je (Přehraj.to, iVysílání), jestli má český nebo slovenský \
dabing (CZ, SK) a jestli je k mání ve 4K."""),

    ("Hledání", "Filmy, herci, režiséři", "search", """\
Hledání otevřete lupou vlevo nahoře (je na každé obrazovce) nebo dlaždicí Hledat v sekci Objevuj.

Hledá se najednou ve vašich videích, v iVysílání, na Přehraj.to a (s klíčem TMDB) mezi filmy a seriály. \
Stačí i jméno herce nebo režiséra: aplikace nabídne osobu a po otevření její filmy a seriály, které \
jsou k mání.

Naposledy hledané výrazy si aplikace pamatuje a nabídne je znovu.

Když film na Přehraj.to pod českým názvem není, zkuste původní název – nebo na stránce titulu \
tlačítko Další → Hledat ručně."""),

    ("Objevuj: filmy a seriály", "Žánry, roky, novinky", "all", """\
Sekce Objevuj nabízí filmy a seriály podle databáze TMDB. Potřebuje klíč TMDB (je zdarma, zadává se \
v Nastavení).

Dlaždice Filmy a Seriály vedou na výpisy: Vše na Přehraj.to, Nově na Přehraj.to, Novinky, \
Populární, Nejlépe hodnocené, Podle roku, Podle žánru a Československé. Dlaždice Všechny filmy a seriály \
ukáže všechno, co aplikace na Přehraj.to a v iVysílání našla, dohromady.

Výpisy ukazují jen tituly, které opravdu jsou k mání na Přehraj.to nebo v iVysílání. Aplikace to \
ověřuje na pozadí, proto se nový výpis chvíli plní – řádek dole říká, kolik titulů zbývá ověřit. \
Co se jednou ověří, zůstává.

První karta každého výpisu je Seřadit: podle hodnocení, roku nebo oblíbenosti (u novinek podle data \
přidání). Výpis se jen přeskládá, tituly v něm zůstávají tytéž. Na kartě je i počet titulů ve výpisu – \
„zatím“, dokud se ještě ověřuje.

Přehraj.to počet dotazů omezuje. Když jich je moc, ověřování si dá přestávku a pokračuje samo; \
výpis i Diagnostika to napíšou."""),

    ("Stránka filmu a seriálu", "Přehrát, verze, díly", "folder", """\
Po otevření titulu jsou nahoře tlačítka:

Přehrát – pustí nejlepší verzi podle vašich předvoleb; na tlačítku je její jazyk a kvalita a pod \
popisem, o který soubor jde.
Stáhnout na disk – uloží tutéž verzi na disk.
V iVysílání – když je titul i tam.
Upoutávka – přehraje se v aplikaci a po skončení se vrátí sem.
Více o filmu – celý popis, režie, obsazení.
Do oblíbených – přidá titul do sekce Oblíbené.
Další – Hledat ručně a Jazyk a kvalita u tohoto filmu (nebo seriálu): nastavení jen pro tento titul.

Pod tlačítky je řada „Verze na Přehraj.to“: každá karta je jeden soubor s jazykem, kvalitou, velikostí \
a délkou. Kterou pustíte, tu si aplikace u titulu zapamatuje.

Dál jsou řady s filmovou sérií, podobnými tituly a herci. U seriálu jsou tu řady a díly; první karta \
vede na rozkoukaný nebo první nezhlédnutý díl.

Když se zvolená verze nepodaří přehrát, přehrávač sám zkusí další nejlepší."""),

    ("Přehrávač", "Zvuk, titulky, další díl", "history", """\
OK během přehrávání ukáže ovládání: pauza, posun zpět a vpřed, časová osa. Šipkou nahoru se dostanete \
na tři tlačítka vpravo nahoře: zvuková stopa, titulky a přehrát od začátku.

Zvuk – okno se zvukovými stopami (jazyky, ✓ je ta, která hraje). Pod nimi jsou dva řádky:

Výstup zvuku – co se stane s prostorovým zvukem: předat soundbaru, nechat dekódovat aplikaci, \
totéž s tlumenými basy, nebo stereo. Když film hučí nebo duní, zkuste „Prostorový s tlumenými basy“.

Posun zvuku – když zvuk nesedí na ústa. Posouvá se po 25 ms a změna platí hned.

Noční režim – hlasité scény tišeji, tiché hlasitěji a řeč výrazněji, ať se v noci nemusí pořád \
sahat na hlasitost. Zapíná se a vypíná klepnutím a platí pro všechna videa, dokud ho nevypnete.

Titulky – výběr nebo vypnutí; „Velikost a pozadí“ mění jejich vzhled. Tlačítko je u každého videa \
z Přehraj.to, i když video vlastní titulky nemá. „Najít titulky (OpenSubtitles)“ \
najde české a slovenské titulky na OpenSubtitles.com a hned je zapne; příště se přidají samy. Potřebuje \
v Nastavení klíč k rozhraní OpenSubtitles (zdarma po registraci na opensubtitles.com), jméno a heslo \
zvýší počet stažení za den.

Jiný obsah? – když ve videu hraje něco úplně jiného než vybraný film nebo díl. Aplikace si tu verzi \
zapamatuje, k tomuto titulu ji už nenabídne (na stránce titulu je až na konci jako „jiný obsah“) \
a hned pustí jinou verzi.

Co zvolíte u jednoho dílu, platí pro celý seriál.

Rozkoukané video se zeptá, jestli navázat, nebo začít znovu. Po dokoukání dílu se nabídne další \
a po deseti sekundách se pustí sám; přechodem na Zavřít odpočet zastavíte."""),

    ("Jazyk a kvalita", "Dabing, titulky, 4K", "settings:movies", """\
Dlaždice „Jazyk a kvalita přehrávání“ v Nástrojích nastavuje pro celou aplikaci tři věci:

Jazyk – Český nebo slovenský dabing, Originál s titulky, nebo Je mi to jedno. Podle toho se vybírá \
verze a řadí výpisy.

Kvalita – Nejvyšší dostupná, 1080p, 720p, nebo Úsporná (nejmenší soubor).

Nejnižší kvalita – co se vůbec nabízí: Bez omezení, HD a vyšší, Jen Full HD a vyšší, Jen 4K. \
Titul, který je jen v horší kvalitě, se ve výpisech neukáže. Platí pro všechny profily, protože \
seznam dostupných titulů je společný; jazyk a kvalita jsou u každého profilu vlastní.

Jen pro jeden titul: na jeho stránce tlačítko Další → „Jazyk a kvalita u tohoto filmu“ (u seriálu \
platí pro všechny díly). Tytéž tři věci se uloží jen k tomuto titulu; zbytek aplikace se dál řídí \
Nástroji. Tlačítko Další pak nese poznámku „vlastní nastavení“ a volba „Podle nastavení aplikace“ \
vrátí titul zpět.

Přehraj.to ve výpisu označuje soubory jen slovem „HD“, ať jde o 720p nebo 4K. Skutečnou kvalitu \
aplikace pozná až ze stránky videa nebo při přehrání, a pak ji ukazuje přesně."""),

    ("Oblíbené a Rozkoukané", "Co sledujete", "favorites", """\
Oblíbené: titul přidáte tlačítkem „Do oblíbených“ na jeho stránce, nebo podržením OK na kartě. \
Film, který na Přehraj.to zatím není v jazyce a kvalitě, jakou chcete, aplikace hlídá a dá vědět, \
až se objeví.

Rozkoukané: všechno, co jste nedokoukali, s ukazatelem, kde jste skončili. Nahoře je velký panel \
s tím posledním. Klepnutí na kartu pustí film nebo díl rovnou od místa, kde jste skončili; od začátku \
ho pustíte podržením OK → Přehrát od začátku. Každý seriál tu má jednu kartu: rozkoukaný díl, a když \
jste díl dokoukali, rovnou ten další.

Odebrat z Rozkoukaných: podržte OK na kartě a zvolte „Odebrat z Rozkoukaných“. Stejně jde video \
označit jako zhlédnuté nebo nezhlédnuté.

Rozkoukané se ukazuje i na domovské obrazovce televize v řadě „Pokračovat ve sledování“."""),

    ("Stahování na disk", "Hraje i bez internetu", "download", """\
Na stránce titulu je tlačítko „Stáhnout na disk“; u karty je totéž v nabídce (podržení OK). \
U seriálu jde stáhnout celá řada najednou.

Dlaždice Stahování v Nástrojích ukazuje, co je stažené a co se právě stahuje. Na jejím konci jsou \
karty „Kam stahovat“ (výběr disku) a „Přesunout vše na jiný disk“.

Stažený soubor má při přehrání přednost: hraje z disku, hned a bez internetu.

V Nastavení jde zapnout „Mazat stažené po dokoukání“.

Disk naformátovaný jako FAT32 neuloží soubor větší než 4 GB – pro filmy ve vysoké kvalitě je lepší exFAT."""),

    ("iVysílání ČT", "Živě a pořady ČT", "history", """\
Sekce iVysílání nepotřebuje přihlášení. Nabízí živé vysílání s programem a kategorie pořadů; \
v pořadu jsou řady a díly.

Na konci výpisu pořadu je karta „Přidat do oblíbených“: pořad se pak objeví v „Moje pořady“ \
i s počtem nových dílů. Rozkoukané díly jsou v sekci Rozkoukané spolu s ostatními a po dokoukání \
se nabídne další díl.

Některé pořady (hlavně zahraniční) ČT chrání tak, že je tahle aplikace přehrát nesmí. Ty se otevřou \
v oficiální aplikaci iVysílání, pokud ji v televizi máte."""),

    ("Účet Přehraj.to a vaše videa", "Přihlášení a vaše videa", "login", """\
E-mail a heslo k Přehraj.to se zadávají v Nástrojích → Nastavení a přihlášení. S účtem se filmy \
přehrávají v původní kvalitě; bez něj jen v převedených verzích.

Nová videa z účtu si aplikace načítá sama po otevření. „Synchronizovat s účtem“ (tamtéž v Nastavení) \
projde účet celý: přidá, co chybí, odebere, co jste z účtu smazali, a doplní hodnocení a plakáty i tam, \
kde se to dřív nepovedlo.

Když aplikace soubor přiřadí ke špatnému filmu nebo dílu, podržte OK na kartě a zvolte \
„Opravit přiřazení“.

Klíč TMDB (zdarma na themoviedb.org) se zadává tamtéž. Bez něj nefunguje sekce Objevuj."""),

    ("Profily", "Každý má své", "favorites:history", """\
Každý profil má vlastní Rozkoukané, Oblíbené, Moje pořady z iVysílání, předvolby jazyka a kvality \
a naposledy hledané výrazy. Katalog, seznam dostupných titulů (i nejnižší kvalita, podle které se \
sestavuje), stažené soubory a přihlášení jsou společné.

Profil přepnete kliknutím na jeho jméno nahoře na hlavní obrazovce, nebo dlaždicí Profil \
v Nástrojích. Tamtéž se přidává nový: vyberete obrázek a napíšete jméno.

Když je profilů víc, aplikace se po spuštění zeptá, kdo se dívá."""),

    ("Záloha, aktualizace, potíže", "Když něco nefunguje", "sync", """\
Záloha a obnova (Nástroje): uloží katalog, historii, oblíbené, profily, nastavení a seznam \
dostupných titulů do jednoho souboru na disk a umí je vrátit. Heslo k Přehraj.to v záloze není. \
Aby záloha přežila odinstalaci aplikace, zvolte v okně zálohy „Povolit zálohu, která přežije odinstalaci“.

Smazat historii (Nástroje): po potvrzení smaže hledané výrazy a historii přehrávání tohoto profilu \
(Rozkoukané, Naposledy přehrané, značky zhlédnuto, nabízené další díly, iVysílání). Katalog, oblíbené, \
stažená videa a nastavení zůstanou.

Aktualizace: aplikace se na novou verzi zeptá sama po spuštění. Ručně: Nastavení a přihlášení → \
Zkontrolovat aktualizace.

Diagnostika (Nástroje) ukazuje stav účtu, připojení a poslední chyby. Má tři tlačítka:
Zkontrolovat znovu.
Zkouška Přehraj.to – ověří, jestli hledání na Přehraj.to vrací to, co má.
Hlášení – ukáže kód QR. Namiřte na něj fotoaparát telefonu a klepněte na „Napsat e-mail“: otevře \
se nový e-mail s textem hlášení (adresát je e-mail vašeho účtu Přehraj.to), který můžete odeslat, \
přeposlat tomu, kdo aplikaci spravuje, nebo zkopírovat.

Když film nejde přehrát: zkuste jinou verzi z řady „Verze na Přehraj.to“. Když zvuk hučí nebo nesedí \
na ústa: v přehrávači tlačítko Zvuk → Výstup zvuku nebo Posun zvuku. Když výpis hlásí, že Přehraj.to omezuje dotazy: \
stačí počkat, aplikace pokračuje sama."""),
)


def guide(params):
    """Výpis témat návodu: karta = téma, po otevření text v okně."""
    cards = []
    for title, subtitle, icon, text in TOPICS:
        card = api._card(title, {"type": "info", "call": "", "title": title, "params": {"text": text}},
                         subtitle, icon=icon)
        cards.append(card)
    return {"items": cards, "next": None, "title": "Návod"}


def tile():
    """Dlaždice do Nástrojů."""
    return api._card("Návod", api._target("list", "guide", {}, "Návod"), "Co aplikace umí a jak ji ovládat",
                     icon="help")


ACTIONS = {"guide": guide}
