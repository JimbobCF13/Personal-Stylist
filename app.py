
import os, json, base64, sqlite3, mimetypes, uuid, urllib.request, urllib.error
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
      original_image_path TEXT,
      category TEXT, garment_type TEXT, brand TEXT, model_line TEXT,
      labelled_size TEXT, colour TEXT, material TEXT, pattern TEXT,
      fit_cut TEXT, fit_feedback TEXT, season TEXT, formality TEXT,
      notes TEXT, ai_confidence REAL DEFAULT 0, created_at TEXT DEFAULT CURRENT_TIMESTAMP
    );

    CREATE TABLE IF NOT EXISTS feedback (
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      outfit_json TEXT, rating TEXT, created_at TEXT DEFAULT CURRENT_TIMESTAMP
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

@app.post("/api/garments")
async def add_garment(
    image_path: str = Form(...),
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
      (image_path,original_image_path or image_path,category,garment_type,brand,model_line,labelled_size,colour,material,pattern,fit_cut,fit_feedback,season,formality,notes,ai_confidence))
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
    if len(data) > 20 * 1024 * 1024:
        raise HTTPException(400, "That photo is too large. Please choose an image under 20 MB.")
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
    garments = [dict(r) for r in con.execute("SELECT * FROM garments ORDER BY id DESC").fetchall()]
    profile = dict(con.execute("SELECT * FROM profile WHERE id=1").fetchone())
    feedback = [dict(r) for r in con.execute(
        "SELECT rating, outfit_json FROM feedback ORDER BY id DESC LIMIT 30"
    ).fetchall()]
    con.close()

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
      reasoning={"effort":"medium"},
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

    prompt = f"""
Search the live web for men's clothing products currently offered by reputable retailers that match this specification.

SEARCH PHRASE: {req.search_phrase}
CATEGORY: {req.category or 'not specified'}
SHOPPING SPECIFICATION: {req.shopping_spec or 'not specified'}
BUDGET: {req.budget or 'not specified'}
SIZE/FIT GUIDANCE: {req.size_fit_guidance or 'not specified'}

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
      reasoning={"effort":"medium"},
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

class Feedback(BaseModel):
    outfit: dict
    rating: str

@app.post("/api/feedback")
def save_feedback(f: Feedback):
    con=db()
    con.execute("INSERT INTO feedback(outfit_json,rating) VALUES (?,?)",(json.dumps(f.outfit),f.rating))
    con.commit(); con.close()
    return {"ok":True}
