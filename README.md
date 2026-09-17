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


## V4.0 Outfit Intelligence + Dictation
Adds a free-text personal-stylist workflow.

Examples:
- “I have a business dinner tomorrow. Smart but not too formal.”
- “Build me an outfit around my grey Hackett trousers.”
- “Casual dinner, cool evening, contemporary but not too young.”

The engine combines the actual wardrobe, measurements and fit profile, brand/size history,
previous outfit feedback and an optional anchor garment. It returns a small ranked set of
outfits with scores, concise reasoning, occasion/weather/formality notes, and a clearly
identified missing piece only when useful.

Dictation uses browser speech recognition where supported. If it is unavailable, the button
focuses the text box and prompts the user to use the native iPhone/Mac keyboard microphone.


## V4.1 Stylist actions + dictation resilience
Each V4 recommendation now supports:
- View on me
- See on model
- Find this piece when a missing/recommended item is suggested

Visualisation includes the recommended extra piece in the image prompt so the complete proposed outfit can be shown.

Product sourcing reuses the live-shopping endpoint to search current UK retailer options.

Browser speech recognition commonly stops when a tab/window loses focus. V4.1 preserves captured text, tells the user dictation paused, and automatically attempts to resume when the Personal Stylist window regains focus.


## V4.2 reliability + continuous dictation
Fixes a front-end rendering failure that could leave the app showing “Styling from your wardrobe…” even after the backend returned HTTP 200.

Changes:
- restores a guaranteed HTML-escaping helper used by V4 outfit cards;
- lowers Stylist reasoning effort to improve response time;
- shows “taking longer than usual” status after 25 seconds;
- stops a Stylist request after 75 seconds rather than spinning forever;
- displays useful API errors;
- enables continuous browser speech recognition where supported;
- automatically restarts recognition if Safari/browser ends a session unexpectedly;
- preserves captured text across restarts;
- pauses on tab/window loss and resumes when focus returns where the browser permits.

Browser dictation accuracy still depends on the browser speech-recognition service. A future upgrade can replace this with OpenAI audio transcription for higher and more controllable accuracy.


## V4.3 Global Auto Preview
Outfit-level recommendations now automatically start a personalised "View on me" preview as soon as the recommendation cards are rendered. This applies to the shared outfit recommendation renderer used by Stylist and other outfit-planning flows that reuse it.

Changes:
- no need to press View on me for outfit recommendations;
- previews start in parallel after recommendations appear;
- each card shows "Creating your look…" while the image is generated;
- generated previews are cached in-browser so reopening the same recommendation is instant;
- See on model remains available as an optional alternative;
- Find this piece remains available for missing recommended items.


## V4.4 Brand Intelligence + Garment Detail + Home Polish

This build combines the next two product stages and a visual polish pass.

### Brand / product intelligence
When a garment has a brand, the app can research the brand and model/line in the background using live web search. It looks for:
- line/model identification only where evidence is strong;
- brand/line fit tendencies;
- sizing and official/retailer size-chart information;
- fabric and construction;
- seasonality;
- useful source pages.

Research is stored separately from the user's garment metadata. It never silently overwrites user-entered details. The garment detail page offers:
- Apply to blank fields;
- Refresh research;
- Ignore.

Saving an edited garment with a brand automatically starts background research.

### Garment detail
Clicking a wardrobe photograph now opens a proper garment page rather than jumping directly into edit. It shows:
- catalogue image;
- garment metadata and fit feedback;
- recent rated-outfit history;
- brand intelligence;
- actions to build an outfit, edit, or clean the photo.

### Home screen
The emoji tile dashboard has been replaced with a quieter editorial menswear layout:
- monochrome line icons;
- stronger typographic hierarchy;
- one prominent Ask My Stylist action;
- cleaner Wardrobe / Shopping / Packing / Profile navigation;
- no generic emoji-card visual language.


## V4.4.1 smooth brand research
Fixes visual flashing during Brand Intelligence research.

The app now keeps the garment detail page stable and updates only the Brand Intelligence panel while background research is running.

## V4.5 Try actual products on me
Live retailer results from a Stylist recommendation now include **Try on me**. Where the retailer exposes a usable product image, the app uses it alongside the user's real wardrobe and saved likeness to generate a personalised preview of that specific product. If an image is unavailable, it falls back to reliable product metadata. The result remains an AI styling visualisation rather than an exact fit guarantee.


## V4.5.1 retailer search reliability
- Removes the retailer-result rendering dependency that could produce a `renderLiveProduct` browser error after a successful live search.
- Product cards are now rendered directly by the Find This Piece flow.
- Makes the active search much more visible with a larger loading panel, animated spinner, explicit live-search explanation and a completion state.


## V4.6 Shopping Shortlist + Compare
- Product result cards now try to show a compact thumbnail.
- If search does not provide an image, the app makes a best-effort lookup of the retailer page's Open Graph/Twitter image.
- Product results can be saved to a persistent My Shortlist view.
- Shortlisted products can be compared by retailer, price, colour, material, fit, sizing guidance and recommendation confidence.


## V4.7 Purchase & Fit Learning
- Retailer results no longer leave large empty image boxes when a thumbnail cannot be obtained. They show a small intentional **Product image unavailable** note instead.
- Shortlisted products can be marked **I bought this** and transferred into the wardrobe with retailer/product data preserved.
- Purchased items are marked **Awaiting fit review**.
- Structured real-world fit review captures size, overall 1–5 fit, chest, waist, length, sleeve, shoulders and notes.
- Confirmed fit history is fed back into future live product searches so recommendations can use the user's actual brand/size experience rather than generic sizing alone.

## V4.8 Wardrobe Organisation + Product Page Import
- Repairs old category duplication without deleting garments or images.
- Canonical order: Jackets & Outerwear, Knitwear, Shirts, Polos & T-Shirts, Trousers, Shorts, Footwear, Accessories, Other.
- Wardrobe renders in category sections rather than newest-upload order.
- Add Item accepts pasted or dragged retailer product URLs and imports real product facts and image when available.
- If no image is exposed, the item can still be saved and displays No photo yet.
- Next planned stage: Fast Wardrobe Setup from natural-language description, dictation, pasted lists or receipts/order history.


## V4.8.2 Retailer-block fallback
When a retailer blocks Render/cloud-server page access with HTTP 403 or similar anti-bot behaviour, product-page import now automatically falls back to OpenAI live web search using the exact supplied product URL. Supported product facts are imported even when the page cannot be fetched directly. Missing retailer images no longer cause the import itself to fail.


## V4.8.3 URL-import save fix + richer product classification
- Fixes Save to wardrobe for imported products that have no downloadable retailer image. `image_path` is now optional for lightweight wardrobe records.
- Save now has a visible Saving state and explicit error reporting instead of failing silently.
- URL imports track a successful imported product independently of whether an image was available.
- Live-web fallback now searches more deliberately for material and explicit fit information.
- Season and formality may be filled as conservative stylist classifications based on the identified garment and evidenced materials/design. Material remains blank when it cannot be substantiated.


## V4.8.4 Add or replace photos after saving
Existing wardrobe garments can now receive a photo at any time, including URL-imported records that were originally saved without one.

From Edit Garment the user can:
- Take Photo;
- Choose Photo / Screenshot from the device;
- replace an existing garment photo.

The new photo is normalised and converted into the usual catalogue display image, while all existing garment metadata, retailer research, fit information and notes remain unchanged.


## V4.8.5 Batch Upload Memory & Reset Fix
- Prevents overlapping garment-analysis requests. Save/Skip are disabled while the current photo is being analysed.
- Adds a 75-second client timeout with a clear recoverable message.
- Cancel now fully resets the batch queue, current index, form fields, file inputs, preview and analysis state.
- Navigating away from Add to Wardrobe also clears an unfinished batch.
- Browser blob preview URLs are revoked as soon as they are no longer needed.
- Large phone JPEGs request lower-resolution decoding via Pillow `draft()` before further processing.
- Normalised AI/catalogue source images are capped at 1600px on the longest side and JPEG quality 88 to reduce Render peak memory.
- Individual uploads are capped at 15 MB.


## V4.8.6 Multiple Photo Selection Hotfix
Fixes a regression introduced by the V4.8.5 reset logic. The browser FileList is live and was being emptied when the Add flow reset the file input before copying the selected files. V4.8.6 snapshots the selected File objects first, then resets the previous batch state, then starts the new queue.


## V4.8.7 Wardrobe Navigation Polish
This is a front-end/navigation-only release. It does not alter, migrate, delete or recreate wardrobe database rows or stored garment images.

- Replaces the wardrobe category dropdown with horizontally scrollable category buttons.
- Each button shows the number of garments in that category.
- **All** retains the grouped wardrobe view.
- Selecting a category shows that category directly.
- When a garment is opened or edited, the app remembers that garment/category.
- Returning to Wardrobe scrolls back to the garment (or its section) instead of jumping to Jackets & Outerwear at the top.


## V4.9 Saved Looks + Persistent Stylist + Live Weather
- Current Ask My Stylist recommendations persist in browser storage across app navigation and page refreshes. They remain until the user deliberately generates a new set.
- Generated outfit visual paths are cached in browser storage so revisiting the current suggestion can reuse an existing visual instead of automatically generating another copy.
- Every recommended outfit has a Favourite button.
- Favourites are stored in a separate SQLite `outfit_favourites` table and do not alter wardrobe garment rows or image records.
- New Saved Looks screen displays saved outfit visuals, owned garments, styling rationale, original request and weather context.
- Ask My Stylist now accepts Location and When.
- When a location is supplied, the backend performs an OpenAI live web search for current forecast information and feeds temperature, rain, wind and practical clothing context into the styling request.
- If weather lookup fails or a requested date is outside reliable forecast range, the app falls back to the user's written request rather than blocking outfit generation.

### Wardrobe preservation
V4.9 only adds a new independent favourites table. Existing `garments` rows, garment images and wardrobe category data are not migrated, rewritten or deleted.


## V4.9.1 Shopping Actions Hotfix
- Restores the missing click handler behind Analyse my wardrobe.
- Shows an immediate, prominent wardrobe-analysis loading state and a recoverable 75-second timeout.
- Renders wardrobe-gap recommendations with synergy, specification, relevant owned pieces and outfit ideas.
- Adds live product search from each gap recommendation.
- Moves Find this piece results above the large outfit image so its loading state is immediately visible.
- Find this piece now visibly changes to Searching, scrolls the search panel into view and re-enables when finished.
- This patch does not modify the wardrobe database or garment image storage.


## V4.9.2 Wardrobe Gap Product Search Hotfix
- Fixes **Find current products** on dynamically generated Wardrobe Gap recommendations.
- Removes recommendation JSON from inline `onclick` attributes. Apostrophes and punctuation in AI-generated recommendation text could break the inline JavaScript while leaving the button looking normal.
- Recommendations are now held in an in-memory map and the results area uses one delegated click listener, so dynamically-created buttons are wired reliably.
- The existing Searching UK retailers loading state remains unchanged.
- No backend, database, wardrobe row or garment-image changes are included in this patch.


## V4.9.3 Large Wardrobe Analysis Performance
- Wardrobe-gap analysis now sends a compact styling representation of each garment rather than the entire database row.
- Excludes image paths, enrichment JSON, retailer metadata and other fields that do not help gap analysis.
- Reduces recent outfit feedback from 30 full rows to 12 compact summaries.
- Uses low reasoning effort for this structured wardrobe-comparison task to reduce latency.
- Extends only the Wardrobe Gaps front-end timeout from 75 to 105 seconds and adds a clear "still working" message at 45 seconds.
- No garment rows, uploaded images or wardrobe database records are modified or migrated by this change.


## V5.0 — Fast Wardrobe Setup
- Adds **Quick Add Wardrobe** from both Home and My Wardrobe.
- Users can type, paste or dictate a natural-language list of clothes they already own.
- AI converts the description into separate, structured wardrobe entries without inventing missing brand/model/size/material details.
- Every proposed item is shown in an editable review screen before saving.
- Individual items can be unticked or removed.
- **Save selected items** uses a single database transaction; nothing is written during the analysis/review stage.
- Quick-added garments can have photos/screenshots added later through the existing Edit Garment photo flow.
- No database migration is required and existing wardrobe rows/images are untouched.


## V5.1 — Build My Own Look + Product-to-Wardrobe
### Build My Own Look
- Visually select any combination of saved wardrobe pieces with one-tap tick selection.
- A sticky Your Look tray shows the current selection.
- Show on me uses the existing high-quality personalised outfit visualisation.
- Regenerate image retries only that visual without changing the selected outfit.
- Analyse this look gives a restrained stylist critique.
- Improve this look explicitly prefers the fewest useful changes rather than replacing the whole outfit.
- Give me alternatives keeps the character of the user's chosen outfit and suggests small directions.

### Style an Online Item
- Paste a retailer product URL.
- The app identifies the product using the existing retailer-page extraction/fallback approach.
- It builds up to three outfits around that external product using only garments the user actually owns.
- Each combination can be shown on the user using the existing product try-on pipeline and saved model photos.
- Individual try-on images can be regenerated without losing the other product/wardrobe combinations.

### Safety / persistence
- No wardrobe database migration is introduced by V5.1.
- Existing garment rows and uploaded wardrobe images are not rewritten or deleted.


## V5.1.1 — Add Garment workflow polish
- Photo-first and retailer-link-first now work as a single combined garment workflow.
- Import a retailer URL first, then add one personal photo without clearing brand/model/material/etc.
- Photo analysis fills only missing fields when web details already exist.
- Start with a personal photo, then import a retailer URL without losing the photo.
- Retailer data enriches product identity while explicit user size/fit entries are retained.
- Existing saved wardrobe data and images are untouched.


## V5.1.2 — Mobile UI polish
- Reworks garment detail on phones into a product-first fashion layout with a full-width hero image.
- Moves the title, useful metadata, chips and primary actions directly beneath the image.
- Garment Details and Brand Intelligence are expandable on mobile, reducing long dense pages.
- Product URLs/long notes remain available but no longer dominate the default phone view.
- Empty outfit history is reduced to one compact status row.
- Improves phone typography, touch targets, card spacing, form sizing and bottom navigation.
- Adds mobile-specific treatment for Wardrobe, Stylist, Build My Own Look, Saved Looks, Shopping and Pack.
- Desktop layouts remain intact.
- No database migrations and no changes to saved wardrobe records or images.


## V5.1.3 — Wardrobe category refinement
The old broad `Jackets & Outerwear` group is split into:
- **Blazers & Tailoring** — blazers, sports jackets, suit jackets, dinner jackets and waistcoats.
- **Jackets** — casual jackets, bombers, Harringtons, field/chore jackets, gilets, overshirts and similar lighter outer layers.
- **Coats** — overcoats, topcoats, trench coats, raincoats, macs, parkas, pea coats and other coat-length outerwear.

Classification now also uses restrained construction clues such as lapels and single/double-breasted tailoring when a retailer generically calls a blazer a “jacket”.

On startup, existing garments may have their **category field only** reclassified into the new taxonomy. No garment records, photos, enrichment data or other wardrobe information are deleted or recreated.


## V5.1.4 — Category precision + image loading resilience
### Categories
- Adds **Overshirts & Shirt Jackets** as its own wardrobe category.
- Overshirts, shirt jackets and shackets are classified there before generic jacket/tailoring rules can catch them.
- Utility shirts and work shirts stay in **Shirts** unless explicitly described as an overshirt or shirt jacket.
- Blazers remain in **Blazers & Tailoring**, casual jackets in **Jackets**, and true coats in **Coats**.
- Existing garments may have only their category field reclassified; photos and garment data are untouched.

### Images
- Wardrobe and garment-detail images automatically retry once if a static image request fails.
- If a cleaned image is unavailable, the UI falls back to the original uploaded image where one exists.
- If both paths fail, the user gets a visible **Photo didn’t load — Tap to retry** control instead of a blank/broken image.
- No image files are deleted, moved or rewritten by this patch.


## V5.1.5 — Image source integrity + sweatshirt taxonomy
### Image fix
- New uploads now preserve two distinct paths: the catalogue/display image and the original source image.
- Fixes the Add Garment bug that previously saved the catalogue image as both paths.
- Retailer URL imports also retain their true downloaded source image separately.
- Wardrobe cards now request images through a stable garment-image endpoint rather than directly depending on a random static filename.
- The server verifies the display file and automatically serves the original source when the display file is missing/corrupt/blank.
- Existing garment records are not deleted. If neither recorded image is usable, the garment remains intact and the UI clearly marks the photo as unavailable.

### Categories
- Adds **Sweatshirts & Hoodies**.
- Crew-neck sweatshirts, quarter-zip sweatshirts and hoodies no longer fall into Polos & T-Shirts.
- Removes the overly broad `top/tops` matching from Polos & T-Shirts.
- Existing categories are safely re-normalised on startup from the stored garment type/details.


## V5.1.6 — Wardrobe-role classification
Classification now prioritises how a garment is actually worn over literal retailer naming.

Key rules:
- Rugby shirt / rugby top → **Knitwear**
- Short-sleeve knitted polo → **Polos & T-Shirts**
- Long-sleeve knitted polo / pullover → **Knitwear**
- Sweatshirts / hoodies → **Sweatshirts & Hoodies**
- Overshirts / shirt jackets → **Overshirts & Shirt Jackets**
- True buttoned shirts → **Shirts**
- Lightweight knitted pullovers can remain **Knitwear** even when sold as a long-sleeve T-shirt.

Existing wardrobe items are re-evaluated from stored garment type, model, fit, notes, brand and material. Only the category field can change; no garment or image data is removed.


## V5.2 — Stylist interaction + improved dictation

### Ask My Stylist
- Every normal stylist outfit now has **Regenerate image**.
- Regenerating affects only that outfit's visual; the other suggestions remain intact.
- Every main stylist outfit now has **More like this**.
- More Like This creates 2–3 restrained variations based on the selected outfit rather than replacing the whole idea.
- Variations preserve at least part of the original outfit, use valid owned garment IDs, and are stored with the current stylist session so they survive a refresh.
- Variations can themselves be favourited, regenerated, shown on the generic model, and can source a missing piece where relevant.

### Dictation
- Replaces reliance on browser SpeechRecognition with recorded audio sent to the app's OpenAI speech-to-text endpoint.
- Default transcription model is `gpt-4o-transcribe`, with `gpt-4o-mini-transcribe` as an automatic fallback.
- Audio is held only in a temporary server file for transcription and deleted immediately afterwards.
- Dictation now works in:
  - Ask My Stylist
  - Quick Add Wardrobe
  - Build My Own Look context
  - Style an Online Item occasion
  - Help Me Pack activities
  - Help Me Pack dress needs
  - Help Me Pack notes
- No database migration. No wardrobe or image data is altered by this patch.


## V5.3 — Help Me Pack 2.0 + Activity UI

### Help Me Pack
- Exact departure and return dates, with trip length calculated automatically.
- Live destination research before wardrobe selection.
- Current published forecast when dates are close enough for a useful forecast.
- Seasonal/historical weather context when a trip is too far away for a reliable forecast — it will not pretend a long-range forecast is known.
- Research of named hotels, restaurants, venues and events.
- Explicit distinction between a verified dress requirement and a stylist inference from destination/venue context.
- A true capsule strategy with deliberate garment re-use across the trip.
- Every packing-plan outfit has **Show on me**, **Regenerate image** and **More like this**.
- Packing visualisations are generated only when requested and cached during the session.

### Activity feedback
- A prominent centre-screen activity card now appears for longer AI work.
- Dictation displays a clear pulsing **Listening…** state, followed by **Transcribing…**.
- Stylist planning, trip research, capsule building and image generation have obvious working states.

### Image-generation speed
- Existing image quality remains at the current medium-quality portrait setting.
- The default personalised visualisation now uses up to 2 likeness reference photos instead of 3, while retaining up to 5 garment references. This reduces input overhead without deliberately lowering output quality.
- Existing image caching remains in place, and packing-plan images are generated on demand rather than all at once.
- `OUTFIT_LIKENESS_REFS=3` can be set in Render if three personal reference photos are preferred.

No database migration. Existing wardrobe records and images are untouched.


## V5.3.1 — Packing outfit separation

- Every packing-plan entry is now exactly **one discrete outfit for one occasion/time of day**.
- If the same day has a daytime look and an evening/dinner look, they are returned as separate records rather than being combined.
- The UI groups looks under the relevant day, but each outfit has its own card, garment strip, **Show this look on me**, **Regenerate this image**, and **More like this look** controls.
- Adds unique `look_id` and `time_of_day` fields to each packing look.
- Packing visual cache keys now include the exact look ID, occasion, note and garment set, preventing one outfit's image from being reused for another.
- The image-generation prompt now explicitly says this is one outfit only and not to merge, swap or blend details between different garment references.
- A new packing plan clears the prior packing image cache so visuals cannot bleed across separate trips/plans.

No database migration. Existing wardrobe records and images are untouched.


## V5.4 — One-tap smart dictation

The app now supports a much smoother voice-first workflow.

### One paragraph → populated form
A new prominent smart-dictation control is available on the main multi-field workflows:
- Help Me Pack
- Ask My Stylist
- What Should I Wear?
- Wardrobe Gaps & Shopping
- My Profile & Fit
- Add Garment details

The user can speak naturally in one paragraph. The app:
1. records and transcribes the speech,
2. uses AI to identify only explicitly supplied facts,
3. fills the relevant structured fields,
4. leaves anything unstated untouched,
5. lets the user review/edit before running or saving.

Examples:
- Packing: “I’m going to San Francisco from 3 to 10 October for lectures, dinners and lots of walking. Smart casual most days, one smart dinner, hand luggage only.”
- Shopping: “I want a lightweight navy jacket for smart-casual dinners, ideally under £250.”
- Profile: “I’m 183 cm, 102 chest, usually prefer a tailored regular fit; Ralph Lauren Custom Slim Fit large works well.”

Existing field-by-field dictation remains available, so users can choose either workflow.

No database migration. Existing wardrobe data and images are untouched.


## V5.4.1 — Simplified Help Me Pack

Help Me Pack is now brief-first rather than form-first.

- The top of the screen is one large **Your Trip** box.
- The user can either type the whole trip naturally or tap **Dictate trip** and say everything in one go.
- The raw trip description is always preserved.
- Before building the plan, AI automatically extracts destination, dates, duration, activities, dress needs, trip type and useful notes from that single brief.
- Depart / Return / Days remain visible as optional quick controls.
- Laundry and permission to suggest missing items remain visible.
- Destination, trip type, known weather, activities, dress needs and extra notes move into a collapsed **Optional trip details** section for users who want to review or fine-tune them.
- The packing/research backend now also receives the original free-text `trip_brief`, so the plan does not depend entirely on field extraction succeeding.

No database migration. Existing wardrobe and image data are untouched.


## V5.5 — Brief-first app + background outfit visuals

### Simpler input across the app
The successful Help Me Pack workflow is now the design pattern for the main input-heavy areas:
- **Ask My Stylist** — one large natural-language brief; build-around, shopping, location and timing are optional details.
- **What Should I Wear?** — one large brief; structured occasion/weather/formality controls are tucked under Optional details and the result runs through the current Ask My Stylist engine.
- **Wardrobe Gaps & Shopping** — one shopping brief; budget, season and use are optional filters.
- **Build My Own Look** — the context input is now a larger natural-language brief with dictation.
- **Style an Online Item** — the styling context is a natural-language brief with dictation.
- **Profile** keeps voice-first entry with manual measurements available for review/editing.
- Quick Add Wardrobe and Help Me Pack were already brief-first.

### Speed improvements
- Help Me Pack no longer re-parses an unchanged dictated brief when Build is pressed.
- Packing-plan reasoning defaults to `low` for faster structured planning while keeping the same model. Set `OPENAI_PACK_REASONING=medium` in Render if you prefer the old slower reasoning level.
- Existing outfit-image quality remains unchanged.
- Background visual work is limited to two concurrent jobs to reduce rate-limit/server pressure.

### Automatic visuals
- Help Me Pack now starts generating **all Show on me images automatically in the background** as soon as the text plan appears.
- The user can read the packing plan immediately while the personalised visuals complete underneath each separate look.
- Style an Online Item also starts its personalised product/wardrobe try-ons automatically after the outfit suggestions appear.
- Ask My Stylist already auto-generates personalised visuals, so that behaviour is retained.
- Manual Regenerate remains available for any visual the user wants changed.

No database migration. Existing wardrobe and image data are untouched.


## V5.5.1 — Safari dynamic image repaint hotfix

Observed behaviour:
- An AI-generated visual could be fully loaded but remain as the grey/black image area in Safari.
- Slightly resizing the Safari window caused the image to appear immediately.
- This strongly indicates a browser paint/compositing issue rather than a failed image-generation request.

Fixes:
- Adds a targeted Safari-safe repaint step after dynamically inserted images load.
- Forces a local reflow/compositor refresh instead of waiting for a window resize.
- Applies this to Ask My Stylist, Saved Looks, Help Me Pack visuals, Build My Own Look and product try-ons.
- Adds eager loading and async decode hints to generated visuals.
- Gives generated-image elements a stable minimum layout area and GPU/compositor layer.
- Saved Look garment thumbnails now use the stable `/api/garments/{id}/image` endpoint instead of old raw image paths.
- Also re-stabilises visible dynamic images when returning to the tab/page.

No database migration. No saved garment or generated-image files are modified or deleted.


## V5.6 — Saved-look exploration + explicit style learning + clearer dictation stop

### Saved Looks
- Adds **More like this** directly to every saved look.
- Adds **Use as inspiration** for a looser interpretation: preserve the taste/polish but allow a different palette and bigger piece changes.
- Saved looks are now fed into the main stylist and packing context as a strong positive style signal.

### Lightweight outfit reactions
Rather than a heavy 1–5 rating scale, each stylist outfit now supports:
- **Favourite** — strongest positive signal and saves the look.
- **Works for me** — positive preference signal.
- **Less like this** — soft negative signal without forcing the user to say they dislike an outfit.

The stylist learns from repeated patterns rather than one click and is explicitly told not to overfit to one colour palette. If saved/reaction history becomes dominated by one palette, it should still offer a strong alternative direction where appropriate.

### Visible learning
The Profile → Style Learning panel now reports:
- number of saved looks,
- outfit reactions,
- recurring colours in saved looks,
- recurring garment types,
- brands with Perfect fit feedback.

### Dictation stop
The centre-screen Listening card now contains its own prominent **Stop dictation** button.
The overlay card is clickable while the blurred background remains non-interactive, so the user no longer has to find the original blurred Stop button behind it.

No database migration. Existing wardrobe, saved looks and image files are untouched.


## V5.7 — Wardrobe Intelligence + dictation stop fix

### Wardrobe Intelligence
Adds a new **My Wardrobe Insights** dashboard from the home screen.

The dashboard combines:
- wardrobe category balance,
- common colours,
- Perfect fit feedback,
- Saved Look patterns,
- repeated garments in Saved Looks,
- outfit reactions.

It surfaces:
- wardrobe strengths,
- genuine gaps,
- Saved Look style patterns,
- versatile pieces,
- a variety nudge so the stylist does not overfit to one palette,
- the most defensible next-purchase opportunity.

Evidence is labelled carefully: Saved Look frequency is treated as preference evidence, not proof of actual wearing frequency.

### Dictation
The centre Listening card now shows a large, high-contrast **■ Stop dictation** button whenever the activity mode is Listening.
The button visibility no longer depends on Safari reporting the recorder state at exactly the same moment the overlay renders.
A visible **Recording is live** badge is also added.

No database migration. Existing wardrobe, saved looks, feedback and images are untouched.


## V6.0 — Get Him Dressed / Invite-only tester accounts

### Product rebrand
- Working product name is now **Get Him Dressed**.
- The app architecture includes a `styling_profile` field so the same platform can later support **Get Her Dressed / womenswear** without cloning the entire codebase.

### Account model
- First account created after deployment becomes **Owner / Admin**.
- That first account is attached to the existing legacy `/var/data` store, so the current wardrobe, photos, saved looks and learning history remain untouched.
- All later accounts require an admin-generated invite code.
- Tester accounts receive isolated SQLite databases and isolated upload/cleaned/generated/model-photo directories under `/var/data/users/<user_id>/`.
- Media URLs now resolve against the signed-in user's private storage rather than one shared public static directory.
- Browser-local stylist caches are cleared when a different user signs in on the same device.

### Authentication
- Email/password login.
- PBKDF2-SHA256 password hashing with per-password random salt.
- Opaque random server-side sessions, stored hashed in the auth database.
- HttpOnly, SameSite=Lax session cookie.
- 30-day sessions.
- Set `COOKIE_SECURE=0` only for local HTTP development; production defaults to secure cookies.

### Tester invites
- Owner/Admin can create one-use invite codes from **My Account**.
- Invites expire after 14 days.
- Each tester builds their own wardrobe, fit profile, saved looks, feedback and reference-photo collection.

### Important deployment step
After V6.0 is deployed, open the app and create the **first account immediately**. The first account automatically becomes the Owner/Admin and inherits the existing wardrobe data. Subsequent registrations are invite-only.

No destructive migration is performed on the existing stylist database or image library.


## V6.1 — Premium UI polish

This release deliberately focuses on perceived product quality before broader tester rollout.

### Visual system
- Refined warm-neutral product palette.
- Cleaner glassy header with compact GHD brand mark and direct Account shortcut.
- More premium typography hierarchy, spacing and card radii.
- Stronger focus states and button feedback.
- Reduced "prototype" feel across cards, forms and navigation.

### Home
- New editorial hero: **Know what works. Wear it better.**
- Higher-quality primary stylist card.
- Individual feature cards now have breathing room rather than one large grid slab.
- Refined icon containers, labels and hover/touch feedback.

### Authentication
- Login/create-account screen now looks like a product landing experience rather than a utility form.
- Added GHD monogram and concise value cues.
- Account mechanics and storage isolation are unchanged from V6.0.

### Other areas
- Wardrobe catalogue cards refined.
- Bottom navigation simplified visually.
- Wardrobe Intelligence and Account styling aligned to the new visual system.

No database migration. No account, wardrobe, image, learning or authentication behaviour is changed by this release.


## V6.2 — Fit & Sizing Intelligence

### Know My Size
Adds a new **Know My Size** area from the home screen.

It learns from actual garment fit reviews:
- labelled size,
- overall fit rating,
- chest,
- waist,
- shoulders,
- sleeve length,
- body / leg length,
- free-text fit notes,
- brand, model/line, garment type and cut.

The dashboard shows:
- fit-review count and evidence confidence,
- strongest fit signals,
- recurring fit problems,
- brand-specific lessons,
- conservative shopping rules,
- brand fit history,
- the most useful unreviewed garments to review next.

### Fit reviews
Fit review is now available on **every wardrobe garment**, not only items bought through the shopping workflow.
This means an existing wardrobe can immediately become training data for sizing intelligence.

### Shopping
Live product sourcing now receives:
- the user's confirmed garment-by-garment fit history,
- aggregated brand patterns,
- body measurements and preferred fit.

Size guidance is instructed to remain line/item-specific. One successful size in a brand must never be treated as proof that every garment from that brand fits the same way.

### Evidence standard
The system deliberately distinguishes:
- one-item anecdotal evidence,
- repeated brand/category fit evidence,
- exact-line evidence,
- user body measurements,
- live retailer/brand sizing information.

When evidence is insufficient, it says sizing needs confirmation rather than inventing certainty.

No destructive migration. Existing user accounts, wardrobes, images and fit fields are preserved.


## V6.3 — Tester / Admin Tools
Owner/Admin can view tester activity, revoke unused invites, disable/re-enable tester access without deleting data, and collect beta feedback from every account.


## V6.4 — Performance Pass

### Faster login/home
- Home no longer waits for wardrobe + profile + health requests sequentially.
- Cached wardrobe metadata paints immediately for returning users.
- Fresh server data refreshes in parallel and silently replaces the cache.
- Added a tiny `/api/bootstrap` request for greeting and top-level counts.

### Faster wardrobe
- The wardrobe index no longer opens and verifies every image with Pillow on every request.
- It now uses cheap filesystem checks for the list view; full validation remains available at garment/detail use.
- Returning to Wardrobe within 30 seconds reuses the fresh in-memory index rather than re-requesting it.
- Wardrobe metadata is cached per signed-in user on that device.

### Smaller images
- Added cached 420×520 JPEG garment thumbnails for grids, strips and supporting outfit views.
- Full-resolution garment images remain available for garment detail/editing.
- Thumbnail files are isolated per user and cache for up to seven days.
- Replacing a garment photo naturally changes the thumbnail cache key.

### Saved Looks
- Saved Looks are cached per user so repeat visits paint immediately.
- Garment strip images and saved visuals lazy-load rather than all decoding at once.

No destructive migration. Existing accounts, wardrobe images, Saved Looks and generated visuals are unchanged.


## V6.5 — Get Her Dressed foundation + Saved Looks navigation
New accounts can choose **Get Him Dressed (Menswear)** or **Get Her Dressed (Womenswear)**.

Womenswear accounts use their own taxonomy: Dresses, Skirts, Jumpsuits & Playsuits,
Blazers & Tailoring, Jackets, Coats, Knitwear, Sweatshirts & Hoodies, Blouses & Shirts,
Tops & T-Shirts, Trousers & Jeans, Shorts, Activewear, Footwear, Bags, Accessories and Other.

Core garment analysis, wardrobe parsing, product search, garment research and outfit visualisation
now use the account styling profile or gender-neutral instructions. Generic womenswear outfit
visualisations use an adult female model.

The signed-in brand changes to **Get Her Dressed** for womenswear accounts while existing
menswear accounts remain **Get Him Dressed**.

A persistent **Saved** tab has also been added to the bottom navigation.

No destructive migration. Existing accounts, wardrobes, images, Saved Looks and learning remain unchanged.


## V6.6 — Womenswear styling, sizing & fit intelligence

This release deepens Get Her Dressed rather than treating womenswear as a renamed menswear experience.

### Womenswear profile
For womenswear accounts the profile now:
- labels chest as Bust / Chest and hips appropriately,
- keeps body measurements shared with the core fit engine,
- adds optional usual Top, Bottom, Dress, Shoe and Bra sizes,
- supports those fields through smart dictation when explicitly stated.

Menswear profiles remain unchanged.

### Fit reviews
Fit reviews now include a separate hips/seat signal in addition to chest/bust, waist,
length, sleeve and shoulders. Womenswear labels use Bust / Chest and Body / Hem Length.

The extra fit evidence is used in Fit Intelligence and live shopping guidance.

### Styling intelligence
The main stylist and shopping-gap engine now receive profile-specific guidance.
For womenswear, the engine explicitly considers:
- dresses, skirts and jumpsuits as well as separates,
- silhouette/proportion, neckline, rise and hem length,
- waist/hip/bust fit,
- footwear height and bag/accessory balance,
- event/cocktail/formal/business distinctions,
while avoiding stereotyped assumptions such as requiring heels or dresses.

### Sizing
Women's numeric and letter sizes are treated as highly brand/line specific.
Real fit history remains stronger evidence than a generic brand-size assumption.

No destructive migration. Existing menswear users, wardrobes, images, Saved Looks and fit history are preserved.


## V6.7 — Shopping Intelligence

### Smarter reasons to buy
Shopping recommendations now explicitly assess:
- wardrobe duplication risk,
- the purchase's role in the wardrobe,
- practical versatility,
- overlap with the closest owned items,
- synergy with existing clothes and Saved Looks.

The stylist is instructed to reject weak additions rather than recommend something merely because it is fashionable.

### Shopping modes
Users can choose:
- Best additions to my wardrobe
- Only genuine wardrobe gaps
- Help me complete an outfit
- Upgrade something I already own

### Live product intelligence
Current retailer products are now assessed against the user's actual wardrobe as well as the requested specification.

Each live product can return:
- wardrobe utility,
- duplicate risk,
- personal fit confidence,
- owned wardrobe pieces it should work especially well with.

The UI displays those signals alongside the existing retailer, price, sizing and Try On Me workflow.

### Fit & style evidence
The live product search continues to use the user's real fit history, measurements and profile-specific menswear/womenswear guidance.

No destructive migration. Accounts, wardrobes, images, Saved Looks, Fit Intelligence and prior shopping data are preserved.


## V6.8 — Guided Onboarding & Dictation Control

### Cancel dictation
All in-app MediaRecorder dictation now has two distinct actions while listening:
- **Stop & use dictation** — finishes recording, transcribes it and applies/adds the result.
- **Cancel dictation** — stops immediately and discards the recording without transcription or writing partial speech into the form.

This applies to both ordinary free-text dictation and smart form-filling dictation.

### New-user walkthrough
New tester accounts receive a six-step guided walkthrough on first use:
1. Why wardrobe data matters.
2. Profile, measurements and model photos.
3. Fast wardrobe setup via Quick Add.
4. How to ask the stylist naturally.
5. Fit-learning / Know My Size.
6. Shopping, packing, Saved Looks and Wardrobe Insights.

The walkthrough adapts to the signed-in Get Him Dressed / Get Her Dressed profile, can open the relevant feature directly, can be skipped, and can be restarted at any time from **My Account → Show me around the app**.

The tour is stored per account in browser storage. Existing owner accounts with established wardrobes are not forced through it.

No database migration and no destructive changes to wardrobes, images, Saved Looks or learning data.


## V6.8.1 — Shopping Audience Guard

This hotfix closes the cross-profile shopping leak found during live testing.

### Hard audience verification
Every live retailer product now returns an explicit audience classification:
- menswear
- womenswear
- unisex
- uncertain

The shopping model must justify that classification from retailer/source evidence such as:
- WOMEN / MEN navigation,
- breadcrumb/category,
- explicit product copy,
- brand product section,
- model context,
- explicit unisex designation.

Generic garment names such as cardigan, coat or trainers are not sufficient evidence.

### Server-side enforcement
The server now performs a second, deterministic check after the live web search:
- Get Him Dressed accepts only menswear or genuinely unisex products.
- Get Her Dressed accepts only womenswear or genuinely unisex products.
- Opposite-profile and uncertain products are discarded before reaching the UI.

The search note reports when mismatched products were filtered out.

This is a sourcing-only hotfix. It does not alter accounts, wardrobes, images, Saved Looks, fit history or onboarding data.


## V6.9 — Reliability & Beta Hardening

### Authentication
- Failed sign-ins are rate-limited per email after 5 failures within 15 minutes.
- A successful login clears the failure counter.
- Disabled tester accounts now receive a clear disabled-account response instead of receiving a session that immediately fails.
- Existing sessions and password storage remain unchanged.

### External URL / image safety
- Retailer thumbnails and product URLs now use the existing public-IP validation rather than scheme-only checks.
- Redirects to private, loopback, reserved or local-network addresses are blocked.
- Try On Me product-image downloads validate the final URL, MIME type and maximum file size before saving anything.

### Storage isolation
- Explicit garment deletion now resolves image files through the signed-in user's storage root and refuses to remove files outside that root.
- This closes an old legacy-path assumption without changing normal wardrobe storage.

### Photo cleanup
- remove.bg credit/quota failures now show:
  "Background-cleaning credits have run out or the service limit has been reached. Add remove.bg credits and try again."
- Original photos remain unchanged after any cleanup failure.

### Privacy / diagnostics
- The public health endpoint no longer exposes internal disk or database paths.
- Owner/Admin gets a new **System check** card showing:
  - storage writable status,
  - missing wardrobe-image references,
  - AI connection,
  - photo-cleanup configuration.

### Front-end failures
The shared API helper now distinguishes:
- connection loss,
- rate limiting,
- temporary service failures,
- ordinary request errors,
with clearer messages and without implying saved data was lost.

No destructive migration. Existing accounts, wardrobes, original images, generated visuals, Saved Looks, fit history and onboarding data are preserved.


## V7.0 — Saved Looks 2.0

Saved Looks is now a wardrobe-memory system rather than a simple favourites list.

### Organisation
Each saved look can now store:
- editable name,
- occasion,
- season,
- up to 12 tags,
- private notes,
- pinned / unpinned state.

Existing saved looks are preserved and automatically gain empty/default values for the new fields.

### Search & filters
Saved Looks can be searched across:
- look name,
- original request,
- notes,
- tags,
- occasion / season,
- brands, garment types, colours and materials in the outfit.

Filters include:
- occasion,
- season,
- pinned looks,
- worn before,
- not worn yet.

### Wear history
**I wore this** records:
- total wear count,
- last-worn date.

This creates a stronger future signal than merely saving a look.

### Reuse
**Wear / style again** takes the saved outfit back into Ask My Stylist as the starting point, keeping the original look intact unless a useful contextual change is needed.

The existing:
- More like this,
- Use as inspiration,
- Works for me,
features remain available.

### Pinned looks
Strong repeatable outfits can be pinned. Pinned looks appear first in the collection and can be filtered directly.

No destructive migration. Existing accounts, wardrobe data, images, Saved Looks, visuals, feedback and fit history remain intact.


## V7.1 — Help Me Pack 3.0 / Persistent Trips

### Saved trips
Packing plans can now be saved, reopened, updated and removed.
A saved trip retains:
- the original trip brief and structured fields,
- destination/dates,
- researched trip/weather context,
- packing plan,
- luggage choice,
- packing checklist state.

### Luggage-aware planning
Users can now specify Hand luggage only, Cabin case, Checked suitcase or Large checked suitcase.
The packing engine is instructed to respect that constraint when deciding how much to take.

### Packing checklist
Every owned garment in the capsule becomes a persistent checklist item.
Travel-day garments are identified separately as **Wear on travel day** rather than **Pack in luggage**.
Checklist progress is saved automatically for saved trips.

### Reopen and continue
Opening a saved trip restores its form fields, research, outfit plan and checklist, so users can continue where they left off rather than rebuilding the trip.

### Refresh weather
A saved/current trip has **Refresh weather**, which reruns destination/weather research while leaving the existing capsule/outfit plan intact. If the trip is already saved, the refreshed context is saved back to that trip.

No destructive migration. Existing wardrobes, Saved Looks, generated outfit visuals and user accounts remain unchanged.


## V7.1.1 — Clearer "I wore this" feedback

- The button is now neutral before a look has been worn, avoiding the impression that it is already selected.
- After recording a wear it changes visibly to **Worn X×**.
- Saved Look cards now show a dedicated wear-status panel with:
  - wear count,
  - last-worn date,
  - an explanation that real wear is stronger learning evidence than simply saving a look.
- A short confirmation toast appears after each click explaining that the wear has been recorded and is influencing future style learning.
- Existing wear counts and history are preserved.


## V7.2 — Get Her Dressed refinement

This phase deepens womenswear behaviour while keeping the same shared platform and preserving Get Him Dressed behaviour.

### Womenswear styling intelligence
The stylist now reasons more explicitly about:
- complete-outfit silhouette and proportion,
- bust/chest, waist, hip and torso fit separately,
- trouser/jean rise, seat/thigh and leg shape,
- skirt sitting point and hem length,
- dress/jumpsuit torso and overall length,
- structured vs relaxed tailoring,
- neckline, sleeve volume and layering,
- footwear practicality and hem/trouser interaction,
- bags and jewellery as purposeful styling elements rather than automatic additions,
- distinct occasion types such as wedding guest, cocktail/party, formal evening, work event and daytime event.

It explicitly avoids body-shape stereotypes and does not default to dresses or heels.

### Womenswear profile preferences
Optional Get Her Dressed profile fields now include:
- preferred trouser/jean rise,
- preferred hem/garment length,
- heel preference,
- bag/jewellery/accessory notes.

These feed future styling and shopping context and can also be captured by smart dictation.

### Wardrobe taxonomy
Womenswear now has a dedicated **Jewellery** category separate from general Accessories.

### Visualisation fix
The retailer-product Try On Me workflow had one legacy prompt that still said "menswear visualisation".
It is now profile-aware and uses the correct Get Him / Get Her audience and model context.

### Occasion vocabulary
Styling forms and dictation now recognise more useful event types:
Wedding guest, Cocktail / party, Formal evening, Work event and Daytime event.

No destructive migration. Existing menswear and womenswear accounts, wardrobes, images, Saved Looks, trips and fit history remain intact.


## V7.3 — Visual & Product Polish

V7.3 focuses on making the existing app feel more deliberate and finished rather than adding another large workflow.

### Correctable wear history
Saved Looks now records a small wear-event history for new wear taps.

- **I wore this** records the wear and increases the total.
- Once a look has been worn, the main action becomes **+ Add another wear**.
- **− Undo last wear** removes the most recently recorded wear.
- The count can never fall below zero.
- If the undone wear was created in V7.3+, the previous last-worn date is restored exactly.
- Older pre-V7.3 wear counts are preserved. If one of those legacy counts is reduced, the old last-worn date is retained until the count reaches zero because the historical individual dates were never stored.

This means accidental taps can now be corrected without corrupting the learning signal.

### Navigation polish
The bottom navigation now clearly highlights the active destination instead of every navigation item having equal visual weight.

### Home polish
- stronger primary stylist CTA,
- softer secondary feature cards,
- subtle trust/intelligence cues for Private wardrobe, Fit learning and Real wear memory,
- refined hover/press feedback.

### Saved Looks polish
- stronger editorial card hierarchy,
- improved generated visual presentation,
- clearer wear-evidence panel,
- cleaner primary vs secondary actions.

### Loading / interaction polish
- consistent shimmer treatment for loading states,
- clearer field focus states,
- softer motion and button press feedback,
- more consistent elevated-card treatment.

No destructive migration. Existing wardrobes, Saved Looks, old wear counts, trips, accounts, images and fit history remain intact.


## V7.3.1 — Saved Look image sizing fix

V7.3 accidentally promoted Saved Look generated visuals to full card width and used `object-fit: cover`, making portrait outfit images oversized and visually dominant.

This corrective release:
- caps Saved Look visuals at 520px wide on desktop,
- centres them within the card,
- preserves their natural portrait aspect ratio,
- uses `object-fit: contain` so no part of the outfit is cropped,
- keeps full responsive width on smaller phones,
- leaves the V7.3 wear-history controls and other polish unchanged.

No database or data migration changes.


## V7.4 — Flexible Styling & Weekly Planner

### Replace one outfit
Ask My Stylist now gives each main suggestion a **Replace this outfit** action.
It generates one genuinely different replacement while preserving the original occasion, weather and constraints, and avoids duplicating the other suggestions already shown.

Help Me Pack also adds **Replace this look** to each individual trip outfit, so one weak day/look can be changed without rebuilding the entire trip.

Plan My Week has the same **Replace this day** behaviour.

### Refine the whole stylist set
Ask My Stylist gains a refinement bar after results are returned.

Quick refinements:
- Darker
- More brown
- Less formal
- Smarter

There is also free text for requests such as:
- "more navy"
- "different shoes"
- "no jackets"
- "more relaxed"
- "less monochrome"

The original styling brief is retained and the refinement is applied on top.

### Plan My Week
A new **Plan My Week** workflow brings the capsule-planning concept home.

Users can provide:
- week starting date,
- 5 or 7 days,
- location for weather,
- natural-language description of the week's schedule,
- work/daily context,
- dress needs,
- optional shopping permission.

The planner creates one distinct outfit per day from the real wardrobe, balances variety with sensible reuse, considers Saved Looks and wear history, and supports personalised outfit visuals.

This is deliberately separate from Help Me Pack: it plans a normal home/work week rather than pretending the user is travelling.

No destructive migration. Existing wardrobes, accounts, Saved Looks, trips, wear history and visuals remain intact.


## V7.4.1 — Menswear wardrobe taxonomy refinement

The visible menswear wardrobe now uses three deliberate top-level tailoring/outerwear groups:

- **Blazers & Tailoring** — blazers, sports coats, complete suits and explicitly identified matching suit components.
- **Overshirts & Shirt Jackets** — kept separate because these can function as shirts, mid-layers or light outer layers.
- **Jackets & Coats** — all genuine outerwear, including wax, rain/technical, denim/trucker, sherpa-lined, bomber, field, leather/suede, gilet, puffer, parka, mac, overcoat and winter coats.

The detailed `garment_type`, `model_line`, material, fit, notes, season and formality fields remain intact, so simplifying the visible browse categories does not flatten the stylist's understanding.

### Existing wardrobe migration
Existing menswear records stored as `Jackets`, `Coats`, `Jackets & Outerwear` or similar legacy values are safely reclassified into `Jackets & Coats` when the wardrobe loads. This changes category metadata only; garment records and images are untouched.

### Suit handling
Complete suits and explicitly labelled suit components are grouped under **Blazers & Tailoring**. The underlying garment type still distinguishes suit jacket, matching trousers, waistcoat or complete suit so the stylist can reason about whether components can sensibly be worn separately.

No destructive migration.


## V7.5 — Style Learning & Wardrobe Memory

This release makes existing behaviour signals materially affect the AI rather than only being stored/displayed.

### Evidence hierarchy
The styling system now consistently distinguishes:
1. repeated actual wear,
2. a single actual wear,
3. repeated positive reactions / recurring saved patterns,
4. one saved-only look.

Fit reviews remain the strongest evidence for fit and sizing; wear evidence is used primarily for taste, practicality and real-world preference.

### Actual wear is now passed into
- Ask My Stylist,
- Replace This Outfit,
- More Like This,
- Help Me Pack,
- Plan My Week,
- Wardrobe Gaps / shopping intelligence,
- Wardrobe Intelligence,
- legacy What Should I Wear flow.

Saved Look context now includes wear count, last-worn date, pinned state, tags, occasion, season and notes where relevant.

### Style Learning panel
My Profile now distinguishes:
- recorded real wears,
- colours recurring in actually worn looks,
- garment types recurring in actually worn looks,
- saved-only patterns,
- outfit reactions,
- perfect-fit brands.

### Wardrobe Intelligence
Wardrobe Intelligence now keeps saved frequency and real-wear frequency separate. It no longer has to treat a repeatedly saved garment as though it were necessarily worn frequently.

No destructive migration. Existing Saved Looks and wear counts are used immediately.


## V7.5.1 — Category classifier fix + manual category override

This corrective build fixes a category self-reinforcement bug and adds manual category control.

### Root cause fixed
The automatic classifier previously included the garment's **existing stored category** in the text it used to decide the next category. This meant a previous mistake could become self-reinforcing: an outerwear garment already stored as `Blazers & Tailoring` supplied the word `Blazers` back to the classifier on every reload.

The stored category is now excluded from semantic classification evidence. The classifier uses the real garment metadata — garment type, model/line, fit/cut, notes, brand and material — then uses the old category only as a final legacy fallback.

A denim/trucker/sherpa jacket therefore resolves to **Jackets & Coats** even if its previous stored category was **Blazers & Tailoring**.

### Manual category dropdown
The Add Garment and Edit Garment forms now use a category dropdown containing the current profile's real wardrobe sections.

When a category is saved from **Edit Garment**, it becomes a manual category override. Automatic classification will no longer move that garment later.

This allows the user to correct edge cases immediately without fighting the AI classifier.

### Persistence
A new additive `category_manual` flag is stored per garment. Existing garments default to automatic classification; manually edited garments become locked to the selected category.

No garments, images, fit history, Saved Looks or other user data are removed.


## V7.6 — Guided Setup & First-Week Experience

V7.6 adds a persistent, server-backed setup progress system so new testers can understand what materially improves the stylist without being forced through the walkthrough again.

### Six useful setup milestones
The app now tracks:
1. Personal profile — name plus at least one sizing, fit or style signal.
2. Model photo — at least one saved likeness photo.
3. Useful wardrobe — at least six wardrobe items.
4. Fit review — at least one confirmed real-garment fit review.
5. Personal stylist — first real stylist result.
6. Saved Look — at least one outfit deliberately saved.

### Home guidance
Until all six milestones are complete, Home shows a compact premium setup card with:
- live completion percentage,
- each milestone and its current real state,
- the next most useful action,
- direct navigation into the right feature.

The card disappears from Home once setup is complete.

### Account status
My Account always shows a compact Personal Setup status, so users can see whether the core personalisation signals are complete without replaying onboarding.

### Cross-device persistence
Progress is calculated from real server data. Stylist usage uses a small additive `setup_events` table, while profile, wardrobe, model photos, fit reviews and Saved Looks are read directly from their existing records.

Existing users are recognised from their current data rather than being treated as new accounts.

No destructive migration.


## V7.6.1 — Build Around anchor fix

A deliberate **Build around** click now takes priority over restoration of the previous Ask My Stylist session.

### Root cause
When Build Around navigated to Ask My Stylist, the app scheduled restoration of the previous stylist session on the next animation frame. The clicked garment was selected correctly at first, but the old saved request could then repaint over the new brief — making it appear that the wrong garment had been selected.

### Fix
- Build Around now sets an explicit one-shot navigation intent before opening Ask My Stylist.
- Previous-session restoration is suppressed for that navigation.
- The clicked garment ID is selected after the anchor list is populated.
- A fresh brief is always written for the clicked item, including colour, brand, model/line and garment type when available.
- Old stylist results/refinement controls are cleared so they cannot be mistaken for results belonging to the newly selected garment.
- The normal previous-session restoration behaviour remains unchanged when opening Ask My Stylist normally.

No data migration and no user data changes.


## V7.6.2 — Fit feedback + garment-aware detailed reviews

### Setup milestone
**Teach me what fits** is complete when the user has either:
- meaningful quick Fit Feedback on at least one garment, or
- a confirmed detailed fit review.

Existing `Perfect fit`, `Slightly tight`, `Slightly loose`, `Too tight` and `Too loose` feedback therefore counts immediately.

### Detailed fit reviews are now optional
Quick Fit Feedback is explicitly treated as useful real-world evidence. The detailed review is available only for extra sizing precision.

### Garment-aware questions
The detailed form now asks only relevant questions:
- tops / knitwear / shirts: chest or bust, shoulders, sleeves, body length
- jackets / coats / blazers / overshirts: chest or bust, shoulders, sleeves, body length
- trousers / jeans / shorts: waist, hips/seat, leg length
- skirts: waist, hips, length
- dresses / jumpsuits: bust/chest, waist, hips, overall length
- footwear: labelled size, overall fit and notes only
- other items: overall fit and notes only

Confirmed-fit summaries show only the fields relevant to that garment.

### Know My Size
Quick Fit Feedback now contributes to Fit Intelligence. A garment with meaningful quick feedback is no longer treated as unreviewed merely because the optional detailed form has not been completed.

Detailed fit reviews no longer overwrite the simpler Fit Feedback value.

No destructive migration.


## V7.6.3 — Generated outfit image sizing

Generated outfit visuals are now constrained by both width and viewport height so a complete look is normally visible on a laptop without scrolling just to see the lower half of the outfit.

Applies consistently to:
- Ask My Stylist visualisations
- Saved Looks
- Build My Own Look
- Help Me Pack generated looks
- other model / try-on generated outfit images

Desktop and laptop visuals are centred, use `object-fit: contain`, and cap their height relative to the browser viewport. Mobile remains full-width with a sensible viewport-height cap.

No image files are altered and there is no data migration.


## V7.7 — Data Safety & Portability

V7.7 adds a private, per-account backup system before any future managed-database or object-storage migration.

### My Account → Backup & portability
Each signed-in user can download a ZIP containing only their own data:
- profile and sizing preferences
- wardrobe records
- quick and detailed fit learning
- outfit reactions
- Saved Looks
- wear-event history
- saved trips and checklists
- shopping shortlist
- setup progress events
- model photos
- uploaded garment images
- cleaned catalogue images
- generated outfit images

### Backup contents
Each ZIP contains:
- `portable-data.json` — human-readable / migration-friendly structured data
- `summary.json` — counts and media size
- `stylist.db` — a consistent SQLite snapshot created via SQLite's backup API
- `media/` — the user's own stored image files
- `README.txt`

### Privacy and safety
The export does **not** include:
- password hashes
- session tokens
- invite codes
- the shared accounts database
- tester feedback belonging to other users
- any other user's wardrobe or media

The export is read-only: creating a backup does not mutate or delete the live wardrobe. Temporary server-side export files are deleted after the download response is served.

This is intentionally a portability/recovery foundation before a later Postgres/object-storage migration.


## V7.8 — Account Security Controls

V7.8 strengthens the account layer before any major database/storage migration.

### My Account → Security
Users can now:
- see how many active sessions exist,
- see whether other sessions/devices are signed in,
- change their password by confirming the current password,
- sign out every other session while keeping the current device signed in.

### Password changes
Changing a password:
- requires the correct current password,
- requires a new password of at least eight characters,
- refuses the same password,
- stores a fresh PBKDF2-HMAC-SHA256 password hash,
- revokes every other active session after the change.

### Session control
`Sign out other devices` deletes only other session tokens for the current user. It does not touch wardrobe/profile data and it does not sign out the current browser.

### Browser write-action protection
Authenticated POST/PUT/PATCH/DELETE API requests now reject an explicit cross-origin Origin/Referer. This complements the existing HttpOnly + SameSite=Lax session cookie.

### Deliberate limitation
V7.8 does **not** pretend to provide email-based forgotten-password recovery. Proper password-reset and email verification should be added only when a real transactional email provider is connected.

No wardrobe/media migration. No destructive user-data changes.


## V7.9 — Premium Visual Redesign

V7.9 is a presentation-layer redesign rather than a feature release. Existing workflows, data models and behaviours remain intact.

### Design direction
- warmer editorial neutral palette
- serif display typography for fashion/editorial hierarchy
- calmer sans-serif UI typography for controls and metadata
- reduced "boxy SaaS" appearance
- more intentional whitespace and spacing rhythm
- lighter borders and shadows
- pill-shaped controls with quieter hierarchy
- stronger fashion-catalogue image framing
- desktop floating navigation dock
- deliberately different desktop and mobile compositions

### Home
The home screen now behaves more like a fashion/editorial landing page:
- larger display headline
- two-column desktop hero
- stronger primary stylist action
- refined trust signals
- three-column desktop feature grid
- calmer setup-progress treatment

### Wardrobe
Wardrobe is now a catalogue-style layout with:
- three columns on larger desktops
- larger garment imagery
- less visual chrome
- editorial category headings
- quieter category pills and search
- cleaner action controls

### Ask My Stylist
Stylist results have been redesigned as premium recommendation cards:
- editorial outfit titles
- refined score treatment
- cleaner garment rows
- softer stylist notes
- better action hierarchy
- more intentional generated-image framing

### Saved Looks, packing, weekly planning and shopping
These now share the same premium visual language so the app feels like one product rather than separate feature modules.

### Account and profile
Security, backups, model photos and profile learning use a private-client aesthetic rather than administrative dashboard styling.

### Generated image sizing
The V7.6.3 laptop-friendly image constraints are explicitly preserved. V7.9 does not make outfit images oversized again.

### Behaviour and data safety
No feature logic was intentionally removed or changed. No destructive migration.
