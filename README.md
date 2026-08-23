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
