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


## V2.1 iPhone photo fix
The server now automatically converts iPhone HEIC/HEIF garment photos to JPEG before sending them to OpenAI. It also corrects EXIF orientation and returns clearer AI error messages.


## V2.2 photo-source update
Add to Wardrobe now offers:
- Take Photo
- Choose from Photos

Both routes use the same AI garment-analysis workflow.


## V2.3 batch wardrobe upload
Choose Multiple Photos lets you select several garment photos in one go. The app queues them, analyses one at a time, and shows Save & Next until the batch is complete.


## V2.4 personal style learning
- Outfit feedback is now passed into the Stylist Agent on future recommendations.
- Repeated Loved/Liked/Not for me/Too smart/Too casual patterns influence future styling.
- Garments marked Perfect fit receive higher styling weight.
- Profile now includes a Style Learning panel summarising feedback and perfect-fit brand patterns.


## V2.5 persistent wardrobe storage
The wardrobe database and garment photographs can now live on Render's persistent disk.

In Render add the environment variable:

`DATA_DIR=/var/data`

With the disk mounted at `/var/data`, the following persist across deploys/restarts:
- wardrobe items
- garment photos
- measurements/profile
- outfit feedback
- style-learning history

Local development falls back to a `data/` directory inside the project.


## V2.6 garment editing
Saved wardrobe items can now be edited without deleting/re-uploading them.

Editable fields include:
- category and garment type
- brand and model/line
- size
- colour and material
- pattern
- fit/cut and fit feedback
- season
- formality
- notes / garment measurements

The original saved photo remains attached to the garment.


## V2.7 See on Model
Outfit cards now include **See on model**.

The server:
- takes the saved garment IDs in the recommended outfit;
- uses the garment photographs as image references where supported;
- sends them to `gpt-image-2` by default;
- generates a realistic full-body generic male model wearing a close visual representation of the outfit;
- saves generated previews on the persistent disk under `/var/data/generated`.

The image is explicitly labelled as an AI styling visualisation rather than an exact fit simulation.

Optional environment variable:
`OPENAI_IMAGE_MODEL=gpt-image-2`

If omitted, `gpt-image-2` is used automatically.

`View on me` is shown as the next planned feature but remains disabled in this build.

## V2.7.1 deployment fix
Adds Pillow, which provides the PIL module required by garment image processing.
Includes V2.6 garment editing, persistent storage and V2.7 See on Model.

## V2.8 View on Me
Profile includes My Model for 1–4 persistent personal reference photos.
Outfit cards support See on model and View on me.
Personalised previews use saved reference photos plus wardrobe garment images.
These remain AI styling visualisations, not exact virtual fitting-room simulations.


## V2.8.1 My Model upload fix
- Cache-busts app.js and style.css so Safari loads the new V2.8 code after deployment.
- Adds visible upload progress and success/error messages to My Model.
- Guards the model-photo event listener to avoid silent frontend failures.


## V2.9 Wardrobe Gaps & Shopping
Adds a Shop screen that analyses the persistent wardrobe and recommends the highest-value additions.

The user can ask for a specific item (for example, a new blazer) or ask what the wardrobe is missing.

Each recommendation includes:
- ideal colour, material, fit and formality;
- wardrobe synergy score;
- why the purchase adds value;
- fit/size guidance using profile and garment history;
- existing wardrobe items it works with;
- outfit ideas using owned garments;
- a precise shopping specification;
- a retailer-search phrase ready for future live web sourcing.

This version deliberately does not claim live retailer products, prices or stock. Live retailer sourcing is the next integration.


## V3.0 Live Shopping
- Wardrobe-gap recommendations now show an understandable synergy label: Excellent / Good / Moderate / Low, alongside the 0–100 score.
- Each recommended wardrobe gap has **Find products to buy**.
- The backend uses the OpenAI Responses API web-search tool to look for current UK retailer/product pages.
- Product cards can show brand, retailer, current price when found, colour/material/fit, sizing guidance, confidence and a retailer link.
- Product images are shown only when a direct usable image URL is actually available; they are never fabricated.
- The search is designed to cover core garments, footwear, outerwear, ties/accessories and headwear.
- Current product facts are not stored as permanent truth because price/stock can change.

Optional environment variable:
`OPENAI_SHOPPING_MODEL=gpt-5.6-terra`
If omitted, the normal OPENAI_MODEL is used.


## V3.1 Modern Monochrome theme
Purely visual refresh. No workflow or backend behaviour changes.

Direction:
- off-white/light-grey canvas
- clean white cards
- near-black typography and actions
- subtle borders and shadows
- reduced visual noise
- wardrobe images presented against light neutral surfaces
- monochrome navigation and controls


## V3.2 wardrobe image cleanup
Wardrobe display images now use a non-generative cleanup pipeline:
- original source photo is retained;
- orientation correction;
- light brightness/contrast normalisation;
- conservative crop;
- consistent catalogue-style portrait canvas;
- neutral near-white background.

This build intentionally avoids aggressive automatic background segmentation that could remove real garment details. A stronger subject-isolation/background-removal stage can be added later with a dedicated segmentation model/service, while still preserving the original photograph.


## V3.3 genuine photo isolation
Adds a visible **Clean up photo** action to saved garments.

The cleanup is non-generative:
- uses the real uploaded photograph;
- isolates the garment using OpenCV GrabCut segmentation;
- preserves garment pixels rather than redrawing the item;
- removes the surrounding floor/wardrobe/background where segmentation is confident;
- centres the isolated item on a consistent near-white catalogue canvas;
- keeps the untouched original photograph;
- adds **Original photo** so the user can revert.

If the segmentation is not confident enough, the app refuses the cleanup rather than damaging the garment image.


## V3.4 — Stylist Context + upgraded cleanup path
- Adds occasion, dress code, desired smartness, season, weather, temperature, location, wardrobe/shopping preference and free-text context to outfit requests.
- The AI stylist now receives those fields as explicit situation constraints alongside wardrobe, fit profile and learned feedback.
- Adds an optional specialist AI background-removal path using `REMOVE_BG_API_KEY`. If no key is configured, cleanup safely falls back to the existing local segmentation, so deployment does not depend on another service.
- The specialist path is non-generative: it removes the background from the real garment photo and keeps the original.


## V3.5 — Help Me Pack + lighter image architecture
- Adds Help Me Pack: destination, trip length, weather, activities, dress needs, laundry and shopping preference.
- Builds an efficient capsule from the real wardrobe and reuses versatile pieces across a trip.
- Wardrobe photos now show the whole garment (`object-fit: contain`) and tapping/clicking the photo opens Edit.
- Removes OpenCV and NumPy from the Render service to reduce memory pressure.
- Clean up photo now uses the specialist remove.bg API only; it never silently falls back to the rough local GrabCut result.
- Before sending a cleanup request, the server creates a bounded 1600px JPEG working copy to reduce memory spikes.
- Original photos remain untouched and restorable.

### One-time setup for better photo cleanup
Add `REMOVE_BG_API_KEY` to Render Environment. Until it is configured, Clean up photo will show a clear setup message and leave the original unchanged.

## V3.6 — cleanup reliability + visible progress
- Fixes the remove.bg multipart request used by V3.5 and surfaces the real remove.bg/API error instead of incorrectly reporting every failure as a missing API key.
- Adds a visible `Cleaning up photo…` overlay and spinner while background removal is running; actions are temporarily disabled to prevent duplicate requests.
- Cleanup is fully non-destructive: the uploaded original is the source of truth and cleanup creates a separate derivative.
- Wardrobe loading self-heals a missing display derivative by falling back to the stored original when that original still exists.
- Older rows are safely backfilled when their current image is a real `/uploads/` source image.
- Processed derivatives are no longer automatically deleted during cleanup/restore, avoiding accidental loss with legacy wardrobe rows.
- Keeps V3.5 Help Me Pack, full-garment `object-fit: contain` cards, and tap/click-photo-to-edit behaviour.
