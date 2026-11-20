# News App — Capstone Consolidation

Django newsroom with reader, journalist and editor accounts, publishers,
article approval, curated newsletters, subscriptions and a token-authenticated API.
Approval emails appear in the development server console. Approval events are
signed and posted to `/api/approved/`. No external email provider is needed.

## Requirements

Python 3.14 and MariaDB for a local installation, or Docker Desktop with Linux
containers and Docker Compose. This repository retains the original dependency
pins supplied with the project. Install the exact versions in requirements.txt.

## Local setup (PowerShell)

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
Copy-Item .env.example .env
python -c "from django.core.management.utils import get_random_secret_key; print(get_random_secret_key())"
notepad .env
```

Put the generated secret in NEWS_SECRET_KEY. Set NEWS_DB_PASSWORD to your
MariaDB application's password. Never commit .env. NEWS_DB_ROOT_PASSWORD is only
used by Compose. Existing terminal environment variables take precedence over .env.

Using a MariaDB administrator, create the local database and user, replacing
`CHOOSE_A_PASSWORD` with your own value:

```sql
CREATE DATABASE news_app CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;
CREATE USER 'news_app_user'@'localhost' IDENTIFIED BY 'CHOOSE_A_PASSWORD';
GRANT ALL PRIVILEGES ON news_app.* TO 'news_app_user'@'localhost';
GRANT ALL PRIVILEGES ON test_news_app.* TO 'news_app_user'@'localhost';
```

```powershell
python manage.py migrate
python manage.py setup_roles
python manage.py check
python manage.py test news
python manage.py runserver
```

Open http://127.0.0.1:8000/accounts/register/ to create an account. No administrator
account is needed for the ordinary browser journeys. Optional admin setup:
`python manage.py createsuperuser`.

## Docker setup

Copy .env.example to .env and replace all three placeholder secrets. The Compose
database is separate from your existing Windows database; it starts empty.

```powershell
docker compose config --quiet
docker compose up --build -d
docker compose logs web
```

Open http://127.0.0.1:8000/. Compose waits for MariaDB, applies migrations and
configures role permissions before starting the server. The database has no host
port exposed. The website binds to localhost. These settings and Django's
runserver are intended for local coursework, not public production hosting.

```powershell
docker compose exec web python manage.py check
docker compose down
```

The named news_data volume preserves container data across restarts. Do not use
`down -v` unless you intend to erase that database. Changing passwords in .env
does not change passwords in an already initialised database volume.

For the full test suite, use the local setup above: its dedicated test database
permissions avoid granting the container app user wider database privileges.

## Verify on a different computer (Codespaces)

Push this repository to GitHub, open a Codespace, and create a fresh .env there
with your own secrets. Run `docker compose config --quiet` and
`docker compose up --build -d`. In the Ports panel forward port 8000 privately.
Add the forwarded hostname, without https:// or a path, to NEWS_ALLOWED_HOSTS in
.env and run `docker compose up -d --force-recreate web`.

Register a journalist and editor. As the editor create a publisher and assign the
journalist; as the journalist create a draft; as the editor approve it and create
a newsletter. Register a reader, view both and save subscriptions. Check
`docker compose logs web` for approval email and the internal API response.
Remote Docker execution remains a submission check until these commands succeed.

## Documentation

Read the included `docs/_build/html/index.html` in a browser. Generated HTML is
tracked as explicitly requested by the Consolidation brief; build caches are ignored.

```powershell
python -m pip install -r requirements-docs.txt
python -m sphinx -b html -W --keep-going docs docs/_build/html
Start-Process .\docs\_build\html\index.html
```

The Sphinx configuration initialises Django with an in-memory database for imports;
it does not require access to application data. Public module references exclude
tests and migrations.

## Browser journeys

- Reader: register, read approved articles/newsletters and manage subscriptions.
- Journalist: register, draft/edit/delete own content and curate newsletters.
- Editor: register, manage publishers and newsroom membership, review articles,
  approve publication and manage newsletters.
- Changed articles return for review; unchanged edit submissions retain approval.

## REST API

POST JSON username/password to `/api/token/`. Include
`Authorization: Token YOUR_TOKEN` on authenticated requests.

| Route | Methods | Purpose |
| --- | --- | --- |
| /api/articles/ | GET, POST | Published list; journalist submission |
| /api/articles/subscribed/ | GET | Reader's subscribed content |
| /api/articles/<id>/ | GET, PUT, DELETE | Published detail; authorised management |
| /api/articles/<id>/approve/ | POST | Editor approval |
| /api/approved/ | POST | Signed internal approval event |

## Git workflow

The baseline is on main, individual script documentation commits are on docs,
and Docker/Compose changes are on container, branched from the baseline.
Both branches are merged into main with merge commits. All three branches should
be pushed so reviewers can inspect the workflow.
