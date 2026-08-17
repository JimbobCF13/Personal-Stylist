# Personal Stylist V2 — deployable build

This is the first proper full-stack prototype.

## Working features
- Persistent SQLite wardrobe
- Permanent stored garment photos
- Profile + measurements + brand/size notes
- Add / browse / delete wardrobe pieces
- Build around a saved garment
- Outfit request flow
- Feedback storage
- Optional AI garment-photo analysis
- Optional AI Stylist Agent for wardrobe-first outfit creation
- Offline fallback mode if no OpenAI API key is configured

## Run locally
Requires Python 3.11+.

1. Create a virtual environment:
   python -m venv .venv
2. Activate it.
3. Install:
   pip install -r requirements.txt
4. Optional AI:
   copy `.env.example` values into your hosting environment.
   Do NOT put an API key into the browser or static JavaScript.
5. Start:
   uvicorn app:app --reload
6. Open:
   http://127.0.0.1:8000

## iPhone use
For proper phone testing this needs to run at a normal web URL. The ChatGPT attachment preview does not execute the JavaScript needed by the full app.

A simple deployment target can run this FastAPI app with persistent disk storage. For a production version, move images to object storage and SQLite to a managed database.

## V2 limitations / next build
- No account/login layer yet
- No live weather integration yet
- No live retailer/product search yet
- No AI likeness/virtual try-on yet
- Feedback is stored but not yet summarized into learned preference weights
- Brand sizing intelligence currently comes from profile notes and garment feedback; retailer size charts/product measurements can be added in the shopping stage

## OpenAI architecture
The server uses the Responses API. Garment analysis sends the clothing photo as image input and requests a strict JSON-schema response. Outfit generation sends the stored wardrobe/profile as structured context and requests structured outfit recommendations.

The default model is configurable by `OPENAI_MODEL` and is set to `gpt-5.6-terra` in the example configuration.
