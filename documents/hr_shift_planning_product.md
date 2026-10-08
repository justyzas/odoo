# Employee Shift Planning (`hr_shift_planning`) — produkto aprašymas

| | |
|---|---|
| **Modulis** | `hr_shift_planning` (`additional_addons/hr_shift_planning/`) |
| **Versija** | 19.0.1.0.0 |
| **Odoo** | 19.0 Community |
| **Priklauso nuo** | `hr` |
| **Autorius** | SEFU, MB developers |
| **Licencija** | LGPL-3 |
| **Skirta** | Sistemos administratoriui ir techniniam prižiūrėtojui |

Susiję dokumentai: [reikalavimų specifikacija](hr_shift_planning_specs.md) · [kūrimo planas](hr_shift_planning_development_plan.md)

---

## 1. Kas tai

Modulis leidžia planuoti darbuotojų pamainas mėnesio lentelėje: kairėje darbuotojai, viršuje mėnesio dienos, langeliuose pamainos. Pamainos priskiriamos iš anksto paruoštais šablonais (pvz. „RYT 06:00–14:00“) vienu ar dviem paspaudimais, tempiant pele per kelis langelius arba klaviatūra.

Pritaikytas slenkantiems grafikams: kiekviena pamaina yra atskiras įrašas (darbuotojas + diena), todėl darbuotoją galima bet kada perkelti iš vienos pamainos į kitą, nekeičiant jokių savaitinių grafikų.

**Ką modulis daro:**
- pamainų šablonų valdymas;
- mėnesio planavimo lentelė su teptuku, tempimu, klaviatūra, rankiniu laiku, kopijavimu;
- mėnesio valandų suma darbuotojui ir darbuotojų skaičius pamainai per dieną;
- įspėjimai pagal LR darbo kodekso ribas (neblokuoja);
- prieigos teisės: Viewer (peržiūra) ir Planner (planavimas).

**Ko modulis nedaro:**
- nefiksuoja faktinio atvykimo / išvykimo (tam bus atskiras modulis `hr_shift_planning_attendance`, žr. [11 skyrių](#11-ateities-darbai));
- nekeičia standartinio Odoo darbo grafiko (`resource.calendar`), atostogų, lankomumo ar atlyginimų skaičiavimo;
- nesiunčia pranešimų darbuotojams.

---

## 2. Diegimas

### 2.1. Pirmas diegimas

1. Įsitikinkite, kad `additional_addons` yra `addons_path` (faile `odoo.conf`).
2. Apps → **Update Apps List** → raskite „Employee Shift Planning“ → **Install**.

   Arba komandine eilute:
   ```bash
   python odoo-bin -c odoo.conf -d <db> -i hr_shift_planning --stop-after-init
   ```
3. Administratorius (`admin`) automatiškai gauna **Planner** teises.

### 2.2. Atnaujinimas

| Kas pasikeitė | Ką daryti |
|---|---|
| Python failai (`models/`, `hooks.py`, `__init__.py`) | Perkrauti serverį su `-u hr_shift_planning` |
| XML (vaizdai, meniu, teisės), JS, SCSS | Apps → Employee Shift Planning → ⋮ → **Upgrade** |
| Po bet kokio atnaujinimo | Naršyklėje Ctrl+F5 |

```bash
python odoo-bin -c odoo.conf -d <db> -u hr_shift_planning
```

### 2.3. Lietuvių kalba

```bash
# jei lietuvių kalba DB dar neįdiegta
python odoo-bin i18n loadlang -c odoo.conf -d <db> -l lt
```
Vertimas yra `i18n/lt.po` ir įkeliamas diegiant / atnaujinant modulį. Naudotojo kalba keičiama jo nustatymuose (Preferences → Language).

---

## 3. Pradinė konfigūracija

### 3.1. Prieigos teisės

Settings → Users & Companies → Users → naudotojas → skiltis **Working Hours**:

| Grupė | Ką gali |
|---|---|
| *(nėra)* | Meniu „Working Hours“ nematomas |
| **Viewer** | Mato planavimo lentelę, šablonus, įspėjimus. Keisti negali. |
| **Planner** (apima Viewer) | Planuoja pamainas, kuria ir keičia šablonus, kopijuoja |

Viewer grupei HR teisių nereikia: darbuotojų sąrašas lentelei skaitomas per viešą darbuotojų profilį (`hr.employee.public`).

Kelios įmonės: kiekvienas naudotojas mato tik šiuo metu pasirinktų įmonių šablonus ir pamainas.

### 3.2. Darbuotojų laiko juosta

**Svarbu.** Pamainos laikas serveryje skaičiuojamas pagal **darbuotojo** laiko juostą (darbuotojo kortelė → Work Information → Timezone), o lentelėje rodomas ir įvedamas pagal **naršyklės** laiko juostą. Abi turi būti `Europe/Vilnius`, kitaip rodomi laikai pasislinks.

### 3.3. Pamainų šablonai

Employees → Working Hours → **Templates** → New.

| Laukas | Paaiškinimas |
|---|---|
| Name | Pavadinimas, pvz. „Rytinė“ |
| Code | 1–3 simboliai, rodomi lentelės langelyje (pvz. „RYT“). Unikalus įmonėje. Pirmoji raidė naudojama klaviatūros greitiesiems klavišams — patogu, kai ji skiriasi tarp šablonų. |
| Color | Langelio fonas |
| Start / End | Laikas 24 val. formatu. Jei End ≤ Start, pamaina baigiasi kitą dieną (naktinė) — pažymima „Ends Next Day“. |
| Break (minutes) | Atimama iš trukmės |
| Duration | Apskaičiuojama: End − Start − Break |
| Sequence | Tvarka teptukų juostoje (sąraše tempiama ⋮⋮) |

Šablonas, naudojamas pamainose, negali būti ištrintas — tik archyvuotas. Archyvuotas šablonas nerodomas teptukų juostoje, bet jau suplanuotos pamainos išlieka.

**Šablono laiko keitimas nekeičia jau suplanuotų pamainų.** Tokios pamainos lentelėje pradedamos rodyti su žvaigždute („RYT*“), nes jų laikas nebesutampa su šablonu. Norint jas atnaujinti, ant jų pritaikomas tas pats šablonas (teptuku).

### 3.4. Darbo kodekso ribos

Employees → Configuration → Settings → blokas **Shift Planning**:

| Nustatymas | Numatyta | Sistemos parametras |
|---|---|---|
| Minimum Rest Between Shifts | 11 h | `hr_shift_planning.min_rest_hours` |
| Maximum Shift Length | 12 h | `hr_shift_planning.max_shift_hours` |
| Maximum Hours per 7 Days | 48 h | `hr_shift_planning.max_week_hours` |

Ribos taikomos tik įspėjimams — išsaugoti jos niekada netrukdo. Pakeitus ribą, lentelę reikia perkrauti.

### 3.5. Valstybinės šventės

Lentelė šventes rodo gelsvu fonu. Jos imamos iš įmonės darbo grafiko: Employees → Configuration → Working Schedules → įmonės grafikas → **Public Holidays** (Odoo „global time off“ be konkretaus darbuotojo).

---

## 4. Naudojimo instrukcija

Employees → Working Hours → **Planning** (3 paspaudimai nuo pradžios ekrano).

### 4.1. Lentelės išdėstymas

```
 [Save (3)] [Discard] [Copy ▾] [⚠ 2]      [RYT][VAK][NAK][Clear]      Spalis 2026 [◀][Today][▶]
 ┌───────────────────┬────┬────┬────┬────║────┬─ ─ ─┬───────┐
 │ Paieška...        │ 1  │ 2  │ 3  │ 4  ║ 5  │     │  Σ h  │
 │ [Visi skyriai ▾]  │ Ket│ Pen│ Šeš│ Sek║ Pir│     │       │
 ├───────────────────┼────┼────┼────┼────║────┼─ ─ ─┼───────┤
 │ Jonaitis          │RYT │RYT │    │    ║VAK │     │ 120h  │
 │ Gamyba            │7.5h│7.5h│    │    ║7.5h│     │       │
 ├───────────────────┼────┼────┼────┼────║────┼─ ─ ─┼───────┤
 │ RYT               │ 5  │ 4  │    │    ║ 3  │     │       │   ← kiek darbuotojų pamainoje
 │ VAK               │ 4  │ 4  │    │    ║ 5  │     │       │
 └───────────────────┴────┴────┴────┴────║────┴─ ─ ─┴───────┘
                                         ↑ savaitės riba (prieš pirmadienį)
```

- **Langelis:** šablono kodas ir valandos, šablono spalva. „RYT*“ — laikas skiriasi nuo šablono. Pilkas „06-12“ — rankinis laikas be šablono. Užvedus pelę — pavadinimas, tikslus laikas, pertrauka, įspėjimai.
- **Žymėjimas:** paspaustas langelis gauna mėlyną rėmelį, paryškinamas darbuotojo vardas ir dienos antraštė.
- **Spalvos:** šiandiena — mėlyna antraštė; savaitgaliai — pilkesni; šventės — gelsvos; neišsaugotas pakeitimas — oranžinis taškelis kampe; DK įspėjimas — geltonas rėmelis.
- **Σ h:** suplanuotos mėnesio valandos (be pertraukų), atsinaujina iš karto.
- **Apatinės eilutės:** kiek darbuotojų kiekvieną dieną dirba kiekviena pamaina (priklauso nuo filtro).
- **Paieška / skyrius:** kampe virš darbuotojų. Paieška ignoruoja didžiąsias raides ir lietuviškas raides („cinkev“ randa „Cinkevičius“).

### 4.2. Pamainų priskyrimas

| Būdas | Kaip | Paspaudimai |
|---|---|---|
| **Teptukas** | Pasirinkite šabloną juostoje viršuje, spauskite langelius. Teptukas lieka aktyvus; dar kartą paspaudus — atžymimas. „Clear“ — trina. | 2, toliau po 1 |
| **Tempimas** | Su teptuku nuspauskite pelę ant langelio ir tempkite — pažymimas stačiakampis (dienos × darbuotojai); atleidus pritaikoma visiems. | 1 tempimas |
| **Sąrašas langelyje** | Be teptuko paspauskite langelį → pasirinkite šabloną arba „Clear“. | 2 |
| **Rankinis laikas** | Langelis → **Custom time** → Template (nebūtina), Start, End, Break → Apply. | 2 + įvedimas |
| **Klaviatūra** | Žr. 4.3. | 1 klavišas |

**Rankinio laiko įvedimas** — 24 val. formatu, priimami trumpiniai:

| Įvedama | Reikšmė |
|---|---|
| `6` | 06:00 |
| `630`, `6.30`, `6:30` | 06:30 |
| `1400` | 14:00 |
| `24:00` | 00:00 |

Pasirinkus šabloną formoje, laikas užpildomas pagal šabloną. Naktinis laikas (pvz. 20:00–02:30) leidžiamas.

### 4.3. Klaviatūra

Veikia, kai lentelėje yra pažymėtas langelis ir žymeklis nėra įvesties lauke.

| Klavišas | Veiksmas |
|---|---|
| ← → ↑ ↓ | Judėti tarp langelių |
| Raidė | Šablonas, kurio kodas prasideda ta raide; žymėjimas pereina į kitą dieną. Pakartotinai — kitas tokiu kodu prasidedantis šablonas. |
| Delete / Backspace | Išvalyti; žymėjimas pereina į kitą dieną |
| Enter | Atidaryti šablonų sąrašą |
| Esc | Uždaryti sąrašą |

Pavyzdys: pažymėkite pirmadienį ir surinkite `R R R R R` — užpildoma darbo savaitė.

### 4.4. Išsaugojimas

Visi pakeitimai laikomi naršyklėje, kol paspaudžiama **Save** (vienas užklausimas serveriui). **Discard** — atmeta visus. Keičiant mėnesį, einant į kitą meniu ar uždarant skirtuką su neišsaugotais pakeitimais, rodomas įspėjimas.

Pakeitimas, kuris grąžina langelį į išsaugotą būseną, automatiškai nebeskaičiuojamas.

### 4.5. Kopijavimas

**Copy** (tik Planner). Taikoma tik **matomiems** darbuotojams (pagal paiešką ir skyriaus filtrą). Rezultatas — neišsaugoti pakeitimai, juos galima peržiūrėti prieš Save arba atmesti.

| Veiksmas | Ką daro |
|---|---|
| **Previous week → week of the selected day** | Pažymėto langelio savaitė (Pr–Sk) užpildoma ankstesnės savaitės pamainomis, įskaitant laisvas dienas. Reikia pažymėto langelio. |
| **Previous month → this month** | Kopijuojama pagal dienos numerį (1-a → 1-a). Dienos, kurių praėjusiame mėnesyje nėra (pvz. 31-a), nekeičiamos. |

### 4.6. Darbo kodekso įspėjimai

| Patikrinimas | Kaip skaičiuojama |
|---|---|
| Poilsis tarp pamainų | Nuo pamainos pabaigos iki kitos pradžios. Pažymimi abu langeliai. |
| Pamainos trukmė | Darbo valandos be pertraukos |
| Valandos per 7 dienas | Slenkantis langas: diena + 6 ankstesnės. Pažymima diena, kurią riba viršijama. |

Reikšmė, lygi ribai, įspėjimo nesukelia. Tikrinamos ir gretimų mėnesių dienos (7 dienos prieš ir 1 po), todėl įspėjimai teisingi ir mėnesio pradžioje / pabaigoje.

Mygtukas **⚠ N** rodo visų įspėjimų sąrašą; paspaudus įrašą lentelė paslenka iki langelio (jei darbuotojas paslėptas filtru, filtras išvalomas). Įspėjimus mato ir Viewer.

---

## 5. Techninė architektūra

### 5.1. Failų struktūra

```
hr_shift_planning/
├── __manifest__.py               # priklausomybės, duomenys, assets, uninstall_hook
├── hooks.py                      # uninstall_hook: šalina hr_shift_planning.* parametrus
├── models/
│   ├── hr_shift_template.py      # hr.shift.template
│   ├── hr_shift.py               # hr.shift + lentelės RPC metodai
│   └── res_config_settings.py    # DK ribų nustatymai, DEFAULT_LIMITS
├── security/
│   ├── hr_shift_planning_security.xml   # privilegija, grupės, ir.rule
│   └── ir.model.access.csv
├── views/
│   ├── hr_shift_template_views.xml      # šablonų list/form/search/action
│   ├── hr_shift_views.xml               # pamainų list/form/search/action (debug meniu)
│   ├── hr_shift_planning_actions.xml    # ir.actions.client → lentelė
│   ├── hr_shift_planning_menus.xml
│   └── res_config_settings_views.xml    # blokas Employees nustatymuose
├── static/src/planning_grid/
│   ├── planning_grid.js/.xml/.scss      # ShiftPlanningGrid (client action)
│   ├── template_picker.js/.xml          # langelio sąrašas + Custom time forma
│   └── warning_list.js/.xml             # įspėjimų sąrašas
├── static/tests/planning_grid.test.js   # Hoot testai
├── tests/                               # Python testai
└── i18n/                                # .pot, lt.po
```

### 5.2. Duomenų modelis

**`hr.shift.template`** — pamainos šablonas

| Laukas | Tipas | Pastabos |
|---|---|---|
| `name` | Char, translate | privalomas |
| `code` | Char(3) | privalomas; `unique(code, company_id)` |
| `color` | Char | hex, numatyta `#FFE08A` |
| `hour_from`, `hour_to` | Float | vietos laikas valandomis, 0 ≤ x < 24 |
| `break_minutes` | Integer | 0 ≤ pertrauka < trukmė |
| `duration` | Float, compute, store | be pertraukos |
| `is_overnight` | Boolean, compute, store | `hour_to <= hour_from` |
| `sequence`, `active` | | |
| `company_id` | Many2one, privalomas | numatyta dabartinė įmonė |

**`hr.shift`** — viena pamaina

| Laukas | Tipas | Pastabos |
|---|---|---|
| `employee_id` | Many2one `hr.employee` | privalomas, `ondelete=cascade` |
| `date` | Date | diena, kurią pamaina **prasideda**; `unique(employee_id, date)` |
| `template_id` | Many2one `hr.shift.template` | neprivalomas, `ondelete=restrict` |
| `start_datetime`, `end_datetime` | Datetime (UTC) | compute iš šablono + datos + darbuotojo TZ, `store`, `readonly=False`, `precompute` |
| `break_minutes` | Integer | kaip aukščiau |
| `duration` | Float, compute, store | `(end − start) − break` |
| `is_custom` | Boolean, compute, store | laikas skiriasi nuo šablono (serverio pusėje) |
| `company_id` | Many2one, compute, store | darbuotojo įmonė, kitaip dabartinė |

Apribojimai: End > Start; Start (darbuotojo TZ) turi būti `date` dieną; pertrauka trumpesnė už pamainą.

Apskaičiuojami laukai priklauso tik nuo `template_id`, ne nuo šablono laikų — todėl šablono keitimas jau suplanuotų pamainų nekeičia. `precompute=True` reikalingas, kad laukai būtų apskaičiuoti prieš INSERT (DB `NOT NULL`).

### 5.3. RPC metodai (lentelei)

**`hr.shift.get_planning_data(date_from, date_to)`** — vienas užklausimas mėnesio duomenims:

```python
{
    "employees": [{"id", "name", "job_title", "department"}],   # hr.employee.public, aktyvūs, env.companies
    "shifts": [{"id", "employee_id", "date", "template_id", "start", "end",
                "break_minutes", "duration", "is_custom"}],      # date_from-7 … date_to+1
    "templates": [...],        # aktyvūs + archyvuoti, naudojami pamainose
    "holidays": [{"date", "name"}],   # globalūs resource.calendar.leaves, naudotojo TZ
    "can_edit": bool,          # Planner grupė
    "limits": {"min_rest_hours", "max_shift_hours", "max_week_hours"},
}
```
Pamainos papildomai grąžinamos 7 dienoms prieš ir 1 dienai po intervalo — DK patikrinimams ir savaitės kopijavimui.

**`hr.shift.save_planning_changes(changes)`** — visi lentelės pakeitimai vienu kartu:

```python
[
    {"employee_id": 7, "date": "2026-10-15", "template_id": 3},           # šablonas su jo laiku
    {"employee_id": 7, "date": "2026-10-16", "template_id": False},       # pašalinti pamainą
    {"employee_id": 7, "date": "2026-10-17", "template_id": 3,            # rankinis laikas
     "custom": {"hour_from": 6.0, "hour_to": 12.0, "break_minutes": 15}},
]
```
Esama tos dienos pamaina atnaujinama, nesanti — sukuriama. Teisės tikrinamos įprastai (ACL + `ir.rule`).

### 5.4. Teisės (techniškai)

| Objektas | Viewer | Planner |
|---|---|---|
| `hr.shift.template` | read | read, write, create, unlink |
| `hr.shift` | read | read, write, create, unlink |

`ir.rule` (noupdate): `[('company_id', 'in', company_ids)]` abiem modeliams.

`sudo()` naudojamas tik vienoje vietoje: DK ribų skaitymui iš `ir.config_parameter` (`_get_limits`).

### 5.5. Kliento dalis

- `ShiftPlanningGrid` — `ir.actions.client`, tag `hr_shift_planning.planning_grid`; naudoja `Layout` (valdymo skydelis).
- Lentelės duomenys laikomi `markRaw` (nereaktyvūs), keičiami tik visu objektu įkeliant mėnesį. Neišsaugoti pakeitimai — reaktyvus `state.pending` (`"employeeId|YYYY-MM-DD" → value | null`).
- Langelio reikšmė: `{ templateId, hourFrom, hourTo, breakMinutes }` (vietos valandos).
- Stulpelio paryškinimas (hover) daromas tiesiogiai DOM'e, ne per būseną — kad pelės judėjimas nepersipieštų visos lentelės.
- DK įspėjimai skaičiuojami naršyklėje (`computeWarnings`) ir talpinami iki kito pakeitimo.
- Išėjimo įspėjimas: `useSetupAction({ beforeLeave, beforeUnload })`.
- Assets: `web.assets_backend` (`static/src/**/*`), testai — `web.assets_unit_tests`.

### 5.6. Poveikis kitiems moduliams

- Kitų modulių metodai neperrašomi, esami vaizdai nekeičiami.
- `res.config.settings`: trys nauji neprivalomi laukai (`config_parameter`).
- Employees nustatymų vaizde pridėtas naujas blokas (xpath `position="after"`).

---

## 6. Testavimas

```bash
# Python testai
# MSYS_NO_PATHCONV=1: kitaip Git Bash „/hr_shift_planning“ paverčia Windows keliu ir paleidžiama 0 testų
MSYS_NO_PATHCONV=1 python odoo-bin -c odoo.conf -d <db> -u hr_shift_planning --test-enable --test-tags /hr_shift_planning --stop-after-init
```

JS (Hoot) testai: paleidus serverį, naršyklėje `/web/tests?filter=hr_shift_planning`.

Greitaveika (200 darbuotojų su mėnesio pamainomis):
```bash
# sukurti
python odoo-bin shell -c odoo.conf -d <db> < documents/scripts/hr_shift_planning_perf_data.py
# ištrinti
PERF_MODE=delete python odoo-bin shell -c odoo.conf -d <db> < documents/scripts/hr_shift_planning_perf_data.py
```
Duomenys kuriami skyriuje „Perf Test“, darbuotojai „Perf Test NNN“. Naudoti tik testinėje / development DB.

---

## 7. Priežiūra

### 7.1. Vertimų atnaujinimas

Pakeitus ar pridėjus sąsajos tekstus:
```bash
# 1. atnaujinti modulį, tada sugeneruoti šabloną
python odoo-bin i18n export -c odoo.conf -d <db> hr_shift_planning
# → additional_addons/hr_shift_planning/i18n/hr_shift_planning.pot

# 2. papildyti i18n/lt.po naujais įrašais (pvz. msgmerge arba Poedit)
msgmerge --update additional_addons/hr_shift_planning/i18n/lt.po additional_addons/hr_shift_planning/i18n/hr_shift_planning.pot

# 3. atnaujinti modulį (įkelia vertimus)
python odoo-bin -c odoo.conf -d <db> -u hr_shift_planning
```

### 7.2. Naujas laukas / pakeitimas

- Keičiant lentelės duomenis, atnaujinti abu: `get_planning_data` (serveris) ir `buildGrid` (`planning_grid.js`), taip pat Hoot testų `makePlanningData`.
- Keičiant išsaugojimo formatą — `save_planning_changes` ir `save()` (`planning_grid.js`).
- Didinant `__manifest__.py` versiją, jei reikia migracijos — `migrations/<versija>/`.

### 7.3. Išdiegimas

Apps → Employee Shift Planning → Uninstall. Ištrinami visi šablonai ir pamainos (lentelės), meniu, teisių grupės, nustatymų blokas ir `hr_shift_planning.*` sistemos parametrai. **Duomenų atkurti nebus galima** — prieš išdiegiant pasidarykite DB kopiją arba eksportuokite pamainas (developer režimas → Working Hours → Shifts (list) → Export).

---

## 8. Problemų sprendimas

| Simptomas | Priežastis / sprendimas |
|---|---|
| Laikai lentelėje pasislinkę 1–3 val. | Nesutampa darbuotojo ir naršyklės laiko juostos (žr. 3.2). |
| Po atnaujinimo „Save“ meta klaidą apie nežinomą metodą | Pakeistas Python kodas, bet serveris neperkrautas. Perkrauti su `-u hr_shift_planning`. |
| Nauji JS / CSS pakeitimai nematomi | Ctrl+F5; jei nepadeda — Upgrade modulį. |
| Naudotojas nemato meniu „Working Hours“ | Nepriskirta Viewer / Planner grupė (žr. 3.1). |
| Viewer nemato teptukų juostos | Taip ir turi būti — reikia Planner. |
| Teptukų juostoje nėra šablono | Šablonas archyvuotas arba kitos įmonės. |
| Negalima ištrinti šablono | Jis naudojamas pamainose — archyvuokite. |
| Visos senos pamainos rodomos su „*“ | Pakeistas šablono laikas (žr. 3.3). |
| Nerodomos šventės | Šventės įvestos ne įmonės darbo grafike arba priskirtos konkrečiam darbuotojui (žr. 3.5). |
| DK ribų pakeitimas neveikia | Perkraukite lentelę (ribos įkeliamos kartu su mėnesio duomenimis). |
| Klaviatūra nereaguoja | Nepažymėtas langelis arba žymeklis paieškos / formos lauke. |
| Nesimato pamainų developer sąraše | Įmonių pasirinkimas viršuje (multi-company) arba filtrai. |

---

## 9. Žinomi apribojimai

- Viena pamaina vienam darbuotojui per dieną.
- Laiko juostos: žr. 3.2. Vasaros / žiemos laiko perėjimo naktį DK poilsio skaičiavimas lentelėje gali skirtis 1 val. (skaičiuojama vietos laiku be DST).
- „Previous month“ kopijuoja pagal dienos numerį, ne pagal savaitės dieną.
- DK ribos bendros visai sistemai (ne įmonei / darbuotojui).
- Lentelė optimizuota iki ~200 darbuotojų vienu metu; daugiau — naudokite skyriaus filtrą.
- Planai neturi būsenų (juodraštis / paskelbta): išsaugota pamaina iš karto galioja.

---

## 10. Sąsajos žodynas (EN ↔ LT)

| Angliškai | Lietuviškai |
|---|---|
| Working Hours | Darbo valandos |
| Planning | Planavimas |
| Templates | Šablonai |
| Shifts (list) | Pamainos (sąrašas) |
| Viewer / Planner | Peržiūra / Planuotojas |
| Custom time | Kitas laikas |
| Clear | Išvalyti |
| Copy | Kopijuoti |
| Save / Discard | Išsaugoti / Atmesti |
| Stay | Likti |
| Apply | Pritaikyti |
| Ends Next Day | Baigiasi kitą dieną |
| Shift Planning (nustatymų blokas) | Pamainų planavimas |
| Labour Code Warnings | Darbo kodekso įspėjimai |

---

## 11. Ateities darbai

- **`hr_shift_planning_attendance`** (planuojamas atskiras modulis): darbuotojų „buvimo“ būsena (raudona / žalia) pagal pamainas, vartelių sistemos duomenys į `hr.attendance`, plano ir fakto palyginimas, lankomumo žiniaraštis.
