# Darbuotojų darbo laiko planavimas — reikalavimų specifikacija

| | |
|---|---|
| **Modulis** | `hr_shift_planning` |
| **Odoo versija** | 19.0 Community |
| **Būsena** | Įgyvendinta (19.0.1.0.0, 2026-10-08) |
| **Data** | 2026-10-07 |

Prioritetai: **M** — privaloma, **S** — pageidautina, **C** — būtų gerai turėti.

---

## 0. Apimtis

**Tikslas:** leisti vadovui greitai suplanuoti mėnesio pamainas visiems darbuotojams vienoje lentelėje, naudojant iš anksto paruoštus darbo laiko šablonus.

**Kontekstas:** darbuotojai dirba rytinėmis, vakarinėmis ir naktinėmis pamainomis, N dienų per savaitę, slenkančiu grafiku, ir gali būti perkeliami iš vienos pamainos į kitą. Standartinis Odoo `resource.calendar` palaiko tik 1 arba 2 savaičių ciklą, todėl tokiems grafikams netinka. Planuojama saugoti vieną įrašą vienai darbuotojo pamainai konkrečią dieną.

**Moduliai:**

| Modulis | Paskirtis | Etapas |
|---|---|---|
| `hr_shift_planning` | Šablonai, pamainos, planavimo lentelė. Priklauso tik nuo `hr`. | 1 |
| `hr_shift_planning_attendance` | Raudona / žalia būsena pagal pamainas, plano ir fakto palyginimas, vartelių integracija | 2 (vėliau) |

**Ne šio etapo apimtis:** faktinio lankomumo fiksavimas, vartelių integracija, atlyginimų skaičiavimas, darbuotojų savitarna.

**Pastaba:** `hr_employee_calendar_planning` (OCA) nebereikalingas. Rekomenduojama jį išdiegti, kol nesukaupta duomenų. Naujas modulis nuo jo nepriklauso.

## 1. Sąvokos

| Sąvoka | Reikšmė |
|---|---|
| **Šablonas** | Pakartotinai naudojamas darbo laikas, pvz. „Rytinė 06:00–14:00“ |
| **Pamaina** | Konkretaus darbuotojo darbo laikas konkrečią dieną (šablono pritaikymas arba rankinis laikas) |
| **Lentelė** | Pagrindinis planavimo vaizdas: darbuotojai × mėnesio dienos |
| **Teptukas** | Lentelėje pasirinktas šablonas, kuris pritaikomas spaudžiant langelius |

## 2. Funkciniai reikalavimai

### FR-1 Meniu [M]

Programoje „Darbuotojai“ atsiranda naujas viršutinio meniu punktas **„Darbo valandos“** su dviem papunkčiais:

- **„Planavimas“** atidaro lentelę (FR-2),
- **„Šablonai“** atidaro šablonų valdymą (FR-3).

Meniu matomas tik naudotojams, turintiems teises (FR-5).

**Priėmimo kriterijus:** iš pagrindinio Odoo ekrano iki lentelės galima nueiti 3 paspaudimais: Darbuotojai → Darbo valandos → Planavimas.

### FR-2 Planavimo lentelė [M]

#### FR-2.1 Struktūra

- Kairėje fiksuotas stulpelis su darbuotojais: vardas ir pavardė, po jais skyrius arba pareigos smulkesniu šriftu.
- Viršuje fiksuota eilutė su visomis einamojo mėnesio dienomis: data ir savaitės diena (Pr, An …).
- Kiekviename langelyje rodoma pamaina: šablono **kodas** (pvz. „R“) ir valandų skaičius, fonas nuspalvintas šablono spalva. Jei pamainos nėra, langelis tuščias.
- Slenkant lentelę darbuotojų stulpelis ir dienų eilutė lieka matomi (sticky).
- Rodomi tik aktyvūs darbuotojai iš pasirinktos įmonės.

#### FR-2.2 Žymėjimas (highlight)

- Paspaudus langelį, pažymimas pats langelis, **darbuotojo vardas kairėje** ir **dienos antraštė viršuje**.
- Užvedus pelę, eilutė ir stulpelis paryškinami silpnesniu tonu, kad būtų lengva sekti akimis.
- Šiandienos stulpelis išskirtas. Savaitgaliai ir valstybinės šventės turi kitokį foną.
- **[S]** Savaitės vizualiai atskirtos: prieš kiekvieną pirmadienį (savaitė prasideda pirmadienį) — ryškesnė vertikali linija per visą lentelės aukštį.

#### FR-2.3 Darbo laiko priskyrimas

**A. Teptukas (pagrindinis būdas) [M]**
Viršuje yra šablonų juosta (spalvoti mygtukai su kodu) ir mygtukas „Išvalyti“. Pasirenkamas šablonas (1 paspaudimas), tada spaudžiamas langelis (2 paspaudimas). Teptukas lieka pasirinktas, todėl kiekvienas kitas langelis užima tik 1 paspaudimą.

**B. Iššokantis langas [M]**
Jei teptukas nepasirinktas, paspaudus langelį atsidaro mažas langas su šablonų sąrašu. Pasirinkus šabloną, jis priskiriamas.

**C. Rankinis laiko keitimas [M]**

- Iššokančiame lange po šablonų sąrašu yra mygtukas **„Kitas laikas“**. Jį paspaudus atsiranda laukai „Pradžia“, „Pabaiga“ ir „Pertrauka“.
- Pirmiausia pasirenkamas šablonas, tada jo laikas pakoreguojamas, pvz. „R“ su 06:00–12:00. Langelis išlaiko šablono spalvą ir kodą, bet pažymimas žvaigždute (**R\***). Užvedus pelę, rodomas tikslus laikas.
- Galima įvesti laiką ir be šablono. Tada langelis būna pilkas, o vietoje kodo rodomas laikas, pvz. „06–12“.
- Šiam veiksmui NFR-2 limitas negalioja, nes tai išimtis, o ne pagrindinis būdas. Bet laukai turi atsirasti per ≤ 2 paspaudimus: langelis, tada „Kitas laikas“.
- Jei ant tokio langelio vėl panaudojamas teptukas, rankinis laikas perrašomas šablono laiku.

**D. Žymėjimas tempiant [S]**
Laikant pelės mygtuką ir tempiant, teptukas pritaikomas visiems pažymėtiems langeliams (keletui dienų arba darbuotojų).

**E. Klaviatūra [S]**
Rodyklėmis judama tarp langelių, šablono kodo raide priskiriamas šablonas, Delete išvalo langelį.

#### FR-2.4 Išsaugojimas [M]

- Pakeisti, bet dar neišsaugoti langeliai pažymimi vizualiai (pvz. taškeliu kampe).
- Mygtukas **„Išsaugoti“** įrašo visus pakeitimus vienu kartu, o **„Atšaukti“** juos atmeta.
- Bandant išeiti iš lentelės ar pakeisti mėnesį su neišsaugotais pakeitimais, rodomas įspėjimas.
- Plano būsenų („Juodraštis“ / „Paskelbta“) nėra. Išsaugota pamaina iš karto galioja ir matoma visiems, turintiems teises.

#### FR-2.5 Naršymas ir filtrai

- **[M]** Mygtukai ◀ / ▶ perjungia mėnesį, mygtukas „Šiandien“ grąžina į einamąjį.
- **[S]** Galima filtruoti pagal skyrių ir ieškoti darbuotojo pagal vardą.
- **[S]** Dešinėje yra stulpelis „Σ val.“: suplanuotos mėnesio valandos kiekvienam darbuotojui.
- **[C]** Apačioje yra eilutė, kuri kiekvienai dienai rodo, kiek žmonių suplanuota kiekvienai pamainai (pvz. R: 5, V: 4, N: 3).

#### FR-2.6 Patikrinimai pagal LR darbo kodeksą [S]

Tai **tik įspėjimai**, išsaugoti jie niekada netrukdo. Įspėjimas rodomas kaip geltonas langelio rėmelis su paaiškinimu užvedus pelę. Tikrinama:

- tarp dviejų pamainų mažiau nei 11 valandų nepertraukiamo poilsio (pvz. naktinė, o kitą rytą rytinė);
- pamaina ilgesnė nei 12 valandų;
- per 7 dienas suplanuota daugiau nei 48 valandos.

Ribos keičiamos nustatymuose.

Prie „Išsaugoti“ mygtuko rodomas įspėjimų skaičius (pvz. ⚠ 3). Jį paspaudus, sąraše matyti, kuriems darbuotojams ir kurioms dienoms įspėjimai taikomi.

#### FR-2.7 Kopijavimas [C]

Mygtukas „Kopijuoti iš praėjusio mėnesio / savaitės“ perkelia pamainas pasirinktiems darbuotojams.

### FR-3 Šablonų valdymas [M]

Sąrašo ir formos vaizdai šablonams kurti, redaguoti ir archyvuoti.

| Laukas | Tipas | Pastaba |
|---|---|---|
| Pavadinimas | tekstas, privalomas | „Rytinė“ |
| Kodas | 1–3 simboliai, privalomas, unikalus įmonėje | rodomas langelyje |
| Spalva | spalvų parinkiklis | langelio fonas |
| Pradžia / pabaiga | laikas | jei pabaiga ≤ pradžia, laikoma, kad pamaina **baigiasi kitą dieną** (naktinė) |
| Pertrauka | minutės | atimama iš trukmės |
| Trukmė | apskaičiuojama, val. | rodoma lentelėje |
| Aktyvus | taip / ne | archyvuoti šablonai nerodomi teptukų juostoje |
| Įmonė | ryšys | palaikomos kelios įmonės |
| Eiliškumas | skaičius | tvarka teptukų juostoje (tempiama pele) |

**Taisyklės:**

- Šablono, kuris jau naudojamas pamainose, ištrinti negalima. Jį galima tik archyvuoti.
- **Šablono pakeitimas jau suplanuotų pamainų nekeičia.** Pamaina saugo savo pradžios ir pabaigos laiką. Tai būtina, kad nesikeistų istoriniai duomenys ir žiniaraštis.

### FR-4 Pamainos duomenų modelis [M]

`hr.shift`:

| Laukas | Pastaba |
|---|---|
| Darbuotojas | privalomas |
| Data | privaloma; dienos, kurią pamaina **prasideda**, data |
| Šablonas | **neprivalomas** (rankiniam laikui be šablono) |
| Pradžia / pabaiga (data ir laikas) | visada saugomi pačioje pamainoje |
| Pertrauka | minutės |
| Trukmė | apskaičiuojama, val. |
| `is_custom` | apskaičiuojamas; ar laikas skiriasi nuo šablono (langelyje rodoma žvaigždutė) |
| Įmonė | iš darbuotojo |

- Naktinė pamaina priskiriama tai dienai, kurią ji **prasideda**.
- Vienam darbuotojui leidžiama ne daugiau kaip viena pamaina per dieną (DB ribojimas).
- Laikas saugomas UTC ir rodomas pagal darbuotojo laiko juostą.
- Kas ir kada sukūrė ar pakeitė pamainą, matyti iš standartinių Odoo laukų (`create_uid` / `write_uid`).
- `state` laukas nekuriamas.

### FR-5 Prieigos teisės [M]

| Grupė | Teisės |
|---|---|
| **Darbo valandos / Planuotojas** | mato ir redaguoja lentelę, valdo šablonus |
| **Darbo valandos / Peržiūra** | mato lentelę, redaguoti negali (teptukų juosta paslėpta) |
| Kiti | meniu nematomas |

Įrašų taisyklė (`ir.rule`): matomi tik savo įmonės duomenys.
**[C]** Pasirinktinai galima riboti pagal skyrių, kuriam vadovaujama.

## 3. Nefunkciniai reikalavimai

| ID | Reikalavimas | Kaip tikrinama |
|---|---|---|
| **NFR-1** | Iš Odoo pagrindinio ekrano iki lentelės ≤ 3 paspaudimai | Rankinis testas |
| **NFR-2** | Lentelėje darbuotojo darbo laikas nustatomas ≤ 2 paspaudimais (+1 nebūtinas „Išsaugoti“). Kai teptukas jau pasirinktas, kiekvienam kitam langeliui reikia 1 paspaudimo. Rankiniam laiko keitimui (FR-2.3 C) šis limitas negalioja. | Rankinis testas |
| **NFR-3** | Langelio aukštis ≥ 60 px, plotis ≥ 44 px (patogu ir lietimui) | CSS / vizualinis testas |
| **NFR-4** | Bazinis modulis **nekeičia kitų modulių elgsenos**: neperrašo jų metodų, neprideda privalomų laukų prie esamų modelių, nekeičia esamų vaizdų. Išdiegus modulį, kitų modulių elgsena lieka nepakitusi. | Kodo peržiūra; `hr` testai praeina su įdiegtu moduliu |
| **NFR-5** | Projektuojama iki 200 darbuotojų (iki 6 200 langelių per mėnesį, virtualizacijos nereikia). Mėnuo įkeliamas per ≤ 2 s viena RPC užklausa. Išsaugojimas vyksta viena užklausa (batch). Paspaudus langelį, vizualinis atsakas < 100 ms (pakeitimai iki išsaugojimo laikomi naršyklėje). | Matavimas su testiniais duomenimis |
| **NFR-6** | Sąsaja lietuvių ir anglų kalbomis (i18n `.po`) | Perjungti kalbą |
| **NFR-7** | Lentelė veikia Chrome, Edge ir Firefox naršyklėse, kai ekrano plotis ≥ 1280 px. Siauresniame ekrane lentelę galima slinkti horizontaliai. | Rankinis testas |
| **NFR-8** | Lentelė sukurta kaip Odoo 19 Owl kliento veiksmas (client action), be išorinių JS bibliotekų | Kodo peržiūra |
| **NFR-9** | Automatiniai testai: Python testai modeliams (naktinė pamaina, ribojimai, teisės) ir Hoot testai lentelei (paspaudimas, žymėjimas, išsaugojimas) | `--test-tags /hr_shift_planning` |

## 4. Kūrimo etapai

1. **Pagrindas:** modulio karkasas, modeliai, teisės, meniu, šablonų valdymas (FR-1, FR-3, FR-4, FR-5).
2. **Lentelė:** Owl komponentas — struktūra, žymėjimas, teptukas, iššokantis langas, išsaugojimas (FR-2.1–FR-2.5).
3. **Papildomos funkcijos:** DK patikrinimai, tempimas, klaviatūra, kopijavimas (FR-2.3 D–E, FR-2.6, FR-2.7).
4. **Integracija (atskiras modulis `hr_shift_planning_attendance`):** buvimo būsena pagal pamainas, vartelių duomenys į `hr.attendance`, plano ir fakto palyginimas (žiniaraštis).

## 5. Įgyvendinimo pastabos

Kūrimo metu priimti sprendimai, kurie patikslina arba keičia aukščiau aprašytus reikalavimus. Smulki istorija — [kūrimo plano](hr_shift_planning_development_plan.md) pakeitimų žurnale.

**Sąsaja**
- Meniu: Employees → Working Hours → Planning / Templates. „Shifts (list)“ (paprastas pamainų sąrašas) matomas tik developer režime — problemų tyrimui ir eksportui.
- Valdymo skydelis: kairėje Save / Discard / Copy ir įspėjimų mygtukas, viduryje teptukų juosta, dešinėje mėnesio naršymas. Paieška ir skyriaus filtras — lentelės kampe virš darbuotojų stulpelio. Stulpelis „Σ h“ prilipęs dešinėje, skaičių eilutės — apačioje.
- Su teptuku langelis užpildomas atleidus pelę (vienas langelis arba tempiant pažymėtas stačiakampis).
- Klaviatūra: po šablono raidės ar Delete žymėjimas pereina į kitą dieną; Enter atidaro šablonų sąrašą.
- „Custom time“ laikas įvedamas tekstu 24 val. formatu (HH:MM, priimami ir trumpi įvedimai: „6“, „630“, „6.30“), nepriklausomai nuo naršyklės kalbos.
- Žvaigždutė („R*“) rodoma, kai pamainos laikas skiriasi nuo **dabartinio** šablono laiko.
- Savaitės atskirtos ryškesne linija prieš kiekvieną pirmadienį.

**Kopijavimas (FR-2.7)**
- Taikomas matomiems (pagal paiešką / skyriaus filtrą) darbuotojams; rezultatas — neišsaugoti pakeitimai.
- „Previous week“ užpildo pažymėtos dienos savaitę (Pr–Sk) ankstesnės savaitės pamainomis, įskaitant tuščias dienas.
- „Previous month“ kopijuoja pagal dienos numerį; dienos, kurių praėjusiame mėnesyje nėra, nekeičiamos.

**DK įspėjimai (FR-2.6)**
- Ribos — Employees → Configuration → Settings → „Shift Planning“; saugomos sistemos parametruose `hr_shift_planning.*` (išdiegiant modulį pašalinami).
- Pamainos trukmė skaičiuojama be pertraukos; poilsis — nuo pamainos pabaigos iki kitos pradžios; 7 dienų riba — slenkančiu langu (diena + 6 ankstesnės). Reikšmė, lygi ribai, įspėjimo nesukelia.
- Įspėjimus ir jų sąrašą mato ir Viewer.

**Laiko juostos**
- Pamainos laikas serveryje skaičiuojamas pagal darbuotojo laiko juostą, o lentelėje rodomas ir įvedamas pagal naršyklės laiko juostą. Abi turi sutapti (`Europe/Vilnius`).

**Poveikis kitiems moduliams (NFR-4)**
- Nauji modeliai: `hr.shift.template`, `hr.shift`. Kitų modulių metodai neperrašomi.
- `res.config.settings`: pridėti trys neprivalomi laukai; Employees nustatymuose pridėtas naujas blokas (esami nekeičiami).
- Darbuotojų sąrašas lentelei skaitomas per `hr.employee.public` (prieinamas visiems vidiniams naudotojams), todėl Viewer grupei nereikia HR teisių.
