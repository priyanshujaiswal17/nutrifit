"""
╔══════════════════════════════════════════════════════════════════╗
║           NutriFit — Complete Single-File Application            ║
║  Flask + MySQL + Ollama phi3 + ChromaDB + MCP Tools              ║
║  Run:  python nutrifit.py                                        ║
╚══════════════════════════════════════════════════════════════════╝

Requirements (install before running):
    pip install flask flask-cors mysql-connector-python ollama chromadb sentence-transformers
"""

# ══════════════════════════════════════════════════════════════════
#  IMPORTS
# ══════════════════════════════════════════════════════════════════
from flask import Flask, request, jsonify, session
from flask_cors import CORS
import mysql.connector
from mysql.connector import Error as MySQLError
import ollama
import chromadb
from chromadb.utils import embedding_functions
import re, os, json
from datetime import date

# ══════════════════════════════════════════════════════════════════
#  FLASK APP
# ══════════════════════════════════════════════════════════════════
app = Flask(__name__, template_folder=None, static_folder=None)
app.secret_key = "nutrifit_secret_2024"
CORS(app)

# ══════════════════════════════════════════════════════════════════
#  DATABASE
# ══════════════════════════════════════════════════════════════════
DB_CONFIG = {
    "host":     "localhost",
    "user":     "root",
    "password": "Root",
    "database": "nutrifit"
}

def get_db():
    db = mysql.connector.connect(**DB_CONFIG)
    return db, db.cursor()

def close_db(db, cursor):
    try:
        cursor.close()
        if db.is_connected():
            db.close()
    except Exception:
        pass

def init_db():
    db, cur = get_db()
    try:
        cur.execute("""CREATE TABLE IF NOT EXISTS users(
            user_id  INT AUTO_INCREMENT PRIMARY KEY,
            username VARCHAR(100) UNIQUE NOT NULL,
            password VARCHAR(255) NOT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)""")
        cur.execute("""CREATE TABLE IF NOT EXISTS members(
            member_id INT AUTO_INCREMENT PRIMARY KEY,
            user_id   INT NOT NULL,
            name      VARCHAR(100) NOT NULL,
            age       INT, gender ENUM('Male','Female'),
            weight    DECIMAL(5,2), height DECIMAL(5,2),
            FOREIGN KEY(user_id) REFERENCES users(user_id))""")
        cur.execute("""CREATE TABLE IF NOT EXISTS food_items(
            food_id   INT AUTO_INCREMENT PRIMARY KEY,
            food_name VARCHAR(255) UNIQUE NOT NULL,
            calories  DECIMAL(8,2), protein DECIMAL(8,2),
            carbs     DECIMAL(8,2), fat     DECIMAL(8,2))""")
        cur.execute("""CREATE TABLE IF NOT EXISTS meals(
            meal_id   INT AUTO_INCREMENT PRIMARY KEY,
            member_id INT NOT NULL,
            meal_type ENUM('Breakfast','Lunch','Snacks','Dinner') NOT NULL,
            meal_date DATE NOT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY(member_id) REFERENCES members(member_id))""")
        cur.execute("""CREATE TABLE IF NOT EXISTS meal_food(
            id       INT AUTO_INCREMENT PRIMARY KEY,
            meal_id  INT NOT NULL,
            food_id  INT NOT NULL,
            quantity DECIMAL(8,2) DEFAULT 1,
            FOREIGN KEY(meal_id) REFERENCES meals(meal_id),
            FOREIGN KEY(food_id) REFERENCES food_items(food_id))""")
        db.commit()
        print("✅ Database tables ready.")
    finally:
        close_db(db, cur)

# ══════════════════════════════════════════════════════════════════
#  AI ENGINE  (Ollama phi3)
# ══════════════════════════════════════════════════════════════════
AI_SYSTEM = """You are NutriFit AI, a professional nutritionist assistant.
Rules:
- Give concise, practical answers
- Use bullet points when listing items
- Always mention calorie counts when discussing food
- Keep responses under 250 words unless asked for a full plan
"""

def ai_generate(prompt: str, max_tokens: int = 300) -> str:
    try:
        r = ollama.chat(
            model="phi3",
            messages=[{"role":"system","content":AI_SYSTEM},
                      {"role":"user",  "content":prompt}],
            options={"num_predict": max_tokens, "temperature": 0.2}
        )
        return r["message"]["content"]
    except Exception as e:
        return f"⚠️ AI unavailable — make sure Ollama is running with phi3.\nError: {e}"

def extract_nutrition(text: str):
    def grab(pattern):
        m = re.search(pattern, text, re.IGNORECASE)
        if not m: raise ValueError(f"Pattern not found: {pattern}\nResponse: {text}")
        return int(float(m.group(1)))
    return grab(r"Calories:\s*(\d+(?:\.\d+)?)"), grab(r"Protein:\s*(\d+(?:\.\d+)?)"), \
           grab(r"Carbs:\s*(\d+(?:\.\d+)?)"),   grab(r"Fat:\s*(\d+(?:\.\d+)?)")

def calc_score(totals: dict, goal: int = 2000) -> dict:
    score = 100
    if totals.get("calories",0) > goal: score -= 20
    if totals.get("protein", 0) < 50:  score -= 20
    if totals.get("carbs",   0) > 300: score -= 15
    if totals.get("fat",     0) > 70:  score -= 15
    score = max(score, 0)
    label, color = (("Excellent","green") if score>=90 else
                    ("Good","blue")        if score>=70 else
                    ("Fair","yellow")      if score>=50 else
                    ("Poor","red"))
    return {"score": score, "label": label, "color": color}

# ══════════════════════════════════════════════════════════════════
#  VECTOR DB  (ChromaDB + sentence-transformers)
# ══════════════════════════════════════════════════════════════════
CHROMA_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "chroma_store")
_embed_fn = embedding_functions.SentenceTransformerEmbeddingFunction(
    model_name="all-MiniLM-L6-v2"
)
_chroma_client     = chromadb.PersistentClient(path=CHROMA_PATH)
_food_collection   = _chroma_client.get_or_create_collection(
    name="nutrifit_foods",
    embedding_function=_embed_fn,
    metadata={"hnsw:space": "cosine"}
)

def vec_add(food_id: int, food_name: str):
    _food_collection.upsert(
        ids=[str(food_id)],
        documents=[food_name],
        metadatas=[{"food_id": food_id, "food_name": food_name}]
    )

def vec_search(query: str, n: int = 5) -> list:
    count = _food_collection.count()
    if count == 0: return []
    res = _food_collection.query(
        query_texts=[query],
        n_results=min(n, count),
        include=["metadatas","distances"]
    )
    return [{"food_id": m["food_id"], "food_name": m["food_name"],
             "similarity": round(1 - d, 4)}
            for m, d in zip(res["metadatas"][0], res["distances"][0])]

# ══════════════════════════════════════════════════════════════════
#  MCP TOOLS
# ══════════════════════════════════════════════════════════════════
def mcp_get_user_profile(user_id):
    db, cur = get_db()
    try:
        cur.execute("SELECT name,age,gender,weight,height FROM members WHERE user_id=%s",(user_id,))
        rows = cur.fetchall()
        if not rows: return {"error":"No members found."}
        return {"members":[{"name":r[0],"age":r[1],"gender":r[2],
                             "weight_kg":float(r[3] or 0),
                             "height_cm":float(r[4] or 0),
                             "bmi":round(float(r[3] or 1)/((float(r[4] or 100)/100)**2),1)}
                            for r in rows]}
    finally: close_db(db,cur)

def mcp_get_today_calories(user_id):
    db, cur = get_db()
    try:
        cur.execute("""SELECT SUM(f.calories*mf.quantity),SUM(f.protein*mf.quantity),
                              SUM(f.carbs*mf.quantity),  SUM(f.fat*mf.quantity)
                       FROM meal_food mf JOIN food_items f ON mf.food_id=f.food_id
                       JOIN meals m ON mf.meal_id=m.meal_id
                       JOIN members mem ON m.member_id=mem.member_id
                       WHERE mem.user_id=%s AND m.meal_date=CURDATE()""",(user_id,))
        r = cur.fetchone()
        return {"date":str(date.today()),
                "calories":round(r[0] or 0,1),"protein_g":round(r[1] or 0,1),
                "carbs_g":  round(r[2] or 0,1),"fat_g":    round(r[3] or 0,1)}
    finally: close_db(db,cur)

def mcp_search_food(query):
    db, cur = get_db()
    try:
        cur.execute("SELECT food_name,calories,protein,carbs,fat FROM food_items WHERE food_name LIKE %s LIMIT 8",(f"%{query}%",))
        return {"query":query,"results":[{"food":r[0],"calories":r[1],"protein":r[2],"carbs":r[3],"fat":r[4]} for r in cur.fetchall()]}
    finally: close_db(db,cur)

def mcp_log_meal(user_id, member_name, food_name, meal_type, quantity):
    db, cur = get_db()
    try:
        cur.execute("SELECT member_id FROM members WHERE user_id=%s AND name=%s",(user_id,member_name))
        m = cur.fetchone()
        if not m: return {"error":f"Member '{member_name}' not found."}
        cur.execute("SELECT food_id FROM food_items WHERE food_name=%s",(food_name,))
        f = cur.fetchone()
        if not f: return {"error":f"Food '{food_name}' not in database."}
        cur.execute("INSERT INTO meals(member_id,meal_type,meal_date) VALUES(%s,%s,CURDATE())",(m[0],meal_type))
        mid = cur.lastrowid
        cur.execute("INSERT INTO meal_food(meal_id,food_id,quantity) VALUES(%s,%s,%s)",(mid,f[0],quantity))
        db.commit()
        return {"message":f"Logged {quantity}x {food_name} as {meal_type} for {member_name}.","meal_id":mid}
    except Exception as e: return {"error":str(e)}
    finally: close_db(db,cur)

MCP_TOOLS = {
    "get_user_profile":  {"desc":"Get profile and BMI for all members.",  "fn": mcp_get_user_profile},
    "get_today_calories":{"desc":"Get today's calorie and macro totals.", "fn": mcp_get_today_calories},
    "search_food":       {"desc":"Search food by name.",                   "fn": mcp_search_food},
    "log_meal":          {"desc":"Log a meal for a member.",               "fn": mcp_log_meal},
}

def mcp_dispatch(user_id, query):
    manifest = "\n".join(f"- {n}: {v['desc']}" for n,v in MCP_TOOLS.items())
    sel_prompt = f"""Available tools:\n{manifest}\nUser query: "{query}"\nUser ID: {user_id}
Reply ONLY with valid JSON: {{"tool":"<name>","args":{{}}}}  or {{"tool":"none","args":{{}}}}"""
    raw = ai_generate(sel_prompt, max_tokens=150)
    tool_name, args = "none", {}
    try:
        m = re.search(r"\{.*\}", raw, re.DOTALL)
        if m:
            p = json.loads(m.group())
            tool_name = p.get("tool","none")
            args      = p.get("args",{})
    except Exception: pass

    tool_result = None
    if tool_name in MCP_TOOLS:
        fn = MCP_TOOLS[tool_name]["fn"]
        try:
            if tool_name in ("get_user_profile","get_today_calories"):
                tool_result = fn(user_id)
            elif tool_name == "search_food":
                tool_result = fn(args.get("query", query))
            elif tool_name == "log_meal":
                tool_result = fn(user_id, args.get("member_name",""),
                                 args.get("food_name",""),
                                 args.get("meal_type","Lunch"),
                                 int(args.get("quantity",1)))
        except Exception as e: tool_result = {"error": str(e)}

    if tool_result:
        ai_resp = ai_generate(f'User asked: "{query}"\nTool used: {tool_name}\nData: {json.dumps(tool_result, default=str)}\nAnswer naturally.', 300)
    else:
        ai_resp = ai_generate(query, 300)

    return {"tool_used": tool_name, "tool_result": tool_result, "response": ai_resp}

# ══════════════════════════════════════════════════════════════════
#  HTML PAGES  (inline templates)
# ══════════════════════════════════════════════════════════════════

# ── Shared CSS ──────────────────────────────────────────────────
SHARED_CSS = """
<style>
@import url('https://fonts.googleapis.com/css2?family=DM+Serif+Display:ital@0;1&family=DM+Sans:wght@300;400;500;600&display=swap');
:root{--bg:#0d0f0e;--surface:#141714;--surface-2:#1b1f1b;--border:#252925;--accent:#b5f23d;--accent-dim:#8ab82e;--accent-bg:rgba(181,242,61,.07);--text:#e8ede6;--text-muted:#7a8878;--text-faint:#3e4a3c;--red:#ff5e5e;--yellow:#f5c842;--blue:#5ea8ff;--radius:14px;--radius-sm:8px;--tr:.22s cubic-bezier(.4,0,.2,1);--shadow:0 4px 24px rgba(0,0,0,.5)}
*,*::before,*::after{box-sizing:border-box;margin:0;padding:0}
html{scroll-behavior:smooth}
body{font-family:'DM Sans',sans-serif;background:var(--bg);color:var(--text);font-size:15px;line-height:1.6;min-height:100vh}
a{color:var(--accent);text-decoration:none}a:hover{text-decoration:underline}
button{cursor:pointer;font-family:inherit}input,select,textarea{font-family:inherit}
h1,h2,h3,h4{font-family:'DM Serif Display',serif;line-height:1.2;color:var(--text)}
h1{font-size:clamp(2rem,5vw,3.5rem)}h2{font-size:clamp(1.5rem,3vw,2.2rem)}h3{font-size:1.3rem}h4{font-size:1.1rem}
p{color:var(--text-muted)}
.container{max-width:1160px;margin:0 auto;padding:0 24px}
/* Navbar */
.navbar{position:sticky;top:0;z-index:100;background:rgba(13,15,14,.88);backdrop-filter:blur(18px);border-bottom:1px solid var(--border);padding:16px 0}
.navbar-inner{display:flex;align-items:center;justify-content:space-between}
.nav-brand{font-family:'DM Serif Display',serif;font-size:1.5rem;color:var(--accent)}
.nav-brand span{color:var(--text)}
.nav-links{display:flex;gap:8px;align-items:center}
.nav-link{padding:7px 14px;border-radius:var(--radius-sm);color:var(--text-muted);font-size:.88rem;font-weight:500;transition:var(--tr)}
.nav-link:hover,.nav-link.active{color:var(--text);background:var(--surface-2);text-decoration:none}
.nav-btn{padding:8px 18px;border-radius:var(--radius-sm);background:var(--accent);color:var(--bg);font-weight:600;font-size:.88rem;border:none;transition:var(--tr)}
.nav-btn:hover{background:var(--accent-dim)}
/* Buttons */
.btn{display:inline-flex;align-items:center;gap:8px;padding:10px 20px;border-radius:var(--radius-sm);font-weight:500;font-size:.9rem;border:none;transition:var(--tr);white-space:nowrap}
.btn-primary{background:var(--accent);color:var(--bg)}.btn-primary:hover{background:var(--accent-dim);transform:translateY(-1px)}
.btn-ghost{background:var(--surface-2);color:var(--text-muted);border:1px solid var(--border)}.btn-ghost:hover{color:var(--text);border-color:#444}
.btn-sm{padding:7px 14px;font-size:.83rem}.btn-full{width:100%;justify-content:center}
.btn:disabled{opacity:.4;cursor:not-allowed;transform:none}
/* Forms */
.form-group{margin-bottom:18px}
.form-label{display:block;font-size:.82rem;font-weight:500;color:var(--text-muted);margin-bottom:6px;letter-spacing:.05em;text-transform:uppercase}
.form-control{width:100%;background:var(--surface-2);border:1px solid var(--border);border-radius:var(--radius-sm);color:var(--text);padding:10px 14px;font-size:.93rem;transition:var(--tr);outline:none}
.form-control:focus{border-color:var(--accent);background:rgba(181,242,61,.04)}
.form-control::placeholder{color:var(--text-faint)}
select.form-control{appearance:none;background-image:url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' width='12' height='12' viewBox='0 0 12 12'%3E%3Cpath fill='%237a8878' d='M6 8L1 3h10z'/%3E%3C/svg%3E");background-repeat:no-repeat;background-position:right 12px center;padding-right:36px}
.form-row{display:grid;grid-template-columns:1fr 1fr;gap:16px}
/* Cards */
.card{background:var(--surface);border:1px solid var(--border);border-radius:var(--radius);padding:24px;transition:var(--tr)}
.card:hover{border-color:#333}
.card-header{display:flex;align-items:center;justify-content:space-between;margin-bottom:20px}
/* Stats */
.stat-grid{display:grid;grid-template-columns:repeat(4,1fr);gap:16px}
.stat-tile{background:var(--surface);border:1px solid var(--border);border-radius:var(--radius);padding:20px}
.stat-tile .label{font-size:.78rem;color:var(--text-muted);text-transform:uppercase;letter-spacing:.06em;margin-bottom:8px}
.stat-tile .value{font-family:'DM Serif Display',serif;font-size:2rem;color:var(--text)}
.stat-tile .unit{font-size:.8rem;color:var(--text-muted);margin-top:2px}
.stat-tile.accent-tile{border-color:var(--accent);background:var(--accent-bg)}.stat-tile.accent-tile .value{color:var(--accent)}
/* Progress */
.progress-bar-wrap{background:var(--surface-2);border-radius:999px;height:8px;overflow:hidden;margin:8px 0}
.progress-bar-fill{height:100%;background:var(--accent);border-radius:999px;transition:width .6s ease}
.progress-bar-fill.warning{background:var(--yellow)}.progress-bar-fill.danger{background:var(--red)}
/* Score badge */
.score-badge{display:inline-flex;align-items:center;gap:8px;padding:8px 16px;border-radius:999px;font-weight:600;font-size:.9rem}
.score-badge.green{background:rgba(181,242,61,.12);color:var(--accent)}.score-badge.blue{background:rgba(94,168,255,.12);color:var(--blue)}
.score-badge.yellow{background:rgba(245,200,66,.12);color:var(--yellow)}.score-badge.red{background:rgba(255,94,94,.12);color:var(--red)}
/* AI box */
.ai-box{background:var(--accent-bg);border:1px solid rgba(181,242,61,.2);border-radius:var(--radius);padding:20px 24px;line-height:1.8;white-space:pre-wrap;color:var(--text);font-size:.93rem}
.ai-box-header{display:flex;align-items:center;gap:8px;font-size:.8rem;font-weight:600;color:var(--accent);text-transform:uppercase;letter-spacing:.08em;margin-bottom:12px}
/* Table */
.data-table{width:100%;border-collapse:collapse;font-size:.88rem}
.data-table th{text-align:left;padding:10px 14px;color:var(--text-muted);font-size:.75rem;font-weight:500;text-transform:uppercase;letter-spacing:.06em;border-bottom:1px solid var(--border)}
.data-table td{padding:12px 14px;border-bottom:1px solid var(--border);color:var(--text-muted)}
.data-table tr:last-child td{border-bottom:none}.data-table tr:hover td{background:var(--surface-2);color:var(--text)}
.data-table .food-name{color:var(--text);font-weight:500}.data-table .cal{color:var(--accent);font-weight:600}
/* Food cards */
.food-grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(200px,1fr));gap:14px}
.food-card{background:var(--surface);border:1px solid var(--border);border-radius:var(--radius);padding:16px;cursor:pointer;transition:var(--tr)}
.food-card:hover{border-color:var(--accent);transform:translateY(-2px);box-shadow:var(--shadow)}
.food-card.selected{border-color:var(--accent);background:var(--accent-bg)}
.food-card .fc-name{font-weight:600;color:var(--text);margin-bottom:6px}.food-card .fc-cals{color:var(--accent);font-size:1.1rem;font-weight:700;margin-bottom:4px}
.food-card .fc-macros{display:flex;gap:8px;font-size:.75rem;color:var(--text-muted)}
/* Tabs */
.tabs{display:flex;gap:4px;border-bottom:1px solid var(--border);margin-bottom:24px}
.tab{padding:10px 18px;font-size:.88rem;font-weight:500;color:var(--text-muted);cursor:pointer;border:none;background:none;border-bottom:2px solid transparent;transition:var(--tr);margin-bottom:-1px}
.tab:hover{color:var(--text)}.tab.active{color:var(--accent);border-bottom-color:var(--accent)}
/* Toast */
.toast-container{position:fixed;bottom:24px;right:24px;z-index:9999;display:flex;flex-direction:column;gap:10px}
.toast{background:var(--surface);border:1px solid var(--border);border-radius:var(--radius-sm);padding:14px 20px;font-size:.88rem;color:var(--text);box-shadow:var(--shadow);animation:slideIn .3s ease;max-width:320px}
.toast.success{border-left:3px solid var(--accent)}.toast.error{border-left:3px solid var(--red)}.toast.info{border-left:3px solid var(--blue)}
@keyframes slideIn{from{opacity:0;transform:translateX(20px)}to{opacity:1;transform:translateX(0)}}
/* Spinner */
.spinner{display:inline-block;width:18px;height:18px;border:2px solid var(--border);border-top-color:var(--accent);border-radius:50%;animation:spin .7s linear infinite}
@keyframes spin{to{transform:rotate(360deg)}}
/* Hero */
.hero{padding:90px 0 70px;position:relative;overflow:hidden}
.hero::before{content:'';position:absolute;inset:0;background:radial-gradient(ellipse 70% 60% at 60% 40%,rgba(181,242,61,.06) 0%,transparent 70%);pointer-events:none}
.hero-eyebrow{display:inline-flex;align-items:center;gap:8px;font-size:.78rem;font-weight:600;color:var(--accent);text-transform:uppercase;letter-spacing:.12em;background:var(--accent-bg);border:1px solid rgba(181,242,61,.2);padding:6px 14px;border-radius:999px;margin-bottom:24px}
.hero-title{max-width:640px;margin-bottom:20px}.hero-title em{font-style:italic;color:var(--accent)}
.hero-sub{max-width:500px;font-size:1.05rem;margin-bottom:36px}
.hero-actions{display:flex;gap:12px;flex-wrap:wrap}
.hero-features{display:flex;gap:24px;margin-top:60px;padding-top:40px;border-top:1px solid var(--border);flex-wrap:wrap}
.hero-feature{display:flex;align-items:center;gap:10px;font-size:.88rem;color:var(--text-muted)}
/* Modals */
.modal-overlay{position:fixed;inset:0;z-index:200;background:rgba(0,0,0,.75);backdrop-filter:blur(8px);display:flex;align-items:center;justify-content:center;opacity:0;pointer-events:none;transition:var(--tr)}
.modal-overlay.open{opacity:1;pointer-events:all}
.modal{background:var(--surface);border:1px solid var(--border);border-radius:var(--radius);padding:36px;width:100%;max-width:420px;transform:translateY(16px);transition:var(--tr)}
.modal-overlay.open .modal{transform:translateY(0)}
/* App layout */
.app-layout{display:grid;grid-template-columns:220px 1fr;min-height:calc(100vh - 60px)}
.sidebar{background:var(--surface);border-right:1px solid var(--border);padding:28px 16px;position:sticky;top:60px;height:calc(100vh - 60px);overflow-y:auto}
.sidebar-section{margin-bottom:24px}
.sidebar-label{font-size:.7rem;font-weight:600;color:var(--text-faint);text-transform:uppercase;letter-spacing:.1em;padding:0 12px;margin-bottom:6px}
.sidebar-link{display:flex;align-items:center;gap:10px;padding:9px 12px;border-radius:var(--radius-sm);color:var(--text-muted);font-size:.88rem;font-weight:500;transition:var(--tr);cursor:pointer;border:none;background:none;width:100%;text-align:left}
.sidebar-link:hover{background:var(--surface-2);color:var(--text)}.sidebar-link.active{background:var(--accent-bg);color:var(--accent)}
.main-content{padding:32px;overflow-y:auto}
/* Pills */
.pill{display:inline-block;padding:3px 10px;border-radius:999px;font-size:.75rem;font-weight:600}
.pill-green{background:rgba(181,242,61,.12);color:var(--accent)}.pill-yellow{background:rgba(245,200,66,.12);color:var(--yellow)}
.pill-red{background:rgba(255,94,94,.12);color:var(--red)}.pill-blue{background:rgba(94,168,255,.12);color:var(--blue)}
.meal-breakfast{color:var(--yellow)}.meal-lunch{color:var(--blue)}.meal-dinner{color:#c084fc}.meal-snacks{color:var(--accent)}
/* Chat */
.chat-msg{display:flex;flex-direction:column;gap:4px}.chat-msg.user{align-items:flex-end}.chat-msg.ai{align-items:flex-start}
.chat-bubble{max-width:85%;padding:12px 16px;border-radius:var(--radius);font-size:.9rem;line-height:1.7;white-space:pre-wrap}
.chat-bubble.user{background:var(--accent);color:var(--bg);font-weight:500}
.chat-bubble.ai{background:var(--surface-2);border:1px solid var(--border);color:var(--text)}
.mcp-turn{background:var(--surface);border:1px solid var(--border);border-radius:var(--radius);padding:20px}
.mcp-tool-badge{display:inline-block;background:rgba(181,242,61,.1);border:1px solid rgba(181,242,61,.2);border-radius:var(--radius-sm);padding:4px 10px;font-size:.78rem;color:var(--accent);font-weight:600;margin-bottom:10px}
/* Grid helpers */
.grid-2{display:grid;grid-template-columns:1fr 1fr;gap:20px}.grid-3{display:grid;grid-template-columns:1fr 1fr 1fr;gap:20px}
.mt-16{margin-top:16px}.mt-24{margin-top:24px}.section{padding:40px 0}
/* Responsive */
@media(max-width:900px){.app-layout{grid-template-columns:1fr}.sidebar{display:none}.stat-grid{grid-template-columns:1fr 1fr}.form-row{grid-template-columns:1fr}.grid-2,.grid-3{grid-template-columns:1fr}}
@media(max-width:600px){.stat-grid{grid-template-columns:1fr 1fr}}
</style>
"""

# ── Shared JS ────────────────────────────────────────────────────
SHARED_JS = """
<script>
const Auth={
  getUser(){return JSON.parse(localStorage.getItem("nf_user")||"null")},
  setUser(u){localStorage.setItem("nf_user",JSON.stringify(u))},
  clearUser(){localStorage.removeItem("nf_user")},
  isLoggedIn(){return!!this.getUser()},
  getUserId(){const u=this.getUser();return u?u.user_id:null}
};
const API={
  async call(method,path,body=null){
    const opts={method,headers:{"Content-Type":"application/json"}};
    if(body)opts.body=JSON.stringify(body);
    const res=await fetch(path,opts);
    const data=await res.json();
    if(!res.ok)throw new Error(data.error||"Request failed");
    return data;
  },
  get(p){return this.call("GET",p)},
  post(p,b){return this.call("POST",p,b)}
};
function toast(msg,type="info",dur=3500){
  let c=document.getElementById("toast-container");
  if(!c){c=document.createElement("div");c.id="toast-container";c.className="toast-container";document.body.appendChild(c);}
  const el=document.createElement("div");el.className=`toast ${type}`;el.textContent=msg;c.appendChild(el);
  setTimeout(()=>{el.style.opacity="0";el.style.transform="translateX(20px)";el.style.transition=".3s";setTimeout(()=>el.remove(),300);},dur);
}
function setLoading(btn,on){
  if(on){btn.dataset.orig=btn.innerHTML;btn.innerHTML=`<span class="spinner"></span> Loading…`;btn.disabled=true;}
  else{btn.innerHTML=btn.dataset.orig||"Submit";btn.disabled=false;}
}
function openModal(id){document.getElementById(id).classList.add("open")}
function closeModal(id){document.getElementById(id).classList.remove("open")}
function escHtml(s){return s.replace(/&/g,"&amp;").replace(/</g,"&lt;").replace(/>/g,"&gt;")}
function fmt(n,u=""){return Math.round(n).toLocaleString()+(u?" "+u:"")}
function setProgress(el,ratio){
  const p=Math.min(ratio*100,100);el.style.width=p+"%";
  el.classList.toggle("warning",ratio>.8&&ratio<=1);el.classList.toggle("danger",ratio>1);
}
function requireAuth(r="/"){if(!Auth.isLoggedIn()){window.location.href=r;return false;}return true;}
document.addEventListener("click",e=>{document.querySelectorAll(".modal-overlay.open").forEach(o=>{if(e.target===o)o.classList.remove("open");});});
document.addEventListener("keydown",e=>{if(e.key==="Escape")document.querySelectorAll(".modal-overlay.open").forEach(o=>o.classList.remove("open"));});

/* Canvas charts */
function drawBarChart(id,labels,values,color="#b5f23d"){
  const c=document.getElementById(id);if(!c)return;
  const ctx=c.getContext("2d"),W=c.width=c.offsetWidth,H=c.height=c.offsetHeight;
  ctx.clearRect(0,0,W,H);
  const max=Math.max(...values,1),pad={t:20,r:20,b:40,l:50};
  const dW=W-pad.l-pad.r,dH=H-pad.t-pad.b,bW=dW/labels.length-8;
  ctx.fillStyle="#252925";
  for(let i=1;i<=4;i++){const y=pad.t+dH*(1-i/4);ctx.fillRect(pad.l,y,dW,1);}
  labels.forEach((lbl,i)=>{
    const x=pad.l+i*(dW/labels.length),bH=(values[i]/max)*dH,y=pad.t+dH-bH;
    ctx.fillStyle=color;ctx.globalAlpha=.85;rRect(ctx,x+4,y,bW,bH,4);ctx.fill();ctx.globalAlpha=1;
    ctx.fillStyle="#7a8878";ctx.font="11px DM Sans";ctx.textAlign="center";ctx.fillText(lbl.substring(0,8),x+bW/2+4,H-8);
  });
  for(let i=0;i<=4;i++){
    const v=Math.round(max*i/4),y=pad.t+dH*(1-i/4);
    ctx.fillStyle="#3e4a3c";ctx.font="10px DM Sans";ctx.textAlign="right";ctx.fillText(v,pad.l-4,y+4);
  }
}
function drawPieChart(id,labels,values,colors){
  const c=document.getElementById(id);if(!c)return;
  const ctx=c.getContext("2d"),W=c.width=c.offsetWidth,H=c.height=c.offsetHeight;
  ctx.clearRect(0,0,W,H);
  const total=values.reduce((a,b)=>a+b,0);if(!total)return;
  const cx=W/2-40,cy=H/2,r=Math.min(cx,cy)-16;
  let angle=-Math.PI/2;
  values.forEach((val,i)=>{
    const sl=(val/total)*Math.PI*2;
    ctx.beginPath();ctx.moveTo(cx,cy);ctx.arc(cx,cy,r,angle,angle+sl);ctx.closePath();
    ctx.fillStyle=colors[i];ctx.fill();angle+=sl;
  });
  ctx.beginPath();ctx.arc(cx,cy,r*.58,0,Math.PI*2);ctx.fillStyle="#141714";ctx.fill();
  const lx=cx+r+20;
  labels.forEach((lbl,i)=>{
    const ly=cy-(labels.length-1)*14+i*30;
    ctx.fillStyle=colors[i];rRect(ctx,lx,ly-8,12,12,3);ctx.fill();
    ctx.fillStyle="#7a8878";ctx.font="11px DM Sans";ctx.textAlign="left";ctx.fillText(lbl,lx+16,ly+3);
    ctx.fillStyle="#e8ede6";ctx.font="600 11px DM Sans";ctx.fillText(Math.round(values[i]/total*100)+"%",lx+16,ly+15);
  });
}
function rRect(ctx,x,y,w,h,r){
  ctx.beginPath();ctx.moveTo(x+r,y);ctx.lineTo(x+w-r,y);ctx.quadraticCurveTo(x+w,y,x+w,y+r);
  ctx.lineTo(x+w,y+h-r);ctx.quadraticCurveTo(x+w,y+h,x+w-r,y+h);ctx.lineTo(x+r,y+h);
  ctx.quadraticCurveTo(x,y+h,x,y+h-r);ctx.lineTo(x,y+r);ctx.quadraticCurveTo(x,y,x+r,y);ctx.closePath();
}
</script>
"""

# ── Nav builder ──────────────────────────────────────────────────
def nav(active=""):
    links = [("Home","/",""),("Dashboard","/dashboard",""),("Food Search","/search",""),("Meal Log","/meal-log",""),("AI Advisor","/ai-advisor","")]
    items = "".join(f'<a href="{u}" class="nav-link{" active" if a==active else ""}">{n}</a>' for n,u,a in [(n,u,active) for n,u,_ in links])
    return f"""<nav class="navbar">
  <div class="container navbar-inner">
    <a href="/" class="nav-brand" style="text-decoration:none">Nutri<span>Fit</span></a>
    <div class="nav-links">
      {"".join(f'<a href="{u}" class="nav-link{" active" if u==("/"+active if active!="index" else "/") else ""}">{nm}</a>' for nm,u,_ in links)}
      <button class="nav-btn" id="nav-auth-btn">Dashboard</button>
    </div>
  </div>
</nav>
<script>
  document.getElementById("nav-auth-btn").onclick=()=>window.location.href=Auth.isLoggedIn()?"/dashboard":"/";
  if(Auth.isLoggedIn()&&window.location.pathname==="/"){{document.getElementById("nav-auth-btn").textContent="Dashboard →"}}
</script>"""

# ══════════════════════════════════════════════════════════════════
#  PAGE: INDEX
# ══════════════════════════════════════════════════════════════════
INDEX_HTML = """<!DOCTYPE html><html lang="en"><head>
<meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>NutriFit — Smart Nutrition</title>
{css}
</head><body>
{nav}
<main class="container">
  <section class="hero">
    <div class="hero-eyebrow"><span style="width:6px;height:6px;background:var(--accent);border-radius:50%"></span>AI-Powered Nutrition Intelligence</div>
    <h1 class="hero-title">Track food. <em>Train smarter.</em> Feel better.</h1>
    <p class="hero-sub">NutriFit combines smart calorie tracking, semantic food search, and an on-device AI nutritionist to keep you on track — privately and effortlessly.</p>
    <div class="hero-actions">
      <button class="btn btn-primary" onclick="openModal('signup-modal')" style="padding:14px 28px;font-size:1rem">Get started free</button>
      <button class="btn btn-ghost"   onclick="openModal('login-modal')"  style="padding:14px 28px;font-size:1rem">Sign in →</button>
    </div>
    <div class="hero-features">
      <div class="hero-feature"><span style="width:8px;height:8px;background:var(--accent);border-radius:50%"></span>Vector-based semantic food search</div>
      <div class="hero-feature"><span style="width:8px;height:8px;background:var(--accent);border-radius:50%"></span>Local AI with Ollama phi3</div>
      <div class="hero-feature"><span style="width:8px;height:8px;background:var(--accent);border-radius:50%"></span>Daily &amp; weekly nutrition scores</div>
      <div class="hero-feature"><span style="width:8px;height:8px;background:var(--accent);border-radius:50%"></span>Multi-member household tracking</div>
      <div class="hero-feature"><span style="width:8px;height:8px;background:var(--accent);border-radius:50%"></span>MCP tool layer for AI data access</div>
    </div>
  </section>
  <section class="section">
    <div class="grid-3">
      <div class="card" style="padding:28px"><div style="font-size:1.8rem;margin-bottom:14px">🔍</div><h3>Semantic Food Search</h3><p style="margin-top:8px">Vector embeddings let you find foods by meaning — get results even with different wording.</p></div>
      <div class="card" style="padding:28px"><div style="font-size:1.8rem;margin-bottom:14px">🤖</div><h3>On-Device AI Nutritionist</h3><p style="margin-top:8px">Powered by Ollama phi3 running locally. Get meal plans, daily suggestions — no cloud needed.</p></div>
      <div class="card" style="padding:28px"><div style="font-size:1.8rem;margin-bottom:14px">📊</div><h3>Smart Nutrition Scoring</h3><p style="margin-top:8px">Every day gets a nutrition score out of 100. Track macros and improve week over week.</p></div>
    </div>
  </section>
</main>

<!-- Login Modal -->
<div class="modal-overlay" id="login-modal">
  <div class="modal">
    <h2 style="margin-bottom:6px">Welcome back</h2><p style="margin-bottom:24px">Sign in to your NutriFit account</p>
    <div class="form-group"><label class="form-label">Username</label><input class="form-control" id="lu" placeholder="your_username" autocomplete="username"></div>
    <div class="form-group"><label class="form-label">Password</label><input class="form-control" id="lp" type="password" placeholder="••••••••" autocomplete="current-password"></div>
    <button class="btn btn-primary btn-full" id="login-btn" style="margin-top:8px">Sign In</button>
    <p style="text-align:center;margin-top:16px;font-size:.88rem">No account? <a href="#" onclick="closeModal('login-modal');openModal('signup-modal')">Sign up</a></p>
  </div>
</div>
<!-- Signup Modal -->
<div class="modal-overlay" id="signup-modal">
  <div class="modal">
    <h2 style="margin-bottom:6px">Create account</h2><p style="margin-bottom:24px">Start tracking your nutrition today</p>
    <div class="form-group"><label class="form-label">Username</label><input class="form-control" id="su" placeholder="choose_a_username" autocomplete="username"></div>
    <div class="form-group"><label class="form-label">Password</label><input class="form-control" id="sp" type="password" placeholder="••••••••" autocomplete="new-password"></div>
    <button class="btn btn-primary btn-full" id="signup-btn" style="margin-top:8px">Create Account</button>
    <p style="text-align:center;margin-top:16px;font-size:.88rem">Have account? <a href="#" onclick="closeModal('signup-modal');openModal('login-modal')">Sign in</a></p>
  </div>
</div>
{js}
<script>
document.getElementById("login-btn").addEventListener("click",async()=>{
  const btn=document.getElementById("login-btn"),u=document.getElementById("lu").value.trim(),p=document.getElementById("lp").value.trim();
  if(!u||!p)return toast("Fill in all fields","error");
  setLoading(btn,true);
  try{const d=await API.post("/api/login",{username:u,password:p});Auth.setUser({user_id:d.user_id,username:u});toast("Welcome back "+u+"!","success");setTimeout(()=>window.location.href="/dashboard",800);}
  catch(e){toast(e.message,"error");}finally{setLoading(btn,false);}
});
document.getElementById("signup-btn").addEventListener("click",async()=>{
  const btn=document.getElementById("signup-btn"),u=document.getElementById("su").value.trim(),p=document.getElementById("sp").value.trim();
  if(!u||!p)return toast("Fill in all fields","error");
  setLoading(btn,true);
  try{await API.post("/api/signup",{username:u,password:p});const d=await API.post("/api/login",{username:u,password:p});Auth.setUser({user_id:d.user_id,username:u});toast("Account created!","success");setTimeout(()=>window.location.href="/dashboard",900);}
  catch(e){toast(e.message,"error");}finally{setLoading(btn,false);}
});
["lu","lp"].forEach(id=>document.getElementById(id).addEventListener("keydown",e=>{if(e.key==="Enter")document.getElementById("login-btn").click();}));
</script>
</body></html>"""

# ══════════════════════════════════════════════════════════════════
#  PAGE: DASHBOARD
# ══════════════════════════════════════════════════════════════════
DASHBOARD_HTML = """<!DOCTYPE html><html lang="en"><head>
<meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Dashboard — NutriFit</title>{css}</head><body>
<nav class="navbar">
  <div class="container navbar-inner">
    <a href="/" class="nav-brand" style="text-decoration:none">Nutri<span>Fit</span></a>
    <div class="nav-links">
      <a href="/" class="nav-link">Home</a><a href="/dashboard" class="nav-link active">Dashboard</a>
      <a href="/search" class="nav-link">Food Search</a><a href="/meal-log" class="nav-link">Meal Log</a>
      <a href="/ai-advisor" class="nav-link">AI Advisor</a>
      <button class="nav-btn" id="logout-btn">Sign Out</button>
    </div>
  </div>
</nav>
<div class="app-layout">
  <aside class="sidebar">
    <div class="sidebar-section">
      <div class="sidebar-label">Overview</div>
      <button class="sidebar-link active" data-tab="daily"><span>📊</span> Daily Summary</button>
      <button class="sidebar-link" data-tab="weekly"><span>📅</span> Weekly Report</button>
    </div>
    <div class="sidebar-section">
      <div class="sidebar-label">Manage</div>
      <button class="sidebar-link" data-tab="log-meal"><span>🍽️</span> Log Meal</button>
      <button class="sidebar-link" data-tab="add-member"><span>👤</span> Add Member</button>
      <button class="sidebar-link" data-tab="meal-plan"><span>🗓️</span> AI Meal Plan</button>
    </div>
    <div style="margin-top:auto;padding:16px 12px;border-top:1px solid var(--border);font-size:.78rem;color:var(--text-muted)">Signed in as<br><strong id="sb-user" style="color:var(--text)"></strong></div>
  </aside>

  <main class="main-content">
    <!-- ── Daily ── -->
    <div id="tab-daily">
      <div style="display:flex;align-items:center;justify-content:space-between;margin-bottom:28px;flex-wrap:wrap;gap:12px">
        <div><h2 style="margin-bottom:4px">Daily Summary</h2><p id="today-date"></p></div>
        <div style="display:flex;align-items:center;gap:10px">
          <label style="font-size:.82rem;color:var(--text-muted)">Calorie Goal</label>
          <input id="calorie-goal" type="number" class="form-control" value="2000" min="500" max="5000" step="50" style="width:110px">
        </div>
      </div>
      <div class="stat-grid">
        <div class="stat-tile accent-tile"><div class="label">Calories</div><div class="value" id="s-cal">—</div><div class="unit">kcal today</div></div>
        <div class="stat-tile"><div class="label">Protein</div><div class="value" id="s-pro">—</div><div class="unit">grams</div></div>
        <div class="stat-tile"><div class="label">Carbs</div><div class="value" id="s-car">—</div><div class="unit">grams</div></div>
        <div class="stat-tile"><div class="label">Fat</div><div class="value" id="s-fat">—</div><div class="unit">grams</div></div>
      </div>
      <div class="card mt-16" style="padding:20px 24px">
        <div style="display:flex;justify-content:space-between;margin-bottom:8px">
          <span style="font-size:.85rem;color:var(--text-muted)">Calorie Progress</span>
          <span id="prog-label" style="font-size:.85rem;color:var(--text-muted)">0 / 2000 kcal</span>
        </div>
        <div class="progress-bar-wrap"><div class="progress-bar-fill" id="cal-prog" style="width:0%"></div></div>
        <div style="display:flex;justify-content:space-between;margin-top:8px">
          <span style="font-size:.8rem;color:var(--text-muted)">Consumed</span>
          <span style="font-size:.8rem;color:var(--text-muted)">Remaining: <span id="rem-cals">—</span> kcal</span>
        </div>
        <div style="margin-top:16px;display:flex;align-items:center;gap:12px">
          <span style="font-size:.85rem;color:var(--text-muted)">Nutrition Score:</span>
          <span class="score-badge green" id="score-badge">—</span>
        </div>
      </div>
      <div class="grid-2 mt-16">
        <div class="card"><h4 style="margin-bottom:12px">Calories by Food</h4><div style="height:180px"><canvas id="chart-bar" style="width:100%;height:100%"></canvas></div></div>
        <div class="card"><h4 style="margin-bottom:12px">Macro Distribution</h4><div style="height:180px"><canvas id="chart-pie" style="width:100%;height:100%"></canvas></div></div>
      </div>
      <div class="card mt-16">
        <div class="card-header"><h4>Today's Food Log</h4></div>
        <div id="food-log-wrap"><div style="text-align:center;padding:32px;color:var(--text-muted)">No meals logged yet today</div></div>
      </div>
      <div class="card mt-16">
        <div class="card-header"><h4>AI Meal Suggestion</h4></div>
        <button class="btn btn-ghost btn-sm" id="btn-suggest">✨ Suggest next meal</button>
        <div id="suggestion-box" style="display:none;margin-top:16px"><div class="ai-box-header">🤖 NutriFit AI</div><div class="ai-box" id="suggestion-text"></div></div>
      </div>
    </div>

    <!-- ── Weekly ── -->
    <div id="tab-weekly" style="display:none">
      <h2 style="margin-bottom:6px">Weekly Report</h2><p style="color:var(--text-muted);margin-bottom:28px">Last 7 days</p>
      <div class="stat-grid">
        <div class="stat-tile"><div class="label">Total Calories</div><div class="value" id="w-cal">—</div></div>
        <div class="stat-tile"><div class="label">Total Protein</div><div class="value" id="w-pro">—</div></div>
        <div class="stat-tile"><div class="label">Total Carbs</div><div class="value" id="w-car">—</div></div>
        <div class="stat-tile"><div class="label">Total Fat</div><div class="value" id="w-fat">—</div></div>
      </div>
      <div class="card mt-16"><h4 style="margin-bottom:12px">Daily Calories (7-day)</h4><div style="height:200px"><canvas id="chart-weekly" style="width:100%;height:100%"></canvas></div></div>
      <div class="card mt-16">
        <div class="card-header"><h4>AI Weekly Analysis</h4></div>
        <button class="btn btn-ghost btn-sm" id="btn-wk-analysis">📈 Generate analysis</button>
        <div id="wk-analysis-box" style="display:none;margin-top:16px"><div class="ai-box-header">🤖 NutriFit AI</div><div class="ai-box" id="wk-analysis-text"></div></div>
      </div>
    </div>

    <!-- ── Log Meal ── -->
    <div id="tab-log-meal" style="display:none">
      <h2 style="margin-bottom:6px">Log a Meal</h2><p style="color:var(--text-muted);margin-bottom:28px">Search food and log it to your daily intake</p>
      <div class="grid-2">
        <div class="card">
          <h4 style="margin-bottom:20px">Meal Details</h4>
          <div class="form-group"><label class="form-label">Member</label><select class="form-control" id="meal-member"></select></div>
          <div class="form-row">
            <div class="form-group"><label class="form-label">Meal Type</label><select class="form-control" id="meal-type"><option>Breakfast</option><option>Lunch</option><option>Snacks</option><option>Dinner</option></select></div>
            <div class="form-group"><label class="form-label">Date</label><input class="form-control" type="date" id="meal-date"></div>
          </div>
          <div class="form-group"><label class="form-label">Quantity</label><input class="form-control" type="number" id="meal-qty" value="1" min="1"></div>
          <div class="form-group"><label class="form-label">Selected Food</label>
            <div id="sel-food" style="background:var(--surface-2);border:1px solid var(--border);border-radius:var(--radius-sm);padding:12px 14px;color:var(--text-muted);min-height:44px">None selected</div>
          </div>
          <button class="btn btn-primary btn-full" id="btn-log-meal">Log Meal</button>
        </div>
        <div class="card">
          <h4 style="margin-bottom:16px">Search Food</h4>
          <div style="display:flex;gap:8px;margin-bottom:16px">
            <input class="form-control" id="food-q" placeholder="e.g. banana, grilled chicken…">
            <button class="btn btn-ghost btn-sm" id="btn-fsearch">Search</button>
          </div>
          <div id="fsearch-results"></div>
          <div style="border-top:1px solid var(--border);margin-top:16px;padding-top:16px">
            <p style="font-size:.82rem;margin-bottom:8px">Not found? AI estimates nutrition:</p>
            <div style="display:flex;gap:8px">
              <input class="form-control" id="ai-food-nm" placeholder="e.g. Masala Dosa">
              <button class="btn btn-ghost btn-sm" id="btn-ai-est">✨ AI</button>
            </div>
          </div>
        </div>
      </div>
    </div>

    <!-- ── Add Member ── -->
    <div id="tab-add-member" style="display:none">
      <h2 style="margin-bottom:6px">Add Family Member</h2><p style="color:var(--text-muted);margin-bottom:28px">Track nutrition for multiple people</p>
      <div class="card" style="max-width:480px">
        <div class="form-group"><label class="form-label">Full Name</label><input class="form-control" id="m-name" placeholder="e.g. John Doe"></div>
        <div class="form-row">
          <div class="form-group"><label class="form-label">Age</label><input class="form-control" type="number" id="m-age" min="1" max="120" placeholder="25"></div>
          <div class="form-group"><label class="form-label">Gender</label><select class="form-control" id="m-gender"><option>Male</option><option>Female</option></select></div>
        </div>
        <div class="form-row">
          <div class="form-group"><label class="form-label">Weight (kg)</label><input class="form-control" type="number" id="m-weight" min="1" placeholder="70"></div>
          <div class="form-group"><label class="form-label">Height (cm)</label><input class="form-control" type="number" id="m-height" min="50" placeholder="170"></div>
        </div>
        <button class="btn btn-primary btn-full" id="btn-add-member">Add Member</button>
      </div>
    </div>

    <!-- ── AI Meal Plan ── -->
    <div id="tab-meal-plan" style="display:none">
      <h2 style="margin-bottom:6px">AI Meal Planner</h2><p style="color:var(--text-muted);margin-bottom:28px">Generate a personalised one-day meal plan</p>
      <div class="grid-2">
        <div class="card">
          <div class="form-group"><label class="form-label">Goal</label><select class="form-control" id="plan-goal"><option>Weight Loss</option><option>Muscle Gain</option><option>Maintain Weight</option></select></div>
          <div class="form-group"><label class="form-label">Diet Type</label><select class="form-control" id="plan-diet"><option>Vegetarian</option><option>Non-Vegetarian</option><option>Vegan</option></select></div>
          <button class="btn btn-primary btn-full" id="btn-gen-plan">✨ Generate Meal Plan</button>
        </div>
        <div class="card">
          <div id="plan-result" style="display:none"><div class="ai-box-header">🤖 Your Meal Plan</div><div class="ai-box" id="plan-text"></div></div>
          <div id="plan-empty" style="text-align:center;padding:32px;color:var(--text-muted)">Click Generate to create your plan</div>
        </div>
      </div>
    </div>
  </main>
</div>
{js}
<script>
if(!requireAuth()){}
const UID=Auth.getUserId();
document.getElementById("sb-user").textContent=Auth.getUser()?.username||"";
document.getElementById("today-date").textContent=new Date().toLocaleDateString("en-US",{weekday:"long",year:"numeric",month:"long",day:"numeric"});
document.getElementById("meal-date").value=new Date().toISOString().split("T")[0];
document.getElementById("logout-btn").addEventListener("click",()=>{Auth.clearUser();window.location.href="/";});

/* Tab switching */
const allTabs=["daily","weekly","log-meal","add-member","meal-plan"];
function switchTab(id){
  allTabs.forEach(t=>document.getElementById("tab-"+t).style.display=t===id?"block":"none");
  document.querySelectorAll(".sidebar-link[data-tab]").forEach(l=>l.classList.toggle("active",l.dataset.tab===id));
  if(id==="daily")loadDaily();
  if(id==="weekly")loadWeekly();
  if(id==="log-meal")loadMembers();
}
document.querySelectorAll(".sidebar-link[data-tab]").forEach(l=>l.addEventListener("click",()=>switchTab(l.dataset.tab)));

/* ── Daily ── */
let dailyTotals={};
async function loadDaily(){
  const data=await API.get(`/api/summary/daily?user_id=${UID}`);
  dailyTotals=data.totals;
  const goal=parseInt(document.getElementById("calorie-goal").value)||2000;
  document.getElementById("s-cal").textContent=fmt(dailyTotals.calories);
  document.getElementById("s-pro").textContent=fmt(dailyTotals.protein);
  document.getElementById("s-car").textContent=fmt(dailyTotals.carbs);
  document.getElementById("s-fat").textContent=fmt(dailyTotals.fat);
  document.getElementById("prog-label").textContent=`${fmt(dailyTotals.calories)} / ${fmt(goal)} kcal`;
  document.getElementById("rem-cals").textContent=fmt(Math.max(goal-dailyTotals.calories,0));
  setProgress(document.getElementById("cal-prog"),dailyTotals.calories/goal);
  const sc=calcScore(dailyTotals,goal);
  const sb=document.getElementById("score-badge");sb.textContent=`${sc.score}/100 — ${sc.label}`;sb.className=`score-badge ${sc.color}`;
  const w=document.getElementById("food-log-wrap");
  if(data.items.length){
    w.innerHTML=`<div style="overflow-x:auto"><table class="data-table"><thead><tr><th>Meal</th><th>Food</th><th>Cal/unit</th><th>Qty</th><th>Total Cal</th><th>Protein</th><th>Carbs</th><th>Fat</th></tr></thead><tbody>${data.items.map(i=>`<tr><td><span class="meal-${i.meal_type.toLowerCase()}" style="font-weight:600">${i.meal_type}</span></td><td class="food-name">${i.food_name}</td><td>${i.calories}</td><td>${i.quantity}</td><td class="cal">${fmt(i.total_calories)}</td><td>${i.protein}g</td><td>${i.carbs}g</td><td>${i.fat}g</td></tr>`).join("")}</tbody></table></div>`;
  }else{w.innerHTML=`<div style="text-align:center;padding:32px;color:var(--text-muted)">No meals logged today</div>`;}
  setTimeout(()=>{
    if(data.items.length){
      drawBarChart("chart-bar",data.items.map(i=>i.food_name),data.items.map(i=>i.total_calories));
      drawPieChart("chart-pie",["Protein","Carbs","Fat"],[dailyTotals.protein,dailyTotals.carbs,dailyTotals.fat],["#5ea8ff","#f5c842","#ff5e5e"]);
    }
  },60);
}
function calcScore(t,goal){
  let s=100;if(t.calories>goal)s-=20;if(t.protein<50)s-=20;if(t.carbs>300)s-=15;if(t.fat>70)s-=15;s=Math.max(s,0);
  const map=[[90,"Excellent","green"],[70,"Good","blue"],[50,"Fair","yellow"],[0,"Poor","red"]];
  const [,l,c]=map.find(([x])=>s>=x);return{score:s,label:l,color:c};
}
document.getElementById("calorie-goal").addEventListener("change",loadDaily);
document.getElementById("btn-suggest").addEventListener("click",async()=>{
  const btn=document.getElementById("btn-suggest");setLoading(btn,true);
  try{const d=await API.post("/api/ai/meal-suggestion",{totals:dailyTotals});document.getElementById("suggestion-text").textContent=d.response;document.getElementById("suggestion-box").style.display="block";}
  catch(e){toast(e.message,"error");}finally{setLoading(btn,false);}
});

/* ── Weekly ── */
let wkAgg={};
async function loadWeekly(){
  const data=await API.get(`/api/summary/weekly?user_id=${UID}`);
  wkAgg=data.reduce((a,d)=>({calories:(a.calories||0)+d.calories,protein:(a.protein||0)+d.protein,carbs:(a.carbs||0)+d.carbs,fat:(a.fat||0)+d.fat}),{});
  document.getElementById("w-cal").textContent=fmt(wkAgg.calories);
  document.getElementById("w-pro").textContent=fmt(wkAgg.protein)+"g";
  document.getElementById("w-car").textContent=fmt(wkAgg.carbs)+"g";
  document.getElementById("w-fat").textContent=fmt(wkAgg.fat)+"g";
  setTimeout(()=>drawBarChart("chart-weekly",data.map(d=>d.date.slice(5)),data.map(d=>d.calories),"#5ea8ff"),60);
}
document.getElementById("btn-wk-analysis").addEventListener("click",async()=>{
  const btn=document.getElementById("btn-wk-analysis");setLoading(btn,true);
  try{const d=await API.post("/api/ai/weekly-analysis",wkAgg);document.getElementById("wk-analysis-text").textContent=d.response;document.getElementById("wk-analysis-box").style.display="block";}
  catch(e){toast(e.message,"error");}finally{setLoading(btn,false);}
});

/* ── Log Meal ── */
let selFoodId=null;
async function loadMembers(){
  const members=await API.get(`/api/members?user_id=${UID}`);
  const sel=document.getElementById("meal-member");
  sel.innerHTML=members.length?members.map(m=>`<option value="${m.member_id}">${m.name}</option>`).join(""):`<option value="">No members — add one</option>`;
}
document.getElementById("btn-fsearch").addEventListener("click",async()=>{
  const q=document.getElementById("food-q").value.trim();if(!q)return;
  const r=document.getElementById("fsearch-results");r.innerHTML=`<div style="text-align:center;padding:12px"><span class="spinner"></span></div>`;
  const foods=await API.get(`/api/food/search?q=${encodeURIComponent(q)}`);
  r.innerHTML=foods.length
    ?`<div class="food-grid" style="margin-top:8px">${foods.map(f=>`<div class="food-card" onclick="selFood(${f.food_id},'${f.food_name.replace(/'/g,"\\'")}',this)"><div class="fc-name">${f.food_name}</div><div class="fc-cals">${f.calories} <span style="font-size:.75rem;font-weight:400">kcal</span></div><div class="fc-macros"><span>P:${f.protein}g</span><span>C:${f.carbs}g</span><span>F:${f.fat}g</span></div></div>`).join("")}</div>`
    :`<p style="color:var(--text-muted);font-size:.88rem;margin-top:8px">No results. Try AI estimation below.</p>`;
});
function selFood(id,name,el){
  selFoodId=id;const d=document.getElementById("sel-food");d.textContent=name;d.style.color="var(--accent)";
  document.querySelectorAll(".food-card").forEach(c=>c.classList.remove("selected"));el.classList.add("selected");
}
document.getElementById("btn-ai-est").addEventListener("click",async()=>{
  const nm=document.getElementById("ai-food-nm").value.trim();if(!nm)return;
  const btn=document.getElementById("btn-ai-est");setLoading(btn,true);
  try{const f=await API.post("/api/food/estimate",{food_name:nm});selFoodId=f.food_id;const d=document.getElementById("sel-food");d.textContent=`${f.food_name} (${f.calories} kcal)`;d.style.color="var(--accent)";toast(`AI: ${f.calories} kcal, P:${f.protein}g C:${f.carbs}g F:${f.fat}g`,"success",5000);}
  catch(e){toast(e.message,"error");}finally{setLoading(btn,false);}
});
document.getElementById("btn-log-meal").addEventListener("click",async()=>{
  if(!selFoodId)return toast("Select a food first","error");
  const btn=document.getElementById("btn-log-meal");setLoading(btn,true);
  try{await API.post("/api/meals",{member_id:document.getElementById("meal-member").value,meal_type:document.getElementById("meal-type").value,meal_date:document.getElementById("meal-date").value,food_id:selFoodId,quantity:parseInt(document.getElementById("meal-qty").value)});toast("Meal logged!","success");selFoodId=null;document.getElementById("sel-food").textContent="None selected";document.getElementById("sel-food").style.color="var(--text-muted)";}
  catch(e){toast(e.message,"error");}finally{setLoading(btn,false);}
});
document.getElementById("food-q").addEventListener("keydown",e=>{if(e.key==="Enter")document.getElementById("btn-fsearch").click();});

/* ── Add Member ── */
document.getElementById("btn-add-member").addEventListener("click",async()=>{
  const nm=document.getElementById("m-name").value.trim();if(!nm)return toast("Name required","error");
  const btn=document.getElementById("btn-add-member");setLoading(btn,true);
  try{await API.post("/api/members",{user_id:UID,name:nm,age:document.getElementById("m-age").value,gender:document.getElementById("m-gender").value,weight:document.getElementById("m-weight").value,height:document.getElementById("m-height").value});toast(`${nm} added!`,"success");document.getElementById("m-name").value="";}
  catch(e){toast(e.message,"error");}finally{setLoading(btn,false);}
});

/* ── Meal Plan ── */
document.getElementById("btn-gen-plan").addEventListener("click",async()=>{
  const btn=document.getElementById("btn-gen-plan");setLoading(btn,true);
  try{const d=await API.post("/api/ai/meal-plan",{goal:document.getElementById("plan-goal").value,diet:document.getElementById("plan-diet").value});document.getElementById("plan-text").textContent=d.response;document.getElementById("plan-result").style.display="block";document.getElementById("plan-empty").style.display="none";}
  catch(e){toast(e.message,"error");}finally{setLoading(btn,false);}
});

loadDaily();
</script>
</body></html>"""

# ══════════════════════════════════════════════════════════════════
#  PAGE: FOOD SEARCH
# ══════════════════════════════════════════════════════════════════
SEARCH_HTML = """<!DOCTYPE html><html lang="en"><head>
<meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Food Search — NutriFit</title>{css}</head><body>
<nav class="navbar">
  <div class="container navbar-inner">
    <a href="/" class="nav-brand" style="text-decoration:none">Nutri<span>Fit</span></a>
    <div class="nav-links">
      <a href="/" class="nav-link">Home</a><a href="/dashboard" class="nav-link">Dashboard</a>
      <a href="/search" class="nav-link active">Food Search</a><a href="/meal-log" class="nav-link">Meal Log</a>
      <a href="/ai-advisor" class="nav-link">AI Advisor</a>
      <button class="nav-btn" id="nav-auth-btn">Dashboard</button>
    </div>
  </div>
</nav>
<main class="container section">
  <div style="max-width:700px;margin:0 auto 48px">
    <div class="hero-eyebrow" style="margin-bottom:16px"><span style="width:6px;height:6px;background:var(--accent);border-radius:50%"></span>Vector-Powered Semantic Search</div>
    <h1 style="margin-bottom:12px">Find <em style="font-style:italic;color:var(--accent)">any</em> food instantly</h1>
    <p style="font-size:1.05rem;color:var(--text-muted);margin-bottom:32px">Our AI search understands meaning — type "high protein breakfast" or "low fat snack" to find best matches.</p>
    <div style="display:flex;gap:10px">
      <input class="form-control" id="search-input" placeholder="Search food by name or description…" style="font-size:1rem;padding:14px 18px" autocomplete="off">
      <button class="btn btn-primary" id="btn-search" style="padding:14px 24px;font-size:1rem">Search</button>
    </div>
    <div style="display:flex;gap:8px;flex-wrap:wrap;margin-top:14px">
      <span style="font-size:.78rem;color:var(--text-faint);align-self:center">Try:</span>
      <button class="btn btn-ghost btn-sm" onclick="qs('chicken breast')">Chicken breast</button>
      <button class="btn btn-ghost btn-sm" onclick="qs('brown rice')">Brown rice</button>
      <button class="btn btn-ghost btn-sm" onclick="qs('banana')">Banana</button>
      <button class="btn btn-ghost btn-sm" onclick="qs('greek yogurt')">Greek yogurt</button>
      <button class="btn btn-ghost btn-sm" onclick="qs('almonds')">Almonds</button>
    </div>
  </div>
  <div id="results-area" style="min-height:200px">
    <div style="text-align:center;padding:48px 20px;color:var(--text-muted)"><div style="font-size:2.5rem;margin-bottom:12px">🔍</div><h3 style="color:var(--text);margin-bottom:6px">Search for a food item</h3><p>Powered by vector embeddings for semantic matching</p></div>
  </div>
  <div class="card mt-16" style="max-width:700px;margin:32px auto 0">
    <h4 style="margin-bottom:8px">Can't find what you're looking for?</h4>
    <p style="margin-bottom:16px">AI will estimate the nutrition using a local language model.</p>
    <div style="display:flex;gap:10px">
      <input class="form-control" id="new-food" placeholder="Enter food name (e.g. Masala Dosa)">
      <button class="btn btn-ghost" id="btn-ai-add" style="white-space:nowrap">✨ AI Estimate</button>
    </div>
    <div id="ai-est-result" style="display:none;margin-top:16px"></div>
  </div>
</main>
{js}
<script>
document.getElementById("nav-auth-btn").onclick=()=>window.location.href=Auth.isLoggedIn()?"/dashboard":"/";
function qs(t){document.getElementById("search-input").value=t;doSearch(t);}
async function doSearch(q){
  if(!q)return;
  const area=document.getElementById("results-area");
  area.innerHTML=`<div style="text-align:center;padding:40px"><span class="spinner" style="width:32px;height:32px;border-width:3px"></span><p style="margin-top:12px;color:var(--text-muted)">Searching with vector embeddings…</p></div>`;
  try{
    const foods=await API.get(`/api/food/search?q=${encodeURIComponent(q)}`);
    if(!foods.length){area.innerHTML=`<div style="text-align:center;padding:48px 20px;color:var(--text-muted)"><div style="font-size:2.5rem;margin-bottom:12px">🤔</div><h3 style="color:var(--text)">No results found</h3><p>Try a different search or use AI estimation below.</p></div>`;return;}
    area.innerHTML=`
      <div style="display:flex;align-items:center;justify-content:space-between;margin-bottom:16px">
        <p style="color:var(--text-muted);font-size:.88rem">${foods.length} result${foods.length!==1?"s":""} for "<strong style="color:var(--text)">${q}</strong>"</p>
        <span style="font-size:.75rem;color:var(--text-faint)">Ranked by semantic similarity</span>
      </div>
      <div style="overflow-x:auto"><table class="data-table">
        <thead><tr><th>Food</th><th>Calories/unit</th><th>Protein</th><th>Carbs</th><th>Fat</th><th>Match</th><th></th></tr></thead>
        <tbody>${foods.map(f=>`<tr>
          <td class="food-name">${f.food_name}</td><td class="cal">${f.calories} kcal</td>
          <td>${f.protein}g</td><td>${f.carbs}g</td><td>${f.fat}g</td>
          <td><div class="progress-bar-wrap" style="width:80px"><div class="progress-bar-fill" style="width:${Math.round((f.similarity||.7)*100)}%"></div></div></td>
          <td><button class="btn btn-ghost btn-sm" onclick="logIt(${f.food_id},'${f.food_name.replace(/'/g,"\\'")}')">+ Log</button></td>
        </tr>`).join("")}</tbody>
      </table></div>`;
  }catch(e){area.innerHTML=`<div style="text-align:center;padding:40px;color:var(--red)">⚠️ ${e.message}</div>`;}
}
document.getElementById("btn-search").addEventListener("click",()=>doSearch(document.getElementById("search-input").value.trim()));
document.getElementById("search-input").addEventListener("keydown",e=>{if(e.key==="Enter")document.getElementById("btn-search").click();});
function logIt(id,name){if(!Auth.isLoggedIn()){toast("Sign in to log meals","info");return;}window.location.href=`/meal-log`;}
document.getElementById("btn-ai-add").addEventListener("click",async()=>{
  const nm=document.getElementById("new-food").value.trim();if(!nm)return toast("Enter a food name","error");
  const btn=document.getElementById("btn-ai-add"),res=document.getElementById("ai-est-result");
  setLoading(btn,true);res.style.display="none";
  try{
    const f=await API.post("/api/food/estimate",{food_name:nm});
    res.innerHTML=`<div class="card" style="background:var(--accent-bg);border-color:rgba(181,242,61,.2)"><div class="ai-box-header" style="margin-bottom:12px">✅ Added to database</div><div style="font-size:1rem;font-weight:600;color:var(--text);margin-bottom:12px">${f.food_name}</div><div class="stat-grid" style="grid-template-columns:repeat(4,1fr)"><div class="stat-tile" style="padding:12px"><div class="label">Calories</div><div class="value" style="font-size:1.4rem">${f.calories}</div></div><div class="stat-tile" style="padding:12px"><div class="label">Protein</div><div class="value" style="font-size:1.4rem">${f.protein}g</div></div><div class="stat-tile" style="padding:12px"><div class="label">Carbs</div><div class="value" style="font-size:1.4rem">${f.carbs}g</div></div><div class="stat-tile" style="padding:12px"><div class="label">Fat</div><div class="value" style="font-size:1.4rem">${f.fat}g</div></div></div></div>`;
    res.style.display="block";toast("Food added and indexed!","success");
  }catch(e){res.innerHTML=`<p style="color:var(--red)">⚠️ ${e.message}</p>`;res.style.display="block";}
  finally{setLoading(btn,false);}
});
</script>
</body></html>"""

# ══════════════════════════════════════════════════════════════════
#  PAGE: MEAL LOG
# ══════════════════════════════════════════════════════════════════
MEAL_LOG_HTML = """<!DOCTYPE html><html lang="en"><head>
<meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Meal Log — NutriFit</title>{css}</head><body>
<nav class="navbar">
  <div class="container navbar-inner">
    <a href="/" class="nav-brand" style="text-decoration:none">Nutri<span>Fit</span></a>
    <div class="nav-links">
      <a href="/" class="nav-link">Home</a><a href="/dashboard" class="nav-link">Dashboard</a>
      <a href="/search" class="nav-link">Food Search</a><a href="/meal-log" class="nav-link active">Meal Log</a>
      <a href="/ai-advisor" class="nav-link">AI Advisor</a>
      <button class="nav-btn" id="nav-auth-btn">Dashboard</button>
    </div>
  </div>
</nav>
<main class="container section">
  <div style="max-width:960px;margin:0 auto">
    <h2 style="margin-bottom:6px">Log a Meal</h2>
    <p style="color:var(--text-muted);margin-bottom:28px">Search for food using AI-powered semantic search and log it instantly</p>
    <div class="grid-2">
      <div class="card">
        <h4 style="margin-bottom:20px">Meal Details</h4>
        <div class="form-group"><label class="form-label">Member</label><select class="form-control" id="meal-member"><option>Loading…</option></select></div>
        <div class="form-row">
          <div class="form-group"><label class="form-label">Meal Type</label><select class="form-control" id="meal-type"><option>Breakfast</option><option>Lunch</option><option>Snacks</option><option>Dinner</option></select></div>
          <div class="form-group"><label class="form-label">Date</label><input class="form-control" type="date" id="meal-date"></div>
        </div>
        <div class="form-group"><label class="form-label">Quantity</label><input class="form-control" type="number" id="meal-qty" value="1" min="1"></div>
        <div class="form-group"><label class="form-label">Selected Food</label>
          <div id="sel-food" style="background:var(--surface-2);border:1px solid var(--border);border-radius:var(--radius-sm);padding:12px 14px;color:var(--text-muted);min-height:44px">None selected</div>
        </div>
        <button class="btn btn-primary btn-full" id="btn-log">Log Meal</button>
      </div>
      <div class="card">
        <h4 style="margin-bottom:16px">Search Food</h4>
        <div style="display:flex;gap:8px;margin-bottom:16px">
          <input class="form-control" id="fq" placeholder="e.g. boiled egg, dal rice…">
          <button class="btn btn-ghost btn-sm" id="btn-s">Search</button>
        </div>
        <div id="sresults"></div>
        <div style="border-top:1px solid var(--border);margin-top:16px;padding-top:16px">
          <p style="font-size:.82rem;margin-bottom:8px">Not found? AI estimates nutrition:</p>
          <div style="display:flex;gap:8px">
            <input class="form-control" id="ai-nm" placeholder="e.g. Palak Paneer">
            <button class="btn btn-ghost btn-sm" id="btn-ai">✨ AI</button>
          </div>
        </div>
      </div>
    </div>
    <div class="card mt-16">
      <h4 style="margin-bottom:16px">Today's Meals</h4>
      <div id="today-log"><div style="text-align:center;padding:28px;color:var(--text-muted)">No meals logged today</div></div>
    </div>
  </div>
</main>
{js}
<script>
if(!requireAuth()){}
const UID=Auth.getUserId();
document.getElementById("nav-auth-btn").onclick=()=>window.location.href=Auth.isLoggedIn()?"/dashboard":"/";
document.getElementById("meal-date").value=new Date().toISOString().split("T")[0];
let selFoodId=null;

(async()=>{
  const m=await API.get(`/api/members?user_id=${UID}`);
  document.getElementById("meal-member").innerHTML=m.length?m.map(x=>`<option value="${x.member_id}">${x.name}</option>`).join(""):`<option value="">No members — add from Dashboard</option>`;
})();

async function loadTodayLog(){
  const data=await API.get(`/api/summary/daily?user_id=${UID}`);
  const w=document.getElementById("today-log");
  if(!data.items.length){w.innerHTML=`<div style="text-align:center;padding:28px;color:var(--text-muted)">No meals logged today</div>`;return;}
  w.innerHTML=`<div style="overflow-x:auto"><table class="data-table"><thead><tr><th>Meal</th><th>Food</th><th>Qty</th><th>Calories</th></tr></thead><tbody>${data.items.map(i=>`<tr><td><span class="meal-${i.meal_type.toLowerCase()}" style="font-weight:600">${i.meal_type}</span></td><td class="food-name">${i.food_name}</td><td>${i.quantity}</td><td class="cal">${Math.round(i.total_calories)} kcal</td></tr>`).join("")}</tbody></table></div>`;
}
loadTodayLog();

document.getElementById("btn-s").addEventListener("click",async()=>{
  const q=document.getElementById("fq").value.trim();if(!q)return;
  const r=document.getElementById("sresults");r.innerHTML=`<div style="text-align:center;padding:12px"><span class="spinner"></span></div>`;
  const foods=await API.get(`/api/food/search?q=${encodeURIComponent(q)}`);
  r.innerHTML=foods.length
    ?`<div style="display:flex;flex-direction:column;gap:8px">${foods.slice(0,5).map(f=>`<div class="food-card" onclick="selF(${f.food_id},'${f.food_name.replace(/'/g,"\\'")}',this)"><div style="display:flex;justify-content:space-between;align-items:center"><span class="fc-name">${f.food_name}</span><span class="fc-cals" style="font-size:1rem">${f.calories} kcal</span></div><div class="fc-macros"><span>P:${f.protein}g</span><span>C:${f.carbs}g</span><span>F:${f.fat}g</span></div></div>`).join("")}</div>`
    :`<p style="color:var(--text-muted);font-size:.88rem;margin-top:8px">No results found.</p>`;
});
function selF(id,name,el){
  selFoodId=id;const d=document.getElementById("sel-food");d.textContent=name;d.style.color="var(--accent)";
  document.querySelectorAll(".food-card").forEach(c=>c.classList.remove("selected"));el.classList.add("selected");
}
document.getElementById("btn-ai").addEventListener("click",async()=>{
  const nm=document.getElementById("ai-nm").value.trim();if(!nm)return;
  const btn=document.getElementById("btn-ai");setLoading(btn,true);
  try{const f=await API.post("/api/food/estimate",{food_name:nm});selFoodId=f.food_id;const d=document.getElementById("sel-food");d.textContent=`${f.food_name} (${f.calories} kcal)`;d.style.color="var(--accent)";toast("AI estimated and saved!","success");}
  catch(e){toast(e.message,"error");}finally{setLoading(btn,false);}
});
document.getElementById("btn-log").addEventListener("click",async()=>{
  if(!selFoodId)return toast("Select a food first","error");
  const btn=document.getElementById("btn-log");setLoading(btn,true);
  try{await API.post("/api/meals",{member_id:document.getElementById("meal-member").value,meal_type:document.getElementById("meal-type").value,meal_date:document.getElementById("meal-date").value,food_id:selFoodId,quantity:parseInt(document.getElementById("meal-qty").value)});toast("Meal logged!","success");selFoodId=null;document.getElementById("sel-food").textContent="None selected";document.getElementById("sel-food").style.color="var(--text-muted)";loadTodayLog();}
  catch(e){toast(e.message,"error");}finally{setLoading(btn,false);}
});
document.getElementById("fq").addEventListener("keydown",e=>{if(e.key==="Enter")document.getElementById("btn-s").click();});
</script>
</body></html>"""

# ══════════════════════════════════════════════════════════════════
#  PAGE: AI ADVISOR
# ══════════════════════════════════════════════════════════════════
AI_ADVISOR_HTML = """<!DOCTYPE html><html lang="en"><head>
<meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>AI Advisor — NutriFit</title>{css}</head><body>
<nav class="navbar">
  <div class="container navbar-inner">
    <a href="/" class="nav-brand" style="text-decoration:none">Nutri<span>Fit</span></a>
    <div class="nav-links">
      <a href="/" class="nav-link">Home</a><a href="/dashboard" class="nav-link">Dashboard</a>
      <a href="/search" class="nav-link">Food Search</a><a href="/meal-log" class="nav-link">Meal Log</a>
      <a href="/ai-advisor" class="nav-link active">AI Advisor</a>
      <button class="nav-btn" id="nav-auth-btn">Dashboard</button>
    </div>
  </div>
</nav>
<main class="container section">
  <div style="max-width:820px;margin:0 auto">
    <div class="hero-eyebrow" style="margin-bottom:16px"><span style="width:6px;height:6px;background:var(--accent);border-radius:50%"></span>Powered by Ollama phi3 — runs locally</div>
    <h1 style="margin-bottom:12px">NutriFit <em style="font-style:italic;color:var(--accent)">AI</em> Advisor</h1>
    <p style="color:var(--text-muted);font-size:1.05rem;margin-bottom:36px">Ask anything about nutrition, get personalised advice, or let the AI look up your data using MCP tools.</p>
    <div class="tabs">
      <button class="tab active" data-p="chat">💬 Free Chat</button>
      <button class="tab" data-p="mcp">🔧 Data-Aware AI (MCP)</button>
    </div>

    <!-- Chat panel -->
    <div id="panel-chat">
      <div class="card" style="padding:0;overflow:hidden">
        <div id="chat-msgs" style="padding:24px;min-height:300px;max-height:460px;overflow-y:auto;display:flex;flex-direction:column;gap:16px">
          <div class="chat-msg ai"><div class="ai-box-header">🤖 NutriFit AI</div><div class="chat-bubble ai">Hi! I'm your personal nutritionist. Ask me anything about food, macros, meal timing, weight goals, or healthy recipes.</div></div>
        </div>
        <div style="padding:16px;border-top:1px solid var(--border);display:flex;gap:10px">
          <input class="form-control" id="chat-in" placeholder="Ask a nutrition question…" style="flex:1">
          <button class="btn btn-primary" id="btn-send">Send</button>
        </div>
      </div>
      <div style="display:flex;gap:8px;flex-wrap:wrap;margin-top:14px">
        <span style="font-size:.78rem;color:var(--text-faint);align-self:center">Try:</span>
        <button class="btn btn-ghost btn-sm" onclick="qa('What are the best high-protein vegetarian foods?')">High protein veg</button>
        <button class="btn btn-ghost btn-sm" onclick="qa('How many calories to lose weight?')">Calorie deficit</button>
        <button class="btn btn-ghost btn-sm" onclick="qa('What to eat before a workout?')">Pre-workout nutrition</button>
        <button class="btn btn-ghost btn-sm" onclick="qa('Difference between simple and complex carbs?')">Carbs explained</button>
      </div>
    </div>

    <!-- MCP panel -->
    <div id="panel-mcp" style="display:none">
      <div class="card" style="margin-bottom:16px;padding:16px 20px;background:var(--accent-bg);border-color:rgba(181,242,61,.2)">
        <div style="font-size:.82rem;color:var(--accent);font-weight:600;margin-bottom:8px">🔧 Available MCP Tools</div>
        <div style="display:flex;gap:8px;flex-wrap:wrap;margin-bottom:8px">
          <span class="pill pill-green">get_user_profile</span>
          <span class="pill pill-green">get_today_calories</span>
          <span class="pill pill-green">search_food</span>
          <span class="pill pill-green">log_meal</span>
        </div>
        <p style="font-size:.83rem">The AI calls these tools to fetch your real data from MySQL before responding.</p>
      </div>
      <div id="mcp-hist" style="display:flex;flex-direction:column;gap:16px;margin-bottom:16px;min-height:80px"></div>
      <div style="display:flex;gap:10px">
        <input class="form-control" id="mcp-in" placeholder="e.g. How many calories today? What is my BMI?" style="flex:1">
        <button class="btn btn-primary" id="btn-mcp">Ask AI</button>
      </div>
      <p style="font-size:.8rem;color:var(--text-faint);margin-top:8px">Must be signed in. AI uses your user ID to fetch real data.</p>
    </div>
  </div>
</main>
{js}
<script>
document.getElementById("nav-auth-btn").onclick=()=>window.location.href=Auth.isLoggedIn()?"/dashboard":"/";
/* Tabs */
document.querySelectorAll(".tab[data-p]").forEach(t=>t.addEventListener("click",()=>{
  document.querySelectorAll(".tab[data-p]").forEach(x=>x.classList.remove("active"));t.classList.add("active");
  ["chat","mcp"].forEach(p=>document.getElementById("panel-"+p).style.display=p===t.dataset.p?"block":"none");
}));
/* Chat */
function appendMsg(role,text){
  const c=document.getElementById("chat-msgs"),el=document.createElement("div");
  el.className=`chat-msg ${role}`;
  if(role==="ai")el.innerHTML=`<div class="ai-box-header">🤖 NutriFit AI</div><div class="chat-bubble ai">${escHtml(text)}</div>`;
  else el.innerHTML=`<div class="chat-bubble user">${escHtml(text)}</div>`;
  c.appendChild(el);c.scrollTop=c.scrollHeight;
}
function addTyping(){
  const c=document.getElementById("chat-msgs"),el=document.createElement("div");
  el.id="typing";el.className="chat-msg ai";
  el.innerHTML=`<div class="chat-bubble ai" style="color:var(--text-muted)"><span class="spinner" style="width:14px;height:14px;border-width:2px"></span> Thinking…</div>`;
  c.appendChild(el);c.scrollTop=c.scrollHeight;
}
function rmTyping(){const e=document.getElementById("typing");if(e)e.remove();}
async function sendChat(q){
  if(!q)return;appendMsg("user",q);addTyping();
  try{const d=await API.post("/api/ai/ask",{question:q});rmTyping();appendMsg("ai",d.response);}
  catch(e){rmTyping();appendMsg("ai","⚠️ "+e.message);}
}
document.getElementById("btn-send").addEventListener("click",()=>{const i=document.getElementById("chat-in");const q=i.value.trim();if(!q)return;i.value="";sendChat(q);});
document.getElementById("chat-in").addEventListener("keydown",e=>{if(e.key==="Enter"&&!e.shiftKey){e.preventDefault();document.getElementById("btn-send").click();}});
function qa(q){document.getElementById("chat-in").value=q;document.getElementById("btn-send").click();}
/* MCP */
document.getElementById("btn-mcp").addEventListener("click",async()=>{
  if(!Auth.isLoggedIn())return toast("Sign in to use data-aware AI","info");
  const input=document.getElementById("mcp-in");const q=input.value.trim();if(!q)return;
  const btn=document.getElementById("btn-mcp");setLoading(btn,true);input.value="";
  const hist=document.getElementById("mcp-hist");
  const ph=document.createElement("div");ph.className="mcp-turn";
  ph.innerHTML=`<div style="color:var(--text-muted);font-size:.85rem;margin-bottom:10px"><strong style="color:var(--text)">You:</strong> ${escHtml(q)}</div><div><span class="spinner" style="width:14px;height:14px;border-width:2px"></span> Calling tools…</div>`;
  hist.appendChild(ph);hist.scrollTop=hist.scrollHeight;
  try{
    const d=await API.post("/api/ai/mcp",{user_id:Auth.getUserId(),query:q});
    ph.innerHTML=`
      <div style="color:var(--text-muted);font-size:.85rem;margin-bottom:10px"><strong style="color:var(--text)">You:</strong> ${escHtml(q)}</div>
      ${d.tool_used!=="none"?`<div class="mcp-tool-badge">🔧 Tool: ${d.tool_used}</div>`:""}
      ${d.tool_result?`<pre style="background:var(--surface-2);border:1px solid var(--border);border-radius:var(--radius-sm);padding:12px;font-size:.78rem;color:var(--text-muted);overflow:auto;margin-bottom:12px;max-height:160px">${escHtml(JSON.stringify(d.tool_result,null,2))}</pre>`:""}
      <div class="ai-box-header">🤖 AI Response</div>
      <div style="white-space:pre-wrap;font-size:.92rem;color:var(--text);line-height:1.7">${escHtml(d.response)}</div>`;
  }catch(e){ph.innerHTML+=`<p style="color:var(--red);margin-top:8px">⚠️ ${e.message}</p>`;}
  finally{setLoading(btn,false);}
});
document.getElementById("mcp-in").addEventListener("keydown",e=>{if(e.key==="Enter")document.getElementById("btn-mcp").click();});
</script>
</body></html>"""

# ── Render helper ────────────────────────────────────────────────
def render(template):
    return template.replace("{css}", SHARED_CSS).replace("{js}", SHARED_JS).replace("{nav}", "")

# ══════════════════════════════════════════════════════════════════
#  FLASK ROUTES — PAGES
# ══════════════════════════════════════════════════════════════════
@app.route("/")
def page_index():
    return render(INDEX_HTML)

@app.route("/dashboard")
def page_dashboard():
    return render(DASHBOARD_HTML)

@app.route("/search")
def page_search():
    return render(SEARCH_HTML)

@app.route("/meal-log")
def page_meal_log():
    return render(MEAL_LOG_HTML)

@app.route("/ai-advisor")
def page_ai_advisor():
    return render(AI_ADVISOR_HTML)

# ══════════════════════════════════════════════════════════════════
#  FLASK ROUTES — API
# ══════════════════════════════════════════════════════════════════

# ── Auth ─────────────────────────────────────────────────────────
@app.route("/api/signup", methods=["POST"])
def api_signup():
    d = request.json
    username, password = d.get("username","").strip(), d.get("password","").strip()
    if not username or not password:
        return jsonify({"error":"Username and password required"}), 400
    db, cur = get_db()
    try:
        cur.execute("SELECT user_id FROM users WHERE username=%s",(username,))
        if cur.fetchone(): return jsonify({"error":"Username already exists"}), 409
        cur.execute("INSERT INTO users(username,password) VALUES(%s,%s)",(username,password))
        db.commit()
        return jsonify({"message":"Account created"}), 201
    except Exception as e: return jsonify({"error":str(e)}), 500
    finally: close_db(db,cur)

@app.route("/api/login", methods=["POST"])
def api_login():
    d = request.json
    db, cur = get_db()
    try:
        cur.execute("SELECT user_id FROM users WHERE username=%s AND password=%s",(d.get("username"),d.get("password")))
        r = cur.fetchone()
        if r: return jsonify({"message":"Login successful","user_id":r[0]})
        return jsonify({"error":"Invalid credentials"}), 401
    except Exception as e: return jsonify({"error":str(e)}), 500
    finally: close_db(db,cur)

# ── Members ───────────────────────────────────────────────────────
@app.route("/api/members", methods=["GET"])
def api_get_members():
    uid = request.args.get("user_id")
    db, cur = get_db()
    try:
        cur.execute("SELECT member_id,name,age,gender,weight,height FROM members WHERE user_id=%s",(uid,))
        return jsonify([{"member_id":r[0],"name":r[1],"age":r[2],"gender":r[3],"weight":float(r[4] or 0),"height":float(r[5] or 0)} for r in cur.fetchall()])
    finally: close_db(db,cur)

@app.route("/api/members", methods=["POST"])
def api_add_member():
    d = request.json
    db, cur = get_db()
    try:
        cur.execute("INSERT INTO members(user_id,name,age,gender,weight,height) VALUES(%s,%s,%s,%s,%s,%s)",
                    (d["user_id"],d["name"],d["age"],d["gender"],d["weight"],d["height"]))
        db.commit()
        return jsonify({"message":"Member added","member_id":cur.lastrowid}), 201
    except Exception as e: return jsonify({"error":str(e)}), 500
    finally: close_db(db,cur)

# ── Food ──────────────────────────────────────────────────────────
@app.route("/api/food/search")
def api_food_search():
    q = request.args.get("q","").strip()
    if not q: return jsonify({"error":"Query required"}), 400
    vec = vec_search(q, 5)
    food_ids = [r["food_id"] for r in vec]
    db, cur = get_db()
    try:
        results = []
        if food_ids:
            ph = ",".join(["%s"]*len(food_ids))
            cur.execute(f"SELECT food_id,food_name,calories,protein,carbs,fat FROM food_items WHERE food_id IN ({ph})", tuple(food_ids))
            order = {fid:i for i,fid in enumerate(food_ids)}
            sim   = {r["food_id"]:r["similarity"] for r in vec}
            rows  = sorted(cur.fetchall(), key=lambda r: order.get(r[0],999))
            results = [{"food_id":r[0],"food_name":r[1],"calories":float(r[2] or 0),"protein":float(r[3] or 0),"carbs":float(r[4] or 0),"fat":float(r[5] or 0),"similarity":sim.get(r[0],0.5)} for r in rows]
        if not results:
            cur.execute("SELECT food_id,food_name,calories,protein,carbs,fat FROM food_items WHERE food_name LIKE %s LIMIT 10",(f"%{q}%",))
            results = [{"food_id":r[0],"food_name":r[1],"calories":float(r[2] or 0),"protein":float(r[3] or 0),"carbs":float(r[4] or 0),"fat":float(r[5] or 0),"similarity":0.5} for r in cur.fetchall()]
        return jsonify(results)
    finally: close_db(db,cur)

@app.route("/api/food/estimate", methods=["POST"])
def api_food_estimate():
    nm = request.json.get("food_name","").strip()
    if not nm: return jsonify({"error":"food_name required"}), 400
    prompt = f"Estimate nutrition for {nm} per 100g.\nRespond ONLY in this exact format:\nCalories: <number>\nProtein: <number>\nCarbs: <number>\nFat: <number>"
    raw = ai_generate(prompt)
    try:
        cal,pro,car,fat = extract_nutrition(raw)
        db, cur = get_db()
        try:
            cur.execute("INSERT INTO food_items(food_name,calories,protein,carbs,fat) VALUES(%s,%s,%s,%s,%s) ON DUPLICATE KEY UPDATE calories=%s,protein=%s,carbs=%s,fat=%s",
                        (nm,cal,pro,car,fat,cal,pro,car,fat))
            db.commit()
            cur.execute("SELECT food_id FROM food_items WHERE food_name=%s",(nm,))
            fid = cur.fetchone()[0]
            vec_add(fid, nm)
            return jsonify({"food_id":fid,"food_name":nm,"calories":cal,"protein":pro,"carbs":car,"fat":fat})
        finally: close_db(db,cur)
    except Exception as e:
        return jsonify({"error":str(e),"raw":raw}), 500

# ── Meals ─────────────────────────────────────────────────────────
@app.route("/api/meals", methods=["POST"])
def api_add_meal():
    d = request.json
    db, cur = get_db()
    try:
        cur.execute("INSERT INTO meals(member_id,meal_type,meal_date) VALUES(%s,%s,%s)",(d["member_id"],d["meal_type"],d["meal_date"]))
        mid = cur.lastrowid
        cur.execute("INSERT INTO meal_food(meal_id,food_id,quantity) VALUES(%s,%s,%s)",(mid,d["food_id"],d["quantity"]))
        db.commit()
        return jsonify({"message":"Meal logged","meal_id":mid}), 201
    except Exception as e: return jsonify({"error":str(e)}), 500
    finally: close_db(db,cur)

# ── Summaries ─────────────────────────────────────────────────────
@app.route("/api/summary/daily")
def api_daily_summary():
    uid = request.args.get("user_id")
    db, cur = get_db()
    try:
        cur.execute("""SELECT m.meal_type,f.food_name,f.calories,f.protein,f.carbs,f.fat,mf.quantity,(f.calories*mf.quantity)
            FROM meal_food mf JOIN food_items f ON mf.food_id=f.food_id JOIN meals m ON mf.meal_id=m.meal_id
            JOIN members mem ON m.member_id=mem.member_id WHERE mem.user_id=%s AND m.meal_date=CURDATE()""",(uid,))
        rows = cur.fetchall()
        items = [{"meal_type":r[0],"food_name":r[1],"calories":float(r[2] or 0),"protein":float(r[3] or 0),"carbs":float(r[4] or 0),"fat":float(r[5] or 0),"quantity":float(r[6] or 0),"total_calories":float(r[7] or 0)} for r in rows]
        totals = {"calories":sum(i["total_calories"] for i in items),"protein":sum(i["protein"]*i["quantity"] for i in items),"carbs":sum(i["carbs"]*i["quantity"] for i in items),"fat":sum(i["fat"]*i["quantity"] for i in items)}
        return jsonify({"items":items,"totals":totals})
    finally: close_db(db,cur)

@app.route("/api/summary/weekly")
def api_weekly_summary():
    uid = request.args.get("user_id")
    db, cur = get_db()
    try:
        cur.execute("""SELECT DATE(m.meal_date),SUM(f.calories*mf.quantity),SUM(f.protein*mf.quantity),SUM(f.carbs*mf.quantity),SUM(f.fat*mf.quantity)
            FROM meal_food mf JOIN food_items f ON mf.food_id=f.food_id JOIN meals m ON mf.meal_id=m.meal_id
            JOIN members mem ON m.member_id=mem.member_id WHERE mem.user_id=%s AND m.meal_date>=CURDATE()-INTERVAL 7 DAY
            GROUP BY DATE(m.meal_date) ORDER BY 1""",(uid,))
        return jsonify([{"date":str(r[0]),"calories":float(r[1] or 0),"protein":float(r[2] or 0),"carbs":float(r[3] or 0),"fat":float(r[4] or 0)} for r in cur.fetchall()])
    finally: close_db(db,cur)

# ── AI endpoints ──────────────────────────────────────────────────
@app.route("/api/ai/meal-suggestion", methods=["POST"])
def api_meal_suggestion():
    t = request.json.get("totals",{})
    p = f"Today's intake:\nCalories:{t.get('calories',0)}\nProtein:{t.get('protein',0)}g\nCarbs:{t.get('carbs',0)}g\nFat:{t.get('fat',0)}g\n\nSuggest a healthy next meal to balance the day."
    return jsonify({"response":ai_generate(p)})

@app.route("/api/ai/meal-plan", methods=["POST"])
def api_meal_plan():
    d = request.json
    p = f"Create a 1-day meal plan.\nGoal:{d.get('goal','Maintain Weight')}\nDiet:{d.get('diet','Vegetarian')}\nInclude breakfast, lunch, dinner, snacks with estimated calories."
    return jsonify({"response":ai_generate(p)})

@app.route("/api/ai/weekly-analysis", methods=["POST"])
def api_weekly_analysis():
    d = request.json
    p = f"Analyze weekly diet:\nCalories:{d.get('calories',0)}\nProtein:{d.get('protein',0)}g\nCarbs:{d.get('carbs',0)}g\nFat:{d.get('fat',0)}g\nProvide: diet quality, nutrient imbalance, 3 improvement tips."
    return jsonify({"response":ai_generate(p)})

@app.route("/api/ai/ask", methods=["POST"])
def api_ask():
    q = request.json.get("question","")
    if not q: return jsonify({"error":"Question required"}), 400
    return jsonify({"response":ai_generate(q)})

@app.route("/api/ai/mcp", methods=["POST"])
def api_mcp():
    d = request.json
    return jsonify(mcp_dispatch(d.get("user_id"), d.get("query","")))

# ── Vector sync ───────────────────────────────────────────────────
@app.route("/api/vector/sync", methods=["POST"])
def api_vec_sync():
    db, cur = get_db()
    try:
        cur.execute("SELECT food_id,food_name FROM food_items")
        foods = cur.fetchall()
        for fid,fnm in foods: vec_add(fid,fnm)
        return jsonify({"message":f"Synced {len(foods)} foods to vector DB"})
    finally: close_db(db,cur)

# ══════════════════════════════════════════════════════════════════
#  ENTRY POINT
# ══════════════════════════════════════════════════════════════════
if __name__ == "__main__":
    print("\n╔══════════════════════════════════════════╗")
    print("║         NutriFit — Starting Up          ║")
    print("╚══════════════════════════════════════════╝\n")
    print("📦 Initialising database tables…")
    init_db()
    print("🌐 Open http://localhost:5000 in your browser\n")
    print("Tips:")
    print("  • Run  'ollama serve'  in a separate terminal before starting")
    print("  • After adding food items, POST /api/vector/sync to index them")
    print("  • MySQL must be running with credentials in DB_CONFIG above\n")
    app.run(debug=True, port=5000)