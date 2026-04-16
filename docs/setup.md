# Development Setup Guide

## Prerequisites

- Python 3.11+
- MySQL 8.0+
- A Gemini API key ([get one here](https://aistudio.google.com/))

---

## Environment Variables

Copy `.env.example` to `.env` and fill in your values:

| Variable | Description |
|---|---|
| `GEMINI_KEY` | Your Google Gemini API key |
| `SECRET_KEY` | A random secret for Flask sessions (use a long random string) |
| `DB_HOST` | MySQL host (default: `localhost`) |
| `DB_USER` | MySQL username (default: `root`) |
| `DB_PASSWORD` | MySQL password |
| `DB_NAME` | Database name (default: `nutrifit`) |

---

## MySQL Setup

1. Open MySQL Workbench or the MySQL CLI.
2. Create the database:
   ```sql
   CREATE DATABASE nutrifit CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;
   ```
3. The app automatically creates all tables on first run.

### Database Schema

| Table | Purpose |
|---|---|
| `users` | Auth — username, hashed password, admin flag |
| `members` | Family members tracked per user account |
| `food_items` | Food database with macros + vector embeddings |
| `meals` | Meal entries (breakfast / lunch / snacks / dinner) |
| `meal_food` | Junction — which foods are in each meal |
| `weight_log` | Daily weight tracking per member |
| `food_favourites` | Per-user bookmarked foods |
| `feedback` | In-app user feedback submissions |

---

## Running Locally

```bash
# 1. Clone the repo
git clone https://github.com/priyanshujaiswal17/nutrifit.git
cd nutrifi

# 2. Create and activate a virtual environment
python -m venv venv
venv\Scripts\activate       # Windows
# source venv/bin/activate   # macOS / Linux

# 3. Install dependencies
pip install -r requirements.txt

# 4. Configure environment
copy .env.example .env
# Edit .env with your actual values

# 5. Run
python app.py
# → http://localhost:5000
```

---

## Production Deployment (Docker)

```bash
# Build image
docker build -t nutrifi .

# Run container (pass env vars)
docker run -p 5000:5000 --env-file .env nutrifi
```

Deploy to any platform supporting Docker: Railway, Render, Fly.io, Google Cloud Run.

---

## Admin Access

To grant admin access to a user, run this SQL after they've registered:

```sql
UPDATE users SET is_admin = 1 WHERE username = 'your_username';
```

The admin panel is accessible at `/admin` when logged in as an admin user.
