# Pamainų planavimas — naudotojo instrukcija

Ši instrukcija skirta darbuotojų pamainas planuojantiems vadovams ir planą peržiūrintiems kolegoms.

Instrukcijoje naudojami lietuviški sąsajos pavadinimai. Jei jūsų Odoo kalba angliška, atitikmenys yra [skyriaus pabaigoje](#sąsajos-pavadinimai-lt--en).

---

## Turinys

1. [Kas tai ir kur rasti](#1-kas-tai-ir-kur-rasti)
2. [Lentelės apžvalga](#2-lentelės-apžvalga)
3. [Pamainų šablonai](#3-pamainų-šablonai)
4. [Kaip suplanuoti pamainas](#4-kaip-suplanuoti-pamainas)
5. [Išsaugojimas ir atšaukimas](#5-išsaugojimas-ir-atšaukimas)
6. [Kopijavimas](#6-kopijavimas)
7. [Darbo kodekso įspėjimai](#7-darbo-kodekso-įspėjimai)
8. [Dažnos situacijos](#8-dažnos-situacijos)
9. [Klaviatūros atmintinė](#9-klaviatūros-atmintinė)
10. [Dažni klausimai](#10-dažni-klausimai)

---

## 1. Kas tai ir kur rasti

Pamainų planavimas — tai mėnesio lentelė: kairėje darbuotojai, viršuje mėnesio dienos, langeliuose — kas kurią dieną kokią pamainą dirba. Pamainos priskiriamos iš paruoštų šablonų (pvz. **RYT** 06:00–14:00, **VAK** 14:00–22:00, **NAK** 22:00–06:00) — dažniausiai vienu paspaudimu.

**Kaip atidaryti:** Darbuotojai → **Darbo valandos** → **Planavimas**

| Jūsų teisės | Ką galite |
|---|---|
| **Peržiūra** | Matyti planą, valandas, įspėjimus. Keisti negalite. |
| **Planuotojas** | Planuoti ir keisti pamainas, kopijuoti, tvarkyti šablonus. |

Jei meniu „Darbo valandos“ nematote — kreipkitės į sistemos administratorių.

---

## 2. Lentelės apžvalga

```
 [Išsaugoti (3)] [Atmesti] [Kopijuoti ▾] [⚠ 2]     [RYT][VAK][NAK][Išvalyti]      Spalis 2026 [◀][Šiandien][▶]
 ┌──────────────────────┬─────┬─────┬─────┬─────║─────┬─ ─ ─┬────────┐
 │ Ieškoti darbuotojo...│  1  │  2  │  3  │  4  ║  5  │     │ Σ val. │
 │ [Visi skyriai     ▾] │ kt  │ pn  │ št  │ sk  ║ pr  │     │        │
 ├──────────────────────┼─────┼─────┼─────┼─────║─────┼─ ─ ─┼────────┤
 │ Jonaitis             │ RYT │ RYT │     │     ║ VAK │     │  120h  │
 │ Gamyba               │ 7.5h│ 7.5h│     │     ║ 7.5h│     │        │
 ├──────────────────────┼─────┼─────┼─────┼─────║─────┼─ ─ ─┼────────┤
 │                  RYT │  5  │  4  │     │     ║  3  │     │        │  ← kiek žmonių dirba
 │                  VAK │  4  │  4  │     │     ║  5  │     │        │     kiekvieną pamainą
 └──────────────────────┴─────┴─────┴─────┴─────║─────┴─ ─ ─┴────────┘
                                                 ↑ savaitės pradžia (pirmadienis)
```

### Viršutinė juosta

| Vieta | Kas ten yra |
|---|---|
| Kairėje | **Išsaugoti** (skliausteliuose — kiek pakeitimų), **Atmesti**, **Kopijuoti**, geltonas **⚠** mygtukas su įspėjimų skaičiumi |
| Viduryje | **Teptukai** — šablonų mygtukai ir **Išvalyti** |
| Dešinėje | Mėnuo ir naršymas: ◀ ankstesnis, **Šiandien**, ▶ kitas |

### Ką rodo langeliai

| Langelis | Reikšmė |
|---|---|
| **RYT** / 7.5h, šablono spalva | Pamaina pagal šabloną, valandos be pertraukos |
| **RYT\*** | Šablonas, bet laikas pakeistas (pvz. trumpesnė diena) |
| Pilkas **06-12** | Laikas be šablono |
| Tuščias | Laisva diena |
| Oranžinis taškelis kampe | Pakeitimas dar **neišsaugotas** |
| Geltonas rėmelis | Darbo kodekso įspėjimas (žr. [7 skyrių](#7-darbo-kodekso-įspėjimai)) |
| Mėlynas rėmelis | Pažymėtas langelis; kartu paryškinamas darbuotojas ir diena |

Užvedus pelę ant langelio matysite šablono pavadinimą, tikslų laiką, pertrauką ir įspėjimus.

### Dienos

- **Šiandiena** — spalvota antraštė.
- **Savaitgaliai** — pilkesni.
- **Valstybinės šventės** — gelsvos; užvedus pelę ant dienos antraštės matomas šventės pavadinimas.
- **Savaitės** atskirtos ryškesne linija prieš kiekvieną pirmadienį.

### Valandos ir skaičiai

- **Σ val.** (dešinėje) — kiek valandų darbuotojui suplanuota šį mėnesį (be pertraukų).
- **Apatinės eilutės** — kiek žmonių kiekvieną dieną dirba kiekvieną pamainą. Patogu patikrinti, ar užtenka žmonių.

Abu skaičiai atsinaujina iš karto, dar prieš išsaugant.

### Paieška ir skyriai

Kairiajame viršutiniame kampe:
- **Ieškoti darbuotojo...** — įveskite vardo dalį. Nebūtina rašyti lietuviškų raidžių: „cinkev“ ras „Cinkevičius“.
- **Visi skyriai** — pasirinkite skyrių, kad matytumėte tik jo darbuotojus.

---

## 3. Pamainų šablonai

*(tik Planuotojams)*

Šablonas — dažnai naudojama pamaina: pavadinimas, trumpas kodas, spalva ir laikas.

**Kaip sukurti:** Darbuotojai → Darbo valandos → **Šablonai** → **Naujas**

| Laukas | Ką įvesti |
|---|---|
| Pavadinimas | Pvz. „Rytinė“ |
| Kodas | 1–3 raidės, rodomos lentelėje, pvz. „RYT“. **Patarimas:** kai kodų pirmosios raidės skiriasi (R, V, N), pamainas galima rašyti klaviatūra. |
| Spalva | Langelio fono spalva |
| Pradžia / Pabaiga | Pvz. 06:00 ir 14:00. Jei pabaiga ankstesnė už pradžią (22:00–06:00), pamaina baigiasi kitą dieną — tai naktinė pamaina. |
| Pertrauka (min.) | Pvz. 30. Atimama iš darbo valandų. |
| Trukmė | Apskaičiuojama automatiškai |

Šablonų sąraše tempdami eilutę už ⋮⋮ galite keisti, kokia tvarka teptukai rodomi lentelėje.

**Nebereikalingas šablonas** — archyvuokite (Veiksmai → Archyvuoti). Jo nebebus tarp teptukų, bet jau suplanuotos pamainos liks.

> **Svarbu:** pakeitus šablono laiką, jau suplanuotos pamainos **nesikeičia** — jos rodomos su žvaigždute (pvz. „RYT\*“). Jei norite jas atnaujinti, uždėkite ant jų tą patį šabloną teptuku ir išsaugokite.

---

## 4. Kaip suplanuoti pamainas

*(tik Planuotojams)*

### 4.1. Teptukas — greičiausias būdas

1. Viršuje paspauskite šabloną, pvz. **RYT**. Mygtukas gauna rėmelį — teptukas pasirinktas.
2. Spauskite langelius — kiekvienas paspaudimas įrašo RYT.
3. Norėdami pakeisti pamainą — pasirinkite kitą teptuką. Norėdami baigti — paspauskite tą patį teptuką dar kartą.

Teptukas **Išvalyti** pašalina pamainą iš langelio.

### 4.2. Tempimas — daug langelių vienu kartu

1. Pasirinkite teptuką.
2. Nuspauskite pelę ant pirmo langelio ir **tempkite** — pažymimas stačiakampis (kelios dienos ir / ar keli darbuotojai).
3. Atleiskite pelę — visi pažymėti langeliai užpildomi.

Pavyzdys: su teptuku **VAK** nutempus nuo pirmadienio iki penktadienio per tris darbuotojus, vienu judesiu užpildomos 15 pamainų.

### 4.3. Pasirinkimas langelyje

Kai teptukas **nepasirinktas**, paspaudus langelį atsiveria sąrašas:
- šablonai — paspauskite norimą;
- **Išvalyti** — pašalinti pamainą;
- **Kitas laikas** — nestandartinis laikas (žr. toliau).

Sąrašą uždaro paspaudimas šalia arba **Esc**.

### 4.4. Kitas laikas (nestandartinė pamaina)

Kai darbuotojas dirba ne pagal šabloną, pvz. tik iki 12:00:

1. Paspauskite langelį (be teptuko) → **Kitas laikas**.
2. Užpildykite:
   - **Šablonas** — pasirinkite, jei tai to šablono pamaina pakeistu laiku (langelyje bus „RYT\*“), arba palikite **Nėra** (langelis bus pilkas, pvz. „06-12“). Pasirinkus šabloną, laikas užsipildo pagal jį — belieka pataisyti.
   - **Pradžia** ir **Pabaiga** — 24 valandų formatu.
   - **Pertrauka (min.)**.
3. **Pritaikyti** (arba Enter).

Laiką galima įvesti trumpai:

| Įvedate | Gaunate |
|---|---|
| `6` | 06:00 |
| `630` arba `6.30` | 06:30 |
| `1400` | 14:00 |

Naktinis laikas (pvz. 20:00–02:30) leidžiamas — pamaina baigsis kitą dieną.

### 4.5. Klaviatūra

Pažymėkite langelį (paspauskite jį ir, jei atsivėrė sąrašas, uždarykite su **Esc**). Toliau:

- **raidė** (pvz. **R**) — įrašo šabloną, kurio kodas prasideda ta raide, ir **pereina į kitą dieną**;
- **Delete** — išvalo ir pereina į kitą dieną;
- **rodyklės** — judėti po lentelę;
- **Enter** — atidaro šablonų sąrašą.

Taip savaitę galima surašyti greitai: pažymėkite pirmadienį ir spauskite `R R R R R`.

---

## 5. Išsaugojimas ir atšaukimas

Pakeitimai **neišsaugomi automatiškai** — iki išsaugojimo jie matomi tik jums (oranžinis taškelis langelio kampe).

- **Išsaugoti** — išsaugo visus pakeitimus vienu kartu. Skaičius skliausteliuose rodo, kiek langelių pakeista.
- **Atmesti** — atmeta visus neišsaugotus pakeitimus.

Jei grąžinate langeliui buvusią reikšmę, pakeitimas automatiškai nebeskaičiuojamas.

Jei turite neišsaugotų pakeitimų ir bandote pereiti į kitą mėnesį, kitą meniu ar uždaryti naršyklės skirtuką — sistema paklaus:
- **Likti** — grįžti ir išsaugoti;
- **Atmesti** — tęsti be pakeitimų.

> Išsaugotos pamainos iš karto matomos visiems, kurie turi prieigą prie planavimo.

---

## 6. Kopijavimas

*(tik Planuotojams)*

Mygtukas **Kopijuoti** viršuje kairėje. Kopijuojama tik **matomiems** darbuotojams — jei įjungtas skyriaus filtras ar paieška, kitų darbuotojų planas nepaliečiamas.

Nukopijuotos pamainos tampa **neišsaugotais pakeitimais**: peržiūrėkite, pataisykite ir spauskite **Išsaugoti** (arba **Atmesti**, jei persigalvojote).

### Praėjusi savaitė → pažymėtos dienos savaitė

1. Paspauskite bet kurį langelį savaitėje, kurią norite užpildyti.
2. **Kopijuoti** → **Praėjusi savaitė → pažymėtos dienos savaitė** → patvirtinkite.

Visa savaitė (pirmadienis–sekmadienis) tampa tokia pat kaip ankstesnė, **įskaitant laisvas dienas**.

### Praėjęs mėnuo → šis mėnuo

**Kopijuoti** → **Praėjęs mėnuo → šis mėnuo** → patvirtinkite.

Kopijuojama pagal datą: 1-a diena į 1-ą, 2-a į 2-ą ir t. t. Dienos, kurių praėjusiame mėnesyje nebuvo (pvz. 31-a), nekeičiamos.

> Kopijuojant pagal datą savaitės dienos pasislenka (spalio 1-a — ketvirtadienis, lapkričio 1-a — sekmadienis). Slenkantiems grafikams dažniausiai patogiau kopijuoti po savaitę.

---

## 7. Darbo kodekso įspėjimai

Lentelė įspėja, kai planas viršija darbo kodekso ribas. Įspėjimai **netrukdo išsaugoti** — sprendimą priimate jūs.

| Įspėjimas | Kada |
|---|---|
| **Tik X val. poilsio tarp pamainų** | Tarp pamainų mažiau nei 11 val. (pvz. naktinė, o kitą rytą — rytinė). Pažymimi abu langeliai. |
| **X val. pamaina** | Pamaina ilgesnė nei 12 val. (be pertraukos). |
| **X val. per 7 dienas iki šios dienos** | Per 7 dienas iš eilės suplanuota daugiau nei 48 val. Pažymima diena, kurią riba viršijama. |

- Langelis su įspėjimu turi **geltoną rėmelį**; užvedus pelę matomas paaiškinimas.
- Geltonas **⚠** mygtukas viršuje rodo įspėjimų skaičių. Paspaudus — sąrašas; paspaudus įrašą, lentelė nušoka į tą langelį.
- Tikrinamos ir gretimų mėnesių dienos, todėl įspėjimai teisingi ir mėnesio pradžioje bei pabaigoje.

Ribas (11 / 12 / 48 val.) nustato administratorius.

---

## 8. Dažnos situacijos

**Suplanuoti naują mėnesį pagal slenkantį grafiką**
1. Pereikite į mėnesį (▶).
2. Užpildykite pirmą savaitę teptukais / tempimu.
3. Pažymėkite langelį antroje savaitėje → **Kopijuoti** → **Praėjusi savaitė…** Pakartokite kitoms savaitėms ir pataisykite tuos darbuotojus, kurių pamaina keičiasi.
4. Patikrinkite **⚠** įspėjimus ir apatines eilutes (ar užtenka žmonių).
5. **Išsaugoti**.

**Perkelti darbuotoją iš rytinės į naktinę nuo tam tikros dienos**
1. Pasirinkite teptuką **NAK**.
2. Nutempkite per to darbuotojo dienas nuo pasikeitimo.
3. Atkreipkite dėmesį į įspėjimą apie poilsį tarp paskutinės rytinės ir pirmos naktinės.
4. **Išsaugoti**.

**Darbuotojas vieną dieną dirba trumpiau**
Paspauskite langelį → **Kitas laikas** → palikite šabloną, pakeiskite pabaigą → **Pritaikyti** → **Išsaugoti**.

**Darbuotojas serga / atostogauja**
Teptuku **Išvalyti** pašalinkite tų dienų pamainas → **Išsaugoti**.

**Planuoti tik vieno skyriaus darbuotojams**
Kampe pasirinkite skyrių. Lentelė, valandų sumos, apatinės eilutės ir kopijavimas veiks tik su to skyriaus darbuotojais.

---

## 9. Klaviatūros atmintinė

*(kai pažymėtas langelis)*

| Klavišas | Veiksmas |
|---|---|
| ← → ↑ ↓ | Judėti po lentelę |
| Raidė (R, V, N…) | Įrašyti šabloną ir pereiti į kitą dieną |
| Ta pati raidė dar kartą | Kitas šablonas, prasidedantis ta raide (jei toks yra) |
| Delete / Backspace | Išvalyti ir pereiti į kitą dieną |
| Enter | Atidaryti šablonų sąrašą |
| Esc | Uždaryti sąrašą |
| Enter (Kitas laikas formoje) | Pritaikyti |

---

## 10. Dažni klausimai

**Nematau teptukų ir mygtuko „Išsaugoti“.**
Turite tik peržiūros teises. Kreipkitės į administratorių, jei reikia planuoti.

**Pakeičiau pamainas, bet kolega jų nemato.**
Pakeitimai neišsaugoti — ieškokite oranžinių taškelių ir spauskite **Išsaugoti**.

**Kodėl pamaina rodoma su žvaigždute (RYT\*)?**
Jos laikas skiriasi nuo šablono — arba buvo pakeistas rankiniu būdu, arba vėliau pakeistas pats šablonas. Teptukas su tuo pačiu šablonu grąžina šablono laiką.

**Tarp teptukų nėra reikiamo šablono.**
Jis archyvuotas arba dar nesukurtas — žr. [3 skyrių](#3-pamainų-šablonai).

**Laikai rodomi pasislinkę 1–3 valandomis.**
Darbuotojo kortelėje nurodyta kita laiko juosta nei jūsų kompiuteryje. Kreipkitės į administratorių.

**Klaviatūra nieko nedaro.**
Pažymėkite langelį ir įsitikinkite, kad žymeklis nėra paieškos lauke ar formoje (paspauskite ant lentelės langelio).

**Ar galiu išsaugoti planą su įspėjimais?**
Taip. Įspėjimai tik informuoja.

**Ar galima vienam darbuotojui suplanuoti dvi pamainas per dieną?**
Ne — viena pamaina per dieną. Jei reikia ilgesnio darbo, naudokite **Kitas laikas**.

**Naktinė pamaina — kuriai dienai ji priklauso?**
Tai dienai, kurią **prasideda**. Pvz. NAK 22:00–06:00 pirmadienį įrašoma į pirmadienio langelį.

---

## Sąsajos pavadinimai (LT → EN)

| Lietuviškai | Angliškai |
|---|---|
| Darbuotojai → Darbo valandos | Employees → Working Hours |
| Planavimas / Šablonai | Planning / Templates |
| Peržiūra / Planuotojas | Viewer / Planner |
| Išsaugoti / Atmesti / Likti | Save / Discard / Stay |
| Kopijuoti | Copy |
| Išvalyti | Clear |
| Kitas laikas / Pritaikyti | Custom time / Apply |
| Šiandien | Today |
| Ieškoti darbuotojo... / Visi skyriai | Search employee... / All departments |
| Σ val. | Σ h |
| Praėjusi savaitė → pažymėtos dienos savaitė | Previous week → week of the selected day |
| Praėjęs mėnuo → šis mėnuo | Previous month → this month |
| Darbo kodekso įspėjimai | Labour code warnings |
