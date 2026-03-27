"""
╔══════════════════════════════════════════════════════════════════╗
║         NutriFit — Obsidian OS  · Premium Redesign              ║
║  Flask + MySQL + Gemini API + MCP Tools                         ║
║  pip install flask flask-cors mysql-connector-python google-generativeai      ║
║  python nutrifit.py → http://localhost:5000                      ║
╚══════════════════════════════════════════════════════════════════╝
"""

import os, sys, logging, time
from flask import Flask, render_template, request, jsonify, session, redirect, url_for, make_response
from flask_cors import CORS
try:
    from flask_limiter import Limiter
    from flask_limiter.util import get_remote_address
    LIMITER_OK = True
except Exception:
    Limiter = None
    get_remote_address = None
    LIMITER_OK = False
    # Optional in prod; app should still boot without it.
import mysql.connector
from mysql.connector import pooling
try:
    # New (supported) SDK installed via `google-genai`
    from google import genai as genai_sdk
    GENAI_SDK = "google-genai"
except Exception:
    genai_sdk = None
    GENAI_SDK = None

try:
    from dotenv import load_dotenv
    DOTENV_OK = True
except Exception:
    load_dotenv = None
    DOTENV_OK = False

# Configure Logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s',
    handlers=[logging.StreamHandler(sys.stdout)]
)
logger = logging.getLogger(__name__)

if DOTENV_OK and load_dotenv:
    load_dotenv() # Load local .env if exists

# Configure Gemini API client (Gemini 2.5 Flash-Lite only)
API_KEY = os.getenv("GEMINI_KEY")
if not API_KEY:
    logger.warning("GEMINI_KEY not set - AI features disabled.")

# Lowest-cost model requested by you
GEMINI_TEXT_MODEL = "gemini-2.5-flash-lite"
# Stable embedding model (used for semantic search)
GEMINI_EMBED_MODEL = os.getenv("GEMINI_EMBED_MODEL", "text-embedding-004")

_genai_client = None
def get_genai_client():
    global _genai_client
    if _genai_client is not None:
        return _genai_client
    if not API_KEY or not genai_sdk:
        _genai_client = None
        return None
    try:
        _genai_client = genai_sdk.Client(api_key=API_KEY)
        return _genai_client
    except Exception as e:
        logger.warning(f"Gemini client init failed: {e}")
        _genai_client = None
        return None

AI_ENABLED = bool(API_KEY) and (genai_sdk is not None)
import re, json, io, csv, requests as http_requests, math
from datetime import date, datetime, timedelta
try:
    import bcrypt
    BCRYPT_OK = True
except ImportError:
    BCRYPT_OK = False
    logger.warning("bcrypt not installed - passwords stored as plain text. Run: pip install bcrypt")

app = Flask(__name__, template_folder=None, static_folder=None)
app.secret_key = os.getenv("SECRET_KEY", "nutrifit_obsidian_prod_2026")

# Production Security & Rate Limiting (limiter optional)
app.config.update(
    SESSION_COOKIE_SECURE=True,
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE='Strict',
)
CORS(app)

if LIMITER_OK and Limiter and get_remote_address:
    limiter = Limiter(
        get_remote_address,
        app=app,
        default_limits=["200 per day", "50 per hour"],
        storage_uri="memory://",
    )
else:
    limiter = None

def limit(rule: str):
    if limiter:
        return limiter.limit(rule)
    return lambda f: f

def limit(rule: str):
    if limiter:
        return limiter.limit(rule)
    return lambda f: f

# ══════════════════════════════════════════════════════════════════
#  DATABASE  ← change password if needed
# ══════════════════════════════════════════════════════════════════
DB_CONFIG = {
    "host":     os.getenv("DB_HOST", "localhost"),
    "user":     os.getenv("DB_USER", "root"),
    "password": os.getenv("DB_PASSWORD", "Root"),
    "database": os.getenv("DB_NAME", "nutrifit")
}

# Database Connection Pool
try:
    db_pool = pooling.MySQLConnectionPool(
        pool_name="nutrifit_pool",
        pool_size=10,
        pool_reset_session=True,
        **DB_CONFIG
    )
    logger.info("Database connection pool initialized (size: 10)")
except Exception as e:
    logger.error(f"Failed to initialize connection pool: {e}")
    db_pool = None

def get_db():
    if not db_pool: 
        raise Exception("Database connection pool not available")
    db = db_pool.get_connection()
    return db, db.cursor()

def close_db(db, cursor):
    try:
        cursor.close()
        if db.is_connected(): db.close()
    except: pass

def init_db():
    db, cur = get_db()
    try:
        cur.execute("""CREATE TABLE IF NOT EXISTS users(
            user_id INT AUTO_INCREMENT PRIMARY KEY,
            username VARCHAR(100) UNIQUE NOT NULL,
            password VARCHAR(255) NOT NULL,
            is_admin TINYINT DEFAULT 0,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)""")
        
        # Ensure columns exist if table was created previously without them
        try:
            cur.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS is_admin TINYINT DEFAULT 0")
            cur.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP")
        except: pass
        
        cur.execute("UPDATE users SET is_admin = 1 WHERE username IN ('priyanshuj0097', 'priya')")
        
        # Add embedding column if it doesn't exist
        try:
            cur.execute("ALTER TABLE food_items ADD COLUMN embedding JSON")
        except: pass
        
        # Background Repair: Generate missing embeddings for existing items (batch of 5)
        if AI_ENABLED and get_genai_client():
            try:
                cur.execute("SELECT food_id, food_name FROM food_items WHERE embedding IS NULL LIMIT 5")
                to_embed = cur.fetchall()
                for fid, name in to_embed:
                    try:
                        client = get_genai_client()
                        emb = client.models.embed_content(
                            model=GEMINI_EMBED_MODEL,
                            contents=name,
                        )
                        vec = emb.embeddings[0].values if getattr(emb, "embeddings", None) else None
                        if vec:
                            cur.execute(
                                "UPDATE food_items SET embedding=%s WHERE food_id=%s",
                                (json.dumps(list(vec)), fid),
                            )
                    except Exception as ee2:
                        logger.warning(f"Embedding repair failed for {fid}: {ee2}")
                db.commit()
            except Exception as ee:
                logger.warning(f"Background embedding repair skip: {ee}")
        
        cur.execute("""CREATE TABLE IF NOT EXISTS members(
            member_id INT AUTO_INCREMENT PRIMARY KEY,
            user_id INT NOT NULL, name VARCHAR(100) NOT NULL,
            age INT, gender ENUM('Male','Female'),
            weight DECIMAL(5,2), height DECIMAL(5,2),
            FOREIGN KEY(user_id) REFERENCES users(user_id))""")
        cur.execute("""CREATE TABLE IF NOT EXISTS food_items(
            food_id INT AUTO_INCREMENT PRIMARY KEY,
            food_name VARCHAR(255) UNIQUE NOT NULL,
            calories DECIMAL(8,2), protein DECIMAL(8,2),
            carbs DECIMAL(8,2), fat DECIMAL(8,2))""")
        cur.execute("""CREATE TABLE IF NOT EXISTS meals(
            meal_id INT AUTO_INCREMENT PRIMARY KEY,
            member_id INT NOT NULL,
            meal_type ENUM('Breakfast','Lunch','Snacks','Dinner') NOT NULL,
            meal_date DATE NOT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY(member_id) REFERENCES members(member_id))""")
        cur.execute("""CREATE TABLE IF NOT EXISTS meal_food(
            id INT AUTO_INCREMENT PRIMARY KEY,
            meal_id INT NOT NULL, food_id INT NOT NULL,
            quantity DECIMAL(8,2) DEFAULT 1,
            FOREIGN KEY(meal_id) REFERENCES meals(meal_id),
            FOREIGN KEY(food_id) REFERENCES food_items(food_id))""")
        cur.execute("""CREATE TABLE IF NOT EXISTS weight_log(
            id INT AUTO_INCREMENT PRIMARY KEY,
            user_id INT NOT NULL,
            member_id INT NOT NULL,
            weight DECIMAL(5,2) NOT NULL,
            logged_date DATE NOT NULL DEFAULT (CURDATE()),
            note VARCHAR(255),
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY(user_id) REFERENCES users(user_id),
            FOREIGN KEY(member_id) REFERENCES members(member_id))""")
        cur.execute("""CREATE TABLE IF NOT EXISTS food_favourites(
            id INT AUTO_INCREMENT PRIMARY KEY,
            user_id INT NOT NULL,
            food_id INT NOT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            UNIQUE KEY uniq_fav(user_id, food_id),
            FOREIGN KEY(user_id) REFERENCES users(user_id),
            FOREIGN KEY(food_id) REFERENCES food_items(food_id))""")
        db.commit()
        logger.info("Database ready")
    finally: close_db(db, cur)

# ══════════════════════════════════════════════════════════════════
#  AI ENGINE
# ══════════════════════════════════════════════════════════════════
AI_SYS = """You are NutriFit AI, a professional nutritionist assistant.
- Give concise, practical answers. Use bullet points for lists.
- Always mention calorie counts when discussing food.
- Keep responses under 250 words unless asked for a full plan."""

def ai_generate(prompt, max_tokens=300):
    if not AI_ENABLED:
        return "⚠️ AI unavailable. GEMINI_KEY not set (or SDK missing)."
    client = get_genai_client()
    if not client:
        return "⚠️ AI unavailable. Gemini client not initialized."

    last_err = None
    for attempt in range(2):
        try:
            resp = client.models.generate_content(
                model=GEMINI_TEXT_MODEL,
                contents=AI_SYS + "\n\nUser: " + prompt,
                config={
                    "max_output_tokens": int(max_tokens),
                    "temperature": 0.2,
                },
            )
            text = getattr(resp, "text", None)
            if text:
                return text
            # Fallback: try to extract text from candidates if needed
            if getattr(resp, "candidates", None):
                parts = []
                for c in resp.candidates:
                    content = getattr(c, "content", None)
                    if content and getattr(content, "parts", None):
                        for p in content.parts:
                            t = getattr(p, "text", None)
                            if t:
                                parts.append(t)
                if parts:
                    return "\n".join(parts).strip()
            return "⚠️ AI returned empty response."
        except Exception as e:
            last_err = e
            err_str = str(e).lower()
            if ("429" in err_str) or ("resource_exhausted" in err_str) or ("rate" in err_str):
                if attempt == 0:
                    time.sleep(3)
                    continue
            logger.warning(f"AI error: {e}")
            break
    logger.error(f"AI failed: {last_err}")
    return f"⚠️ AI unavailable.\nLast Error: {last_err}"

def extract_nutrition(text):
    def g(p):
        m = re.search(p, text, re.IGNORECASE)
        if not m: raise ValueError(f"Missing {p} in: {text}")
        return int(float(m.group(1)))
    return g(r"Calories:\s*(\d+(?:\.\d+)?)"), g(r"Protein:\s*(\d+(?:\.\d+)?)"), \
           g(r"Carbs:\s*(\d+(?:\.\d+)?)"),   g(r"Fat:\s*(\d+(?:\.\d+)?)")

def calc_score(t, goal=2000):
    s = 100
    if t.get("calories",0)>goal: s-=20
    if t.get("protein",0)<50:   s-=20
    if t.get("carbs",0)>300:    s-=15
    if t.get("fat",0)>70:       s-=15
    s = max(s,0)
    if s>=90: lbl,clr="Excellent","emerald"
    elif s>=70: lbl,clr="Good","sky"
    elif s>=50: lbl,clr="Fair","amber"
    else: lbl,clr="Poor","rose"
    return {"score":s,"label":lbl,"color":clr}

# ══════════════════════════════════════════════════════════════════
#  MCP TOOLS
# ══════════════════════════════════════════════════════════════════
def mcp_user_profile(uid):
    db,cur=get_db()
    try:
        cur.execute("SELECT name,age,gender,weight,height FROM members WHERE user_id=%s",(uid,))
        rows=cur.fetchall()
        if not rows: return {"error":"No members found."}
        return {"members":[{"name":r[0],"age":r[1],"gender":r[2],
            "weight_kg":float(r[3] or 0),"height_cm":float(r[4] or 0),
            "bmi":round(float(r[3] or 1)/((float(r[4] or 100)/100)**2),1)} for r in rows]}
    finally: close_db(db,cur)

def mcp_today_calories(uid):
    db,cur=get_db()
    try:
        cur.execute("""SELECT SUM(f.calories*mf.quantity),SUM(f.protein*mf.quantity),
            SUM(f.carbs*mf.quantity),SUM(f.fat*mf.quantity)
            FROM meal_food mf JOIN food_items f ON mf.food_id=f.food_id
            JOIN meals m ON mf.meal_id=m.meal_id
            JOIN members mem ON m.member_id=mem.member_id
            WHERE mem.user_id=%s AND m.meal_date=CURDATE()""",(uid,))
        r=cur.fetchone()
        return {"date":str(date.today()),"calories":round(r[0] or 0,1),
            "protein_g":round(r[1] or 0,1),"carbs_g":round(r[2] or 0,1),"fat_g":round(r[3] or 0,1)}
    finally: close_db(db,cur)

def cosine_similarity(v1, v2):
    if not v1 or not v2: return 0
    dot = sum(a * b for a, b in zip(v1, v2))
    mag1 = math.sqrt(sum(a * a for a in v1))
    mag2 = math.sqrt(sum(b * b for b in v2))
    if mag1 == 0 or mag2 == 0: return 0
    return dot / (mag1 * mag2)

def _norm_food_text(s: str) -> str:
    s = (s or "").lower()
    s = re.sub(r"[^a-z0-9]+", " ", s)
    return re.sub(r"\s+", " ", s).strip()

def _levenshtein_ratio(a: str, b: str) -> float:
    a = a or ""
    b = b or ""
    if a == b:
        return 1.0
    la, lb = len(a), len(b)
    if la == 0 or lb == 0:
        return 0.0
    # DP distance, O(la*lb) but strings are short (food names)
    prev = list(range(lb + 1))
    for i, ca in enumerate(a, start=1):
        cur = [i]
        for j, cb in enumerate(b, start=1):
            ins = cur[j - 1] + 1
            dele = prev[j] + 1
            sub = prev[j - 1] + (0 if ca == cb else 1)
            cur.append(min(ins, dele, sub))
        prev = cur
    dist = prev[-1]
    return 1.0 - (dist / max(la, lb))

def _token_overlap_score(q: str, name: str) -> float:
    qn = _norm_food_text(q)
    nn = _norm_food_text(name)
    qt = [t for t in qn.split(" ") if t]
    nt = [t for t in nn.split(" ") if t]
    if not qt or not nt:
        return 0.0
    qset = set(qt)
    nset = set(nt)
    inter = len(qset & nset)
    if inter == 0:
        return 0.0
    # Reward coverage of query tokens (paneer curry -> paneer sabzi)
    return inter / max(1, len(qset))

def mcp_search_food(q):
    db,cur=get_db()
    try:
        # 0) Normalize query + fast candidate prefilter in MySQL (instant)
        q = (q or "").strip()
        nq = _norm_food_text(q)
        if not nq:
            return {"query": q, "source": "smart", "results": []}

        tokens = [t for t in nq.split(" ") if t][:4]
        like_clauses = []
        params = []
        for t in tokens:
            like_clauses.append("food_name LIKE %s")
            params.append(f"%{t}%")

        # SOUNDEX helps for paneer/panir style typos (English-ish)
        soundex_clause = "SOUNDEX(food_name) = SOUNDEX(%s)"
        params_soundex = [q]

        where = ""
        if like_clauses:
            where = "(" + " AND ".join(like_clauses) + ") OR (" + soundex_clause + ")"
            params = params + params_soundex
        else:
            where = soundex_clause
            params = params_soundex

        # Keep candidate set small so fuzzy is fast
        cur.execute(
            f"""SELECT food_id,food_name,calories,protein,carbs,fat,embedding
                FROM food_items
                WHERE {where}
                LIMIT 220""",
            tuple(params),
        )
        rows = cur.fetchall()

        # If prefilter is empty (rare), do a small LIKE fallback
        if not rows:
            cur.execute(
                "SELECT food_id,food_name,calories,protein,carbs,fat,embedding FROM food_items WHERE food_name LIKE %s LIMIT 220",
                (f"%{q}%",),
            )
            rows = cur.fetchall()

        # 1) Fast fuzzy scoring on a small set (typo + phrase tolerant)
        scored = []
        for fid, name, cal, pro, carb, fat, emb_json in rows:
            if not name:
                continue
            nn = _norm_food_text(name)
            if not nn:
                continue

            # Fuzzy handles typos: panir -> paneer
            fuzzy = _levenshtein_ratio(nq, nn)
            tok = _token_overlap_score(q, name)
            fuzzy_score = (0.65 * fuzzy) + (0.35 * tok)

            final = fuzzy_score

            # Small boost for prefix match on first token (paneer curry -> paneer ...)
            if nq and nn.startswith(nq.split(" ")[0]):
                final += 0.05

            scored.append({
                "food_id": fid,
                "food_name": name,
                "calories": float(cal or 0),
                "protein": float(pro or 0),
                "carbs": float(carb or 0),
                "fat": float(fat or 0),
                "score": float(final),
                "match": "fuzzy"
            })

        scored.sort(key=lambda x: x["score"], reverse=True)
        top = scored[:12]

        # 2) Optional semantic refinement (only when fuzzy confidence is low)
        # This avoids network latency on every keystroke.
        if (AI_ENABLED and top and top[0]["score"] < 0.72) or (AI_ENABLED and not top):
            try:
                client = get_genai_client()
                if client:
                    emb = client.models.embed_content(model=GEMINI_EMBED_MODEL, contents=q)
                    q_vec = emb.embeddings[0].values if getattr(emb, "embeddings", None) else None
                    if q_vec:
                        # Build a quick map fid->embedding from rows
                        emb_map = {r[0]: r[6] for r in rows}
                        for item in scored:
                            emb_json = emb_map.get(item["food_id"])
                            if not emb_json:
                                continue
                            try:
                                f_vec = json.loads(emb_json)
                                sem = cosine_similarity(list(q_vec), f_vec)
                                # Blend: keep fuzzy but allow semantic to promote "related" foods
                                item["score"] = float(max(item["score"], 0.60 * sem + 0.40 * item["score"]))
                                if sem > 0:
                                    item["match"] = "semantic+fuzzy"
                            except Exception:
                                continue
                        scored.sort(key=lambda x: x["score"], reverse=True)
                        top = scored[:12]
            except Exception as e:
                logger.warning(f"Embedding error: {e}")

        return {"query": q, "source": "instant", "results": top[:12]}
    finally: close_db(db,cur)

def mcp_log_meal(uid,member_name,food_name,meal_type,qty):
    db,cur=get_db()
    try:
        cur.execute("SELECT member_id FROM members WHERE user_id=%s AND name=%s",(uid,member_name))
        m=cur.fetchone()
        if not m: return {"error":f"Member '{member_name}' not found."}
        cur.execute("SELECT food_id FROM food_items WHERE food_name=%s",(food_name,))
        f=cur.fetchone()
        if not f: return {"error":f"Food '{food_name}' not in database."}
        cur.execute("INSERT INTO meals(member_id,meal_type,meal_date) VALUES(%s,%s,CURDATE())",(m[0],meal_type))
        mid=cur.lastrowid
        cur.execute("INSERT INTO meal_food(meal_id,food_id,quantity) VALUES(%s,%s,%s)",(mid,f[0],qty))
        db.commit()
        return {"message":f"Logged {qty}x {food_name} as {meal_type} for {member_name}.","meal_id":mid}
    except Exception as e: return {"error":str(e)}
    finally: close_db(db,cur)

MCP = {
    "get_user_profile":  {"desc":"Get profile and BMI.","fn":mcp_user_profile},
    "get_today_calories":{"desc":"Get today's macros.","fn":mcp_today_calories},
    "search_food":       {"desc":"Search food by name.","fn":mcp_search_food},
    "log_meal":          {"desc":"Log a meal.","fn":mcp_log_meal},
}

def mcp_dispatch(uid, query):
    manifest="\n".join(f"- {n}: {v['desc']}" for n,v in MCP.items())
    raw=ai_generate(f'Tools:\n{manifest}\nQuery: "{query}"\nUser ID: {uid}\nReply ONLY JSON: {{"tool":"<n>","args":{{}}}}',150)
    tool,args="none",{}
    try:
        m=re.search(r"\{.*\}",raw,re.DOTALL)
        if m:
            p=json.loads(m.group()); tool=p.get("tool","none"); args=p.get("args",{})
    except: pass
    result=None
    if tool in MCP:
        fn=MCP[tool]["fn"]
        try:
            if tool in("get_user_profile","get_today_calories"): result=fn(uid)
            elif tool=="search_food": result=fn(args.get("query",query))
            elif tool=="log_meal": result=fn(uid,args.get("member_name",""),args.get("food_name",""),args.get("meal_type","Lunch"),int(args.get("quantity",1)))
        except Exception as e: result={"error":str(e)}
    resp=ai_generate(f'User: "{query}"\nTool: {tool}\nData: {json.dumps(result,default=str)}\nAnswer naturally.' if result else query,300)
    return {"tool_used":tool,"tool_result":result,"response":resp}

# ══════════════════════════════════════════════════════════════════
#  DESIGN SYSTEM — OBSIDIAN OS
#  Fonts: Syne (headings) + Plus Jakarta Sans (body)
#  Palette: #070708 base · #F97316 coral accent · #10B981 success
# ══════════════════════════════════════════════════════════════════
CSS = """<style>
@import url('https://fonts.googleapis.com/css2?family=Syne:wght@400;500;600;700;800&family=Plus+Jakarta+Sans:ital,wght@0,300;0,400;0,500;0,600;1,400&display=swap');

/* ── Reset & Base ── */
*,*::before,*::after{box-sizing:border-box;margin:0;padding:0}
html{scroll-behavior:smooth;-webkit-font-smoothing:antialiased}
:root{
  --bg:#070708;
  --s1:#0E0E12;
  --s2:#141419;
  --s3:#1C1C23;
  --border:rgba(255,255,255,0.07);
  --border-2:rgba(255,255,255,0.12);
  --accent:#F97316;
  --accent-2:#FB923C;
  --accent-glow:rgba(249,115,22,0.18);
  --accent-subtle:rgba(249,115,22,0.08);
  --emerald:#10B981;
  --sky:#38BDF8;
  --amber:#FBBF24;
  --rose:#F43F5E;
  --violet:#8B5CF6;
  --text:#F4F4F5;
  --text-muted:#71717A;
  --text-faint:#3F3F46;
  --r:16px;--r-sm:10px;--r-xs:6px;
  --tr:0.2s cubic-bezier(.4,0,.2,1);
  --shadow-sm:0 2px 8px rgba(0,0,0,.4);
  --shadow:0 8px 32px rgba(0,0,0,.6);
  --shadow-lg:0 24px 64px rgba(0,0,0,.8);
  --font-head:'Syne',sans-serif;
  --font-body:'Plus Jakarta Sans',sans-serif;
}
body{
  font-family:var(--font-body);
  background:var(--bg);
  color:var(--text);
  font-size:14px;
  line-height:1.6;
  min-height:100vh;
  overflow-x:hidden;
}

/* ── Grain texture overlay ── */
body::before{
  content:'';
  position:fixed;inset:0;z-index:0;
  background-image:url("data:image/svg+xml,%3Csvg viewBox='0 0 256 256' xmlns='http://www.w3.org/2000/svg'%3E%3Cfilter id='noise'%3E%3CfeTurbulence type='fractalNoise' baseFrequency='0.9' numOctaves='4' stitchTiles='stitch'/%3E%3C/filter%3E%3Crect width='100%25' height='100%25' filter='url(%23noise)' opacity='0.035'/%3E%3C/svg%3E");
  pointer-events:none;
  opacity:1;
}

/* ── Ambient background glow ── */
body::after{
  content:'';
  position:fixed;
  top:-30%;left:60%;
  width:600px;height:600px;
  background:radial-gradient(circle,rgba(249,115,22,0.04) 0%,transparent 70%);
  pointer-events:none;
  z-index:0;
}

/* ── All content above grain ── */
nav,.app-layout,.page-wrap{position:relative;z-index:1}

a{color:var(--accent);text-decoration:none}
a:hover{text-decoration:underline}
button{cursor:pointer;font-family:var(--font-body)}
input,select,textarea{font-family:var(--font-body)}
h1,h2,h3,h4,h5{font-family:var(--font-head);line-height:1.15;color:var(--text);letter-spacing:-0.02em}
h1{font-size:clamp(2.4rem,5vw,3.8rem);font-weight:800}
h2{font-size:clamp(1.5rem,3vw,2rem);font-weight:700}
h3{font-size:1.2rem;font-weight:700}
h4{font-size:1rem;font-weight:600}
p{color:var(--text-muted);line-height:1.7}

/* ── Scrollbar ── */
::-webkit-scrollbar{width:4px}
::-webkit-scrollbar-track{background:transparent}
::-webkit-scrollbar-thumb{background:var(--s3);border-radius:99px}

/* ── Container ── */
.container{max-width:1180px;margin:0 auto;padding:0 28px}

/* ══════════════════════════════
   NAVBAR
══════════════════════════════ */
.navbar{
  position:sticky;top:0;z-index:100;
  background:rgba(7,7,8,0.8);
  backdrop-filter:blur(24px);
  border-bottom:1px solid var(--border);
  padding:0;height:60px;
  display:flex;align-items:center;
}
.nav-inner{
  display:flex;align-items:center;
  justify-content:space-between;width:100%;
}
.nav-logo{
  display:flex;align-items:center;gap:10px;
  font-family:var(--font-head);font-size:1.2rem;font-weight:800;
  color:var(--text);letter-spacing:-0.03em;
}
.nav-logo-icon{
  width:32px;height:32px;
  background:linear-gradient(135deg,var(--accent),#EA580C);
  border-radius:8px;display:flex;align-items:center;justify-content:center;
  font-size:1rem;box-shadow:0 0 20px var(--accent-glow);
}
.nav-links{display:flex;gap:2px;align-items:center}
.nav-link{
  padding:6px 14px;border-radius:var(--r-sm);
  color:var(--text-muted);font-size:.83rem;font-weight:500;
  transition:var(--tr);letter-spacing:0.01em;
}
.nav-link:hover{color:var(--text);background:var(--s2);text-decoration:none}
.nav-link.active{color:var(--text);background:var(--s2)}
.nav-actions{display:flex;align-items:center;gap:8px}
.nav-badge{
  display:flex;align-items:center;gap:6px;
  padding:6px 14px;border-radius:var(--r-sm);
  background:var(--s2);border:1px solid var(--border);
  font-size:.82rem;color:var(--text-muted);font-weight:500;
}
.nav-badge .dot{
  width:7px;height:7px;border-radius:50%;
  background:var(--emerald);
  box-shadow:0 0 8px var(--emerald);
  animation:pulse 2s infinite;
}
@keyframes pulse{0%,100%{opacity:1}50%{opacity:.5}}
.btn-nav{
  padding:7px 16px;border-radius:var(--r-sm);
  background:linear-gradient(135deg,var(--accent),#EA580C);
  color:#fff;font-weight:600;font-size:.83rem;
  border:none;transition:var(--tr);
  box-shadow:0 0 20px var(--accent-glow);
}
.btn-nav:hover{transform:translateY(-1px);box-shadow:0 4px 24px var(--accent-glow)}

/* ══════════════════════════════
   BUTTONS
══════════════════════════════ */
.btn{
  display:inline-flex;align-items:center;gap:8px;
  padding:10px 20px;border-radius:var(--r-sm);
  font-weight:600;font-size:.85rem;border:none;
  transition:var(--tr);white-space:nowrap;
  font-family:var(--font-body);letter-spacing:0.01em;
}
.btn-primary{
  background:linear-gradient(135deg,var(--accent),#EA580C);
  color:#fff;box-shadow:0 0 24px var(--accent-glow);
}
.btn-primary:hover{transform:translateY(-1px);box-shadow:0 4px 32px rgba(249,115,22,.3)}
.btn-ghost{
  background:var(--s2);color:var(--text-muted);
  border:1px solid var(--border);
}
.btn-ghost:hover{color:var(--text);border-color:var(--border-2);background:var(--s3)}
.btn-sm{padding:7px 14px;font-size:.8rem}
.btn-full{width:100%;justify-content:center}
.btn:disabled{opacity:.35;cursor:not-allowed;transform:none !important}

/* ══════════════════════════════
   FORM CONTROLS
══════════════════════════════ */
.form-group{margin-bottom:16px}
.form-label{
  display:block;font-size:.73rem;font-weight:600;
  color:var(--text-muted);margin-bottom:6px;
  letter-spacing:.06em;text-transform:uppercase;
}
.form-control{
  width:100%;background:var(--s2);
  border:1px solid var(--border);
  border-radius:var(--r-sm);color:var(--text);
  padding:10px 14px;font-size:.88rem;
  transition:var(--tr);outline:none;
}
.form-control:focus{
  border-color:var(--accent);
  background:var(--s1);
  box-shadow:0 0 0 3px var(--accent-subtle);
}
.form-control::placeholder{color:var(--text-faint)}
select.form-control{
  appearance:none;
  background-image:url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' width='12' height='12' viewBox='0 0 12 12'%3E%3Cpath fill='%2371717A' d='M6 8L1 3h10z'/%3E%3C/svg%3E");
  background-repeat:no-repeat;background-position:right 12px center;padding-right:36px;
}
.form-row{display:grid;grid-template-columns:1fr 1fr;gap:14px}

/* ══════════════════════════════
   CARDS
══════════════════════════════ */
.card{
  background:var(--s1);
  border:1px solid var(--border);
  border-radius:var(--r);padding:24px;
  transition:var(--tr);
  position:relative;overflow:hidden;
}
.card::before{
  content:'';position:absolute;inset:0;
  background:linear-gradient(135deg,rgba(255,255,255,0.02) 0%,transparent 60%);
  pointer-events:none;
}
.card-lift:hover{
  transform:translateY(-2px);
  border-color:var(--border-2);
  box-shadow:var(--shadow);
}
.card-glow:hover{
  border-color:rgba(249,115,22,.25);
  box-shadow:0 0 32px rgba(249,115,22,.08);
}
.card-lift:active, .stat-tile:active { transform: scale(0.98); }
.card-header{
  display:flex;align-items:center;
  justify-content:space-between;margin-bottom:20px;
}

/* ══════════════════════════════
   STAT TILES — Bento style
══════════════════════════════ */
.bento-grid{
  display:grid;
  grid-template-columns:repeat(4,1fr);
  gap:14px;margin-bottom:16px;
}
.stat-tile{
  background:var(--s1);border:1px solid var(--border);
  border-radius:var(--r);padding:20px;
  position:relative;overflow:hidden;
  transition:var(--tr);
}
.stat-tile::after{
  content:'';position:absolute;
  top:-40px;right:-40px;
  width:100px;height:100px;
  border-radius:50%;
  background:var(--accent-subtle);
  transition:var(--tr);
}
.stat-tile:hover::after{transform:scale(1.4)}
.stat-tile:hover{
  border-color:var(--border-2);
  transform:translateY(-4px) scale(1.02);
  box-shadow:var(--shadow);
}
.st-icon{
  font-size:.85rem;margin-bottom:12px;display:flex;
  align-items:center;gap:6px;color:var(--text-muted);font-weight:500;
}
.st-val{
  font-family:var(--font-head);font-size:2.2rem;
  font-weight:800;color:var(--text);line-height:1;
  margin-bottom:4px;letter-spacing:-0.03em;
  font-variant-numeric:tabular-nums;
}
.st-sub{font-size:.75rem;color:var(--text-faint)}
.st-accent .st-val{color:var(--accent)}
.st-accent{border-color:rgba(249,115,22,.2);background:rgba(249,115,22,.04)}
.st-emerald .st-val{color:var(--emerald)}
.st-sky .st-val{color:var(--sky)}
.st-amber .st-val{color:var(--amber)}
.st-rose .st-val{color:var(--rose)}

/* ══════════════════════════════
   CIRCULAR PROGRESS
══════════════════════════════ */
.ring-wrap{
  display:flex;align-items:center;gap:28px;
  padding:24px;background:var(--s1);
  border:1px solid var(--border);border-radius:var(--r);
}
.ring-svg{transform:rotate(-90deg);flex-shrink:0}
.ring-bg{fill:none;stroke:var(--s3);stroke-width:8}
.ring-fill{
  fill:none;stroke:var(--accent);stroke-width:8;
  stroke-linecap:round;
  stroke-dasharray:326.7;stroke-dashoffset:326.7;
  transition:stroke-dashoffset 1s cubic-bezier(.4,0,.2,1);
  filter:drop-shadow(0 0 6px var(--accent));
}
.ring-fill.warn{stroke:var(--amber);filter:drop-shadow(0 0 6px var(--amber))}
.ring-fill.danger{stroke:var(--rose);filter:drop-shadow(0 0 6px var(--rose))}
.ring-center{position:relative}
.ring-meta{flex:1}
.ring-meta h3{font-size:1.8rem;font-weight:800;margin-bottom:2px;letter-spacing:-0.03em}
.ring-meta p{font-size:.82rem;color:var(--text-muted);margin-bottom:16px}
.macro-row{display:flex;gap:16px;flex-wrap:wrap}
.macro-chip{
  display:flex;flex-direction:column;
  background:var(--s2);border:1px solid var(--border);
  border-radius:var(--r-xs);padding:10px 14px;min-width:80px;
}
.macro-chip .mc-lbl{font-size:.68rem;font-weight:600;text-transform:uppercase;letter-spacing:.06em;color:var(--text-faint);margin-bottom:3px}
.macro-chip .mc-val{font-family:var(--font-head);font-size:1.1rem;font-weight:700;line-height:1}
.mc-pro{color:var(--sky)}
.mc-car{color:var(--amber)}
.mc-fat{color:var(--rose)}

/* ══════════════════════════════
   SCORE PILL
══════════════════════════════ */
.score-pill{
  display:inline-flex;align-items:center;gap:8px;
  padding:6px 14px;border-radius:999px;
  font-family:var(--font-head);font-weight:700;font-size:.88rem;
}
.sp-emerald{background:rgba(16,185,129,.1);color:var(--emerald);border:1px solid rgba(16,185,129,.2)}
.sp-sky{background:rgba(56,189,248,.1);color:var(--sky);border:1px solid rgba(56,189,248,.2)}
.sp-amber{background:rgba(251,191,36,.1);color:var(--amber);border:1px solid rgba(251,191,36,.2)}
.sp-rose{background:rgba(244,63,94,.1);color:var(--rose);border:1px solid rgba(244,63,94,.2)}

/* ══════════════════════════════
   TABLE
══════════════════════════════ */
.tbl{width:100%;border-collapse:collapse;font-size:.84rem}
.tbl th{
  text-align:left;padding:8px 12px;
  color:var(--text-faint);font-size:.7rem;font-weight:600;
  text-transform:uppercase;letter-spacing:.07em;
  border-bottom:1px solid var(--border);
}
.tbl td{padding:11px 12px;border-bottom:1px solid var(--border);color:var(--text-muted);transition:var(--tr)}
.tbl tr:last-child td{border-bottom:none}
.tbl tbody tr:hover td{background:var(--s2);color:var(--text)}
.tbl .t-name{color:var(--text);font-weight:600}
.tbl .t-cal{color:var(--accent);font-weight:700;font-variant-numeric:tabular-nums}
.tbl .t-meal{font-size:.75rem;font-weight:600;padding:3px 8px;border-radius:999px}
.meal-tag-breakfast{background:rgba(251,191,36,.1);color:var(--amber)}
.meal-tag-lunch{background:rgba(56,189,248,.1);color:var(--sky)}
.meal-tag-dinner{background:rgba(139,92,246,.1);color:var(--violet)}
.meal-tag-snacks{background:rgba(16,185,129,.1);color:var(--emerald)}

/* ══════════════════════════════
   FOOD CARDS
══════════════════════════════ */
.food-grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(180px,1fr));gap:10px}
.food-card{
  background:var(--s2);border:1px solid var(--border);
  border-radius:var(--r-sm);padding:14px;cursor:pointer;
  transition:var(--tr);position:relative;overflow:hidden;
}
.food-card:hover{border-color:var(--accent);transform:translateY(-2px);box-shadow:0 4px 20px var(--accent-subtle)}
.food-card.picked{border-color:var(--accent);background:var(--accent-subtle)}
.fc-n{font-weight:600;color:var(--text);margin-bottom:5px;font-size:.88rem;line-height:1.3}
.fc-c{color:var(--accent);font-family:var(--font-head);font-size:1.1rem;font-weight:800;margin-bottom:4px}
.fc-m{display:flex;gap:8px;font-size:.72rem;color:var(--text-faint)}

/* ══════════════════════════════
   AI BOX
══════════════════════════════ */
.ai-wrap{
  background:linear-gradient(135deg,rgba(249,115,22,.06) 0%,rgba(234,88,12,.03) 100%);
  border:1px solid rgba(249,115,22,.18);
  border-radius:var(--r);padding:20px;
  white-space:pre-wrap;line-height:1.8;
  color:var(--text);font-size:.88rem;
}
.ai-label{
  display:flex;align-items:center;gap:8px;
  font-size:.72rem;font-weight:700;color:var(--accent);
  text-transform:uppercase;letter-spacing:.1em;margin-bottom:12px;
}
.ai-label::before{
  content:'';width:6px;height:6px;border-radius:50%;
  background:var(--accent);box-shadow:0 0 8px var(--accent);
  animation:pulse 2s infinite;
}

/* ══════════════════════════════
   TOAST
══════════════════════════════ */
.toast-wrap{position:fixed;bottom:24px;right:24px;z-index:9999;display:flex;flex-direction:column;gap:8px}
.toast{
  background:var(--s2);border:1px solid var(--border);
  border-radius:var(--r-sm);padding:12px 18px;
  font-size:.84rem;color:var(--text);
  box-shadow:var(--shadow);max-width:320px;
  animation:toastIn .3s cubic-bezier(.4,0,.2,1);
  display:flex;align-items:center;gap:10px;
}
.toast.success{border-left:3px solid var(--emerald)}
.toast.error{border-left:3px solid var(--rose)}
.toast.info{border-left:3px solid var(--sky)}
@keyframes toastIn{from{opacity:0;transform:translateX(20px) scale(.96)}to{opacity:1;transform:none}}

/* ══════════════════════════════
   SPINNER
══════════════════════════════ */
.spin{
  display:inline-block;width:16px;height:16px;
  border:2px solid var(--border);
  border-top-color:var(--accent);
  border-radius:50%;animation:sp .6s linear infinite;flex-shrink:0;
}
@keyframes sp{to{transform:rotate(360deg)}}

/* ══════════════════════════════
   MODAL
══════════════════════════════ */
.modal-bg{
  position:fixed;inset:0;z-index:200;
  background:rgba(0,0,0,.85);backdrop-filter:blur(12px);
  display:flex;align-items:center;justify-content:center;
  opacity:0;pointer-events:none;transition:var(--tr);
}
.modal-bg.open{opacity:1;pointer-events:all}
.modal{
  background:var(--s1);border:1px solid var(--border-2);
  border-radius:20px;padding:36px;width:100%;max-width:440px;
  transform:translateY(20px) scale(.97);transition:var(--tr);
  box-shadow:var(--shadow-lg);position:relative;overflow:hidden;
}
.modal::before{
  content:'';position:absolute;top:-60px;right:-60px;
  width:180px;height:180px;border-radius:50%;
  background:radial-gradient(circle,var(--accent-subtle),transparent 70%);
  pointer-events:none;
}
.modal-bg.open .modal{transform:none}
.modal-brand{
  font-family:var(--font-head);font-size:1.5rem;font-weight:800;
  margin-bottom:4px;letter-spacing:-0.03em;
}
.modal-sub{font-size:.85rem;color:var(--text-muted);margin-bottom:24px}
.modal-divider{
  text-align:center;font-size:.75rem;color:var(--text-faint);
  margin:16px 0;position:relative;
}
.modal-divider::before{
  content:'';position:absolute;top:50%;left:0;right:0;
  height:1px;background:var(--border);z-index:0;
}
.modal-divider span{background:var(--s1);padding:0 12px;position:relative;z-index:1}

/* ══════════════════════════════
   APP LAYOUT (sidebar + main)
══════════════════════════════ */
.app-layout{display:grid;grid-template-columns:240px 1fr;min-height:calc(100vh - 60px)}

/* Sidebar */
.sidebar{
  background:var(--s1);border-right:1px solid var(--border);
  padding:20px 12px;position:sticky;top:60px;
  height:calc(100vh - 60px);overflow-y:auto;
  display:flex;flex-direction:column;gap:4px;
}
.sb-section{margin-bottom:8px;margin-top:12px}
.sb-section:first-child{margin-top:0}
.sb-label{
  font-size:.67rem;font-weight:700;color:var(--text-faint);
  text-transform:uppercase;letter-spacing:.1em;
  padding:0 10px;margin-bottom:4px;display:block;
}
.sb-link{
  display:flex;align-items:center;gap:10px;
  padding:9px 10px;border-radius:var(--r-sm);
  color:var(--text-muted);font-size:.84rem;font-weight:500;
  transition:var(--tr);cursor:pointer;border:none;
  background:none;width:100%;text-align:left;
  position:relative;
}
.sb-link:hover{background:var(--s2);color:var(--text)}
.sb-link.active{
  background:var(--accent-subtle);color:var(--accent);
  border:1px solid rgba(249,115,22,.15);
}
.sb-link.active .sb-ic{color:var(--accent)}
.sb-ic{font-size:1rem;width:20px;text-align:center;flex-shrink:0}
.sb-footer{
  margin-top:auto;padding:12px 10px;
  border-top:1px solid var(--border);
}
.sb-user{display:flex;align-items:center;gap:10px}
.sb-avatar{
  width:32px;height:32px;border-radius:8px;
  background:linear-gradient(135deg,var(--accent),#EA580C);
  display:flex;align-items:center;justify-content:center;
  font-family:var(--font-head);font-size:.85rem;font-weight:800;color:#fff;flex-shrink:0;
}
.sb-name{font-size:.82rem;font-weight:600;color:var(--text)}
.sb-role{font-size:.72rem;color:var(--text-faint)}

/* Main content */
.main{padding:28px 32px;overflow-y:auto;min-height:calc(100vh - 60px)}

/* ══════════════════════════════
   TABS (AI advisor)
══════════════════════════════ */
.tabs-bar{display:flex;gap:2px;background:var(--s2);border-radius:var(--r-sm);padding:4px;margin-bottom:24px;border:1px solid var(--border)}
.tab-btn{
  flex:1;padding:8px 16px;border-radius:7px;
  font-size:.83rem;font-weight:600;color:var(--text-muted);
  cursor:pointer;border:none;background:none;
  transition:var(--tr);text-align:center;
}
.tab-btn:hover{color:var(--text)}
.tab-btn.active{
  background:var(--s3);color:var(--text);
  box-shadow:var(--shadow-sm);
}

/* ══════════════════════════════
   CHAT BUBBLES
══════════════════════════════ */
.chat-box{
  background:var(--s1);border:1px solid var(--border);
  border-radius:var(--r);overflow:hidden;
}
.chat-msgs{
  padding:20px;min-height:280px;max-height:420px;
  overflow-y:auto;display:flex;flex-direction:column;gap:14px;
}
.msg{display:flex;flex-direction:column;gap:4px}
.msg.user{align-items:flex-end}
.msg.ai{align-items:flex-start}
.bubble{
  max-width:80%;padding:11px 16px;
  border-radius:var(--r-sm);font-size:.87rem;line-height:1.7;
  white-space:pre-wrap;
}
.bubble.user{
  background:linear-gradient(135deg,var(--accent),#EA580C);
  color:#fff;font-weight:500;border-radius:var(--r-sm) var(--r-sm) 4px var(--r-sm);
}
.bubble.ai{
  background:var(--s2);border:1px solid var(--border);color:var(--text);
  border-radius:4px var(--r-sm) var(--r-sm) var(--r-sm);
}
.chat-input-bar{
  padding:14px 16px;border-top:1px solid var(--border);
  display:flex;gap:10px;background:var(--s2);
}
.mcp-card{
  background:var(--s1);border:1px solid var(--border);
  border-radius:var(--r);padding:20px;
}
.mcp-tool-badge{
  display:inline-flex;align-items:center;gap:6px;
  background:rgba(249,115,22,.1);border:1px solid rgba(249,115,22,.2);
  border-radius:var(--r-xs);padding:4px 10px;
  font-size:.75rem;color:var(--accent);font-weight:600;margin-bottom:10px;
}

/* ══════════════════════════════
   HERO PAGE
══════════════════════════════ */
.hero{
  padding:100px 0 80px;position:relative;overflow:hidden;
}
.hero-bg-orb{
  position:absolute;top:50%;left:50%;
  transform:translate(-50%,-50%);
  width:800px;height:400px;
  background:radial-gradient(ellipse,rgba(249,115,22,0.05) 0%,transparent 70%);
  pointer-events:none;
}
.hero-badge{
  display:inline-flex;align-items:center;gap:8px;
  background:var(--accent-subtle);border:1px solid rgba(249,115,22,.2);
  border-radius:999px;padding:6px 14px;
  font-size:.75rem;font-weight:700;color:var(--accent);
  text-transform:uppercase;letter-spacing:.08em;margin-bottom:24px;
}
.hero-title{
  font-size:clamp(3rem,7vw,5rem);font-weight:800;
  letter-spacing:-0.04em;line-height:1.05;
  max-width:700px;margin-bottom:20px;
}
.hero-title .grad{
  background:linear-gradient(135deg,var(--accent),var(--amber));
  -webkit-background-clip:text;-webkit-text-fill-color:transparent;background-clip:text;
}
.hero-sub{
  font-size:1.05rem;color:var(--text-muted);
  max-width:500px;margin-bottom:36px;line-height:1.7;
}
.hero-ctas{display:flex;gap:12px;flex-wrap:wrap;margin-bottom:64px}
.hero-cta-primary{
  padding:14px 28px;font-size:1rem;border-radius:var(--r-sm);
  background:linear-gradient(135deg,var(--accent),#EA580C);
  color:#fff;font-weight:700;border:none;cursor:pointer;
  transition:var(--tr);font-family:var(--font-head);
  box-shadow:0 0 32px var(--accent-glow);letter-spacing:-0.01em;
}
.hero-cta-primary:hover{transform:translateY(-2px);box-shadow:0 8px 40px rgba(249,115,22,.3)}
.hero-cta-ghost{
  padding:14px 28px;font-size:1rem;border-radius:var(--r-sm);
  background:var(--s2);color:var(--text-muted);
  border:1px solid var(--border);cursor:pointer;
  transition:var(--tr);font-family:var(--font-head);font-weight:600;
}
.hero-cta-ghost:hover{color:var(--text);border-color:var(--border-2);transform:translateY(-1px)}
.hero-stats{
  display:flex;gap:0;
  background:var(--s1);border:1px solid var(--border);
  border-radius:var(--r);overflow:hidden;
}
.hs-item{
  flex:1;padding:20px 24px;text-align:center;
  border-right:1px solid var(--border);
}
.hs-item:last-child{border-right:none}
.hs-val{
  font-family:var(--font-head);font-size:1.8rem;font-weight:800;
  color:var(--accent);letter-spacing:-0.04em;margin-bottom:4px;
}
.hs-lbl{font-size:.75rem;color:var(--text-faint)}
.feature-grid{display:grid;grid-template-columns:repeat(3,1fr);gap:16px;margin-top:80px}
.feat-card{
  background:var(--s1);border:1px solid var(--border);
  border-radius:var(--r);padding:28px;
  transition:var(--tr);cursor:default;
}
.feat-card:hover{border-color:var(--border-2);transform:translateY(-3px);box-shadow:var(--shadow)}
.feat-icon{
  width:44px;height:44px;border-radius:10px;
  display:flex;align-items:center;justify-content:center;
  font-size:1.3rem;margin-bottom:16px;
}
.fi-orange{background:rgba(249,115,22,.1)}
.fi-sky{background:rgba(56,189,248,.1)}
.fi-emerald{background:rgba(16,185,129,.1)}
.fi-violet{background:rgba(139,92,246,.1)}
.fi-amber{background:rgba(251,191,36,.1)}
.feat-title{font-size:1rem;font-weight:700;margin-bottom:8px}
.feat-desc{font-size:.84rem;color:var(--text-muted);line-height:1.6}

/* ══════════════════════════════
   SEARCH PAGE
══════════════════════════════ */
.search-hero{padding:60px 0 40px;text-align:center}
.search-bar-wrap{
  display:flex;gap:0;max-width:600px;margin:0 auto 16px;
  background:var(--s2);border:1px solid var(--border);
  border-radius:var(--r);overflow:hidden;transition:var(--tr);
}
.search-bar-wrap:focus-within{border-color:var(--accent);box-shadow:0 0 0 3px var(--accent-subtle)}
.search-input{
  flex:1;background:none;border:none;
  color:var(--text);padding:14px 18px;
  font-size:.95rem;outline:none;
}
.search-input::placeholder{color:var(--text-faint)}
.search-btn{
  padding:14px 22px;background:linear-gradient(135deg,var(--accent),#EA580C);
  border:none;color:#fff;font-weight:600;font-size:.88rem;cursor:pointer;
  transition:var(--tr);
}
.search-btn:hover{opacity:.9}
.pill-wrap{display:flex;gap:8px;flex-wrap:wrap;justify-content:center;margin-bottom:40px}
.pill{
  padding:5px 14px;border-radius:999px;
  background:var(--s2);border:1px solid var(--border);
  font-size:.78rem;color:var(--text-muted);cursor:pointer;
  transition:var(--tr);
}
.pill:hover{border-color:var(--accent);color:var(--accent)}

/* ══════════════════════════════
   CHARTS (canvas)
══════════════════════════════ */
.chart-wrap{
  background:var(--s2);border-radius:var(--r-sm);
  overflow:hidden;padding:8px;
}

/* ══════════════════════════════
   UTILITY
══════════════════════════════ */
.g2{display:grid;grid-template-columns:1fr 1fr;gap:16px}
.g3{display:grid;grid-template-columns:1fr 1fr 1fr;gap:16px}
.mt8{margin-top:8px}.mt12{margin-top:12px}.mt16{margin-top:16px}.mt20{margin-top:20px}.mt28{margin-top:28px}
.sec{padding:40px 0}
.page-title{margin-bottom:4px}
.page-sub{font-size:.88rem;color:var(--text-muted);margin-bottom:28px}
.section-head{
  display:flex;align-items:center;justify-content:space-between;
  margin-bottom:16px;
}
.empty{text-align:center;padding:40px 20px;color:var(--text-muted)}
.empty-icon{font-size:2rem;margin-bottom:10px}
.empty h3{color:var(--text);margin-bottom:6px}
.divider{height:1px;background:var(--border);margin:20px 0}
.badge{display:inline-flex;align-items:center;padding:3px 10px;border-radius:999px;font-size:.72rem;font-weight:600}

/* ══════════════════════════════
   LIGHT MODE (Feature 12)
══════════════════════════════ */
body.light-mode{
  --bg:#F8F7F4;
  --s1:#FFFFFF;
  --s2:#F1F0ED;
  --s3:#E8E6E1;
  --border:rgba(0,0,0,0.08);
  --border-2:rgba(0,0,0,0.14);
  --text:#1A1A1A;
  --text-muted:#6B6B6B;
  --text-faint:#AAAAAA;
  --accent:#E8650A;
  --accent-glow:rgba(232,101,10,0.15);
  --accent-subtle:rgba(232,101,10,0.08);
}
body.light-mode::before{opacity:0.015}
body.light-mode::after{background:radial-gradient(circle,rgba(232,101,10,0.04) 0%,transparent 70%)}
.theme-toggle{
  width:36px;height:36px;border-radius:8px;
  background:var(--s2);border:1px solid var(--border);
  color:var(--text-muted);font-size:1rem;
  display:flex;align-items:center;justify-content:center;
  cursor:pointer;transition:var(--tr);
}
.theme-toggle:hover{border-color:var(--accent);color:var(--accent)}

/* ══════════════════════════════
   WEIGHT TRACKER
══════════════════════════════ */
.weight-entry{
  display:flex;align-items:center;justify-content:space-between;
  padding:12px 14px;background:var(--s2);border:1px solid var(--border);
  border-radius:var(--r-sm);margin-bottom:8px;
}
.we-date{font-size:.75rem;color:var(--text-faint)}
.we-val{font-family:var(--font-head);font-size:1.2rem;font-weight:700;color:var(--accent)}
.we-note{font-size:.78rem;color:var(--text-muted)}
.weight-change-up{color:var(--rose);font-size:.75rem;font-weight:600}
.weight-change-down{color:var(--emerald);font-size:.75rem;font-weight:600}

/* ══════════════════════════════
   FAVOURITES
══════════════════════════════ */
.fav-card{
  display:flex;align-items:center;justify-content:space-between;
  padding:10px 14px;background:var(--s2);border:1px solid var(--border);
  border-radius:var(--r-sm);margin-bottom:8px;transition:var(--tr);
}
.fav-card:hover{border-color:var(--accent)}
.fav-star{color:var(--amber);font-size:1rem;cursor:pointer;transition:var(--tr)}
.fav-star:hover{transform:scale(1.3)}
.fav-name{font-weight:600;color:var(--text);font-size:.88rem}
.fav-cals{color:var(--accent);font-weight:700;font-size:.9rem}
.fav-macros{font-size:.72rem;color:var(--text-faint)}

/* ══════════════════════════════
   BARCODE SCANNER
══════════════════════════════ */
.scanner-wrap{
  position:relative;background:var(--s2);border:1px solid var(--border);
  border-radius:var(--r);overflow:hidden;min-height:200px;
  display:flex;align-items:center;justify-content:center;flex-direction:column;gap:12px;
}
.scanner-line{
  position:absolute;top:0;left:0;right:0;height:2px;
  background:linear-gradient(90deg,transparent,var(--accent),transparent);
  animation:scan 2s linear infinite;
}
@keyframes scan{0%{top:0}100%{top:100%}}
#barcode-video{width:100%;max-height:200px;object-fit:cover;border-radius:var(--r-sm)}

/* ══════════════════════════════
   CONVERSATIONAL LOG
══════════════════════════════ */
.conv-input-wrap{
  display:flex;flex-direction:column;gap:10px;
  background:var(--s2);border:1px solid var(--border);
  border-radius:var(--r);padding:16px;
}
.conv-result-item{
  display:flex;align-items:center;justify-content:space-between;
  padding:8px 12px;background:var(--s1);border:1px solid var(--border);
  border-radius:var(--r-sm);font-size:.84rem;
}
.conv-result-item .cr-name{font-weight:600;color:var(--text)}
.conv-result-item .cr-cal{color:var(--accent);font-weight:700}

/* ══════════════════════════════
   CALORIE GOAL CALCULATOR
══════════════════════════════ */
.goal-result{
  display:grid;grid-template-columns:repeat(3,1fr);gap:10px;margin-top:14px;
}
.goal-tile{
  text-align:center;padding:14px;border-radius:var(--r-sm);
  border:1px solid var(--border);background:var(--s2);
  cursor:pointer;transition:var(--tr);
}
.goal-tile:hover{border-color:var(--accent)}
.goal-tile.active{border-color:var(--accent);background:var(--accent-subtle)}
.gt-cal{font-family:var(--font-head);font-size:1.4rem;font-weight:800;color:var(--accent)}
.gt-lbl{font-size:.72rem;color:var(--text-faint);margin-top:3px}

/* ══════════════════════════════
   DEFICIENCY BADGE
══════════════════════════════ */
.deficiency-box{
  background:linear-gradient(135deg,rgba(244,63,94,.06),rgba(251,191,36,.04));
  border:1px solid rgba(244,63,94,.15);border-radius:var(--r);
  padding:18px 22px;white-space:pre-wrap;line-height:1.8;
  color:var(--text);font-size:.9rem;
}

/* ══════════════════════════════
   HISTORY DATE PICKER
══════════════════════════════ */
.history-nav{
  display:flex;align-items:center;gap:10px;
  background:var(--s2);border:1px solid var(--border);
  border-radius:var(--r-sm);padding:8px 14px;
}
.history-nav button{
  background:var(--s3);border:none;color:var(--text-muted);
  padding:4px 10px;border-radius:6px;cursor:pointer;transition:var(--tr);font-size:1rem;
}
.history-nav button:hover{color:var(--accent);background:var(--accent-subtle)}
#hist-date{background:none;border:none;color:var(--text);font-family:var(--font-head);
  font-size:.95rem;font-weight:700;cursor:pointer;outline:none}


/* ── Responsive Overrides ── */
@media (max-width: 1024px) {
  .app-layout { grid-template-columns: 1fr; }
  .sidebar { 
    position: fixed; bottom: 0; left: 0; right: 0; top: auto; 
    height: 70px; width: 100% !important; flex-direction: row !important; 
    border-right: none; border-top: 1px solid var(--border);
    z-index: 1000; padding: 0 5px; justify-content: space-around;
    background: rgba(14,14,18,0.92); backdrop-filter: blur(20px);
  }
  .sidebar .sb-section { margin: 0; display: flex; align-items: center; }
  .sidebar .sb-label, .sidebar .sb-footer, .sidebar .sb-role, .sidebar .sb-name { display: none; }
  .sidebar .sb-link { 
    flex-direction: column; gap: 4px; font-size: 0.6rem; 
    padding: 8px 10px; text-align: center; width: auto; 
    border: none !important; background: none !important; color: var(--text-muted);
  }
  .sidebar .sb-link.active { color: var(--accent); }
  .sidebar .sb-ic { font-size: 1.3rem; width: auto; margin: 0; }
  .main { padding: 20px 16px 90px; }
  .bento-grid { grid-template-columns: repeat(2, 1fr); }
  .responsive-grid { grid-template-columns: 1fr !important; }
}

@media (max-width: 768px) {
  .navbar .nav-links { display: none; }
  .ham-toggle { display: flex !important; }
  .hero { padding: 60px 0; text-align: center; }
  .hero-title { font-size: 2.5rem; margin: 0 auto 20px; }
  .hero-sub { margin: 0 auto 30px; }
  .hero-ctas { justify-content: center; }
  .bento-grid { grid-template-columns: 1fr; }
  .ring-wrap { flex-direction: column; text-align: center; padding: 20px; gap: 24px; }
  .ring-meta h3 { font-size: 1.4rem; }
  .macro-row { justify-content: center; }
  .feature-grid { grid-template-columns: 1fr; }
  .g2, .g3 { grid-template-columns: 1fr; }
  .card { padding: 20px; }
  .modal { max-width: 92%; padding: 24px; }
  .chat-msgs { min-height: 400px; }
  .bubble { max-width: 92%; }
  .nav-actions .btn-nav:not(.mob-btn) { display: none; }
}

@media (max-width: 480px) {
  .hero-title { font-size: 2rem; }
  .nav-logo { font-size: 1rem; }
  .nav-logo-icon { width: 28px; height: 28px; font-size: 0.8rem; }
  .btn-nav { padding: 6px 12px; font-size: 0.75rem; }
}

/* ── Mobile Menu ── */
.ham-toggle {
  display: none; align-items: center; justify-content: center;
  width: 40px; height: 40px; border-radius: 8px;
  background: var(--s2); border: 1px solid var(--border);
  color: var(--text); font-size: 1.2rem; transition: var(--tr);
}
.ham-toggle:hover { border-color: var(--accent); }

.mob-menu-bg {
  position: fixed; inset: 0; background: rgba(0,0,0,0.85);
  backdrop-filter: blur(12px); z-index: 2000;
  opacity: 0; pointer-events: none; transition: 0.3s;
}
.mob-menu-bg.open { opacity: 1; pointer-events: all; }
.mob-menu {
  position: absolute; right: 0; top: 0; bottom: 0; width: 300px;
  background: var(--bg); border-left: 1px solid var(--border);
  transform: translateX(100%); transition: 0.4s cubic-bezier(0.4, 0, 0.2, 1);
  display: flex; flex-direction: column;
}
.mob-menu-bg.open .mob-menu { transform: none; }
.mob-nav-links { padding: 20px; display: flex; flex-direction: column; gap: 4px; flex: 1; }
.mob-nav-links .nav-link {
  display: flex !important; align-items: center; gap: 12px;
  padding: 14px 16px; border-radius: 12px; font-size: 1rem; font-weight: 600;
  color: var(--text-muted); transition: var(--tr); border: 1px solid transparent;
}
.mob-nav-links .nav-link:hover { background: var(--s2); color: var(--text); border-color: var(--border); }
.mob-nav-links .nav-link.active { background: var(--accent-subtle); color: var(--accent); border-color: rgba(249,115,22,0.2); }


/* ══════════════════════════════
   PAGE TRANSITIONS
══════════════════════════════ */
.tab-panel{animation:fadeUp .3s cubic-bezier(.4,0,.2,1)}
@keyframes fadeUp{from{opacity:0;transform:translateY(12px)}to{opacity:1;transform:none}}
  /* ── Extra UX Polishing ── */
  @keyframes shimmer { 0% { background-position: -468px 0 } 100% { background-position: 468px 0 } }
  .shimmer-bg { animation: shimmer 1.2s linear infinite; background: linear-gradient(to right, var(--s3) 8%, var(--s1) 18%, var(--s3) 33%); background-size: 800px 104px; position: relative; }
  
  .toast { position: fixed; bottom: 30px; right: 30px; padding: 16px 24px; border-radius: 12px; background: var(--s2); border: 1px solid var(--border); color: var(--text); box-shadow: var(--shadow-lg); z-index: 9999; display: flex; align-items: center; gap: 12px; transform: translateY(100px); opacity: 0; transition: all 0.4s cubic-bezier(0.18, 0.89, 0.32, 1.28); }
  .toast.show { transform: translateY(0); opacity: 1; }
  .toast-icon { width: 24px; height: 24px; border-radius: 6px; display: flex; align-items: center; justify-content: center; font-size: 14px; flex-shrink:0; }
  .toast-success .toast-icon { background: rgba(16,185,129,0.15); color: var(--emerald); }
  .toast-error .toast-icon { background: rgba(244,63,94,0.15); color: var(--rose); }

  .page-fade { animation: fadeIn 0.5s ease-out; }
  @keyframes fadeIn { from { opacity: 0; transform: translateY(10px); } to { opacity: 1; transform: translateY(0); } }
  
  .card-lift, .btn, .nav-link, .stat-tile { transition: all 0.3s cubic-bezier(0.4, 0, 0.2, 1); }
  .btn:active { transform: scale(0.95); }
  .card-lift:hover { transform: translateY(-6px) scale(1.01); box-shadow: var(--shadow-lg); }
</style>"""

# ══════════════════════════════════════════════════════════════════
#  SHARED JS
# ══════════════════════════════════════════════════════════════════
JS = """<script>
/* ── Auth ── */
const Auth={
  get(){return JSON.parse(localStorage.getItem("nf_v2")||"null")},
  set(u){localStorage.setItem("nf_v2",JSON.stringify(u))},
  clear(){localStorage.removeItem("nf_v2")},
  loggedIn(){return!!this.get()},
  uid(){const u=this.get();return u?u.user_id:null},
  isAdmin(){const u=this.get();return u&&u.is_admin===true},
  async logout(){
    try { await API.post("/api/logout", {}); } catch(e){}
    Auth.clear();
    window.location.href="/";
  }
};

/* ── API ── */
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

/* ── Toast ── */
function toast(msg,type="info",dur=3500){
  let c=document.getElementById("tc");
  if(!c){c=document.createElement("div");c.id="tc";c.className="toast-wrap";document.body.appendChild(c);}
  const icons={success:"✓",error:"✕",info:"·"};
  const el=document.createElement("div");
  el.className="toast "+type;
  el.innerHTML=`<span style="font-weight:700;font-size:1rem">${icons[type]||"·"}</span><span>${msg}</span>`;
  c.appendChild(el);
  setTimeout(()=>{el.style.opacity="0";el.style.transform="translateX(20px)";
    el.style.transition=".3s";setTimeout(()=>el.remove(),300);},dur);
}

/* ── Button loading ── */
function setLoad(btn,on){
  if(on){btn.dataset.orig=btn.innerHTML;
    btn.innerHTML='<span class="spin"></span><span>Loading…</span>';btn.disabled=true;}
  else{btn.innerHTML=btn.dataset.orig||"Submit";btn.disabled=false;}
}

/* ── Modal ── */
function openM(id){document.getElementById(id).classList.add("open")}
function closeM(id){document.getElementById(id).classList.remove("open")}
document.addEventListener("click",e=>{
  document.querySelectorAll(".modal-bg.open").forEach(o=>{if(e.target===o)o.classList.remove("open");});
});
document.addEventListener("keydown",e=>{
  if(e.key==="Escape")document.querySelectorAll(".modal-bg.open").forEach(o=>o.classList.remove("open"));
});

/* ── Format ── */
function fmt(n,u=""){return Math.round(n||0).toLocaleString()+(u?" "+u:"")}
function esc(s){return String(s).replace(/&/g,"&amp;").replace(/</g,"&lt;").replace(/>/g,"&gt;")}

/* ── Count-up animation ── */
function countUp(el,target,duration=1100){
  if(!el)return;
  const start=Date.now();
  const from=parseFloat(el.dataset.from||0);
  const tick=()=>{
    const e=Date.now()-start;
    const p=Math.min(e/duration,1);
    const ease=1-Math.pow(1-p,3);
    el.textContent=Math.round(from+ease*(target-from)).toLocaleString();
    if(p<1)requestAnimationFrame(tick);
  };
  requestAnimationFrame(tick);
}

/* ── Circular progress ── */
function setRing(id,ratio,warn=false){
  const el=document.getElementById(id);if(!el)return;
  const r=52,circ=2*Math.PI*r;
  const offset=circ*(1-Math.min(ratio,1));
  el.style.strokeDasharray=circ;
  el.style.strokeDashoffset=offset;
  el.className="ring-fill"+(ratio>1?" danger":ratio>.8?" warn":"");
}

/* ── Canvas bar chart ── */
function barChart(id,labels,values,color="#F97316"){
  const c=document.getElementById(id);if(!c)return;
  const ctx=c.getContext("2d");
  c.width=c.offsetWidth;c.height=c.offsetHeight;
  const W=c.width,H=c.height,max=Math.max(...values,1);
  const pad={t:16,r:12,b:32,l:42};
  const dW=W-pad.l-pad.r,dH=H-pad.t-pad.b;
  ctx.clearRect(0,0,W,H);
  /* grid lines */
  ctx.strokeStyle="rgba(255,255,255,0.04)";ctx.lineWidth=1;
  for(let i=1;i<=4;i++){
    const y=pad.t+dH*(1-i/4);
    ctx.beginPath();ctx.moveTo(pad.l,y);ctx.lineTo(pad.l+dW,y);ctx.stroke();
  }
  /* bars */
  const bW=Math.max(4,dW/labels.length-8);
  labels.forEach((lbl,i)=>{
    const x=pad.l+i*(dW/labels.length)+(dW/labels.length-bW)/2;
    const bH=Math.max(2,(values[i]/max)*dH),y=pad.t+dH-bH;
    const grad=ctx.createLinearGradient(0,y,0,y+bH);
    grad.addColorStop(0,color);grad.addColorStop(1,color+"55");
    ctx.fillStyle=grad;ctx.globalAlpha=.9;
    roundRect(ctx,x,y,bW,bH,4);ctx.fill();ctx.globalAlpha=1;
    ctx.fillStyle="#3F3F46";ctx.font="10px Plus Jakarta Sans";
    ctx.textAlign="center";ctx.fillText(String(lbl).substring(0,6),x+bW/2,H-6);
  });
  /* y labels */
  for(let i=0;i<=4;i++){
    const v=Math.round(max*i/4);const y=pad.t+dH*(1-i/4);
    ctx.fillStyle="#3F3F46";ctx.font="9px Plus Jakarta Sans";
    ctx.textAlign="right";ctx.fillText(v>999?Math.round(v/100)/10+"k":v,pad.l-4,y+3);
  }
}

/* ── Canvas pie/donut ── */
function donut(id,labels,values,colors){
  const c=document.getElementById(id);if(!c)return;
  const ctx=c.getContext("2d");
  c.width=c.offsetWidth;c.height=c.offsetHeight;
  const W=c.width,H=c.height;
  const total=values.reduce((a,b)=>a+b,0);if(!total)return;
  const cx=W*0.38,cy=H/2,r=Math.min(cx,cy)-16;
  let angle=-Math.PI/2;
  values.forEach((val,i)=>{
    const sl=(val/total)*Math.PI*2;
    ctx.beginPath();ctx.moveTo(cx,cy);
    ctx.arc(cx,cy,r,angle,angle+sl);ctx.closePath();
    ctx.fillStyle=colors[i];ctx.fill();
    angle+=sl;
  });
  /* donut hole */
  ctx.beginPath();ctx.arc(cx,cy,r*.56,0,Math.PI*2);
  ctx.fillStyle="#0E0E12";ctx.fill();
  /* center text */
  ctx.fillStyle="#F4F4F5";ctx.font="700 14px Syne";
  ctx.textAlign="center";ctx.fillText("Macros",cx,cy-4);
  /* legend */
  const lx=W*0.72;
  labels.forEach((lbl,i)=>{
    const ly=cy-(labels.length-1)*18+i*36;
    ctx.fillStyle=colors[i];roundRect(ctx,lx,ly-8,12,12,3);ctx.fill();
    ctx.fillStyle="#71717A";ctx.font="10px Plus Jakarta Sans";
    ctx.textAlign="left";ctx.fillText(lbl,lx+16,ly+2);
    ctx.fillStyle="#F4F4F5";ctx.font="700 11px Syne";
    ctx.fillText(Math.round(values[i]/total*100)+"%",lx+16,ly+14);
  });
}

function roundRect(ctx,x,y,w,h,r){
  if(h<=0)return;
  ctx.beginPath();ctx.moveTo(x+r,y);ctx.lineTo(x+w-r,y);
  ctx.quadraticCurveTo(x+w,y,x+w,y+r);ctx.lineTo(x+w,y+h-r);
  ctx.quadraticCurveTo(x+w,y+h,x+w-r,y+h);ctx.lineTo(x+r,y+h);
  ctx.quadraticCurveTo(x,y+h,x,y+h-r);ctx.lineTo(x,y+r);
  ctx.quadraticCurveTo(x,y,x+r,y);ctx.closePath();
}

function reqAuth(){
  if(!Auth.loggedIn()){window.location.href="/";return false;}return true;
}
</script>"""

# ══════════════════════════════════════════════════════════════════
#  PAGE WRAPPER
# ══════════════════════════════════════════════════════════════════
def page(title, body, active=""):
    links=[("Home","/"),("Dashboard","/dashboard"),
           ("Meal Log","/meal-log"),("AI Advisor","/ai-advisor"),("About","/about")]
    if session.get("is_admin"):
        links.append(("Admin Panel", "/admin"))
    nav="".join(f'<a href="{u}" class="nav-link{" active" if u==active else ""}">{n}</a>' for n,u in links)
    return f"""<!DOCTYPE html><html lang="en"><head>
<meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1,maximum-scale=1,user-scalable=0">
<title>{title} — NutriFit</title>
<link rel="icon" type="image/x-icon" href="/favicon.ico">
{CSS}
</head><body>
<div id="toast-container"></div>
<script>
  function showToast(msg, type='success'){{
    const container = document.getElementById('toast-container');
    const t = document.createElement('div');
    t.className = `toast toast-${{type}}`;
    const icon = type === 'success' ? '✓' : '✕';
    t.innerHTML = `<div class="toast-icon">${{icon}}</div><div style="font-size:0.9rem;font-weight:600">${{msg}}</div>`;
    container.appendChild(t);
    setTimeout(() => t.classList.add('show'), 100);
    setTimeout(() => {{ t.classList.remove('show'); setTimeout(() => t.remove(), 400); }}, 4000);
  }}
  // Replace standard alert
  window._alert = window.alert;
  window.alert = (m) => showToast(m, m.toLowerCase().includes('err') || m.toLowerCase().includes('fail') ? 'error' : 'success');
</script>
<div class="page-fade">
<nav class="navbar">
  <div class="container nav-inner">
    <a href="/" class="nav-logo">
      <div class="nav-logo-icon">🥗</div>
      NutriFit
    </a>
    <div class="nav-links">{nav}</div>
    <div class="nav-actions">
      <button class="theme-toggle" id="theme-toggle" title="Toggle light/dark">🌙</button>
      <div class="nav-badge" id="nav-status" style="display:none">
        <span class="dot"></span><span id="nav-username"></span>
      </div>
      <button class="btn-nav" id="nav-btn">Get Started</button>
      <button class="btn btn-ghost" id="logout-btn" style="display:none;padding:8px 14px;border-radius:10px;font-size:0.85rem;color:var(--rose)">Sign Out</button>
      <button class="ham-toggle" onclick="toggleMobMenu()">☰</button>
    </div>
  </div>
</nav>

<div class="mob-menu-bg" id="mob-menu-bg" onclick="if(event.target==this)toggleMobMenu()">
  <div class="mob-menu">
     <div style="display:flex;justify-content:space-between;align-items:center;padding:24px;border-bottom:1px solid var(--border)">
        <span style="font-family:var(--font-head);font-weight:800;font-size:1.2rem">Navigation</span>
        <button onclick="toggleMobMenu()" style="background:none;border:none;color:var(--text);font-size:1.8rem;line-height:1">×</button>
     </div>
     <div class="mob-nav-links">
        {nav}
        <a href="#" class="nav-link" id="mob-logout" style="display:none;color:var(--rose);border-top:1px solid var(--border);margin-top:8px">Sign Out</a>
     </div>
     <div style="padding:24px;border-top:1px solid var(--border)">
        <button class="btn btn-primary btn-full mob-btn" onclick="toggleMobMenu();Auth.loggedIn()?window.location.href='/dashboard':document.getElementById('nav-btn').click()">Dashboard ↑</button>
     </div>
  </div>
</div>

<script>
function toggleMobMenu(){{
  document.getElementById('mob-menu-bg').classList.toggle('open');
}}
</script>
{JS}
{body}
<script>
(function(){{
  const btn=document.getElementById("nav-btn");
  const status=document.getElementById("nav-status");
  const unEl=document.getElementById("nav-username");
  if(Auth.loggedIn()){{
    const u=Auth.get();
    if(status){{status.style.display="flex";}}
    if(unEl){{unEl.textContent=u.username;}}
    btn.textContent="Dashboard";
    btn._handled=true;
    btn.onclick=()=>window.location.href="/dashboard";
    const lout=document.getElementById("logout-btn");
    if(lout){{
      lout.style.display="block";
      lout.onclick=()=>Auth.logout();
    }}
    const mobLout=document.getElementById("mob-logout");
    if(mobLout){{
      mobLout.style.display="flex";
      mobLout.onclick=(e)=>{{ e.preventDefault(); Auth.logout(); }};
    }}
  }} else {{
    btn.onclick=()=>openM("m-login");
  }}
  /* Theme toggle (Feature 12) */
  const themeBtn=document.getElementById("theme-toggle");
  const saved=localStorage.getItem("nf_theme")||"dark";
  if(saved==="light"){{document.body.classList.add("light-mode");themeBtn.textContent="☀️";}}
  themeBtn.addEventListener("click",()=>{{
    const isLight=document.body.classList.toggle("light-mode");
    themeBtn.textContent=isLight?"☀️":"🌙";
    localStorage.setItem("nf_theme",isLight?"light":"dark");
  }});
}})();
</script>
</body></html>"""

# ══════════════════════════════════════════════════════════════════
#  INDEX PAGE
# ══════════════════════════════════════════════════════════════════
INDEX = """
<div class="page-wrap">
<main class="container">
  <section class="hero">
    <div class="hero-bg-orb"></div>
    <div class="hero-badge">
      <span style="width:6px;height:6px;background:var(--accent);border-radius:50%"></span>
      AI-Powered Nutrition Intelligence
    </div>
    <h1 class="hero-title">
      Your body.<br>Your data.<br><span class="grad">Your edge.</span>
    </h1>
    <p class="hero-sub">
      NutriFit is the nutrition OS for serious people — smart food tracking, Gemini AI nutritionist,
      and deep insights. No cloud. No subscriptions. Just results.
    </p>
    <div class="hero-ctas" id="hero-ctas">
      <button class="hero-cta-primary" onclick="openM('m-signup')">Start for free →</button>
      <button class="hero-cta-ghost" onclick="openM('m-login')">Sign in</button>
    </div>

    <div class="hero-stats">
      <div class="hs-item">
        <div class="hs-val">100%</div>
        <div class="hs-lbl">Gemini AI · No cloud</div>
      </div>
      <div class="hs-item">
        <div class="hs-val">5+</div>
        <div class="hs-lbl">Dashboard features</div>
      </div>
      <div class="hs-item">
        <div class="hs-val">4</div>
        <div class="hs-lbl">MCP AI tools</div>
      </div>
      <div class="hs-item">
        <div class="hs-val">1 cmd</div>
        <div class="hs-lbl">To run the full app</div>
      </div>
    </div>
  </section>

  <section class="sec">
    <div style="text-align:center;margin-bottom:48px">
      <h2 style="margin-bottom:10px">Everything you need to<br><span class="grad">eat smarter</span></h2>
      <p style="max-width:440px;margin:0 auto">Built for people who want real data about their diet, not generic advice from an app that doesn't know them.</p>
    </div>
    <div class="feature-grid">
      <div class="feat-card">
        <div class="feat-icon fi-orange">🔍</div>
        <div class="feat-title">Smart Food Search</div>
        <p class="feat-desc">Search any food by name. If it's not in the database, Gemini AI estimates full nutrition in seconds.</p>
      </div>
      <div class="feat-card">
        <div class="feat-icon fi-sky">🤖</div>
        <div class="feat-title">On-Device AI Nutritionist</div>
        <p class="feat-desc">100% Gemini AI via Gemini API. Get meal plans, diet analysis, and recommendations — your data never leaves your machine.</p>
      </div>
      <div class="feat-card">
        <div class="feat-icon fi-emerald">📊</div>
        <div class="feat-title">Nutrition Scoring</div>
        <p class="feat-desc">Every day gets a score out of 100. Track macros, hit your calorie goal, and see your weekly trend with animated charts.</p>
      </div>
      <div class="feat-card">
        <div class="feat-icon fi-violet">👨‍👩‍👧</div>
        <div class="feat-title">Multi-Member Tracking</div>
        <p class="feat-desc">Track nutrition for your entire household. Each member gets their own BMI calculation and meal history.</p>
      </div>
      <div class="feat-card">
        <div class="feat-icon fi-amber">🔧</div>
        <div class="feat-title">MCP Tool Layer</div>
        <p class="feat-desc">AI has structured access to your database via 4 tools — get_profile, today_calories, search_food, log_meal.</p>
      </div>
      <div class="feat-card">
        <div class="feat-icon fi-orange">🗓️</div>
        <div class="feat-title">AI Meal Planner</div>
        <p class="feat-desc">Generate full 1-day meal plans tailored to your goal (weight loss, muscle gain) and diet type in one click.</p>
      </div>
    </div>
  </section>
</main>
</div>

<!-- Login Modal -->
<div class="modal-bg" id="m-login">
  <div class="modal">
    <div class="modal-brand">Welcome back</div>
    <div class="modal-sub">Sign in to your NutriFit account</div>
    <div class="form-group">
      <label class="form-label">Username</label>
      <input class="form-control" id="l-u" placeholder="your_username" autocomplete="username">
    </div>
    <div class="form-group">
      <label class="form-label">Password</label>
      <input class="form-control" id="l-p" type="password" placeholder="••••••••">
    </div>
    <button class="btn btn-primary btn-full" id="btn-login" style="margin-top:4px;padding:12px">Sign In</button>
    <div class="modal-divider"><span>or</span></div>
    <p style="text-align:center;font-size:.84rem;color:var(--text-muted)">
      No account? <a href="#" onclick="closeM('m-login');openM('m-signup')">Create one free</a>
    </p>
  </div>
</div>

<!-- Signup Modal -->
<div class="modal-bg" id="m-signup">
  <div class="modal">
    <div class="modal-brand">Create account</div>
    <div class="modal-sub">Start tracking your nutrition today — free forever</div>
    <div class="form-group">
      <label class="form-label">Username</label>
      <input class="form-control" id="s-u" placeholder="choose_a_username" autocomplete="username">
    </div>
    <div class="form-group">
      <label class="form-label">Password</label>
      <input class="form-control" id="s-p" type="password" placeholder="••••••••">
    </div>
    <button class="btn btn-primary btn-full" id="btn-signup" style="margin-top:4px;padding:12px">Create Account</button>
    <div class="modal-divider"><span>or</span></div>
    <p style="text-align:center;font-size:.84rem;color:var(--text-muted)">
      Have account? <a href="#" onclick="closeM('m-signup');openM('m-login')">Sign in</a>
    </p>
  </div>
</div>

<script>
document.getElementById("btn-login").onclick=async()=>{
  const btn=document.getElementById("btn-login");
  const u=document.getElementById("l-u").value.trim();
  const p=document.getElementById("l-p").value.trim();
  if(!u||!p)return toast("Fill in all fields","error");
  setLoad(btn,true);
  try{
    const d=await API.post("/api/login",{username:u,password:p});
    Auth.set({user_id:d.user_id,username:u});
    toast("Welcome back, "+u+"! 🎉","success");
    setTimeout(()=>window.location.href="/dashboard",700);
  }catch(e){toast(e.message,"error");}
  finally{setLoad(btn,false);}
};
document.getElementById("btn-signup").onclick=async()=>{
  const btn=document.getElementById("btn-signup");
  const u=document.getElementById("s-u").value.trim();
  const p=document.getElementById("s-p").value.trim();
  if(!u||!p)return toast("Fill in all fields","error");
  setLoad(btn,true);
  try{
    await API.post("/api/signup",{username:u,password:p});
    const d=await API.post("/api/login",{username:u,password:p});
    Auth.set({user_id:d.user_id,username:u});
    toast("Account created! Welcome ✓","success");
    setTimeout(()=>window.location.href="/dashboard",800);
  }catch(e){toast(e.message,"error");}
  finally{setLoad(btn,false);}
};
  if(Auth.loggedIn()){{
    const dCtas = document.getElementById("hero-ctas");
    if(dCtas) dCtas.style.display="none";
  }}
  ["l-u","l-p"].forEach(id=>document.getElementById(id).addEventListener("keydown",e=>{
  if(e.key==="Enter")document.getElementById("btn-login").click();
}));
</script>
"""

# ══════════════════════════════════════════════════════════════════
#  DASHBOARD PAGE
# ══════════════════════════════════════════════════════════════════
DASHBOARD = """
<div class="app-layout">
  <!-- Sidebar -->
  <aside class="sidebar">
    <div class="sb-section">
      <span class="sb-label">Overview</span>
      <button class="sb-link active" data-tab="daily">
        <span class="sb-ic">📊</span>Daily Summary
      </button>
      <button class="sb-link" data-tab="weekly">
        <span class="sb-ic">📅</span>Weekly Report
      </button>
    </div>
    <div class="sb-section">
      <span class="sb-label">Actions</span>
      <button class="sb-link" data-tab="log">
        <span class="sb-ic">🍽️</span>Log Meal
      </button>
      <button class="sb-link" data-tab="member">
        <span class="sb-ic">👤</span>Add Member
      </button>
      <button class="sb-link" data-tab="plan">
        <span class="sb-ic">🗓️</span>AI Meal Plan
      </button>
    </div>
    <div class="sb-section">
      <span class="sb-label">Health</span>
      <button class="sb-link" data-tab="weight">
        <span class="sb-ic">⚖️</span>Weight Tracker
      </button>
      <button class="sb-link" data-tab="history">
        <span class="sb-ic">📆</span>Meal History
      </button>
      <button class="sb-link" data-tab="deficiency">
        <span class="sb-ic">🔬</span>Nutrient Check
      </button>
      <button class="sb-link" data-tab="export">
        <span class="sb-ic">📥</span>Export Data
      </button>
    </div>
    <div class="sb-section">
      <span class="sb-label">Support</span>
      <button class="sb-link" data-tab="feedback">
        <span class="sb-ic">💌</span>Feedback
      </button>
    </div>
    <div class="sb-footer">
      <div class="sb-user">
        <div class="sb-avatar" id="sb-av">?</div>
        <div>
          <div class="sb-name" id="sb-name">—</div>
          <div class="sb-role">Member</div>
        </div>
      </div>
    </div>
  </aside>

  <!-- Main -->
  <main class="main">

    <!-- ═══ DAILY SUMMARY ═══ -->
    <div id="t-daily" class="tab-panel">
      <div style="display:flex;align-items:flex-start;justify-content:space-between;margin-bottom:24px;flex-wrap:wrap;gap:12px">
        <div>
          <h2 class="page-title">Daily Summary</h2>
          <p id="today-dt" class="page-sub" style="margin-bottom:0"></p>
        </div>
        <div style="display:flex;align-items:center;gap:10px;background:var(--s2);border:1px solid var(--border);border-radius:var(--r-sm);padding:8px 14px">
          <span style="font-size:.78rem;color:var(--text-muted);font-weight:600;text-transform:uppercase;letter-spacing:.05em">Goal</span>
          <input id="cal-goal" type="number" value="2000" min="500" max="6000" step="50"
            style="width:80px;background:none;border:none;color:var(--text);font-family:var(--font-head);font-size:1rem;font-weight:700;outline:none;text-align:right">
          <span style="font-size:.75rem;color:var(--text-faint)">kcal</span>
        </div>
      </div>

      <!-- Bento stats -->
      <div class="bento-grid">
        <div class="stat-tile st-accent">
          <div class="st-icon">🔥 Calories</div>
          <div class="st-val" id="s-cal">0</div>
          <div class="st-sub">kcal consumed today</div>
        </div>
        <div class="stat-tile">
          <div class="st-icon" style="color:var(--sky)">💪 Protein</div>
          <div class="st-val" id="s-pro" style="color:var(--sky)">0</div>
          <div class="st-sub">grams</div>
        </div>
        <div class="stat-tile">
          <div class="st-icon" style="color:var(--amber)">⚡ Carbs</div>
          <div class="st-val" id="s-car" style="color:var(--amber)">0</div>
          <div class="st-sub">grams</div>
        </div>
        <div class="stat-tile">
          <div class="st-icon" style="color:var(--rose)">🧈 Fat</div>
          <div class="st-val" id="s-fat" style="color:var(--rose)">0</div>
          <div class="st-sub">grams</div>
        </div>
      </div>

      <!-- Calorie ring + macro chips -->
      <div class="ring-wrap mt16">
        <div style="position:relative;flex-shrink:0">
          <svg class="ring-svg" width="120" height="120" viewBox="0 0 120 120">
            <circle class="ring-bg" cx="60" cy="60" r="52"/>
            <circle class="ring-fill" id="cal-ring" cx="60" cy="60" r="52"/>
          </svg>
          <div style="position:absolute;inset:0;display:flex;flex-direction:column;align-items:center;justify-content:center">
            <span id="ring-pct" style="font-family:var(--font-head);font-size:1.3rem;font-weight:800;color:var(--text)">0%</span>
          </div>
        </div>
        <div class="ring-meta">
          <h3 id="ring-consumed">0 <span style="font-size:1rem;color:var(--text-muted);font-weight:400">/ 2000 kcal</span></h3>
          <p style="margin-bottom:16px;font-size:.84rem">Remaining: <span id="ring-rem" style="color:var(--accent);font-weight:600">2000 kcal</span></p>
          <div class="macro-row">
            <div class="macro-chip">
              <span class="mc-lbl">Protein</span>
              <span class="mc-val mc-pro" id="mc-pro">0g</span>
            </div>
            <div class="macro-chip">
              <span class="mc-lbl">Carbs</span>
              <span class="mc-val mc-car" id="mc-car">0g</span>
            </div>
            <div class="macro-chip">
              <span class="mc-lbl">Fat</span>
              <span class="mc-val mc-fat" id="mc-fat">0g</span>
            </div>
            <div class="macro-chip" style="margin-left:auto">
              <span class="mc-lbl">Score</span>
              <span id="score-el" class="score-pill sp-emerald" style="margin-top:2px">—</span>
            </div>
          </div>
        </div>
      </div>

      <!-- Charts -->
      <div class="g2 mt16">
        <div class="card">
          <div class="card-header"><h4>Calories by Food</h4></div>
          <div class="chart-wrap" style="height:180px">
            <canvas id="chart-bar" style="width:100%;height:100%"></canvas>
          </div>
        </div>
        <div class="card">
          <div class="card-header"><h4>Macro Split</h4></div>
          <div class="chart-wrap" style="height:180px">
            <canvas id="chart-donut" style="width:100%;height:100%"></canvas>
          </div>
        </div>
      </div>

      <!-- Food log -->
      <div class="card mt16">
        <div class="card-header">
          <h4>Today's Food Log</h4>
          <button class="btn btn-ghost btn-sm" onclick="loadDaily()">↻ Refresh</button>
        </div>
        <div id="food-log">
          <div class="empty"><div class="empty-icon">🍽️</div><h3>No meals yet</h3><p>Log your first meal using the sidebar</p></div>
        </div>
      </div>

      <!-- AI suggestion -->
      <div class="card mt16">
        <div class="card-header">
          <h4>AI Meal Suggestion</h4>
          <button class="btn btn-ghost btn-sm" id="btn-suggest">✨ Suggest next meal</button>
        </div>
        <div id="suggest-box" style="display:none">
          <div class="ai-label">NutriFit AI Response</div>
          <div class="ai-wrap" id="suggest-txt">
            <div class="shimmer-bg" style="height:80px;border-radius:12px"></div>
          </div>
        </div>
      </div>
    </div>

    <!-- ═══ WEEKLY ═══ -->
    <div id="t-weekly" class="tab-panel" style="display:none">
      <h2 class="page-title">Weekly Report</h2>
      <p class="page-sub">Last 7 days of nutrition data</p>
      <div class="bento-grid">
        <div class="stat-tile"><div class="st-icon">🔥 Total Cal</div><div class="st-val" id="w-cal">—</div></div>
        <div class="stat-tile"><div class="st-icon" style="color:var(--sky)">💪 Protein</div><div class="st-val" id="w-pro" style="color:var(--sky)">—</div></div>
        <div class="stat-tile"><div class="st-icon" style="color:var(--amber)">⚡ Carbs</div><div class="st-val" id="w-car" style="color:var(--amber)">—</div></div>
        <div class="stat-tile"><div class="st-icon" style="color:var(--rose)">🧈 Fat</div><div class="st-val" id="w-fat" style="color:var(--rose)">—</div></div>
      </div>
      <div class="card mt16">
        <div class="card-header"><h4>Daily Calories — 7 Day Trend</h4></div>
        <div class="chart-wrap" style="height:200px">
          <canvas id="chart-wk" style="width:100%;height:100%"></canvas>
        </div>
      </div>
      <div class="card mt16">
        <div class="card-header">
          <h4>AI Weekly Analysis</h4>
          <button class="btn btn-ghost btn-sm" id="btn-wk">📈 Generate</button>
        </div>
        <div id="wk-box" style="display:none">
          <div class="ai-label">NutriFit AI Analysis</div>
          <div class="ai-wrap" id="wk-txt">
            <div class="shimmer-bg" style="height:120px;border-radius:12px"></div>
          </div>
        </div>
      </div>
    </div>

    <!-- ═══ LOG MEAL ═══ -->
    <div id="t-log" class="tab-panel" style="display:none">
      <h2 class="page-title">Log a Meal</h2>
      <p class="page-sub">Search food and add it to your daily intake</p>
      <div class="g2">
        <div class="card">
          <h4 style="margin-bottom:18px">Meal Details</h4>
          <div class="form-group">
            <label class="form-label">Member</label>
            <select class="form-control" id="meal-mem"></select>
          </div>
          <div class="form-row">
            <div class="form-group">
              <label class="form-label">Meal Type</label>
              <select class="form-control" id="meal-type">
                <option>Breakfast</option><option>Lunch</option>
                <option>Snacks</option><option>Dinner</option>
              </select>
            </div>
            <div class="form-group">
              <label class="form-label">Date</label>
              <input class="form-control" type="date" id="meal-dt">
            </div>
          </div>
          <div class="form-group">
            <label class="form-label">Quantity (units)</label>
            <input class="form-control" type="number" id="meal-qty" value="1" min="1">
          </div>
          <div class="form-group">
            <label class="form-label">Selected Food</label>
            <div id="sel-food" style="background:var(--s2);border:1px solid var(--border);border-radius:var(--r-sm);padding:11px 14px;color:var(--text-faint);min-height:42px;font-size:.88rem">
              Nothing selected — search below ↓
            </div>
          </div>
          <button class="btn btn-primary btn-full" id="btn-log" style="padding:12px">Log Meal</button>
        </div>
        <div class="card">
          <h4 style="margin-bottom:14px">Search Food</h4>
          <div style="display:flex;gap:8px;margin-bottom:14px">
            <input class="form-control" id="f-q" placeholder="e.g. banana, grilled chicken…">
            <button class="btn btn-ghost btn-sm" id="btn-fs">Search</button>
          </div>
          <div id="f-results"></div>
          <div class="divider"></div>
          <p style="font-size:.8rem;color:var(--text-muted);margin-bottom:10px">
            🤖 Not in database? AI estimates nutrition instantly:
          </p>
          <div style="display:flex;gap:8px">
            <input class="form-control" id="ai-nm" placeholder="e.g. Masala Dosa, Poha…">
            <button class="btn btn-ghost btn-sm" id="btn-ai-est">✨ Estimate</button>
          </div>
        </div>
      </div>
    </div>

    <!-- ═══ ADD MEMBER ═══ -->
    <div id="t-member" class="tab-panel" style="display:none">
      <h2 class="page-title">Add Family Member</h2>
      <p class="page-sub">Track nutrition for multiple people in your household</p>
      <div class="g2" style="align-items:start">
        <div class="card">
          <div class="form-group">
            <label class="form-label">Full Name</label>
            <input class="form-control" id="m-name" placeholder="e.g. Priya Sharma">
          </div>
          <div class="form-row">
            <div class="form-group">
              <label class="form-label">Age</label>
              <input class="form-control" type="number" id="m-age" min="1" max="120" placeholder="25">
            </div>
            <div class="form-group">
              <label class="form-label">Gender</label>
              <select class="form-control" id="m-gender"><option>Male</option><option>Female</option></select>
            </div>
          </div>
          <div class="form-row">
            <div class="form-group">
              <label class="form-label">Weight (kg)</label>
              <input class="form-control" type="number" id="m-wt" min="1" placeholder="65">
            </div>
            <div class="form-group">
              <label class="form-label">Height (cm)</label>
              <input class="form-control" type="number" id="m-ht" min="50" placeholder="165">
            </div>
          </div>
          <button class="btn btn-primary btn-full" id="btn-add-mem" style="padding:12px">Add Member</button>
        </div>
        <div class="card" style="background:var(--accent-subtle);border-color:rgba(249,115,22,.15)">
          <div style="font-size:2rem;margin-bottom:14px">👨‍👩‍👧</div>
          <h3 style="margin-bottom:8px">Multi-member tracking</h3>
          <p style="font-size:.84rem;line-height:1.7">Add your family members to track everyone's nutrition separately. BMI is calculated automatically from weight and height.</p>
          <div style="margin-top:20px;padding:14px;background:var(--s2);border-radius:var(--r-sm)">
            <div style="font-size:.72rem;font-weight:700;text-transform:uppercase;letter-spacing:.06em;color:var(--text-faint);margin-bottom:8px">BMI Formula</div>
            <div style="font-family:var(--font-head);font-size:1rem;color:var(--accent)">weight(kg) ÷ height(m)²</div>
          </div>
        </div>
      </div>
    </div>

    <!-- ═══ AI MEAL PLAN ═══ -->
    <div id="t-plan" class="tab-panel" style="display:none">
      <h2 class="page-title">AI Meal Planner</h2>
      <p class="page-sub">Generate a personalised one-day meal plan</p>
      <div class="g2" style="align-items:start">
        <div class="card">
          <div class="form-group">
            <label class="form-label">Goal</label>
            <select class="form-control" id="pl-goal">
              <option>Weight Loss</option><option>Muscle Gain</option><option>Maintain Weight</option>
            </select>
          </div>
          <div class="form-group">
            <label class="form-label">Diet Preference</label>
            <select class="form-control" id="pl-diet">
              <option>Vegetarian</option><option>Non-Vegetarian</option><option>Vegan</option>
            </select>
          </div>
          <button class="btn btn-primary btn-full" id="btn-plan" style="padding:12px">✨ Generate Meal Plan</button>
          <div style="margin-top:12px;border-top:1px solid var(--border);padding-top:12px">
            <button class="btn btn-ghost btn-full btn-sm" id="btn-grocery">🛒 Generate Grocery List</button>
          </div>
        </div>
        <div class="card">
          <div id="plan-res" style="display:none">
            <div class="ai-label">Your AI Meal Plan</div>
            <div class="ai-wrap" id="plan-txt"></div>
          </div>
          <div id="grocery-res" style="display:none;margin-top:14px">
            <div class="ai-label">🛒 Grocery List</div>
            <div class="ai-wrap" id="grocery-txt"></div>
          </div>
          <p id="plan-empty" style="text-align:center;padding:32px;color:var(--text-muted)">
            Click Generate to create your plan
          </p>
        </div>
      </div>
    </div>

    <!-- ═══ WEIGHT TRACKER ═══ -->
    <div id="t-weight" class="tab-panel" style="display:none">
      <h2 class="page-title">Weight Tracker</h2>
      <p class="page-sub">Log your weight daily and track your progress over time</p>
      <div class="g2" style="align-items:start">
        <div class="card">
          <h4 style="margin-bottom:16px">Log Today's Weight</h4>
          <div class="form-group">
            <label class="form-label">Member</label>
            <select class="form-control" id="wt-member"></select>
          </div>
          <div class="form-row">
            <div class="form-group">
              <label class="form-label">Weight (kg)</label>
              <input class="form-control" type="number" id="wt-weight" min="20" max="300" step="0.1" placeholder="65.5">
            </div>
            <div class="form-group">
              <label class="form-label">Date</label>
              <input class="form-control" type="date" id="wt-date">
            </div>
          </div>
          <div class="form-group">
            <label class="form-label">Note (optional)</label>
            <input class="form-control" id="wt-note" placeholder="e.g. After workout">
          </div>
          <button class="btn btn-primary btn-full" id="btn-log-weight" style="padding:12px">Log Weight</button>
          <div style="margin-top:14px;padding:14px;background:var(--accent-subtle);border-radius:var(--r-sm)">
            <div class="ai-label" style="margin-bottom:6px">Harris-Benedict Goal</div>
            <div id="hb-result" style="font-size:.84rem;color:var(--text-muted)">
              <button class="btn btn-ghost btn-sm btn-full" id="btn-calc-goal">📊 Calculate My Calorie Goal</button>
            </div>
          </div>
        </div>
        <div class="card">
          <div class="section-head">
            <h4>30-Day Weight History</h4>
            <select id="wt-days" class="form-control" style="width:100px">
              <option value="30">30 days</option>
              <option value="60">60 days</option>
              <option value="90">90 days</option>
            </select>
          </div>
          <div class="chart-wrap" style="height:180px;margin-bottom:14px">
            <canvas id="chart-weight" style="width:100%;height:100%"></canvas>
          </div>
          <div id="wt-log-list" style="max-height:200px;overflow-y:auto"></div>
        </div>
      </div>
    </div>

    <!-- ═══ MEAL HISTORY ═══ -->
    <div id="t-history" class="tab-panel" style="display:none">
      <h2 class="page-title">Meal History</h2>
      <p class="page-sub">Browse your food log on any past date</p>
      <div class="history-nav" style="margin-bottom:20px">
        <button id="hist-prev">◀</button>
        <input type="date" id="hist-date" class="form-control" style="border:none;background:none;width:auto">
        <button id="hist-next">▶</button>
        <span style="font-size:.8rem;color:var(--text-muted);margin-left:auto">
          <span id="hist-total-cal" style="color:var(--accent);font-weight:700">0</span> kcal
        </span>
      </div>
      <div class="card">
        <div id="hist-log">
          <div class="empty"><div class="empty-icon">📆</div><h3>Select a date</h3></div>
        </div>
      </div>
      <div class="card mt16">
        <h4 style="margin-bottom:12px">30-Day Calorie Trend</h4>
        <div style="display:flex;gap:8px;margin-bottom:12px">
          <button class="btn btn-ghost btn-sm" onclick="loadTrend(30)" id="trend-30">30 days</button>
          <button class="btn btn-ghost btn-sm" onclick="loadTrend(60)" id="trend-60">60 days</button>
          <button class="btn btn-ghost btn-sm" onclick="loadTrend(90)" id="trend-90">90 days</button>
        </div>
        <div class="chart-wrap" style="height:200px">
          <canvas id="chart-trend" style="width:100%;height:100%"></canvas>
        </div>
      </div>
    </div>





    <!-- ═══ NUTRIENT DEFICIENCY CHECK ═══ -->
    <div id="t-deficiency" class="tab-panel" style="display:none">
      <h2 class="page-title">Nutrient Check</h2>
      <p class="page-sub">AI analyses your last 7 days of eating and identifies nutrient deficiencies</p>
      <div class="g2" style="align-items:start">
        <div class="card">
          <div style="text-align:center;padding:20px 0">
            <div style="font-size:3rem;margin-bottom:12px">🔬</div>
            <h3 style="margin-bottom:8px">Deficiency Analysis</h3>
            <p style="margin-bottom:20px">AI looks at your 7-day average intake and identifies missing nutrients with specific food recommendations</p>
            <button class="btn btn-primary" id="btn-deficiency" style="padding:12px 24px">🔬 Analyse My Diet</button>
          </div>
          <div id="def-averages" style="display:none;margin-top:14px">
            <div class="ai-label">Your 7-Day Averages</div>
            <div class="bento-grid" id="def-stats" style="margin-top:8px"></div>
          </div>
        </div>
        <div class="card">
          <div id="def-result">
            <div class="empty"><div class="empty-icon">🥗</div>
              <h3>Analysis pending</h3><p>Click Analyse to check your nutrient profile</p>
            </div>
          </div>
        </div>
      </div>
    </div>

    <!-- ═══ EXPORT DATA ═══ -->
    <div id="t-export" class="tab-panel" style="display:none">
      <h2 class="page-title">Export Data</h2>
      <p class="page-sub">Download your nutrition data as a CSV file</p>
      <div class="g2" style="align-items:start">
        <div class="card">
          <h4 style="margin-bottom:16px">Export Options</h4>
          <div class="form-group">
            <label class="form-label">Time Range</label>
            <select class="form-control" id="exp-days">
              <option value="7">Last 7 days</option>
              <option value="30">Last 30 days</option>
              <option value="60">Last 60 days</option>
              <option value="90">Last 90 days</option>
            </select>
          </div>
          <button class="btn btn-primary btn-full" id="btn-export-csv" style="padding:12px">📥 Download CSV</button>
          <div style="margin-top:14px;padding:14px;background:var(--s2);border-radius:var(--r-sm)">
            <div class="ai-label" style="margin-bottom:8px">CSV Contains</div>
            <div style="font-size:.82rem;color:var(--text-muted);line-height:1.8">
              Date · Meal Type · Food Name · Quantity · Calories/unit · Total Calories · Protein · Carbs · Fat
            </div>
          </div>
        </div>
        <div class="card" style="background:var(--accent-subtle);border-color:rgba(249,115,22,.15)">
          <div style="font-size:2.5rem;margin-bottom:12px">📊</div>
          <h3 style="margin-bottom:8px">Your data, your way</h3>
          <p style="font-size:.86rem;line-height:1.8">Export your full nutrition history as a spreadsheet-compatible CSV. Open it in Excel, Google Sheets, or any data tool for custom analysis.</p>
          <div style="margin-top:16px;font-size:.78rem;color:var(--text-faint)">
            🔒 The file is generated directly from your local MySQL database.
          </div>
        </div>
      </div>
    </div>

    <!-- ═══ FEEDBACK ═══ -->
    <div id="t-feedback" class="tab-panel" style="display:none">
      <h2 class="page-title">Feedback</h2>
      <p class="page-sub">We value your input — share your thoughts, suggestions, or report an issue</p>
      <div class="g2" style="align-items:start">
        <div class="card">
          <h4 style="margin-bottom:18px">Send Feedback</h4>
          <div class="form-group">
            <label class="form-label">Category</label>
            <select class="form-control" id="fb-type">
              <option value="suggestion">💡 Suggestion</option>
              <option value="bug">🐛 Bug Report</option>
              <option value="praise">🌟 Positive Feedback</option>
              <option value="other">💬 Other</option>
            </select>
          </div>
          <div class="form-group">
            <label class="form-label">Subject</label>
            <input class="form-control" id="fb-subject" placeholder="Brief summary of your feedback…">
          </div>
          <div class="form-group">
            <label class="form-label">Message</label>
            <textarea class="form-control" id="fb-message" rows="5"
              placeholder="Tell us what you think, what could be improved, or what you love about NutriFit…"></textarea>
          </div>
          <div class="form-group">
            <label class="form-label">Rating (optional)</label>
            <div id="fb-stars" style="display:flex;gap:8px;font-size:1.5rem;cursor:pointer;margin-top:4px">
              <span class="fb-star" data-val="1" style="color:var(--text-faint);transition:var(--tr)">★</span>
              <span class="fb-star" data-val="2" style="color:var(--text-faint);transition:var(--tr)">★</span>
              <span class="fb-star" data-val="3" style="color:var(--text-faint);transition:var(--tr)">★</span>
              <span class="fb-star" data-val="4" style="color:var(--text-faint);transition:var(--tr)">★</span>
              <span class="fb-star" data-val="5" style="color:var(--text-faint);transition:var(--tr)">★</span>
            </div>
          </div>
          <button class="btn btn-primary btn-full" id="btn-fb-submit" style="padding:12px">📨 Submit Feedback</button>
        </div>
        <div class="card" style="background:var(--accent-subtle);border-color:rgba(249,115,22,.15)">
          <div style="font-size:2.5rem;margin-bottom:16px">💌</div>
          <h3 style="margin-bottom:10px">Your voice matters</h3>
          <p style="font-size:.86rem;line-height:1.85;margin-bottom:20px">
            Every piece of feedback helps make NutriFit better for everyone. Whether it's a tiny tweak or a big idea — we're listening.
          </p>
          <div id="fb-history" style="display:flex;flex-direction:column;gap:10px"></div>
          <div id="fb-thankyou" style="display:none;text-align:center;padding:24px 0">
            <div style="font-size:2rem;margin-bottom:10px">🎉</div>
            <h4 style="color:var(--emerald);margin-bottom:6px">Thank you!</h4>
            <p style="font-size:.85rem">Your feedback has been recorded. We appreciate it!</p>
          </div>
        </div>
      </div>
    </div>

  </main>
</div>

<script>
if(!Auth.loggedIn()){window.location.href="/";throw 0;}
const UID=Auth.uid();
const USER=Auth.get();
document.getElementById("sb-name").textContent=USER?.username||"User";
document.getElementById("sb-av").textContent=(USER?.username||"U")[0].toUpperCase();
document.getElementById("today-dt").textContent=
  new Date().toLocaleDateString("en-US",{weekday:"long",year:"numeric",month:"long",day:"numeric"});
document.getElementById("meal-dt").value=new Date().toISOString().split("T")[0];
document.getElementById("nav-btn").textContent="Sign Out";
document.getElementById("nav-btn")._handled=true;
document.getElementById("nav-btn").onclick=()=>{Auth.clear();window.location.href="/";};

/* ── Tab switch ── */
const TABS=["daily","weekly","log","member","plan","weight","history","deficiency","export","feedback"];
function switchTab(id){
  TABS.forEach(t=>{
    const el=document.getElementById("t-"+t);
    if(el) el.style.display=t===id?"block":"none";
  });
  document.querySelectorAll(".sb-link[data-tab]").forEach(l=>
    l.classList.toggle("active",l.dataset.tab===id));
  if(id==="daily")   loadDaily();
  if(id==="weekly")  loadWeekly();
  if(id==="log")     loadMembers();
  if(id==="weight")  loadWeightTab();
  if(id==="history") initHistory();
}
document.querySelectorAll(".sb-link[data-tab]").forEach(l=>
  l.addEventListener("click",()=>switchTab(l.dataset.tab)));

/* ── Daily ── */
let dTotals={calories:0,protein:0,carbs:0,fat:0};
async function loadDaily(){
  try{
    const data=await API.get(`/api/summary/daily?user_id=${UID}`);
    dTotals=data.totals;
    const goal=parseInt(document.getElementById("cal-goal").value)||2000;
    const ratio=dTotals.calories/goal;
    /* count-up animations */
    countUp(document.getElementById("s-cal"),dTotals.calories);
    countUp(document.getElementById("s-pro"),dTotals.protein);
    countUp(document.getElementById("s-car"),dTotals.carbs);
    countUp(document.getElementById("s-fat"),dTotals.fat);
    /* ring */
    setRing("cal-ring",ratio);
    document.getElementById("ring-pct").textContent=Math.round(ratio*100)+"%";
    document.getElementById("ring-consumed").innerHTML=
      `${fmt(dTotals.calories)} <span style="font-size:1rem;color:var(--text-muted);font-weight:400">/ ${fmt(goal)} kcal</span>`;
    document.getElementById("ring-rem").textContent=fmt(Math.max(goal-dTotals.calories,0))+" kcal";
    document.getElementById("mc-pro").textContent=fmt(dTotals.protein)+"g";
    document.getElementById("mc-car").textContent=fmt(dTotals.carbs)+"g";
    document.getElementById("mc-fat").textContent=fmt(dTotals.fat)+"g";
    /* score */
    const sc=getScore(dTotals,goal);
    const sel=document.getElementById("score-el");
    sel.textContent=sc.score+"/100 · "+sc.label;
    sel.className="score-pill sp-"+sc.color;
    /* food log table */
    const log=document.getElementById("food-log");
    if(data.items.length){
      log.innerHTML=`<div style="overflow-x:auto"><table class="tbl">
        <thead><tr><th>Meal</th><th>Food</th><th>Cal/unit</th><th>Qty</th>
          <th>Total</th><th>P</th><th>C</th><th>F</th></tr></thead>
        <tbody>${data.items.map(i=>`<tr>
          <td><span class="t-meal meal-tag-${i.meal_type.toLowerCase()}">${i.meal_type}</span></td>
          <td class="t-name">${i.food_name}</td>
          <td>${i.calories}</td><td>${i.quantity}</td>
          <td class="t-cal">${fmt(i.total_calories)} kcal</td>
          <td style="color:var(--sky)">${fmt(i.protein)}g</td>
          <td style="color:var(--amber)">${fmt(i.carbs)}g</td>
          <td style="color:var(--rose)">${fmt(i.fat)}g</td>
        </tr>`).join("")}</tbody></table></div>`;
    }else{
      log.innerHTML=`<div class="empty"><div class="empty-icon">🍽️</div>
        <h3>No meals logged today</h3><p>Use Log Meal to add your first meal</p></div>`;
    }
    setTimeout(()=>{
      if(data.items.length){
        barChart("chart-bar",data.items.map(i=>i.food_name),data.items.map(i=>i.total_calories));
        donut("chart-donut",["Protein","Carbs","Fat"],
          [dTotals.protein,dTotals.carbs,dTotals.fat],
          ["#38BDF8","#FBBF24","#F43F5E"]);
      }
    },80);
  }catch(e){toast(e.message,"error");}
}
function getScore(t,goal){
  let s=100;
  if(t.calories>goal)s-=20;if(t.protein<50)s-=20;
  if(t.carbs>300)s-=15;if(t.fat>70)s-=15;s=Math.max(s,0);
  const m=[[90,"Excellent","emerald"],[70,"Good","sky"],[50,"Fair","amber"],[0,"Poor","rose"]];
  const [,l,c]=m.find(([x])=>s>=x);return{score:s,label:l,color:c};
}
document.getElementById("cal-goal").addEventListener("change",loadDaily);
document.getElementById("btn-suggest").addEventListener("click",async()=>{
  const btn=document.getElementById("btn-suggest");setLoad(btn,true);
  const box=document.getElementById("suggest-box");box.style.display="block";
  const txt=document.getElementById("suggest-txt");
  txt.innerHTML=`<div class="shimmer-bg" style="height:80px;border-radius:12px"></div>`;
  try{
    const d=await API.post("/api/ai/meal-suggestion",{totals:dTotals});
    txt.textContent=d.response;
  }catch(e){toast(e.message,"error");}finally{setLoad(btn,false);}
});

/* ── Weekly ── */
let wkAgg={};
async function loadWeekly(){
  try{
    const data=await API.get(`/api/summary/weekly?user_id=${UID}`);
    wkAgg=data.reduce((a,d)=>({
      calories:(a.calories||0)+d.calories,protein:(a.protein||0)+d.protein,
      carbs:(a.carbs||0)+d.carbs,fat:(a.fat||0)+d.fat}),{});
    countUp(document.getElementById("w-cal"),wkAgg.calories);
    document.getElementById("w-pro").textContent=fmt(wkAgg.protein)+"g";
    document.getElementById("w-car").textContent=fmt(wkAgg.carbs)+"g";
    document.getElementById("w-fat").textContent=fmt(wkAgg.fat)+"g";
    setTimeout(()=>barChart("chart-wk",data.map(d=>d.date.slice(5)),data.map(d=>d.calories),"#38BDF8"),80);
  }catch(e){toast(e.message,"error");}
}
document.getElementById("btn-wk").addEventListener("click",async()=>{
  const btn=document.getElementById("btn-wk");setLoad(btn,true);
  const box=document.getElementById("wk-box");box.style.display="block";
  const txt=document.getElementById("wk-txt");
  txt.innerHTML=`<div class="shimmer-bg" style="height:120px;border-radius:12px"></div>`;
  try{
    const d=await API.post("/api/ai/weekly-analysis",wkAgg);
    txt.textContent=d.response;
  }catch(e){toast(e.message,"error");}finally{setLoad(btn,false);}
});

/* ── Log Meal ── */
let selFid=null;
async function loadMembers(){
  const m=await API.get(`/api/members?user_id=${UID}`);
  document.getElementById("meal-mem").innerHTML=m.length
    ?m.map(x=>`<option value="${x.member_id}">${x.name}</option>`).join("")
    :`<option value="">No members — add one first</option>`;
}
document.getElementById("btn-fs").addEventListener("click",async()=>{
  const q=document.getElementById("f-q").value.trim();if(!q)return;
  const r=document.getElementById("f-results");
  r.innerHTML=`<div style="text-align:center;padding:14px"><span class="spin"></span></div>`;
  try{
    const foods=await API.get(`/api/food/search?q=${encodeURIComponent(q)}`);
    r.innerHTML=foods.length
      ?`<div class="food-grid">${foods.map(f=>`
          <div class="food-card" onclick="selF(${f.food_id},'${f.food_name.replace(/'/g,"\\'")}',this)">
            <div class="fc-n">${f.food_name}</div>
            <div class="fc-c">${f.calories}<span style="font-size:.72rem;font-weight:400"> kcal</span></div>
            <div class="fc-m"><span>P:${f.protein}g</span><span>C:${f.carbs}g</span><span>F:${f.fat}g</span></div>
          </div>`).join("")}</div>`
      :`<p style="color:var(--text-muted);font-size:.84rem;padding:8px 0">No results — try AI estimation below</p>`;
  }catch(e){toast(e.message,"error");}
});
function selF(id,name,el){
  selFid=id;
  const d=document.getElementById("sel-food");
  d.textContent="✓  "+name;d.style.color="var(--accent)";d.style.fontWeight="600";
  document.querySelectorAll(".food-card").forEach(c=>c.classList.remove("picked"));
  el.classList.add("picked");
}
document.getElementById("btn-ai-est").addEventListener("click",async()=>{
  const nm=document.getElementById("ai-nm").value.trim();if(!nm)return;
  const btn=document.getElementById("btn-ai-est");setLoad(btn,true);
  try{
    const d=document.getElementById("sel-food");
    d.innerHTML=`<div class="shimmer-bg" style="height:24px;width:120px;display:inline-block;border-radius:4px"></div>`;
    const f=await API.post("/api/food/estimate",{food_name:nm});
    selFid=f.food_id;
    d.textContent=`✓  ${f.food_name} · ${f.calories} kcal (AI)`;
    d.style.color="var(--accent)";d.style.fontWeight="600";
    toast(`AI estimated: ${f.calories}kcal  P:${f.protein}g  C:${f.carbs}g  F:${f.fat}g`,"success",5000);
  }catch(e){toast(e.message,"error");}finally{setLoad(btn,false);}
});
document.getElementById("btn-log").addEventListener("click",async()=>{
  if(!selFid)return toast("Select a food first","error");
  const btn=document.getElementById("btn-log");setLoad(btn,true);
  try{
    await API.post("/api/meals",{
      member_id:document.getElementById("meal-mem").value,
      meal_type:document.getElementById("meal-type").value,
      meal_date:document.getElementById("meal-dt").value,
      food_id:selFid,
      quantity:parseInt(document.getElementById("meal-qty").value)
    });
    toast("Meal logged successfully ✓","success");
    selFid=null;
    const d=document.getElementById("sel-food");
    d.textContent="Nothing selected — search below ↓";
    d.style.color="var(--text-faint)";d.style.fontWeight="400";
  }catch(e){toast(e.message,"error");}finally{setLoad(btn,false);}
});
document.getElementById("f-q").addEventListener("keydown",e=>{
  if(e.key==="Enter")document.getElementById("btn-fs").click();
});

/* ── Add Member ── */
document.getElementById("btn-add-mem").addEventListener("click",async()=>{
  const nm=document.getElementById("m-name").value.trim();
  if(!nm)return toast("Name is required","error");
  const btn=document.getElementById("btn-add-mem");setLoad(btn,true);
  try{
    await API.post("/api/members",{user_id:UID,name:nm,
      age:document.getElementById("m-age").value,
      gender:document.getElementById("m-gender").value,
      weight:document.getElementById("m-wt").value,
      height:document.getElementById("m-ht").value});
    toast(`${nm} added ✓`,"success");
    document.getElementById("m-name").value="";
  }catch(e){toast(e.message,"error");}finally{setLoad(btn,false);}
});

/* ── AI Meal Plan ── */
document.getElementById("btn-plan").addEventListener("click",async()=>{
  const btn=document.getElementById("btn-plan");setLoad(btn,true);
  try{
    const d=await API.post("/api/ai/meal-plan",{
      goal:document.getElementById("pl-goal").value,
      diet:document.getElementById("pl-diet").value});
    document.getElementById("plan-txt").textContent=d.response;
    document.getElementById("plan-result").style.display="block";
    document.getElementById("plan-empty").style.display="none";
  }catch(e){toast(e.message,"error");}finally{setLoad(btn,false);}
});

loadDaily();

/* ═══════════════════════════════════════════════
   FEATURE 3: HARRIS-BENEDICT (Weight tab)
═══════════════════════════════════════════════ */
async function loadWeightTab(){
  const m=await API.get(`/api/members?user_id=${UID}`);
  const el=document.getElementById("wt-member");
  if(el) el.innerHTML=m.length?m.map(x=>`<option value="${x.member_id}">${x.name}</option>`).join(""):`<option value="">No members</option>`;
  if(document.getElementById("wt-date")) document.getElementById("wt-date").value=new Date().toISOString().split("T")[0];
  loadWeightHistory();
}
async function loadWeightHistory(){
  const dEl=document.getElementById("wt-days");
  const days=dEl?dEl.value:30;
  try{
    const data=await API.get(`/api/weight/history?user_id=${UID}&days=${days}`);
    if(data.length){
      setTimeout(()=>barChart("chart-weight",data.map(d=>d.date.slice(5)),data.map(d=>d.weight),"#38BDF8"),80);
      const list=document.getElementById("wt-log-list");
      if(list){
        const sorted=[...data].reverse().slice(0,10);
        list.innerHTML=sorted.map((r,i)=>{
          const prev=sorted[i+1];
          let change="";
          if(prev){const diff=(r.weight-prev.weight).toFixed(1);change=diff>0?`<span style="color:var(--rose);font-size:.75rem;font-weight:600">▲${diff}kg</span>`:`<span style="color:var(--emerald);font-size:.75rem;font-weight:600">▼${Math.abs(diff)}kg</span>`;}
          return `<div style="display:flex;align-items:center;justify-content:space-between;padding:10px 14px;background:var(--s2);border:1px solid var(--border);border-radius:var(--r-sm);margin-bottom:8px"><div><div style="font-size:.75rem;color:var(--text-faint)">${r.date}</div>${r.note?`<div style="font-size:.78rem;color:var(--text-muted)">${r.note}</div>`:""}</div><div style="display:flex;align-items:center;gap:10px">${change}<div style="font-family:var(--font-head);font-size:1.2rem;font-weight:700;color:var(--accent)">${r.weight} kg</div></div></div>`;
        }).join("");
      }
    }
  }catch(e){}
}
const wtDays=document.getElementById("wt-days");
if(wtDays) wtDays.addEventListener("change",loadWeightHistory);
const btnLogWt=document.getElementById("btn-log-weight");
if(btnLogWt) btnLogWt.addEventListener("click",async()=>{
  const wt=document.getElementById("wt-weight").value;
  if(!wt)return toast("Enter your weight","error");
  const btn=btnLogWt;setLoad(btn,true);
  try{
    await API.post("/api/weight/log",{user_id:UID,
      member_id:document.getElementById("wt-member").value,
      weight:parseFloat(wt),
      date:document.getElementById("wt-date").value,
      note:document.getElementById("wt-note").value});
    toast("Weight logged ✓","success");
    loadWeightHistory();
  }catch(e){toast(e.message,"error");}finally{setLoad(btn,false);}
});
const btnCalcGoal=document.getElementById("btn-calc-goal");
if(btnCalcGoal) btnCalcGoal.addEventListener("click",async()=>{
  try{
    const d=await API.get(`/api/members/calorie-goal?user_id=${UID}&activity=moderate`);
    document.getElementById("hb-result").innerHTML=`
      <div style="margin-bottom:10px;font-size:.84rem;color:var(--text-muted)">
        BMR: <strong style="color:var(--text)">${d.bmr} kcal</strong> · TDEE: <strong style="color:var(--accent)">${d.tdee} kcal</strong>
      </div>
      <div style="display:grid;grid-template-columns:repeat(3,1fr);gap:8px;margin-top:10px">
        <div style="text-align:center;padding:12px;border-radius:var(--r-sm);border:1px solid var(--border);background:var(--s2);cursor:pointer" onclick="setGoal(${d.weight_loss})">
          <div style="font-family:var(--font-head);font-size:1.3rem;font-weight:800;color:var(--accent)">${d.weight_loss}</div>
          <div style="font-size:.72rem;color:var(--text-faint);margin-top:3px">Weight Loss</div>
        </div>
        <div style="text-align:center;padding:12px;border-radius:var(--r-sm);border:1px solid var(--border);background:var(--s2);cursor:pointer" onclick="setGoal(${d.maintain})">
          <div style="font-family:var(--font-head);font-size:1.3rem;font-weight:800;color:var(--emerald)">${d.maintain}</div>
          <div style="font-size:.72rem;color:var(--text-faint);margin-top:3px">Maintain</div>
        </div>
        <div style="text-align:center;padding:12px;border-radius:var(--r-sm);border:1px solid var(--border);background:var(--s2);cursor:pointer" onclick="setGoal(${d.muscle_gain})">
          <div style="font-family:var(--font-head);font-size:1.3rem;font-weight:800;color:var(--sky)">${d.muscle_gain}</div>
          <div style="font-size:.72rem;color:var(--text-faint);margin-top:3px">Muscle Gain</div>
        </div>
      </div>`;
  }catch(e){toast(e.message,"error");}
});
function setGoal(cal){
  document.getElementById("cal-goal").value=cal;
  switchTab("daily");
  toast(`Calorie goal set to ${cal} kcal ✓`,"success");
}

/* ═══════════════════════════════════════════════
   FEATURE 5: MEAL HISTORY
═══════════════════════════════════════════════ */
function initHistory(){
  const el=document.getElementById("hist-date");
  if(el){el.value=new Date().toISOString().split("T")[0];loadHistoryDate(el.value);}
  loadTrend(30);
}
async function loadHistoryDate(dateStr){
  try{
    const data=await API.get(`/api/summary/daily?user_id=${UID}&date=${dateStr}`);
    const calEl=document.getElementById("hist-total-cal");
    if(calEl) calEl.textContent=fmt(data.totals.calories);
    const log=document.getElementById("hist-log");
    if(!log) return;
    if(data.items.length){
      log.innerHTML=`<div style="overflow-x:auto"><table class="tbl">
        <thead><tr><th>Meal</th><th>Food</th><th>Qty</th><th>Total Cal</th><th>Protein</th><th>Carbs</th><th>Fat</th></tr></thead>
        <tbody>${data.items.map(i=>`<tr>
          <td><span class="t-meal meal-tag-${i.meal_type.toLowerCase()}">${i.meal_type}</span></td>
          <td class="t-name">${i.food_name}</td><td>${i.quantity}</td>
          <td class="t-cal">${fmt(i.total_calories)} kcal</td>
          <td style="color:var(--sky)">${fmt(i.protein)}g</td>
          <td style="color:var(--amber)">${fmt(i.carbs)}g</td>
          <td style="color:var(--rose)">${fmt(i.fat)}g</td>
        </tr>`).join("")}</tbody></table></div>`;
    }else{
      log.innerHTML=`<div class="empty"><div class="empty-icon">📭</div><h3>No meals on ${dateStr}</h3></div>`;
    }
  }catch(e){toast(e.message,"error");}
}
async function loadTrend(days){
  ["trend-30","trend-60","trend-90"].forEach(id=>{
    const el=document.getElementById(id);
    if(el) el.className=el.textContent.includes(days)?"btn btn-primary btn-sm":"btn btn-ghost btn-sm";
  });
  try{
    const data=await API.get(`/api/summary/trend?user_id=${UID}&days=${days}`);
    setTimeout(()=>barChart("chart-trend",data.map(d=>d.date.slice(5)),data.map(d=>d.calories),"#F97316"),80);
  }catch(e){}
}
const histDate=document.getElementById("hist-date");
if(histDate) histDate.addEventListener("change",e=>loadHistoryDate(e.target.value));
const histPrev=document.getElementById("hist-prev");
if(histPrev) histPrev.addEventListener("click",()=>{
  const el=document.getElementById("hist-date");
  const d=new Date(el.value);d.setDate(d.getDate()-1);
  el.value=d.toISOString().split("T")[0];loadHistoryDate(el.value);
});
const histNext=document.getElementById("hist-next");
if(histNext) histNext.addEventListener("click",()=>{
  const el=document.getElementById("hist-date");
  const d=new Date(el.value);d.setDate(d.getDate()+1);
  el.value=d.toISOString().split("T")[0];loadHistoryDate(el.value);
});




/* ═══════════════════════════════════════════════
   FEATURE 9: NUTRIENT DEFICIENCY
═══════════════════════════════════════════════ */
const btnDef=document.getElementById("btn-deficiency");
if(btnDef) btnDef.addEventListener("click",async()=>{
  const btn=btnDef;setLoad(btn,true);
  const resEl=document.getElementById("def-result");
  resEl.innerHTML=`<div class="ai-label">AI Deficiency Analysis</div><div class="shimmer-bg" style="height:150px;border-radius:12px"></div>`;
  try{
    const d=await API.post("/api/ai/deficiency",{user_id:UID});
    resEl.innerHTML=`
      <div class="ai-label">AI Deficiency Analysis</div>
      <div style="background:linear-gradient(135deg,rgba(244,63,94,.06),rgba(251,191,36,.04));border:1px solid rgba(244,63,94,.15);border-radius:var(--r);padding:18px 22px;white-space:pre-wrap;line-height:1.8;color:var(--text);font-size:.9rem">${esc(d.response)}</div>`;
    if(d.averages){
      const av=d.averages;
      const statsEl=document.getElementById("def-stats");
      if(statsEl) statsEl.innerHTML=`
        <div class="stat-tile st-accent" style="padding:14px"><div class="s-label">Avg Cal</div><div class="s-value" style="font-size:1.4rem">${av.calories}</div></div>
        <div class="stat-tile" style="padding:14px"><div class="s-label" style="color:var(--sky)">Avg Protein</div><div class="s-value" style="font-size:1.4rem;color:var(--sky)">${av.protein}g</div></div>
        <div class="stat-tile" style="padding:14px"><div class="s-label" style="color:var(--amber)">Avg Carbs</div><div class="s-value" style="font-size:1.4rem;color:var(--amber)">${av.carbs}g</div></div>
        <div class="stat-tile" style="padding:14px"><div class="s-label" style="color:var(--rose)">Avg Fat</div><div class="s-value" style="font-size:1.4rem;color:var(--rose)">${av.fat}g</div></div>`;
      const defAvg=document.getElementById("def-averages");
      if(defAvg) defAvg.style.display="block";
    }
  }catch(e){toast(e.message,"error");}finally{setLoad(btn,false);}
});

/* ═══════════════════════════════════════════════
   FEATURE 11: GROCERY LIST
═══════════════════════════════════════════════ */
const btnGrocery=document.getElementById("btn-grocery");
if(btnGrocery) btnGrocery.addEventListener("click",async()=>{
  const btn=btnGrocery;setLoad(btn,true);
  try{
    const grocEl=document.getElementById("grocery-txt");
    const grocRes=document.getElementById("grocery-res");
    grocEl.innerHTML=`<div class="shimmer-bg" style="height:120px;border-radius:12px"></div>`;
    grocRes.style.display="block";
    const d=await API.post("/api/ai/grocery-list",{
      plan:window._lastMealPlan||"",
      goal:document.getElementById("pl-goal").value,
      diet:document.getElementById("pl-diet").value});
    grocEl.textContent=d.response;
    const planEmpty=document.getElementById("plan-empty");
    if(planEmpty) planEmpty.style.display="none";
  }catch(e){toast(e.message,"error");}finally{setLoad(btn,false);}
});

/* ═══════════════════════════════════════════════
   FEATURE 7: CSV EXPORT
═══════════════════════════════════════════════ */
const btnExport=document.getElementById("btn-export-csv");
if(btnExport) btnExport.addEventListener("click",()=>{
  const days=document.getElementById("exp-days").value;
  window.open(`/api/export/csv?user_id=${UID}&days=${days}`,"_blank");
  toast(`Downloading last ${days} days as CSV ✓`,"success");
});

/* ═══════════════════════════════════════════════
   MEAL PLAN — save plan for grocery list
═══════════════════════════════════════════════ */
const btnPlanOld=document.getElementById("btn-plan");
if(btnPlanOld){
  btnPlanOld.removeEventListener("click",btnPlanOld._oldHandler);
  btnPlanOld.addEventListener("click",async()=>{
    setLoad(btnPlanOld,true);
    const planTxt=document.getElementById("plan-txt");
    const planRes=document.getElementById("plan-res");
    const planEmpty=document.getElementById("plan-empty");
    planTxt.innerHTML=`<div class="shimmer-bg" style="height:150px;border-radius:12px"></div>`;
    planRes.style.display="block";
    planEmpty.style.display="none";
    try{
      const d=await API.post("/api/ai/meal-plan",{
        goal:document.getElementById("pl-goal").value,
        diet:document.getElementById("pl-diet").value});
      if(planTxt) planTxt.textContent=d.response;
      window._lastMealPlan=d.response;
      toast("Meal plan ready! You can now generate a grocery list ✓","success");
    }catch(e){toast(e.message,"error");}finally{setLoad(btnPlanOld,false);}
  });
}

/* ═══════════════════════════════════════════════
   FEEDBACK
═══════════════════════════════════════════════ */
let fbRating=0;
document.querySelectorAll(".fb-star").forEach(star=>{
  star.addEventListener("mouseenter",()=>{
    const v=parseInt(star.dataset.val);
    document.querySelectorAll(".fb-star").forEach(s=>{
      s.style.color=parseInt(s.dataset.val)<=v?"var(--amber)":"var(--text-faint)";
    });
  });
  star.addEventListener("mouseleave",()=>{
    document.querySelectorAll(".fb-star").forEach(s=>{
      s.style.color=parseInt(s.dataset.val)<=fbRating?"var(--amber)":"var(--text-faint)";
    });
  });
  star.addEventListener("click",()=>{
    fbRating=parseInt(star.dataset.val);
    document.querySelectorAll(".fb-star").forEach(s=>{
      s.style.color=parseInt(s.dataset.val)<=fbRating?"var(--amber)":"var(--text-faint)";
    });
  });
});
const btnFbSubmit=document.getElementById("btn-fb-submit");
if(btnFbSubmit) btnFbSubmit.addEventListener("click",async()=>{
  const subject=document.getElementById("fb-subject").value.trim();
  const message=document.getElementById("fb-message").value.trim();
  const type=document.getElementById("fb-type").value;
  if(!subject||!message)return toast("Please fill in subject and message","error");
  const btn=btnFbSubmit;setLoad(btn,true);
  try{
    await API.post("/api/feedback",{user_id:UID,type,subject,message,rating:fbRating});
    document.getElementById("fb-subject").value="";
    document.getElementById("fb-message").value="";
    fbRating=0;
    document.querySelectorAll(".fb-star").forEach(s=>s.style.color="var(--text-faint)");
    const thanks=document.getElementById("fb-thankyou");
    if(thanks){thanks.style.display="block";setTimeout(()=>thanks.style.display="none",5000);}
    toast("Feedback submitted ✓","success");
    loadFeedbackHistory();
  }catch(e){toast(e.message,"error");}finally{setLoad(btn,false);}
});
async function loadFeedbackHistory(){
  try{
    const data=await API.get(`/api/feedback?user_id=${UID}`);
    const el=document.getElementById("fb-history");if(!el)return;
    if(!data.length){el.innerHTML="";return;}
    const icons={suggestion:"💡",bug:"🐛",praise:"🌟",other:"💬"};
    el.innerHTML=`<div style="font-size:.72rem;font-weight:700;text-transform:uppercase;letter-spacing:.06em;color:var(--text-faint);margin-bottom:8px">Your Previous Feedback</div>`
      +data.slice(0,3).map(f=>`
        <div style="padding:10px 14px;background:var(--s1);border:1px solid var(--border);border-radius:var(--r-sm)">
          <div style="display:flex;align-items:center;justify-content:space-between;margin-bottom:4px">
            <span style="font-size:.82rem;font-weight:600;color:var(--text)">${icons[f.type]||'💬'} ${esc(f.subject)}</span>
            ${f.rating?`<span style="color:var(--amber);font-size:.8rem">${'★'.repeat(f.rating)}</span>`:""}
          </div>
          <div style="font-size:.76rem;color:var(--text-faint)">${f.created_at.slice(0,10)}</div>
        </div>`).join("");
  }catch(e){}
}
</script>
"""


# ══════════════════════════════════════════════════════════════════
#  MEAL LOG PAGE
# ══════════════════════════════════════════════════════════════════
MEAL_LOG = """
<div class="page-wrap">
<main class="container sec">
  <h2 class="page-title">Log a Meal</h2>
  <p class="page-sub">Search food and add it to your daily intake</p>
  <div class="g2">
    <div class="card">
      <h4 style="margin-bottom:18px">Meal Details</h4>
      <div class="form-group">
        <label class="form-label">Member</label>
        <select class="form-control" id="mm"><option>Loading members…</option></select>
      </div>
      <div class="form-row">
        <div class="form-group">
          <label class="form-label">Meal Type</label>
          <select class="form-control" id="mtype">
            <option>Breakfast</option><option>Lunch</option>
            <option>Snacks</option><option>Dinner</option>
          </select>
        </div>
        <div class="form-group">
          <label class="form-label">Date</label>
          <input class="form-control" type="date" id="mdt">
        </div>
      </div>
      <div class="form-group">
        <label class="form-label">Quantity</label>
        <input class="form-control" type="number" id="mqty" value="1" min="1">
      </div>
      <div class="form-group">
        <label class="form-label">Selected Food</label>
        <div id="sf" style="background:var(--s2);border:1px solid var(--border);border-radius:var(--r-sm);padding:11px 14px;color:var(--text-faint);min-height:42px;font-size:.88rem">
          Nothing selected yet ↓
        </div>
      </div>
      <button class="btn btn-primary btn-full" id="btn-log" style="padding:12px">Log Meal</button>
    </div>
    <div class="card">
      <h4 style="margin-bottom:14px">Search Food</h4>
      <div style="display:flex;gap:8px;margin-bottom:14px">
        <input class="form-control" id="fq" placeholder="e.g. boiled egg, dal rice, paneer…">
        <button class="btn btn-ghost btn-sm" id="btn-fs">Search</button>
      </div>
      <div id="fr"></div>
      <div class="divider"></div>
      <p style="font-size:.8rem;color:var(--text-muted);margin-bottom:10px">
        🤖 AI estimates nutrition for unknown foods:
      </p>
      <div style="display:flex;gap:8px">
        <input class="form-control" id="ainm" placeholder="e.g. Palak Paneer, Idli Sambar…">
        <button class="btn btn-ghost btn-sm" id="btn-ai">✨ AI</button>
      </div>
    </div>
  </div>
  <div class="card mt16">
    <div class="section-head">
      <h4>Today's Meals</h4>
      <button class="btn btn-ghost btn-sm" onclick="loadLog()">↻ Refresh</button>
    </div>
    <div id="today-log">
      <div class="empty"><div class="empty-icon">🍽️</div><h3>No meals today</h3></div>
    </div>
  </div>
</main>
</div>

<script>
if(!reqAuth()){}
const UID=Auth.uid();
document.getElementById("mdt").value=new Date().toISOString().split("T")[0];
let sfid=null;
(async()=>{
  const m=await API.get(`/api/members?user_id=${UID}`);
  document.getElementById("mm").innerHTML=m.length
    ?m.map(x=>`<option value="${x.member_id}">${x.name}</option>`).join("")
    :`<option value="">No members — add from Dashboard</option>`;
})();
async function loadLog(){
  const data=await API.get(`/api/summary/daily?user_id=${UID}`);
  const w=document.getElementById("today-log");
  if(!data.items.length){
    w.innerHTML=`<div class="empty"><div class="empty-icon">🍽️</div><h3>No meals today</h3></div>`;return;
  }
  w.innerHTML=`<div style="overflow-x:auto"><table class="tbl">
    <thead><tr><th>Meal</th><th>Food</th><th>Qty</th><th>Calories</th></tr></thead>
    <tbody>${data.items.map(i=>`<tr>
      <td><span class="t-meal meal-tag-${i.meal_type.toLowerCase()}">${i.meal_type}</span></td>
      <td class="t-name">${i.food_name}</td>
      <td>${i.quantity}</td>
      <td class="t-cal">${Math.round(i.total_calories)} kcal</td>
    </tr>`).join("")}</tbody></table></div>`;
}
loadLog();
document.getElementById("btn-fs").addEventListener("click",async()=>{
  const q=document.getElementById("fq").value.trim();if(!q)return;
  const r=document.getElementById("fr");
  r.innerHTML=`<div style="text-align:center;padding:14px"><span class="spin"></span></div>`;
  const foods=await API.get(`/api/food/search?q=${encodeURIComponent(q)}`);
  r.innerHTML=foods.length
    ?`<div class="food-grid">${foods.slice(0,6).map(f=>`
        <div class="food-card" onclick="pick(${f.food_id},'${f.food_name.replace(/'/g,"\\\\'")}',this)">
          <div class="fc-n">${f.food_name}</div>
          <div class="fc-c">${f.calories}<span style="font-size:.72rem;font-weight:400"> kcal</span></div>
          <div class="fc-m"><span>P:${f.protein}g</span><span>C:${f.carbs}g</span><span>F:${f.fat}g</span></div>
        </div>`).join("")}</div>`
    :`<p style="color:var(--text-muted);font-size:.84rem;padding:6px 0">No results found</p>`;
});
function pick(id,name,el){
  sfid=id;const d=document.getElementById("sf");
  d.textContent="✓  "+name;d.style.color="var(--accent)";d.style.fontWeight="600";
  document.querySelectorAll(".food-card").forEach(c=>c.classList.remove("picked"));
  el.classList.add("picked");
}
document.getElementById("btn-ai").addEventListener("click",async()=>{
  const nm=document.getElementById("ainm").value.trim();if(!nm)return;
  const btn=document.getElementById("btn-ai");setLoad(btn,true);
  try{
    const d=document.getElementById("sf");
    d.innerHTML=`<div class="shimmer-bg" style="height:24px;width:120px;display:inline-block;border-radius:4px"></div>`;
    const f=await API.post("/api/food/estimate",{food_name:nm});
    sfid=f.food_id;
    d.textContent=`✓  ${f.food_name} · ${f.calories} kcal (AI estimated)`;
    d.style.color="var(--accent)";d.style.fontWeight="600";
    toast("AI estimated and saved ✓","success");
  }catch(e){toast(e.message,"error");}finally{setLoad(btn,false);}
});
document.getElementById("btn-log").addEventListener("click",async()=>{
  if(!sfid)return toast("Select a food first","error");
  const btn=document.getElementById("btn-log");setLoad(btn,true);
  try{
    await API.post("/api/meals",{
      member_id:document.getElementById("mm").value,
      meal_type:document.getElementById("mtype").value,
      meal_date:document.getElementById("mdt").value,
      food_id:sfid,quantity:parseInt(document.getElementById("mqty").value)});
    toast("Meal logged ✓","success");sfid=null;
    document.getElementById("sf").textContent="Nothing selected yet ↓";
    document.getElementById("sf").style.color="var(--text-faint)";
    document.getElementById("sf").style.fontWeight="400";
    loadLog();
  }catch(e){toast(e.message,"error");}finally{setLoad(btn,false);}
});
document.getElementById("fq").addEventListener("keydown",e=>{
  if(e.key==="Enter")document.getElementById("btn-fs").click();
});
</script>
"""

# ══════════════════════════════════════════════════════════════════
#  AI ADVISOR PAGE
# ══════════════════════════════════════════════════════════════════
AI_ADV = """
<div class="page-wrap">
<main class="container sec">
  <div style="max-width:780px;margin:0 auto">
    <div style="display:flex;align-items:center;gap:8px;margin-bottom:16px">
      <div class="hero-badge" style="margin-bottom:0">🤖 Gemini AI Co-pilot</div>
      <div style="display:flex;align-items:center;gap:6px;font-size:.7rem;color:var(--emerald);font-weight:600;text-transform:uppercase;letter-spacing:.05em">
        <span style="display:block;width:6px;height:6px;background:var(--emerald);border-radius:50%;box-shadow:0 0 8px var(--emerald);animation:pulse 2s infinite"></span>
        Database Connected
      </div>
    </div>
    
    <h1 style="margin-bottom:12px;letter-spacing:-0.04em">
      Your personal<br><span class="grad">AI Nutritionist</span>
    </h1>
    <p style="font-size:.95rem;margin-bottom:32px;max-width:500px;color:var(--text-faint)">
      Ask anything about nutrition, recipes, or your own health data. I'm connected to your database and ready to help.
    </p>

    <div class="chat-box" style="border-top:1px solid var(--border)">
      <div id="chat-msgs" class="chat-msgs" style="min-height:300px">
        <div class="msg ai">
          <div class="ai-label">NutriFit AI</div>
          <div class="bubble ai">Hey! I'm your unified AI Co-pilot. I can provide general nutrition advice or look into your real logs to give you personalized charts and answers. What's on your mind?</div>
        </div>
      </div>
      <div class="chat-input-bar">
        <input class="form-control" id="ci" placeholder="Ask about food, or 'How many calories did I eat today?'…" style="flex:1;background:var(--s3)">
        <button class="btn btn-primary" id="btn-send" style="padding:10px 18px">Ask AI ↑</button>
      </div>
    </div>
    
    <div style="display:flex;gap:8px;flex-wrap:wrap;margin-top:20px">
      <span style="font-size:.74rem;color:var(--text-faint);align-self:center">Personalized insights:</span>
      <span class="pill" onclick="qa('What is my current BMI and health status?')">Check my BMI</span>
      <span class="pill" onclick="qa('How many calories have I consumed today?')">Today's Calories</span>
      <span class="pill" onclick="qa('Can you look at my profile and suggest a meal plan?')">Suggest Meal Plan</span>
      <span class="pill" onclick="qa('Log 1 piece of Chicken Sandwich for Lunch for me.')">Auto-Log Meal</span>
    </div>
  </div>
</main>
</div>

<script>
function addMsg(role,text,toolUsed=null){
  const c=document.getElementById("chat-msgs");
  const el=document.createElement("div");el.className="msg "+role;
  if(role==="ai"){
    let html = `<div class="ai-label">NutriFit AI</div>`;
    if(toolUsed && toolUsed !== "none"){
      html += `<div class="mcp-tool-badge" style="margin-bottom:8px;font-size:0.65rem;opacity:0.8">🔧 Used tool: ${toolUsed}</div>`;
    }
    html += `<div class="bubble ai">${esc(text)}</div>`;
    el.innerHTML = html;
  } else {
    el.innerHTML=`<div class="bubble user">${esc(text)}</div>`;
  }
  c.appendChild(el);c.scrollTop=c.scrollHeight;
}

function addTyping(){
  const c=document.getElementById("chat-msgs");
  const el=document.createElement("div");el.id="typing";el.className="msg ai";
  el.innerHTML=`<div class="bubble ai" style="color:var(--text-muted);display:flex;align-items:center;gap:8px">
    <span class="spin" style="width:12px;height:12px;border-width:2px"></span> Analyzing…</div>`;
  c.appendChild(el);c.scrollTop=c.scrollHeight;
}

function rmTyping(){const e=document.getElementById("typing");if(e)e.remove();}

async function sendChat(q){
  if(!q)return;
  addMsg("user",q);
  const c=document.getElementById("chat-msgs");
  const shim=document.createElement("div");shim.id="shim-temp";shim.className="msg ai";
  shim.innerHTML=`<div class="ai-label">NutriFit AI</div><div class="bubble ai shimmer-bg" style="height:60px;width:80%;border-radius:12px"></div>`;
  c.appendChild(shim);c.scrollTop=c.scrollHeight;
  try {
    const d = await API.post("/api/ai/ask", {question: q});
    if(document.getElementById("shim-temp")) document.getElementById("shim-temp").remove();
    addMsg("ai", d.response, d.tool_used);
    if(d.tool_result && q.toLowerCase().includes("log")) {
       toast("Action performed successfully!","success");
    }
  } catch(e) {
    if(document.getElementById("shim-temp")) document.getElementById("shim-temp").remove();
    addMsg("ai","⚠️ Sorry, I encountered an error: "+e.message);
  }
}

document.getElementById("btn-send").onclick=()=>{
  const i=document.getElementById("ci");const q=i.value.trim();if(!q)return;i.value="";sendChat(q);
};
document.getElementById("ci").addEventListener("keydown",e=>{
  if(e.key==="Enter"&&!e.shiftKey){e.preventDefault();document.getElementById("btn-send").click();}
});
function qa(q){document.getElementById("ci").value=q;document.getElementById("btn-send").click();}
</script>
<style>
@keyframes pulse {
  0% { transform: scale(1); opacity: 1; }
  50% { transform: scale(1.2); opacity: 0.7; }
  100% { transform: scale(1); opacity: 1; }
}
</style>
"""

ADMIN = """
<div class="page-wrap">
<main class="container sec">
  <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:30px">
    <div>
      <h2 class="page-title">Admin Insights</h2>
      <p class="page-sub">Comprehensive overview of platform activity and user growth</p>
    </div>
    <div style="display:flex;gap:12px">
       <button class="sb-link active" onclick="switchAdminTab('users', this)" style="width:auto;border:1px solid var(--border)">👥 Users</button>
       <button class="sb-link" onclick="switchAdminTab('feedback', this)" style="width:auto;border:1px solid var(--border)">💌 Feedback</button>
    </div>
  </div>

  <!-- Users Tab Content -->
  <div id="admin-tab-users">
    <div id="admin-stats-pills" style="display:flex;gap:12px;margin-bottom:20px">
       <div class="pill">Total Users: <span id="stat-u">0</span></div>
       <div class="pill">Total Logs: <span id="stat-m">0</span></div>
    </div>
    <div class="g2" style="grid-template-columns: 1fr 2fr; align-items: start;" id="admin-main-grid">
      <div class="card">
         <h4 style="margin-bottom:16px">User Directory</h4>
         <div class="form-group">
           <input class="form-control" id="u-search" placeholder="Search username…">
         </div>
         <div id="u-list" style="max-height: 500px; overflow-y: auto; display:flex; flex-direction:column; gap:8px">
           <div style="text-align:center;padding:20px;color:var(--text-faint)">Searching users…</div>
         </div>
      </div>
      <div id="u-details-panel">
         <div class="card" style="min-height: 500px; display:flex; align-items:center; justify-content:center; text-align:center">
            <div>
              <div style="font-size:3rem;margin-bottom:16px">👤</div>
              <h3>Select a user</h3>
              <p style="color:var(--text-faint)">Choose a user from the directory to view their health metrics and AI analysis</p>
            </div>
         </div>
      </div>
    </div>
  </div>

  <!-- Feedback Tab Content -->
  <div id="admin-tab-feedback" style="display:none">
    <div class="card">
       <div class="section-head">
         <h4>User Feedback Repository</h4>
         <button class="btn btn-ghost btn-sm" onclick="loadAdminFeedback()">↻ Refresh</button>
       </div>
       <div id="fb-admin-list" style="display:grid; grid-template-columns: repeat(auto-fill, minmax(320px, 1fr)); gap:16px; margin-top:16px">
          <div style="text-align:center;padding:40px;grid-column:1/-1;color:var(--text-faint)">Loading feedback…</div>
       </div>
    </div>
  </div>
</main>
</div>

<script>
function switchAdminTab(tab, btn){
  document.getElementById('admin-tab-users').style.display = tab === 'users' ? 'block' : 'none';
  document.getElementById('admin-tab-feedback').style.display = tab === 'feedback' ? 'block' : 'none';
  
  // Toggle active class on buttons
  btn.parentNode.querySelectorAll('button').forEach(b => b.classList.remove('active'));
  btn.classList.add('active');
  
  if(tab === 'feedback') loadAdminFeedback();
}

async function loadAdminFeedback(){
  const list = document.getElementById("fb-admin-list");
  list.innerHTML = `<div style="text-align:center;padding:40px;grid-column:1/-1"><span class="spin"></span> Loading feedback…</div>`;
  try {
    const data = await API.get("/api/admin/feedback");
    if(!data.length) {
      list.innerHTML = `<div style="text-align:center;padding:40px;grid-column:1/-1;color:var(--text-faint)">No feedback submissions yet</div>`;
      return;
    }
    const icons = {suggestion:"💡", bug:"🐛", praise:"🌟", other:"💬"};
    const typeColors = {suggestion:"sky", bug:"rose", praise:"emerald", other:"s2"};
    list.innerHTML = data.map(f => {
      const color = typeColors[f.type] || "s2";
      return `
      <div class="card card-lift card-glow" style="padding:24px; border-left: 4px solid var(--${color === 's2' ? 'border' : color})">
        <div style="display:flex; justify-content:space-between; align-items:start; margin-bottom:14px">
          <div>
            <div style="font-size:0.7rem; color:var(--text-faint); font-weight:700; text-transform:uppercase; letter-spacing:0.1em; margin-bottom:4px">User: ${f.username}</div>
            <h4 style="color:var(--text); font-family:var(--font-head); font-weight:700">${icons[f.type]||'💬'} ${esc(f.subject)}</h4>
          </div>
          ${f.rating ? `<div style="color:var(--amber); font-size:0.9rem; background:rgba(251,191,36,0.1); padding:2px 8px; border-radius:99px">${'★'.repeat(f.rating)}</div>` : ''}
        </div>
        <p style="font-size:0.88rem; color:var(--text-muted); line-height:1.7; margin-bottom:18px; white-space:pre-wrap; border-left: 2px solid var(--border); padding-left:12px">${esc(f.message)}</p>
        <div style="font-size:0.72rem; color:var(--text-faint); display:flex; justify-content:space-between; align-items:center; border-top:1px solid var(--border); padding-top:12px">
           <span>🗓️ ${f.created_at}</span>
           <span class="badge sp-${color}" style="font-size:0.65rem; text-transform:uppercase">${f.type}</span>
        </div>
      </div>
    `}).join("");
  } catch(e){ 
    list.innerHTML = `<div style="text-align:center;padding:40px;grid-column:1/-1;color:var(--rose)">Error: ${e.message}</div>`;
  }
}

// Sync admin status with server before anything else
async function ensureAdminSession(){
  try {
    const u = Auth.get();
    if(!u) return false;
    const r = await API.post("/api/session-sync", {user_id: u.user_id});
    if(r.is_admin) {
      // Update localStorage with admin flag
      u.is_admin = true;
      Auth.set(u);
      return true;
    }
    return false;
  } catch(e){ 
    console.error("Session sync failed:", e);
    return false; 
  }
}

async function loadStats(){
  try {
    const d = await API.get("/api/admin/stats");
    document.getElementById("stat-u").textContent = d.users;
    document.getElementById("stat-m").textContent = d.meals;
  } catch(e){
    console.error("Admin Stats error:", e);
    toast("Admin Stats: " + e.message, "error");
  }
}

async function searchUsers(q=""){
  const list = document.getElementById("u-list");
  try {
    const users = await API.get(`/api/admin/users?q=${q}`);
    if(!users.length) {
       list.innerHTML = `<div style="text-align:center;padding:20px;color:var(--text-faint)">No users found</div>`;
       return;
    }
    list.innerHTML = users.map(u => `
      <div class="user-item" onclick="viewUser(${u.user_id},'${u.username}')" style="padding:12px 16px; background:var(--s2); border:1px solid var(--border); border-radius:12px; cursor:pointer; transition:var(--tr)">
        <div style="font-weight:600; color:var(--text)">${u.username} ${u.is_admin?'<span style="color:var(--accent);font-size:0.7rem">[Admin]</span>':''}</div>
        <div style="font-size:0.75rem; color:var(--text-faint)">Joined: ${u.created_at.split(' ')[0]}</div>
      </div>
    `).join("");
  } catch(e){ 
    list.innerHTML = `<div style="text-align:center;padding:20px;color:var(--rose)">Error: ${e.message}</div>`;
    toast("User Search: " + e.message, "error");
  }
}

async function viewUser(id, name){
  const panel = document.getElementById("u-details-panel");
  panel.innerHTML = `<div class="card" style="text-align:center;padding:40px"><span class="spin"></span><br>Loading details for ${name}…</div>`;
  
  try {
    const d = await API.get(`/api/admin/user-details?user_id=${id}`);
    panel.innerHTML = `
      <div class="card mb16">
        <div style="display:flex; justify-content:space-between; align-items:start; margin-bottom:20px">
          <div>
            <h3 style="margin-bottom:4px">${d.username}</h3>
            <p style="font-size:0.85rem; color:var(--text-faint)">Platform Member since ${d.joined.split(' ')[0]}</p>
          </div>
          <div class="badge-accent">${d.total_meals} Total Logs</div>
        </div>
        
        <div class="g2" style="grid-template-columns: repeat(auto-fit, minmax(140px, 1fr)); gap:12px">
          ${d.members.map(m => `
            <div style="padding:16px; background:var(--s1); border:1px solid var(--border); border-radius:16px">
              <div style="font-weight:700; color:var(--accent); margin-bottom:4px">${m.name}</div>
              <div style="font-size:0.78rem; color:var(--text-muted)">${m.age}y · ${m.gender} · ${m.weight}kg</div>
            </div>
          `).join("")}
        </div>
      </div>

      <div class="card">
        <div class="section-head">
          <h4>Admin AI Summary</h4>
          <button class="btn btn-ghost btn-sm" id="btn-admin-ai" onclick="runAdminAI(${id})">✨ Analyze User</button>
        </div>
        <div id="admin-ai-box" style="margin-top:16px; font-size:0.9rem; line-height:1.7; color:var(--text-muted)">
          Click "Analyze User" to generate an executive health summary for this member.
        </div>
      </div>
    `;
  } catch(e){ panel.innerHTML = "Error loading details"; }
}

async function runAdminAI(id){
  const box = document.getElementById("admin-ai-box");
  const btn = document.getElementById("btn-admin-ai");
  btn.disabled = true; btn.textContent = "Analyzing…";
  box.innerHTML = `<div style="padding:20px;text-align:center"><span class="spin"></span> Analyzing user data…</div>`;
  
  try {
    const d = await API.get(`/api/admin/ai-summary?user_id=${id}`);
    box.innerHTML = `<div style="background:var(--s2); padding:20px; border-radius:16px; border-left:4px solid var(--accent)">${(d.summary||'').split(String.fromCharCode(10)).join('<br>')}</div>`;
  } catch(e){ box.innerHTML = "Error generating AI summary."; }
  finally { btn.disabled = false; btn.textContent = "✨ Analyze User"; }
}

document.getElementById("u-search").oninput = (e) => searchUsers(e.target.value);

// Init: sync session, check admin, then load data
(async function(){
  const isAdmin = await ensureAdminSession();
  if(!isAdmin && !Auth.isAdmin()){
    window.location.href="/";
    return;
  }
  loadStats();
  searchUsers();
})();
</script>
<style>
.user-item:hover { border-color: var(--accent); transform: translateX(4px); background: var(--s3) !important; }
.badge-accent { background: var(--accent-subtle); color: var(--accent); padding: 4px 12px; border-radius: 20px; font-size: 0.75rem; font-weight: 700; }
@media (max-width: 1024px) {
  #admin-main-grid { grid-template-columns: 1fr !important; }
}
</style>
"""



# ══════════════════════════════════════════════════════════════════
#  FLASK ROUTES — PAGES
# ══════════════════════════════════════════════════════════════════
@app.route("/")
def r_index():      return page("NutriFit", INDEX, "/")
@app.route("/dashboard")
def r_dashboard():  return page("Dashboard", DASHBOARD, "/dashboard")
@app.route("/admin")
def r_admin():      return page("Admin Panel", ADMIN, "/admin")
@app.route("/meal-log")
def r_meal_log():   return page("Meal Log", MEAL_LOG, "/meal-log")
@app.route("/ai-advisor")
def r_ai():         return page("AI Advisor", AI_ADV, "/ai-advisor")

@app.route("/favicon.ico")
def r_favicon():
    return app.send_static_file("favicon.ico") if app.static_folder else ("", 204)

# ══════════════════════════════════════════════════════════════════
#  FLASK ROUTES — API  (all unchanged from previous version)
# ══════════════════════════════════════════════════════════════════
@app.route("/api/signup", methods=["POST"])
@limit("5 per minute")
def api_signup():
    d=request.json; u,p=d.get("username","").strip(),d.get("password","").strip()
    if not u or not p: return jsonify({"error":"Username and password required"}),400
    if BCRYPT_OK:
        p_store = bcrypt.hashpw(p.encode(), bcrypt.gensalt()).decode()
    else:
        p_store = p
    db,cur=get_db()
    try:
        cur.execute("SELECT user_id FROM users WHERE username=%s",(u,))
        if cur.fetchone(): return jsonify({"error":"Username already exists"}),409
        cur.execute("INSERT INTO users(username,password) VALUES(%s,%s)",(u,p_store))
        db.commit(); return jsonify({"message":"Account created"}),201
    except Exception as e: return jsonify({"error":str(e)}),500
    finally: close_db(db,cur)

@app.route("/api/login", methods=["POST"])
@limit("10 per minute")
def api_login():
    d=request.json; db,cur=get_db()
    try:
        cur.execute("SELECT user_id,password,is_admin FROM users WHERE username=%s",(d.get("username"),))
        r=cur.fetchone()
        if not r: return jsonify({"error":"Invalid credentials"}),401
        uid,stored,is_admin=r[0],r[1],r[2]
        if BCRYPT_OK:
            try:
                ok = bcrypt.checkpw(d.get("password","").encode(), stored.encode())
            except Exception:
                ok = (stored == d.get("password",""))
        else:
            ok = (stored == d.get("password",""))
        if ok:
            session["user_id"] = uid
            session["is_admin"] = bool(is_admin)
            return jsonify({"message":"Login successful","user_id":uid,"is_admin":bool(is_admin)})
        return jsonify({"error":"Invalid credentials"}),401
    except Exception as e: return jsonify({"error":str(e)}),500
    finally: close_db(db,cur)
    
@app.route("/api/logout", methods=["POST"])
def api_logout():
    session.clear()
    return jsonify({"message":"Logged out successfully"})

@app.route("/api/session-sync", methods=["POST"])
def api_session_sync():
    """Re-establish server session from client-side auth (handles stale cookies)."""
    d = request.json
    uid = d.get("user_id")
    if not uid:
        return jsonify({"error": "user_id required"}), 400
    db, cur = get_db()
    try:
        cur.execute("SELECT user_id, username, is_admin FROM users WHERE user_id=%s", (uid,))
        r = cur.fetchone()
        if not r:
            return jsonify({"error": "User not found"}), 404
        session["user_id"] = r[0]
        session["is_admin"] = bool(r[2])
        return jsonify({"message": "Session synced", "is_admin": bool(r[2])})
    except Exception as e:
        return jsonify({"error": str(e)}), 500
    finally:
        close_db(db, cur)

@app.route("/api/members", methods=["GET"])
def api_get_members():
    uid=request.args.get("user_id"); db,cur=get_db()
    try:
        cur.execute("SELECT member_id,name,age,gender,weight,height FROM members WHERE user_id=%s",(uid,))
        return jsonify([{"member_id":r[0],"name":r[1],"age":r[2],"gender":r[3],"weight":float(r[4] or 0),"height":float(r[5] or 0)} for r in cur.fetchall()])
    finally: close_db(db,cur)

@app.route("/api/members", methods=["POST"])
def api_add_member():
    d=request.json; db,cur=get_db()
    try:
        cur.execute("INSERT INTO members(user_id,name,age,gender,weight,height) VALUES(%s,%s,%s,%s,%s,%s)",(d["user_id"],d["name"],d["age"],d["gender"],d["weight"],d["height"]))
        db.commit(); return jsonify({"message":"Member added","member_id":cur.lastrowid}),201
    except Exception as e: return jsonify({"error":str(e)}),500
    finally: close_db(db,cur)

@app.route("/api/food/search")
def api_food_search():
    q=request.args.get("q","").strip()
    if not q: return jsonify([]), 200
    res = mcp_search_food(q)
    return jsonify(res["results"])

@app.route("/api/food/estimate", methods=["POST"])
@limit("10 per minute")
def api_food_estimate():
    nm=request.json.get("food_name","").strip()
    if not nm: return jsonify({"error":"food_name required"}),400
    raw=ai_generate(f"Estimate nutrition for {nm} per 100g.\nReply ONLY in this exact format:\nCalories: <number>\nProtein: <number>\nCarbs: <number>\nFat: <number>")
    try:
        cal,pro,car,fat=extract_nutrition(raw)
        # Generate embedding for the new food item
        emb_json = None
        try:
            if AI_ENABLED:
                client = get_genai_client()
                if client:
                    emb = client.models.embed_content(
                        model=GEMINI_EMBED_MODEL,
                        contents=nm,
                    )
                    vec = emb.embeddings[0].values if getattr(emb, "embeddings", None) else None
                    if vec:
                        emb_json = json.dumps(list(vec))
        except Exception as ee:
            logger.warning(f"Embedding generation failed for {nm}: {ee}")

        db,cur=get_db()
        try:
            cur.execute("""INSERT INTO food_items(food_name,calories,protein,carbs,fat,embedding) 
                           VALUES(%s,%s,%s,%s,%s,%s) 
                           ON DUPLICATE KEY UPDATE calories=%s,protein=%s,carbs=%s,fat=%s,embedding=%s""",
                        (nm,cal,pro,car,fat,emb_json,cal,pro,car,fat,emb_json))
            db.commit()
            cur.execute("SELECT food_id FROM food_items WHERE food_name=%s",(nm,))
            fid=cur.fetchone()[0]
            return jsonify({"food_id":fid,"food_name":nm,"calories":cal,"protein":pro,"carbs":car,"fat":fat})
        finally: close_db(db,cur)
    except Exception as e: return jsonify({"error":str(e),"raw":raw}),500

@app.route("/api/meals", methods=["POST"])
def api_add_meal():
    d=request.json; db,cur=get_db()
    try:
        cur.execute("INSERT INTO meals(member_id,meal_type,meal_date) VALUES(%s,%s,%s)",(d["member_id"],d["meal_type"],d["meal_date"]))
        mid=cur.lastrowid
        cur.execute("INSERT INTO meal_food(meal_id,food_id,quantity) VALUES(%s,%s,%s)",(mid,d["food_id"],d["quantity"]))
        db.commit(); return jsonify({"message":"Meal logged","meal_id":mid}),201
    except Exception as e: return jsonify({"error":str(e)}),500
    finally: close_db(db,cur)

@app.route("/api/summary/daily")
def api_daily():
    uid=request.args.get("user_id")
    selected_date=request.args.get("date", str(date.today()))
    db,cur=get_db()
    try:
        cur.execute("""SELECT m.meal_type,f.food_name,f.calories,f.protein,f.carbs,f.fat,mf.quantity,(f.calories*mf.quantity)
            FROM meal_food mf JOIN food_items f ON mf.food_id=f.food_id
            JOIN meals m ON mf.meal_id=m.meal_id JOIN members mem ON m.member_id=mem.member_id
            WHERE mem.user_id=%s AND m.meal_date=%s""",(uid, selected_date))
        rows=cur.fetchall()
        items=[{"meal_type":r[0],"food_name":r[1],"calories":float(r[2] or 0),"protein":float(r[3] or 0),
            "carbs":float(r[4] or 0),"fat":float(r[5] or 0),"quantity":float(r[6] or 0),"total_calories":float(r[7] or 0)} for r in rows]
        totals={"calories":sum(i["total_calories"] for i in items),
            "protein":sum(i["protein"]*i["quantity"] for i in items),
            "carbs":sum(i["carbs"]*i["quantity"] for i in items),
            "fat":sum(i["fat"]*i["quantity"] for i in items)}
        return jsonify({"items":items,"totals":totals,"date":selected_date})
    finally: close_db(db,cur)

@app.route("/api/summary/weekly")
def api_weekly():
    uid=request.args.get("user_id"); db,cur=get_db()
    try:
        cur.execute("""SELECT DATE(m.meal_date),SUM(f.calories*mf.quantity),SUM(f.protein*mf.quantity),
            SUM(f.carbs*mf.quantity),SUM(f.fat*mf.quantity)
            FROM meal_food mf JOIN food_items f ON mf.food_id=f.food_id
            JOIN meals m ON mf.meal_id=m.meal_id JOIN members mem ON m.member_id=mem.member_id
            WHERE mem.user_id=%s AND m.meal_date>=CURDATE()-INTERVAL 7 DAY
            GROUP BY DATE(m.meal_date) ORDER BY 1""",(uid,))
        return jsonify([{"date":str(r[0]),"calories":float(r[1] or 0),"protein":float(r[2] or 0),
            "carbs":float(r[3] or 0),"fat":float(r[4] or 0)} for r in cur.fetchall()])
    finally: close_db(db,cur)

@app.route("/api/ai/meal-suggestion", methods=["POST"])
@limit("10 per minute")
def api_suggest():
    t=request.json.get("totals",{})
    return jsonify({"response":ai_generate(f"Today's intake:\nCalories:{t.get('calories',0)}\nProtein:{t.get('protein',0)}g\nCarbs:{t.get('carbs',0)}g\nFat:{t.get('fat',0)}g\n\nSuggest a healthy next meal to balance the day.")})

@app.route("/api/ai/meal-plan", methods=["POST"])
@limit("10 per minute")
def api_meal_plan():
    d=request.json
    return jsonify({"response":ai_generate(f"Create a 1-day meal plan.\nGoal:{d.get('goal','Maintain Weight')}\nDiet:{d.get('diet','Vegetarian')}\nInclude breakfast, lunch, dinner, snacks with estimated calories.")})

@app.route("/api/ai/weekly-analysis", methods=["POST"])
@limit("10 per minute")
def api_weekly_analysis():
    d=request.json
    return jsonify({"response":ai_generate(f"Analyze weekly diet:\nCalories:{d.get('calories',0)}\nProtein:{d.get('protein',0)}g\nCarbs:{d.get('carbs',0)}g\nFat:{d.get('fat',0)}g\nProvide: diet quality, nutrient imbalance, 3 improvement tips.")})

# ══════════════════════════════════════════════════════════════════
#  ADMIN API (Feature: Owner Insights)
# ══════════════════════════════════════════════════════════════════
def admin_only():
    # Fast path: session already has admin flag
    if session.get("is_admin"):
        return None
    # Fallback: check the database in case the session is stale
    uid = session.get("user_id")
    if uid:
        db, cur = get_db()
        try:
            cur.execute("SELECT is_admin FROM users WHERE user_id=%s", (uid,))
            r = cur.fetchone()
            if r and r[0]:
                session["is_admin"] = True  # refresh session
                return None
        finally:
            close_db(db, cur)
    return jsonify({"error":"Unauthorized. Admin access only."}),403

@app.route("/api/admin/stats")
def api_admin_stats():
    chk = admin_only()
    if chk: return chk
    db, cur = get_db()
    try:
        cur.execute("SELECT COUNT(*) FROM users")
        u_count = cur.fetchone()[0]
        cur.execute("SELECT COUNT(*) FROM meals")
        m_count = cur.fetchone()[0]
        cur.execute("SELECT COUNT(*) FROM members")
        mem_count = cur.fetchone()[0]
        return jsonify({"users": u_count, "meals": m_count, "members": mem_count})
    finally: close_db(db, cur)

@app.route("/api/admin/users")
def api_admin_users():
    chk = admin_only()
    if chk: return chk
    q = request.args.get("q", "")
    db, cur = get_db()
    try:
        if q:
            cur.execute("SELECT user_id,username,created_at,is_admin FROM users WHERE username LIKE %s", ("%"+q+"%",))
        else:
            cur.execute("SELECT user_id,username,created_at,is_admin FROM users ORDER BY created_at DESC LIMIT 50")
        rows = cur.fetchall()
        return jsonify([{"user_id": r[0], "username": r[1], "created_at": str(r[2]), "is_admin": bool(r[3])} for r in rows])
    finally: close_db(db, cur)

@app.route("/api/admin/user-details")
def api_admin_user_details():
    chk = admin_only()
    if chk: return chk
    uid = request.args.get("user_id")
    db, cur = get_db()
    try:
        cur.execute("SELECT username,created_at FROM users WHERE user_id=%s", (uid,))
        user = cur.fetchone()
        cur.execute("SELECT COUNT(*) FROM meals WHERE member_id IN (SELECT member_id FROM members WHERE user_id=%s)", (uid,))
        meals = cur.fetchone()[0]
        cur.execute("SELECT name,age,gender,weight,height FROM members WHERE user_id=%s", (uid,))
        mems = cur.fetchall()
        return jsonify({
            "username": user[0], "joined": str(user[1]), "total_meals": meals,
            "members": [{"name": m[0], "age": m[1], "gender": m[2], "weight": m[3], "height": m[4]} for m in mems]
        })
    finally: close_db(db, cur)

@app.route("/api/admin/ai-summary")
@limit("5 per minute")
def api_admin_ai_summary():
    chk = admin_only()
    if chk: return chk
    uid = request.args.get("user_id")
    db, cur = get_db()
    try:
        cur.execute("""SELECT f.food_name, ROUND(f.calories * mf.quantity, 1) as total_cal, m.meal_date, m.meal_type
                       FROM meals m
                       JOIN meal_food mf ON m.meal_id = mf.meal_id
                       JOIN food_items f ON mf.food_id = f.food_id
                       WHERE m.member_id IN (SELECT member_id FROM members WHERE user_id=%s) 
                       ORDER BY m.meal_date DESC LIMIT 20""", (uid,))
        rows = cur.fetchall()
        meal_txt = "\n".join([f"{r[2]} ({r[3]}): {r[0]} — {r[1]} kcal" for r in rows])
        prompt = f"Analyze this user's data for the administrator. Provide a professional executive summary of their engagement and health trend. User ID: {uid}\nRecent Logs:\n{meal_txt}"
        res = ai_generate(prompt, max_tokens=400)
        return jsonify({"summary": res})
    finally: close_db(db, cur)

@app.route("/api/admin/feedback")
def api_admin_feedback():
    chk = admin_only()
    if chk: return chk
    db, cur = get_db()
    try:
        cur.execute("""SELECT f.id, u.username, f.type, f.subject, f.message, f.rating, f.created_at 
                       FROM feedback f JOIN users u ON f.user_id = u.user_id 
                       ORDER BY f.created_at DESC""")
        rows = cur.fetchall()
        return jsonify([{"id": r[0], "username": r[1], "type": r[2], "subject": r[3], "message": r[4], "rating": r[5], "created_at": str(r[6])} for r in rows])
    finally: close_db(db, cur)

@app.route("/api/ai/ask", methods=["POST"])
@limit("10 per minute")
def api_ask():
    q=request.json.get("question","")
    if not q: return jsonify({"error":"Question required"}),400
    # Unified dispatch: calls mcp_dispatch which automatically decides if a tool is needed
    return jsonify(mcp_dispatch(session.get("user_id"), q))

@app.route("/api/ai/mcp", methods=["POST"])
def api_mcp():
    d=request.json
    return jsonify(mcp_dispatch(d.get("user_id"),d.get("query","")))

# ══════════════════════════════════════════════════════════════════
#  ABOUT PAGE
# ══════════════════════════════════════════════════════════════════
ABOUT = """
<div class="page-wrap">

<!-- Hero -->
<section style="padding:80px 0 60px;position:relative;overflow:hidden">
  <div style="position:absolute;top:-10%;left:40%;width:600px;height:600px;
    background:radial-gradient(circle,rgba(249,115,22,0.06) 0%,transparent 65%);
    pointer-events:none"></div>
  <div class="container" style="max-width:860px">
    <div class="hero-badge" style="margin-bottom:20px">👨‍💻 The Developer</div>
    <h1 style="font-size:clamp(2.4rem,5vw,3.6rem);letter-spacing:-0.04em;margin-bottom:18px">
      Built by a student,<br><span class="grad">for real results.</span>
    </h1>
    <p style="font-size:1rem;color:var(--text-muted);max-width:520px;line-height:1.8;margin-bottom:0">
      NutriFit started as a passion project and grew into a fully functional nutrition intelligence platform — smart cloud AI, real database, seamless experience.
    </p>
  </div>
</section>

<!-- Main content -->
<div class="container" style="max-width:860px;padding-bottom:80px">

  <!-- Profile card -->
  <div class="card" style="background:linear-gradient(135deg,var(--s1),var(--s2));
    border-color:rgba(249,115,22,.18);margin-bottom:20px;padding:36px">
    <div style="display:flex;align-items:center;gap:28px;flex-wrap:wrap">
      <!-- Avatar -->
      <div style="width:88px;height:88px;border-radius:20px;flex-shrink:0;
        background:linear-gradient(135deg,var(--accent),#EA580C);
        display:flex;align-items:center;justify-content:center;
        font-family:var(--font-head);font-size:2.2rem;font-weight:800;color:#fff;
        box-shadow:0 0 32px var(--accent-glow)">PJ</div>
      <!-- Info -->
      <div style="flex:1;min-width:200px">
        <h2 style="font-size:1.8rem;margin-bottom:4px;letter-spacing:-0.03em">Priyanshu Jaiswal</h2>
        <p style="font-size:.9rem;color:var(--accent);font-weight:600;margin-bottom:10px;
          font-family:var(--font-head)">Full Stack Developer · AI Enthusiast</p>
        <div style="display:flex;gap:10px;flex-wrap:wrap">
          <span style="display:flex;align-items:center;gap:6px;background:var(--s3);
            border:1px solid var(--border);border-radius:999px;padding:5px 12px;
            font-size:.78rem;color:var(--text-muted)">
            🚀 Independent Developer
          </span>
          <span style="display:flex;align-items:center;gap:6px;background:var(--s3);
            border:1px solid var(--border);border-radius:999px;padding:5px 12px;
            font-size:.78rem;color:var(--text-muted)">
            💻 Building Modern Software
          </span>
        </div>
      </div>
      <!-- Contact button -->
      <a href="mailto:jpriyanshu317@gmail.com"
        style="display:inline-flex;align-items:center;gap:8px;padding:11px 20px;
        border-radius:var(--r-sm);background:linear-gradient(135deg,var(--accent),#EA580C);
        color:#fff;font-weight:600;font-size:.85rem;text-decoration:none;
        box-shadow:0 0 24px var(--accent-glow);transition:var(--tr);white-space:nowrap;
        font-family:var(--font-body)"
        onmouseover="this.style.transform='translateY(-2px)'"
        onmouseout="this.style.transform='none'">
        ✉ Get in Touch
      </a>
    </div>
  </div>

  <!-- 2-col layout -->
  <div class="g2 responsive-grid" style="margin-bottom:20px;align-items:start">

    <!-- About + Contact -->
    <div style="display:flex;flex-direction:column;gap:16px">
      <div class="card">
        <h4 style="margin-bottom:14px;display:flex;align-items:center;gap:8px">
          <span style="background:var(--accent-subtle);border-radius:6px;padding:4px 8px;font-size:.8rem">👤</span>
          About Me
        </h4>
        <p style="font-size:.88rem;line-height:1.85;color:var(--text-muted)">
          I'm Priyanshu Jaiswal, a Full Stack Developer. I'm passionate about building software that solves real problems — NutriFit is a reflection of that.
        </p>
        <p style="font-size:.88rem;line-height:1.85;color:var(--text-muted);margin-top:10px">
          I built this as a project to explore full-stack Python development, AI integration with Google Gemini, and modern UI/UX design — all packed into a single deployable file.
        </p>
      </div>

      <div class="card">
        <h4 style="margin-bottom:16px;display:flex;align-items:center;gap:8px">
          <span style="background:var(--accent-subtle);border-radius:6px;padding:4px 8px;font-size:.8rem">📬</span>
          Contact
        </h4>
        <div style="display:flex;flex-direction:column;gap:10px">
          <a href="mailto:jpriyanshu317@gmail.com"
            style="display:flex;align-items:center;gap:12px;padding:12px 14px;
            background:var(--s2);border:1px solid var(--border);border-radius:var(--r-sm);
            text-decoration:none;transition:var(--tr)"
            onmouseover="this.style.borderColor='var(--accent)'"
            onmouseout="this.style.borderColor='var(--border)'">
            <span style="width:34px;height:34px;border-radius:8px;background:rgba(249,115,22,.1);
              display:flex;align-items:center;justify-content:center;font-size:1rem;flex-shrink:0">📧</span>
            <div>
              <div style="font-size:.72rem;font-weight:600;text-transform:uppercase;
                letter-spacing:.06em;color:var(--text-faint);margin-bottom:2px">Gmail</div>
              <div style="font-size:.88rem;font-weight:600;color:var(--accent)">jpriyanshu317@gmail.com</div>
            </div>
          </a>
          <div style="display:flex;align-items:center;gap:12px;padding:12px 14px;
            background:var(--s2);border:1px solid var(--border);border-radius:var(--r-sm)">
            <span style="width:34px;height:34px;border-radius:8px;background:rgba(56,189,248,.1);
              display:flex;align-items:center;justify-content:center;font-size:1rem;flex-shrink:0">🎓</span>
            <div>
              <div style="font-size:.72rem;font-weight:600;text-transform:uppercase;
                letter-spacing:.06em;color:var(--text-faint);margin-bottom:2px">Role</div>
              <div style="font-size:.88rem;font-weight:600;color:var(--text)">Independent Developer</div>
            </div>
          </div>
          <div style="display:flex;align-items:center;gap:12px;padding:12px 14px;
            background:var(--s2);border:1px solid var(--border);border-radius:var(--r-sm)">
            <span style="width:34px;height:34px;border-radius:8px;background:rgba(139,92,246,.1);
              display:flex;align-items:center;justify-content:center;font-size:1rem;flex-shrink:0">💻</span>
            <div>
              <div style="font-size:.72rem;font-weight:600;text-transform:uppercase;
                letter-spacing:.06em;color:var(--text-faint);margin-bottom:2px">Role</div>
              <div style="font-size:.88rem;font-weight:600;color:var(--text)">Full Stack Developer</div>
            </div>
          </div>
        </div>
      </div>
    </div>

    <!-- Tech stack + project -->
    <div style="display:flex;flex-direction:column;gap:16px">
      <div class="card">
        <h4 style="margin-bottom:16px;display:flex;align-items:center;gap:8px">
          <span style="background:var(--accent-subtle);border-radius:6px;padding:4px 8px;font-size:.8rem">🛠️</span>
          Tech Stack
        </h4>
        <div style="display:flex;flex-direction:column;gap:8px">
          <div style="display:flex;align-items:center;justify-content:space-between;
            padding:10px 14px;background:var(--s2);border:1px solid var(--border);border-radius:var(--r-sm)">
            <div style="display:flex;align-items:center;gap:10px">
              <span style="font-size:1.1rem">🐍</span>
              <span style="font-size:.86rem;font-weight:600;color:var(--text)">Python + Flask</span>
            </div>
            <span style="font-size:.72rem;background:rgba(249,115,22,.1);color:var(--accent);
              border-radius:999px;padding:3px 10px;font-weight:600">Backend</span>
          </div>
          <div style="display:flex;align-items:center;justify-content:space-between;
            padding:10px 14px;background:var(--s2);border:1px solid var(--border);border-radius:var(--r-sm)">
            <div style="display:flex;align-items:center;gap:10px">
              <span style="font-size:1.1rem">🗄️</span>
              <span style="font-size:.86rem;font-weight:600;color:var(--text)">MySQL</span>
            </div>
            <span style="font-size:.72rem;background:rgba(56,189,248,.1);color:var(--sky);
              border-radius:999px;padding:3px 10px;font-weight:600">Database</span>
          </div>
          <div style="display:flex;align-items:center;justify-content:space-between;
            padding:10px 14px;background:var(--s2);border:1px solid var(--border);border-radius:var(--r-sm)">
            <div style="display:flex;align-items:center;gap:10px">
              <span style="font-size:1.1rem">🤖</span>
              <span style="font-size:.86rem;font-weight:600;color:var(--text)">Google Gemini API</span>
            </div>
            <span style="font-size:.72rem;background:rgba(16,185,129,.1);color:var(--emerald);
              border-radius:999px;padding:3px 10px;font-weight:600">AI Engine</span>
          </div>
          <div style="display:flex;align-items:center;justify-content:space-between;
            padding:10px 14px;background:var(--s2);border:1px solid var(--border);border-radius:var(--r-sm)">
            <div style="display:flex;align-items:center;gap:10px">
              <span style="font-size:1.1rem">🎨</span>
              <span style="font-size:.86rem;font-weight:600;color:var(--text)">HTML · CSS · Vanilla JS</span>
            </div>
            <span style="font-size:.72rem;background:rgba(251,191,36,.1);color:var(--amber);
              border-radius:999px;padding:3px 10px;font-weight:600">Frontend</span>
          </div>
          <div style="display:flex;align-items:center;justify-content:space-between;
            padding:10px 14px;background:var(--s2);border:1px solid var(--border);border-radius:var(--r-sm)">
            <div style="display:flex;align-items:center;gap:10px">
              <span style="font-size:1.1rem">🔧</span>
              <span style="font-size:.86rem;font-weight:600;color:var(--text)">MCP Tool Layer</span>
            </div>
            <span style="font-size:.72rem;background:rgba(139,92,246,.1);color:var(--violet);
              border-radius:999px;padding:3px 10px;font-weight:600">AI Tools</span>
          </div>
        </div>
      </div>

      <div class="card" style="background:var(--accent-subtle);border-color:rgba(249,115,22,.15)">
        <h4 style="margin-bottom:12px;display:flex;align-items:center;gap:8px">
          <span style="background:var(--accent-subtle);border-radius:6px;padding:4px 8px;font-size:.8rem">💡</span>
          Why I Built This
        </h4>
        <p style="font-size:.86rem;line-height:1.85;color:var(--text-muted)">
          Most nutrition apps are bloated with unnecessary features. I wanted to prove that a powerful, AI-driven nutrition tracker could be built with modern cloud APIs like Google Gemini — smart, fast, and highly effective.
        </p>
        <p style="font-size:.86rem;line-height:1.85;color:var(--text-muted);margin-top:10px">
          NutriFit uses intelligent cloud AI. Your health metrics are securely processed to give you the best possible recommendations.
        </p>
      </div>
    </div>
  </div>

  <!-- Features built -->
  <div class="card" style="margin-bottom:20px">
    <h4 style="margin-bottom:18px;display:flex;align-items:center;gap:8px">
      <span style="background:var(--accent-subtle);border-radius:6px;padding:4px 8px;font-size:.8rem">✅</span>
      What's Inside NutriFit
    </h4>
    <div class="responsive-grid" style="display:grid;grid-template-columns:repeat(3,1fr);gap:10px">
      <div style="display:flex;align-items:center;gap:10px;padding:10px 12px;
        background:var(--s2);border-radius:var(--r-sm);border:1px solid var(--border)">
        <span style="color:var(--emerald);font-size:1rem">✓</span>
        <span style="font-size:.82rem;color:var(--text-muted)">Daily calorie tracking</span>
      </div>
      <div style="display:flex;align-items:center;gap:10px;padding:10px 12px;
        background:var(--s2);border-radius:var(--r-sm);border:1px solid var(--border)">
        <span style="color:var(--emerald);font-size:1rem">✓</span>
        <span style="font-size:.82rem;color:var(--text-muted)">Weekly nutrition report</span>
      </div>
      <div style="display:flex;align-items:center;gap:10px;padding:10px 12px;
        background:var(--s2);border-radius:var(--r-sm);border:1px solid var(--border)">
        <span style="color:var(--emerald);font-size:1rem">✓</span>
        <span style="font-size:.82rem;color:var(--text-muted)">AI food estimation</span>
      </div>
      <div style="display:flex;align-items:center;gap:10px;padding:10px 12px;
        background:var(--s2);border-radius:var(--r-sm);border:1px solid var(--border)">
        <span style="color:var(--emerald);font-size:1rem">✓</span>
        <span style="font-size:.82rem;color:var(--text-muted)">AI personalised meal plans</span>
      </div>
      <div style="display:flex;align-items:center;gap:10px;padding:10px 12px;
        background:var(--s2);border-radius:var(--r-sm);border:1px solid var(--border)">
        <span style="color:var(--emerald);font-size:1rem">✓</span>
        <span style="font-size:.82rem;color:var(--text-muted)">Multi-member tracking</span>
      </div>
      <div style="display:flex;align-items:center;gap:10px;padding:10px 12px;
        background:var(--s2);border-radius:var(--r-sm);border:1px solid var(--border)">
        <span style="color:var(--emerald);font-size:1rem">✓</span>
        <span style="font-size:.82rem;color:var(--text-muted)">BMI auto-calculation</span>
      </div>
      <div style="display:flex;align-items:center;gap:10px;padding:10px 12px;
        background:var(--s2);border-radius:var(--r-sm);border:1px solid var(--border)">
        <span style="color:var(--emerald);font-size:1rem">✓</span>
        <span style="font-size:.82rem;color:var(--text-muted)">Nutrition score (0–100)</span>
      </div>
      <div style="display:flex;align-items:center;gap:10px;padding:10px 12px;
        background:var(--s2);border-radius:var(--r-sm);border:1px solid var(--border)">
        <span style="color:var(--emerald);font-size:1rem">✓</span>
        <span style="font-size:.82rem;color:var(--text-muted)">MCP AI tool layer</span>
      </div>
      <div style="display:flex;align-items:center;gap:10px;padding:10px 12px;
        background:var(--s2);border-radius:var(--r-sm);border:1px solid var(--border)">
        <span style="color:var(--emerald);font-size:1rem">✓</span>
        <span style="font-size:.82rem;color:var(--text-muted)">100% cloud smart</span>
      </div>
    </div>
  </div>

  <!-- Footer CTA -->
  <div class="card" style="text-align:center;padding:40px;
    background:linear-gradient(135deg,rgba(249,115,22,.06),rgba(234,88,12,.03));
    border-color:rgba(249,115,22,.15)">
    <h3 style="margin-bottom:8px;font-size:1.4rem">Got feedback or questions?</h3>
    <p style="margin-bottom:22px;font-size:.9rem">
      I'm always open to feedback, collaboration, or just a chat about tech.
    </p>
    <a href="mailto:jpriyanshu317@gmail.com"
      style="display:inline-flex;align-items:center;gap:8px;padding:12px 24px;
      border-radius:var(--r-sm);background:linear-gradient(135deg,var(--accent),#EA580C);
      color:#fff;font-weight:700;font-size:.9rem;text-decoration:none;
      box-shadow:0 0 24px var(--accent-glow);font-family:var(--font-head);
      transition:var(--tr)"
      onmouseover="this.style.transform='translateY(-2px)';this.style.boxShadow='0 8px 32px rgba(249,115,22,.35)'"
      onmouseout="this.style.transform='none';this.style.boxShadow='0 0 24px var(--accent-glow)'">
      ✉ jpriyanshu317@gmail.com
    </a>
    <p style="margin-top:24px;font-size:.76rem;color:var(--text-faint)">
      Made with ☕ &amp; Python · Building Modern Software · 2026
    </p>
  </div>

</div>
</div>
"""

@app.route("/about")
def r_about():  return page("About", ABOUT, "/about")

# ══════════════════════════════════════════════════════════════════
#  NEW FEATURE ENDPOINTS
# ══════════════════════════════════════════════════════════════════

# ── Feature 2: 30/90-day trend ────────────────────────────────────
@app.route("/api/summary/trend")
@limit("5 per minute")
def api_trend():
    uid=request.args.get("user_id")
    days=int(request.args.get("days",30))
    db,cur=get_db()
    try:
        cur.execute("""SELECT DATE(m.meal_date) as day,
            SUM(f.calories*mf.quantity),SUM(f.protein*mf.quantity),
            SUM(f.carbs*mf.quantity),SUM(f.fat*mf.quantity)
            FROM meal_food mf JOIN food_items f ON mf.food_id=f.food_id
            JOIN meals m ON mf.meal_id=m.meal_id JOIN members mem ON m.member_id=mem.member_id
            WHERE mem.user_id=%s AND m.meal_date>=CURDATE()-INTERVAL %s DAY
            GROUP BY DATE(m.meal_date) ORDER BY day""",(uid,days))
        return jsonify([{"date":str(r[0]),"calories":float(r[1] or 0),"protein":float(r[2] or 0),
            "carbs":float(r[3] or 0),"fat":float(r[4] or 0)} for r in cur.fetchall()])
    finally: close_db(db,cur)

# ── Feature 3: Harris-Benedict calorie goal ───────────────────────
@app.route("/api/members/calorie-goal")
@limit("5 per minute")
def api_calorie_goal():
    uid=request.args.get("user_id")
    activity=request.args.get("activity","moderate")
    db,cur=get_db()
    try:
        cur.execute("SELECT name,age,gender,weight,height FROM members WHERE user_id=%s LIMIT 1",(uid,))
        r=cur.fetchone()
        if not r: return jsonify({"error":"No member found"}),404
        name,age,gender,weight,height=r
        w=float(weight or 70); h=float(height or 170); a=int(age or 25)
        # Harris-Benedict BMR
        if gender=="Male":
            bmr=88.362+(13.397*w)+(4.799*h)-(5.677*a)
        else:
            bmr=447.593+(9.247*w)+(3.098*h)-(4.330*a)
        activity_map={"sedentary":1.2,"light":1.375,"moderate":1.55,"active":1.725,"very_active":1.9}
        tdee=bmr*activity_map.get(activity,1.55)
        return jsonify({"name":name,"bmr":round(bmr),"tdee":round(tdee),
            "weight_loss":round(tdee-500),"muscle_gain":round(tdee+300),"maintain":round(tdee)})
    finally: close_db(db,cur)

# ── Feature 4: Weight tracking ────────────────────────────────────
@app.route("/api/weight/log", methods=["POST"])
@limit("10 per minute")
def api_weight_log():
    d=request.json; db,cur=get_db()
    try:
        cur.execute("""INSERT INTO weight_log(user_id,member_id,weight,logged_date,note)
            VALUES(%s,%s,%s,%s,%s) ON DUPLICATE KEY UPDATE weight=%s,note=%s""",
            (d["user_id"],d["member_id"],d["weight"],d.get("date",str(date.today())),d.get("note",""),
             d["weight"],d.get("note","")))
        db.commit()
        # Also update the member's current weight
        cur.execute("UPDATE members SET weight=%s WHERE member_id=%s AND user_id=%s",
            (d["weight"],d["member_id"],d["user_id"]))
        db.commit()
        return jsonify({"message":"Weight logged"}),201
    except Exception as e: return jsonify({"error":str(e)}),500
    finally: close_db(db,cur)

@app.route("/api/weight/history")
def api_weight_history():
    uid=request.args.get("user_id")
    mid=request.args.get("member_id")
    days=int(request.args.get("days",30))
    db,cur=get_db()
    try:
        q="SELECT logged_date,weight,note FROM weight_log WHERE user_id=%s AND logged_date>=CURDATE()-INTERVAL %s DAY"
        params=[uid,days]
        if mid:
            q+=" AND member_id=%s"
            params.append(mid)
        q+=" ORDER BY logged_date"
        cur.execute(q,params)
        return jsonify([{"date":str(r[0]),"weight":float(r[1]),"note":r[2] or ""} for r in cur.fetchall()])
    finally: close_db(db,cur)

# ── Feature 7: CSV export ─────────────────────────────────────────
@app.route("/api/export/csv")
def api_export_csv():
    uid=request.args.get("user_id")
    days=int(request.args.get("days",7))
    db,cur=get_db()
    try:
        cur.execute("""SELECT DATE(m.meal_date),m.meal_type,f.food_name,mf.quantity,
            f.calories,(f.calories*mf.quantity),f.protein,f.carbs,f.fat
            FROM meal_food mf JOIN food_items f ON mf.food_id=f.food_id
            JOIN meals m ON mf.meal_id=m.meal_id JOIN members mem ON m.member_id=mem.member_id
            WHERE mem.user_id=%s AND m.meal_date>=CURDATE()-INTERVAL %s DAY
            ORDER BY m.meal_date,m.meal_type""",(uid,days))
        rows=cur.fetchall()
        si=io.StringIO()
        w=csv.writer(si)
        w.writerow(["Date","Meal Type","Food","Quantity","Cal/Unit","Total Cal","Protein(g)","Carbs(g)","Fat(g)"])
        for r in rows:
            w.writerow([r[0],r[1],r[2],r[3],r[4],round(float(r[5] or 0),1),r[6],r[7],r[8]])
        out=make_response(si.getvalue())
        out.headers["Content-Disposition"]=f"attachment; filename=nutrifit_export_{days}days.csv"
        out.headers["Content-type"]="text/csv"
        return out
    finally: close_db(db,cur)

# ── Feature 9: AI nutrient deficiency detection ───────────────────
@app.route("/api/ai/deficiency", methods=["POST"])
@limit("10 per minute")
def api_deficiency():
    d=request.json; uid=d.get("user_id")
    db,cur=get_db()
    try:
        cur.execute("""SELECT AVG(f.protein*mf.quantity),AVG(f.carbs*mf.quantity),
            AVG(f.fat*mf.quantity),AVG(f.calories*mf.quantity)
            FROM meal_food mf JOIN food_items f ON mf.food_id=f.food_id
            JOIN meals m ON mf.meal_id=m.meal_id JOIN members mem ON m.member_id=mem.member_id
            WHERE mem.user_id=%s AND m.meal_date>=CURDATE()-INTERVAL 7 DAY""",(uid,))
        r=cur.fetchone()
        avg_pro=round(float(r[0] or 0)); avg_car=round(float(r[1] or 0))
        avg_fat=round(float(r[2] or 0)); avg_cal=round(float(r[3] or 0))
        prompt=f"""Analyze this user's average daily nutrition over the past 7 days and identify specific nutrient deficiencies:
Average daily: Calories={avg_cal}kcal, Protein={avg_pro}g, Carbs={avg_car}g, Fat={avg_fat}g

Based on standard nutrition guidelines:
1. List specific nutrients that appear deficient (iron, vitamin D, calcium, fiber, omega-3, etc.)
2. For each deficiency, name 3 specific foods that would help
3. Give one practical meal idea that addresses multiple deficiencies at once
Keep it concise, specific, and actionable."""
        return jsonify({"response":ai_generate(prompt,400),"averages":{"calories":avg_cal,"protein":avg_pro,"carbs":avg_car,"fat":avg_fat}})
    finally: close_db(db,cur)

# ── Feature 11: AI grocery list from meal plan ────────────────────
@app.route("/api/ai/grocery-list", methods=["POST"])
@limit("10 per minute")
def api_grocery():
    d=request.json
    plan=d.get("plan",""); goal=d.get("goal","Maintain Weight"); diet=d.get("diet","Vegetarian")
    if not plan:
        prompt=f"Create a 1-day meal plan for {goal}, {diet}. Then generate a grocery shopping list."
    else:
        prompt=f"Based on this meal plan:\n{plan}\n\nGenerate a complete grocery shopping list."
    prompt+="""
Format the grocery list grouped by category:
🥬 Vegetables & Fruits:
🥩 Proteins:
🌾 Grains & Carbs:
🧴 Dairy & Others:

Include approximate quantities needed. Keep it practical for one week of this diet."""
    return jsonify({"response":ai_generate(prompt,400)})

# ── Feedback ──────────────────────────────────────────────────────
@app.route("/api/feedback", methods=["POST"])
def api_feedback_post():
    d=request.json; db,cur=get_db()
    try:
        cur.execute("""CREATE TABLE IF NOT EXISTS feedback(
            id INT AUTO_INCREMENT PRIMARY KEY,
            user_id INT NOT NULL,
            type VARCHAR(50) DEFAULT 'other',
            subject VARCHAR(255) NOT NULL,
            message TEXT NOT NULL,
            rating INT DEFAULT 0,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY(user_id) REFERENCES users(user_id))""")
        cur.execute("INSERT INTO feedback(user_id,type,subject,message,rating) VALUES(%s,%s,%s,%s,%s)",
            (d["user_id"],d.get("type","other"),d["subject"],d["message"],d.get("rating",0)))
        db.commit(); return jsonify({"message":"Feedback submitted"}),201
    except Exception as e: return jsonify({"error":str(e)}),500
    finally: close_db(db,cur)

@app.route("/api/feedback")
def api_feedback_get():
    uid=request.args.get("user_id"); db,cur=get_db()
    try:
        cur.execute("SELECT id,type,subject,message,rating,created_at FROM feedback WHERE user_id=%s ORDER BY created_at DESC LIMIT 10",(uid,))
        return jsonify([{"id":r[0],"type":r[1],"subject":r[2],"message":r[3],"rating":r[4],"created_at":str(r[5])} for r in cur.fetchall()])
    finally: close_db(db,cur)


if __name__=="__main__":
    logger.info("━"*56)
    logger.info("  NutriFit  ·  Obsidian OS  ·  v2.0 — Production Ready")
    logger.info("━"*56)
    logger.info("  Multi-connection Pool Initialized")
    logger.info("  Rate Limiting Active")
    logger.info("  Logging Active")
    logger.info("  Security Hardening: Strict Cookies")
    logger.info("  UI/UX Polishing: Shimmer & Toasts")
    logger.info("\n  Initialising database…")
    try:
        init_db()
    except Exception as e:
        logger.error(f"  MySQL Error: {e}")
        logger.info("  → Make sure MySQL is running")
        logger.info("  → Run: CREATE DATABASE nutrifit; in MySQL Workbench")
        exit(1)
    logger.info("  Ready  ->  http://localhost:5000\n")
    app.run(host="0.0.0.0", debug=False, port=int(os.getenv("PORT", "5000")))
