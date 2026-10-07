# hr_shift_planning — kūrimo planas

| | |
|---|---|
| **Specifikacija** | [hr_shift_planning_specs.md](hr_shift_planning_specs.md) |
| **Modulio vieta** | `additional_addons/hr_shift_planning/` |
| **Paskutinis atnaujinimas** | 2026-10-07 |

## Darbo eiga

1. Claude įgyvendina vieną žingsnį ir atnaujina šį failą (būsena, pastabos, nukrypimai nuo plano).
2. Claude pateikia testavimo sąrašą ir siūlomą commit message.
3. Naudotojas ištestuoja. Jei randa klaidų, Claude jas taiso tame pačiame žingsnyje.
4. Naudotojas padaro commit ir duoda signalą pereiti prie kito žingsnio.

Kiekvienas žingsnis baigiasi veikiančia, įdiegiama modulio versija. Kūrimo metu sąsaja testuojama angliškai. Lietuviškas vertimas (`i18n/lt.po`) daromas žingsnyje 8.

## Bendros komandos

Modulio atnaujinimas (po kiekvieno žingsnio):

```bash
python odoo-bin -d <db> -u hr_shift_planning --stop-after-init
```

Python testai:

```bash
python odoo-bin -d <db> -u hr_shift_planning --test-enable --test-tags /hr_shift_planning --stop-after-init
```

JS (Hoot) testai: paleidus serverį, naršyklėje atidaryti `/web/tests` ir filtruoti pagal `hr_shift_planning`.

## Sąsajos pavadinimai

Iki žingsnio 8 sąsaja yra angliška. Testavimo žingsniuose naudojami lietuviški pavadinimai atitinka šiuos angliškus:

| Lietuviškai | Angliškai (sąsajoje) |
|---|---|
| Darbuotojai | Employees |
| Darbo valandos | Working Hours |
| Planavimas | Planning |
| Šablonai | Templates |
| Pamainos (sąrašas) | Shifts (list) |
| Darbo valandos / Peržiūra | Working Hours / Viewer |
| Darbo valandos / Planuotojas | Working Hours / Planner |
| Išsaugoti / Atšaukti | Save / Discard |
| Išvalyti | Clear |
| Kitas laikas | Custom time |
| Šiandien | Today |
| Archyvuoti (filtras) | Archived |
| Baigiasi kitą dieną | Ends Next Day |
| Pertrauka (min.) | Break (minutes) |
| Trukmė | Duration (hours) |
| Konfigūracija → Nustatymai | Configuration → Settings |

## Būsena

| # | Žingsnis | Specifikacija | Būsena |
|---|---|---|---|
| 0 | Pasiruošimas: `hr_employee_calendar_planning` pašalinimas | — | ✅ Baigtas |
| 1 | Karkasas, teisės, šablonai | FR-1 (dalinai), FR-3, FR-5 | ✅ Baigtas |
| 2 | Pamainos modelis | FR-4, FR-5 | ⬜ Neprasidėjęs |
| 3 | Lentelė: peržiūra ir naršymas | FR-1, FR-2.1, FR-2.2, FR-2.5 [M] | ⬜ Neprasidėjęs |
| 4 | Lentelė: redagavimas ir išsaugojimas | FR-2.3 A–B, FR-2.4, FR-5 | ⬜ Neprasidėjęs |
| 5 | Rankinis laikas, valandų suma, filtrai | FR-2.3 C, FR-2.5 [S] | ⬜ Neprasidėjęs |
| 6 | DK įspėjimai | FR-2.6 | ⬜ Neprasidėjęs |
| 7 | Greitinimo funkcijos | FR-2.3 D–E, FR-2.5 [C], FR-2.7 | ⬜ Neprasidėjęs |
| 8 | Vertimai ir galutinis patikrinimas | NFR-1–NFR-9 | ⬜ Neprasidėjęs |

Būsenos: ⬜ Neprasidėjęs · 🔄 Vykdomas · 🧪 Laukia testavimo · ✅ Baigtas

---

## Žingsnis 0 — Pasiruošimas (pasirenkamas)

**Tikslas:** pašalinti `hr_employee_calendar_planning`, nes jis nereikalingas ir prideda sudėtingumo (automatiškai generuojami kalendoriai, draudimas sukurti darbuotoją be grafiko).

**Darbai:**
- Naudotojas išdiegia modulį per Apps → `hr_employee_calendar_planning` → Uninstall.
- Claude pašalina `additional_addons/hr_employee_calendar_planning/` iš repozitorijos.

**Testavimas:**
- [ ] Apps sąraše modulis nebeįdiegtas.
- [ ] Darbuotojo kortelėje vėl matomas standartinis laukas „Working Hours“, nebėra „Calendar planning“ lentelės.
- [ ] Galima sukurti naują darbuotoją, nenurodžius grafiko planavimo eilučių.
- [ ] Esamų darbuotojų „Working Hours“ reikšmė nepasikeitė į tuščią. Jei pasikeitė, priskirti įmonės grafiką.

**Commit message:** `remove hr_employee_calendar_planning (replaced by hr_shift_planning)`

---

## Žingsnis 1 — Karkasas, teisės, šablonai

**Tikslas:** įdiegiamas modulis su teisių grupėmis, meniu ir pilnai veikiančiu šablonų valdymu.

**Apimtis:** FR-1 (meniu „Darbo valandos“ → „Šablonai“), FR-3, FR-5 (grupės ir modelio teisės šablonams).

**Darbai:**
- `__manifest__.py` (priklauso tik nuo `hr`), `__init__.py`.
- Teisių grupės: „Darbo valandos / Peržiūra“ ir „Darbo valandos / Planuotojas“ (Planuotojas paveldi Peržiūrą). Administratoriui priskiriamas Planuotojas.
- Modelis `hr.shift.template`: pavadinimas, kodas, spalva, pradžia, pabaiga, pertrauka, trukmė (apskaičiuojama), aktyvus, įmonė, eiliškumas.
- Ribojimai: kodas 1–3 simboliai, unikalus įmonėje.
- Naktinės pamainos trukmė skaičiuojama teisingai (pabaiga ≤ pradžia reiškia kitą dieną).
- Sąrašo vaizdas (eiliškumas tempiant, spalva), formos vaizdas, paieška su filtru „Archyvuoti“.
- Meniu: Darbuotojai → Darbo valandos → Šablonai.
- `ir.rule`: tik savo įmonės šablonai.
- Python testai: trukmės skaičiavimas (dieninė, naktinė, su pertrauka), kodo unikalumas, teisės.

**Testavimas:**
- [ ] Modulis įdiegiamas per Apps be klaidų.
- [ ] Meniu Darbuotojai → Darbo valandos → Šablonai matomas administratoriui.
- [ ] Sukurti šablonus: Rytinė (R, 06:00–14:00, 30 min. pertrauka), Vakarinė (V, 14:00–22:00, 30 min.), Naktinė (N, 22:00–06:00, 30 min.). Trukmė (Duration) rodo 07:30 visiems trims.
- [ ] Naktinei pažymėta „Baigiasi kitą dieną“ (Ends Next Day), rytinei ir vakarinei — ne.
- [ ] Pakeitus pabaigą, trukmė persiskaičiuoja iš karto.
- [ ] Bandant sukurti antrą šabloną su kodu „R“, rodoma klaida.
- [ ] Kodo lauke negalima įvesti daugiau nei 3 simbolių.
- [ ] Pradžia 25:00 arba pertrauka, ilgesnė už pamainą, neleidžiama.
- [ ] Sąraše šablonų tvarką galima keisti tempiant, spalva matoma.
- [ ] Archyvuotas šablonas dingsta iš sąrašo ir matomas su filtru „Archyvuoti“.
- [ ] Sukurti naudotoją su grupe „Darbo valandos / Peržiūra“: mato šablonus, bet negali jų kurti ar redaguoti.
- [ ] Naudotojas be grupių nemato meniu „Darbo valandos“.
- [ ] Python testai praeina.

**Commit message:** `hr_shift_planning: module skeleton, security groups and shift templates`

---

## Žingsnis 2 — Pamainos modelis

**Tikslas:** pamainų duomenys saugomi ir validuojami. Kol nėra lentelės, pamainas galima peržiūrėti ir redaguoti laikinu sąrašo vaizdu.

**Apimtis:** FR-4, FR-5 (teisės pamainoms).

**Darbai:**
- Modelis `hr.shift`: darbuotojas, data, šablonas (neprivalomas), pradžia ir pabaiga (UTC), pertrauka, trukmė, `is_custom`, įmonė.
- Pradžia ir pabaiga apskaičiuojamos iš šablono ir datos pagal darbuotojo laiko juostą. Naktinės pamainos pabaiga tenka kitai dienai.
- DB ribojimas: viena pamaina vienam darbuotojui per dieną.
- Šablono, naudojamo pamainose, ištrinti negalima (`ondelete="restrict"`).
- Pakeitus šablono laiką, esamos pamainos nesikeičia.
- Laikinas meniu „Darbo valandos → Pamainos (sąrašas)“: redaguojamas sąrašas, grupavimas pagal darbuotoją ir datą. Žingsnyje 3 jį pakeis lentelė.
- `ir.rule`: tik savo įmonės pamainos. Peržiūros grupė tik skaito.
- Python testai: naktinė pamaina, laiko juosta, ribojimas, `is_custom`, šablono keitimas, teisės.

**Testavimas:**
- [ ] Atnaujinus modulį, meniu atsiranda „Pamainos (sąrašas)“.
- [ ] Sukurti pamainą darbuotojui su šablonu „R“: pradžia 06:00, pabaiga 14:00 (rodoma vietos laiku), trukmė 7,5 val.
- [ ] Pamaina su šablonu „N“ datai 10-15: pabaiga rodoma 10-16 06:00.
- [ ] Antra pamaina tam pačiam darbuotojui tą pačią dieną neleidžiama, rodoma klaida.
- [ ] Pakeitus pamainos pabaigą į 12:00, `is_custom` pažymėtas.
- [ ] Pamaina be šablono su rankiniu laiku išsaugoma.
- [ ] Pakeitus šablono „R“ pradžią į 07:00, jau sukurta pamaina lieka 06:00.
- [ ] Bandant ištrinti naudojamą šabloną, rodoma klaida. Archyvuoti galima.
- [ ] Peržiūros grupės naudotojas mato pamainas, bet negali jų keisti.
- [ ] Darbuotojų modulis ir darbuotojo kortelė veikia kaip anksčiau (NFR-4).
- [ ] Python testai praeina.

**Commit message:** `hr_shift_planning: shift model with constraints and access rules`

---

## Žingsnis 3 — Lentelė: peržiūra ir naršymas

**Tikslas:** pagrindinė lentelė rodo visų darbuotojų mėnesio pamainas. Redaguoti dar negalima.

**Apimtis:** FR-1 (meniu „Planavimas“), FR-2.1, FR-2.2, FR-2.5 [M].

**Darbai:**
- Serverio metodas, kuris viena užklausa grąžina mėnesio duomenis: darbuotojus, pamainas, šablonus, valstybines šventes (NFR-5).
- Owl kliento veiksmas (client action): darbuotojų stulpelis kairėje, dienų eilutė viršuje, langeliai su kodu, valandomis ir spalva.
- Sticky darbuotojų stulpelis ir dienų eilutė, langelio aukštis ≥ 60 px (NFR-3).
- Žymėjimas: paspaustas langelis, jo darbuotojas ir diena. Užvedus pelę paryškinama eilutė ir stulpelis.
- Šiandienos, savaitgalių ir švenčių išskyrimas.
- Mėnesio naršymas ◀ / ▶ / „Šiandien“.
- Meniu: Darbuotojai → Darbo valandos → Planavimas (pirmas punktas).
- Hoot testai: atvaizdavimas, žymėjimas, naršymas.

**Testavimas:**
- [ ] Darbuotojai → Darbo valandos → Planavimas atidaro lentelę per 3 paspaudimus (NFR-1).
- [ ] Rodomi visi aktyvūs įmonės darbuotojai, archyvuoti nerodomi.
- [ ] Viršuje matomos visos einamojo mėnesio dienos su savaitės dienomis.
- [ ] Žingsnyje 2 sukurtos pamainos rodomos teisingose vietose, su kodu, valandomis ir šablono spalva.
- [ ] Paspaudus langelį, pažymimas langelis, darbuotojo vardas ir dienos antraštė.
- [ ] Užvedus pelę, eilutė ir stulpelis paryškinami.
- [ ] Šiandiena, savaitgaliai ir valstybinės šventės išskirti.
- [ ] ◀ / ▶ perjungia mėnesį, „Šiandien“ grąžina į einamąjį.
- [ ] Slenkant žemyn ir į šoną, vardai ir dienos lieka matomi.
- [ ] Langelių aukštis ≥ 60 px (naršyklės DevTools).
- [ ] Hoot ir Python testai praeina.

**Commit message:** `hr_shift_planning: read-only monthly planning grid`

---

## Žingsnis 4 — Lentelė: redagavimas ir išsaugojimas

**Tikslas:** pamainos priskiriamas lentelėje ≤ 2 paspaudimais ir išsaugomos vienu kartu.

**Apimtis:** FR-2.3 A–B, FR-2.4, FR-5 (sąsaja pagal grupę).

**Darbai:**
- Teptukų juosta (aktyvūs šablonai pagal eiliškumą) ir mygtukas „Išvalyti“.
- Teptukas: pasirinktas šablonas lieka aktyvus, kol nepasirenkamas kitas arba nepaspaudžiamas dar kartą.
- Iššokantis langas, kai teptukas nepasirinktas.
- Neišsaugoti pakeitimai laikomi naršyklėje ir pažymimi langelyje.
- „Išsaugoti“ (viena serverio užklausa) ir „Atšaukti“.
- Įspėjimas išeinant ar keičiant mėnesį su neišsaugotais pakeitimais.
- Peržiūros grupei teptukų juosta ir redagavimas paslėpti. Serveris taip pat atmeta jų pakeitimus.
- Hoot testai: teptukas, iššokantis langas, išsaugojimas, atšaukimas. Python testas: išsaugojimo metodas ir teisės.

**Testavimas:**
- [ ] Pasirinkus teptuką „R“ ir paspaudus langelį, langelyje atsiranda „R“ (2 paspaudimai, NFR-2).
- [ ] Toliau spaudžiant kitus langelius, kiekvienas užpildomas 1 paspaudimu.
- [ ] „Išvalyti“ teptukas ištrina pamainą langelyje.
- [ ] Be teptuko paspaudus langelį, atsidaro šablonų langas. Pasirinkus šabloną, jis priskiriamas.
- [ ] Pakeisti langeliai pažymėti kaip neišsaugoti.
- [ ] „Išsaugoti“: žymės dingsta, perkrovus puslapį pakeitimai išlieka.
- [ ] „Atšaukti“: pakeitimai atmetami.
- [ ] Su neišsaugotais pakeitimais keičiant mėnesį ar išeinant iš puslapio, rodomas įspėjimas.
- [ ] Priskyrus pamainą langeliui, kuriame jau yra kita, ji pakeičiama (ne dubliuojama).
- [ ] Peržiūros grupės naudotojas mato lentelę, bet teptukų juostos nėra ir paspaudimas nieko nekeičia.
- [ ] Hoot ir Python testai praeina.

**Commit message:** `hr_shift_planning: brush and popover editing with batch save`

---

## Žingsnis 5 — Rankinis laikas, valandų suma, filtrai

**Tikslas:** galima nurodyti nestandartinį laiką, matyti mėnesio valandas ir filtruoti darbuotojus.

**Apimtis:** FR-2.3 C, FR-2.5 [S].

**Darbai:**
- Iššokančiame lange mygtukas „Kitas laikas“ su laukais „Pradžia“, „Pabaiga“, „Pertrauka“.
- Rankinis laikas su šablonu rodomas su žvaigždute („R\*“), be šablono — pilkas langelis su laiku („06–12“). Užvedus pelę rodomas tikslus laikas.
- Teptukas ant rankinio langelio perrašo laiką šablono laiku.
- Stulpelis „Σ val.“ dešinėje, atsinaujina iš karto keičiant (dar neišsaugojus).
- Filtras pagal skyrių ir paieška pagal vardą.
- Hoot testai.

**Testavimas:**
- [ ] Langelis → „Kitas laikas“: laukai atsiranda per 2 paspaudimus.
- [ ] Šablonas „R“ su laiku 06:00–12:00 rodomas kaip „R\*“, užvedus pelę matyti 06:00–12:00.
- [ ] Laikas be šablono rodomas pilkame langelyje kaip „06–12“.
- [ ] Teptukas „R“ ant „R\*“ langelio grąžina 06:00–14:00, žvaigždutė dingsta.
- [ ] „Σ val.“ teisingai sumuoja mėnesio valandas, atsižvelgiant į pertraukas ir rankinius laikus.
- [ ] Suma atsinaujina iš karto, dar neišsaugojus.
- [ ] Filtras pagal skyrių rodo tik to skyriaus darbuotojus.
- [ ] Paieška pagal vardo dalį veikia.
- [ ] Hoot testai praeina.

**Commit message:** `hr_shift_planning: custom shift times, monthly totals and filters`

---

## Žingsnis 6 — DK įspėjimai

**Tikslas:** lentelė įspėja apie darbo kodekso pažeidimus, bet išsaugoti netrukdo.

**Apimtis:** FR-2.6.

**Darbai:**
- Nustatymai (Darbuotojai → Konfigūracija → Nustatymai): minimalus poilsis tarp pamainų (11 val.), maksimali pamaina (12 val.), maksimalios valandos per 7 dienas (48 val.).
- Patikrinimai skaičiuojami naršyklėje iš karto keičiant, įskaitant pamainas, kurios patenka į gretimą mėnesį (paskutinė praėjusio mėnesio diena, pirmos kito mėnesio dienos).
- Geltonas langelio rėmelis su paaiškinimu užvedus pelę.
- Įspėjimų skaičius prie „Išsaugoti“ ir sąrašas jį paspaudus.
- Python testai (ribų skaičiavimas), Hoot testai.

**Testavimas:**
- [ ] Naktinė pamaina (22:00–06:00), o kitą dieną rytinė (06:00): abu langeliai su geltonu rėmeliu, užvedus pelę paaiškinimas apie 11 val. poilsį.
- [ ] Rankinė pamaina 06:00–20:00 (14 val.): įspėjimas apie > 12 val.
- [ ] 7 dienos iš eilės po 7,5 val. (52,5 val.): įspėjimas apie > 48 val.
- [ ] Prie „Išsaugoti“ rodomas įspėjimų skaičius, paspaudus matomas sąrašas.
- [ ] Su įspėjimais išsaugoti galima.
- [ ] Nustatymuose pakeitus ribą (pvz. 12 → 10 val.), įspėjimai persiskaičiuoja.
- [ ] Naktinė pamaina paskutinę mėnesio dieną ir rytinė pirmą kito mėnesio dieną: įspėjimas rodomas.
- [ ] Testai praeina.

**Commit message:** `hr_shift_planning: labour code warnings in planning grid`

---

## Žingsnis 7 — Greitinimo funkcijos

**Tikslas:** didelius kiekius pamainų galima suplanuoti greičiau.

**Apimtis:** FR-2.3 D–E, FR-2.5 [C], FR-2.7.

**Darbai:**
- Tempimas pele: teptukas pritaikomas pažymėtam stačiakampiui (dienos × darbuotojai).
- Klaviatūra: rodyklės, šablono kodo raidė, Delete.
- Apatinė eilutė: kiek žmonių kiekvienai pamainai kiekvieną dieną.
- „Kopijuoti iš praėjusio mėnesio / savaitės“ pasirinktiems darbuotojams (į neišsaugotus pakeitimus, kad būtų galima peržiūrėti prieš išsaugant).
- Hoot testai.

**Testavimas:**
- [ ] Su teptuku „V“ tempiant per 5 dienas ir 3 darbuotojus, užpildomi 15 langelių.
- [ ] Rodyklės juda tarp langelių, žymėjimas seka.
- [ ] Paspaudus „N“ pažymėtame langelyje, priskiriama naktinė. Delete išvalo.
- [ ] Apatinė eilutė rodo teisingus skaičius ir atsinaujina iš karto.
- [ ] „Kopijuoti iš praėjusios savaitės“ užpildo savaitę kaip neišsaugotus pakeitimus. „Atšaukti“ juos atmeta.
- [ ] Kopijuojant mėnesį su skirtingu dienų skaičiumi, papildomos dienos lieka tuščios.
- [ ] Hoot testai praeina.

**Commit message:** `hr_shift_planning: drag selection, keyboard shortcuts and copy`

---

## Žingsnis 8 — Vertimai ir galutinis patikrinimas

**Tikslas:** išversti sąsają į lietuvių kalbą, patvirtinti, kad visi nefunkciniai reikalavimai įvykdyti, ir paruošti modulį naudojimui.

**Apimtis:** NFR-1–NFR-9.

**Darbai:**
- `i18n/hr_shift_planning.pot` ir `i18n/lt.po`: visi modelių, laukų, meniu, vaizdų, klaidų pranešimų ir lentelės (JS) tekstai.
- Greitaveikos testas su 200 darbuotojų ir pilnu mėnesiu (testinių duomenų scenarijus).
- Peržiūrėti, kad modulis neperrašo kitų modulių metodų ir vaizdų (NFR-4). Paleisti `hr` testus su įdiegtu moduliu.
- Patikrinti išdiegimą.
- `ruff check`.
- Atnaujinti specifikaciją, jei kūrimo metu atsirado pakeitimų.

**Testavimas:**
- [ ] Su 200 darbuotojų mėnuo įkeliamas per ≤ 2 s (NFR-5).
- [ ] Langelio paspaudimo atsakas jaučiasi akimirksniu.
- [ ] Lentelė veikia Chrome, Edge ir Firefox; esant < 1280 px pločiui slenkama horizontaliai (NFR-7).
- [ ] Perjungus naudotojo kalbą į lietuvių, visa modulio sąsaja (meniu, šablonai, pamainos, lentelė, įspėjimai, klaidų pranešimai) rodoma lietuviškai, be angliškų likučių (NFR-6).
- [ ] Angliška sąsaja veikia kaip anksčiau.
- [ ] `hr` modulio testai praeina su įdiegtu `hr_shift_planning` (NFR-4).
- [ ] Išdiegus modulį, Darbuotojų modulis veikia kaip anksčiau.
- [ ] Visi Python ir Hoot testai praeina (NFR-9).

**Commit message:** `hr_shift_planning: Lithuanian translation, performance check and final review`

---

## Ateities darbai (ne šio plano apimtis)

- `hr_shift_planning_attendance`: buvimo būsena (raudona / žalia) pagal pamainas, vartelių duomenys į `hr.attendance`, plano ir fakto palyginimas, lankomumo žiniaraštis.

## Pakeitimų žurnalas

| Data | Pakeitimas |
|---|---|
| 2026-10-07 | Sukurtas planas |
| 2026-10-07 | Lietuviškas vertimas perkeltas į žingsnį 8; kūrimo metu testuojama angliškai |
| 2026-10-07 | Žingsnis 0: modulio aplankas pašalintas. Žingsnis 1 įgyvendintas; papildomai pridėtas laukas „Baigiasi kitą dieną“ (`is_overnight`). Testuojama development DB |
| 2026-10-07 | Žingsniai 0 ir 1 ištestuoti rankiniu būdu, visi punktai praėjo. Python testai nepaleisti |
