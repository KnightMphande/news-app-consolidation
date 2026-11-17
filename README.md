# Newsroom

A Django news application with a browser interface and a token-authenticated REST API. Readers follow journalists and publishers; journalists write stories; editors review submissions, curate newsletters and manage publisher memberships.

## System requirements

- Python 3.14 (the author's Windows environment)
- MariaDB running on port 3306
- Packages pinned in `requirements.txt`

The project uses Django 6.1.1, Django REST Framework 3.18.1 and mysqlclient 2.3.0. MariaDB 12.3.3 was used in the author's environment. The application has no bundled database or preset passwords.

## Install on Windows

Open PowerShell in the directory containing `manage.py`:

```powershell
python -m venv myenv
.\myenv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
Copy-Item .env.example .env
```

Edit `.env` locally and replace both placeholder values. `NEWS_DB_PASSWORD` is the password for the MariaDB application account. Generate a secret key with:

```powershell
python -c "from django.core.management.utils import get_random_secret_key; print(get_random_secret_key())"
```

Copy that key into `NEWS_SECRET_KEY`. When a value contains `#`, wrap the complete value in single quotes in `.env`. Do not publish the resulting `.env`. Existing operating-system environment variables take precedence over `.env`; remove an obsolete session value with `Remove-Item Env:NEWS_DB_PASSWORD` if needed.

## Create the MariaDB database

Log into MariaDB as an administrator. If its client is not on PATH, adjust this example to match the installed location:

```powershell
& "C:\Program Files\MariaDB 12.3\bin\mariadb.exe" -u root -p
```

For a new installation, run the following SQL, replacing the password placeholder with the value used in `.env`:

```sql
CREATE DATABASE news_app CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;
CREATE USER 'news_app_user'@'localhost' IDENTIFIED BY 'REPLACE_WITH_YOUR_DATABASE_PASSWORD';
GRANT ALL PRIVILEGES ON news_app.* TO 'news_app_user'@'localhost';
GRANT ALL PRIVILEGES ON test_news_app.* TO 'news_app_user'@'localhost';
exit;
```

If the database/account already exists, retain it. Do not recreate or drop an existing application database.

## Initialise and run

```powershell
python manage.py migrate
python manage.py setup_roles
python manage.py check
python manage.py test news
python manage.py runserver
```

Open http://127.0.0.1:8000/accounts/register/ to create an account, or follow **Create account** on the login page. Registration signs the user in and assigns their chosen role group immediately. `setup_roles` also reconciles existing accounts after imports or permission changes. No superuser is needed for the normal website journeys.

## Use the website

| Role | Main actions |
|---|---|
| Reader | View published articles and newsletters; manage publisher/journalist subscriptions. |
| Journalist | Create independent or affiliated publisher articles, edit/delete own stories, curate own newsletters. |
| Editor | Review and approve articles; edit/delete stories and newsletters; create newsletters; create and manage affiliated publications. |

The educational sign-up workflow permits users to choose any of these three news roles. Choosing Editor gives editorial privileges, but never staff/superuser access. A production newsroom would normally require invitations or approval for editorial accounts.

To set up a publication entirely through the website:

1. Register a journalist account, then log out.
2. Register an editor account, open **Publishers**, and select **Create publisher**.
3. Enter its name/description and select registered journalists and editors. The creating editor is retained automatically.
4. Log in as the journalist. **Create article** now offers that publisher, or an independent article.
5. Log in as an editor, open the draft, and select **Approve and publish**.
6. Register a reader, set publisher/journalist subscriptions, and open published content and newsletters.

An affiliated editor can use **Manage publisher** to update its details and newsroom members. Other editors cannot change those memberships. Publisher membership governs management and authorship; article approval remains a global editor permission, consistent with the existing editorial policy.

Only currently approved articles can be selected for newsletters, and stories returned to draft are hidden from reader newsletter pages. Edits to article content return it to approval. Admin approval is read-only: publication uses the website or API so notifications are triggered.

## Browser routes

| Route | Purpose |
|---|---|
| `/accounts/register/` | Sign up with username, email, role and password confirmation |
| `/accounts/login/`, `/accounts/logout/` | Login and POST logout |
| `/` | Articles visible to the current role |
| `/articles/create/` | Journalist article submission |
| `/articles/<id>/` | Article detail with authorised actions |
| `/articles/<id>/edit/`, `/articles/<id>/delete/` | Edit and deletion confirmation |
| `/articles/<id>/approve/` | Editor approval by POST |
| `/publishers/`, `/publishers/<id>/` | Publications and newsroom details |
| `/publishers/create/`, `/publishers/<id>/edit/` | Editor publication/membership management |
| `/newsletters/`, `/newsletters/<id>/` | Collections of approved articles |
| `/newsletters/create/`, `/newsletters/<id>/edit/`, `/newsletters/<id>/delete/` | Newsletter management |
| `/subscriptions/` | Reader subscription choices |

## REST API

Obtain a token with a POST of `username` and `password` to `/api/token/`. Send `Authorization: Token YOUR_TOKEN` on subsequent API requests. Never publish tokens.

| Method | Route | Access |
|---|---|---|
| GET | `/api/articles/` | Approved articles, authenticated view permission |
| GET | `/api/articles/subscribed/` | Reader's subscribed publishers OR journalists |
| GET | `/api/articles/<id>/` | Approved article detail |
| POST | `/api/articles/` | Journalist creates a draft |
| PUT | `/api/articles/<id>/` | Journalist author or editor; resets approval |
| DELETE | `/api/articles/<id>/` | Journalist author or editor |
| POST | `/api/articles/<id>/approve/` | Editor approval |
| POST | `/api/approved/` | Internal signed approval event, not token login |

Article write fields are `title`, `content` and optional `publisher`. Author and approval cannot be overridden by submitted values. Separate serializers exist for articles, users, publishers and newsletters.

## Notifications

Approval uses the brief's **without signals** strategy: approval views call the notification service. A user-save signal is used separately for role membership. Active subscribed readers receive individual article messages with deduplicated addresses. The development console mail backend prints email to the runserver terminal; it does not deliver to an external inbox.

The service sends a signed HTTP POST to `http://127.0.0.1:8000/api/approved/`, which records one receipt per article. Runserver's default threaded mode supports this local self-request. Repeated approval of an unchanged article skips delivery; an edited draft can be approved again. Delivery failures are logged and shown as warnings while the article remains approved. The integration has a five-second timeout and no durable retry queue.

## Verification

Run `python manage.py test news` to exercise the existing role/API/content tests and the new registration, publisher-access and complete browser workflow tests. Tests use `test_news_app`, not the application database. `planning/07_feedback_resolution.md` maps the resubmission changes to marker feedback and records the test environment.

The normal local application remains configured for MariaDB. Use the development server only for local demonstrations; production hosting requires HTTPS, private secrets, DEBUG disabled, a production server and a reviewed editorial account policy.
