
import os, json, base64, sqlite3, mimetypes, uuid
from pathlib import Path
from typing import Optional
from fastapi import FastAPI, UploadFile, File, Form, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
try:
    from openai import OpenAI
except Exception:
    OpenAI = None

from PIL import Image, ImageOps, UnidentifiedImageError
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

app = FastAPI(title="Personal Stylist V2")
app.mount("/static", StaticFiles(directory=ROOT/"static"), name="static")
app.mount("/uploads", StaticFiles(directory=UPLOADS), name="uploads")

def db():
    con = sqlite3.connect(DB)
    con.row_factory = sqlite3.Row
    return con

def init_db():
    con = db()
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
      category TEXT, garment_type TEXT, brand TEXT, model_line TEXT,
      labelled_size TEXT, colour TEXT, material TEXT, pattern TEXT,
      fit_cut TEXT, fit_feedback TEXT, season TEXT, formality TEXT,
      notes TEXT, ai_confidence REAL DEFAULT 0, created_at TEXT DEFAULT CURRENT_TIMESTAMP
    );

    CREATE TABLE IF NOT EXISTS feedback (
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      outfit_json TEXT, rating TEXT, created_at TEXT DEFAULT CURRENT_TIMESTAMP
    );
    """)
    con.commit()
    con.close()

init_db()

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
    con.close()
    return rows


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
    row = con.execute("SELECT image_path FROM garments WHERE id=?", (gid,)).fetchone()
    con.execute("DELETE FROM garments WHERE id=?", (gid,))
    con.commit(); con.close()
    if row:
        try:
            p = ROOT / row["image_path"].lstrip("/")
            if p.exists(): p.unlink()
        except Exception:
            pass
    return {"ok": True}

def normalise_image_for_ai(source_path: Path) -> Path:
    """Convert uploaded images (including iPhone HEIC/HEIF) to JPEG for AI input."""
    try:
        with Image.open(source_path) as im:
            im = ImageOps.exif_transpose(im)

            # Flatten transparency before JPEG conversion.
            if im.mode in ("RGBA", "LA") or (im.mode == "P" and "transparency" in im.info):
                rgba = im.convert("RGBA")
                background = Image.new("RGB", rgba.size, "white")
                background.paste(rgba, mask=rgba.getchannel("A"))
                im = background
            else:
                im = im.convert("RGB")

            # Preserve garment detail without sending unnecessarily huge images.
            max_side = 2048
            if max(im.size) > max_side:
                scale = max_side / max(im.size)
                im = im.resize((round(im.width * scale), round(im.height * scale)))

            out_path = source_path.with_suffix(".jpg")
            im.save(out_path, format="JPEG", quality=92, optimize=True)

        if out_path != source_path and source_path.exists():
            try:
                source_path.unlink()
            except Exception:
                pass
        return out_path

    except (UnidentifiedImageError, OSError, ValueError) as exc:
        raise HTTPException(
            status_code=400,
            detail="I couldn't read that photo. Please try taking it again or choose another image."
        ) from exc


def encode_image(path: Path):
    data = base64.b64encode(path.read_bytes()).decode()
    return f"data:image/jpeg;base64,{data}"

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
    if len(data) > 20 * 1024 * 1024:
        raise HTTPException(400, "That photo is too large. Please choose an image under 20 MB.")

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

    return {
      "image_path": f"/uploads/{image_path.name}",
      "analysis": result,
      "ai_enabled": result is not None
    }

@app.post("/api/garments")
async def add_garment(
    image_path: str = Form(...),
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
      (image_path,category,garment_type,brand,model_line,labelled_size,colour,material,pattern,fit_cut,fit_feedback,season,formality,notes,ai_confidence)
      VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
      (image_path,category,garment_type,brand,model_line,labelled_size,colour,material,pattern,fit_cut,fit_feedback,season,formality,notes,ai_confidence))
    con.commit(); gid=cur.lastrowid; con.close()
    return {"ok":True,"id":gid}

class OutfitRequest(BaseModel):
    occasion: str
    temperature_c: float
    weather: str=""
    location: str=""
    anchor_id: Optional[int]=None

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

class Feedback(BaseModel):
    outfit: dict
    rating: str

@app.post("/api/feedback")
def save_feedback(f: Feedback):
    con=db()
    con.execute("INSERT INTO feedback(outfit_json,rating) VALUES (?,?)",(json.dumps(f.outfit),f.rating))
    con.commit(); con.close()
    return {"ok":True}
