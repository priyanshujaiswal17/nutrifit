# NutriFit

[![GitHub](https://img.shields.io/badge/GitHub-priyanshujaiswal17%2Fnutrifit-181717?logo=github)](https://github.com/priyanshujaiswal17/nutrifit)

A full-stack nutrition tracking web application built with Flask, MySQL, and the Google Gemini API. Track meals, monitor daily macros, get AI-powered meal suggestions, and manage family members — all from a clean, dark-themed interface.

---

## Features

- **User accounts** — register, login, bcrypt-hashed passwords, session management
- **Family members** — track nutrition for multiple people under one account
- **Food database** — semantic + fuzzy search over a custom food database
- **Meal logging** — log breakfast, lunch, snacks, and dinner with quantity support
- **Macro tracking** — real-time calories, protein, carbs, and fat totals
- **Nutrition score** — daily diet quality score (Excellent / Good / Fair / Poor)
- **AI meal suggestions** — Gemini-powered, Indian home-cooking focused suggestions
- **AI meal plans** — one-day plan generator with calorie targeting
- **Weight log** — track weight over time with chart visualization
- **Favourites** — bookmark frequently used foods
- **AI chat assistant** — context-aware nutrition Q&A with MCP tool dispatch
- **Admin panel** — user management and feedback viewer
- **Grocery export** — download meal plan as CSV
- **Rate limiting** — built-in request throttling via Flask-Limiter

---

## Tech Stack

| Layer | Technology |
|---|---|
| Backend | Python 3.11, Flask 2.3 |
| Database | MySQL 8.0 with connection pooling |
| AI | Google Gemini API (`gemini-2.5-flash-lite`) |
| Embeddings | `text-embedding-004` for semantic food search |
| Auth | bcrypt password hashing |
| Frontend | Vanilla HTML/CSS/JS (embedded in Flask, dark Obsidian theme) |
| Deployment | Gunicorn + Docker |

---

## Quick Start

### Prerequisites

- Python 3.11+
- MySQL 8.0+
- A [Google Gemini API key](https://aistudio.google.com/)

### Setup

```bash
# Clone
git clone https://github.com/priyanshujaiswal17/nutrifit.git
cd nutrifit

# Virtual environment
python -m venv venv
venv\Scripts\activate        # Windows
# source venv/bin/activate   # macOS / Linux

# Install dependencies
pip install -r requirements.txt

# Configure environment
copy .env.example .env
# Open .env and fill in your Gemini key and MySQL password
```

### Database

Open MySQL and run:

```sql
CREATE DATABASE nutrifit CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;
```

All tables are created automatically on first run.

### Run

```bash
python app.py
```

Open **http://localhost:5000** in your browser.

For detailed setup instructions see [docs/setup.md](docs/setup.md).

---

## Project Structure

```
nutrifi/
├── app.py              # Main Flask application (routes, DB, AI, frontend)
├── requirements.txt    # Python dependencies
├── Dockerfile          # Container build config
├── .env.example        # Environment variable template
├── .gitignore
├── README.md
└── docs/
    └── setup.md        # Detailed local setup & DB schema guide
```

---

## Environment Variables

| Variable | Default | Description |
|---|---|---|
| `GEMINI_KEY` | — | Google Gemini API key (required for AI features) |
| `SECRET_KEY` | — | Flask session secret (set a long random string) |
| `DB_HOST` | `localhost` | MySQL host |
| `DB_USER` | `root` | MySQL username |
| `DB_PASSWORD` | — | MySQL password |
| `DB_NAME` | `nutrifit` | Database name |

Copy `.env.example` → `.env` and fill in your values. **Never commit `.env`.**

---

## License

MIT

---

> Repository: [github.com/priyanshujaiswal17/nutrifit](https://github.com/priyanshujaiswal17/nutrifit)
