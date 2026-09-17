
import os, json, base64, sqlite3, mimetypes, uuid, urllib.request, urllib.error, re, tempfile, hashlib, hmac, secrets, zipfile, shutil
from contextvars import ContextVar
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Optional
from fastapi import FastAPI, UploadFile, File, Form, HTTPException, BackgroundTasks, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
try:
    from openai import OpenAI
except Exception:
    OpenAI = None

from PIL import Image, ImageOps, ImageEnhance, UnidentifiedImageError

from pillow_heif import register_heif_opener

# Enables Pillow to read iPhone HEIC/HEIF photos.
register_heif_opener()

ROOT = Path(__file__).resolve().parent

# Persistent storage.
# Existing single-user data remains untouched at DATA_DIR and becomes the first
# (owner/admin) account's store. Tester accounts receive isolated subdirectories.
DATA_DIR = Path(os.getenv("DATA_DIR", str(ROOT / "data")))
DATA_DIR.mkdir(parents=True, exist_ok=True)

AUTH_DB = DATA_DIR / "accounts.db"
USERS_DIR = DATA_DIR / "users"
USERS_DIR.mkdir(parents=True, exist_ok=True)

CURRENT_USER = ContextVar("ghd_current_user", default=None)
SESSION_COOKIE = "ghd_session"
SESSION_DAYS = 30

app = FastAPI(title="Get Him Dressed")
app.mount("/static", StaticFiles(directory=ROOT/"static"), name="static")

def auth_db():
    con=sqlite3.connect(AUTH_DB)
    con.row_factory=sqlite3.Row
    return con

def utc_now():
    return datetime.now(timezone.utc)

def init_auth_db():
    con=auth_db()
    con.executescript("""
    CREATE TABLE IF NOT EXISTS users (
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      email TEXT UNIQUE NOT NULL,
      display_name TEXT NOT NULL,
      password_hash TEXT NOT NULL,
      role TEXT NOT NULL DEFAULT 'tester',
      styling_profile TEXT NOT NULL DEFAULT 'menswear',
      storage_scope TEXT NOT NULL DEFAULT 'isolated',
      active INTEGER NOT NULL DEFAULT 1,
      created_at TEXT NOT NULL
    );
    CREATE TABLE IF NOT EXISTS sessions (
      token_hash TEXT PRIMARY KEY,
      user_id INTEGER NOT NULL,
      expires_at TEXT NOT NULL,
      created_at TEXT NOT NULL,
      FOREIGN KEY(user_id) REFERENCES users(id)
    );
    CREATE TABLE IF NOT EXISTS invites (
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      code TEXT UNIQUE NOT NULL,
      created_by INTEGER NOT NULL,
      max_uses INTEGER NOT NULL DEFAULT 1,
      uses INTEGER NOT NULL DEFAULT 0,
      expires_at TEXT,
      created_at TEXT NOT NULL,
      FOREIGN KEY(created_by) REFERENCES users(id)
    );
    CREATE TABLE IF NOT EXISTS tester_feedback (
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      user_id INTEGER NOT NULL,
      rating INTEGER,
      category TEXT NOT NULL DEFAULT 'general',
      message TEXT NOT NULL,
      created_at TEXT NOT NULL,
      FOREIGN KEY(user_id) REFERENCES users(id)
    );
    CREATE TABLE IF NOT EXISTS login_attempts (
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      email TEXT NOT NULL,
      attempted_at TEXT NOT NULL
    );
    CREATE INDEX IF NOT EXISTS idx_login_attempts_email_time
      ON login_attempts(email,attempted_at);
    CREATE TABLE IF NOT EXISTS usage_events (
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      user_id INTEGER NOT NULL,
      event_type TEXT NOT NULL,
      endpoint TEXT,
      units REAL NOT NULL DEFAULT 1,
      input_tokens INTEGER NOT NULL DEFAULT 0,
      cached_input_tokens INTEGER NOT NULL DEFAULT 0,
      output_tokens INTEGER NOT NULL DEFAULT 0,
      estimated_usd REAL NOT NULL DEFAULT 0,
      metadata_json TEXT,
      created_at TEXT NOT NULL,
      FOREIGN KEY(user_id) REFERENCES users(id)
    );
    CREATE INDEX IF NOT EXISTS idx_usage_events_user_time ON usage_events(user_id,created_at);
    CREATE INDEX IF NOT EXISTS idx_usage_events_type_time ON usage_events(event_type,created_at);
    """)
    try:
        con.execute("ALTER TABLE users ADD COLUMN active INTEGER NOT NULL DEFAULT 1")
    except sqlite3.OperationalError:
        pass
    try:
        con.execute("ALTER TABLE users ADD COLUMN home_order_json TEXT")
    except sqlite3.OperationalError:
        pass
    con.commit(); con.close()

def password_hash(password: str) -> str:
    if len(password or "") < 8:
        raise HTTPException(400,"Use a password of at least 8 characters.")
    salt=secrets.token_bytes(16)
    iterations=260000
    digest=hashlib.pbkdf2_hmac("sha256",password.encode("utf-8"),salt,iterations)
    return f"pbkdf2_sha256${iterations}${salt.hex()}${digest.hex()}"

def password_ok(password: str, stored: str) -> bool:
    try:
        alg,it,salt_hex,digest_hex=stored.split("$",3)
        if alg!="pbkdf2_sha256": return False
        test=hashlib.pbkdf2_hmac("sha256",password.encode("utf-8"),bytes.fromhex(salt_hex),int(it))
        return hmac.compare_digest(test.hex(),digest_hex)
    except Exception:
        return False

def public_user(row):
    if not row: return None
    d=dict(row)
    return {k:d.get(k) for k in ["id","email","display_name","role","styling_profile","storage_scope","active","created_at"]}

def get_user_by_id(uid: int):
    con=auth_db()
    row=con.execute("SELECT * FROM users WHERE id=?",(uid,)).fetchone()
    con.close()
    return public_user(row)

def current_user():
    return CURRENT_USER.get()

def current_user_id():
    u=current_user()
    return int(u["id"]) if u else None

def active_user_root():
    u=current_user()
    if not u or u.get("storage_scope")=="legacy":
        root=DATA_DIR
    else:
        root=USERS_DIR/str(u["id"])
    root.mkdir(parents=True,exist_ok=True)
    return root

def uploads_dir():
    p=active_user_root()/"uploads"; p.mkdir(parents=True,exist_ok=True); return p

def cleaned_dir():
    p=active_user_root()/"cleaned"; p.mkdir(parents=True,exist_ok=True); return p

def generated_dir():
    p=active_user_root()/"generated"; p.mkdir(parents=True,exist_ok=True); return p

def model_photos_dir():
    p=active_user_root()/"model_photos"; p.mkdir(parents=True,exist_ok=True); return p

def current_db_path():
    return active_user_root()/"stylist.db"

def db():
    con=sqlite3.connect(current_db_path())
    con.row_factory=sqlite3.Row
    return con

def session_hash(token: str):
    return hashlib.sha256(token.encode("utf-8")).hexdigest()

def create_session(user_id: int):
    token=secrets.token_urlsafe(32)
    now=utc_now()
    expires=now+timedelta(days=SESSION_DAYS)
    con=auth_db()
    con.execute("DELETE FROM sessions WHERE expires_at < ?",(now.isoformat(),))
    con.execute("INSERT INTO sessions(token_hash,user_id,expires_at,created_at) VALUES (?,?,?,?)",
                (session_hash(token),user_id,expires.isoformat(),now.isoformat()))
    con.commit(); con.close()
    return token,expires

def user_from_session(token: str):
    if not token: return None
    now=utc_now().isoformat()
    con=auth_db()
    row=con.execute("""
      SELECT u.* FROM sessions s JOIN users u ON u.id=s.user_id
      WHERE s.token_hash=? AND s.expires_at>? AND COALESCE(u.active,1)=1
    """,(session_hash(token),now)).fetchone()
    con.close()
    return public_user(row)

def require_admin():
    u=current_user()
    if not u or u.get("role")!="admin":
        raise HTTPException(403,"Admin access required.")
    return u

TEXT_MODEL_PRICING={
    "gpt-5.6-terra":{"input":2.00,"cached":0.20,"output":12.00},
    "gpt-5.6-luna":{"input":0.20,"cached":0.02,"output":1.20},
    "gpt-5.6-sol":{"input":4.00,"cached":0.40,"output":20.00},
}
BETA_DAILY_IMAGE_LIMIT=max(1,int(os.getenv("BETA_DAILY_IMAGE_LIMIT","20")))
BETA_MONTHLY_IMAGE_LIMIT=max(BETA_DAILY_IMAGE_LIMIT,int(os.getenv("BETA_MONTHLY_IMAGE_LIMIT","300")))

def record_usage_event(event_type:str, endpoint:str="", units:float=1, input_tokens:int=0,
                       cached_input_tokens:int=0, output_tokens:int=0,
                       estimated_usd:float=0, metadata:dict|None=None):
    uid=current_user_id()
    if not uid:
        return
    try:
        con=auth_db()
        con.execute("""INSERT INTO usage_events
          (user_id,event_type,endpoint,units,input_tokens,cached_input_tokens,output_tokens,estimated_usd,metadata_json,created_at)
          VALUES (?,?,?,?,?,?,?,?,?,?)""",
          (uid,event_type,endpoint or "",float(units or 0),int(input_tokens or 0),
           int(cached_input_tokens or 0),int(output_tokens or 0),float(estimated_usd or 0),
           json.dumps(metadata or {},ensure_ascii=False),utc_now().isoformat()))
        con.commit();con.close()
    except Exception:
        pass

def _usage_value(obj,name,default=0):
    if obj is None: return default
    if isinstance(obj,dict): return obj.get(name,default) or default
    return getattr(obj,name,default) or default

def tracked_responses_create(client,*args,**kwargs):
    response=client.responses.create(*args,**kwargs)
    try:
        usage=getattr(response,"usage",None)
        input_tokens=int(_usage_value(usage,"input_tokens",0))
        output_tokens=int(_usage_value(usage,"output_tokens",0))
        details=_usage_value(usage,"input_tokens_details",{}) or {}
        cached=int(_usage_value(details,"cached_tokens",0))
        model=str(kwargs.get("model") or os.getenv("OPENAI_MODEL","gpt-5.6-terra"))
        rates=TEXT_MODEL_PRICING.get(model,TEXT_MODEL_PRICING["gpt-5.6-terra"])
        uncached=max(0,input_tokens-cached)
        cost=(uncached*rates["input"]+cached*rates["cached"]+output_tokens*rates["output"])/1_000_000
        record_usage_event("text_ai","responses",1,input_tokens,cached,output_tokens,cost,{"model":model})
    except Exception:
        record_usage_event("text_ai","responses",1,metadata={"model":str(kwargs.get("model") or "")})
    return response

def image_usage_counts(uid:int|None=None):
    uid=int(uid or current_user_id() or 0)
    if not uid: return {"today":0,"month":0}
    now=utc_now()
    day_start=now.replace(hour=0,minute=0,second=0,microsecond=0).isoformat()
    month_start=now.replace(day=1,hour=0,minute=0,second=0,microsecond=0).isoformat()
    con=auth_db()
    today=int(con.execute("SELECT COALESCE(SUM(units),0) FROM usage_events WHERE user_id=? AND event_type='image_generation' AND created_at>=?",(uid,day_start)).fetchone()[0] or 0)
    month=int(con.execute("SELECT COALESCE(SUM(units),0) FROM usage_events WHERE user_id=? AND event_type='image_generation' AND created_at>=?",(uid,month_start)).fetchone()[0] or 0)
    con.close()
    return {"today":today,"month":month}

def enforce_beta_image_limit():
    u=current_user() or {}
    if u.get("role")=="admin": return
    counts=image_usage_counts()
    if counts["today"]>=BETA_DAILY_IMAGE_LIMIT:
        raise HTTPException(429,f"Beta image limit reached for today ({BETA_DAILY_IMAGE_LIMIT}). Styling still works; more images will be available tomorrow.")
    if counts["month"]>=BETA_MONTHLY_IMAGE_LIMIT:
        raise HTTPException(429,f"Beta image limit reached for this month ({BETA_MONTHLY_IMAGE_LIMIT}). Please contact the beta owner if you need more.")

init_auth_db()

PUBLIC_PATHS={
    "/","/api/health","/api/auth/status","/api/auth/login","/api/auth/register"
}

@app.middleware("http")
async def account_context(request: Request, call_next):
    path=request.url.path
    raw_token=request.cookies.get(SESSION_COOKIE,"")
    user=user_from_session(raw_token)
    token=CURRENT_USER.set(user)
    try:
        protected_media=path.startswith("/uploads/") or path.startswith("/cleaned/") or path.startswith("/generated/") or path.startswith("/model-photos/")
        protected_api=path.startswith("/api/") and path not in PUBLIC_PATHS
        if (protected_api or protected_media) and not user:
            return JSONResponse({"detail":"Please sign in again to continue."},status_code=401)

        # Basic CSRF hardening for authenticated state-changing requests.
        # SameSite=Lax remains the first line of defence; this rejects an explicit
        # cross-origin browser Origin/Referer when one is supplied.
        if user and path.startswith("/api/") and request.method.upper() in {"POST","PUT","PATCH","DELETE"}:
            origin=(request.headers.get("origin") or "").strip()
            referer=(request.headers.get("referer") or "").strip()
            host=(request.headers.get("host") or "").strip().lower()
            candidate=origin or referer
            if candidate:
                try:
                    from urllib.parse import urlparse
                    parsed=urlparse(candidate)
                    candidate_host=(parsed.netloc or "").strip().lower()
                    if candidate_host and candidate_host!=host:
                        return JSONResponse({"detail":"This account action was blocked because it came from another site."},status_code=403)
                except Exception:
                    return JSONResponse({"detail":"This account action could not be verified."},status_code=403)

        return await call_next(request)
    finally:
        CURRENT_USER.reset(token)

def safe_media_path(kind: str, filename: str):
    if Path(filename).name != filename:
        raise HTTPException(400,"Invalid file path.")
    roots={
      "uploads":uploads_dir(),
      "cleaned":cleaned_dir(),
      "generated":generated_dir(),
      "model-photos":model_photos_dir()
    }
    root=roots.get(kind)
    if not root: raise HTTPException(404,"File not found.")
    path=root/filename
    if not path.exists() or not path.is_file():
        raise HTTPException(404,"File not found.")
    return path

@app.get("/uploads/{filename}")
def private_upload(filename:str): return FileResponse(safe_media_path("uploads",filename),headers={"Cache-Control":"private, max-age=3600"})
@app.get("/cleaned/{filename}")
def private_cleaned(filename:str): return FileResponse(safe_media_path("cleaned",filename),headers={"Cache-Control":"private, max-age=3600"})
@app.get("/generated/{filename}")
def private_generated(filename:str): return FileResponse(safe_media_path("generated",filename),headers={"Cache-Control":"private, max-age=3600"})
@app.get("/model-photos/{filename}")
def private_model_photo(filename:str): return FileResponse(safe_media_path("model-photos",filename),headers={"Cache-Control":"private, max-age=3600"})


MENSWEAR_CATEGORY_ORDER = [
    "Blazers & Tailoring", "Overshirts & Shirt Jackets", "Jackets & Coats",
    "Knitwear", "Sweatshirts & Hoodies", "Shirts", "Polos & T-Shirts",
    "Trousers", "Shorts", "Footwear", "Accessories", "Other",
]
WOMENSWEAR_CATEGORY_ORDER = [
    "Dresses", "Skirts", "Jumpsuits & Playsuits", "Blazers & Tailoring",
    "Jackets", "Coats", "Knitwear", "Sweatshirts & Hoodies",
    "Blouses & Shirts", "Tops & T-Shirts", "Trousers & Jeans", "Shorts",
    "Activewear", "Footwear", "Bags", "Jewellery", "Accessories", "Other",
]
WARDROBE_CATEGORY_ORDER = MENSWEAR_CATEGORY_ORDER

def styling_profile():
    return ((current_user() or {}).get("styling_profile") or "menswear").strip().lower()

def is_womenswear():
    return styling_profile()=="womenswear"

def fashion_audience():
    return "women's" if is_womenswear() else "men's"

def fashion_person():
    return "adult female model" if is_womenswear() else "adult male model"

def product_brand_name():
    return "Get Her Dressed" if is_womenswear() else "Get Him Dressed"

def wardrobe_category_order():
    return WOMENSWEAR_CATEGORY_ORDER if is_womenswear() else MENSWEAR_CATEGORY_ORDER

def styling_profile_guidance():
    if is_womenswear():
        return """
WOMENSWEAR PROFILE:
- Act as a contemporary women's personal stylist, without assumptions about age, body shape, femininity, modesty or preferred level of dressiness.
- Build from the user's real wardrobe and stated preferences first. Dresses, skirts and heels are options, never defaults.
- Evaluate silhouette and proportion as a complete outfit: where a waist is defined or relaxed, balance between fitted/straight/wide pieces, hem position, trouser rise, jacket length, sleeve volume and footwear weight.
- Distinguish top size, bottom size, dress size, shoe size and bra size. Women's sizing varies sharply by brand, line and fabrication; exact personal fit evidence outranks generic size charts.
- For dresses and jumpsuits, reason separately about bust/chest, waist, hips, torso length, overall length, neckline and sleeve/strap fit.
- For trousers and jeans, consider rise, waistband, hips/seat, thigh, leg shape and inseam. Do not treat a waist measurement alone as enough to predict fit.
- For skirts, consider waist/hip relationship, intended sitting point and hem length.
- For tailoring, distinguish structured vs relaxed shoulders, bust closure, waist suppression, jacket length and trouser/skirt proportion.
- For knitwear/tops, consider neckline, shoulder line, bust ease, body length, sleeve shape and layering.
- Footwear should account for practical walking, trouser/hem length and user heel preference. Never assume heels are needed for smartness.
- Bags and jewellery should be treated as purposeful styling tools: scale, hardware, formality, colour and visual balance should support the outfit rather than being added automatically.
- Occasionwear should distinguish wedding guest, cocktail/party, formal evening, work event, smart dinner, daytime event and business needs. Avoid generic 'occasionwear' advice.
- When a user has not stated a preference, offer a strong contemporary option without inventing a body-shape rule or gender stereotype.
- If an accessory or jewellery item materially completes a look and the user owns one, use its real garment ID. Do not invent ownership.
"""
    return """
MENSWEAR PROFILE:
- Style as a contemporary men's personal stylist.
- Pay attention to shoulder/chest fit, trouser rise/leg, sleeve/body length, layering, footwear and level of tailoring.
- Use the user's wardrobe and fit evidence rather than generic brand assumptions.
- The visible category "Jackets & Coats" intentionally groups true outerwear together, but garment_type/model_line/notes remain decisive: distinguish wax, rain/technical, denim/trucker, sherpa-lined, bomber, field, leather, parka, mac, overcoat and winter outerwear by function, warmth, weather suitability and formality.
- "Overshirts & Shirt Jackets" is deliberately separate because those pieces may function as a shirt, mid-layer or light outer layer.
- "Blazers & Tailoring" contains blazers, sports coats and suits/suit components. Use garment_type/model_line to distinguish complete suits, suit jackets, matching suit trousers and waistcoats, and only suggest separating matching suit pieces when the fabric/cut/context makes that sensible.
"""


def canonical_wardrobe_category(
    category: str = "",
    garment_type: str = "",
    model_line: str = "",
    fit_cut: str = "",
    notes: str = "",
    brand: str = "",
    material: str = "",
) -> str:
    primary = re.sub(r"\s+", " ", f"{garment_type or ''}".strip().lower())
    # Stored category is deliberately excluded from semantic evidence.
    # Otherwise one bad classification can reinforce itself on every reload.
    support = re.sub(
        r"\s+", " ",
        f"{model_line or ''} {fit_cut or ''} {notes or ''} {brand or ''} {material or ''}".strip().lower()
    )
    raw = f"{primary} {support}".strip()

    if is_womenswear():
        women_rules = [
            ("Dresses", ["dress","dresses","gown","maxi dress","midi dress","mini dress","shirt dress","wrap dress"]),
            ("Skirts", ["skirt","skirts","midi skirt","maxi skirt","mini skirt"]),
            ("Jumpsuits & Playsuits", ["jumpsuit","jumpsuits","playsuit","playsuits","romper","rompers"]),
            ("Blazers & Tailoring", ["blazer","blazers","suit jacket","tailored jacket","waistcoat","tailored vest"]),
            ("Coats", ["coat","coats","overcoat","trench","parka","raincoat","mac","pea coat","puffer coat"]),
            ("Jackets", ["jacket","jackets","bomber","denim jacket","leather jacket","suede jacket","gilet","puffer jacket"]),
            ("Knitwear", ["knitwear","jumper","sweater","cardigan","cashmere","merino","roll neck","turtleneck","pullover"]),
            ("Sweatshirts & Hoodies", ["sweatshirt","hoodie","hooded sweatshirt","quarter zip sweatshirt"]),
            ("Blouses & Shirts", ["blouse","blouses","shirt","shirts","button-down","button down"]),
            ("Tops & T-Shirts", ["top","tops","t-shirt","t shirt","tee","tees","camisole","cami","vest top","tank top","bodysuit"]),
            ("Trousers & Jeans", ["trouser","trousers","jean","jeans","chino","chinos","cargo pants","wide-leg","wide leg","legging","leggings"]),
            ("Shorts", ["shorts","cycling shorts"]),
            ("Activewear", ["sports bra","gym top","gym leggings","running tights","activewear","yoga pants","tennis skirt"]),
            ("Footwear", ["shoe","shoes","trainer","trainers","sneaker","sneakers","loafer","loafers","boot","boots","heel","heels","pump","pumps","sandal","sandals","flat","flats","espadrille"]),
            ("Bags", ["handbag","handbags","bag","bags","tote","crossbody","clutch","shoulder bag","bucket bag","satchel"]),
            ("Jewellery", ["jewellery","jewelry","necklace","pendant","bracelet","bangle","earring","earrings","ring","rings","brooch"]),
            ("Accessories", ["belt","belts","hat","hats","cap","caps","beanie","scarf","scarves","glove","gloves","watch","watches","sunglasses","hair accessory","hairband"]),
        ]
        for cat,terms in women_rules:
            if any(term in primary for term in terms):
                return cat
        for cat,terms in women_rules:
            if any(term in raw for term in terms):
                return cat
        legacy=(category or "").strip().casefold()
        for canonical in WOMENSWEAR_CATEGORY_ORDER:
            if legacy==canonical.casefold():
                return canonical
        return "Other"

    footwear = ["footwear","shoe","shoes","sneaker","sneakers","trainer","trainers","loafer","loafers","boot","boots","derby","derbies","brogue","brogues","oxford shoe","monk strap","espadrille","slipper"]
    shorts = ["shorts","swim short","swim shorts"]
    suit_components = ["suit","two-piece suit","three-piece suit","2 piece suit","3 piece suit","suit jacket","suit trousers","suit pants","matching suit trousers","waistcoat","waistcoats"]
    trousers = ["trouser","trousers","chino","chinos","jean","jeans","jogger","joggers","cargo trouser","cargo pants","pants"]
    overshirts = ["overshirt","overshirts","shirt jacket","shirt-jacket","shirtjacket","shacket","shackets"]
    tailoring = ["blazer","blazers","sport coat","sports coat","sports jacket","suit jacket","dinner jacket","tuxedo jacket","tailored jacket","waistcoat","waistcoats","suit"]
    coats = ["overcoat","topcoat","trench coat","trenchcoat","raincoat","rain coat","mac","mac coat","parka","car coat","pea coat","peacoat","duffle coat","duffel coat","greatcoat","winter coat","coat","coats"]
    jackets = ["jacket","jackets","bomber","harrington","field jacket","chore jacket","denim jacket","trucker","trucker jacket","sherpa","sherpa jacket","wax jacket","waxed jacket","technical jacket","rain jacket","shell jacket","leather jacket","suede jacket","gilet","gilets","puffer jacket","quilted jacket","windbreaker"]
    sweatshirts = ["sweatshirt","sweatshirts","sweat shirt","crew-neck sweatshirt","crew neck sweatshirt","quarter-zip sweatshirt","quarter zip sweatshirt","hoodie","hoodies","hooded sweatshirt"]
    shirts = ["shirt","shirts","oxford shirt","dress shirt","casual shirt","linen shirt","utility shirt","work shirt"]
    accessories = ["accessory","accessories","tie","ties","belt","belts","hat","hats","cap","caps","beanie","scarf","scarves","glove","gloves","bag","bags","watch","watches"]

    if any(w in primary for w in footwear): return "Footwear"
    if any(w in primary for w in shorts): return "Shorts"

    # Keep complete suits and explicitly identified matching suit components together
    # in the visible tailoring section. garment_type/model_line still tells the stylist
    # whether it is the jacket, trousers, waistcoat or a complete suit.
    if any(w in primary for w in suit_components): return "Blazers & Tailoring"

    if any(w in primary for w in trousers): return "Trousers"
    if any(w in primary for w in overshirts): return "Overshirts & Shirt Jackets"
    if any(w in primary for w in tailoring): return "Blazers & Tailoring"
    if any(w in primary for w in coats): return "Jackets & Coats"
    if any(w in primary for w in sweatshirts): return "Sweatshirts & Hoodies"

    # Retailer naming is not always the same as wardrobe function.
    short_sleeve = any(x in raw for x in ["short sleeve","short-sleeve"])
    long_sleeve = any(x in raw for x in ["long sleeve","long-sleeve"])
    is_polo = "polo" in raw
    is_rugby = any(x in raw for x in ["rugby shirt","rugby top","rugby jersey"])
    knit_signal = any(x in raw for x in [
        "knit","knitted","merino","wool","cashmere","fine gauge","fine-gauge",
        "sweater","jumper","pullover"
    ])

    # A short-sleeve knitted polo still functions as a polo.
    if is_polo and short_sleeve:
        return "Polos & T-Shirts"

    # Long-sleeve knitted polos/pullovers function as lightweight knitwear.
    if is_polo and long_sleeve and knit_signal:
        return "Knitwear"

    # Rugby shirts are pullover layering pieces in this wardrobe.
    if is_rugby:
        return "Knitwear"

    # Some retailers use "T-shirt" for a fine/lightweight pullover.
    long_sleeve_crew_tee = (
        long_sleeve
        and any(x in raw for x in ["crew neck","crew-neck"])
        and any(x in raw for x in ["t-shirt","t shirt","tee"])
    )
    if long_sleeve_crew_tee and (
        knit_signal
        or ("belstaff" in raw and any(x in raw for x in ["crew neck","crew-neck"]))
    ):
        return "Knitwear"

    knitwear_terms = [
        "knitwear","jumper","jumpers","sweater","sweaters","cardigan","cardigans",
        "quarter zip","half zip","roll neck","turtleneck","pullover"
    ]
    if any(w in primary for w in knitwear_terms):
        return "Knitwear"
    if "knit" in primary and not is_polo:
        return "Knitwear"

    if ("utility shirt" in primary or "work shirt" in primary) and "jacket" not in primary:
        return "Shirts"

    generic_jacket = ("jacket" in primary or primary in ("", "outerwear"))
    tailoring_signals = [
        "notch lapel","notched lapel","peak lapel","shawl lapel",
        "single-breasted","single breasted","double-breasted","double breasted",
        "two-button","two button","three-button","three button",
        "suit jacket","tailored jacket","blazer"
    ]
    if generic_jacket and any(s in support for s in tailoring_signals):
        return "Blazers & Tailoring"

    if any(w in primary for w in jackets): return "Jackets & Coats"

    if is_polo or any(w in primary for w in ["t-shirt","t shirt","tee","tees","tshirt"]):
        return "Polos & T-Shirts"
    if any(w in primary for w in shirts): return "Shirts"
    if any(w in primary for w in accessories): return "Accessories"

    if any(w in raw for w in overshirts): return "Overshirts & Shirt Jackets"
    if any(w in raw for w in tailoring): return "Blazers & Tailoring"
    if any(w in raw for w in coats): return "Jackets & Coats"
    if any(w in raw for w in jackets): return "Jackets & Coats"
    if any(w in raw for w in footwear): return "Footwear"
    if any(w in raw for w in shorts): return "Shorts"
    if any(w in raw for w in trousers): return "Trousers"
    if any(w in raw for w in sweatshirts): return "Sweatshirts & Hoodies"
    if is_rugby: return "Knitwear"
    if is_polo and short_sleeve: return "Polos & T-Shirts"
    if is_polo and long_sleeve and knit_signal: return "Knitwear"
    if knit_signal and not (is_polo and short_sleeve): return "Knitwear"
    if is_polo or any(w in raw for w in ["t-shirt","t shirt","tee","tees","tshirt"]): return "Polos & T-Shirts"
    if any(w in raw for w in shirts): return "Shirts"
    if any(w in raw for w in accessories): return "Accessories"

    legacy = (category or "").strip().casefold()
    if legacy in ("jackets","coats","jackets & outerwear","outerwear","jackets & coats"): return "Jackets & Coats"
    if legacy == "blazers & tailoring": return "Blazers & Tailoring"
    if legacy in ("overshirts & shirt jackets","overshirts"): return "Overshirts & Shirt Jackets"

    for canonical in wardrobe_category_order():
        if legacy == canonical.casefold():
            return canonical
    return "Other"

def normalise_existing_wardrobe_categories():
    con=db()
    rows=con.execute("""
        SELECT id, category, garment_type, model_line, fit_cut, notes, brand, material,
               COALESCE(category_manual,0) AS category_manual
        FROM garments
    """).fetchall()
    changed=0
    for row in rows:
        if int(row["category_manual"] or 0):
            continue
        new_cat=canonical_wardrobe_category(
            row["category"] or "", row["garment_type"] or "",
            row["model_line"] or "", row["fit_cut"] or "", row["notes"] or "",
            row["brand"] or "", row["material"] or ""
        )
        if (row["category"] or "").strip()!=new_cat:
            con.execute("UPDATE garments SET category=? WHERE id=?",(new_cat,row["id"]))
            changed+=1
    if changed:
        con.commit()
    con.close()
    return changed

def init_db():
    con = db()
    con.execute("""
    CREATE TABLE IF NOT EXISTS shopping_shortlist (
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      product_key TEXT UNIQUE,
      name TEXT, brand TEXT, retailer TEXT, price TEXT, url TEXT, image_url TEXT,
      colour TEXT, material TEXT, fit TEXT, size_note TEXT, confidence TEXT,
      why_it_matches TEXT, context_json TEXT,
      created_at TEXT DEFAULT CURRENT_TIMESTAMP
    )
    """)
    con.executescript("""
    CREATE TABLE IF NOT EXISTS profile (
      id INTEGER PRIMARY KEY CHECK(id=1),
      name TEXT, height_cm REAL, chest_cm REAL, waist_cm REAL, hips_cm REAL,
      thigh_cm REAL, inseam_cm REAL, sleeve_cm REAL, neck_cm REAL,
      preferred_fit TEXT, style_notes TEXT, brand_notes TEXT,
      usual_top_size TEXT, usual_bottom_size TEXT, usual_dress_size TEXT,
      usual_shoe_size TEXT, bra_size TEXT
    );
    INSERT OR IGNORE INTO profile(id) VALUES (1);

    CREATE TABLE IF NOT EXISTS garments (
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      image_path TEXT NOT NULL,
      original_image_path TEXT,
      category TEXT, garment_type TEXT, brand TEXT, model_line TEXT,
      labelled_size TEXT, colour TEXT, material TEXT, pattern TEXT,
      fit_cut TEXT, fit_feedback TEXT, season TEXT, formality TEXT,
      notes TEXT, ai_confidence REAL DEFAULT 0,
      enrichment_json TEXT, enrichment_status TEXT DEFAULT '', enrichment_updated_at TEXT,
      created_at TEXT DEFAULT CURRENT_TIMESTAMP
    );

    CREATE TABLE IF NOT EXISTS feedback (
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      outfit_json TEXT, rating TEXT, created_at TEXT DEFAULT CURRENT_TIMESTAMP
    );
    CREATE TABLE IF NOT EXISTS outfit_favourites (
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      label TEXT,
      outfit_json TEXT NOT NULL,
      request_text TEXT,
      weather_context TEXT,
      visual_path TEXT,
      created_at TEXT DEFAULT CURRENT_TIMESTAMP
    );
    CREATE TABLE IF NOT EXISTS model_photos (
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      image_path TEXT NOT NULL,
      label TEXT,
      created_at TEXT DEFAULT CURRENT_TIMESTAMP
    );
    CREATE TABLE IF NOT EXISTS saved_trips (
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      title TEXT NOT NULL,
      destination TEXT DEFAULT '',
      start_date TEXT DEFAULT '',
      end_date TEXT DEFAULT '',
      luggage TEXT DEFAULT '',
      request_json TEXT NOT NULL,
      context_json TEXT NOT NULL,
      plan_json TEXT NOT NULL,
      checklist_json TEXT DEFAULT '{}',
      created_at TEXT DEFAULT CURRENT_TIMESTAMP,
      updated_at TEXT DEFAULT CURRENT_TIMESTAMP
    );
    CREATE TABLE IF NOT EXISTS outfit_wear_events (
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      favourite_id INTEGER NOT NULL,
      worn_at TEXT NOT NULL,
      previous_last_worn_at TEXT,
      created_at TEXT DEFAULT CURRENT_TIMESTAMP
    );
    CREATE TABLE IF NOT EXISTS setup_events (
      event_key TEXT PRIMARY KEY,
      completed_at TEXT NOT NULL
    );
    CREATE INDEX IF NOT EXISTS idx_outfit_wear_events_favourite
      ON outfit_wear_events(favourite_id,id);
    """)
    try:
        con.execute("ALTER TABLE garments ADD COLUMN original_image_path TEXT")
    except sqlite3.OperationalError:
        pass
    for sql in [
        "ALTER TABLE garments ADD COLUMN enrichment_json TEXT",
        "ALTER TABLE garments ADD COLUMN enrichment_status TEXT DEFAULT ''",
        "ALTER TABLE garments ADD COLUMN enrichment_updated_at TEXT",
        "ALTER TABLE garments ADD COLUMN purchase_status TEXT DEFAULT ''",
        "ALTER TABLE garments ADD COLUMN purchase_retailer TEXT DEFAULT ''",
        "ALTER TABLE garments ADD COLUMN purchase_price TEXT DEFAULT ''",
        "ALTER TABLE garments ADD COLUMN category_manual INTEGER NOT NULL DEFAULT 0",
        "ALTER TABLE garments ADD COLUMN purchase_url TEXT DEFAULT ''",
        "ALTER TABLE garments ADD COLUMN purchase_date TEXT DEFAULT ''",
        "ALTER TABLE garments ADD COLUMN fit_review_status TEXT DEFAULT ''",
        "ALTER TABLE garments ADD COLUMN fit_rating INTEGER",
        "ALTER TABLE garments ADD COLUMN fit_chest TEXT DEFAULT ''",
        "ALTER TABLE garments ADD COLUMN fit_waist TEXT DEFAULT ''",
        "ALTER TABLE garments ADD COLUMN fit_length TEXT DEFAULT ''",
        "ALTER TABLE garments ADD COLUMN fit_sleeve TEXT DEFAULT ''",
        "ALTER TABLE garments ADD COLUMN fit_shoulders TEXT DEFAULT ''",
        "ALTER TABLE garments ADD COLUMN fit_hips TEXT DEFAULT ''",
        "ALTER TABLE garments ADD COLUMN fit_notes TEXT DEFAULT ''",
        "ALTER TABLE garments ADD COLUMN fit_reviewed_at TEXT",
        "ALTER TABLE profile ADD COLUMN usual_top_size TEXT DEFAULT ''",
        "ALTER TABLE profile ADD COLUMN usual_bottom_size TEXT DEFAULT ''",
        "ALTER TABLE profile ADD COLUMN usual_dress_size TEXT DEFAULT ''",
        "ALTER TABLE profile ADD COLUMN usual_shoe_size TEXT DEFAULT ''",
        "ALTER TABLE profile ADD COLUMN bra_size TEXT DEFAULT ''",
        "ALTER TABLE profile ADD COLUMN preferred_rise TEXT DEFAULT ''",
        "ALTER TABLE profile ADD COLUMN preferred_hem_length TEXT DEFAULT ''",
        "ALTER TABLE profile ADD COLUMN heel_preference TEXT DEFAULT ''",
        "ALTER TABLE profile ADD COLUMN accessory_notes TEXT DEFAULT ''",
        "ALTER TABLE outfit_favourites ADD COLUMN tags_json TEXT DEFAULT '[]'",
        "ALTER TABLE outfit_favourites ADD COLUMN occasion TEXT DEFAULT ''",
        "ALTER TABLE outfit_favourites ADD COLUMN season TEXT DEFAULT ''",
        "ALTER TABLE outfit_favourites ADD COLUMN notes TEXT DEFAULT ''",
        "ALTER TABLE outfit_favourites ADD COLUMN wore_count INTEGER DEFAULT 0",
        "ALTER TABLE outfit_favourites ADD COLUMN last_worn_at TEXT",
        "ALTER TABLE outfit_favourites ADD COLUMN is_pinned INTEGER DEFAULT 0",
        "ALTER TABLE outfit_favourites ADD COLUMN updated_at TEXT"
    ]:
        try:
            con.execute(sql)
        except sqlite3.OperationalError:
            pass
    con.commit()
    con.close()

init_db()
normalise_existing_wardrobe_categories()


@app.post("/api/transcribe-audio")
async def transcribe_audio(file: UploadFile = File(...)):
    """Transcribe short in-app dictation using OpenAI speech-to-text.

    Audio is written only to a temporary file for the API request and is removed
    immediately afterwards. Nothing is added to the wardrobe/database here.
    """
    if not os.getenv("OPENAI_API_KEY") or OpenAI is None:
        raise HTTPException(400, "OpenAI dictation is not connected.")

    data=await file.read()
    if not data:
        raise HTTPException(400, "No audio was received.")
    if len(data) > 12 * 1024 * 1024:
        raise HTTPException(400, "That dictation recording is too large. Please keep each recording under about 90 seconds.")

    suffix=Path(file.filename or "dictation.webm").suffix.lower()
    if suffix not in {".webm",".mp4",".m4a",".wav",".mp3",".mpeg",".mpga",".ogg"}:
        suffix=".webm"

    tmp_path=None
    last_error=None
    try:
        with tempfile.NamedTemporaryFile(prefix="stylist_dictation_",suffix=suffix,delete=False) as tmp:
            tmp.write(data)
            tmp_path=Path(tmp.name)

        client=OpenAI()
        preferred=(os.getenv("OPENAI_TRANSCRIBE_MODEL") or "gpt-4o-transcribe").strip()
        models=[]
        for model in [preferred, "gpt-4o-mini-transcribe"]:
            if model and model not in models:
                models.append(model)

        for model in models:
            try:
                with tmp_path.open("rb") as audio_file:
                    result=client.audio.transcriptions.create(
                        model=model,
                        file=audio_file,
                        language="en"
                    )
                text=(getattr(result,"text","") or "").strip()
                if text:
                    return {"ok":True,"text":text,"model":model}
                last_error=RuntimeError("The transcription returned no text.")
            except Exception as exc:
                last_error=exc

        raise HTTPException(
            502,
            f"I couldn't transcribe that recording. Please try again. {str(last_error)[:180] if last_error else ''}".strip()
        )
    finally:
        if tmp_path:
            try:
                tmp_path.unlink(missing_ok=True)
            except Exception:
                pass


class RegisterRequest(BaseModel):
    display_name: str
    email: str
    password: str
    invite_code: str = ""
    styling_profile: str = "menswear"

class LoginRequest(BaseModel):
    email: str
    password: str

LOGIN_WINDOW_MINUTES=15
LOGIN_MAX_FAILURES=5

def enforce_login_rate_limit(email: str):
    cutoff=(utc_now()-timedelta(minutes=LOGIN_WINDOW_MINUTES)).isoformat()
    con=auth_db()
    con.execute("DELETE FROM login_attempts WHERE attempted_at < ?",(cutoff,))
    count=con.execute("SELECT COUNT(*) AS n FROM login_attempts WHERE email=? AND attempted_at>=?",
                      (email,cutoff)).fetchone()["n"]
    con.commit(); con.close()
    if count>=LOGIN_MAX_FAILURES:
        raise HTTPException(429,"Too many unsuccessful sign-in attempts. Please wait about 15 minutes and try again.")

def record_login_failure(email: str):
    con=auth_db()
    con.execute("INSERT INTO login_attempts(email,attempted_at) VALUES (?,?)",(email,utc_now().isoformat()))
    con.commit(); con.close()

def clear_login_failures(email: str):
    con=auth_db()
    con.execute("DELETE FROM login_attempts WHERE email=?",(email,))
    con.commit(); con.close()

def initialise_isolated_user_store(user: dict):
    token=CURRENT_USER.set(user)
    try:
        init_db()
        normalise_existing_wardrobe_categories()
    finally:
        CURRENT_USER.reset(token)

@app.get("/api/auth/status")
def auth_status(request: Request):
    con=auth_db()
    user_count=con.execute("SELECT COUNT(*) AS n FROM users").fetchone()["n"]
    con.close()
    user=user_from_session(request.cookies.get(SESSION_COOKIE,""))
    return {
      "authenticated":bool(user),
      "user":user,
      "bootstrap_available":user_count==0,
      "app_name":product_brand_name()
    }

@app.post("/api/auth/register")
def register_account(req: RegisterRequest):
    email=(req.email or "").strip().lower()
    name=(req.display_name or "").strip()
    profile=(req.styling_profile or "menswear").strip().lower()
    if profile not in {"menswear","womenswear"}:
        profile="menswear"
    if not email or "@" not in email:
        raise HTTPException(400,"Enter a valid email address.")
    if not name:
        raise HTTPException(400,"Enter your name.")

    con=auth_db()
    count=con.execute("SELECT COUNT(*) AS n FROM users").fetchone()["n"]
    role="admin" if count==0 else "tester"
    scope="legacy" if count==0 else "isolated"

    if count>0:
        code=(req.invite_code or "").strip().upper()
        invite=con.execute("""
          SELECT * FROM invites
          WHERE code=? AND uses < max_uses AND (expires_at IS NULL OR expires_at>?)
        """,(code,utc_now().isoformat())).fetchone()
        if not invite:
            con.close()
            raise HTTPException(400,"That invite code is invalid or has already been used.")
    else:
        invite=None

    try:
        cur=con.execute("""
          INSERT INTO users(email,display_name,password_hash,role,styling_profile,storage_scope,created_at)
          VALUES (?,?,?,?,?,?,?)
        """,(email,name,password_hash(req.password),role,profile,scope,utc_now().isoformat()))
        uid=cur.lastrowid
        if invite:
            con.execute("UPDATE invites SET uses=uses+1 WHERE id=?",(invite["id"],))
        con.commit()
    except sqlite3.IntegrityError:
        con.close()
        raise HTTPException(400,"An account with that email already exists.")

    row=con.execute("SELECT * FROM users WHERE id=?",(uid,)).fetchone()
    con.close()
    user=public_user(row)
    if scope=="isolated":
        initialise_isolated_user_store(user)

    token,expires=create_session(uid)
    response=JSONResponse({"ok":True,"user":user})
    response.set_cookie(SESSION_COOKIE,token,httponly=True,samesite="lax",
                        secure=os.getenv("COOKIE_SECURE","1")!="0",
                        max_age=SESSION_DAYS*86400,path="/")
    return response

@app.post("/api/auth/login")
def login_account(req: LoginRequest):
    email=(req.email or "").strip().lower()
    enforce_login_rate_limit(email)
    con=auth_db()
    row=con.execute("SELECT * FROM users WHERE email=?",(email,)).fetchone()
    con.close()
    if not row or not password_ok(req.password,row["password_hash"]):
        record_login_failure(email)
        raise HTTPException(401,"Email or password is incorrect.")
    if int(row["active"] if "active" in row.keys() else 1)==0:
        raise HTTPException(403,"This tester account is currently disabled.")
    clear_login_failures(email)
    user=public_user(row)
    token,expires=create_session(user["id"])
    response=JSONResponse({"ok":True,"user":user})
    response.set_cookie(SESSION_COOKIE,token,httponly=True,samesite="lax",
                        secure=os.getenv("COOKIE_SECURE","1")!="0",
                        max_age=SESSION_DAYS*86400,path="/")
    return response

@app.post("/api/auth/logout")
def logout_account(request: Request):
    token=request.cookies.get(SESSION_COOKIE,"")
    if token:
        con=auth_db()
        con.execute("DELETE FROM sessions WHERE token_hash=?",(session_hash(token),))
        con.commit(); con.close()
    response=JSONResponse({"ok":True})
    response.delete_cookie(SESSION_COOKIE,path="/")
    return response


def user_storage_root_for(row):
    d=dict(row)
    return DATA_DIR if d.get("storage_scope")=="legacy" else USERS_DIR/str(d["id"])

def user_store_counts(row):
    path=user_storage_root_for(row)/"stylist.db"
    counts={"wardrobe_items":0,"saved_looks":0,"fit_reviews":0}
    if not path.exists():
        return counts
    try:
        con=sqlite3.connect(path)
        counts["wardrobe_items"]=con.execute("SELECT COUNT(*) FROM garments").fetchone()[0]
        counts["saved_looks"]=con.execute("SELECT COUNT(*) FROM outfit_favourites").fetchone()[0]
        try:
            counts["fit_reviews"]=con.execute("SELECT COUNT(*) FROM garments WHERE fit_review_status='confirmed'").fetchone()[0]
        except Exception:
            pass
        con.close()
    except Exception:
        pass
    return counts

def _dir_usage_bytes(path:Path):
    total=0
    if not path.exists(): return 0
    for p in path.rglob("*"):
        try:
            if p.is_file(): total+=p.stat().st_size
        except Exception: pass
    return total

@app.get("/api/admin/beta-usage")
def admin_beta_usage():
    require_admin()
    now=utc_now()
    day_start=now.replace(hour=0,minute=0,second=0,microsecond=0).isoformat()
    month_start=now.replace(day=1,hour=0,minute=0,second=0,microsecond=0).isoformat()
    con=auth_db()
    users=con.execute("SELECT * FROM users ORDER BY created_at ASC").fetchall()
    totals=con.execute("""SELECT
      COALESCE(SUM(CASE WHEN event_type='image_generation' AND created_at>=? THEN units ELSE 0 END),0) images_today,
      COALESCE(SUM(CASE WHEN event_type='image_generation' AND created_at>=? THEN units ELSE 0 END),0) images_month,
      COALESCE(SUM(CASE WHEN event_type='text_ai' AND created_at>=? THEN 1 ELSE 0 END),0) text_calls_month,
      COALESCE(SUM(CASE WHEN event_type='text_ai' AND created_at>=? THEN input_tokens ELSE 0 END),0) input_tokens_month,
      COALESCE(SUM(CASE WHEN event_type='text_ai' AND created_at>=? THEN output_tokens ELSE 0 END),0) output_tokens_month,
      COALESCE(SUM(CASE WHEN event_type='text_ai' AND created_at>=? THEN estimated_usd ELSE 0 END),0) text_cost_month
      FROM usage_events""",(day_start,month_start,month_start,month_start,month_start,month_start)).fetchone()
    rows=[]
    for row in users:
        uid=int(row["id"])
        usage=con.execute("""SELECT
          COALESCE(SUM(CASE WHEN event_type='image_generation' AND created_at>=? THEN units ELSE 0 END),0) images_today,
          COALESCE(SUM(CASE WHEN event_type='image_generation' AND created_at>=? THEN units ELSE 0 END),0) images_month,
          COALESCE(SUM(CASE WHEN event_type='text_ai' AND created_at>=? THEN 1 ELSE 0 END),0) text_calls_month,
          COALESCE(SUM(CASE WHEN event_type='text_ai' AND created_at>=? THEN estimated_usd ELSE 0 END),0) text_cost_month
          FROM usage_events WHERE user_id=?""",(day_start,month_start,month_start,month_start,uid)).fetchone()
        rows.append({
          "id":uid,"display_name":row["display_name"],"email":row["email"],"role":row["role"],
          "active":int(row["active"]),"images_today":int(usage["images_today"] or 0),
          "images_month":int(usage["images_month"] or 0),"text_calls_month":int(usage["text_calls_month"] or 0),
          "text_cost_month":round(float(usage["text_cost_month"] or 0),4),
          "storage_bytes":_dir_usage_bytes(user_storage_root_for(row))
        })
    con.close()

    ai_connected=bool(os.getenv("OPENAI_API_KEY"))
    disk_ok=False
    try:
        probe=DATA_DIR/".beta_readiness_probe"; probe.write_text("ok"); probe.unlink(); disk_ok=True
    except Exception:
        disk_ok=False
    checks=[
      {"label":"AI connection","ok":ai_connected,"detail":"OpenAI API key configured" if ai_connected else "OpenAI API key missing"},
      {"label":"Persistent storage","ok":disk_ok,"detail":"Storage is writable" if disk_ok else "Storage write check failed"},
      {"label":"Data backup","ok":True,"detail":"Per-account export available"},
      {"label":"Tester isolation","ok":True,"detail":"Tester accounts use isolated stores"},
      {"label":"Usage safeguards","ok":True,"detail":f"{BETA_DAILY_IMAGE_LIMIT}/day · {BETA_MONTHLY_IMAGE_LIMIT}/month per tester"},
    ]
    return {
      "ready_for_small_beta":all(c["ok"] for c in checks),
      "recommended_first_wave":"5–8 testers",
      "checks":checks,
      "limits":{"daily_images":BETA_DAILY_IMAGE_LIMIT,"monthly_images":BETA_MONTHLY_IMAGE_LIMIT},
      "totals":{
        "images_today":int(totals["images_today"] or 0),
        "images_month":int(totals["images_month"] or 0),
        "text_calls_month":int(totals["text_calls_month"] or 0),
        "input_tokens_month":int(totals["input_tokens_month"] or 0),
        "output_tokens_month":int(totals["output_tokens_month"] or 0),
        "estimated_text_cost_month":round(float(totals["text_cost_month"] or 0),4),
        "storage_bytes":sum(r["storage_bytes"] for r in rows)
      },
      "users":rows,
      "pricing_note":"Text cost is estimated from measured token usage. Image generations are counted separately because reference-image inputs vary. Your OpenAI invoice is the source of truth."
    }

@app.get("/api/admin/users")
def admin_users():
    require_admin()
    con=auth_db()
    rows=con.execute("""
      SELECT u.*,
             (SELECT MAX(created_at) FROM sessions s WHERE s.user_id=u.id) AS last_session_at,
             (SELECT COUNT(*) FROM tester_feedback f WHERE f.user_id=u.id) AS feedback_count
      FROM users u ORDER BY u.created_at ASC
    """).fetchall()
    con.close()
    result=[]
    for row in rows:
        u=public_user(row)
        u["last_session_at"]=row["last_session_at"]
        u["feedback_count"]=row["feedback_count"]
        u.update(user_store_counts(row))
        result.append(u)
    return result

@app.post("/api/admin/users/{uid}/disable")
def disable_user(uid:int):
    admin=require_admin()
    if uid==admin["id"]:
        raise HTTPException(400,"You cannot disable your own owner account.")
    con=auth_db()
    row=con.execute("SELECT role FROM users WHERE id=?",(uid,)).fetchone()
    if not row:
        con.close(); raise HTTPException(404,"User not found.")
    if row["role"]=="admin":
        con.close(); raise HTTPException(400,"Admin accounts cannot be disabled here.")
    con.execute("UPDATE users SET active=0 WHERE id=?",(uid,))
    con.execute("DELETE FROM sessions WHERE user_id=?",(uid,))
    con.commit(); con.close()
    return {"ok":True}

@app.post("/api/admin/users/{uid}/enable")
def enable_user(uid:int):
    require_admin()
    con=auth_db()
    row=con.execute("SELECT id FROM users WHERE id=?",(uid,)).fetchone()
    if not row:
        con.close(); raise HTTPException(404,"User not found.")
    con.execute("UPDATE users SET active=1 WHERE id=?",(uid,))
    con.commit(); con.close()
    return {"ok":True}

@app.delete("/api/account/invites/{code}")
def revoke_invite(code:str):
    u=require_admin()
    con=auth_db()
    row=con.execute("SELECT id,uses FROM invites WHERE code=? AND created_by=?",(code,u["id"])).fetchone()
    if not row:
        con.close(); raise HTTPException(404,"Invite not found.")
    if row["uses"]>0:
        con.close(); raise HTTPException(400,"Used invites are kept for history.")
    con.execute("DELETE FROM invites WHERE id=?",(row["id"],))
    con.commit(); con.close()
    return {"ok":True}

class TesterFeedbackRequest(BaseModel):
    rating: Optional[int]=None
    category: str="general"
    message: str

@app.post("/api/tester-feedback")
def tester_feedback(req: TesterFeedbackRequest):
    u=current_user()
    message=(req.message or "").strip()
    if not message:
        raise HTTPException(400,"Add a short feedback note first.")
    rating=req.rating
    if rating is not None:
        rating=max(1,min(5,int(rating)))
    con=auth_db()
    con.execute("""INSERT INTO tester_feedback(user_id,rating,category,message,created_at)
                   VALUES (?,?,?,?,?)""",
                (u["id"],rating,(req.category or "general")[:50],message,utc_now().isoformat()))
    con.commit(); con.close()
    return {"ok":True}

@app.get("/api/admin/feedback")
def admin_feedback():
    require_admin()
    con=auth_db()
    rows=[dict(r) for r in con.execute("""
      SELECT f.id,f.rating,f.category,f.message,f.created_at,u.display_name,u.email
      FROM tester_feedback f JOIN users u ON u.id=f.user_id
      ORDER BY f.id DESC LIMIT 100
    """).fetchall()]
    con.close()
    return rows

@app.get("/api/admin/system-status")
def admin_system_status():
    require_admin()
    root=active_user_root()
    writable=False
    probe=root/".ghd_write_test"
    try:
        probe.write_text("ok")
        writable=True
        probe.unlink(missing_ok=True)
    except Exception:
        writable=False

    con=db()
    rows=[dict(r) for r in con.execute("SELECT id,image_path,original_image_path FROM garments").fetchall()]
    con.close()
    missing=0
    for row in rows:
        paths=[row.get("image_path"),row.get("original_image_path")]
        if paths and not any(p and resolve_saved_image_path(p).exists() for p in paths):
            missing+=1
    return {
      "storage_writable":writable,
      "wardrobe_items":len(rows),
      "missing_image_items":missing,
      "ai_enabled":bool(os.getenv("OPENAI_API_KEY")) and OpenAI is not None,
      "photo_cleanup_enabled":bool(os.getenv("REMOVE_BG_API_KEY"))
    }

@app.get("/api/account")
def account_details():
    u=current_user()
    return {"user":u}

HOME_FEATURE_KEYS=[
    "wardrobe","shopping","packing","weekplanner","fitintel","profile",
    "buildlook","productlook","quickwardrobe","shortlist","intelligence","account","savedlooks"
]

def _normalise_home_order(raw):
    values=[]
    if isinstance(raw,str):
        try: raw=json.loads(raw)
        except Exception: raw=[]
    if isinstance(raw,list):
        for value in raw:
            key=str(value or "").strip()
            if key in HOME_FEATURE_KEYS and key not in values:
                values.append(key)
    for key in HOME_FEATURE_KEYS:
        if key not in values:
            values.append(key)
    return values

class HomeOrderRequest(BaseModel):
    order: list[str]

@app.get("/api/account/home-order")
def get_home_order():
    uid=current_user_id()
    con=auth_db()
    row=con.execute("SELECT home_order_json FROM users WHERE id=?",(uid,)).fetchone()
    con.close()
    order=_normalise_home_order(row["home_order_json"] if row else None)
    return {"order":order,"default_order":HOME_FEATURE_KEYS}

@app.put("/api/account/home-order")
def save_home_order(req: HomeOrderRequest):
    uid=current_user_id()
    order=_normalise_home_order(req.order)
    con=auth_db()
    con.execute("UPDATE users SET home_order_json=? WHERE id=?",(json.dumps(order),uid))
    con.commit();con.close()
    return {"ok":True,"order":order,"default_order":HOME_FEATURE_KEYS}

@app.delete("/api/account/home-order")
def reset_home_order():
    uid=current_user_id()
    con=auth_db()
    con.execute("UPDATE users SET home_order_json=NULL WHERE id=?",(uid,))
    con.commit();con.close()
    return {"ok":True,"order":HOME_FEATURE_KEYS,"default_order":HOME_FEATURE_KEYS}


def _safe_export_name(value:str) -> str:
    cleaned=re.sub(r"[^A-Za-z0-9_-]+","-",str(value or "").strip()).strip("-")
    return cleaned[:60] or "account"

def _table_exists(con, name:str) -> bool:
    row=con.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",(name,)).fetchone()
    return bool(row)

def _export_table(con, name:str):
    if not _table_exists(con,name):
        return []
    return [dict(r) for r in con.execute(f'SELECT * FROM "{name}"').fetchall()]

def account_data_manifest():
    """Return only the signed-in user's own portable data. Never includes credentials."""
    u=current_user() or {}
    con=db()
    tables=[
      "profile","garments","feedback","outfit_favourites","outfit_wear_events",
      "model_photos","saved_trips","shopping_shortlist","setup_events"
    ]
    payload={
      "export_format":"get-dressed-portable-backup-v1",
      "exported_at":utc_now().isoformat(),
      "app_version":"7.10.2",
      "account":{
        "id":u.get("id"),
        "email":u.get("email"),
        "display_name":u.get("display_name"),
        "styling_profile":u.get("styling_profile"),
        "created_at":u.get("created_at"),
      },
      "tables":{}
    }
    for table in tables:
        payload["tables"][table]=_export_table(con,table)
    con.close()
    return payload

def account_data_summary():
    con=db()
    def count(name):
        return int(con.execute(f'SELECT COUNT(*) FROM "{name}"').fetchone()[0] or 0) if _table_exists(con,name) else 0
    summary={
      "wardrobe_items":count("garments"),
      "saved_looks":count("outfit_favourites"),
      "fit_reviews":int(con.execute(
        "SELECT COUNT(*) FROM garments WHERE COALESCE(fit_review_status,'')='confirmed'"
      ).fetchone()[0] or 0) if _table_exists(con,"garments") else 0,
      "saved_trips":count("saved_trips"),
      "model_photos":count("model_photos"),
      "shortlist_items":count("shopping_shortlist"),
    }
    con.close()
    root=active_user_root()
    media_bytes=0
    media_files=0
    for folder_name in ["uploads","cleaned","generated","model_photos"]:
        folder=root/folder_name
        if not folder.exists(): continue
        for p in folder.rglob("*"):
            try:
                if p.is_file():
                    media_files+=1
                    media_bytes+=p.stat().st_size
            except Exception:
                pass
    summary["media_files"]=media_files
    summary["media_bytes"]=media_bytes
    return summary

@app.get("/api/account/data-summary")
def get_account_data_summary():
    return account_data_summary()

@app.get("/api/account/export")
def export_account_data(background_tasks: BackgroundTasks):
    """Create an on-demand, read-only backup ZIP for the signed-in account."""
    u=current_user() or {}
    root=active_user_root()
    tmp_dir=Path(tempfile.mkdtemp(prefix="get_dressed_export_"))
    safe_name=_safe_export_name(u.get("display_name") or u.get("email") or "account")
    stamp=utc_now().strftime("%Y-%m-%d")
    out=tmp_dir/f"get-dressed-{safe_name}-{stamp}.zip"

    try:
        manifest=account_data_manifest()
        summary=account_data_summary()

        # Produce a consistent SQLite snapshot using SQLite's backup API rather than
        # copying a live WAL-backed database file.
        snapshot=tmp_dir/"stylist.db"
        source=db()
        target=sqlite3.connect(snapshot)
        try:
            source.backup(target)
        finally:
            target.close()
            source.close()

        with zipfile.ZipFile(out,"w",zipfile.ZIP_DEFLATED,allowZip64=True) as z:
            z.writestr(
              "README.txt",
              "Get Dressed account backup\n"
              f"Exported: {manifest['exported_at']}\n"
              "Contains only this signed-in account's wardrobe/profile/style data and media.\n"
              "It does not contain passwords, session tokens, invite codes, or other users' data.\n"
              "portable-data.json is the migration-friendly readable snapshot.\n"
              "stylist.db is a consistent SQLite snapshot for technical recovery.\n"
            )
            z.writestr("portable-data.json",json.dumps(manifest,ensure_ascii=False,indent=2,default=str))
            z.writestr("summary.json",json.dumps(summary,ensure_ascii=False,indent=2,default=str))
            z.write(snapshot,"stylist.db")

            for folder_name in ["uploads","cleaned","generated","model_photos"]:
                folder=root/folder_name
                if not folder.exists():
                    continue
                for p in folder.rglob("*"):
                    if not p.is_file():
                        continue
                    try:
                        resolved=p.resolve()
                        if not resolved.is_relative_to(root.resolve()):
                            continue
                        arc=Path("media")/folder_name/p.relative_to(folder)
                        z.write(p,arc.as_posix())
                    except Exception:
                        continue
    except Exception:
        shutil.rmtree(tmp_dir,ignore_errors=True)
        raise

    background_tasks.add_task(shutil.rmtree,tmp_dir,True)
    return FileResponse(
      out,
      filename=out.name,
      media_type="application/zip",
      headers={
        "Cache-Control":"no-store, private",
        "X-Content-Type-Options":"nosniff"
      }
    )



class ChangePasswordRequest(BaseModel):
    current_password: str
    new_password: str

@app.get("/api/account/security")
def account_security(request: Request):
    u=current_user()
    token=request.cookies.get(SESSION_COOKIE,"")
    current_hash=session_hash(token) if token else ""
    now=utc_now().isoformat()
    con=auth_db()
    con.execute("DELETE FROM sessions WHERE expires_at < ?",(now,))
    rows=con.execute(
      "SELECT token_hash,created_at,expires_at FROM sessions WHERE user_id=? ORDER BY created_at DESC",
      (u["id"],)
    ).fetchall()
    con.commit();con.close()
    return {
      "active_sessions":len(rows),
      "other_sessions":sum(1 for r in rows if r["token_hash"]!=current_hash),
      "current_session_created_at":next((r["created_at"] for r in rows if r["token_hash"]==current_hash),None),
      "session_expires_at":next((r["expires_at"] for r in rows if r["token_hash"]==current_hash),None),
    }

@app.post("/api/account/security/change-password")
def change_account_password(req: ChangePasswordRequest, request: Request):
    u=current_user()
    current=(req.current_password or "")
    new=(req.new_password or "")
    if len(new)<8:
        raise HTTPException(400,"Use a new password of at least 8 characters.")
    if current==new:
        raise HTTPException(400,"Choose a different new password.")

    con=auth_db()
    row=con.execute("SELECT password_hash FROM users WHERE id=?",(u["id"],)).fetchone()
    if not row or not password_ok(current,row["password_hash"]):
        con.close()
        raise HTTPException(401,"Your current password is incorrect.")

    new_hash=password_hash(new)
    current_token=request.cookies.get(SESSION_COOKIE,"")
    keep_hash=session_hash(current_token) if current_token else ""
    con.execute("UPDATE users SET password_hash=? WHERE id=?",(new_hash,u["id"]))
    if keep_hash:
        con.execute("DELETE FROM sessions WHERE user_id=? AND token_hash<>?",(u["id"],keep_hash))
    else:
        con.execute("DELETE FROM sessions WHERE user_id=?",(u["id"],))
    con.commit();con.close()
    return {"ok":True,"other_sessions_revoked":True}

@app.post("/api/account/security/sign-out-others")
def sign_out_other_sessions(request: Request):
    u=current_user()
    token=request.cookies.get(SESSION_COOKIE,"")
    if not token:
        raise HTTPException(401,"Please sign in again.")
    keep_hash=session_hash(token)
    con=auth_db()
    before=int(con.execute(
      "SELECT COUNT(*) FROM sessions WHERE user_id=? AND token_hash<>?",
      (u["id"],keep_hash)
    ).fetchone()[0] or 0)
    con.execute("DELETE FROM sessions WHERE user_id=? AND token_hash<>?",(u["id"],keep_hash))
    con.commit();con.close()
    return {"ok":True,"revoked_sessions":before}


@app.post("/api/account/invites")
def create_invite():
    u=require_admin()
    code=secrets.token_hex(4).upper()
    expires=utc_now()+timedelta(days=14)
    con=auth_db()
    con.execute("""INSERT INTO invites(code,created_by,max_uses,uses,expires_at,created_at)
                   VALUES (?,?,1,0,?,?)""",
                (code,u["id"],expires.isoformat(),utc_now().isoformat()))
    con.commit(); con.close()
    return {"code":code,"expires_at":expires.isoformat()}

@app.get("/api/account/invites")
def list_invites():
    u=require_admin()
    con=auth_db()
    rows=[dict(r) for r in con.execute(
      "SELECT code,max_uses,uses,expires_at,created_at FROM invites WHERE created_by=? ORDER BY id DESC LIMIT 20",
      (u["id"],)
    ).fetchall()]
    con.close()
    return rows

@app.get("/")
def home():
    return FileResponse(ROOT/"static"/"index.html")

@app.get("/api/health")
def health():
    return {
      "ok": True,
      "ai_enabled": bool(os.getenv("OPENAI_API_KEY")) and OpenAI is not None,
      "photo_cleanup_enabled": bool(os.getenv("REMOVE_BG_API_KEY"))
    }

@app.get("/api/bootstrap")
def app_bootstrap():
    con=db()
    profile=con.execute("SELECT name FROM profile WHERE id=1").fetchone()
    wardrobe_count=con.execute("SELECT COUNT(*) AS n FROM garments").fetchone()["n"]
    saved_count=con.execute("SELECT COUNT(*) AS n FROM outfit_favourites").fetchone()["n"]
    con.close()
    return {
      "name":(profile["name"] if profile else "") or "",
      "wardrobe_count":wardrobe_count,
      "saved_look_count":saved_count
    }


SETUP_STEPS = [
    ("profile","Personalise your profile","profile"),
    ("model_photo","Add a model photo","profile"),
    ("wardrobe","Build a useful wardrobe","quickwardrobe"),
    ("fit_review","Teach me what fits","fitintel"),
    ("stylist","Try your personal stylist","stylistv4"),
    ("saved_look","Save a look you like","savedlooks"),
]

@app.get("/api/setup-progress")
def setup_progress():
    con=db()
    profile=dict(con.execute("SELECT * FROM profile WHERE id=1").fetchone() or {})
    wardrobe_count=int(con.execute("SELECT COUNT(*) FROM garments").fetchone()[0] or 0)
    model_photo_count=int(con.execute("SELECT COUNT(*) FROM model_photos").fetchone()[0] or 0)
    saved_count=int(con.execute("SELECT COUNT(*) FROM outfit_favourites").fetchone()[0] or 0)
    feedback_count=int(con.execute("SELECT COUNT(*) FROM feedback").fetchone()[0] or 0)
    fit_review_count=int(con.execute(
        "SELECT COUNT(*) FROM garments WHERE COALESCE(fit_review_status,'')='confirmed' OR fit_reviewed_at IS NOT NULL"
    ).fetchone()[0] or 0)
    quick_fit_count=int(con.execute("""
        SELECT COUNT(*) FROM garments
        WHERE TRIM(COALESCE(fit_feedback,''))<>''
          AND LOWER(TRIM(COALESCE(fit_feedback,''))) NOT IN ('unknown','fit unknown')
    """).fetchone()[0] or 0)
    event_rows=con.execute("SELECT event_key,completed_at FROM setup_events").fetchall()
    events={r["event_key"]:r["completed_at"] for r in event_rows}
    con.close()

    profile_signals=[
      profile.get("height_cm"),profile.get("chest_cm"),profile.get("waist_cm"),
      profile.get("usual_top_size"),profile.get("usual_bottom_size"),profile.get("usual_dress_size"),
      profile.get("usual_shoe_size"),profile.get("preferred_fit"),profile.get("style_notes"),profile.get("brand_notes")
    ]
    profile_complete=bool((profile.get("name") or "").strip()) and sum(1 for x in profile_signals if x not in (None,""))>=1
    stylist_complete=bool(events.get("stylist_result") or feedback_count>0 or saved_count>0)

    state={
      "profile":profile_complete,
      "model_photo":model_photo_count>=1,
      "wardrobe":wardrobe_count>=6,
      "fit_review":(fit_review_count>=1 or quick_fit_count>=1),
      "stylist":stylist_complete,
      "saved_look":saved_count>=1,
    }
    detail={
      "profile": "Measurements, sizing or preferences added" if profile_complete else "Add your name plus at least one fit, size or style preference.",
      "model_photo": f"{model_photo_count} model photo{'s' if model_photo_count!=1 else ''} saved" if model_photo_count else "Add a clear photo so outfit visuals can look like you.",
      "wardrobe": f"{wardrobe_count} wardrobe items saved" if wardrobe_count else "Add your everyday favourites first.",
      "fit_review": (
          f"{quick_fit_count} garment{'s' if quick_fit_count!=1 else ''} with fit feedback"
          if quick_fit_count else
          (f"{fit_review_count} detailed fit review{'s' if fit_review_count!=1 else ''}" if fit_review_count else "Mark how at least one garment fits.")
      ),
      "stylist": "Personal stylist used" if stylist_complete else "Ask the stylist for your first real outfit.",
      "saved_look": f"{saved_count} saved look{'s' if saved_count!=1 else ''}" if saved_count else "Save one outfit that feels right.",
    }

    steps=[]
    for key,label,screen in SETUP_STEPS:
        steps.append({"key":key,"label":label,"screen":screen,"complete":bool(state[key]),"detail":detail[key]})
    completed=sum(1 for x in steps if x["complete"])
    next_step=next((x for x in steps if not x["complete"]),None)
    return {
      "completed":completed,
      "total":len(steps),
      "percent":round((completed/len(steps))*100) if steps else 100,
      "is_complete":completed==len(steps),
      "steps":steps,
      "next_step":next_step,
      "wardrobe_target":6,
    }

class SetupEventRequest(BaseModel):
    event_key: str

@app.post("/api/setup-progress/event")
def record_setup_event(req: SetupEventRequest):
    allowed={"stylist_result"}
    key=(req.event_key or "").strip()
    if key not in allowed:
        raise HTTPException(400,"Unknown setup event.")
    con=db()
    con.execute("""
      INSERT INTO setup_events(event_key,completed_at) VALUES (?,?)
      ON CONFLICT(event_key) DO UPDATE SET completed_at=excluded.completed_at
    """,(key,utc_now().isoformat()))
    con.commit();con.close()
    return {"ok":True,"event_key":key}


@app.get("/api/profile")
def get_profile():
    con = db()
    row = con.execute("SELECT * FROM profile WHERE id=1").fetchone()
    con.close()
    return dict(row)

class Profile(BaseModel):
    name: Optional[str]=""
    height_cm: Optional[float]=None
    chest_cm: Optional[float]=None
    waist_cm: Optional[float]=None
    hips_cm: Optional[float]=None
    thigh_cm: Optional[float]=None
    inseam_cm: Optional[float]=None
    sleeve_cm: Optional[float]=None
    neck_cm: Optional[float]=None
    preferred_fit: Optional[str]=""
    style_notes: Optional[str]=""
    brand_notes: Optional[str]=""
    usual_top_size: Optional[str]=""
    usual_bottom_size: Optional[str]=""
    usual_dress_size: Optional[str]=""
    usual_shoe_size: Optional[str]=""
    bra_size: Optional[str]=""
    preferred_rise: Optional[str]=""
    preferred_hem_length: Optional[str]=""
    heel_preference: Optional[str]=""
    accessory_notes: Optional[str]=""

@app.put("/api/profile")
def save_profile(p: Profile):
    con = db()
    con.execute("""UPDATE profile SET name=?,height_cm=?,chest_cm=?,waist_cm=?,hips_cm=?,thigh_cm=?,
        inseam_cm=?,sleeve_cm=?,neck_cm=?,preferred_fit=?,style_notes=?,brand_notes=?,
        usual_top_size=?,usual_bottom_size=?,usual_dress_size=?,usual_shoe_size=?,bra_size=?,
        preferred_rise=?,preferred_hem_length=?,heel_preference=?,accessory_notes=? WHERE id=1""",
        (p.name,p.height_cm,p.chest_cm,p.waist_cm,p.hips_cm,p.thigh_cm,p.inseam_cm,p.sleeve_cm,p.neck_cm,
         p.preferred_fit,p.style_notes,p.brand_notes,p.usual_top_size,p.usual_bottom_size,p.usual_dress_size,
         p.usual_shoe_size,p.bra_size,p.preferred_rise,p.preferred_hem_length,p.heel_preference,p.accessory_notes))
    con.commit(); con.close()
    return {"ok": True}


def saved_image_is_usable(rel_path: str) -> bool:
    if not rel_path:
        return False
    try:
        path=resolve_saved_image_path(rel_path)
        if not path.exists() or not path.is_file() or path.stat().st_size < 512:
            return False
        with Image.open(path) as im:
            im.verify()
        # A second lightweight pass catches a completely blank generated catalogue
        # canvas while remaining conservative for pale garments.
        with Image.open(path) as im:
            thumb=ImageOps.exif_transpose(im).convert("L")
            thumb.thumbnail((64,64))
            extrema=thumb.getextrema()
            if extrema and (extrema[1]-extrema[0]) < 4:
                return False
        return True
    except Exception:
        return False


def best_garment_image(row: dict) -> tuple[str,str]:
    display=row.get("image_path") or ""
    original=row.get("original_image_path") or ""
    if saved_image_is_usable(display):
        return display, "display"
    if original and original != display and saved_image_is_usable(original):
        return original, "original"
    if original and saved_image_is_usable(original):
        return original, "original"
    return "", "missing"


@app.get("/api/garments/{gid}/image")
def garment_image(gid: int):
    con=db()
    row=con.execute("SELECT id,image_path,original_image_path FROM garments WHERE id=?",(gid,)).fetchone()
    if not row:
        con.close()
        raise HTTPException(404,"Garment not found.")
    data=dict(row)
    rel,source=best_garment_image(data)
    if not rel:
        con.close()
        raise HTTPException(404,"This garment's saved photo file is unavailable.")
    if source=="original" and rel != (data.get("image_path") or ""):
        con.execute("UPDATE garments SET image_path=? WHERE id=?",(rel,gid))
        con.commit()
    con.close()
    path=resolve_saved_image_path(rel)
    return FileResponse(path, headers={"Cache-Control":"no-store, max-age=0"})


@app.get("/api/garments")
def garments():
    """
    Fast wardrobe index.

    The previous implementation opened/verified every image file with Pillow on
    every wardrobe request. That becomes expensive as the wardrobe grows.
    Here we only do a cheap filesystem existence/size check. Full image
    validation still happens when an individual garment/detail image is used.
    """
    con=db()
    rows=[dict(r) for r in con.execute("SELECT * FROM garments ORDER BY id DESC").fetchall()]
    changed=False

    for row in rows:
        # Keep the visible wardrobe taxonomy canonical as the app evolves.
        # This is a safe metadata-only migration: no garment/image is deleted.
        if not int(row.get("category_manual") or 0):
            canonical_category=canonical_wardrobe_category(
                category=row.get("category") or "",
                garment_type=row.get("garment_type") or "",
                model_line=row.get("model_line") or "",
                fit_cut=row.get("fit_cut") or "",
                notes=row.get("notes") or "",
                brand=row.get("brand") or "",
                material=row.get("material") or "",
            )
            if canonical_category and canonical_category != (row.get("category") or ""):
                row["category"]=canonical_category
                con.execute("UPDATE garments SET category=? WHERE id=?",(canonical_category,row["id"]))
                changed=True

        display=row.get("image_path") or ""
        original=row.get("original_image_path") or ""

        if not original and display.startswith("/uploads/"):
            p=resolve_saved_image_path(display)
            if p.exists() and p.is_file() and p.stat().st_size>0:
                original=display
                row["original_image_path"]=display
                con.execute("UPDATE garments SET original_image_path=? WHERE id=?",(display,row["id"]))
                changed=True

        def cheap_ok(rel):
            if not rel:return False
            try:
                p=resolve_saved_image_path(rel)
                return p.exists() and p.is_file() and p.stat().st_size>0
            except Exception:
                return False

        if cheap_ok(display):
            row["image_status"]="display"
            row["image_available"]=True
        elif cheap_ok(original):
            row["image_status"]="original"
            row["image_available"]=True
            if original!=display:
                row["image_path"]=original
                con.execute("UPDATE garments SET image_path=? WHERE id=?",(original,row["id"]))
                changed=True
        else:
            row["image_status"]="missing"
            row["image_available"]=False

    if changed: con.commit()
    con.close()
    return rows


@app.get("/api/garments/{gid}/thumbnail")
def garment_thumbnail(gid:int):
    """Return a small cached catalogue thumbnail for grid/list views."""
    con=db()
    row=con.execute("SELECT id,image_path,original_image_path FROM garments WHERE id=?",(gid,)).fetchone()
    con.close()
    if not row:
        raise HTTPException(404,"Garment not found.")

    data=dict(row)
    rel=data.get("image_path") or data.get("original_image_path") or ""
    source=resolve_saved_image_path(rel) if rel else None
    if not source or not source.exists():
        raise HTTPException(404,"Garment photo unavailable.")

    thumbs=active_user_root()/"thumbs"
    thumbs.mkdir(parents=True,exist_ok=True)
    # Include source filename + mtime so replacing a garment photo creates a new cache file.
    stamp=int(source.stat().st_mtime)
    safe_stem=re.sub(r"[^A-Za-z0-9_-]+","_",source.stem)[:48]
    out=thumbs/f"g{gid}_{safe_stem}_{stamp}.jpg"

    if not out.exists():
        try:
            with Image.open(source) as opened:
                im=ImageOps.exif_transpose(opened)
                if im.mode!="RGB":
                    if im.mode in ("RGBA","LA"):
                        bg=Image.new("RGB",im.size,"white")
                        bg.paste(im,mask=im.getchannel("A"))
                        im=bg
                    else:
                        im=im.convert("RGB")
                im.thumbnail((420,520),Image.Resampling.LANCZOS)
                canvas=Image.new("RGB",(420,520),(248,248,247))
                x=(420-im.width)//2
                y=(520-im.height)//2
                canvas.paste(im,(x,y))
                canvas.save(out,"JPEG",quality=82,optimize=True)
        except Exception:
            return FileResponse(source,headers={"Cache-Control":"private, max-age=86400"})

    return FileResponse(out,headers={"Cache-Control":"private, max-age=604800, immutable"})


GARMENT_ENRICHMENT_SCHEMA = {
  "type": "object",
  "properties": {
    "identification_summary": {"type": "string"},
    "likely_exact_match": {"type": "boolean"},
    "model_line": {"type": "string"},
    "fit_profile": {"type": "string"},
    "sizing_guidance": {"type": "string"},
    "fabric_details": {"type": "string"},
    "construction_details": {"type": "string"},
    "seasonality": {"type": "string"},
    "measurements_or_size_chart": {"type": "string"},
    "confidence": {"type": "string", "enum": ["high","medium","low"]},
    "suggested_updates": {
      "type": "object",
      "properties": {
        "model_line": {"type": "string"},
        "material": {"type": "string"},
        "fit_cut": {"type": "string"},
        "season": {"type": "string"},
        "formality": {"type": "string"},
        "notes": {"type": "string"}
      },
      "required": ["model_line","material","fit_cut","season","formality","notes"],
      "additionalProperties": False
    },
    "sources": {
      "type": "array",
      "maxItems": 6,
      "items": {
        "type": "object",
        "properties": {
          "title": {"type": "string"},
          "url": {"type": "string"},
          "note": {"type": "string"}
        },
        "required": ["title","url","note"],
        "additionalProperties": False
      }
    }
  },
  "required": [
    "identification_summary","likely_exact_match","model_line","fit_profile","sizing_guidance",
    "fabric_details","construction_details","seasonality","measurements_or_size_chart",
    "confidence","suggested_updates","sources"
  ],
  "additionalProperties": False
}

def _run_garment_enrichment_current(gid: int):
    con = db()
    row = con.execute("SELECT * FROM garments WHERE id=?", (gid,)).fetchone()
    if not row:
        con.close()
        return

    garment = dict(row)
    profile = dict(con.execute("SELECT * FROM profile WHERE id=1").fetchone())
    con.close()

    if not garment.get("brand"):
        con = db()
        con.execute(
            "UPDATE garments SET enrichment_status='needs_brand', enrichment_updated_at=CURRENT_TIMESTAMP WHERE id=?",
            (gid,)
        )
        con.commit()
        con.close()
        return

    if not os.getenv("OPENAI_API_KEY") or OpenAI is None:
        con = db()
        con.execute(
            "UPDATE garments SET enrichment_status='error', enrichment_updated_at=CURRENT_TIMESTAMP WHERE id=?",
            (gid,)
        )
        con.commit()
        con.close()
        return

    prompt = f"""
Research this real {fashion_audience()} garment using the live web.

KNOWN GARMENT DATA:
Brand: {garment.get('brand') or ''}
Model / line entered by user: {garment.get('model_line') or ''}
Garment type: {garment.get('garment_type') or garment.get('category') or ''}
Labelled size: {garment.get('labelled_size') or ''}
Colour: {garment.get('colour') or ''}
Material already recorded: {garment.get('material') or ''}
Fit/cut already recorded: {garment.get('fit_cut') or ''}
Notes: {garment.get('notes') or ''}

USER FIT CONTEXT:
Height: {profile.get('height_cm') or ''}
Chest: {profile.get('chest_cm') or ''}
Waist: {profile.get('waist_cm') or ''}
Inseam: {profile.get('inseam_cm') or ''}
Preferred fit: {profile.get('preferred_fit') or ''}
Brand notes: {profile.get('brand_notes') or ''}

GOAL:
Find reliable information that makes this garment more useful to a personal stylist:
brand/line fit tendencies, sizing information, fabric/construction, seasonality, and official
or retailer size-chart information where available.

If model/line is blank, you MAY identify a likely line only when the available evidence is strong.
Do not guess an exact product from colour/type alone. Mark likely_exact_match false when uncertain.

Rules:
- Prefer official brand pages and reputable retailer/product pages.
- Never invent a measurement, product line, URL, fabric composition or fit claim.
- If something cannot be established, return an empty string.
- Sources must be real URLs found during the live search.
- suggested_updates are suggestions for the user to review; do not assume they will be applied.
- sizing_guidance must state uncertainty clearly and must not claim a size is guaranteed to fit.
"""

    try:
        client = OpenAI()
        response = tracked_responses_create(client,
            model=os.getenv("OPENAI_SHOPPING_MODEL", os.getenv("OPENAI_MODEL","gpt-5.6-terra")),
            reasoning={"effort":"low"},
            tools=[{"type":"web_search"}],
            tool_choice="auto",
            include=["web_search_call.action.sources"],
            input=prompt,
            text={"format":{
                "type":"json_schema",
                "name":"garment_brand_enrichment",
                "schema":GARMENT_ENRICHMENT_SCHEMA,
                "strict":True
            }}
        )
        result = json.loads(response.output_text)
        con = db()
        con.execute(
            """UPDATE garments
               SET enrichment_json=?, enrichment_status='ready', enrichment_updated_at=CURRENT_TIMESTAMP
               WHERE id=?""",
            (json.dumps(result, ensure_ascii=False), gid)
        )
        con.commit()
        con.close()
    except Exception as exc:
        con = db()
        con.execute(
            """UPDATE garments
               SET enrichment_json=?, enrichment_status='error', enrichment_updated_at=CURRENT_TIMESTAMP
               WHERE id=?""",
            (json.dumps({"error": str(exc)[:500]}), gid)
        )
        con.commit()
        con.close()


def run_garment_enrichment(gid: int, user_id: Optional[int]=None):
    token=None
    if user_id:
        user=get_user_by_id(user_id)
        if user:
            token=CURRENT_USER.set(user)
    try:
        return _run_garment_enrichment_current(gid)
    finally:
        if token is not None:
            CURRENT_USER.reset(token)

@app.get("/api/garments/{gid}/detail")
def garment_detail(gid: int):
    con = db()
    row = con.execute("SELECT * FROM garments WHERE id=?", (gid,)).fetchone()
    if not row:
        con.close()
        raise HTTPException(404, "Garment not found")

    garment = dict(row)
    best_image,image_source=best_garment_image(garment)
    garment["image_status"]=image_source
    garment["image_available"]=bool(best_image)
    if best_image and best_image != (garment.get("image_path") or ""):
        garment["image_path"]=best_image
        con.execute("UPDATE garments SET image_path=? WHERE id=?",(best_image,gid))
        con.commit()
    feedback_rows = con.execute(
        "SELECT rating, outfit_json, created_at FROM feedback ORDER BY id DESC LIMIT 100"
    ).fetchall()
    con.close()

    appearances = []
    for r in feedback_rows:
        try:
            outfit = json.loads(r["outfit_json"] or "{}")
            ids = outfit.get("garment_ids") or outfit.get("owned_garment_ids") or []
            if gid in ids:
                appearances.append({
                    "rating": r["rating"],
                    "created_at": r["created_at"],
                    "label": outfit.get("label") or "Outfit"
                })
        except Exception:
            pass

    enrichment = None
    if garment.get("enrichment_json"):
        try:
            enrichment = json.loads(garment["enrichment_json"])
        except Exception:
            enrichment = None

    garment["enrichment"] = enrichment
    garment["outfit_history"] = appearances[:12]
    garment["outfit_history_count"] = len(appearances)
    return garment


@app.post("/api/garments/{gid}/enrich")
def enrich_garment(gid: int, background_tasks: BackgroundTasks):
    con = db()
    row = con.execute("SELECT id, brand FROM garments WHERE id=?", (gid,)).fetchone()
    if not row:
        con.close()
        raise HTTPException(404, "Garment not found")
    if not row["brand"]:
        con.close()
        raise HTTPException(400, "Add the brand first so I have something reliable to research.")

    con.execute(
        "UPDATE garments SET enrichment_status='researching', enrichment_updated_at=CURRENT_TIMESTAMP WHERE id=?",
        (gid,)
    )
    con.commit()
    con.close()

    background_tasks.add_task(run_garment_enrichment, gid, current_user_id())
    return {"ok": True, "status": "researching"}


@app.post("/api/garments/{gid}/apply-enrichment")
def apply_garment_enrichment(gid: int):
    con = db()
    row = con.execute("SELECT * FROM garments WHERE id=?", (gid,)).fetchone()
    if not row:
        con.close()
        raise HTTPException(404, "Garment not found")

    garment = dict(row)
    try:
        data = json.loads(garment.get("enrichment_json") or "{}")
        suggestions = data.get("suggested_updates") or {}
    except Exception:
        suggestions = {}

    if not suggestions:
        con.close()
        raise HTTPException(400, "There are no researched updates to apply.")

    # Never overwrite user-entered metadata silently: only fill currently blank fields.
    fields = ["model_line","material","fit_cut","season","formality"]
    updates = {}
    for field in fields:
        current = garment.get(field) or ""
        suggested = suggestions.get(field) or ""
        if not current.strip() and suggested.strip():
            updates[field] = suggested.strip()

    notes_suggestion = (suggestions.get("notes") or "").strip()
    if notes_suggestion:
        current_notes = (garment.get("notes") or "").strip()
        if notes_suggestion not in current_notes:
            updates["notes"] = (current_notes + ("\n" if current_notes else "") + "Web research: " + notes_suggestion).strip()

    if updates:
        sets = ", ".join(f"{k}=?" for k in updates)
        con.execute(
            f"UPDATE garments SET {sets} WHERE id=?",
            tuple(updates.values()) + (gid,)
        )
    con.commit()
    con.close()
    return {"ok": True, "applied_fields": list(updates.keys())}


@app.post("/api/garments/{gid}/ignore-enrichment")
def ignore_garment_enrichment(gid: int):
    con = db()
    exists = con.execute("SELECT id FROM garments WHERE id=?", (gid,)).fetchone()
    if not exists:
        con.close()
        raise HTTPException(404, "Garment not found")
    con.execute(
        "UPDATE garments SET enrichment_status='ignored', enrichment_updated_at=CURRENT_TIMESTAMP WHERE id=?",
        (gid,)
    )
    con.commit()
    con.close()
    return {"ok": True}

class GarmentUpdate(BaseModel):
    category: Optional[str] = ""
    category_manual: Optional[bool] = True
    garment_type: Optional[str] = ""
    brand: Optional[str] = ""
    model_line: Optional[str] = ""
    labelled_size: Optional[str] = ""
    colour: Optional[str] = ""
    material: Optional[str] = ""
    pattern: Optional[str] = ""
    fit_cut: Optional[str] = ""
    fit_feedback: Optional[str] = "Unknown"
    season: Optional[str] = ""
    formality: Optional[str] = ""
    notes: Optional[str] = ""


@app.post("/api/garments/{gid}/cleanup-image")
def cleanup_garment_image(gid: int):
    con = db()
    row = con.execute(
        "SELECT id, image_path, original_image_path FROM garments WHERE id=?",
        (gid,)
    ).fetchone()
    if not row:
        con.close()
        raise HTTPException(404, "Garment not found")

    # The original upload is always preferred as the cleanup source. For an
    # older row, only promote image_path to original when it is a real upload.
    original_rel = row["original_image_path"] or ""
    if not original_rel and str(row["image_path"] or "").startswith("/uploads/"):
        original_rel = row["image_path"]
        con.execute("UPDATE garments SET original_image_path=? WHERE id=?", (original_rel, gid))
        con.commit()

    source_rel = original_rel or row["image_path"]
    source_path = resolve_saved_image_path(source_rel)
    if not source_path.exists():
        con.close()
        raise HTTPException(404, "The original garment photo could not be found. Cleanup was not attempted and no image was changed.")

    try:
        cleaned_path = premium_remove_background(source_path)
    except CleanupNotConfigured as exc:
        con.close()
        raise HTTPException(503, str(exc))
    except CleanupServiceError as exc:
        con.close()
        raise HTTPException(502, str(exc))

    new_rel = f"/cleaned/{cleaned_path.name}"

    # V3.6 deliberately does NOT delete either the original or a previous
    # cleaned file here. Cleanup is non-destructive and can always fall back.
    con.execute(
        "UPDATE garments SET image_path=?, original_image_path=? WHERE id=?",
        (new_rel, original_rel or source_rel, gid)
    )
    con.commit()
    con.close()
    return {"ok": True, "image_path": new_rel, "original_image_path": original_rel or source_rel}

@app.post("/api/garments/{gid}/restore-original")
def restore_original_garment_image(gid: int):
    con = db()
    row = con.execute(
        "SELECT image_path, original_image_path FROM garments WHERE id=?",
        (gid,)
    ).fetchone()
    if not row:
        con.close()
        raise HTTPException(404, "Garment not found")

    original = row["original_image_path"]
    if not original:
        con.close()
        raise HTTPException(400, "No separate original photo is available for this garment.")

    old_display = row["image_path"]
    con.execute("UPDATE garments SET image_path=? WHERE id=?", (original, gid))
    con.commit()
    con.close()

    # Keep the processed derivative on disk. It is disposable, but retaining it
    # avoids any chance of deleting the only usable image because of legacy data.
    return {"ok": True, "image_path": original}

@app.put("/api/garments/{gid}")
def update_garment(gid: int, g: GarmentUpdate):
    con = db()
    exists = con.execute("SELECT id FROM garments WHERE id=?", (gid,)).fetchone()
    if not exists:
        con.close()
        raise HTTPException(404, "Garment not found")

    selected_category=(g.category or "").strip()
    if selected_category not in wardrobe_category_order():
        selected_category=canonical_wardrobe_category(
            selected_category,g.garment_type,g.model_line,g.fit_cut,g.notes,g.brand,g.material
        )
    con.execute("""UPDATE garments SET
        category=?, category_manual=?, garment_type=?, brand=?, model_line=?, labelled_size=?,
        colour=?, material=?, pattern=?, fit_cut=?, fit_feedback=?,
        season=?, formality=?, notes=?
        WHERE id=?""",
        (selected_category,1 if g.category_manual else 0,g.garment_type,g.brand,g.model_line,g.labelled_size,
         g.colour,g.material,g.pattern,g.fit_cut,g.fit_feedback,
         g.season,g.formality,g.notes,gid))
    con.commit()
    con.close()
    return {"ok": True, "id": gid}

@app.delete("/api/garments/{gid}")
def delete_garment(gid: int):
    con = db()
    row = con.execute("SELECT image_path, original_image_path FROM garments WHERE id=?", (gid,)).fetchone()
    con.execute("DELETE FROM garments WHERE id=?", (gid,))
    con.commit(); con.close()
    if row:
        for rel in [row["image_path"], row["original_image_path"]]:
            if not rel:
                continue
            try:
                p=resolve_saved_image_path(rel)
                active_root=active_user_root().resolve()
                resolved=p.resolve()
                # Never allow a garment delete to escape the signed-in user's storage.
                if resolved.is_relative_to(active_root) and p.exists() and p.is_file():
                    p.unlink()
            except Exception:
                pass
    return {"ok": True}

def normalise_image_for_ai(source_path: Path) -> Path:
    """Convert uploads to a bounded JPEG while keeping peak memory modest."""
    try:
        with Image.open(source_path) as opened:
            # JPEG draft asks Pillow/libjpeg to decode near the target resolution
            # instead of first expanding a 12/24/48MP phone image at full size.
            try:
                if (opened.format or "").upper() in ("JPEG","JPG"):
                    opened.draft("RGB", (1600, 1600))
            except Exception:
                pass

            im = ImageOps.exif_transpose(opened)

            # Bound dimensions before creating further RGB/transparency copies.
            max_side = 1600
            if max(im.size) > max_side:
                im.thumbnail((max_side, max_side), Image.Resampling.LANCZOS)

            if im.mode in ("RGBA","LA") or (im.mode=="P" and "transparency" in im.info):
                rgba=im.convert("RGBA")
                background=Image.new("RGB",rgba.size,"white")
                background.paste(rgba,mask=rgba.getchannel("A"))
                try:
                    rgba.close()
                except Exception:
                    pass
                im=background
            elif im.mode!="RGB":
                im=im.convert("RGB")

            out_path=source_path.with_suffix(".jpg")
            im.save(out_path,format="JPEG",quality=88,optimize=False)

            try:
                if im is not opened:
                    im.close()
            except Exception:
                pass

        if out_path != source_path and source_path.exists():
            try: source_path.unlink()
            except Exception: pass
        return out_path

    except (UnidentifiedImageError,OSError,ValueError) as exc:
        raise HTTPException(
            status_code=400,
            detail="I couldn't read that photo. Please try taking it again or choose another image."
        ) from exc


def encode_image(path: Path):
    data = base64.b64encode(path.read_bytes()).decode()
    return f"data:image/jpeg;base64,{data}"


def create_catalogue_image(source_path: Path) -> Path:
    """
    Create a cleaner wardrobe display image from the real uploaded garment photo.
    This is non-generative: it does not invent or redraw the garment.
    It corrects orientation, lightly normalises contrast/brightness, applies a
    conservative subject crop, and places the real pixels on a neutral canvas.

    Note: fully automatic semantic background removal is intentionally conservative
    in this build. We avoid aggressive segmentation that could cut away sleeves,
    hems, laces, straps, or other garment details.
    """
    try:
        with Image.open(source_path) as im:
            im = ImageOps.exif_transpose(im).convert("RGB")

            # Light photographic normalisation only.
            im = ImageEnhance.Contrast(im).enhance(1.04)
            im = ImageEnhance.Brightness(im).enhance(1.02)

            # Conservative crop: remove only a small outer margin.
            w, h = im.size
            pad_x = int(w * 0.03)
            pad_y = int(h * 0.03)
            if w > 300 and h > 300:
                im = im.crop((pad_x, pad_y, w - pad_x, h - pad_y))

            # Fit onto a consistent portrait catalogue canvas without distortion.
            canvas_w, canvas_h = 1200, 1500
            margin = 80
            available_w = canvas_w - 2 * margin
            available_h = canvas_h - 2 * margin

            scale = min(available_w / im.width, available_h / im.height)
            new_size = (max(1, round(im.width * scale)), max(1, round(im.height * scale)))
            im = im.resize(new_size)

            canvas = Image.new("RGB", (canvas_w, canvas_h), (248, 248, 247))
            x = (canvas_w - im.width) // 2
            y = (canvas_h - im.height) // 2
            canvas.paste(im, (x, y))

            out_path = cleaned_dir() / f"catalogue_{uuid.uuid4().hex}.jpg"
            canvas.save(out_path, format="JPEG", quality=92, optimize=True)
            return out_path
    except Exception:
        # Never block wardrobe upload just because the display cleanup failed.
        return source_path




class CleanupNotConfigured(Exception):
    pass


class CleanupServiceError(Exception):
    pass



def premium_remove_background(source_path: Path) -> Path:
    """Remove the background using remove.bg without altering the source file.

    V3.6 fixes the multipart body used in V3.5 (real CRLF separators rather
    than escaped text), preserves useful API errors, and keeps memory bounded.
    """
    api_key = os.getenv("REMOVE_BG_API_KEY", "").strip()
    if not api_key:
        raise CleanupNotConfigured(
            "Photo cleanup is not configured on the running service. REMOVE_BG_API_KEY was not visible to this process. Your original photo is unchanged."
        )

    import io
    try:
        with Image.open(source_path) as im:
            im = ImageOps.exif_transpose(im).convert("RGB")
            im.thumbnail((1600, 1600), Image.Resampling.LANCZOS)
            buf = io.BytesIO()
            im.save(buf, "JPEG", quality=86, optimize=True)
            raw = buf.getvalue()
    except Exception as exc:
        raise CleanupServiceError(f"I couldn't prepare this photo for cleanup: {exc}")

    boundary = "----PersonalStylist" + uuid.uuid4().hex
    crlf = "\r\n"
    parts = []

    def field(name, value):
        parts.append((
            f"--{boundary}{crlf}"
            f'Content-Disposition: form-data; name="{name}"{crlf}{crlf}'
            f"{value}{crlf}"
        ).encode("utf-8"))

    field("size", "auto")
    field("format", "png")
    parts.append((
        f"--{boundary}{crlf}"
        f'Content-Disposition: form-data; name="image_file"; filename="garment.jpg"{crlf}'
        f"Content-Type: image/jpeg{crlf}{crlf}"
    ).encode("utf-8"))
    parts.append(raw)
    parts.append(crlf.encode("ascii"))
    parts.append(f"--{boundary}--{crlf}".encode("ascii"))

    req = urllib.request.Request(
        "https://api.remove.bg/v1.0/removebg",
        data=b"".join(parts),
        headers={
            "X-Api-Key": api_key,
            "Content-Type": f"multipart/form-data; boundary={boundary}",
            "User-Agent": "Personal-Stylist/3.6",
        },
        method="POST",
    )

    try:
        with urllib.request.urlopen(req, timeout=75) as response:
            png = response.read()
    except urllib.error.HTTPError as exc:
        try:
            detail = exc.read().decode("utf-8", errors="replace")
            parsed = json.loads(detail)
            errors = parsed.get("errors") or []
            message = errors[0].get("title") if errors and isinstance(errors[0], dict) else detail
        except Exception:
            message = f"HTTP {exc.code}"
        lower_message=str(message or "").lower()
        if exc.code in (402,429) or any(x in lower_message for x in ["credit","quota","insufficient"]):
            raise CleanupServiceError(
                "Background-cleaning credits have run out or the service limit has been reached. "
                "Add remove.bg credits and try again. Your original photo is unchanged."
            )
        raise CleanupServiceError(f"Background-removal service returned an error: {message}. Your original photo is unchanged.")
    except urllib.error.URLError as exc:
        raise CleanupServiceError(f"Background-removal service could not be reached: {exc.reason}. Your original photo is unchanged.")
    except Exception as exc:
        raise CleanupServiceError(f"Photo cleanup failed: {exc}. Your original photo is unchanged.")

    try:
        cutout = Image.open(io.BytesIO(png)).convert("RGBA")
        bbox = cutout.getbbox()
        if not bbox:
            raise CleanupServiceError("The cleanup service returned an empty image. Your original photo is unchanged.")
        cutout = cutout.crop(bbox)
        canvas_w, canvas_h, margin = 1200, 1500, 90
        scale = min((canvas_w - 2 * margin) / cutout.width, (canvas_h - 2 * margin) / cutout.height)
        cutout = cutout.resize(
            (max(1, round(cutout.width * scale)), max(1, round(cutout.height * scale))),
            Image.Resampling.LANCZOS,
        )
        canvas = Image.new("RGBA", (canvas_w, canvas_h), (249, 249, 248, 255))
        canvas.alpha_composite(cutout, ((canvas_w - cutout.width) // 2, (canvas_h - cutout.height) // 2))
        out = cleaned_dir() / f"ai_isolated_{uuid.uuid4().hex}.png"
        canvas.convert("RGB").save(out, "PNG", optimize=True)
        return out
    except CleanupServiceError:
        raise
    except Exception as exc:
        raise CleanupServiceError(f"The cleaned image could not be prepared: {exc}. Your original photo is unchanged.")


def resolve_saved_image_path(rel_path: str) -> Path:
    rel = str(rel_path or "").lstrip("/")
    if rel.startswith(("uploads/","cleaned/","model-photos/","generated/")):
        return active_user_root() / rel
    return ROOT / rel

GARMENT_SCHEMA = {
  "type":"object",
  "properties":{
    "category":{"type":"string"},
    "garment_type":{"type":"string"},
    "brand":{"type":"string"},
    "model_line":{"type":"string"},
    "labelled_size":{"type":"string"},
    "colour":{"type":"string"},
    "material":{"type":"string"},
    "pattern":{"type":"string"},
    "fit_cut":{"type":"string"},
    "season":{"type":"string"},
    "formality":{"type":"string"},
    "notes":{"type":"string"},
    "confidence":{"type":"number","minimum":0,"maximum":1}
  },
  "required":["category","garment_type","brand","model_line","labelled_size","colour","material","pattern","fit_cut","season","formality","notes","confidence"],
  "additionalProperties":False
}

def analyse_image(path: Path):
    if not os.getenv("OPENAI_API_KEY") or OpenAI is None:
        return None
    client = OpenAI()
    img = encode_image(path)
    prompt = f"""Analyse this photograph as a careful {fashion_audience()} wardrobe cataloguer.
Identify only details reasonably visible from the image. Do not invent brand, size,
fabric composition, model/line or fit if they cannot be seen or inferred with reasonable confidence.
Use empty strings for unknown fields. Colour should be specific (e.g. stone, cream, navy, sage),
not merely 'light'. Season and formality should be practical clothing classifications.
The notes field should mention uncertainty or useful visible details.

For CATEGORY, use the current wardrobe profile ({styling_profile()}). If womenswear, use only:
Dresses, Skirts, Jumpsuits & Playsuits, Blazers & Tailoring, Jackets, Coats, Knitwear,
Sweatshirts & Hoodies, Blouses & Shirts, Tops & T-Shirts, Trousers & Jeans, Shorts,
Activewear, Footwear, Bags, Accessories, Other.
If menswear, classify by wardrobe role and silhouette rather than blindly copying the product-name noun:
- short-sleeve knitted polo -> Polos & T-Shirts
- long-sleeve knitted polo/pullover -> Knitwear
- rugby shirt/rugby top -> Knitwear
- sweatshirt/hoodie -> Sweatshirts & Hoodies
- overshirt/shirt jacket -> Overshirts & Shirt Jackets
- a true buttoned shirt -> Shirts
- a lightweight knitted pullover remains Knitwear even if a retailer calls it a "T-shirt".
Use the closest established wardrobe category and do not let the word "shirt" override the actual construction/use."""
    response = tracked_responses_create(client,
        model=os.getenv("OPENAI_MODEL","gpt-5.6-terra"),
        reasoning={"effort":"low"},
        input=[{
          "role":"user",
          "content":[
            {"type":"input_text","text":prompt},
            {"type":"input_image","image_url":img,"detail":"high"}
          ]
        }],
        text={"format":{
          "type":"json_schema",
          "name":"garment_analysis",
          "schema":GARMENT_SCHEMA,
          "strict":True
        }}
    )
    return json.loads(response.output_text)

@app.post("/api/analyse-garment")
async def analyse_garment(file: UploadFile = File(...)):
    # Save the raw upload first. Do not trust the filename/extension because
    # iPhones can upload HEIC/HEIF with inconsistent metadata.
    original_suffix = Path(file.filename or "photo").suffix.lower() or ".upload"
    raw_name = f"{uuid.uuid4().hex}{original_suffix}"
    raw_path = uploads_dir() / raw_name

    data = await file.read()
    if not data:
        raise HTTPException(400, "The uploaded photo was empty.")
    if len(data) > 15 * 1024 * 1024:
        raise HTTPException(400, "That photo is too large. Please choose an image under 15 MB.")

    raw_path.write_bytes(data)

    # Convert HEIC/HEIF (and normalise other readable formats) to JPEG.
    image_path = normalise_image_for_ai(raw_path)

    try:
        result = analyse_image(image_path)
    except Exception as exc:
        message = str(exc)
        lower = message.lower()

        if "insufficient_quota" in lower or "billing" in lower:
            detail = "OpenAI billing or API credit needs attention before AI garment analysis can run."
        elif "model" in lower and ("not found" in lower or "does not exist" in lower):
            detail = "The configured OpenAI model is not available to this API project. Check OPENAI_MODEL in Render."
        elif "invalid image" in lower or "valid image" in lower:
            detail = "The photo could not be processed by the AI. Please try taking it again."
        else:
            detail = f"AI garment analysis failed: {message[:300]}"

        raise HTTPException(status_code=502, detail=detail) from exc

    catalogue_path = create_catalogue_image(image_path)

    return {
      "image_path": (
          f"/cleaned/{catalogue_path.name}"
          if catalogue_path.parent == cleaned_dir()
          else f"/uploads/{image_path.name}"
      ),
      "original_image_path": f"/uploads/{image_path.name}",
      "analysis": result,
      "ai_enabled": result is not None
    }


@app.post("/api/garments/{gid}/photo")
async def replace_garment_photo(gid: int, file: UploadFile = File(...)):
    con=db()
    row=con.execute("SELECT id,image_path,original_image_path FROM garments WHERE id=?",(gid,)).fetchone()
    if not row:
        con.close()
        raise HTTPException(404,"Garment not found.")

    suffix=Path(file.filename or "photo").suffix.lower() or ".upload"
    raw=uploads_dir() /f"{uuid.uuid4().hex}{suffix}"
    data=await file.read()
    if not data:
        con.close()
        raise HTTPException(400,"The uploaded photo was empty.")
    if len(data)>20*1024*1024:
        con.close()
        raise HTTPException(400,"That photo is too large. Please choose an image under 15 MB.")
    raw.write_bytes(data)

    try:
        normal=normalise_image_for_ai(raw)
        catalogue=create_catalogue_image(normal)
        display=(f"/cleaned/{catalogue.name}" if catalogue.parent==cleaned_dir() else f"/uploads/{normal.name}")
        original=f"/uploads/{normal.name}"
    except Exception:
        con.close()
        raise

    old_display=row["image_path"] or ""
    old_original=row["original_image_path"] or ""
    con.execute("UPDATE garments SET image_path=?,original_image_path=? WHERE id=?",(display,original,gid))
    con.commit()
    con.close()

    # Best-effort cleanup of the previous files once the DB update succeeds.
    for rel in {old_display,old_original}:
        if not rel or rel in {display,original}:
            continue
        try:
            rel_path=str(rel).lstrip("/")
            if rel_path.startswith(("cleaned/","uploads/")):
                p=DATA_DIR/rel_path
                if p.exists():p.unlink()
        except Exception:
            pass

    return {"ok":True,"id":gid,"image_path":display,"original_image_path":original}

@app.post("/api/garments")
async def add_garment(
    image_path: str = Form(""),
    original_image_path: str = Form(""),
    category: str = Form(""),
    garment_type: str = Form(""),
    brand: str = Form(""),
    model_line: str = Form(""),
    labelled_size: str = Form(""),
    colour: str = Form(""),
    material: str = Form(""),
    pattern: str = Form(""),
    fit_cut: str = Form(""),
    fit_feedback: str = Form("Unknown"),
    season: str = Form(""),
    formality: str = Form(""),
    notes: str = Form(""),
    ai_confidence: float = Form(0)
):
    con = db()
    cur = con.execute("""INSERT INTO garments
      (image_path,original_image_path,category,category_manual,garment_type,brand,model_line,labelled_size,colour,material,pattern,fit_cut,fit_feedback,season,formality,notes,ai_confidence)
      VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
      (image_path,original_image_path or image_path,canonical_wardrobe_category(category, garment_type, model_line, fit_cut, notes, brand, material),0,garment_type,brand,model_line,labelled_size,colour,material,pattern,fit_cut,fit_feedback,season,formality,notes,ai_confidence))
    con.commit(); gid=cur.lastrowid; con.close()
    return {"ok":True,"id":gid}


class QuickWardrobeRequest(BaseModel):
    description: str

QUICK_WARDROBE_SCHEMA = {
 "type":"object",
 "properties":{
  "summary":{"type":"string"},
  "items":{"type":"array","minItems":1,"maxItems":40,"items":{
   "type":"object",
   "properties":{
    "category":{"type":"string"},
    "garment_type":{"type":"string"},
    "brand":{"type":"string"},
    "model_line":{"type":"string"},
    "labelled_size":{"type":"string"},
    "colour":{"type":"string"},
    "material":{"type":"string"},
    "pattern":{"type":"string"},
    "fit_cut":{"type":"string"},
    "fit_feedback":{"type":"string","enum":["Unknown","Perfect fit","Slightly tight","Slightly loose","Too tight","Too loose"]},
    "season":{"type":"string"},
    "formality":{"type":"string"},
    "notes":{"type":"string"},
    "confidence":{"type":"string","enum":["high","medium","low"]}
   },
   "required":["category","garment_type","brand","model_line","labelled_size","colour","material","pattern","fit_cut","fit_feedback","season","formality","notes","confidence"],
   "additionalProperties":False
  }}
 },
 "required":["summary","items"],
 "additionalProperties":False
}

@app.post("/api/quick-wardrobe/parse")
def parse_quick_wardrobe(req: QuickWardrobeRequest):
    text=(req.description or "").strip()
    if len(text)<3:
        raise HTTPException(400,"Describe at least one wardrobe item.")
    if len(text)>12000:
        raise HTTPException(400,"That wardrobe description is too long. Split it into two batches.")

    client=OpenAI()
    instructions="""You convert a user's plain-language description of their existing wardrobe into reviewable garment records.
Extract only garments the user actually says they own. One physical garment = one item.
If they describe multiples, create separate items only when the description distinguishes them; otherwise create the stated quantity as separate records with the same known metadata.
Never invent a brand, model, size, material, colour, pattern, fit or season. Leave unknown strings blank.
Use these canonical categories only: {", ".join(wardrobe_category_order())}. Blazers, sports jackets and suit jackets belong in Blazers & Tailoring. Overshirts, shirt jackets and shackets belong in Overshirts & Shirt Jackets. Utility shirts and work shirts belong in Shirts unless explicitly described as an overshirt or shirt jacket. Sweatshirts and hoodies belong in Sweatshirts & Hoodies; do not classify them as Polos & T-Shirts or Knitwear.
Classify by wardrobe role rather than literal product naming: rugby shirts belong in Knitwear; short-sleeve knitted polos belong in Polos & T-Shirts; long-sleeve knitted polos/pullovers belong in Knitwear. A lightweight knitted pullover can belong in Knitwear even if a retailer calls it a long-sleeve T-shirt. All true outerwear belongs in Jackets & Coats: Harringtons, bombers, gilets, wax jackets, denim/trucker jackets, sherpa-lined jackets, technical/rain jackets, parkas, macs, overcoats and winter coats.
Normalise obvious garment wording into a useful garment_type, e.g. polo shirt, crew-neck T-shirt, chinos, loafers, overshirt.
Season and formality can be inferred conservatively from the garment itself, but leave blank when uncertain.
Set confidence based on how completely the user's description supports the record.
The user will review every item before saving, so concise notes are better than speculation."""

    try:
        response=tracked_responses_create(client,
            model=os.getenv("OPENAI_MODEL","gpt-5.6-terra"),
            reasoning={"effort":"low"},
            instructions=instructions,
            input=text,
            text={"format":{"type":"json_schema","name":"quick_wardrobe","schema":QUICK_WARDROBE_SCHEMA,"strict":True}}
        )
        data=json.loads(response.output_text)
        for item in data.get("items",[]):
            item["category"]=canonical_wardrobe_category(item.get("category",""),item.get("garment_type",""))
        return data
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(500,f"I couldn't turn that description into wardrobe items: {str(e)[:240]}")

class QuickWardrobeItem(BaseModel):
    category: str=""
    garment_type: str=""
    brand: str=""
    model_line: str=""
    labelled_size: str=""
    colour: str=""
    material: str=""
    pattern: str=""
    fit_cut: str=""
    fit_feedback: str="Unknown"
    season: str=""
    formality: str=""
    notes: str=""

class QuickWardrobeSaveRequest(BaseModel):
    items: list[QuickWardrobeItem]

@app.post("/api/quick-wardrobe/save")
def save_quick_wardrobe(req: QuickWardrobeSaveRequest):
    if not req.items:
        raise HTTPException(400,"There are no items to save.")
    if len(req.items)>50:
        raise HTTPException(400,"Save a maximum of 50 items at a time.")

    con=db()
    saved=[]
    try:
        for item in req.items:
            garment_type=(item.garment_type or "").strip()
            if not garment_type:
                continue
            cur=con.execute("""INSERT INTO garments
              (image_path,original_image_path,category,garment_type,brand,model_line,labelled_size,colour,material,pattern,fit_cut,fit_feedback,season,formality,notes,ai_confidence)
              VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
              ("","",canonical_wardrobe_category(item.category,garment_type),garment_type,
               item.brand.strip(),item.model_line.strip(),item.labelled_size.strip(),item.colour.strip(),
               item.material.strip(),item.pattern.strip(),item.fit_cut.strip(),
               item.fit_feedback if item.fit_feedback in ["Unknown","Perfect fit","Slightly tight","Slightly loose","Too tight","Too loose"] else "Unknown",
               item.season.strip(),item.formality.strip(),item.notes.strip(),0))
            saved.append(cur.lastrowid)
        con.commit()
    except Exception:
        con.rollback()
        raise
    finally:
        con.close()

    if not saved:
        raise HTTPException(400,"No valid garments were available to save.")
    return {"ok":True,"saved_count":len(saved),"ids":saved}


class OutfitRequest(BaseModel):
    occasion: str
    temperature_c: float
    weather: str=""
    location: str=""
    anchor_id: Optional[int]=None
    dress_code: str="Use your judgement"
    smartness: str="Balanced"
    season: str="Auto / current"
    wardrobe_mode: str="Wardrobe first; suggest gaps only when useful"
    context_notes: str=""

OUTFIT_SCHEMA = {
 "type":"object",
 "properties":{
   "summary":{"type":"string"},
   "outfits":{"type":"array","minItems":1,"maxItems":3,"items":{
     "type":"object",
     "properties":{
       "label":{"type":"string"},
       "garment_ids":{"type":"array","items":{"type":"integer"}},
       "reason":{"type":"string"},
       "weather_note":{"type":"string"},
       "occasion_note":{"type":"string"},
       "missing_piece":{"type":"string"},
       "shopping_priority":{"type":"string","enum":["none","low","medium","high"]}
     },
     "required":["label","garment_ids","reason","weather_note","occasion_note","missing_piece","shopping_priority"],
     "additionalProperties":False
   }}
 },
 "required":["summary","outfits"],
 "additionalProperties":False
}

STYLIST_INSTRUCTIONS = """You are a highly skilled personal stylist for one individual.
Prioritise the user's real wardrobe. Never claim they own an item not in the wardrobe data.
Reason carefully about colour, shade, fabric/texture, season, actual temperature, occasion,
formality, silhouette, footwear, body/fit preferences, brand/size history and fit feedback.

PERSONALISATION:
- Treat repeated feedback patterns as meaningful evidence and a single rating cautiously.
- Increase the likelihood of combinations similar to outfits repeatedly Loved or Liked.
- Reduce the likelihood of combinations similar to outfits repeatedly rated Not for me.
- If the user repeatedly says Too smart or Too casual, adjust formality accordingly.
- Give strong weight to garments marked Perfect fit.
- Use brand/model/size notes and fit feedback when choosing between otherwise similar pieces.
- Do not overfit; preserve useful variety.

Treat the SITUATION fields as explicit styling instructions: occasion, dress code, requested smartness, season, weather, temperature, location and free-text context all matter.
Respect wardrobe_mode: if it is wardrobe-only, do not suggest missing pieces; if shopping is allowed, still prefer strong outfits from the wardrobe and suggest a gap only when it materially improves the result.
Prefer strong outfits fully from the wardrobe over marginally better outfits requiring purchases.
If a useful piece is missing, name only the category/style/colour/material needed; do not invent a product.
Produce genuinely different outfit options. Use only garment IDs supplied in the wardrobe JSON.
Be concise but specific about why the outfit works and, where relevant, connect recommendations to learned preferences."""





def saved_style_evidence(con, limit:int=40):
    """Compact, ranked style-memory evidence for AI prompts.

    A Saved Look is positive evidence, but a look the user has actually worn is
    stronger. This keeps the raw data transparent instead of inventing a hidden
    score: consumers receive wore_count, pinned state and last_worn_at directly,
    plus a simple evidence_level label.
    """
    rows=[dict(r) for r in con.execute("""
      SELECT id,label,outfit_json,request_text,weather_context,
             tags_json,occasion,season,notes,wore_count,last_worn_at,is_pinned,created_at
      FROM outfit_favourites
      ORDER BY COALESCE(wore_count,0) DESC, COALESCE(is_pinned,0) DESC, id DESC
      LIMIT ?
    """,(max(1,min(int(limit or 40),100)),)).fetchall()]
    evidence=[]
    for row in rows:
        try:
            outfit=json.loads(row.get("outfit_json") or "{}")
        except Exception:
            outfit={}
        try:
            tags=json.loads(row.get("tags_json") or "[]")
            if not isinstance(tags,list): tags=[]
        except Exception:
            tags=[]
        worn=max(0,int(row.get("wore_count") or 0))
        evidence.append({
          "saved_look_id":row.get("id"),
          "label":row.get("label") or outfit.get("label") or "",
          "garment_ids":outfit.get("owned_garment_ids") or outfit.get("garment_ids") or [],
          "wore_count":worn,
          "last_worn_at":row.get("last_worn_at"),
          "is_pinned":bool(row.get("is_pinned")),
          "occasion":row.get("occasion") or "",
          "season":row.get("season") or "",
          "tags":tags[:8],
          "notes":row.get("notes") or "",
          "request_text":row.get("request_text") or "",
          "weather_context":row.get("weather_context") or "",
          "evidence_level":"strong_real_wear" if worn>=2 else ("real_wear" if worn==1 else "saved_preference")
        })
    return evidence

def garment_wear_evidence(saved_looks:list[dict]):
    counts={}
    for look in saved_looks or []:
        worn=max(0,int(look.get("wore_count") or 0))
        if worn<=0:
            continue
        for raw in look.get("garment_ids") or []:
            try: gid=int(raw)
            except Exception: continue
            counts[gid]=counts.get(gid,0)+worn
    return counts


WARDROBE_INTELLIGENCE_SCHEMA = {
 "type":"object",
 "properties":{
  "summary":{"type":"string"},
  "strengths":{"type":"array","maxItems":5,"items":{"type":"string"}},
  "gaps":{"type":"array","maxItems":5,"items":{"type":"object","properties":{
    "title":{"type":"string"},
    "reason":{"type":"string"},
    "priority":{"type":"string","enum":["low","medium","high"]}
  },"required":["title","reason","priority"],"additionalProperties":False}},
  "saved_style_patterns":{"type":"array","maxItems":5,"items":{"type":"string"}},
  "versatility_wins":{"type":"array","maxItems":5,"items":{"type":"string"}},
  "variety_nudge":{"type":"string"},
  "next_purchase":{"type":"object","properties":{
    "item":{"type":"string"},
    "why":{"type":"string"},
    "unlock_estimate":{"type":"string"}
  },"required":["item","why","unlock_estimate"],"additionalProperties":False}
 },
 "required":["summary","strengths","gaps","saved_style_patterns","versatility_wins","variety_nudge","next_purchase"],
 "additionalProperties":False
}

@app.get("/api/wardrobe-intelligence")
def wardrobe_intelligence():
    con=db()
    garments=[dict(r) for r in con.execute("SELECT * FROM garments ORDER BY id DESC").fetchall()]
    favourites=saved_style_evidence(con,100)
    feedback=[dict(r) for r in con.execute(
        "SELECT rating,outfit_json,created_at FROM feedback ORDER BY id DESC LIMIT 100"
    ).fetchall()]
    con.close()

    if not garments:
        return {
          "metrics":{"total_items":0,"categories":0,"saved_looks":0,"perfect_fit_items":0},
          "category_counts":[],
          "colour_counts":[],
          "saved_item_counts":[],
          "analysis":{
            "summary":"Add wardrobe items to unlock wardrobe intelligence.",
            "strengths":[],"gaps":[],"saved_style_patterns":[],"versatility_wins":[],
            "variety_nudge":"",
            "next_purchase":{"item":"","why":"","unlock_estimate":""}
          }
        }

    category_counts={}
    colour_counts={}
    perfect_fit=0
    for g in garments:
        category=(g.get("category") or "Other").strip()
        category_counts[category]=category_counts.get(category,0)+1
        colour=(g.get("colour") or "").strip()
        if colour:
            colour_counts[colour]=colour_counts.get(colour,0)+1
        if (g.get("fit_feedback") or "").strip().lower()=="perfect fit":
            perfect_fit+=1

    garment_by_id={g["id"]:g for g in garments}
    saved_item_counts={}
    for row in favourites:
        for raw in row.get("garment_ids") or []:
            try: gid=int(raw)
            except Exception: continue
            if gid in garment_by_id:
                saved_item_counts[gid]=saved_item_counts.get(gid,0)+1
    worn_item_counts=garment_wear_evidence(favourites)

    category_list=[
        {"name":name,"count":count}
        for name,count in sorted(category_counts.items(),key=lambda x:(-x[1],x[0]))
    ]
    colour_list=[
        {"name":name,"count":count}
        for name,count in sorted(colour_counts.items(),key=lambda x:(-x[1],x[0]))[:12]
    ]
    saved_list=[]
    for gid,count in sorted(saved_item_counts.items(),key=lambda x:(-x[1],x[0]))[:12]:
        g=garment_by_id[gid]
        saved_list.append({
          "id":gid,
          "count":count,
          "label":" ".join(x for x in [g.get("brand"),g.get("garment_type") or g.get("category")] if x),
          "colour":g.get("colour") or "",
          "category":g.get("category") or ""
        })

    worn_list=[]
    for gid,count in sorted(worn_item_counts.items(),key=lambda x:(-x[1],x[0]))[:12]:
        g=garment_by_id.get(gid)
        if not g: continue
        worn_list.append({
          "id":gid,"count":count,
          "label":" ".join(x for x in [g.get("brand"),g.get("garment_type") or g.get("category")] if x),
          "colour":g.get("colour") or "","category":g.get("category") or ""
        })

    metrics={
      "total_items":len(garments),
      "categories":len(category_counts),
      "saved_looks":len(favourites),
      "worn_saved_looks":sum(1 for x in favourites if int(x.get("wore_count") or 0)>0),
      "recorded_wears":sum(int(x.get("wore_count") or 0) for x in favourites),
      "perfect_fit_items":perfect_fit
    }

    compact=[{
      "id":g["id"],
      "category":g.get("category"),
      "garment_type":g.get("garment_type"),
      "brand":g.get("brand"),
      "colour":g.get("colour"),
      "material":g.get("material"),
      "pattern":g.get("pattern"),
      "fit_cut":g.get("fit_cut"),
      "fit_feedback":g.get("fit_feedback"),
      "season":g.get("season"),
      "formality":g.get("formality"),
      "saved_look_count":saved_item_counts.get(g["id"],0)
    } for g in garments]

    fallback={
      "summary":"Your dashboard is based on wardrobe composition, saved-look patterns and fit feedback.",
      "strengths":[],
      "gaps":[],
      "saved_style_patterns":[],
      "versatility_wins":[],
      "variety_nudge":"Keep using Saved Looks and Works for me / Less like this to make these insights sharper.",
      "next_purchase":{"item":"No clear purchase yet","why":"More preference evidence will make this more useful.","unlock_estimate":"Not enough evidence yet"}
    }

    analysis=fallback
    if os.getenv("OPENAI_API_KEY") and OpenAI is not None:
        prompt={
          "metrics":metrics,
          "category_counts":category_list,
          "colour_counts":colour_list,
          "frequently_saved_items":saved_list,
          "wardrobe":compact,
          "recent_feedback":feedback[-60:],
          "saved_looks":favourites[:40]
        }
        instructions="""You are analysing one man's real wardrobe for a private personal stylist app.

Give practical wardrobe intelligence, not generic fashion advice.

Evidence rules:
- Distinguish wardrobe composition from actual wear. A garment appearing rarely in Saved Looks is NOT proof it is rarely worn.
- Saved Looks are positive preference evidence; repeated appearances are stronger than one appearance.
- Fit feedback is high-value evidence.
- Identify genuine category/colour/formality/seasonal imbalances only when supported by the data.
- Do not tell the user to buy something merely because a category count is low. A purchase should solve a real versatility or occasion gap.
- "next_purchase" should be "No clear purchase needed" when the current evidence does not justify one.
- unlock_estimate must be qualitative and evidence-based (for example "would combine with several navy/grey trousers and two jackets"), not a fabricated numeric count.
- Notice dominant colour patterns, but explicitly preserve variety rather than reinforcing one palette endlessly.
- Strong wardrobe items are pieces that combine broad versatility, good fit feedback, or repeated Saved Look use.
- Keep the writing concise and specific to the supplied wardrobe.
"""
        try:
            response=tracked_responses_create(OpenAI(),
              model=os.getenv("OPENAI_MODEL","gpt-5.6-terra"),
              reasoning={"effort":"low"},
              instructions=instructions,
              input=json.dumps(prompt,ensure_ascii=False),
              text={"format":{"type":"json_schema","name":"wardrobe_intelligence","schema":WARDROBE_INTELLIGENCE_SCHEMA,"strict":True}}
            )
            analysis=json.loads(response.output_text)
        except Exception:
            analysis=fallback

    return {
      "metrics":metrics,
      "category_counts":category_list,
      "colour_counts":colour_list,
      "saved_item_counts":saved_list,
      "worn_item_counts":worn_list,
      "analysis":analysis,
      "evidence_note":"Saved Look frequency shows preference. Recorded wears are separate, stronger evidence of what you actually use."
    }

@app.get("/api/style-learning")
def style_learning():
    con = db()
    rows = [dict(r) for r in con.execute(
        "SELECT rating, outfit_json, created_at FROM feedback ORDER BY id DESC LIMIT 100"
    ).fetchall()]
    garments = [dict(r) for r in con.execute(
        "SELECT id, brand, garment_type, fit_feedback, colour, material, formality FROM garments ORDER BY id DESC"
    ).fetchall()]
    favourites = saved_style_evidence(con,100)
    con.close()

    counts = {}
    for r in rows:
        counts[r["rating"]] = counts.get(r["rating"], 0) + 1

    perfect_fit_brands = {}
    for g in garments:
        if (g.get("fit_feedback") or "").lower() == "perfect fit" and g.get("brand"):
            perfect_fit_brands[g["brand"]] = perfect_fit_brands.get(g["brand"], 0) + 1
    top_brands = sorted(perfect_fit_brands.items(), key=lambda x: (-x[1], x[0]))[:5]

    garment_map={g["id"]:g for g in garments}
    saved_colours={}
    saved_categories={}
    worn_colours={}
    worn_categories={}
    for row in favourites:
        worn=max(0,int(row.get("wore_count") or 0))
        for raw_id in row.get("garment_ids") or []:
            try: gid=int(raw_id)
            except Exception: continue
            g=garment_map.get(gid)
            if not g: continue
            colour=(g.get("colour") or "").strip()
            category=(g.get("garment_type") or "").strip()
            if colour:
                saved_colours[colour]=saved_colours.get(colour,0)+1
                if worn: worn_colours[colour]=worn_colours.get(colour,0)+worn
            if category:
                saved_categories[category]=saved_categories.get(category,0)+1
                if worn: worn_categories[category]=worn_categories.get(category,0)+worn

    top_colours=sorted(saved_colours.items(),key=lambda x:(-x[1],x[0]))[:5]
    top_categories=sorted(saved_categories.items(),key=lambda x:(-x[1],x[0]))[:5]
    top_worn_colours=sorted(worn_colours.items(),key=lambda x:(-x[1],x[0]))[:5]
    top_worn_categories=sorted(worn_categories.items(),key=lambda x:(-x[1],x[0]))[:5]
    worn_look_count=sum(1 for x in favourites if int(x.get("wore_count") or 0)>0)
    recorded_wears=sum(int(x.get("wore_count") or 0) for x in favourites)

    return {
        "feedback_count": len(rows),
        "saved_look_count": len(favourites),
        "worn_look_count": worn_look_count,
        "recorded_wears": recorded_wears,
        "ratings": counts,
        "perfect_fit_brands": [{"brand": b, "count": c} for b, c in top_brands],
        "saved_colours": [{"name": n, "count": c} for n,c in top_colours],
        "saved_garment_types": [{"name": n, "count": c} for n,c in top_categories],
        "worn_colours": [{"name": n, "count": c} for n,c in top_worn_colours],
        "worn_garment_types": [{"name": n, "count": c} for n,c in top_worn_categories],
        "message": "Actual recorded wears now carry more weight than saved-only looks. Saved looks, reactions and fit feedback still matter, but repeated real wear is treated as the strongest style-preference evidence while preserving variety."
    }

@app.post("/api/outfits")
def outfits(req: OutfitRequest):
    con = db()
    garments = [dict(r) for r in con.execute("SELECT * FROM garments ORDER BY id DESC").fetchall()]
    profile = dict(con.execute("SELECT * FROM profile WHERE id=1").fetchone())
    recent_feedback = [dict(r) for r in con.execute(
        "SELECT rating, outfit_json, created_at FROM feedback ORDER BY id DESC LIMIT 30"
    ).fetchall()]
    saved_looks = saved_style_evidence(con,30)
    con.close()
    if len(garments) < 2:
        raise HTTPException(400, "Add at least two garments first.")
    if not os.getenv("OPENAI_API_KEY") or OpenAI is None:
        return fallback_outfits(garments, req)
    client = OpenAI()
    anchor = next((g for g in garments if g["id"]==req.anchor_id), None)
    context = {
      "profile": profile,
      "situation": req.model_dump(),
      "anchor_garment": anchor,
      "wardrobe": garments,
      "recent_feedback": recent_feedback,
      "saved_looks": saved_looks,
      "learning_rules": {
        "actual_wear_outweighs_saved_only": True,
        "use_repeated_patterns_not_single_reactions": True,
        "perfect_fit_feedback_is_high_value": True,
        "rejected_outfits_should_reduce_similar_future_combinations": True,
        "liked_or_loved_outfits_should_increase_similar_future_combinations": True
      }
    }
    response = tracked_responses_create(client,
      model=os.getenv("OPENAI_MODEL","gpt-5.6-terra"),
      reasoning={"effort":"medium"},
      instructions=STYLIST_INSTRUCTIONS,
      input=json.dumps(context, ensure_ascii=False),
      text={"format":{
        "type":"json_schema",
        "name":"outfit_recommendations",
        "schema":OUTFIT_SCHEMA,
        "strict":True
      }}
    )
    return json.loads(response.output_text)

def fallback_outfits(garments, req):
    # Deliberately simple offline fallback: allows UX testing with no API key.
    def role(g):
        s=(g.get("category","")+" "+g.get("garment_type","")).lower()
        if any(x in s for x in ["trouser","chino","short"]): return "bottom"
        if any(x in s for x in ["shoe","loafer","trainer","boot"]): return "shoes"
        if any(x in s for x in ["jacket","blazer","coat","overshirt"]): return "layer"
        return "top"
    anchor = next((g for g in garments if g["id"]==req.anchor_id),None)
    chosen={}
    if anchor: chosen[role(anchor)] = anchor
    for r in ["top","bottom","shoes"]:
        if r not in chosen:
            candidate=next((g for g in garments if role(g)==r and (not anchor or g["id"]!=anchor["id"])),None)
            if candidate: chosen[r]=candidate
    ids=[g["id"] for g in chosen.values()]
    missing = next((r for r in ["top","bottom","shoes"] if r not in chosen),"")
    return {
      "summary":"Offline test recommendation. Connect an OpenAI API key for full styling reasoning.",
      "outfits":[{
        "label":"Wardrobe-first test",
        "garment_ids":ids,
        "reason":"Uses available wardrobe categories to test the working flow. AI styling is not enabled.",
        "weather_note":f"Requested around {req.temperature_c:g}°C.",
        "occasion_note":req.occasion,
        "missing_piece":missing,
        "shopping_priority":"medium" if missing else "none"
      }]
    }




class TripContextRequest(BaseModel):
    destination: str = ""
    trip_brief: str = ""
    start_date: str = ""
    end_date: str = ""
    days: int = 5
    trip_type: str = "Mixed"
    weather: str = ""
    activities: str = ""
    dress_needs: str = ""
    notes: str = ""

TRIP_CONTEXT_SCHEMA = {
 "type":"object","properties":{
  "weather_mode":{"type":"string","enum":["forecast","seasonal","user-provided","unavailable"]},
  "weather_summary":{"type":"string"},
  "temperature_low_c":{"type":["number","null"]},
  "temperature_high_c":{"type":["number","null"]},
  "rain":{"type":"string"},
  "wind":{"type":"string"},
  "packing_weather_note":{"type":"string"},
  "destination_summary":{"type":"string"},
  "dress_context":{"type":"string"},
  "activity_context":{"type":"string"},
  "named_places":{"type":"array","maxItems":8,"items":{"type":"object","properties":{
   "name":{"type":"string"},"place_type":{"type":"string"},"dress_context":{"type":"string"},
   "evidence_level":{"type":"string","enum":["verified","inferred","general"]},"note":{"type":"string"}
  },"required":["name","place_type","dress_context","evidence_level","note"],"additionalProperties":False}},
  "sources":{"type":"array","maxItems":10,"items":{"type":"object","properties":{
   "title":{"type":"string"},"url":{"type":"string"},"supports":{"type":"string"}
  },"required":["title","url","supports"],"additionalProperties":False}},
  "research_note":{"type":"string"}
 },
 "required":["weather_mode","weather_summary","temperature_low_c","temperature_high_c","rain","wind",
 "packing_weather_note","destination_summary","dress_context","activity_context","named_places","sources","research_note"],
 "additionalProperties":False
}



class VoiceFormRequest(BaseModel):
    mode: str
    transcript: str

VOICE_FORM_SCHEMA = {
 "type":"object",
 "properties":{
  "summary":{"type":"string"},
  "fields":{"type":"array","maxItems":24,"items":{
   "type":"object",
   "properties":{
    "field":{"type":"string"},
    "value":{"type":"string"}
   },
   "required":["field","value"],
   "additionalProperties":False
  }}
 },
 "required":["summary","fields"],
 "additionalProperties":False
}

@app.post("/api/voice-form/parse")
def parse_voice_form(req: VoiceFormRequest):
    mode=(req.mode or "").strip().lower()
    transcript=(req.transcript or "").strip()
    if not transcript:
        raise HTTPException(400,"No dictated text was supplied.")
    if not os.getenv("OPENAI_API_KEY") or OpenAI is None:
        raise HTTPException(503,"Smart dictation needs the AI connection.")

    allowed={
      "packing":{
        "destination","start_date","end_date","days","trip_type","weather",
        "activities","dress_needs","laundry","shopping_allowed","notes"
      },
      "stylist":{
        "request_text","location","when","shopping"
      },
      "week":{
        "start_date","days","location","work_context","dress_needs","shopping_allowed"
      },
      "outfit":{
        "occasion","dress_code","smartness","season","temperature","weather",
        "location","wardrobe_mode","context_notes"
      },
      "shopping":{
        "goal","budget","season","occasion","shopping_mode"
      },
      "profile":{
        "name","height_cm","chest_cm","waist_cm","hips_cm","thigh_cm","inseam_cm",
        "sleeve_cm","neck_cm","preferred_fit","style_notes","brand_notes",
        "usual_top_size","usual_bottom_size","usual_dress_size","usual_shoe_size","bra_size",
        "preferred_rise","preferred_hem_length","heel_preference","accessory_notes"
      },
      "garment":{
        "category","garment_type","brand","model_line","labelled_size","colour",
        "material","pattern","fit_cut","fit_feedback","season","formality","notes"
      }
    }
    if mode not in allowed:
        raise HTTPException(400,"That part of the app does not support smart dictation yet.")

    today=date.today().isoformat()
    mode_guidance={
      "packing":"""Extract a complete trip brief.
- Normalize dates to YYYY-MM-DD when the user gives enough information.
- If the user gives a date range, populate both start_date and end_date.
- Calculate days inclusively when both dates are known.
- trip_type should be one of: Mixed, Business, City break, Holiday / resort, Wedding / event, Weekend.
- laundry should be No, Yes, or Possibly.
- shopping_allowed should be Yes or No.
- Put activities, dinners, meetings, venues, walking, flights etc. into activities.
- Put desired smartness/dress requirements into dress_needs.
- Put useful leftovers or constraints into notes.""",
      "stylist":"""Extract the styling request.
- request_text should preserve the user's full styling intent in natural language.
- location is the place relevant to the outfit/weather if clearly stated.
- when is the date/day/time phrase, normalized clearly where possible.
- shopping should be "owned" if they explicitly want wardrobe only, otherwise "open" when they allow suggestions.""",
      "week":"""Extract useful structure from a weekly outfit-planning brief.
- start_date should be YYYY-MM-DD only when the user gives enough information to resolve it.
- days should be 5 or 7 only when the user clearly states the planning span.
- location is the place relevant to the week's weather if stated.
- work_context should capture office, commute, WFH, school run, meetings and other day-to-day context.
- dress_needs should capture business casual, smart meetings, relaxed days or other dress requirements.
- shopping_allowed should be Yes only when the user explicitly allows a missing/new item; otherwise omit it.
- The full spoken brief is preserved separately in the main week brief, so do not try to squeeze every detail into these fields.""",
      "outfit":"""Extract the structured outfit request.
- occasion should be one of: Casual daytime, Smart casual, Dinner, Date night, Business meeting, Business casual, Wedding / event, Wedding guest, Cocktail / party, Formal evening, Work event, Daytime event, Holiday / resort, Travel day.
- dress_code: Use your judgement, Casual, Smart casual, Business casual, Business, Cocktail, Formal.
- smartness: Balanced, Relaxed, Polished but not overdressed, Smart, Very smart.
- season: Auto / current, Spring, Summer, Autumn, Winter, Transitional.
- weather: Dry, Sunny, Cloudy, Rain likely, Windy, Cold / crisp, Hot / humid.
- wardrobe_mode: Wardrobe first; suggest gaps only when useful, My wardrobe only, Open to one new piece, Open to new pieces / a new outfit.
- Keep unstructured preferences in context_notes.""",
      "shopping":"""Extract a shopping brief.
- goal is the main requested item/problem in natural language.
- budget should be exactly one of: No fixed budget, Under £100, £100–£250, £250–£500, £500+, Show me different budgets.
- season should be: Any season, Spring/Summer, Autumn/Winter, All-season.
- occasion should capture intended use.
- shopping_mode should be one of: best_addition, strict_gap, complete_outfit, upgrade_existing.
  Use best_addition unless the user explicitly asks for a strict gap, a whole/complete outfit, or to upgrade/replace something they own.""",
      "profile":"""Extract only explicitly stated personal fit/profile information.
- Measurement fields are numbers in centimetres only. Do not invent or convert unless units are clear.
- preferred_fit should be one of: Tailored / regular, Slim, Relaxed, Mixed by garment.
- General style preferences go in style_notes.
- Brand-specific sizes/fit observations go in brand_notes.
- usual_top_size, usual_bottom_size, usual_dress_size, usual_shoe_size and bra_size are strings; populate only when explicitly stated.""",
      "garment":"""Extract only garment facts the user actually states.
- Do not invent brand, material, size or model.
- fit_feedback should be one of: Unknown, Perfect fit, Slightly tight, Slightly loose, Too tight, Too loose.
- Category should use the app's established wardrobe categories when clear.
- Put any residual factual detail in notes.""",
      "profile":"""Extract only profile information explicitly stated.
- preferred_rise can capture low, mid, high or mixed rise preferences.
- preferred_hem_length can capture preferred dress/skirt/trouser lengths in the user's own words.
- heel_preference should capture practical footwear preference, e.g. flats, low heel, block heel, high heel, mixed, avoid heels.
- accessory_notes should capture stated bag, jewellery or accessory preferences.
- Do not infer body shape or invent preferences from measurements."""
    }[mode]

    prompt=f"""Turn this spoken dictation into fields for a personal stylist app.

TODAY: {today}
MODE: {mode}
ALLOWED FIELD NAMES: {', '.join(sorted(allowed[mode]))}

DICTATION:
{transcript}

Rules:
- Return only fields supported by what the user actually said.
- Never invent missing facts.
- Ignore filler speech.
- Preserve important nuance.
- Use only ALLOWED FIELD NAMES.
- Omit a field instead of returning an empty value.
{mode_guidance}
"""
    try:
        response=tracked_responses_create(OpenAI(),
            model=os.getenv("OPENAI_MODEL","gpt-5.6-terra"),
            reasoning={"effort":"low"},
            input=prompt,
            text={"format":{"type":"json_schema","name":"voice_form","schema":VOICE_FORM_SCHEMA,"strict":True}}
        )
        result=json.loads(response.output_text)
    except Exception as exc:
        raise HTTPException(502,f"I couldn't understand that dictation: {str(exc)[:180]}")

    result["fields"]=[
        f for f in result.get("fields",[])
        if f.get("field") in allowed[mode] and str(f.get("value","")).strip()
    ]
    return result

@app.post("/api/trip-context")
def trip_context(req: TripContextRequest):
    if not (req.destination or "").strip() and not (req.trip_brief or "").strip():
        raise HTTPException(400,"Tell me about the trip first.")
    if not os.getenv("OPENAI_API_KEY") or OpenAI is None:
        raise HTTPException(503,"Trip research needs the AI connection.")

    today=date.today()
    start_date=end_date=None
    try:
        if req.start_date:
            start_date=date.fromisoformat(req.start_date)
        if req.end_date:
            end_date=date.fromisoformat(req.end_date)
    except ValueError:
        raise HTTPException(400,"Please use valid trip dates.")
    if start_date and end_date and end_date < start_date:
        raise HTTPException(400,"The return date cannot be before the departure date.")

    days=max(1,min(int(req.days or 5),60))
    if start_date and end_date:
        days=(end_date-start_date).days+1

    if start_date:
        days_until=(start_date-today).days
        requested_mode="forecast" if 0 <= days_until <= 14 else "seasonal"
    elif (req.weather or "").strip():
        requested_mode="user-provided"
    else:
        requested_mode="seasonal"

    prompt=f"""Research this trip for a personal fashion packing assistant.

TODAY: {today.isoformat()}
DESTINATION: {req.destination or 'extract from trip brief if clearly stated'}
FULL TRIP BRIEF: {req.trip_brief or 'none'}
TRIP DATES: {req.start_date or 'not supplied'} to {req.end_date or 'not supplied'}
TRIP LENGTH: {days} days
TRIP TYPE: {req.trip_type}
USER WEATHER NOTE: {req.weather or 'none'}
ACTIVITIES / NAMED VENUES: {req.activities or 'none'}
DRESS NEEDS: {req.dress_needs or 'none'}
OTHER NOTES / HOTELS / RESTAURANTS / EVENTS: {req.notes or 'none'}

REQUESTED WEATHER MODE: {requested_mode}

Rules:
- Use live web search.
- If forecast mode, use current published forecasts for the destination and dates. Prefer an official meteorological service where practical; corroborate with another reputable source when useful.
- If the dates are beyond a reasonably reliable forecast window, do not pretend a forecast exists. Use seasonal/historical typical conditions for that place and time of year and set weather_mode to seasonal.
- Consider weather supplied by the user alongside web evidence.
- Identify hotels, restaurants, venues, resorts or events explicitly named in the user's text and research the exact place where possible.
- For dress context, distinguish an explicit dress code from a stylist inference based on the venue's positioning, photographs or reputable descriptions.
- Never invent a dress code. Use evidence_level verified only for an explicit source-supported requirement.
- Include practical styling context such as walking, indoor/outdoor transitions and local formality where supported.
- Return real source pages used during research.
- This is clothing and packing guidance, not safety-critical weather advice.
"""
    try:
        response=tracked_responses_create(OpenAI(),
            model=os.getenv("OPENAI_SHOPPING_MODEL",os.getenv("OPENAI_MODEL","gpt-5.6-terra")),
            reasoning={"effort":"low"},
            tools=[{"type":"web_search"}],
            tool_choice="auto",
            include=["web_search_call.action.sources"],
            input=prompt,
            text={"format":{"type":"json_schema","name":"trip_context","schema":TRIP_CONTEXT_SCHEMA,"strict":True}}
        )
        result=json.loads(response.output_text)
    except Exception as exc:
        result={
         "weather_mode":"user-provided" if req.weather else "unavailable",
         "weather_summary":req.weather or "Live weather research was unavailable.",
         "temperature_low_c":None,"temperature_high_c":None,"rain":"","wind":"",
         "packing_weather_note":"Use your written weather expectations and layer conservatively." if req.weather else "Check the forecast again closer to departure.",
         "destination_summary":f"{req.destination} — destination research was temporarily unavailable.",
         "dress_context":req.dress_needs or "","activity_context":req.activities or "",
         "named_places":[],"sources":[],
         "research_note":f"Live trip research could not complete: {str(exc)[:180]}"
        }

    result["today"]=today.isoformat()
    result["start_date"]=req.start_date
    result["end_date"]=req.end_date
    result["days"]=days
    return result


class SavedTripRequest(BaseModel):
    title: str = ""
    destination: str = ""
    start_date: str = ""
    end_date: str = ""
    luggage: str = ""
    request: dict = {}
    trip_context: dict = {}
    plan: dict = {}
    checklist: dict = {}

def saved_trip_row(row):
    d=dict(row)
    for src,dst,default in [
        ("request_json","request",{}),("context_json","trip_context",{}),
        ("plan_json","plan",{}),("checklist_json","checklist",{})
    ]:
        try:d[dst]=json.loads(d.get(src) or "{}")
        except Exception:d[dst]=default
    return d

@app.get("/api/saved-trips")
def get_saved_trips():
    con=db()
    rows=con.execute("SELECT * FROM saved_trips ORDER BY COALESCE(start_date,''), id DESC").fetchall()
    con.close()
    return [saved_trip_row(r) for r in rows]

@app.post("/api/saved-trips")
def save_trip(req: SavedTripRequest):
    title=(req.title or req.destination or "Saved trip").strip()[:140]
    con=db()
    cur=con.execute("""
      INSERT INTO saved_trips
      (title,destination,start_date,end_date,luggage,request_json,context_json,plan_json,checklist_json,updated_at)
      VALUES (?,?,?,?,?,?,?,?,?,?)
    """,(title,(req.destination or "").strip()[:140],req.start_date or "",req.end_date or "",
         (req.luggage or "").strip()[:80],json.dumps(req.request or {},ensure_ascii=False),
         json.dumps(req.trip_context or {},ensure_ascii=False),json.dumps(req.plan or {},ensure_ascii=False),
         json.dumps(req.checklist or {},ensure_ascii=False),utc_now().isoformat()))
    tid=cur.lastrowid
    con.commit()
    row=con.execute("SELECT * FROM saved_trips WHERE id=?",(tid,)).fetchone()
    con.close()
    return saved_trip_row(row)

@app.put("/api/saved-trips/{tid}")
def update_trip(tid:int, req: SavedTripRequest):
    con=db()
    exists=con.execute("SELECT id FROM saved_trips WHERE id=?",(tid,)).fetchone()
    if not exists:
        con.close(); raise HTTPException(404,"Saved trip not found.")
    title=(req.title or req.destination or "Saved trip").strip()[:140]
    con.execute("""
      UPDATE saved_trips SET title=?,destination=?,start_date=?,end_date=?,luggage=?,
       request_json=?,context_json=?,plan_json=?,checklist_json=?,updated_at=? WHERE id=?
    """,(title,(req.destination or "").strip()[:140],req.start_date or "",req.end_date or "",
         (req.luggage or "").strip()[:80],json.dumps(req.request or {},ensure_ascii=False),
         json.dumps(req.trip_context or {},ensure_ascii=False),json.dumps(req.plan or {},ensure_ascii=False),
         json.dumps(req.checklist or {},ensure_ascii=False),utc_now().isoformat(),tid))
    con.commit()
    row=con.execute("SELECT * FROM saved_trips WHERE id=?",(tid,)).fetchone()
    con.close()
    return saved_trip_row(row)

@app.delete("/api/saved-trips/{tid}")
def delete_trip(tid:int):
    con=db(); con.execute("DELETE FROM saved_trips WHERE id=?",(tid,)); con.commit(); con.close()
    return {"ok":True}

class PackingRequest(BaseModel):
    destination: str = ""
    trip_brief: str = ""
    start_date: str = ""
    end_date: str = ""
    days: int = 5
    trip_type: str = "Mixed"
    weather: str = ""
    activities: str = ""
    dress_needs: str = ""
    laundry: str = "No"
    shopping_allowed: bool = True
    luggage: str = ""
    notes: str = ""
    trip_context: dict = {}

PACKING_SCHEMA = {
 "type":"object","properties":{
  "summary":{"type":"string"},
  "capsule_strategy":{"type":"string"},
  "packing_list":{"type":"array","items":{"type":"object","properties":{
   "garment_id":{"type":"integer"},"why_pack":{"type":"string"},"wear_count":{"type":"integer"}
  },"required":["garment_id","why_pack","wear_count"],"additionalProperties":False}},
  "outfit_plan":{"type":"array","items":{"type":"object","properties":{
   "look_id":{"type":"string"},
   "day":{"type":"string"},
   "date":{"type":"string"},
   "time_of_day":{"type":"string"},
   "occasion":{"type":"string"},
   "garment_ids":{"type":"array","items":{"type":"integer"}},
   "note":{"type":"string"},
   "reuse_note":{"type":"string"}
  },"required":["look_id","day","date","time_of_day","occasion","garment_ids","note","reuse_note"],"additionalProperties":False}},
  "missing_items":{"type":"array","items":{"type":"string"}},
  "packing_tip":{"type":"string"}
 },
 "required":["summary","capsule_strategy","packing_list","outfit_plan","missing_items","packing_tip"],
 "additionalProperties":False
}

class WeekPlanRequest(BaseModel):
    start_date: str = ""
    days: int = 5
    location: str = ""
    brief: str = ""
    work_context: str = ""
    dress_needs: str = ""
    weather_context: dict = {}
    shopping_allowed: bool = False

@app.post("/api/plan-my-week")
def plan_my_week(req: WeekPlanRequest):
    con=db()
    garments=[dict(r) for r in con.execute("SELECT * FROM garments ORDER BY id DESC").fetchall()]
    profile=dict(con.execute("SELECT * FROM profile WHERE id=1").fetchone())
    feedback=[dict(r) for r in con.execute("SELECT rating,outfit_json,created_at FROM feedback ORDER BY id DESC LIMIT 30").fetchall()]
    favourites=[dict(r) for r in con.execute(
      "SELECT label,outfit_json,request_text,weather_context,wore_count,last_worn_at,is_pinned,tags_json,occasion,season,notes FROM outfit_favourites ORDER BY COALESCE(wore_count,0) DESC, id DESC LIMIT 30"
    ).fetchall()]
    con.close()
    if len(garments)<3:
        raise HTTPException(400,"Add at least three wardrobe items before planning a week.")
    if not os.getenv("OPENAI_API_KEY") or OpenAI is None:
        raise HTTPException(503,"Plan My Week needs the AI stylist connection.")

    valid_ids={g["id"] for g in garments}
    compact=[{k:g.get(k) for k in [
      "id","category","garment_type","brand","model_line","labelled_size","colour","material",
      "pattern","fit_cut","fit_feedback","season","formality"
    ]} for g in garments]
    days=max(1,min(int(req.days or 5),7))
    instructions="""You are a personal stylist planning a normal week at home/work, NOT a travel packing list.

Create one distinct, practical outfit for each requested day using the user's ACTUAL wardrobe.
The purpose is to remove daily decision-making while keeping the week coherent and varied.

Rules:
- Return exactly the requested number of day looks when wardrobe coverage allows.
- Use only supplied garment IDs.
- Respect work context, dress requirements, location/weather, fit history, saved looks and actual wear evidence.
- Reuse versatile shoes, coats or trousers where sensible, but do not make every day feel identical.
- Avoid repeating the exact same full outfit.
- Treat Saved Looks with real wear counts as stronger positive evidence than merely saved looks.
- Do not assume every workday has the same formality.
- If shopping_allowed is false, missing_items must be empty.
- If shopping_allowed is true, mention only genuine gaps, never create shopping for novelty.
- date should be YYYY-MM-DD when start_date is supplied.
- time_of_day should normally be Daytime or Workday.
- occasion should be a short useful label such as Office, Client meeting, Work from home, Casual Friday, Dinner after work.
- packing_list should contain the unique wardrobe garments used across the week; why_pack should instead explain why the item is useful in the weekly rotation.
- packing_tip should be one concise preparation tip for the week.
"""
    context={
      "week":{"start_date":req.start_date,"days":days,"location":req.location,"brief":req.brief,
              "work_context":req.work_context,"dress_needs":req.dress_needs,
              "shopping_allowed":req.shopping_allowed},
      "weather_context":req.weather_context or {},
      "profile":profile,"wardrobe":compact,"recent_feedback":feedback,"saved_looks":favourites
    }
    try:
        response=tracked_responses_create(OpenAI(),
          model=os.getenv("OPENAI_MODEL","gpt-5.6-terra"),
          reasoning={"effort":"low"},
          instructions=instructions+styling_profile_guidance(),
          input=json.dumps(context,ensure_ascii=False),
          text={"format":{"type":"json_schema","name":"weekly_outfit_plan","schema":PACKING_SCHEMA,"strict":True}}
        )
        result=json.loads(response.output_text)
    except Exception as exc:
        raise HTTPException(502,f"I couldn't plan the week: {str(exc)[:220]}")

    clean=[]
    for n,o in enumerate(result.get("outfit_plan",[]),start=1):
        ids=[]
        for value in o.get("garment_ids",[]):
            try: gid=int(value)
            except Exception: continue
            if gid in valid_ids and gid not in ids: ids.append(gid)
        if not ids: continue
        o["garment_ids"]=ids
        o["look_id"]=str(o.get("look_id") or f"week-look-{n}")
        clean.append(o)
        if len(clean)>=days: break
    result["outfit_plan"]=clean

    used={gid for o in clean for gid in o.get("garment_ids",[])}
    result["packing_list"]=[
      x for x in result.get("packing_list",[])
      if int(x.get("garment_id",0) or 0) in used
    ]
    result["week_context"]={
      "start_date":req.start_date,"days":days,"location":req.location,
      "brief":req.brief,"work_context":req.work_context,"dress_needs":req.dress_needs,
      "weather_context":req.weather_context or {}
    }
    return result


@app.post("/api/help-me-pack")
def help_me_pack(req: PackingRequest):
    con=db()
    garments=[dict(r) for r in con.execute("SELECT * FROM garments ORDER BY id DESC").fetchall()]
    profile=dict(con.execute("SELECT * FROM profile WHERE id=1").fetchone())
    feedback=[dict(r) for r in con.execute("SELECT rating,outfit_json,created_at FROM feedback ORDER BY id DESC LIMIT 30").fetchall()]
    favourites=[dict(r) for r in con.execute(
        "SELECT label,outfit_json,request_text,weather_context,wore_count,last_worn_at,is_pinned,tags_json,occasion,season,notes FROM outfit_favourites ORDER BY COALESCE(wore_count,0) DESC, id DESC LIMIT 20"
    ).fetchall()]
    con.close()

    if len(garments)<3:
        raise HTTPException(400,"Add at least three wardrobe items before using Help Me Pack.")
    if not os.getenv("OPENAI_API_KEY") or OpenAI is None:
        raise HTTPException(503,"Help Me Pack needs the AI stylist connection.")

    valid_ids={g["id"] for g in garments}
    compact=[{k:g.get(k) for k in [
     "id","category","garment_type","brand","model_line","labelled_size","colour","material",
     "pattern","fit_cut","fit_feedback","season","formality"
    ]} for g in garments]

    instructions="""You are a meticulous personal stylist and efficient travel packer.
Build a coherent capsule from the user's ACTUAL wardrobe, not unrelated outfits.

Rules:
- Use only supplied garment IDs for owned pieces.
- Never claim the user owns something absent from the wardrobe.
- Reuse versatile garments deliberately across days/occasions to reduce luggage.
- Respect researched weather, destination/venue context, activities, dates, dress needs, laundry, fit history and style feedback.
- Saved looks include wore_count. Prefer patterns proven by actual wear over saved-only ideas, while still creating enough variety for the trip.
- Treat inferred venue dress guidance as guidance, not a verified rule.
- Avoid overpacking. Respect the stated luggage allowance/size. Shoes, trousers and outer layers should earn their place by working across multiple looks where possible.
- If shopping_allowed is false, missing_items must be empty.
- If shopping_allowed is true, list a missing item only for a genuine gap.
- Each outfit_plan entry must represent ONE discrete outfit for ONE occasion/time of day.
- If one day needs different looks (for example lecture/daytime and dinner/evening), create separate outfit_plan entries for that same day/date. Never combine multiple looks into one garment_ids list or one note.
- look_id must be unique within this packing plan and stable-looking, for example "2026-10-05-evening-dinner" or "day-2-afternoon".
- time_of_day should be a simple label such as Morning, Daytime, Afternoon, Evening or Travel.
- date should be YYYY-MM-DD when exact dates are supplied; otherwise blank.
- reuse_note should make rewearing clear.
- The garment_ids array is the COMPLETE and EXACT outfit to be visualised for that one look. Do not include alternative garments in the same array.
- Do not invent weather or dress codes beyond trip_context.
"""
    context={
     "trip":{
      "destination":req.destination,"trip_brief":req.trip_brief,"start_date":req.start_date,"end_date":req.end_date,
      "days":req.days,"trip_type":req.trip_type,"user_weather":req.weather,
      "activities":req.activities,"dress_needs":req.dress_needs,"laundry":req.laundry,
      "shopping_allowed":req.shopping_allowed,"luggage":req.luggage,"notes":req.notes
     },
     "researched_trip_context":req.trip_context or {},
     "profile":profile,"wardrobe":compact,"recent_feedback":feedback,"saved_looks":favourites
    }

    try:
        response=tracked_responses_create(OpenAI(),
            model=os.getenv("OPENAI_MODEL","gpt-5.6-terra"),
            reasoning={"effort":os.getenv("OPENAI_PACK_REASONING","low")},
            instructions=instructions,
            input=json.dumps(context,ensure_ascii=False),
            text={"format":{"type":"json_schema","name":"packing_plan","schema":PACKING_SCHEMA,"strict":True}}
        )
        result=json.loads(response.output_text)
    except Exception as exc:
        raise HTTPException(502,f"I couldn't build the packing plan: {str(exc)[:220]}")

    clean_pack=[]
    for item in result.get("packing_list",[]):
        try: gid=int(item.get("garment_id"))
        except Exception: continue
        if gid in valid_ids:
            item["garment_id"]=gid
            clean_pack.append(item)
    result["packing_list"]=clean_pack

    clean_outfits=[]
    used_look_ids=set()
    for n,outfit in enumerate(result.get("outfit_plan",[]),start=1):
        ids=[]
        for value in outfit.get("garment_ids",[]):
            try: gid=int(value)
            except Exception: continue
            if gid in valid_ids and gid not in ids:
                ids.append(gid)
        if not ids:
            continue

        outfit["garment_ids"]=ids
        base_look_id=re.sub(r"[^a-z0-9_-]+","-",str(outfit.get("look_id") or f"look-{n}").strip().lower()).strip("-") or f"look-{n}"
        look_id=base_look_id
        suffix=2
        while look_id in used_look_ids:
            look_id=f"{base_look_id}-{suffix}"
            suffix+=1
        used_look_ids.add(look_id)
        outfit["look_id"]=look_id
        outfit["time_of_day"]=str(outfit.get("time_of_day") or "").strip()
        clean_outfits.append(outfit)
    result["outfit_plan"]=clean_outfits
    result["trip_context"]=req.trip_context or {}
    return result

@app.get("/api/model-photos")
def get_model_photos():
    con = db()
    rows = [dict(r) for r in con.execute("SELECT * FROM model_photos ORDER BY id ASC").fetchall()]
    con.close()
    return rows

@app.post("/api/model-photos")
async def add_model_photo(file: UploadFile = File(...), label: str = Form("")):
    suffix = Path(file.filename or "portrait").suffix.lower() or ".upload"
    raw_path = model_photos_dir() / f"{uuid.uuid4().hex}{suffix}"
    data = await file.read()
    if not data:
        raise HTTPException(400, "The uploaded photo was empty.")
    if len(data) > 15 * 1024 * 1024:
        raise HTTPException(400, "That photo is too large. Please choose an image under 15 MB.")
    raw_path.write_bytes(data)
    image_path = normalise_image_for_ai(raw_path)
    if image_path.parent != model_photos_dir():
        target = model_photos_dir() / image_path.name
        target.write_bytes(image_path.read_bytes())
        image_path = target
    con = db()
    cur = con.execute("INSERT INTO model_photos(image_path,label) VALUES (?,?)",
                      (f"/model-photos/{image_path.name}", label or ""))
    con.commit()
    pid = cur.lastrowid
    con.close()
    return {"ok": True, "id": pid, "image_path": f"/model-photos/{image_path.name}"}

@app.delete("/api/model-photos/{photo_id}")
def delete_model_photo(photo_id: int):
    con = db()
    row = con.execute("SELECT image_path FROM model_photos WHERE id=?", (photo_id,)).fetchone()
    con.execute("DELETE FROM model_photos WHERE id=?", (photo_id,))
    con.commit()
    con.close()
    if row:
        p = model_photos_dir() / Path(row["image_path"]).name
        try:
            if p.exists():
                p.unlink()
        except Exception:
            pass
    return {"ok": True}

class OutfitVisualisationRequest(BaseModel):
    garment_ids: list[int]
    label: Optional[str] = "Outfit"
    reason: Optional[str] = ""
    occasion: Optional[str] = ""
    temperature_c: Optional[float] = None
    use_my_likeness: Optional[bool] = False
    requested_extra_piece: Optional[str] = ""

@app.post("/api/outfit-visualisation")
def outfit_visualisation(req: OutfitVisualisationRequest):
    enforce_beta_image_limit()
    if not os.getenv("OPENAI_API_KEY") or OpenAI is None:
        raise HTTPException(400, "OpenAI image generation is not connected.")

    ids = [int(x) for x in req.garment_ids if isinstance(x, int) or str(x).isdigit()]
    if not ids:
        raise HTTPException(400, "This outfit does not contain any saved garments.")

    con = db()
    placeholders = ",".join("?" for _ in ids)
    rows = [dict(r) for r in con.execute(
        f"SELECT * FROM garments WHERE id IN ({placeholders})", ids
    ).fetchall()]
    profile = dict(con.execute("SELECT * FROM profile WHERE id=1").fetchone())
    model_photos = [dict(r) for r in con.execute(
        "SELECT * FROM model_photos ORDER BY id ASC LIMIT 4"
    ).fetchall()]
    con.close()

    if not rows:
        raise HTTPException(404, "The outfit garments could not be found.")

    # Preserve outfit order supplied by the client.
    by_id = {g["id"]: g for g in rows}
    garments = [by_id[i] for i in ids if i in by_id]

    descriptions = []
    garment_image_files = []
    for n, g in enumerate(garments, start=1):
        descriptions.append(
            f"{n}. {g.get('brand') or ''} {g.get('garment_type') or g.get('category') or 'garment'}; "
            f"colour: {g.get('colour') or 'unknown'}; material: {g.get('material') or 'unknown'}; "
            f"pattern: {g.get('pattern') or 'none/unknown'}; fit: {g.get('fit_cut') or 'unknown'}."
        )
        rel = g.get("image_path") or ""
        p = resolve_saved_image_path(rel)
        if p.exists():
            garment_image_files.append(p)

    likeness_files = []
    if req.use_my_likeness:
        for mp in model_photos:
            p = model_photos_dir() / Path(mp.get("image_path") or "").name
            if p.exists():
                likeness_files.append(p)
        if not likeness_files:
            raise HTTPException(400, "Add at least one photo in My Model before using View on me.")

    height = profile.get("height_cm")
    preferred_fit = profile.get("preferred_fit") or "natural contemporary fit"
    style_notes = profile.get("style_notes") or ""

    prompt = f"""
Create a photorealistic full-body {fashion_audience()} fashion lookbook image showing one {fashion_person()}
wearing the outfit represented by the supplied garment reference images.

OUTFIT:
{chr(10).join(descriptions)}

Recommended extra piece not yet owned:
{req.requested_extra_piece or 'none'}

Context:
- Outfit label: {req.label or 'Outfit'}
- Occasion: {req.occasion or 'general smart/casual use'}
- Approximate temperature: {req.temperature_c if req.temperature_c is not None else 'not specified'} C
- Preferred fit: {preferred_fit}
- User style notes: {style_notes}
- User height, if supplied: {height or 'not supplied'} cm

Important:
- This request represents ONE outfit only. Do not combine it with another look or introduce alternative versions of any garment.
- Use exactly the supplied outfit garments as the clothing brief. Each saved garment reference belongs to this one look only.
- Use the reference garment images as closely as reasonably possible for colour, material, silhouette, pattern and footwear.
- Do not swap colours between garments, merge two garments into one, or substitute a different top/trouser/jacket because another reference looks similar.
- If multiple upper-body pieces are supplied because the outfit is layered, show them as distinct layers rather than blending their details together.
- Do not add visible logos or brand marks that are not clearly present in the reference images.
- Do not invent extra statement garments.
- If a small neutral accessory is needed for realism, keep it unobtrusive.
- Show the entire outfit head-to-toe, including footwear.
- Natural standing pose, premium contemporary fashion editorial photography.
- Neutral understated studio or softly lit architectural background.
- If use_my_likeness is true, use the supplied personal reference photos to preserve the user's visible identity, face, hair, skin tone and overall proportions as closely as reasonably possible.
- If use_my_likeness is false, use a generic {fashion_person()} who does not resemble any particular real person.
- This is a styling visualisation, not a claim of exact garment fit.
"""
    prompt += f"\nuse_my_likeness: {bool(req.use_my_likeness)}\n"


    client = OpenAI()
    image_model = os.getenv("OPENAI_IMAGE_MODEL", "gpt-image-2")
    result = None

    # First choice: use the saved garment photographs as high-fidelity visual references.
    opened = []
    try:
        reference_files = []
        if req.use_my_likeness:
            likeness_limit=max(1,min(int(os.getenv("OUTFIT_LIKENESS_REFS","2")),3))
            reference_files.extend(likeness_files[:likeness_limit])
        reference_files.extend(garment_image_files[:5])
        if reference_files:
            opened = [open(p, "rb") for p in reference_files[:7]]
            result = client.images.edit(
                model=image_model,
                image=opened,
                prompt=prompt,
                size="1024x1536",
                quality="medium"
            )
        else:
            result = client.images.generate(
                model=image_model,
                prompt=prompt,
                size="1024x1536",
                quality="medium"
            )
    except Exception as first_exc:
        # Safe fallback: if multi-image editing is unavailable to this account/SDK,
        # create a visual from the stored garment metadata rather than failing outright.
        try:
            result = client.images.generate(
                model=image_model,
                prompt=prompt,
                size="1024x1536",
                quality="medium"
            )
        except Exception as second_exc:
            raise HTTPException(
                status_code=502,
                detail=f"Outfit visualisation failed: {str(second_exc)[:300]}"
            ) from second_exc
    finally:
        for f in opened:
            try:
                f.close()
            except Exception:
                pass

    if not result or not getattr(result, "data", None):
        raise HTTPException(502, "The image model did not return an image.")

    item = result.data[0]
    b64 = getattr(item, "b64_json", None)
    if not b64:
        raise HTTPException(502, "The image model returned an unsupported image response.")

    filename = f"outfit_{uuid.uuid4().hex}.png"
    out_path = generated_dir() / filename
    out_path.write_bytes(base64.b64decode(b64))
    record_usage_event("image_generation","/api/outfit-visualisation",1,metadata={
      "model":image_model,"size":"1024x1536","quality":"medium",
      "reference_images":len(reference_files[:7]) if 'reference_files' in locals() else 0
    })

    return {
        "ok": True,
        "image_path": f"/generated/{filename}",
        "label": req.label or "Outfit",
        "notice": ("AI personalised outfit visualisation — intended to show the overall look on you, not exact fit or exact garment reproduction."
                   if req.use_my_likeness else
                   "AI outfit visualisation — useful for judging the overall look, not exact fit or garment reproduction.")
    }


class WardrobeGapRequest(BaseModel):
    goal: Optional[str] = ""
    budget: Optional[str] = ""
    occasion: Optional[str] = ""
    season: Optional[str] = ""
    shopping_mode: Optional[str] = "best_addition"
    max_recommendations: Optional[int] = 4

GAP_SCHEMA = {
  "type": "object",
  "properties": {
    "summary": {"type": "string"},
    "recommendations": {
      "type": "array",
      "minItems": 1,
      "maxItems": 5,
      "items": {
        "type": "object",
        "properties": {
          "title": {"type": "string"},
          "category": {"type": "string"},
          "ideal_colour": {"type": "string"},
          "ideal_material": {"type": "string"},
          "ideal_fit": {"type": "string"},
          "formality": {"type": "string"},
          "why_this_adds_value": {"type": "string"},
          "purchase_role": {"type": "string", "enum": ["new capability","versatility boost","occasion gap","upgrade","complete outfit","replacement"]},
          "duplicate_risk": {"type": "string", "enum": ["low","medium","high"]},
          "duplicate_reason": {"type": "string"},
          "versatility_note": {"type": "string"},
          "wardrobe_synergy_score": {"type": "integer", "minimum": 0, "maximum": 100},
          "owned_garment_ids": {"type": "array", "items": {"type": "integer"}},
          "outfit_ideas": {
            "type": "array",
            "minItems": 1,
            "maxItems": 4,
            "items": {
              "type": "object",
              "properties": {
                "owned_garment_ids": {"type": "array", "items": {"type": "integer"}},
                "description": {"type": "string"}
              },
              "required": ["owned_garment_ids", "description"],
              "additionalProperties": False
            }
          },
          "size_fit_guidance": {"type": "string"},
          "shopping_spec": {"type": "string"},
          "search_phrase": {"type": "string"},
          "priority": {"type": "string", "enum": ["high", "medium", "low"]}
        },
        "required": [
          "title","category","ideal_colour","ideal_material","ideal_fit","formality",
          "why_this_adds_value","purchase_role","duplicate_risk","duplicate_reason","versatility_note",
          "wardrobe_synergy_score","owned_garment_ids",
          "outfit_ideas","size_fit_guidance","shopping_spec","search_phrase","priority"
        ],
        "additionalProperties": False
      }
    }
  },
  "required": ["summary", "recommendations"],
  "additionalProperties": False
}

SHOPPING_STYLIST_INSTRUCTIONS = """You are the wardrobe-planning and shopping specialist for one user.

Your job is not to recommend random fashionable products. Analyse the user's ACTUAL wardrobe,
measurements, fit history, brand notes, and style feedback, then identify purchases that add the most value.

PRINCIPLES:
- Wardrobe first. Do not recommend replacing something the user already owns unless there is a clear reason.
- Explicitly check for duplication. Compare category, colour, material, fit/cut, formality and use-case against owned pieces.
- Maximise wardrobe utility, not novelty: favour purchases that solve a real gap or make many existing pieces easier to wear.
- A low duplicate-risk item should add a genuinely new capability, useful contrast, fit solution, season, formality level or outfit role.
- A medium/high duplicate-risk item can still be valid only when it is a purposeful upgrade/replacement or materially better for the user's stated use.
- Maximise wardrobe synergy: favour a purchase that creates many strong outfits with existing pieces.
- Respect the user's requested goal. If they ask for a blazer, recommend the best blazer specification rather than changing category.
- Be specific about shade, fabric, texture, construction, seasonality, formality and fit.
- Use only supplied wardrobe garment IDs when referencing owned items.
- Never claim a live product, price, stock level or retailer availability unless live retailer data is actually supplied.
- For size guidance, combine body measurements, brand/model notes and perfect-fit garment history, but express uncertainty clearly.
- Produce recommendations that are meaningfully different from one another.
- The shopping_spec should be precise enough to search retailers later.
- search_phrase should be concise and useful for a future live shopping search.
- Actual wear evidence is stronger than saved-only looks when deciding whether the user really uses a style or garment combination.
- Do not call a category a gap merely because it is underrepresented if the user's real-wear evidence shows they rarely choose that type.
- purchase_role must describe what job the purchase does in the wardrobe.
- duplicate_reason must name the closest overlap or explain why overlap is low.
- versatility_note should explain the practical breadth of use without inventing numeric outfit counts.
- If the requested shopping mode is "complete_outfit", recommendations may form a coordinated small set but should still reuse owned wardrobe pieces wherever sensible.
- If shopping mode is "upgrade_existing", only suggest upgrades where the existing wardrobe data gives a defensible reason.
- If shopping mode is "strict_gap", reject weak additions rather than filling the list with low-value purchases.
"""

@app.post("/api/wardrobe-gaps")
def wardrobe_gaps(req: WardrobeGapRequest):
    con = db()
    garment_rows = [dict(r) for r in con.execute("SELECT * FROM garments ORDER BY id DESC").fetchall()]
    profile_row = dict(con.execute("SELECT * FROM profile WHERE id=1").fetchone())
    feedback_rows = [dict(r) for r in con.execute(
        "SELECT rating, outfit_json FROM feedback ORDER BY id DESC LIMIT 20"
    ).fetchall()]
    favourite_rows = [dict(r) for r in con.execute(
        "SELECT label,outfit_json,wore_count,last_worn_at,is_pinned,tags_json,occasion,season,notes FROM outfit_favourites ORDER BY COALESCE(wore_count,0) DESC, id DESC LIMIT 20"
    ).fetchall()]
    con.close()

    # Keep the analysis prompt lean as the wardrobe grows. We deliberately exclude
    # image paths, research blobs, purchase metadata and other fields that do not
    # help decide wardrobe gaps but substantially increase prompt size.
    garments = [{
        "id": g.get("id"),
        "category": g.get("category") or "",
        "garment_type": g.get("garment_type") or "",
        "brand": g.get("brand") or "",
        "model_line": g.get("model_line") or "",
        "labelled_size": g.get("labelled_size") or "",
        "colour": g.get("colour") or "",
        "material": g.get("material") or "",
        "pattern": g.get("pattern") or "",
        "fit_cut": g.get("fit_cut") or "",
        "fit_feedback": g.get("fit_feedback") or "",
        "season": g.get("season") or "",
        "formality": g.get("formality") or "",
        "fit_rating": g.get("fit_rating"),
        "fit_notes": g.get("fit_notes") or "",
        "purchase_status": g.get("purchase_status") or "",
        "purchase_price": g.get("purchase_price") or ""
    } for g in garment_rows]

    profile = {
        k: profile_row.get(k)
        for k in [
            "height_cm","chest_cm","waist_cm","hips_cm","thigh_cm",
            "inseam_cm","sleeve_cm","neck_cm","preferred_fit",
            "style_notes","brand_notes","usual_top_size","usual_bottom_size",
            "usual_dress_size","usual_shoe_size","bra_size","preferred_rise","preferred_hem_length","heel_preference","accessory_notes"
        ]
        if k in profile_row
    }

    # Recent feedback is useful, but only pass compact summaries.
    feedback = []
    for row in feedback_rows:
        item = {"rating": row.get("rating")}
        try:
            parsed = json.loads(row.get("outfit_json") or "{}")
            item["garment_ids"] = parsed.get("garment_ids") or parsed.get("owned_garment_ids") or []
            item["label"] = parsed.get("label") or ""
        except Exception:
            item["garment_ids"] = []
            item["label"] = ""
        feedback.append(item)

    saved_looks=[]
    for row in favourite_rows:
        try:
            parsed=json.loads(row.get("outfit_json") or "{}")
            saved_looks.append({
              "label":row.get("label") or parsed.get("label") or "",
              "garment_ids":parsed.get("owned_garment_ids") or parsed.get("garment_ids") or [],
              "wore_count":int(row.get("wore_count") or 0),
              "last_worn_at":row.get("last_worn_at"),
              "is_pinned":bool(row.get("is_pinned")),
              "occasion":row.get("occasion") or "",
              "season":row.get("season") or ""
            })
        except Exception:
            pass

    if not garments:
        raise HTTPException(400, "Add some wardrobe items first so I can identify useful gaps.")

    if not os.getenv("OPENAI_API_KEY") or OpenAI is None:
        raise HTTPException(400, "OpenAI is not connected.")

    max_recs = max(1, min(int(req.max_recommendations or 4), 5))
    context = {
      "goal": req.goal or "Identify the most useful additions to this wardrobe",
      "budget": req.budget or "not specified",
      "occasion": req.occasion or "not specified",
      "season": req.season or "not specified",
      "shopping_mode": req.shopping_mode or "best_addition",
      "max_recommendations": max_recs,
      "profile": profile,
      "wardrobe": garments,
      "recent_feedback": feedback,
      "saved_looks": saved_looks
    }

    client = OpenAI()
    response = tracked_responses_create(client,
      model=os.getenv("OPENAI_MODEL","gpt-5.6-terra"),
      reasoning={"effort":"low"},
      instructions=SHOPPING_STYLIST_INSTRUCTIONS + styling_profile_guidance(),
      input=json.dumps(context, ensure_ascii=False),
      text={"format":{
        "type":"json_schema",
        "name":"wardrobe_gap_recommendations",
        "schema":GAP_SCHEMA,
        "strict":True
      }}
    )
    result = json.loads(response.output_text)
    result["recommendations"] = result.get("recommendations", [])[:max_recs]
    return result




def canonicalise_analysis_category(analysis: dict) -> dict:
    if not isinstance(analysis, dict):
        return analysis
    analysis["category"] = canonical_wardrobe_category(
        analysis.get("category",""), analysis.get("garment_type",""),
        analysis.get("model_line",""), analysis.get("fit_cut",""), analysis.get("notes",""),
        analysis.get("brand",""), analysis.get("material","")
    )
    return analysis

PRODUCT_URL_IMPORT_SCHEMA = {
  "type":"object","properties":{
    "category":{"type":"string"},"garment_type":{"type":"string"},"brand":{"type":"string"},"model_line":{"type":"string"},"labelled_size":{"type":"string"},"colour":{"type":"string"},"material":{"type":"string"},"pattern":{"type":"string"},"fit_cut":{"type":"string"},"season":{"type":"string"},"formality":{"type":"string"},"notes":{"type":"string"},"confidence":{"type":"number","minimum":0,"maximum":1}},
  "required":["category","garment_type","brand","model_line","labelled_size","colour","material","pattern","fit_cut","season","formality","notes","confidence"],"additionalProperties":False}

class ProductUrlImportRequest(BaseModel): url: str

def _public_http_url(url: str) -> bool:
    try:
        import socket, ipaddress
        from urllib.parse import urlparse
        p=urlparse(url)
        if p.scheme not in ("http","https") or not p.hostname:return False
        for info in socket.getaddrinfo(p.hostname,p.port or (443 if p.scheme=="https" else 80)):
            ip=ipaddress.ip_address(info[4][0])
            if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved or ip.is_multicast or ip.is_unspecified:return False
        return True
    except Exception:return False

class _PublicOnlyRedirectHandler(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        if not _public_http_url(newurl):
            raise urllib.error.URLError("Blocked unsafe redirect target.")
        return super().redirect_request(req,fp,code,msg,headers,newurl)

def _safe_urlopen(request, timeout=12):
    target=request.full_url if hasattr(request,"full_url") else str(request)
    if not _public_http_url(target):
        raise urllib.error.URLError("Blocked unsafe URL.")
    opener=urllib.request.build_opener(_PublicOnlyRedirectHandler())
    response=opener.open(request,timeout=timeout)
    final_url=response.geturl()
    if not _public_http_url(final_url):
        response.close()
        raise urllib.error.URLError("Blocked unsafe redirect target.")
    return response

def _fetch_product_page_meta(url: str) -> dict:
    if not _public_http_url(url):raise HTTPException(400,"Please use a normal public retailer product URL.")
    try:
        import urllib.request, html as _html, re as _re
        from urllib.parse import urljoin
        req=urllib.request.Request(url,headers={"User-Agent":"Mozilla/5.0","Accept":"text/html,application/xhtml+xml"})
        with _safe_urlopen(req,timeout=12) as r:
            if "text/html" not in (r.headers.get("Content-Type") or "").lower():raise HTTPException(400,"That link does not appear to be a retailer product page.")
            raw=r.read(1200000).decode("utf-8","ignore")
        def mv(keys):
            for key in keys:
                for pat in [rf'<meta[^>]+(?:property|name)=["\\\']{_re.escape(key)}["\\\'][^>]+content=["\\\']([^"\\\']+)["\\\']',rf'<meta[^>]+content=["\\\']([^"\\\']+)["\\\'][^>]+(?:property|name)=["\\\']{_re.escape(key)}["\\\']']:
                    m=_re.search(pat,raw,_re.I)
                    if m:return _html.unescape(m.group(1).strip())
            return ""
        title=mv(["og:title","twitter:title"])
        if not title:
            m=_re.search(r"<title[^>]*>(.*?)</title>",raw,_re.I|_re.S)
            if m:title=_html.unescape(_re.sub(r"<[^>]+>"," ",m.group(1))).strip()
        description=mv(["og:description","description","twitter:description"])
        image=mv(["og:image:secure_url","og:image","twitter:image","twitter:image:src"])
        if image:image=urljoin(url,image)
        text=_re.sub(r"(?is)<script.*?</script>|<style.*?</style>"," ",raw); text=_re.sub(r"(?s)<[^>]+>"," ",text); text=_html.unescape(_re.sub(r"\s+"," ",text)).strip()[:18000]
        return {"title":title,"description":description,"image_url":image,"page_text":text}
    except HTTPException:
        raise
    except Exception as exc:
        return {"title":"","description":"","image_url":"","page_text":"","direct_fetch_error":str(exc)[:220]}

def _download_import_image(image_url: str) -> tuple[str,str]:
    if not image_url or not _public_http_url(image_url):return "",""
    try:
        import urllib.request
        req=urllib.request.Request(image_url,headers={"User-Agent":"Mozilla/5.0","Accept":"image/*"})
        with _safe_urlopen(req,timeout=12) as r:
            ctype=(r.headers.get("Content-Type") or "").lower()
            if not ctype.startswith("image/"):return "",""
            data=r.read(12*1024*1024)
        if not data:return "",""
        suffix=".png" if "png" in ctype else ".webp" if "webp" in ctype else ".jpg"
        raw=uploads_dir() /f"urlimport_{uuid.uuid4().hex}{suffix}"; raw.write_bytes(data); normal=normalise_image_for_ai(raw); catalogue=create_catalogue_image(normal)
        display=f"/cleaned/{catalogue.name}" if catalogue.parent==cleaned_dir() else f"/uploads/{normal.name}"
        return display,f"/uploads/{normal.name}"
    except Exception:return "",""

@app.post("/api/import-product-url")
def import_product_url(req: ProductUrlImportRequest):
    url=(req.url or "").strip()
    if not _public_http_url(url):
        raise HTTPException(400,"Please use a normal public retailer product URL.")

    meta=_fetch_product_page_meta(url)
    direct_blocked=bool(meta.get("direct_fetch_error"))
    analysis=None
    import_method="direct_page"

    if os.getenv("OPENAI_API_KEY") and OpenAI is not None:
        client=OpenAI()

        if not direct_blocked and (meta.get("title") or meta.get("page_text")):
            prompt=f"""Extract the fashion product details from this retailer page. The user owns or has bought the item.
URL: {url}
PAGE TITLE: {meta.get('title') or ''}
PAGE DESCRIPTION: {meta.get('description') or ''}
PAGE TEXT EXCERPT:
{meta.get('page_text') or ''}

Use the retailer page as the factual source.
- Never guess brand, model, fabric composition, colour or pattern.
- labelled_size should normally be empty because the page cannot establish which size the user owns.
- For fit_cut, prefer an explicit retailer fit description; otherwise provide a conservative stylist classification only when the page's cut/silhouette description supports it.
- season may be classified from the known garment type and evidenced material.
- formality may be classified from the garment's known type and design.
- Keep notes factual and concise.
- For category, classify by wardrobe function rather than literal naming. A short-sleeve knitted polo is Polos & T-Shirts; a long-sleeve knitted polo/pullover or rugby shirt is Knitwear; an overshirt is Overshirts & Shirt Jackets; a sweatshirt is Sweatshirts & Hoodies."""
            try:
                response=tracked_responses_create(client,
                    model=os.getenv("OPENAI_MODEL","gpt-5.6-terra"),
                    reasoning={"effort":"low"},
                    input=prompt,
                    text={"format":{"type":"json_schema","name":"product_url_import","schema":PRODUCT_URL_IMPORT_SCHEMA,"strict":True}}
                )
                analysis=json.loads(response.output_text)
            except Exception:
                analysis=None

        if analysis is None:
            import_method="web_search_fallback"
            search_prompt=f"""Identify the exact fashion product represented by this retailer URL and extract supported facts from the live web.

EXACT PRODUCT URL:
{url}

The retailer may block direct server access. Use live web search, prioritising the official brand/retailer result and reliable indexed snippets.

Rules:
- Search specifically for the exact product name/slug, official brand result, retailer snippets and reputable stockists/reviews where useful.
- Brand, model/line, colour, material and pattern must be supported by web evidence. Never invent those fields.
- labelled_size must be empty because the URL does not establish which size the user owns.
- fit_cut: use the retailer/brand's stated fit when available. If not stated but the cut is reasonably classifiable from reliable product descriptions, provide a concise stylist classification.
- season: classify practical seasonality from the known garment type and evidenced material (for example "Spring/Summer" or "Year-round"). This is a stylist classification, not a retailer claim.
- formality: classify the garment's normal clothing formality from its known type/design (for example "Casual", "Smart casual", "Business casual", "Formal"). This is a stylist classification.
- If material cannot be established from a reliable web result, leave material empty rather than guessing.
- category should reflect wardrobe function, not merely the retailer's noun: short-sleeve knitted polos are Polos & T-Shirts; long-sleeve knitted polos/pullovers and rugby shirts are Knitwear; sweatshirts are Sweatshirts & Hoodies; overshirts are Overshirts & Shirt Jackets; true buttoned shirts are Shirts.
- garment_type should still describe the exact item using the retailer's wording where useful.
- notes should be short and factual; if season/formality/fit_cut are stylist classifications, do not describe them as retailer-provided facts.
- If exact product identification is uncertain, leave uncertain factual fields empty and use low confidence.
"""
            try:
                response=tracked_responses_create(client,
                    model=os.getenv("OPENAI_SHOPPING_MODEL",os.getenv("OPENAI_MODEL","gpt-5.6-terra")),
                    reasoning={"effort":"medium"},
                    tools=[{"type":"web_search"}],
                    tool_choice="auto",
                    include=["web_search_call.action.sources"],
                    input=search_prompt,
                    text={"format":{"type":"json_schema","name":"product_url_import","schema":PRODUCT_URL_IMPORT_SCHEMA,"strict":True}}
                )
                analysis=json.loads(response.output_text)
            except Exception as exc:
                raise HTTPException(502,f"I couldn't identify that product from the live web either: {str(exc)[:220]}")

    if analysis is None:
        from urllib.parse import urlparse
        slug=urlparse(url).path.rstrip("/").split("/")[-1].replace("-"," ").strip()
        analysis={"category":"Other","garment_type":slug.title() or "Imported product","brand":"","model_line":"","labelled_size":"","colour":"","material":"","pattern":"","fit_cut":"","season":"","formality":"","notes":"Imported from retailer product URL.","confidence":0}
        import_method="url_only"

    analysis=canonicalise_analysis_category(analysis)
    display,original=_download_import_image(meta.get("image_url") or "")
    return {
        "ok":True,"source_url":url,"image_path":display,"original_image_path":original,
        "image_available":bool(display),"analysis":analysis,"page_title":meta.get("title") or "",
        "import_method":import_method,"direct_page_blocked":direct_blocked
    }


class ProductSourceRequest(BaseModel):
    search_phrase: str
    shopping_spec: Optional[str] = ""
    budget: Optional[str] = ""
    category: Optional[str] = ""
    size_fit_guidance: Optional[str] = ""
    owned_garment_ids: Optional[list[int]] = []

PRODUCT_SOURCE_SCHEMA = {
  "type": "object",
  "properties": {
    "products": {
      "type": "array",
      "maxItems": 6,
      "items": {
        "type": "object",
        "properties": {
          "name": {"type": "string"},
          "brand": {"type": "string"},
          "retailer": {"type": "string"},
          "price": {"type": "string"},
          "url": {"type": "string"},
          "image_url": {"type": "string"},
          "colour": {"type": "string"},
          "material": {"type": "string"},
          "fit": {"type": "string"},
          "size_note": {"type": "string"},
          "why_it_matches": {"type": "string"},
          "wardrobe_utility": {"type": "string"},
          "duplicate_risk": {"type": "string", "enum": ["low","medium","high"]},
          "fit_confidence": {"type": "string", "enum": ["high","medium","low"]},
          "best_with_owned_ids": {"type": "array", "items": {"type": "integer"}, "maxItems": 6},
          "audience": {"type": "string", "enum": ["menswear","womenswear","unisex","uncertain"]},
          "audience_evidence": {"type": "string"},
          "confidence": {"type": "string", "enum": ["high","medium","low"]}
        },
        "required": ["name","brand","retailer","price","url","image_url","colour","material","fit","size_note",
                     "why_it_matches","wardrobe_utility","duplicate_risk","fit_confidence","best_with_owned_ids",
                     "audience","audience_evidence","confidence"],
        "additionalProperties": False
      }
    },
    "search_note": {"type": "string"}
  },
  "required": ["products","search_note"],
  "additionalProperties": False
}

@app.post("/api/source-products")
def source_products(req: ProductSourceRequest):
    if not os.getenv("OPENAI_API_KEY") or OpenAI is None:
        raise HTTPException(400, "OpenAI is not connected.")

    fit_evidence=fit_evidence_snapshot(limit=80)
    fit_rows=fit_evidence["confirmed"]
    shop_con=db()
    wardrobe_rows=[dict(r) for r in shop_con.execute("""
      SELECT id,category,garment_type,brand,model_line,colour,material,fit_cut,formality,season,labelled_size,fit_feedback
      FROM garments ORDER BY id DESC LIMIT 180
    """).fetchall()]
    shop_con.close()
    requested_owned={int(x) for x in (req.owned_garment_ids or []) if isinstance(x,int) or str(x).isdigit()}
    compact_wardrobe=[
      {k:g.get(k) for k in ["id","category","garment_type","brand","model_line","colour","material","fit_cut","formality","season","labelled_size","fit_feedback"]}
      for g in wardrobe_rows
    ]
    preferred_owned=[g for g in compact_wardrobe if g.get("id") in requested_owned]
    fit_learning="\n".join([
      f"- {r.get('brand') or ''} {r.get('model_line') or r.get('garment_type') or r.get('category') or ''}, "
      f"size {r.get('labelled_size') or ''}: {r.get('fit_rating') or 'n/a'}/5; "
      f"chest/bust {r.get('fit_chest') or '—'}, waist {r.get('fit_waist') or '—'}, hips {r.get('fit_hips') or '—'}, "
      f"length {r.get('fit_length') or '—'}, sleeve {r.get('fit_sleeve') or '—'}, "
      f"shoulders {r.get('fit_shoulders') or '—'}. {r.get('fit_notes') or ''}"
      for r in fit_rows[:40]
    ])
    brand_patterns=json.dumps(fit_evidence["brands"][:12],ensure_ascii=False)
    user_measurements=json.dumps({k:fit_evidence["profile"].get(k) for k in [
      "height_cm","chest_cm","waist_cm","hips_cm","thigh_cm","inseam_cm","sleeve_cm","neck_cm","preferred_fit",
      "usual_top_size","usual_bottom_size","usual_dress_size","usual_shoe_size","bra_size","preferred_rise","preferred_hem_length","heel_preference","accessory_notes"
    ]},ensure_ascii=False)

    prompt = f"""
Search the live web for {fashion_audience()} clothing products currently offered by reputable retailers that match this specification.

SEARCH PHRASE: {req.search_phrase}
CATEGORY: {req.category or 'not specified'}
SHOPPING SPECIFICATION: {req.shopping_spec or 'not specified'}
BUDGET: {req.budget or 'not specified'}
SIZE/FIT GUIDANCE: {req.size_fit_guidance or 'not specified'}

CONFIRMED REAL-WORLD FIT HISTORY:
{fit_learning or 'No confirmed fit reviews yet.'}

AGGREGATED BRAND PATTERNS:
{brand_patterns}

USER MEASUREMENTS / PREFERRED FIT:
{user_measurements}

FULL OWNED WARDROBE SUMMARY:
{json.dumps(compact_wardrobe,ensure_ascii=False)}

OWNED PIECES THIS RECOMMENDATION IS INTENDED TO WORK WITH:
{json.dumps(preferred_owned,ensure_ascii=False)}

The user is in the United Kingdom. Prefer UK retailer/product pages and GBP prices.
Find up to 6 genuinely relevant products across useful price points where possible.

Rules:
- Only return a product if you found a real product or retailer page for it on the live web.
- AUDIENCE IS A HARD REQUIREMENT. The signed-in account is {styling_profile()}.
- audience must be one of menswear, womenswear, unisex, uncertain.
- For a menswear account, only menswear or genuinely unisex products are acceptable.
- For a womenswear account, only womenswear or genuinely unisex products are acceptable.
- Do NOT infer audience just from a generic garment noun such as cardigan, coat, knitwear, trousers or trainers.
- Determine audience from explicit retailer navigation/category, product title/copy, breadcrumb, brand product section, model context or other source evidence.
- If source evidence is mixed or insufficient, set audience to uncertain. The server will discard it.
- audience_evidence must briefly state the source signal used, e.g. "retailer WOMEN category", "Men > Knitwear breadcrumb", or "explicitly unisex product".
- URL must be the actual source/product URL you found; never invent a URL.
- Never invent price, stock, material, fit or sizing. If not found, return an empty string for that field.
- image_url is optional in practice: only return it when a direct usable product image URL is explicitly available in the search result/source; otherwise return an empty string.
- Do not claim a size is in stock unless the source explicitly establishes it.
- size_note should give the most defensible starting size/fit guidance from the user's REAL fit history plus the current product/line evidence.
- Never generalise one garment to an entire brand. If the exact line differs, explicitly say that.
- When there is insufficient evidence, say sizing needs confirmation rather than guessing.
- Compare every live product with the owned wardrobe before calling it useful.
- duplicate_risk should reflect genuine similarity to what the user already owns, not just same broad category.
- wardrobe_utility should explain what the product unlocks or improves with existing clothes.
- best_with_owned_ids may contain only real IDs from FULL OWNED WARDROBE SUMMARY.
- fit_confidence should reflect the user's personal fit history plus exact current product/line evidence, not general confidence in the web search.
- Prefer official brand or retailer product pages over aggregators.
"""

    client = OpenAI()
    try:
        response = tracked_responses_create(client,
            model=os.getenv("OPENAI_SHOPPING_MODEL", os.getenv("OPENAI_MODEL","gpt-5.6-terra")),
            reasoning={"effort":"medium"},
            tools=[{"type":"web_search"}],
            tool_choice="auto",
            include=["web_search_call.action.sources"],
            input=prompt,
            text={"format":{
                "type":"json_schema",
                "name":"live_product_results",
                "schema":PRODUCT_SOURCE_SCHEMA,
                "strict":True
            }}
        )
        result=json.loads(response.output_text)
        expected="womenswear" if is_womenswear() else "menswear"
        products=result.get("products") or []
        kept=[]
        filtered=[]
        for product in products:
            audience=(product.get("audience") or "uncertain").strip().lower()
            if audience in {expected,"unisex"}:
                kept.append(product)
            else:
                filtered.append({
                    "name":product.get("name") or "Product",
                    "audience":audience,
                    "audience_evidence":product.get("audience_evidence") or ""
                })
        result["products"]=kept
        if filtered:
            count=len(filtered)
            suffix=f" {count} product{'s' if count!=1 else ''} removed because the retailer evidence did not match this account's {expected} profile."
            result["search_note"]=((result.get("search_note") or "").strip()+suffix).strip()
        return result
    except Exception as exc:
        raise HTTPException(502, f"Live product search failed: {str(exc)[:350]}")



class FavouriteOutfitRequest(BaseModel):
    outfit: dict
    request_text: Optional[str] = ""
    weather_context: Optional[str] = ""
    visual_path: Optional[str] = ""

class SavedLookUpdateRequest(BaseModel):
    label: Optional[str] = ""
    tags: Optional[list[str]] = []
    occasion: Optional[str] = ""
    season: Optional[str] = ""
    notes: Optional[str] = ""
    is_pinned: Optional[bool] = False

def saved_look_row(row):
    d=dict(row)
    try:d["outfit"]=json.loads(d.get("outfit_json") or "{}")
    except Exception:d["outfit"]={}
    try:d["tags"]=json.loads(d.get("tags_json") or "[]")
    except Exception:d["tags"]=[]
    if not isinstance(d["tags"],list):d["tags"]=[]
    d["tags"]=[str(x).strip() for x in d["tags"] if str(x).strip()][:12]
    d["wore_count"]=int(d.get("wore_count") or 0)
    d["is_pinned"]=bool(d.get("is_pinned") or 0)
    return d

@app.get("/api/outfit-favourites")
def get_outfit_favourites():
    con=db()
    rows=con.execute("""
      SELECT * FROM outfit_favourites
      ORDER BY COALESCE(is_pinned,0) DESC, id DESC
    """).fetchall()
    con.close()
    return [saved_look_row(r) for r in rows]

@app.post("/api/outfit-favourites")
def save_outfit_favourite(req: FavouriteOutfitRequest):
    outfit=req.outfit or {}
    label=str(outfit.get("label") or "Saved look")
    # Use stylist fields as gentle defaults where they are actually present.
    occasion=str(outfit.get("occasion_fit") or "")
    con=db()
    cur=con.execute("""
      INSERT INTO outfit_favourites
      (label,outfit_json,request_text,weather_context,visual_path,occasion,tags_json,wore_count,is_pinned,updated_at)
      VALUES (?,?,?,?,?,?,'[]',0,0,?)
    """,(label,json.dumps(outfit,ensure_ascii=False),req.request_text or "",
         req.weather_context or "",req.visual_path or "",occasion,utc_now().isoformat()))
    fid=cur.lastrowid
    con.commit()
    row=con.execute("SELECT * FROM outfit_favourites WHERE id=?",(fid,)).fetchone()
    con.close()
    return saved_look_row(row)

@app.put("/api/outfit-favourites/{fid}")
def update_outfit_favourite(fid:int, req: SavedLookUpdateRequest):
    tags=[]
    seen=set()
    for raw in (req.tags or []):
        tag=" ".join(str(raw).strip().split())[:40]
        key=tag.casefold()
        if tag and key not in seen:
            tags.append(tag);seen.add(key)
        if len(tags)>=12:break
    con=db()
    exists=con.execute("SELECT id FROM outfit_favourites WHERE id=?",(fid,)).fetchone()
    if not exists:
        con.close();raise HTTPException(404,"Saved look not found.")
    con.execute("""
      UPDATE outfit_favourites
      SET label=?,tags_json=?,occasion=?,season=?,notes=?,is_pinned=?,updated_at=?
      WHERE id=?
    """,((req.label or "Saved look").strip()[:120],json.dumps(tags,ensure_ascii=False),
         (req.occasion or "").strip()[:80],(req.season or "").strip()[:40],
         (req.notes or "").strip()[:1000],1 if req.is_pinned else 0,
         utc_now().isoformat(),fid))
    con.commit()
    row=con.execute("SELECT * FROM outfit_favourites WHERE id=?",(fid,)).fetchone()
    con.close()
    return saved_look_row(row)

@app.post("/api/outfit-favourites/{fid}/wore")
def mark_saved_look_worn(fid:int):
    now=utc_now().isoformat()
    con=db()
    exists=con.execute("SELECT id,last_worn_at FROM outfit_favourites WHERE id=?",(fid,)).fetchone()
    if not exists:
        con.close();raise HTTPException(404,"Saved look not found.")
    con.execute("""
      INSERT INTO outfit_wear_events(favourite_id,worn_at,previous_last_worn_at)
      VALUES (?,?,?)
    """,(fid,now,exists["last_worn_at"]))
    con.execute("""
      UPDATE outfit_favourites
      SET wore_count=COALESCE(wore_count,0)+1,last_worn_at=?,updated_at=?
      WHERE id=?
    """,(now,now,fid))
    con.commit()
    row=con.execute("SELECT * FROM outfit_favourites WHERE id=?",(fid,)).fetchone()
    con.close()
    return saved_look_row(row)

@app.post("/api/outfit-favourites/{fid}/undo-wear")
def undo_saved_look_wear(fid:int):
    con=db()
    row=con.execute("SELECT * FROM outfit_favourites WHERE id=?",(fid,)).fetchone()
    if not row:
        con.close();raise HTTPException(404,"Saved look not found.")

    current=max(0,int(row["wore_count"] or 0))
    if current<=0:
        con.close()
        return saved_look_row(row)

    event=con.execute("""
      SELECT * FROM outfit_wear_events
      WHERE favourite_id=? ORDER BY id DESC LIMIT 1
    """,(fid,)).fetchone()

    new_count=max(0,current-1)
    if event:
        restored_last=event["previous_last_worn_at"]
        con.execute("DELETE FROM outfit_wear_events WHERE id=?",(event["id"],))
    else:
        # Counts created before V7.3 have no per-wear event history.
        # Preserve the old last-worn date unless the count returns to zero.
        restored_last=None if new_count==0 else row["last_worn_at"]

    now=utc_now().isoformat()
    con.execute("""
      UPDATE outfit_favourites
      SET wore_count=?,last_worn_at=?,updated_at=?
      WHERE id=?
    """,(new_count,restored_last,now,fid))
    con.commit()
    updated=con.execute("SELECT * FROM outfit_favourites WHERE id=?",(fid,)).fetchone()
    con.close()
    return saved_look_row(updated)

@app.delete("/api/outfit-favourites/{fid}")
def delete_outfit_favourite(fid:int):
    con=db()
    con.execute("DELETE FROM outfit_favourites WHERE id=?",(fid,))
    con.commit()
    con.close()
    return {"ok":True}

WEATHER_CONTEXT_SCHEMA={
  "type":"object",
  "properties":{
    "location":{"type":"string"},
    "date_or_period":{"type":"string"},
    "summary":{"type":"string"},
    "temperature_low_c":{"type":["number","null"]},
    "temperature_high_c":{"type":["number","null"]},
    "rain":{"type":"string"},
    "wind":{"type":"string"},
    "styling_context":{"type":"string"},
    "confidence":{"type":"string","enum":["high","medium","low"]}
  },
  "required":["location","date_or_period","summary","temperature_low_c","temperature_high_c",
              "rain","wind","styling_context","confidence"],
  "additionalProperties":False
}

class WeatherContextRequest(BaseModel):
    location: str
    when: Optional[str] = "today"

@app.post("/api/weather-context")
def weather_context(req: WeatherContextRequest):
    location=(req.location or "").strip()
    when=(req.when or "today").strip()
    if not location:
        raise HTTPException(400,"Enter a location first.")
    if not os.getenv("OPENAI_API_KEY") or OpenAI is None:
        raise HTTPException(400,"Live weather lookup needs the OpenAI connection.")

    prompt=f"""Find the most relevant current weather forecast available online for:
LOCATION: {location}
WHEN: {when}

This weather will be used by a personal stylist. Use current forecast information from reliable
weather sources. If the requested date is outside reliable forecast range, say so and use low
confidence rather than inventing conditions.

Summarise temperatures in Celsius, precipitation/rain risk, wind and practical clothing implications.
The styling_context should be concise and useful for choosing layers, fabrics, outerwear and footwear.
"""
    try:
        response=tracked_responses_create(OpenAI(),
            model=os.getenv("OPENAI_SHOPPING_MODEL",os.getenv("OPENAI_MODEL","gpt-5.6-terra")),
            reasoning={"effort":"low"},
            tools=[{"type":"web_search"}],
            tool_choice="auto",
            include=["web_search_call.action.sources"],
            input=prompt,
            text={"format":{
                "type":"json_schema",
                "name":"weather_context",
                "schema":WEATHER_CONTEXT_SCHEMA,
                "strict":True
            }}
        )
        return json.loads(response.output_text)
    except Exception as exc:
        raise HTTPException(502,f"Weather lookup failed: {str(exc)[:260]}")

class StylistV4Request(BaseModel):
    request_text: str
    anchor_garment_id: Optional[int] = None
    owned_only: Optional[bool] = False
    max_options: Optional[int] = 3

STYLIST_V4_SCHEMA = {
  "type": "object",
  "properties": {
    "summary": {"type": "string"},
    "outfits": {
      "type": "array",
      "minItems": 1,
      "maxItems": 4,
      "items": {
        "type": "object",
        "properties": {
          "label": {"type": "string"},
          "rank": {"type": "integer", "minimum": 1, "maximum": 4},
          "score": {"type": "integer", "minimum": 0, "maximum": 100},
          "owned_garment_ids": {"type": "array", "items": {"type": "integer"}},
          "missing_piece": {"type": "string"},
          "missing_piece_reason": {"type": "string"},
          "why_it_works": {"type": "string"},
          "occasion_fit": {"type": "string"},
          "weather_fit": {"type": "string"},
          "formality_fit": {"type": "string"},
          "style_note": {"type": "string"}
        },
        "required": [
          "label","rank","score","owned_garment_ids","missing_piece","missing_piece_reason",
          "why_it_works","occasion_fit","weather_fit","formality_fit","style_note"
        ],
        "additionalProperties": False
      }
    }
  },
  "required": ["summary","outfits"],
  "additionalProperties": False
}

STYLIST_V4_INSTRUCTIONS = """You are a high-level personal stylist for one user.

Use the user's actual wardrobe, fit profile, brand/size history and previous style feedback.
The request is free text and may contain occasion, weather, dress code, preferred garment,
destination, season, desired smartness or social context.

Priorities:
- Return only a small number of genuinely strong, differentiated outfits.
- Rank them best-first.
- Prefer the user's actual wardrobe.
- Never claim the user owns anything unless its garment ID appears in the supplied wardrobe.
- If one missing item would materially improve an outfit, name it precisely.
- If owned_only is true, do not recommend a missing item.
- If an anchor garment is supplied, every outfit must contain it.
- Reason about colour harmony, material/texture, silhouette, footwear, layering, weather,
  seasonality, formality, occasion and practicality.
- Use fit feedback, preferred brands and learned feedback where relevant.
- Treat SAVED LOOKS as positive evidence: the user deliberately kept those outfits.
- Treat SAVED LOOKS with wore_count > 0 as stronger REAL-WEAR evidence. Repeated wears are stronger than a single wear; a saved-only look must never outweigh repeated actual wear.
- is_pinned, tags, occasion, season, notes and last_worn_at are useful context, but do not invent preferences from missing metadata.
- Treat "Works for me" feedback as a positive signal and "Less like this" as a soft negative signal.
- Evidence hierarchy for taste: repeated actual wear > single actual wear > repeated positive reactions/saved patterns > one saved look. Fit reviews remain the strongest evidence for sizing/fit, not taste.
- Learn repeated patterns across saved looks and feedback: palette, contrast, silhouette, layering,
  footwear, smartness and recurring garment combinations.
- Do not overfit to one repeated pattern. If recent preferences are dominated by one palette
  (for example light blue / pale neutrals), keep some options in that direction but deliberately
  include at least one strong alternative palette when appropriate.
- A single reaction should not outweigh repeated evidence.
- Distinguish timelessly appropriate choices from trend-led choices when useful.
- Keep explanations concise and specific rather than generic.
- Score each outfit 0–100 for how well it fits the request and the user's known preferences.
- Return only supplied wardrobe IDs in owned_garment_ids.
"""

@app.post("/api/stylist-v4")
def stylist_v4(req: StylistV4Request):
    request_text = (req.request_text or "").strip()
    if not request_text:
        raise HTTPException(400, "Tell me what you are dressing for.")

    con = db()
    garments = [dict(r) for r in con.execute("SELECT * FROM garments ORDER BY id DESC").fetchall()]
    profile = dict(con.execute("SELECT * FROM profile WHERE id=1").fetchone())
    feedback = [dict(r) for r in con.execute(
        "SELECT rating, outfit_json FROM feedback ORDER BY id DESC LIMIT 40"
    ).fetchall()]
    favourites = [dict(r) for r in con.execute(
        "SELECT label,outfit_json,request_text,weather_context,wore_count,last_worn_at,is_pinned,tags_json,occasion,season,notes FROM outfit_favourites ORDER BY COALESCE(wore_count,0) DESC, id DESC LIMIT 30"
    ).fetchall()]
    con.close()

    if not garments:
        raise HTTPException(400, "Add some wardrobe items first so I can style from your actual clothes.")
    if not os.getenv("OPENAI_API_KEY") or OpenAI is None:
        raise HTTPException(400, "OpenAI is not connected.")

    anchor = None
    if req.anchor_garment_id is not None:
        anchor = next((g for g in garments if g["id"] == req.anchor_garment_id), None)
        if anchor is None:
            raise HTTPException(404, "That wardrobe item could not be found.")

    max_options = max(1, min(int(req.max_options or 3), 4))
    context = {
      "request_text": request_text,
      "anchor_garment": anchor,
      "owned_only": bool(req.owned_only),
      "max_options": max_options,
      "profile": profile,
      "styling_profile": (current_user() or {}).get("styling_profile","menswear"),
      "wardrobe": garments,
      "recent_feedback": feedback,
      "saved_looks": favourites
    }

    client = OpenAI()
    response = tracked_responses_create(client,
      model=os.getenv("OPENAI_MODEL","gpt-5.6-terra"),
      reasoning={"effort":"low"},
      instructions=STYLIST_V4_INSTRUCTIONS + styling_profile_guidance(),
      input=json.dumps(context, ensure_ascii=False),
      text={"format":{
        "type":"json_schema",
        "name":"stylist_v4_outfits",
        "schema":STYLIST_V4_SCHEMA,
        "strict":True
      }}
    )

    result = json.loads(response.output_text)
    result["outfits"] = result.get("outfits", [])[:max_options]
    return result



class ReplaceOutfitRequest(BaseModel):
    base_outfit: dict
    request_text: str = ""
    feedback: str = ""
    weather_context: str = ""
    owned_only: bool = False
    other_outfits: list[dict] = []

@app.post("/api/stylist-v4/replace-one")
def stylist_v4_replace_one(req: ReplaceOutfitRequest):
    if not isinstance(req.base_outfit,dict):
        raise HTTPException(400,"That outfit could not be read.")
    con=db()
    wardrobe=[dict(r) for r in con.execute("SELECT * FROM garments ORDER BY id DESC").fetchall()]
    profile=dict(con.execute("SELECT * FROM profile WHERE id=1").fetchone())
    feedback_rows=[dict(r) for r in con.execute("SELECT rating,outfit_json FROM feedback ORDER BY id DESC LIMIT 30").fetchall()]
    saved_looks=saved_style_evidence(con,30)
    con.close()
    if not wardrobe:
        raise HTTPException(400,"Add some wardrobe items first.")
    if not os.getenv("OPENAI_API_KEY") or OpenAI is None:
        raise HTTPException(503,"OpenAI is not connected.")

    valid_ids={g["id"] for g in wardrobe}
    compact=[{k:g.get(k) for k in [
        "id","category","garment_type","brand","model_line","labelled_size","colour","material",
        "pattern","fit_cut","fit_feedback","season","formality"
    ]} for g in wardrobe]

    instructions="""You are replacing ONE outfit the user does not want.

Return exactly ONE strong replacement, not a set of options.

Rules:
- Keep the original request, occasion, weather and practical constraints.
- The replacement must be meaningfully different from the rejected/base outfit.
- Also avoid simply duplicating the other outfits already shown.
- Respect any user refinement such as darker, lighter, more brown, less formal, smarter, more relaxed, different shoes, etc.
- Use only supplied wardrobe IDs in owned_garment_ids.
- Prefer the user's wardrobe. If owned_only is true, missing_piece must be blank.
- If shopping is allowed, suggest at most one genuinely useful missing item.
- Keep the result coherent as one complete outfit.
- Score it 0–100 and keep explanations concise.
"""
    context={
      "original_request":(req.request_text or "").strip(),
      "user_refinement":(req.feedback or "").strip(),
      "weather_context":(req.weather_context or "").strip(),
      "owned_only":bool(req.owned_only),
      "rejected_outfit":req.base_outfit,
      "other_outfits_already_shown":req.other_outfits[:6],
      "profile":profile,
      "wardrobe":compact,
      "recent_feedback":feedback_rows,
      "saved_looks":saved_looks
    }
    try:
        response=tracked_responses_create(OpenAI(),
          model=os.getenv("OPENAI_MODEL","gpt-5.6-terra"),
          reasoning={"effort":"low"},
          instructions=instructions+styling_profile_guidance(),
          input=json.dumps(context,ensure_ascii=False),
          text={"format":{"type":"json_schema","name":"replacement_outfit","schema":STYLIST_V4_SCHEMA,"strict":True}}
        )
        data=json.loads(response.output_text)
    except Exception as exc:
        raise HTTPException(502,f"I couldn't create a replacement outfit: {str(exc)[:220]}")

    base_sig=tuple(sorted(int(x) for x in (req.base_outfit.get("owned_garment_ids") or []) if str(x).isdigit()))
    other_sigs={
      tuple(sorted(int(x) for x in (o.get("owned_garment_ids") or []) if str(x).isdigit()))
      for o in (req.other_outfits or [])
    }
    for outfit in data.get("outfits",[]):
        ids=[]
        for value in outfit.get("owned_garment_ids",[]):
            try: gid=int(value)
            except Exception: continue
            if gid in valid_ids and gid not in ids: ids.append(gid)
        sig=tuple(sorted(ids))
        if ids and sig!=base_sig and sig not in other_sigs:
            outfit["owned_garment_ids"]=ids
            outfit["rank"]=1
            return {"summary":data.get("summary") or "One replacement option.","outfit":outfit}
    raise HTTPException(502,"I couldn't find a sufficiently different replacement from the current options. Try adding a short preference such as 'darker' or 'more relaxed'.")



class RefineOutfitRequest(BaseModel):
    base_outfit: dict
    refinement: str
    request_text: str = ""
    weather_context: str = ""
    owned_only: bool = True

@app.post("/api/stylist-v4/refine-one")
def stylist_v4_refine_one(req: RefineOutfitRequest):
    if not isinstance(req.base_outfit,dict):
        raise HTTPException(400,"That outfit could not be read.")
    refinement=(req.refinement or "").strip()
    if not refinement:
        raise HTTPException(400,"Tell me what you want to change about this look.")

    con=db()
    wardrobe=[dict(r) for r in con.execute("SELECT * FROM garments ORDER BY id DESC").fetchall()]
    profile=dict(con.execute("SELECT * FROM profile WHERE id=1").fetchone())
    saved_looks=saved_style_evidence(con,30)
    con.close()
    if not wardrobe:
        raise HTTPException(400,"Add some wardrobe items first.")
    if not os.getenv("OPENAI_API_KEY") or OpenAI is None:
        raise HTTPException(503,"OpenAI is not connected.")

    valid_ids={int(g["id"]) for g in wardrobe}
    base_ids=[]
    for value in req.base_outfit.get("owned_garment_ids") or []:
        try: gid=int(value)
        except Exception: continue
        if gid in valid_ids and gid not in base_ids:
            base_ids.append(gid)
    if not base_ids:
        raise HTTPException(400,"The current outfit no longer contains available wardrobe items.")

    compact=[{k:g.get(k) for k in [
        "id","category","garment_type","brand","model_line","labelled_size","colour","material",
        "pattern","fit_cut","fit_feedback","season","formality"
    ]} for g in wardrobe]

    instructions="""You are refining ONE existing outfit, not replacing it.

Make the smallest useful change that satisfies the user's instruction.

Rules:
- Treat the supplied base outfit as the look to preserve.
- If the user asks to ADD something (for example 'add a grey blazer' or 'add a layer'),
  keep every existing owned garment unless there is a genuine clothing conflict, and add the best matching owned item.
- If the user asks to SWAP one element, change only that element wherever possible.
- If the user asks to make it smarter, more casual, warmer, darker, lighter, etc., preserve most of the outfit
  and alter only the minimum number of pieces needed.
- Use only supplied wardrobe IDs in owned_garment_ids.
- Do not invent ownership.
- If owned_only is true, missing_piece must be blank.
- If the requested item is not owned and owned_only is true, make the closest useful owned refinement and explain it briefly.
- Preserve the occasion, weather suitability and overall character unless the refinement explicitly changes them.
- Return exactly ONE refined outfit.
- Keep explanations concise and specific.
"""
    context={
      "original_request":(req.request_text or "").strip(),
      "refinement":refinement,
      "weather_context":(req.weather_context or "").strip(),
      "owned_only":bool(req.owned_only),
      "base_outfit":req.base_outfit,
      "base_owned_ids":base_ids,
      "profile":profile,
      "wardrobe":compact,
      "saved_looks":saved_looks
    }
    try:
        response=tracked_responses_create(OpenAI(),
          model=os.getenv("OPENAI_MODEL","gpt-5.6-terra"),
          reasoning={"effort":"low"},
          instructions=instructions+styling_profile_guidance(),
          input=json.dumps(context,ensure_ascii=False),
          text={"format":{"type":"json_schema","name":"refined_outfit","schema":STYLIST_V4_SCHEMA,"strict":True}}
        )
        data=json.loads(response.output_text)
    except Exception as exc:
        raise HTTPException(502,f"I couldn't refine that outfit: {str(exc)[:220]}")

    adding=bool(re.search(r"\b(add|layer|put on|wear with|include)\b",refinement.lower()))
    base_set=set(base_ids)
    for outfit in data.get("outfits",[]):
        ids=[]
        for value in outfit.get("owned_garment_ids",[]):
            try: gid=int(value)
            except Exception: continue
            if gid in valid_ids and gid not in ids:
                ids.append(gid)
        if not ids:
            continue
        overlap=len(base_set & set(ids))
        # "Add" should preserve the whole base look. Other refinements should
        # preserve all but at most one existing piece.
        if adding and not base_set.issubset(set(ids)):
            continue
        if not adding and overlap < max(1,len(base_ids)-1):
            continue
        outfit["owned_garment_ids"]=ids
        outfit["rank"]=1
        if req.owned_only:
            outfit["missing_piece"]=""
            outfit["missing_piece_reason"]=""
        return {"summary":data.get("summary") or "Refined this look.","outfit":outfit}

    raise HTTPException(502,"I couldn't make that change without losing too much of the original outfit. Try a slightly more specific instruction.")


class StylistMoreLikeRequest(BaseModel):
    base_outfit: dict
    request_text: str = ""
    weather_context: str = ""
    owned_only: bool = False
    max_options: int = 3

@app.post("/api/stylist-v4/more-like-this")
def stylist_v4_more_like_this(req: StylistMoreLikeRequest):
    if not isinstance(req.base_outfit, dict):
        raise HTTPException(400, "That outfit could not be read.")

    con=db()
    wardrobe=[dict(r) for r in con.execute("SELECT * FROM garments ORDER BY id DESC").fetchall()]
    profile=dict(con.execute("SELECT * FROM profile WHERE id=1").fetchone())
    feedback=[dict(r) for r in con.execute(
        "SELECT rating, outfit_json FROM feedback ORDER BY id DESC LIMIT 30"
    ).fetchall()]
    saved_looks=saved_style_evidence(con,30)
    con.close()

    if not wardrobe:
        raise HTTPException(400, "Add some wardrobe items first.")
    if not os.getenv("OPENAI_API_KEY") or OpenAI is None:
        raise HTTPException(400, "OpenAI is not connected.")

    valid_ids={g["id"] for g in wardrobe}
    base_ids=[int(x) for x in (req.base_outfit.get("owned_garment_ids") or []) if int(x) in valid_ids]
    if not base_ids:
        raise HTTPException(400, "The original outfit no longer contains available wardrobe items.")

    max_options=max(2,min(int(req.max_options or 3),3))
    compact_wardrobe=[
        {k:g.get(k) for k in [
            "id","category","garment_type","brand","model_line","labelled_size",
            "colour","material","pattern","fit_cut","fit_feedback","season","formality"
        ]}
        for g in wardrobe
    ]

    instructions="""You are extending an existing personal styling result.

Create 2–3 strong variations that are recognisably 'more like' the supplied base outfit.
Do not replace the whole idea just to be different.

Rules:
- Preserve the original occasion, smartness and overall character.
- Prefer small, deliberate changes: usually one or two owned-garment swaps per variation.
- When the base outfit contains 3 or more owned pieces, normally retain at least 2 of them.
- When it contains 1–2 owned pieces, retain at least 1.
- Variations must be meaningfully different from each other and from the base outfit.
- Use only supplied wardrobe IDs in owned_garment_ids.
- Never claim the user owns anything else.
- If owned_only is true, missing_piece must be blank.
- If owned_only is false, suggest a missing item only when it materially improves a variation.
- Respect fit history, colour harmony, silhouette, weather, formality and the user's original request.
- Use saved_looks as taste evidence; entries with wore_count > 0 are real-wear evidence and should carry more weight than saved-only looks.
- Keep explanations concise and specific.
- Rank the variations best-first and score each 0–100.
"""

    context={
        "original_request":(req.request_text or "").strip(),
        "weather_context":(req.weather_context or "").strip(),
        "owned_only":bool(req.owned_only),
        "base_outfit":req.base_outfit,
        "base_owned_ids":base_ids,
        "profile":profile,
        "wardrobe":compact_wardrobe,
        "recent_feedback":feedback,
        "saved_looks":saved_looks,
        "max_options":max_options
    }

    try:
        response=tracked_responses_create(OpenAI(),
            model=os.getenv("OPENAI_MODEL","gpt-5.6-terra"),
            reasoning={"effort":"low"},
            instructions=instructions + styling_profile_guidance(),
            input=json.dumps(context,ensure_ascii=False),
            text={"format":{
                "type":"json_schema",
                "name":"stylist_more_like_this",
                "schema":STYLIST_V4_SCHEMA,
                "strict":True
            }}
        )
        result=json.loads(response.output_text)
    except Exception as exc:
        raise HTTPException(502,f"I couldn't create variations from that look: {str(exc)[:220]}")

    cleaned=[]
    seen=set()
    base_set=set(base_ids)
    for outfit in result.get("outfits",[]):
        ids=[]
        for value in outfit.get("owned_garment_ids",[]):
            try:
                gid=int(value)
            except Exception:
                continue
            if gid in valid_ids and gid not in ids:
                ids.append(gid)

        # A More Like This result should still visibly inherit the original look.
        if not ids or not (base_set & set(ids)):
            continue

        signature=tuple(sorted(ids))
        if signature in seen or set(ids)==base_set:
            continue
        seen.add(signature)

        outfit["owned_garment_ids"]=ids
        outfit["rank"]=len(cleaned)+1
        cleaned.append(outfit)
        if len(cleaned)>=max_options:
            break

    if not cleaned:
        raise HTTPException(502,"I couldn't make useful variations without losing the character of the original outfit. Please try again.")

    result["outfits"]=cleaned
    return result


class ShortlistProductRequest(BaseModel):
    product: dict
    context: Optional[dict] = None

def _safe_web_url(url: str) -> bool:
    return _public_http_url(url)

def _extract_product_image(page_url: str) -> str:
    if not _safe_web_url(page_url):
        return ""
    try:
        import urllib.request, re as _re, html as _html
        from urllib.parse import urljoin
        req=urllib.request.Request(page_url,headers={
            "User-Agent":"Mozilla/5.0",
            "Accept":"text/html,application/xhtml+xml"
        })
        with _safe_urlopen(req,timeout=8) as r:
            if "text/html" not in (r.headers.get("Content-Type") or "").lower():
                return ""
            raw=r.read(900000).decode("utf-8","ignore")
        patterns=[
            r'<meta[^>]+property=["\']og:image(?::secure_url)?["\'][^>]+content=["\']([^"\']+)["\']',
            r'<meta[^>]+content=["\']([^"\']+)["\'][^>]+property=["\']og:image(?::secure_url)?["\']',
            r'<meta[^>]+name=["\']twitter:image(?::src)?["\'][^>]+content=["\']([^"\']+)["\']',
            r'<meta[^>]+content=["\']([^"\']+)["\'][^>]+name=["\']twitter:image(?::src)?["\']'
        ]
        for pat in patterns:
            m=_re.search(pat,raw,_re.I)
            if m:
                u=urljoin(page_url,_html.unescape(m.group(1).strip()))
                if _safe_web_url(u): return u
    except Exception:
        pass
    return ""

@app.post("/api/product-thumbnail")
def product_thumbnail(payload: dict):
    supplied=str(payload.get("image_url") or "")
    if _safe_web_url(supplied):
        return {"image_url":supplied,"source":"search"}
    found=_extract_product_image(str(payload.get("url") or ""))
    return {"image_url":found,"source":"page" if found else "none"}

@app.get("/api/shopping-shortlist")
def get_shopping_shortlist():
    con=db()
    rows=[dict(r) for r in con.execute("SELECT * FROM shopping_shortlist ORDER BY id DESC").fetchall()]
    con.close()
    for r in rows:
        try:r["context"]=json.loads(r.get("context_json") or "{}")
        except Exception:r["context"]={}
    return rows

@app.post("/api/shopping-shortlist")
def add_shopping_shortlist(req: ShortlistProductRequest):
    p=req.product or {}
    url=str(p.get("url") or "").strip()
    name=str(p.get("name") or "Product").strip()
    brand=str(p.get("brand") or "").strip()
    retailer=str(p.get("retailer") or "").strip()
    key=(url or f"{brand}|{retailer}|{name}").lower()
    image_url=str(p.get("image_url") or "").strip()
    if not image_url and url:image_url=_extract_product_image(url)
    con=db()
    con.execute("""
      INSERT INTO shopping_shortlist
      (product_key,name,brand,retailer,price,url,image_url,colour,material,fit,size_note,confidence,why_it_matches,context_json)
      VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)
      ON CONFLICT(product_key) DO UPDATE SET
       name=excluded.name,brand=excluded.brand,retailer=excluded.retailer,price=excluded.price,
       url=excluded.url,image_url=CASE WHEN excluded.image_url<>'' THEN excluded.image_url ELSE shopping_shortlist.image_url END,
       colour=excluded.colour,material=excluded.material,fit=excluded.fit,size_note=excluded.size_note,
       confidence=excluded.confidence,why_it_matches=excluded.why_it_matches,context_json=excluded.context_json
    """,(key,name,brand,retailer,str(p.get("price") or ""),url,image_url,str(p.get("colour") or ""),
         str(p.get("material") or ""),str(p.get("fit") or ""),str(p.get("size_note") or ""),
         str(p.get("confidence") or ""),str(p.get("why_it_matches") or ""),
         json.dumps(req.context or {},ensure_ascii=False)))
    con.commit()
    row=dict(con.execute("SELECT * FROM shopping_shortlist WHERE product_key=?",(key,)).fetchone())
    con.close()
    return row

@app.delete("/api/shopping-shortlist/{sid}")
def delete_shopping_shortlist(sid:int):
    con=db(); con.execute("DELETE FROM shopping_shortlist WHERE id=?",(sid,)); con.commit(); con.close()
    return {"ok":True}


class FitReviewRequest(BaseModel):
    labelled_size: Optional[str] = ""
    fit_rating: Optional[int] = None
    fit_chest: Optional[str] = ""
    fit_waist: Optional[str] = ""
    fit_length: Optional[str] = ""
    fit_sleeve: Optional[str] = ""
    fit_shoulders: Optional[str] = ""
    fit_hips: Optional[str] = ""
    fit_notes: Optional[str] = ""

@app.post("/api/garments/{gid}/fit-review")
def save_fit_review(gid: int, req: FitReviewRequest):
    con=db()
    row=con.execute("SELECT * FROM garments WHERE id=?",(gid,)).fetchone()
    if not row:
        con.close()
        raise HTTPException(404,"Garment not found.")
    rating=req.fit_rating
    if rating is not None:
        rating=max(1,min(5,int(rating)))
    reviewed=datetime.now(timezone.utc).isoformat()
    con.execute("""
      UPDATE garments SET
        labelled_size=CASE WHEN ?<>'' THEN ? ELSE labelled_size END,
        fit_review_status='confirmed', fit_rating=?,
        fit_chest=?,fit_waist=?,fit_hips=?,fit_length=?,fit_sleeve=?,fit_shoulders=?,
        fit_notes=?,fit_reviewed_at=?
      WHERE id=?
    """,(req.labelled_size or "",req.labelled_size or "",rating,
         req.fit_chest or "",req.fit_waist or "",req.fit_hips or "",req.fit_length or "",
         req.fit_sleeve or "",req.fit_shoulders or "",req.fit_notes or "",
         reviewed,gid))
    con.commit()
    updated=dict(con.execute("SELECT * FROM garments WHERE id=?",(gid,)).fetchone())
    con.close()
    return updated

@app.get("/api/fit-learning")
def get_fit_learning():
    con=db()
    rows=[dict(r) for r in con.execute("""
      SELECT brand,labelled_size,fit_rating,fit_chest,fit_waist,fit_hips,fit_length,
             fit_sleeve,fit_shoulders,fit_notes,fit_reviewed_at
      FROM garments
      WHERE fit_review_status='confirmed' AND brand<>''
      ORDER BY fit_reviewed_at DESC LIMIT 100
    """).fetchall()]
    con.close()
    return rows




def fit_evidence_snapshot(limit: int = 120):
    con=db()
    profile=dict(con.execute("SELECT * FROM profile WHERE id=1").fetchone())
    rows=[dict(r) for r in con.execute("""
      SELECT id,brand,model_line,garment_type,category,labelled_size,fit_cut,fit_feedback,
             fit_review_status,fit_rating,fit_chest,fit_waist,fit_hips,fit_length,
             fit_sleeve,fit_shoulders,fit_notes,fit_reviewed_at
      FROM garments
      ORDER BY COALESCE(fit_reviewed_at,created_at) DESC
      LIMIT ?
    """,(limit,)).fetchall()]
    con.close()

    confirmed=[r for r in rows if r.get("fit_review_status")=="confirmed"]
    quick_feedback=[
      r for r in rows
      if (r.get("fit_feedback") or "").strip()
      and (r.get("fit_feedback") or "").strip().lower() not in ("unknown","fit unknown")
    ]
    by_brand={}
    by_category={}
    for r in confirmed:
        brand=(r.get("brand") or "").strip()
        cat=(r.get("garment_type") or r.get("category") or "Garment").strip()
        if brand:
            by_brand.setdefault(brand,[]).append(r)
        if cat:
            by_category.setdefault(cat,[]).append(r)

    def summarise(group):
        out=[]
        for name,items in group.items():
            ratings=[int(x["fit_rating"]) for x in items if x.get("fit_rating")]
            sizes={}
            for x in items:
                size=(x.get("labelled_size") or "").strip()
                if size:
                    sizes[size]=sizes.get(size,0)+1
            issues={}
            for area in ["fit_chest","fit_waist","fit_hips","fit_length","fit_sleeve","fit_shoulders"]:
                values=[(x.get(area) or "").strip() for x in items if (x.get(area) or "").strip()]
                for v in values:
                    if v!="Good":
                        label=f"{area.replace('fit_','').replace('_',' ')}: {v}"
                        issues[label]=issues.get(label,0)+1
            out.append({
              "name":name,
              "reviews":len(items),
              "average_rating":round(sum(ratings)/len(ratings),1) if ratings else None,
              "sizes":sorted([{"size":k,"count":v} for k,v in sizes.items()],key=lambda z:(-z["count"],z["size"])),
              "issues":sorted([{"issue":k,"count":v} for k,v in issues.items()],key=lambda z:(-z["count"],z["issue"]))[:5]
            })
        return sorted(out,key=lambda z:(-z["reviews"],z["name"].lower()))

    return {
      "profile":profile,
      "confirmed":confirmed,
      "quick_feedback":quick_feedback,
      "unreviewed":[
        r for r in rows
        if r.get("fit_review_status")!="confirmed"
        and ((r.get("fit_feedback") or "").strip().lower() in ("","unknown","fit unknown"))
      ],
      "brands":summarise(by_brand),
      "categories":summarise(by_category)
    }


FIT_INTELLIGENCE_SCHEMA={
 "type":"object",
 "properties":{
  "summary":{"type":"string"},
  "confidence":{"type":"string","enum":["low","medium","high"]},
  "what_fits_best":{"type":"array","maxItems":6,"items":{"type":"string"}},
  "watch_out_for":{"type":"array","maxItems":6,"items":{"type":"string"}},
  "brand_lessons":{"type":"array","maxItems":8,"items":{"type":"object","properties":{
    "brand":{"type":"string"},"lesson":{"type":"string"},"confidence":{"type":"string","enum":["low","medium","high"]}
  },"required":["brand","lesson","confidence"],"additionalProperties":False}},
  "shopping_rules":{"type":"array","maxItems":6,"items":{"type":"string"}},
  "next_reviews":{"type":"array","maxItems":6,"items":{"type":"integer"}}
 },
 "required":["summary","confidence","what_fits_best","watch_out_for","brand_lessons","shopping_rules","next_reviews"],
 "additionalProperties":False
}

@app.get("/api/fit-intelligence")
def fit_intelligence():
    evidence=fit_evidence_snapshot()
    confirmed=evidence["confirmed"]
    quick_feedback=evidence["quick_feedback"]
    unreviewed=evidence["unreviewed"]

    fallback={
      "summary":(
        f"Learning from {len(quick_feedback)} garment{'s' if len(quick_feedback)!=1 else ''} with real fit feedback."
        if quick_feedback and not confirmed else
        ("Add Fit Feedback to garments as you upload them to teach the sizing engine."
         if not quick_feedback and not confirmed else
         f"Learning from {len(quick_feedback)} quick fit signal{'s' if len(quick_feedback)!=1 else ''} and {len(confirmed)} detailed review{'s' if len(confirmed)!=1 else ''}.")
      ),
      "confidence":"low" if len(confirmed)<3 else "medium" if len(confirmed)<8 else "high",
      "what_fits_best":[],
      "watch_out_for":[],
      "brand_lessons":[],
      "shopping_rules":[],
      "next_reviews":[int(r["id"]) for r in unreviewed[:6]]
    }

    analysis=fallback
    if (confirmed or quick_feedback) and os.getenv("OPENAI_API_KEY") and OpenAI is not None:
        compact={
          "profile":evidence["profile"],
          "brand_patterns":evidence["brands"][:15],
          "category_patterns":evidence["categories"][:15],
          "confirmed_reviews":[{
            k:r.get(k) for k in [
              "id","brand","model_line","garment_type","category","labelled_size",
              "fit_cut","fit_feedback","fit_rating","fit_chest","fit_waist","fit_hips","fit_length",
              "fit_sleeve","fit_shoulders","fit_notes"
            ]
          } for r in confirmed[:60]],
          "quick_fit_feedback":[{
            k:r.get(k) for k in [
              "id","brand","model_line","garment_type","category","labelled_size","fit_cut","fit_feedback"
            ]
          } for r in quick_feedback[:80]],
          "unreviewed_items":[{
            k:r.get(k) for k in ["id","brand","model_line","garment_type","category","labelled_size"]
          } for r in unreviewed[:30]]
        }
        instructions=f"""You are the fit-learning engine for a personal clothing stylist.

Use ONLY the supplied real-world fit feedback, detailed fit reviews and measurements. Quick Fit Feedback
such as Perfect fit, Slightly tight or Slightly loose is valid real-world evidence. Detailed reviews add specificity
but are optional. Do not invent body characteristics, brand sizing rules or certainty that the evidence does not support.
The current styling profile is: {styling_profile()}.
For womenswear, treat bust/chest, waist, hips, hem/body length and shoe/dress/top/bottom sizing as distinct signals where available.
For menswear, keep the existing chest/shoulder/waist/sleeve/trouser evidence model.

Important:
- One garment is anecdotal evidence. Multiple consistent reviews are stronger.
- Brand fit is line/item specific; never claim all garments from a brand fit identically.
- Separate labelled size from actual fit.
- Identify useful repeated tendencies in chest, waist, shoulders, sleeve/body length and overall rating.
- Shopping rules should be practical and conservative, e.g. "Start with L in this brand's similar slim-cut tops, but verify the exact line."
- If evidence is weak, say so.
- next_reviews should contain IDs of unreviewed items that would add the most useful evidence: favour repeated brands, common categories, or items with a labelled size.
- Keep this concise and useful.
"""
        try:
            response=tracked_responses_create(OpenAI(),
              model=os.getenv("OPENAI_MODEL","gpt-5.6-terra"),
              reasoning={"effort":"low"},
              instructions=instructions,
              input=json.dumps(compact,ensure_ascii=False),
              text={"format":{"type":"json_schema","name":"fit_intelligence","schema":FIT_INTELLIGENCE_SCHEMA,"strict":True}}
            )
            analysis=json.loads(response.output_text)
        except Exception:
            analysis=fallback

    return {
      "metrics":{
        "confirmed_reviews":len(confirmed),
        "brands_learned":len(evidence["brands"]),
        "categories_learned":len(evidence["categories"]),
        "unreviewed_items":len(unreviewed)
      },
      "brand_patterns":evidence["brands"][:12],
      "category_patterns":evidence["categories"][:12],
      "analysis":analysis
    }

LOOK_CRITIQUE_SCHEMA={
 "type":"object",
 "properties":{
  "verdict":{"type":"string"},
  "score":{"type":"integer","minimum":0,"maximum":100},
  "what_works":{"type":"array","items":{"type":"string"},"maxItems":4},
  "small_changes":{"type":"array","items":{
   "type":"object",
   "properties":{
    "change":{"type":"string"},
    "reason":{"type":"string"},
    "replacement_garment_id":{"type":["integer","null"]}
   },
   "required":["change","reason","replacement_garment_id"],
   "additionalProperties":False
  },"maxItems":4},
  "stylist_note":{"type":"string"}
 },
 "required":["verdict","score","what_works","small_changes","stylist_note"],
 "additionalProperties":False
}

class LookCritiqueRequest(BaseModel):
    garment_ids:list[int]
    request_text:Optional[str]=""
    mode:Optional[str]="analyse"

@app.post("/api/look-critique")
def look_critique(req:LookCritiqueRequest):
    ids=[int(x) for x in req.garment_ids if isinstance(x,int) or str(x).isdigit()]
    if not ids: raise HTTPException(400,"Select some wardrobe pieces first.")
    con=db()
    ph=",".join("?" for _ in ids)
    selected=[dict(r) for r in con.execute(f"SELECT * FROM garments WHERE id IN ({ph})",ids).fetchall()]
    wardrobe=[dict(r) for r in con.execute("SELECT * FROM garments ORDER BY id DESC").fetchall()]
    profile=dict(con.execute("SELECT * FROM profile WHERE id=1").fetchone())
    con.close()
    selected_by={g["id"]:g for g in selected}
    selected=[selected_by[i] for i in ids if i in selected_by]

    compact=lambda g:{k:g.get(k) for k in ["id","category","garment_type","brand","model_line","labelled_size","colour","material","pattern","fit_cut","fit_feedback","season","formality","fit_notes"]}
    mode=(req.mode or "analyse").lower()
    instruction={
      "analyse":"Assess the user's chosen outfit. Keep it intact unless there is a genuine issue. Explain what works and offer only worthwhile small refinements.",
      "improve":"Improve the user's chosen outfit with the fewest changes possible. Prefer swapping only one piece, or at most two, using garments the user already owns.",
      "alternatives":"Keep the core character of the user's chosen outfit and suggest small alternative directions using their wardrobe; do not replace the entire look."
    }.get(mode,"Assess the outfit and prefer small refinements.")

    context={"mode":mode,"user_request":req.request_text or "No occasion supplied","selected_outfit":[compact(g) for g in selected],
             "wardrobe":[compact(g) for g in wardrobe],"profile":profile}
    response=tracked_responses_create(OpenAI(),
      model=os.getenv("OPENAI_MODEL","gpt-5.6-terra"),reasoning={"effort":"low"},
      instructions=f"""You are a restrained, practical personal stylist. {instruction}
Never claim an item is owned unless its garment id is in the wardrobe data.
replacement_garment_id must be a real wardrobe id or null. Avoid change for change's sake.""",
      input=json.dumps(context,ensure_ascii=False),
      text={"format":{"type":"json_schema","name":"look_critique","schema":LOOK_CRITIQUE_SCHEMA,"strict":True}}
    )
    return json.loads(response.output_text)

PRODUCT_WARDROBE_SCHEMA={
 "type":"object",
 "properties":{
  "summary":{"type":"string"},
  "outfits":{"type":"array","minItems":1,"maxItems":4,"items":{
   "type":"object","properties":{
    "label":{"type":"string"},
    "score":{"type":"integer","minimum":0,"maximum":100},
    "owned_garment_ids":{"type":"array","items":{"type":"integer"}},
    "why_it_works":{"type":"string"},
    "style_note":{"type":"string"}
   },
   "required":["label","score","owned_garment_ids","why_it_works","style_note"],
   "additionalProperties":False
  }}
 },
 "required":["summary","outfits"],"additionalProperties":False
}

class ProductWardrobeLooksRequest(BaseModel):
    url:str
    occasion:Optional[str]=""
    max_options:Optional[int]=3

@app.post("/api/product-wardrobe-looks")
def product_wardrobe_looks(req:ProductWardrobeLooksRequest):
    url=(req.url or "").strip()
    if not _public_http_url(url): raise HTTPException(400,"Please use a normal public retailer product URL.")
    # Reuse the same resilient retailer-page extraction used by wardrobe URL import.
    meta=_fetch_product_page_meta(url)
    analysis=None
    if os.getenv("OPENAI_API_KEY") and OpenAI is not None:
        prompt=f"""Identify the menswear product represented by this retailer URL using the supplied page metadata.
URL: {url}
TITLE: {meta.get('title') or ''}
DESCRIPTION: {meta.get('description') or ''}
TEXT: {(meta.get('page_text') or '')[:8000]}
Return supported product facts only. Never invent brand, model, colour, material or fit."""
        try:
            response=tracked_responses_create(OpenAI(),
              model=os.getenv("OPENAI_MODEL","gpt-5.6-terra"),reasoning={"effort":"low"},input=prompt,
              text={"format":{"type":"json_schema","name":"product_url_import","schema":PRODUCT_URL_IMPORT_SCHEMA,"strict":True}}
            )
            analysis=json.loads(response.output_text)
        except Exception:
            analysis=None
    if analysis is None:
        # Existing web-search fallback handles retailers that block direct page fetches.
        search_prompt=f"""Identify the exact menswear product at this retailer URL using live web search: {url}.
Return only facts supported by the retailer or reliable indexed product information. Never guess."""
        response=tracked_responses_create(OpenAI(),
          model=os.getenv("OPENAI_MODEL","gpt-5.6-terra"),reasoning={"effort":"low"},
          tools=[{"type":"web_search"}],tool_choice="auto",input=search_prompt,
          text={"format":{"type":"json_schema","name":"product_url_import","schema":PRODUCT_URL_IMPORT_SCHEMA,"strict":True}}
        )
        analysis=json.loads(response.output_text)

    analysis=canonicalise_analysis_category(analysis)
    con=db()
    wardrobe=[dict(r) for r in con.execute("SELECT * FROM garments ORDER BY id DESC").fetchall()]
    profile=dict(con.execute("SELECT * FROM profile WHERE id=1").fetchone())
    con.close()
    if not wardrobe: raise HTTPException(400,"Add some wardrobe items first.")

    compact=lambda g:{k:g.get(k) for k in ["id","category","garment_type","brand","model_line","labelled_size","colour","material","pattern","fit_cut","fit_feedback","season","formality"]}
    max_options=max(1,min(int(req.max_options or 3),4))
    context={"product":analysis,"product_url":url,"occasion":req.occasion or "not specified",
             "wardrobe":[compact(g) for g in wardrobe],"profile":profile,"max_options":max_options}
    response=tracked_responses_create(OpenAI(),
      model=os.getenv("OPENAI_MODEL","gpt-5.6-terra"),reasoning={"effort":"low"},
      instructions="""Build outfits around the external product using only the user's real wardrobe for the remaining pieces.
Return distinct, practical options. owned_garment_ids must contain only real ids from the supplied wardrobe.
Do not include a wardrobe substitute for the external product itself. Prefer combinations that demonstrate whether buying the product would genuinely add value.""",
      input=json.dumps(context,ensure_ascii=False),
      text={"format":{"type":"json_schema","name":"product_wardrobe_looks","schema":PRODUCT_WARDROBE_SCHEMA,"strict":True}}
    )
    result=json.loads(response.output_text)
    result["outfits"]=result.get("outfits",[])[:max_options]
    return {"product":analysis,"product_url":url,"page_image_url":meta.get("image_url") or "","result":result}

class ProductTryOnRequest(BaseModel):
    garment_ids: list[int]
    product_name: str
    product_brand: Optional[str] = ""
    product_retailer: Optional[str] = ""
    product_image_url: Optional[str] = ""
    product_description: Optional[str] = ""
    product_colour: Optional[str] = ""
    product_material: Optional[str] = ""
    product_fit: Optional[str] = ""
    outfit_label: Optional[str] = "Outfit"
    outfit_reason: Optional[str] = ""
    use_my_likeness: Optional[bool] = True

@app.post("/api/product-tryon")
def product_tryon(req: ProductTryOnRequest):
    enforce_beta_image_limit()
    if not os.getenv("OPENAI_API_KEY") or OpenAI is None:
        raise HTTPException(400, "OpenAI image generation is not connected.")

    ids=[int(x) for x in req.garment_ids if isinstance(x,int) or str(x).isdigit()]
    if not ids:
        raise HTTPException(400, "This outfit does not contain any saved wardrobe garments.")

    con=db()
    ph=",".join("?" for _ in ids)
    rows=[dict(r) for r in con.execute(f"SELECT * FROM garments WHERE id IN ({ph})",ids).fetchall()]
    model_photos=[dict(r) for r in con.execute("SELECT * FROM model_photos ORDER BY id ASC LIMIT 4").fetchall()]
    con.close()

    by_id={g["id"]:g for g in rows}
    garments=[by_id[i] for i in ids if i in by_id]
    if not garments:
        raise HTTPException(404,"The wardrobe pieces could not be found.")

    garment_files=[]; descriptions=[]
    for n,g in enumerate(garments,start=1):
        descriptions.append(
            f"{n}. {g.get('brand') or ''} {g.get('garment_type') or g.get('category') or 'garment'}; "
            f"colour {g.get('colour') or 'unknown'}; material {g.get('material') or 'unknown'}; "
            f"fit {g.get('fit_cut') or 'unknown'}."
        )
        rel=str(g.get("image_path") or "").lstrip("/")
        p=DATA_DIR/rel if rel.startswith(("uploads/","cleaned/","generated/","model-photos/")) else ROOT/rel
        if p.exists(): garment_files.append(p)

    likeness_files=[]
    if req.use_my_likeness:
        for mp in model_photos:
            p=model_photos_dir() /Path(mp.get("image_path") or "").name
            if p.exists(): likeness_files.append(p)
        if not likeness_files:
            raise HTTPException(400,"Add at least one photo in My Model before using Try on me.")

    product_file=None
    if req.product_image_url and _public_http_url(req.product_image_url):
        try:
            request=urllib.request.Request(req.product_image_url,headers={"User-Agent":"Mozilla/5.0","Accept":"image/*"})
            with _safe_urlopen(request,timeout=12) as r:
                ctype=(r.headers.get("Content-Type") or "").lower()
                length=r.headers.get("Content-Length")
                if not ctype.startswith("image/"):
                    raise ValueError("Product reference was not an image.")
                if length and int(length)>12*1024*1024:
                    raise ValueError("Product reference image is too large.")
                data=r.read(12*1024*1024+1)
                if len(data)>12*1024*1024:
                    raise ValueError("Product reference image is too large.")
            if data:
                tmp=generated_dir() /f"product_ref_{uuid.uuid4().hex}.img"
                tmp.write_bytes(data)
                product_file=normalise_image_for_ai(tmp)
                try:
                    if tmp.exists() and tmp!=product_file: tmp.unlink()
                except Exception: pass
        except Exception:
            product_file=None

    prompt=f"""
Create a photorealistic full-body {fashion_audience()} fashion visualisation showing one {fashion_person()}.

SPECIFIC RETAILER PRODUCT TO ADD:
Name: {req.product_name}
Brand: {req.product_brand or 'not specified'}
Retailer: {req.product_retailer or 'not specified'}
Colour: {req.product_colour or 'not specified'}
Material: {req.product_material or 'not specified'}
Fit: {req.product_fit or 'not specified'}
Description: {req.product_description or 'not specified'}

OWNED WARDROBE:
{chr(10).join(descriptions)}

Outfit: {req.outfit_label or 'Outfit'}
Reason: {req.outfit_reason or ''}

PROFILE-SPECIFIC STYLING GUIDANCE:
{styling_profile_guidance()}

If a retailer product image is supplied, reproduce that product as closely as reasonably possible:
colour, silhouette, lapels/collar, buttons, length, texture, pattern and visible construction.
Use the supplied wardrobe images for owned pieces. Show the complete outfit head-to-toe.
If personal reference photos are supplied, preserve the user's visible identity, face, hair,
skin tone and overall proportions as closely as reasonably possible.
Do not invent visible logos. This is an AI styling visualisation, not a guarantee of exact fit.
"""

    client=OpenAI()
    image_model=os.getenv("OPENAI_IMAGE_MODEL","gpt-image-2")
    refs=[]
    if req.use_my_likeness: refs.extend(likeness_files[:3])
    if product_file and product_file.exists(): refs.append(product_file)
    refs.extend(garment_files[:5])
    opened=[]
    try:
        if refs:
            opened=[open(p,"rb") for p in refs[:8]]
            result=client.images.edit(model=image_model,image=opened,prompt=prompt,size="1024x1536",quality="medium")
        else:
            result=client.images.generate(model=image_model,prompt=prompt,size="1024x1536",quality="medium")
    except Exception as exc:
        raise HTTPException(502,f"Product try-on failed: {str(exc)[:350]}") from exc
    finally:
        for f in opened:
            try:f.close()
            except Exception:pass

    if not result or not getattr(result,"data",None):
        raise HTTPException(502,"The image model did not return an image.")
    b64=getattr(result.data[0],"b64_json",None)
    if not b64:
        raise HTTPException(502,"The image model returned an unsupported image response.")

    filename=f"product_tryon_{uuid.uuid4().hex}.png"
    out=generated_dir() /filename
    out.write_bytes(base64.b64decode(b64))
    record_usage_event("image_generation","/api/product-tryon",1,metadata={
      "model":image_model,"size":"1024x1536","quality":"medium","reference_images":len(refs[:8])
    })
    return {
        "ok":True,
        "image_path":f"/generated/{filename}",
        "notice":"AI try-on using the selected retailer product and your wardrobe. Useful for judging the overall look, not exact fit."
    }

class Feedback(BaseModel):
    outfit: dict
    rating: str

@app.post("/api/feedback")
def save_feedback(f: Feedback):
    con=db()
    con.execute("INSERT INTO feedback(outfit_json,rating) VALUES (?,?)",(json.dumps(f.outfit),f.rating))
    con.commit(); con.close()
    return {"ok":True}
