# Social Media Search Dashboard (MVP basis)

Python-project voor het lokaal doorzoeken van content van eigen Instagram Business- en Facebook Page-accounts.

Deze fase bevat:

- projectsetup
- configuratie
- SQLite schema en init-script
- basis dataclasses/models
- skeleton voor dashboard en sync-entrypoint
- robuuste Meta API-client (paginering, retry/backoff, foutafhandeling, logging)

Er is nog **geen volledige data-sync naar SQLite** geïmplementeerd in deze fase.

## Scope v1 (doel)

- Instagram posts/captions/hashtags
- Instagram comments
- Facebook Page posts
- Facebook comments
- Geen stories
- Geen replies (nested comments) in MVP
- Zoeken alleen in lokale SQLite database

## Architectuur (huidige basis)

- `app.py`: Streamlit dashboard entrypoint
- `sync.py`: sync CLI entrypoint (placeholder)
- `config.py`: env-configuratie
- `models.py`: basis dataclasses
- `meta/client.py`: Graph API client skeleton
- `meta/endpoints.py`: endpoint- en fieldset-definities
- `db/schema.sql`: SQLite schema + FTS5
- `db/database.py`: DB helper functies
- `scripts/init_db.py`: database initialisatie
- `scripts/check_meta_client.py`: handmatige API-client smoke check met `.env`
- `scripts/meta_api_inspector.py`: uitgebreide API-validatie en JSON-export

## Vereisten

- Python 3.11+
- Windows/macOS/Linux

## Installatie

1. Maak een virtual environment:

   ```bash
   python -m venv .venv
   ```

2. Activeer de omgeving:

   Windows PowerShell:

   ```powershell
   .\.venv\Scripts\Activate.ps1
   ```

3. Installeer dependencies:

   ```bash
   pip install -r requirements.txt
   ```

4. Maak `.env` op basis van voorbeeld:

   ```bash
   copy .env.example .env
   ```

   (op macOS/Linux: `cp .env.example .env`)

5. Vul de Meta-gegevens in `.env` in.

## Facebook Login for Business (aanbevolen flow)

Voor productie heb je doorgaans nodig:

- `pages_show_list`
- `pages_read_engagement`
- `instagram_basic`
- `instagram_manage_comments`

Daarnaast:

- gekoppelde Facebook Page en Instagram Business-account
- juiste Page-taken voor de gebruiker
- App Review/Business Verification (buiten testmodus)

## Database initialiseren

Voer uit:

```bash
python scripts/init_db.py
```

Dit maakt standaard de database aan op `data/social_search.db`.

## Dashboard starten

```bash
streamlit run app.py
```

## Meta setup flow (zonder SQLite sync)

1. Maak `.env` aan:

```powershell
copy .env.example .env
```

2. Vul minimaal in:
- `META_APP_ID`
- `META_APP_SECRET`
- `META_USER_ACCESS_TOKEN` (short-lived uit Graph API Explorer)
- `META_PAGE_ID`
- `META_INSTAGRAM_BUSINESS_ACCOUNT_ID`

3. Vernieuw tokens structureel (aanbevolen):

```powershell
python scripts/refresh_all_tokens.py
```

Dit script:
- zet de short-lived `META_USER_ACCESS_TOKEN` om naar een long-lived user token
- haalt een nieuw Page Access Token op via `GET /{META_PAGE_ID}?fields=access_token`
- werkt `.env` bij (`META_USER_ACCESS_TOKEN`, `META_PAGE_ACCESS_TOKEN`, optioneel IG account id)
- print alleen gemaskeerde tokens

4. Alternatief: losse stappen

```powershell
python scripts/get_long_lived_token.py --update-env
python scripts/get_page_access_token.py --update-env
```

5. Valideer API-data:

```powershell
python scripts/meta_api_inspector.py --limit 5 --export-json inspector-output.json
```

### Token verlopen (Graph API error code 190)

Gebruik geen nieuwe short-lived Graph Explorer tokens als permanente oplossing.

1. Genereer een nieuwe **User Token** in [Graph API Explorer](https://developers.facebook.com/tools/explorer/)
2. Zet die tijdelijk in `META_USER_ACCESS_TOKEN` in `.env`
3. Run:

```powershell
python scripts/refresh_all_tokens.py
python sync.py --all
```

Het dashboard toont bij error code 190:
`Meta token verlopen. Genereer nieuwe User Token en run scripts/refresh_all_tokens.py.`

Setup-scripts maskeren secrets in console-output (alleen eerste/laatste 4 tekens).

## Meta client smoke test (zonder DB-sync)

Voer uit:

```bash
python scripts/check_meta_client.py
```

Optioneel met request debug logging:

```bash
python scripts/check_meta_client.py --verbose
```

Dit controleert technisch:

- ophalen van Pages
- ophalen van gekoppeld Instagram Business Account ID
- ophalen van Instagram media + comments
- ophalen van Facebook Page posts + comments

## Meta API Inspector (validatiefase, zonder SQLite)

Voer uit:

```bash
python scripts/meta_api_inspector.py
```

Nuttige opties:

```bash
python scripts/meta_api_inspector.py --limit 5 --export-json inspector-output.json
python scripts/meta_api_inspector.py --show-raw
python scripts/meta_api_inspector.py --verbose
```

Inspector-functionaliteit:

- test de Meta API verbinding
- toont beschikbare Facebook Pages
- toont gekoppelde Instagram Business Account
- haalt laatste posts en comments op (IG + FB)
- normaliseert output per post/comment
- toont beschikbare en ontbrekende velden
- schrijft optioneel volledige debug JSON weg

Belangrijk: Inspector schrijft niets naar SQLite.

## Unit tests (zonder echte API-calls)

De tests gebruiken HTTP mocking, dus er worden geen echte Meta API-calls gedaan:

```bash
pytest -q
```

## Huidige status

- Dashboard toont basis UI met zoekveld.
- Zoekfunctie en syncflow worden in volgende fases toegevoegd.
- MVP blijft local-first: zoeken gebeurt straks alleen in SQLite.
