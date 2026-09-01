
import os, json, base64, sqlite3, mimetypes, uuid, urllib.request, urllib.error, re
from pathlib import Path
from typing import Optional
from fastapi import FastAPI, UploadFile, File, Form, HTTPException, BackgroundTasks
from fastapi.responses import FileResponse
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
# On Render set DATA_DIR=/var/data, matching the mounted persistent disk.
# Local development falls back to a project "data" directory.
DATA_DIR = Path(os.getenv("DATA_DIR", str(ROOT / "data")))
DATA_DIR.mkdir(parents=True, exist_ok=True)

DB = DATA_DIR / "stylist.db"
UPLOADS = DATA_DIR / "uploads"
UPLOADS.mkdir(parents=True, exist_ok=True)

CLEANED = DATA_DIR / "cleaned"
CLEANED.mkdir(parents=True, exist_ok=True)

GENERATED = DATA_DIR / "generated"
GENERATED.mkdir(parents=True, exist_ok=True)

MODEL_PHOTOS = DATA_DIR / "model_photos"
MODEL_PHOTOS.mkdir(parents=True, exist_ok=True)

app = FastAPI(title="Personal Stylist V2")
app.mount("/static", StaticFiles(directory=ROOT/"static"), name="static")
app.mount("/uploads", StaticFiles(directory=UPLOADS), name="uploads")
app.mount("/cleaned", StaticFiles(directory=CLEANED), name="cleaned")
app.mount("/generated", StaticFiles(directory=GENERATED), name="generated")
app.mount("/model-photos", StaticFiles(directory=MODEL_PHOTOS), name="model-photos")

def db():
    con = sqlite3.connect(DB)
    con.row_factory = sqlite3.Row
    return con


WARDROBE_CATEGORY_ORDER = [
    "Jackets & Outerwear", "Knitwear", "Shirts", "Polos & T-Shirts",
    "Trousers", "Shorts", "Footwear", "Accessories", "Other",
]

def canonical_wardrobe_category(category: str = "", garment_type: str = "") -> str:
    raw = re.sub(r"\s+", " ", f"{category or ''} {garment_type or ''}".strip().lower())
    rules = [
        ("Footwear", ["footwear","shoe","shoes","sneaker","sneakers","trainer","trainers","loafer","loafers","boot","boots","derby","derbies","brogue","brogues","oxford shoe","monk strap","espadrille","slipper"]),
        ("Shorts", ["shorts","swim short","swim shorts"]),
        ("Trousers", ["trouser","trousers","chino","chinos","jean","jeans","jogger","joggers","cargo trouser","cargo pants","pants"]),
        ("Jackets & Outerwear", ["outerwear","jacket","jackets","coat","coats","blazer","blazers","gilet","gilets","overshirt","overshirts","parka","raincoat","mac"]),
        ("Knitwear", ["knitwear","jumper","jumpers","sweater","sweaters","cardigan","cardigans","quarter zip","half zip","roll neck","turtleneck","knit"]),
        ("Polos & T-Shirts", ["polo","polo shirt","t-shirt","t shirt","tee","tees","tshirt","top","tops"]),
        ("Shirts", ["shirt","shirts","oxford shirt","dress shirt","casual shirt","linen shirt"]),
        ("Accessories", ["accessory","accessories","tie","ties","belt","belts","hat","hats","cap","caps","beanie","scarf","scarves","glove","gloves","bag","bags","watch","watches"]),
    ]
    for canonical, words in rules:
        if any(w in raw for w in words): return canonical
    for canonical in WARDROBE_CATEGORY_ORDER:
        if (category or "").strip().casefold()==canonical.casefold(): return canonical
    return "Other"

def normalise_existing_wardrobe_categories():
    con=db(); rows=con.execute("SELECT id, category, garment_type FROM garments").fetchall(); changed=0
    for row in rows:
        new_cat=canonical_wardrobe_category(row["category"] or "",row["garment_type"] or "")
        if (row["category"] or "").strip()!=new_cat:
            con.execute("UPDATE garments SET category=? WHERE id=?",(new_cat,row["id"])); changed+=1
    if changed: con.commit()
    con.close(); return changed

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
      preferred_fit TEXT, style_notes TEXT, brand_notes TEXT
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
        "ALTER TABLE garments ADD COLUMN purchase_url TEXT DEFAULT ''",
        "ALTER TABLE garments ADD COLUMN purchase_date TEXT DEFAULT ''",
        "ALTER TABLE garments ADD COLUMN fit_review_status TEXT DEFAULT ''",
        "ALTER TABLE garments ADD COLUMN fit_rating INTEGER",
        "ALTER TABLE garments ADD COLUMN fit_chest TEXT DEFAULT ''",
        "ALTER TABLE garments ADD COLUMN fit_waist TEXT DEFAULT ''",
        "ALTER TABLE garments ADD COLUMN fit_length TEXT DEFAULT ''",
        "ALTER TABLE garments ADD COLUMN fit_sleeve TEXT DEFAULT ''",
        "ALTER TABLE garments ADD COLUMN fit_shoulders TEXT DEFAULT ''",
        "ALTER TABLE garments ADD COLUMN fit_notes TEXT DEFAULT ''",
        "ALTER TABLE garments ADD COLUMN fit_reviewed_at TEXT"
    ]:
        try:
            con.execute(sql)
        except sqlite3.OperationalError:
            pass
    con.commit()
    con.close()

init_db()
normalise_existing_wardrobe_categories()

@app.get("/")
def home():
    return FileResponse(ROOT/"static"/"index.html")

@app.get("/api/health")
def health():
    return {"ok": True, "ai_enabled": bool(os.getenv("OPENAI_API_KEY")) and OpenAI is not None, "data_dir": str(DATA_DIR), "database": str(DB)}

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

@app.put("/api/profile")
def save_profile(p: Profile):
    con = db()
    con.execute("""UPDATE profile SET name=?,height_cm=?,chest_cm=?,waist_cm=?,hips_cm=?,thigh_cm=?,
        inseam_cm=?,sleeve_cm=?,neck_cm=?,preferred_fit=?,style_notes=?,brand_notes=? WHERE id=1""",
        (p.name,p.height_cm,p.chest_cm,p.waist_cm,p.hips_cm,p.thigh_cm,p.inseam_cm,p.sleeve_cm,p.neck_cm,p.preferred_fit,p.style_notes,p.brand_notes))
    con.commit(); con.close()
    return {"ok": True}

@app.get("/api/garments")
def garments():
    con = db()
    rows = [dict(r) for r in con.execute("SELECT * FROM garments ORDER BY id DESC").fetchall()]

    # V3.6 safety repair: a cleaned/display image is disposable; the uploaded
    # original is the source of truth. If a display file has disappeared, fall
    # back to the original automatically. Also backfill original_image_path for
    # older rows where the current image is itself an uploaded source image.
    changed = False
    for row in rows:
        image_rel = row.get("image_path") or ""
        original_rel = row.get("original_image_path") or ""
        image_exists = bool(image_rel) and resolve_saved_image_path(image_rel).exists()
        original_exists = bool(original_rel) and resolve_saved_image_path(original_rel).exists()

        if not original_exists and image_exists and str(image_rel).startswith("/uploads/"):
            original_rel = image_rel
            row["original_image_path"] = original_rel
            con.execute("UPDATE garments SET original_image_path=? WHERE id=?", (original_rel, row["id"]))
            original_exists = True
            changed = True

        if not image_exists and original_exists:
            row["image_path"] = original_rel
            con.execute("UPDATE garments SET image_path=? WHERE id=?", (original_rel, row["id"]))
            changed = True

    if changed:
        con.commit()
    con.close()
    return rows



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

def run_garment_enrichment(gid: int):
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
Research this real men's garment using the live web.

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
        response = client.responses.create(
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


@app.get("/api/garments/{gid}/detail")
def garment_detail(gid: int):
    con = db()
    row = con.execute("SELECT * FROM garments WHERE id=?", (gid,)).fetchone()
    if not row:
        con.close()
        raise HTTPException(404, "Garment not found")

    garment = dict(row)
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

    background_tasks.add_task(run_garment_enrichment, gid)
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

    con.execute("""UPDATE garments SET
        category=?, garment_type=?, brand=?, model_line=?, labelled_size=?,
        colour=?, material=?, pattern=?, fit_cut=?, fit_feedback=?,
        season=?, formality=?, notes=?
        WHERE id=?""",
        (g.category, g.garment_type, g.brand, g.model_line, g.labelled_size,
         g.colour, g.material, g.pattern, g.fit_cut, g.fit_feedback,
         g.season, g.formality, g.notes, gid))
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
                rel_path = str(rel).lstrip("/")
                if rel_path.startswith("cleaned/"):
                    p = DATA_DIR / rel_path
                elif rel_path.startswith("uploads/"):
                    p = DATA_DIR / rel_path
                else:
                    p = ROOT / rel_path
                if p.exists():
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

            out_path = CLEANED / f"catalogue_{uuid.uuid4().hex}.jpg"
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
        out = CLEANED / f"ai_isolated_{uuid.uuid4().hex}.png"
        canvas.convert("RGB").save(out, "PNG", optimize=True)
        return out
    except CleanupServiceError:
        raise
    except Exception as exc:
        raise CleanupServiceError(f"The cleaned image could not be prepared: {exc}. Your original photo is unchanged.")


def resolve_saved_image_path(rel_path: str) -> Path:
    rel = str(rel_path or "").lstrip("/")
    if rel.startswith("uploads/") or rel.startswith("cleaned/") or rel.startswith("model-photos/") or rel.startswith("generated/"):
        return DATA_DIR / rel
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
    prompt = """Analyse this photograph as a careful menswear wardrobe cataloguer.
Identify only details reasonably visible from the image. Do not invent brand, size,
fabric composition, model/line or fit if they cannot be seen or inferred with reasonable confidence.
Use empty strings for unknown fields. Colour should be specific (e.g. stone, cream, navy, sage),
not merely 'light'. Season and formality should be practical menswear classifications.
The notes field should mention uncertainty or useful visible details."""
    response = client.responses.create(
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
    raw_path = UPLOADS / raw_name

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
          if catalogue_path.parent == CLEANED
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
    raw=UPLOADS/f"{uuid.uuid4().hex}{suffix}"
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
        display=(f"/cleaned/{catalogue.name}" if catalogue.parent==CLEANED else f"/uploads/{normal.name}")
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
      (image_path,original_image_path,category,garment_type,brand,model_line,labelled_size,colour,material,pattern,fit_cut,fit_feedback,season,formality,notes,ai_confidence)
      VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
      (image_path,original_image_path or image_path,canonical_wardrobe_category(category, garment_type),garment_type,brand,model_line,labelled_size,colour,material,pattern,fit_cut,fit_feedback,season,formality,notes,ai_confidence))
    con.commit(); gid=cur.lastrowid; con.close()
    return {"ok":True,"id":gid}

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

STYLIST_INSTRUCTIONS = """You are a highly skilled personal menswear stylist for one individual.
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


@app.get("/api/style-learning")
def style_learning():
    con = db()
    rows = [dict(r) for r in con.execute(
        "SELECT rating, outfit_json, created_at FROM feedback ORDER BY id DESC LIMIT 100"
    ).fetchall()]
    garments = [dict(r) for r in con.execute(
        "SELECT brand, garment_type, fit_feedback, colour, material, formality FROM garments ORDER BY id DESC"
    ).fetchall()]
    con.close()

    counts = {}
    for r in rows:
        counts[r["rating"]] = counts.get(r["rating"], 0) + 1

    perfect_fit_brands = {}
    for g in garments:
        if (g.get("fit_feedback") or "").lower() == "perfect fit" and g.get("brand"):
            perfect_fit_brands[g["brand"]] = perfect_fit_brands.get(g["brand"], 0) + 1

    top_brands = sorted(perfect_fit_brands.items(), key=lambda x: (-x[1], x[0]))[:5]

    return {
        "feedback_count": len(rows),
        "ratings": counts,
        "perfect_fit_brands": [{"brand": b, "count": c} for b, c in top_brands],
        "message": "The stylist uses repeated patterns in your feedback and fit history; one-off ratings are treated cautiously."
    }

@app.post("/api/outfits")
def outfits(req: OutfitRequest):
    con = db()
    garments = [dict(r) for r in con.execute("SELECT * FROM garments ORDER BY id DESC").fetchall()]
    profile = dict(con.execute("SELECT * FROM profile WHERE id=1").fetchone())
    recent_feedback = [dict(r) for r in con.execute(
        "SELECT rating, outfit_json, created_at FROM feedback ORDER BY id DESC LIMIT 30"
    ).fetchall()]
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
      "learning_rules": {
        "use_repeated_patterns_not_single_reactions": True,
        "perfect_fit_feedback_is_high_value": True,
        "rejected_outfits_should_reduce_similar_future_combinations": True,
        "liked_or_loved_outfits_should_increase_similar_future_combinations": True
      }
    }
    response = client.responses.create(
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




class PackingRequest(BaseModel):
    destination: str
    days: int = 5
    trip_type: str = "Mixed"
    weather: str = ""
    activities: str = ""
    dress_needs: str = ""
    laundry: str = "No"
    shopping_allowed: bool = True
    notes: str = ""

PACKING_SCHEMA = {
 "type":"object","properties":{
  "summary":{"type":"string"},
  "packing_list":{"type":"array","items":{"type":"object","properties":{
   "garment_id":{"type":"integer"},"why_pack":{"type":"string"},"wear_count":{"type":"integer"}
  },"required":["garment_id","why_pack","wear_count"],"additionalProperties":False}},
  "outfit_plan":{"type":"array","items":{"type":"object","properties":{
   "day":{"type":"string"},"occasion":{"type":"string"},"garment_ids":{"type":"array","items":{"type":"integer"}},"note":{"type":"string"}
  },"required":["day","occasion","garment_ids","note"],"additionalProperties":False}},
  "missing_items":{"type":"array","items":{"type":"string"}},
  "packing_tip":{"type":"string"}
 },"required":["summary","packing_list","outfit_plan","missing_items","packing_tip"],"additionalProperties":False
}

@app.post("/api/help-me-pack")
def help_me_pack(req: PackingRequest):
    con=db()
    garments=[dict(r) for r in con.execute("SELECT * FROM garments ORDER BY id DESC").fetchall()]
    profile=dict(con.execute("SELECT * FROM profile WHERE id=1").fetchone())
    feedback=[dict(r) for r in con.execute("SELECT rating,outfit_json,created_at FROM feedback ORDER BY id DESC LIMIT 30").fetchall()]
    con.close()
    if len(garments)<3:
        raise HTTPException(400,"Add at least three wardrobe items before using Help Me Pack.")
    if not os.getenv("OPENAI_API_KEY") or OpenAI is None:
        raise HTTPException(503,"Help Me Pack needs the AI stylist connection.")
    instructions="""You are a meticulous personal menswear stylist and efficient travel packer. Build a practical capsule from the user's ACTUAL wardrobe. Reuse versatile garments across outfits to reduce luggage. Respect destination, trip length, activities, dress needs, weather, laundry access, fit history and style feedback. Use only supplied garment IDs for owned pieces. Never claim the user owns something absent from the wardrobe. If something genuinely useful is missing, list it briefly under missing_items; if shopping_allowed is false, keep missing_items empty. Include enough outfit planning to make the suitcase useful, but do not force a unique outfit for every day when rewearing is sensible."""
    context={"trip":req.model_dump(),"profile":profile,"wardrobe":garments,"recent_feedback":feedback}
    client=OpenAI()
    response=client.responses.create(model=os.getenv("OPENAI_MODEL","gpt-5.6-terra"),reasoning={"effort":"medium"},instructions=instructions,input=json.dumps(context,ensure_ascii=False),text={"format":{"type":"json_schema","name":"packing_plan","schema":PACKING_SCHEMA,"strict":True}})
    return json.loads(response.output_text)

@app.get("/api/model-photos")
def get_model_photos():
    con = db()
    rows = [dict(r) for r in con.execute("SELECT * FROM model_photos ORDER BY id ASC").fetchall()]
    con.close()
    return rows

@app.post("/api/model-photos")
async def add_model_photo(file: UploadFile = File(...), label: str = Form("")):
    suffix = Path(file.filename or "portrait").suffix.lower() or ".upload"
    raw_path = MODEL_PHOTOS / f"{uuid.uuid4().hex}{suffix}"
    data = await file.read()
    if not data:
        raise HTTPException(400, "The uploaded photo was empty.")
    if len(data) > 15 * 1024 * 1024:
        raise HTTPException(400, "That photo is too large. Please choose an image under 15 MB.")
    raw_path.write_bytes(data)
    image_path = normalise_image_for_ai(raw_path)
    if image_path.parent != MODEL_PHOTOS:
        target = MODEL_PHOTOS / image_path.name
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
        p = MODEL_PHOTOS / Path(row["image_path"]).name
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
        rel = (g.get("image_path") or "").lstrip("/")
        if rel.startswith("uploads/"):
            p = DATA_DIR / rel
        else:
            p = ROOT / rel
        if p.exists():
            garment_image_files.append(p)

    likeness_files = []
    if req.use_my_likeness:
        for mp in model_photos:
            p = MODEL_PHOTOS / Path(mp.get("image_path") or "").name
            if p.exists():
                likeness_files.append(p)
        if not likeness_files:
            raise HTTPException(400, "Add at least one photo in My Model before using View on me.")

    height = profile.get("height_cm")
    preferred_fit = profile.get("preferred_fit") or "natural contemporary fit"
    style_notes = profile.get("style_notes") or ""

    prompt = f"""
Create a photorealistic full-body men's fashion lookbook image showing one adult male model
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
- Use the reference garment images as closely as reasonably possible for colour, material,
  silhouette, pattern and footwear.
- Do not add visible logos or brand marks that are not clearly present in the reference images.
- Do not invent extra statement garments.
- If a small neutral accessory is needed for realism, keep it unobtrusive.
- Show the entire outfit head-to-toe, including footwear.
- Natural standing pose, premium contemporary menswear editorial photography.
- Neutral understated studio or softly lit architectural background.
- If use_my_likeness is true, use the supplied personal reference photos to preserve the user's visible identity, face, hair, skin tone and overall proportions as closely as reasonably possible.
- If use_my_likeness is false, use a generic adult male model who does not resemble any particular real person.
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
            reference_files.extend(likeness_files[:3])
        reference_files.extend(garment_image_files[:5])
        if reference_files:
            opened = [open(p, "rb") for p in reference_files[:8]]
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
    out_path = GENERATED / filename
    out_path.write_bytes(base64.b64decode(b64))

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
          "why_this_adds_value","wardrobe_synergy_score","owned_garment_ids",
          "outfit_ideas","size_fit_guidance","shopping_spec","search_phrase","priority"
        ],
        "additionalProperties": False
      }
    }
  },
  "required": ["summary", "recommendations"],
  "additionalProperties": False
}

SHOPPING_STYLIST_INSTRUCTIONS = """You are the wardrobe-planning and shopping specialist for one male user.

Your job is not to recommend random fashionable products. Analyse the user's ACTUAL wardrobe,
measurements, fit history, brand notes, and style feedback, then identify purchases that add the most value.

PRINCIPLES:
- Wardrobe first. Do not recommend replacing something the user already owns unless there is a clear reason.
- Maximise wardrobe synergy: favour a purchase that creates many strong outfits with existing pieces.
- Respect the user's requested goal. If they ask for a blazer, recommend the best blazer specification rather than changing category.
- Be specific about shade, fabric, texture, construction, seasonality, formality and fit.
- Use only supplied wardrobe garment IDs when referencing owned items.
- Never claim a live product, price, stock level or retailer availability unless live retailer data is actually supplied.
- For size guidance, combine body measurements, brand/model notes and perfect-fit garment history, but express uncertainty clearly.
- Produce recommendations that are meaningfully different from one another.
- The shopping_spec should be precise enough to search retailers later.
- search_phrase should be concise and useful for a future live shopping search.
"""

@app.post("/api/wardrobe-gaps")
def wardrobe_gaps(req: WardrobeGapRequest):
    con = db()
    garment_rows = [dict(r) for r in con.execute("SELECT * FROM garments ORDER BY id DESC").fetchall()]
    profile_row = dict(con.execute("SELECT * FROM profile WHERE id=1").fetchone())
    feedback_rows = [dict(r) for r in con.execute(
        "SELECT rating, outfit_json FROM feedback ORDER BY id DESC LIMIT 12"
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
        "fit_notes": g.get("fit_notes") or ""
    } for g in garment_rows]

    profile = {
        k: profile_row.get(k)
        for k in [
            "height_cm","chest_cm","waist_cm","hips_cm","thigh_cm",
            "inseam_cm","sleeve_cm","neck_cm","preferred_fit",
            "style_notes","brand_notes"
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
      "max_recommendations": max_recs,
      "profile": profile,
      "wardrobe": garments,
      "recent_feedback": feedback
    }

    client = OpenAI()
    response = client.responses.create(
      model=os.getenv("OPENAI_MODEL","gpt-5.6-terra"),
      reasoning={"effort":"low"},
      instructions=SHOPPING_STYLIST_INSTRUCTIONS,
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

def _fetch_product_page_meta(url: str) -> dict:
    if not _public_http_url(url):raise HTTPException(400,"Please use a normal public retailer product URL.")
    try:
        import urllib.request, html as _html, re as _re
        from urllib.parse import urljoin
        req=urllib.request.Request(url,headers={"User-Agent":"Mozilla/5.0","Accept":"text/html,application/xhtml+xml"})
        with urllib.request.urlopen(req,timeout=12) as r:
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
        with urllib.request.urlopen(req,timeout=12) as r:
            ctype=(r.headers.get("Content-Type") or "").lower()
            if not ctype.startswith("image/"):return "",""
            data=r.read(12*1024*1024)
        if not data:return "",""
        suffix=".png" if "png" in ctype else ".webp" if "webp" in ctype else ".jpg"
        raw=UPLOADS/f"urlimport_{uuid.uuid4().hex}{suffix}"; raw.write_bytes(data); normal=normalise_image_for_ai(raw); catalogue=create_catalogue_image(normal)
        display=f"/cleaned/{catalogue.name}" if catalogue.parent==CLEANED else f"/uploads/{normal.name}"
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
            prompt=f"""Extract the menswear product details from this retailer page. The user owns or has bought the item.
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
- Keep notes factual and concise."""
            try:
                response=client.responses.create(
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
            search_prompt=f"""Identify the exact menswear product represented by this retailer URL and extract supported facts from the live web.

EXACT PRODUCT URL:
{url}

The retailer may block direct server access. Use live web search, prioritising the official brand/retailer result and reliable indexed snippets.

Rules:
- Search specifically for the exact product name/slug, official brand result, retailer snippets and reputable stockists/reviews where useful.
- Brand, model/line, colour, material and pattern must be supported by web evidence. Never invent those fields.
- labelled_size must be empty because the URL does not establish which size the user owns.
- fit_cut: use the retailer/brand's stated fit when available. If not stated but the cut is reasonably classifiable from reliable product descriptions, provide a concise stylist classification.
- season: classify practical seasonality from the known garment type and evidenced material (for example "Spring/Summer" or "Year-round"). This is a stylist classification, not a retailer claim.
- formality: classify the garment's normal menswear formality from its known type/design (for example "Casual", "Smart casual", "Business casual", "Formal"). This is a stylist classification.
- If material cannot be established from a reliable web result, leave material empty rather than guessing.
- category and garment_type should describe the exact item.
- notes should be short and factual; if season/formality/fit_cut are stylist classifications, do not describe them as retailer-provided facts.
- If exact product identification is uncertain, leave uncertain factual fields empty and use low confidence.
"""
            try:
                response=client.responses.create(
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

    analysis["category"]=canonical_wardrobe_category(analysis.get("category") or "",analysis.get("garment_type") or "")
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
          "confidence": {"type": "string", "enum": ["high","medium","low"]}
        },
        "required": ["name","brand","retailer","price","url","image_url","colour","material","fit","size_note","why_it_matches","confidence"],
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

    fit_con=db()
    fit_rows=[dict(r) for r in fit_con.execute("""
      SELECT brand,labelled_size,fit_rating,fit_chest,fit_waist,fit_length,fit_sleeve,fit_shoulders,fit_notes
      FROM garments
      WHERE fit_review_status='confirmed' AND brand<>''
      ORDER BY fit_reviewed_at DESC LIMIT 30
    """).fetchall()]
    fit_con.close()
    fit_learning="\n".join([
      f"- {r.get('brand') or ''} size {r.get('labelled_size') or ''}: {r.get('fit_rating') or 'n/a'}/5; "
      f"chest {r.get('fit_chest') or '—'}, waist {r.get('fit_waist') or '—'}, "
      f"length {r.get('fit_length') or '—'}, sleeve {r.get('fit_sleeve') or '—'}, "
      f"shoulders {r.get('fit_shoulders') or '—'}. {r.get('fit_notes') or ''}"
      for r in fit_rows
    ])

    prompt = f"""
Search the live web for men's clothing products currently offered by reputable retailers that match this specification.

SEARCH PHRASE: {req.search_phrase}
CATEGORY: {req.category or 'not specified'}
SHOPPING SPECIFICATION: {req.shopping_spec or 'not specified'}
BUDGET: {req.budget or 'not specified'}
SIZE/FIT GUIDANCE: {req.size_fit_guidance or 'not specified'}

CONFIRMED REAL-WORLD FIT HISTORY:
{fit_learning or 'No confirmed fit reviews yet.'}

The user is in the United Kingdom. Prefer UK retailer/product pages and GBP prices.
Find up to 6 genuinely relevant products across useful price points where possible.

Rules:
- Only return a product if you found a real product or retailer page for it on the live web.
- URL must be the actual source/product URL you found; never invent a URL.
- Never invent price, stock, material, fit or sizing. If not found, return an empty string for that field.
- image_url is optional in practice: only return it when a direct usable product image URL is explicitly available in the search result/source; otherwise return an empty string.
- Do not claim a size is in stock unless the source explicitly establishes it.
- size_note should explain how the known product/brand fit relates to the supplied fit guidance; if evidence is insufficient, say sizing needs confirmation.
- Prefer official brand or retailer product pages over aggregators.
"""

    client = OpenAI()
    try:
        response = client.responses.create(
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
        return json.loads(response.output_text)
    except Exception as exc:
        raise HTTPException(502, f"Live product search failed: {str(exc)[:350]}")



class FavouriteOutfitRequest(BaseModel):
    outfit: dict
    request_text: Optional[str] = ""
    weather_context: Optional[str] = ""
    visual_path: Optional[str] = ""

@app.get("/api/outfit-favourites")
def get_outfit_favourites():
    con=db()
    rows=[dict(r) for r in con.execute(
        "SELECT * FROM outfit_favourites ORDER BY id DESC"
    ).fetchall()]
    con.close()
    for row in rows:
        try: row["outfit"]=json.loads(row.get("outfit_json") or "{}")
        except Exception: row["outfit"]={}
    return rows

@app.post("/api/outfit-favourites")
def save_outfit_favourite(req: FavouriteOutfitRequest):
    outfit=req.outfit or {}
    label=str(outfit.get("label") or "Saved look")
    con=db()
    cur=con.execute("""
      INSERT INTO outfit_favourites
      (label,outfit_json,request_text,weather_context,visual_path)
      VALUES (?,?,?,?,?)
    """,(label,json.dumps(outfit,ensure_ascii=False),req.request_text or "",
         req.weather_context or "",req.visual_path or ""))
    fid=cur.lastrowid
    con.commit()
    row=dict(con.execute("SELECT * FROM outfit_favourites WHERE id=?",(fid,)).fetchone())
    con.close()
    return row

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

This weather will be used by a menswear stylist. Use current forecast information from reliable
weather sources. If the requested date is outside reliable forecast range, say so and use low
confidence rather than inventing conditions.

Summarise temperatures in Celsius, precipitation/rain risk, wind and practical clothing implications.
The styling_context should be concise and useful for choosing layers, fabrics, outerwear and footwear.
"""
    try:
        response=OpenAI().responses.create(
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

STYLIST_V4_INSTRUCTIONS = """You are a high-level personal menswear stylist for one male user.

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
      "wardrobe": garments,
      "recent_feedback": feedback
    }

    client = OpenAI()
    response = client.responses.create(
      model=os.getenv("OPENAI_MODEL","gpt-5.6-terra"),
      reasoning={"effort":"low"},
      instructions=STYLIST_V4_INSTRUCTIONS,
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



class ShortlistProductRequest(BaseModel):
    product: dict
    context: Optional[dict] = None

def _safe_web_url(url: str) -> bool:
    try:
        from urllib.parse import urlparse
        p=urlparse(url or "")
        return p.scheme in ("http","https") and bool(p.netloc)
    except Exception:
        return False

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
        with urllib.request.urlopen(req,timeout=8) as r:
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
    feedback=(
        f"Fit review: {rating or 'unrated'}/5. "
        f"Chest {req.fit_chest or '—'}; waist {req.fit_waist or '—'}; "
        f"length {req.fit_length or '—'}; sleeve {req.fit_sleeve or '—'}; "
        f"shoulders {req.fit_shoulders or '—'}. {req.fit_notes or ''}"
    ).strip()
    con.execute("""
      UPDATE garments SET
        labelled_size=CASE WHEN ?<>'' THEN ? ELSE labelled_size END,
        fit_review_status='confirmed', fit_rating=?,
        fit_chest=?,fit_waist=?,fit_length=?,fit_sleeve=?,fit_shoulders=?,
        fit_notes=?,fit_reviewed_at=?,fit_feedback=?
      WHERE id=?
    """,(req.labelled_size or "",req.labelled_size or "",rating,
         req.fit_chest or "",req.fit_waist or "",req.fit_length or "",
         req.fit_sleeve or "",req.fit_shoulders or "",req.fit_notes or "",
         reviewed,feedback,gid))
    con.commit()
    updated=dict(con.execute("SELECT * FROM garments WHERE id=?",(gid,)).fetchone())
    con.close()
    return updated

@app.get("/api/fit-learning")
def get_fit_learning():
    con=db()
    rows=[dict(r) for r in con.execute("""
      SELECT brand,labelled_size,fit_rating,fit_chest,fit_waist,fit_length,
             fit_sleeve,fit_shoulders,fit_notes,fit_reviewed_at
      FROM garments
      WHERE fit_review_status='confirmed' AND brand<>''
      ORDER BY fit_reviewed_at DESC LIMIT 100
    """).fetchall()]
    con.close()
    return rows

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
            p=MODEL_PHOTOS/Path(mp.get("image_path") or "").name
            if p.exists(): likeness_files.append(p)
        if not likeness_files:
            raise HTTPException(400,"Add at least one photo in My Model before using Try on me.")

    product_file=None
    if req.product_image_url:
        try:
            import urllib.request
            from urllib.parse import urlparse
            parsed=urlparse(req.product_image_url)
            if parsed.scheme in ("http","https"):
                request=urllib.request.Request(req.product_image_url,headers={"User-Agent":"Mozilla/5.0"})
                with urllib.request.urlopen(request,timeout=12) as r:
                    data=r.read(12*1024*1024)
                if data:
                    tmp=GENERATED/f"product_ref_{uuid.uuid4().hex}.img"
                    tmp.write_bytes(data)
                    product_file=normalise_image_for_ai(tmp)
                    try:
                        if tmp.exists() and tmp!=product_file: tmp.unlink()
                    except Exception: pass
        except Exception:
            product_file=None

    prompt=f"""
Create a photorealistic full-body menswear visualisation.

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
    out=GENERATED/filename
    out.write_bytes(base64.b64decode(b64))
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
