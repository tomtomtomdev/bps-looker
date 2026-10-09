# BPS WebAPI reference

Condensed from the official docs at https://webapi.bps.go.id/documentation/ (read 2026-10-05).
Items marked **(verified)** were checked against the live API with a real key on that date.

## Basics

| | |
|---|---|
| Base URL | `https://webapi.bps.go.id/v1/api/` |
| Auth | `key=<BPS_API_KEY>` query param on every request. Key lives in `.env` (gitignored); template in `.env.example`. |
| Format | JSON |
| Language | `lang=ind` (default) or `lang=eng` on most endpoints |
| Domain | 4-digit BPS site code: `0000` = national (Pusat), `1100` = Aceh, `3100` = DKI Jakarta, regencies like `3171`. See `/domain`. |

### Gotchas (verified)

- **User-Agent is required.** BPS's perimeter WAF returns an HTML "Perimeter WAF Block" page for default `curl`/`python-urllib` User-Agents. Send a browser-like UA, e.g. `User-Agent: Mozilla/5.0 (Macintosh) bps-looker`.
- **Use `/v1/api/view` and `/v1/api/list`.** The docs show some endpoints as `/v1/view` or `/v1/list` — those return `404 - Unable to resolve the request`.
- **Errors come back as HTTP 200** with `"status": "Error"` and a `message`. Always check `status`, not the HTTP code. Examples:
  - bad/missing key → `{"status":"Error","message":"You are not Allowed to take this action. Please re-check your key"}` (some key strings, e.g. `000…0x`, are instead blocked by the WAF with a 403 HTML page)
  - `model=data` without `th` → `"'th' parameter is required and must be an integer, separated by colon (:) for range or semicolon (;) for multiple values."`
- `data-availability` is `"available"` or `"not-available"` (empty result).
- Path-style URLs also work: `/v1/api/list/model/th/domain/0000/var/1804/key/<KEY>/`.
- Text fields (`notes`, `table`, `abstract`) can contain HTML or HTML-escaped HTML (`&lt;br /&gt;`).
- Responses include extra fields beyond what the docs list (e.g. `subcsa_id`/`subcsa_name` on vars and press releases).

### Response envelope (list endpoints)

```json
{
  "status": "OK",
  "data-availability": "available",
  "data": [
    { "page": 1, "pages": 6, "per_page": 10, "count": 10, "total": 52 },
    [ { ...item... }, ... ]
  ]
}
```

`data[0]` = pagination, `data[1]` = items. Paginate with `page=N` until `page == pages`. Default `per_page` is 10; `perpage` is ignored for `var` and `th` (verified), documented for `tablestatistic`/`glosarium`. View endpoints return `data` as a single object.

### Example

```bash
set -a; . ./.env; set +a
curl -s -A "Mozilla/5.0" \
  "https://webapi.bps.go.id/v1/api/list?model=subject&domain=0000&lang=ind&key=$BPS_API_KEY"
```

---

## Master data

### Domain — `GET /domain`
| Param | Req | Notes |
|---|---|---|
| `type` | yes | `all` · `prov` · `kab` · `kabbyprov` |
| `prov` | with `kabbyprov` | 4-digit province ID, e.g. `3100` |

Item: `domain_id`, `domain_name`, `domain_url`. `type=all` → 549 domains; `type=prov` → 34 (verified). Not paginated; `data[0]` is `{"page": 1, "pages": 1, "total": 549}` (verified 2026-10-09). `type=kabbyprov&prov=3400` lists the province's regencies/cities (not the province itself) — exactly the `type=all` ids sharing its first two digits (checked for 3400 → 5 and 9400 → 29, 2026-10-09); `bps seed dynamic --prov` uses that id prefix.

### Subject categories — `GET /list?model=subcat`
Params: `domain`, `lang`, `page`. Item: `subcat_id`, `title`.
National values (verified): `0` Lainnya, `1` Sosial dan Kependudukan, `2` Ekonomi dan Perdagangan, `3` Pertanian dan Pertambangan.

### Subjects — `GET /list?model=subject`
Params: `domain`, `lang`, `subcat` (opt), `page`. Item: `sub_id`, `title`, `subcat_id`, `subcat`, `ntabel`. National has 52 subjects (verified).

### Units — `GET /list?model=unit`
Params: `domain`, `lang`, `page`. Item: `unit_id`, `unit`.

---

## Dynamic tables

A dynamic table is a **variable** (`var`) broken down by:
- **vervar** — vertical variable (rows, usually regions or categories)
- **turvar** — derived variable (columns/sub-categories; `0` = "Tidak ada" when unused)
- **th** — period (year); `th_id` is *not* the year (e.g. `117` = 2017, `119` = 2019)
- **turth** — derived period (`0` = annual "Tahun", `1..12` = months, etc.)

Discovery flow: `subject` → `var` (by subject) → `th` (by var) → `data`.

### Variables — `GET /list?model=var`
| Param | Req | Notes |
|---|---|---|
| `domain` | yes | |
| `subject` | opt | filter by `sub_id` |
| `year` | opt | |
| `area` | opt | `1` = only vars that exist on this domain |
| `vervar` | opt | |
| `lang`, `page` | opt | |

Item (verified): `var_id`, `title`, `sub_id`, `sub_name`, `subcsa_id`, `subcsa_name`, `def`, `notes`, `vertical`, `unit`, `graph_id`, `graph_name`.

### Periods — `GET /list?model=th`
Params: `domain`, `var` (opt), `page`. Item: `th_id`, `th` (year label).

### Derived periods — `GET /list?model=turth`
Params: `domain`, `var` (opt), `page`. Item: `turth_id`, `turth`, `group_turth_id`, `name_group_turth`.

### Derived variables — `GET /list?model=turvar`
Params: `domain`, `var` (opt), `group` (opt), `nopage=1` (disable pagination), `page`. Item: `turvar_id`, `turvar`, `group_turvar_id`, `name_group_turvar`.

### Vertical variables — `GET /list?model=vervar`
Params: `domain`, `var` (opt), `page`. Item: `vervar_id`, `vervar`, `item_ver_id`, `group_ver_id`, `name_group_ver_id`.

### Data — `GET /list?model=data`
| Param | Req | Notes |
|---|---|---|
| `domain` | yes | |
| `var` | yes | |
| `th` | **yes** | single `117`, list `117;119`, range `117:119`. **Max periods per call depend on the domain:** national `0000` → 3 (`…parameter is 3`); province and regency domains → **2** (`The maximum allowed number of years for the 'th' parameter is 2. You provided 3.` — `1200/297`, `3401/109`; lists `a;b;c` count the same; verified 2026-10-09/10, national still accepts 3 at the same time) |
| `turvar`, `vervar`, `turth` | opt | same syntax |
| `lang` | opt | |

Not paginated. Monthly vars return `turtahun` 1–12 (verified on var 2263); the `turtahun` list also has `13` = `Tahunan` with no values in `datacontent` (2024). Top-level keys (verified): `status`, `data-availability`, `last_update`, `subject`, `var`, `turvar`, `labelvervar`, `vervar`, `tahun`, `turtahun`, `datacontent`, `related`. Each dimension is a list of `{val, label}`. `var[0]` also has `unit`, `subj`, `def`, `decimal`, `note`.

**`datacontent` keys are the concatenation `vervar + var + turvar + th + turth`** (verified):

```
var=1804, vervar=1, turvar=0, th=117, turth=0  →  "1" "1804" "0" "117" "0"  →  "1180401170": 440
```

To decode, build keys from the dimension lists rather than splitting the string (the parts have variable length).

---

## Static tables

### List — `GET /list?model=statictable`
Params: `domain`, `lang`, `page`, `month`, `year`, `keyword`.
Item: `table_id`, `title`, `subj_id`, `subj`, `updt_date`, `size`, `excel` (download URL). National has ~722 tables (verified).

### Detail — `GET /view?model=statictable&id=<table_id>`
Params: `domain`, `lang`, `id`. Data: `table_id`, `sub_id`, `subcsa_id`, `subcsa`, `title`, `table` (HTML-escaped HTML table), `cr_date`, `updt_date`, `size`, `excel`.

---

## CSA subjects (Classification of Statistical Activities)

| Endpoint | Params | Item |
|---|---|---|
| `/list?model=subcatcsa` | `domain` | `subcat_id`, `title` (e.g. 514 demografi & sosial, 515 ekonomi, 516 lingkungan hidup & multi-domain) |
| `/list?model=subjectcsa` | `domain`, `subcat` | `sub_id`, `title`, `subcat_id`, `subcat` |
| `/list?model=tablestatistic` | `domain`, `subject`, `page`, `perpage` | `id`, `title`, `id_subject`, `subject`, `id_subcat`, `subcat`, `tablesource` (1 static, 2 dynamic, 3 SIMDASI) |
| `/view?model=tablestatistic` | `domain`, `id`, `year` (opt), `lang` | table detail |

---

## Press releases, publications, news

| Endpoint | Params | Item fields |
|---|---|---|
| `/list?model=pressrelease` | `domain`, `lang`, `page`, `month`, `year`, `keyword` | `brs_id`, `subj_id`, `subj`, `title`, `abstract`, `rl_date`, `updt_date`, `pdf`, `size` |
| `/view?model=pressrelease&id=` | `domain`, `lang`, `id` | + `abstract` |
| `/list?model=publication` | same as pressrelease | `pub_id` (string), `title`, `issn`, `abstract`, `sch_date`, `rl_date`, `updt_date`, `cover`, `pdf`, `size` |
| `/view?model=publication&id=` | `domain`, `lang`, `id` | + `kat_no`, `pub_no` |
| `/list?model=news` | `domain`, `lang`, `page`, `newscat` (`sensus`/`survey`/`lainnya`), `month` (`01`–`12`), `year`, `keyword` | `news_id`, `newscat_id`, `newscat_name`, `title`, `news`, `rl_date` |
| `/view?model=news&id=` | `domain`, `lang`, `id` | + `picture` |
| `/list?model=newscategory` | `domain`, `lang` | `newscat_id`, `newscat_name` |

---

## Other content

### Strategic indicators — `GET /list?model=indicators`
National and province domains only. Params: `domain`, `lang`, `var`, `page`.
Item (verified): `var`, `indicator_id`, `subject_csa`, `title`, `name`, `data_source`, `value`, `unit`, `category`, `hash_id`, `periode`. National has 16 (inflation, unemployment, etc.).

### Infographics — `GET /list?model=infographic`
Params: `domain`, `lang`, `page`, `keyword`. Item: `inf_id`, `title`, `img`, `desc`, `category` (1 social, 2 economy, 3 agriculture), `dl`.

### Glossary — `GET /list?model=glosarium` / `GET /view?model=glosarium&id=`
List params: `prefix` (first letter), `page`, `perpage` (≤500). Items are Elasticsearch hits; fields are under `_source`: `id`, `konsep`/`konsep_en`, `definisi`/`definisi_en`, `satuan`, `ukuran`, `sumberData`, ...

### Search — `GET /list?model=<model>&keyword=...`
Params: `model`, `domain`, `lang`, `page`, `keyword` (use `+` for spaces).

### Statistical classifications (KBLI / KBKI)
- List: `/list?model=kbli2009|kbli2015|kbli2017|kbli2020|kbki2015` + `page`, `perpage`, `level`
  - KBLI levels: `kategori`, `golongan pokok`, `golongan`, `subgolongan`, `kelompok`
  - KBKI levels: `seksi`, `divisi`, `kelompok`, `kelas`, `subkelas`, `kelompok komoditas`
- Detail: `/view?model=<same>&id=kbli_2009_01` (also `lang`)

### SDGs — `GET /list?model=sdgs&domain=0000`
Param `goal` (1–17). Lists SDG tables with their `var` IDs. Read the data via `model=data&domain=0000&var=<id>` (e.g. var `1804` = disaster victims, `192` = poverty % by province).

### SDDS — `GET /list?model=sdds&domain=0000`
Special Data Dissemination Standard tables (CPI, WPI, GDP, unemployment, exports...). Each has a model (`data` or `statictable`) and a var ID, e.g. `1753` export oil & gas/non-oil & gas, `1709` CPI 90 cities.

---

## Foreign trade — `GET /dataexim/`
| Param | Notes |
|---|---|
| `sumber` | `1` export, `2` import |
| `periode` | `1` monthly, `2` annual |
| `kodehs` | HS code(s), `;`-separated |
| `jenishs` | `1` 2-digit HS, `2` full HS (per that year's HS master) |
| `tahun` | year — **lowercase** (docs say `Tahun`, which errors with `Parameter tahun is missing`) (verified) |

Response has `metadata` (field descriptions) and `data` rows: `value` (USD), `netweight` (kg), `kodehs`, `pod` (Indonesian port), `ctr` (partner country), `tahun`.

Example: `/dataexim/?sumber=1&periode=2&kodehs=03&jenishs=1&tahun=2024&key=KEY`

Verified: no pagination (whole result in one response); monthly rows add `bulan: "[11] November"`; `kodehs` is `"[03] Fish, ..."`; 10 chapters monthly for one year ≈ 14k rows / 2.2 MB / 10 s.

Discovery (2026-10-09, `scripts/trade_discovery.py`):
- **Earliest year is 2014** (exports and imports, annual and monthly). 2013, 2012, 2010 → HTTP 200 `{"status": "OK", "data-availability": "unavailable"}`.
- Chapter **77** → unavailable (reserved in HS). Chapters **98** ("Incompletely knocked down motor vehicles…") and **99** ("Software, digital product, and parcel goods") have data — crawl 01–99 except 77.
- `jenishs=2` works with **8-digit** national codes: `kodehs=03011110` → rows with `kodehs: "[03011110] Live fry freshwater ornamental fish"` and an extra field `jenishs: "hs2022"`. `03`, `0301`, `030111` and dotted `0301.11.10` → unavailable. `;`-joined 8-digit codes are accepted.
- Some rows have `ctr: null` (2025/2026 exports, HS 84/87 via Tanjung Priok; seen 2026-10-09), like `pod: null` — stored with the `''` sentinel.
- Descriptions vary: 2014 import rows say `"[03] Ikan dan krustasea, …"` (Indonesian) while exports say `"[03] Fish, crustaceans and mollusca"`.

---

## Census (sensus.bps.go.id) — `/interoperabilitas/datasource/sensus/id/<n>/`

| id | Purpose | Params |
|---|---|---|
| 37 | List census events | — |
| 38 | Data topics in an event | `kegiatan` (e.g. `sp2020`) |
| 39 | Areas in an event (docs URL mistakenly shows 38) | `kegiatan` |
| 40 | Datasets for event + topic | `kegiatan`, `topik` |
| 41 | Census data | `kegiatan`, `wilayah_sensus`, `dataset` |

id 41 returns `data[1] = {timestamp, status, data_count, data: [...]}`. Rows: `id_wilayah`, `kode_wilayah`, `nama_wilayah`, `level_wilayah`, `id_indikator`, `nama_indikator`, `id/nama_kategori_1..4`, `id/kode/nama_item__kategori_1..4`, `period`, `nilai` (string number).

## SIMDASI — `/interoperabilitas/datasource/simdasi/id/<n>/`
Source of the *Statistik Indonesia* and *Daerah Dalam Angka* publications. Areas use **7-digit MFD codes** (`0000000` Indonesia, `3100000` DKI Jakarta).

| id | Purpose | Params |
|---|---|---|
| 26 | Province MFD codes | — |
| 27 | Regency MFD codes | `parent` (province code) |
| 28 | District MFD codes | `parent` (regency code) |
| 22 | Subjects/chapters for an area | `wilayah` |
| 23 | Tables for an area | `wilayah` |
| 24 | Tables for area + subject | `wilayah`, `id_subjek` (= `mms_id`) |
| 25 | Table detail/data | `wilayah`, `tahun`, `id_tabel` |
| 34 | Master tables | — |
| 36 | Master table detail | `id_tabel` |

Table items: `id_tabel` (opaque string), `judul`/`judul_en`, `kode_tabel`, `ketersediaan_tahun` (array of years), `id_subject`, `bab`/`bab_en`, `subject`/`subject_en`, `mms_id`, `mms_subject`.
