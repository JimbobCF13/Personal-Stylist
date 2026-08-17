
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

ROOT = Path(__file__).resolve().parent
DB = ROOT / "stylist.db"
UPLOADS = ROOT / "uploads"
UPLOADS.mkdir(exist_ok=True)

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
    return {"ok": True, "ai_enabled": bool(os.getenv("OPENAI_API_KEY")) and OpenAI is not None}

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

def encode_image(path: Path):
    mime = mimetypes.guess_type(str(path))[0] or "image/jpeg"
    data = base64.b64encode(path.read_bytes()).decode()
    return f"data:{mime};base64,{data}"

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
    suffix = Path(file.filename or "photo.jpg").suffix.lower() or ".jpg"
    if suffix not in [".jpg",".jpeg",".png",".webp",".heic"]:
        raise HTTPException(400, "Unsupported image type")
    name = f"{uuid.uuid4().hex}{suffix}"
    path = UPLOADS / name
    path.write_bytes(await file.read())
    result = analyse_image(path)
    return {
      "image_path": f"/uploads/{name}",
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
Prefer strong outfits fully from the wardrobe over marginally better outfits requiring purchases.
If a useful piece is missing, name only the category/style/colour/material needed; do not invent a product.
Produce genuinely different outfit options. Use only garment IDs supplied in the wardrobe JSON.
Be concise but specific about why the outfit works."""

@app.post("/api/outfits")
def outfits(req: OutfitRequest):
    con = db()
    garments = [dict(r) for r in con.execute("SELECT * FROM garments ORDER BY id DESC").fetchall()]
    profile = dict(con.execute("SELECT * FROM profile WHERE id=1").fetchone())
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
      "wardrobe": garments
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
