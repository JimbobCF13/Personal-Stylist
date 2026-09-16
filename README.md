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
