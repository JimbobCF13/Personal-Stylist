let currentGarmentDetail=null;

const $=id=>document.getElementById(id);
let garments=[], uploadedPath="", aiConfidence=0;
let editingGarmentId=null;
let detailGarmentId=null;
let enrichmentPollTimer=null;
let photoQueue=[], currentPhotoIndex=-1, batchMode=false;
const cleanupInProgress=new Set();


function esc(value){
 return String(value??"")
  .replaceAll("&","&amp;")
  .replaceAll("<","&lt;")
  .replaceAll(">","&gt;")
  .replaceAll('"',"&quot;")
  .replaceAll("'","&#039;");
}

async function api(url,opts={}){
 const r=await fetch(url,opts); const data=await r.json().catch(()=>({}));
 if(!r.ok) throw new Error(data.detail||"Something went wrong");
 return data;
}
function go(id){document.querySelectorAll(".screen").forEach(x=>x.classList.remove("active"));$(id).classList.add("active");scrollTo(0,0);if(id==="wardrobe")loadGarments();if(id==="shortlist")loadShortlist();if(id==="garmentdetail"&&detailGarmentId)loadGarmentDetail(detailGarmentId);if(id==="outfits")populateAnchor();if(id==="stylistv4")populateV4Anchor();if(id==="profile"){loadProfile();loadStyleLearning();loadModelPhotos()}}
document.addEventListener("click",e=>{const b=e.target.closest("[data-go]");if(b)go(b.dataset.go)});
async function init(){
 try{const h=await api("/api/health");$("status").textContent=h.ai_enabled?"AI stylist connected":"Working prototype · AI key not connected"}catch{$("status").textContent="App offline"}
 await loadGarments(); await loadProfile();
}
const WARDROBE_ORDER=["Jackets & Outerwear","Knitwear","Shirts","Polos & T-Shirts","Trousers","Shorts","Footwear","Accessories","Other"];
function normalisedCategory(c){const raw=String(c||"Other").trim().toLowerCase();return WARDROBE_ORDER.find(x=>x.toLowerCase()===raw)||"Other";}
async function loadGarments(){garments=await api("/api/garments");garments.forEach(g=>g.category=normalisedCategory(g.category));$("count").textContent=`${garments.length} saved item${garments.length===1?"":"s"}`;const present=WARDROBE_ORDER.filter(cat=>garments.some(g=>g.category===cat));$("filter").innerHTML='<option value="">All categories</option>'+present.map(c=>`<option value="${esc(c)}">${esc(c)}</option>`).join("");renderGarments();}
function garmentCard(g){const cleaning=cleanupInProgress.has(g.id);const label=esc((g.brand?g.brand+" ":"")+(g.garment_type||"Garment"));const image=g.image_path?`<img class="garment-photo" src="${g.image_path}" alt="${label}" onclick="openGarment(${g.id})" title="Open garment" onerror="this.classList.add('image-missing')">`:`<button class="garment-no-photo" onclick="openGarment(${g.id})" type="button"><span>No photo yet</span><small>Open garment</small></button>`;return `<div class="garment${cleaning?" is-cleaning":""}"><div class="garment-photo-wrap">${image}${cleaning?`<div class="cleanup-overlay"><span class="cleanup-spinner"></span><b>Cleaning up photo…</b><small>Preparing your catalogue image.</small></div>`:""}</div><div class="meta"><b>${label}</b><small>${esc([g.colour,g.material,g.labelled_size].filter(Boolean).join(" · "))}</small><div><span class="pill">${esc(g.fit_feedback||"Fit unknown")}</span></div><div class="row" style="margin-top:9px"><button class="secondary" onclick="buildAround(${g.id})">Build around</button><button class="ghost" onclick="editGarment(${g.id})">Edit</button>${g.image_path?`<button class="ghost cleanup-btn" onclick="cleanupPhoto(${g.id})">${cleaning?"Cleaning…":"Clean up photo"}</button>`:""}${g.original_image_path&&g.image_path!==g.original_image_path?`<button class="ghost" onclick="restoreOriginal(${g.id})">Original photo</button>`:""}<button class="danger" onclick="del(${g.id})">Delete</button></div></div></div>`;}
function renderGarments(){const q=$("search").value.toLowerCase(),f=$("filter").value;const list=garments.filter(g=>(!f||g.category===f)&&(!q||JSON.stringify(g).toLowerCase().includes(q)));if(!list.length){$("garments").innerHTML='<div class="empty">No garments match this view.</div>';return;}const cats=f?[f]:WARDROBE_ORDER;$("garments").innerHTML=cats.map(cat=>{const items=list.filter(g=>g.category===cat);if(!items.length)return "";return `<section class="wardrobe-group"><div class="wardrobe-group-head"><h4>${esc(cat)}</h4><span>${items.length} item${items.length===1?"":"s"}</span></div><div class="garments wardrobe-group-grid">${items.map(garmentCard).join("")}</div></section>`;}).join("");}
$("search").addEventListener("input",renderGarments);$("filter").addEventListener("change",renderGarments);



function openGarment(id){
 detailGarmentId=id;
 clearTimeout(enrichmentPollTimer);
 go("garmentdetail");
}

function detailValue(v){
 return v ? esc(v) : '<span class="detail-empty">Not recorded</span>';
}

function enrichmentSources(sources){
 if(!sources||!sources.length)return "";
 return `<div class="research-sources"><small>SOURCES</small>${sources.map(s=>{
  let u="#";
  try{const x=new URL(s.url);if(["http:","https:"].includes(x.protocol))u=x.href}catch{}
  return `<a href="${u}" target="_blank" rel="noopener"><b>${esc(s.title||"Source")}</b><span>${esc(s.note||"")}</span></a>`;
 }).join("")}</div>`;
}


function renderFitReviewPanel(g){
 const status=g.fit_review_status||"";
 if(!status && g.purchase_status!=="bought")return "";
 if(status==="confirmed"){
  return `<div class="detail-research fit-confirmed">
   <div class="research-head"><div><small>FIT LEARNING</small><h4>Fit confirmed</h4></div><span class="fit-status-pill">Learned</span></div>
   <p><b>${esc(g.brand||"This garment")} ${esc(g.labelled_size||"")}</b>${g.fit_rating?` · ${esc(g.fit_rating)}/5`:""}</p>
   <div class="fit-summary-grid">
    <span>Chest <b>${esc(g.fit_chest||"—")}</b></span><span>Waist <b>${esc(g.fit_waist||"—")}</b></span>
    <span>Length <b>${esc(g.fit_length||"—")}</b></span><span>Sleeve <b>${esc(g.fit_sleeve||"—")}</b></span>
    <span>Shoulders <b>${esc(g.fit_shoulders||"—")}</b></span>
   </div>
   ${g.fit_notes?`<p>${esc(g.fit_notes)}</p>`:""}
   <button class="ghost" onclick="openFitReview(${g.id})">Update fit review</button>
  </div>`;
 }
 return `<div class="detail-research fit-awaiting">
  <div class="research-head"><div><small>FIT LEARNING</small><h4>How does it actually fit?</h4></div><span class="fit-status-pill awaiting">Awaiting review</span></div>
  <p>This purchase is saved in your wardrobe. Once it arrives, review the real fit and I’ll use that experience in future shopping recommendations.</p>
  <button class="primary" onclick="openFitReview(${g.id})">Review the fit</button>
 </div>`;
}

function fitOptions(value){
 const opts=["Much too tight","Slightly tight","Good","Slightly loose","Much too loose"];
 return opts.map(x=>`<option ${value===x?"selected":""}>${x}</option>`).join("");
}

function openFitReview(id){
 const g=currentGarmentDetail||{};
 const panel=$("fitReviewPanel");
 if(!panel)return;
 panel.innerHTML=`<div class="detail-research fit-form">
  <small>FIT REVIEW</small><h4>Teach the stylist how this actually fits</h4>
  <div class="fit-form-grid">
   <label>Labelled size<input id="fit-size" value="${esc(g.labelled_size||"")}"></label>
   <label>Overall fit<select id="fit-rating"><option value="">Choose</option>${[1,2,3,4,5].map(n=>`<option value="${n}" ${Number(g.fit_rating)===n?"selected":""}>${n}/5</option>`).join("")}</select></label>
   <label>Chest<select id="fit-chest">${fitOptions(g.fit_chest)}</select></label>
   <label>Waist<select id="fit-waist">${fitOptions(g.fit_waist)}</select></label>
   <label>Body / leg length<select id="fit-length">${fitOptions(g.fit_length)}</select></label>
   <label>Sleeve<select id="fit-sleeve">${fitOptions(g.fit_sleeve)}</select></label>
   <label>Shoulders<select id="fit-shoulders">${fitOptions(g.fit_shoulders)}</select></label>
  </div>
  <label>Anything else<textarea id="fit-notes" rows="3" placeholder="e.g. good through the body but sleeves slightly long">${esc(g.fit_notes||"")}</textarea></label>
  <div class="row"><button class="primary" onclick="saveFitReview(${id})">Save fit review</button><button class="ghost" onclick="loadGarmentDetail(${id})">Cancel</button></div>
 </div>`;
}

async function saveFitReview(id){
 try{
  await api(`/api/garments/${id}/fit-review`,{
   method:"POST",headers:{"Content-Type":"application/json"},
   body:JSON.stringify({
    labelled_size:$("fit-size").value.trim(),
    fit_rating:$("fit-rating").value?Number($("fit-rating").value):null,
    fit_chest:$("fit-chest").value,fit_waist:$("fit-waist").value,
    fit_length:$("fit-length").value,fit_sleeve:$("fit-sleeve").value,
    fit_shoulders:$("fit-shoulders").value,fit_notes:$("fit-notes").value.trim()
   })
  });
  await loadGarmentDetail(id);
 }catch(err){alert(err.message)}
}

function renderEnrichmentPanel(g){
 const status=g.enrichment_status||"";
 const e=g.enrichment;

 if(status==="researching"){
  return `<div class="detail-research"><div class="research-head"><div><small>BRAND INTELLIGENCE</small><h4>Researching ${esc(g.brand||"this garment")}…</h4></div><span class="spinner"></span></div><p>I’m checking brand/line fit information, sizing, fabric and construction in the background.</p></div>`;
 }
 if(status==="needs_brand"){
  return `<div class="detail-research"><small>BRAND INTELLIGENCE</small><h4>Add the brand to research this garment</h4><p>Once a brand is known I can look for line-specific fit and sizing information.</p></div>`;
 }
 if(status==="error"){
  return `<div class="detail-research"><small>BRAND INTELLIGENCE</small><h4>Research couldn’t be completed</h4><p>Your garment data is unchanged.</p><button class="ghost" onclick="researchGarment(${g.id})">Try again</button></div>`;
 }
 if(status==="ignored"){
  return `<div class="detail-research muted-research"><small>BRAND INTELLIGENCE</small><p>Research is hidden for this garment.</p><button class="ghost" onclick="researchGarment(${g.id})">Research again</button></div>`;
 }
 if(!e){
  return `<div class="detail-research"><small>BRAND INTELLIGENCE</small><h4>Make this garment smarter</h4><p>Research the brand and model/line for fit tendencies, sizing information and construction details.</p><button class="primary" onclick="researchGarment(${g.id})" ${g.brand?"":"disabled"}>${g.brand?"Research brand & model":"Add brand first"}</button></div>`;
 }

 const exact=e.likely_exact_match?'<span class="research-confidence exact">Likely exact line</span>':'<span class="research-confidence">Best available match</span>';
 return `<div class="detail-research research-ready">
   <div class="research-head"><div><small>BRAND INTELLIGENCE</small><h4>${esc(e.model_line||g.brand||"Web research")}</h4></div>${exact}</div>
   <p>${esc(e.identification_summary||"")}</p>
   <div class="research-grid">
    <div><small>FIT PROFILE</small><p>${detailValue(e.fit_profile)}</p></div>
    <div><small>SIZE GUIDANCE</small><p>${detailValue(e.sizing_guidance)}</p></div>
    <div><small>FABRIC</small><p>${detailValue(e.fabric_details)}</p></div>
    <div><small>CONSTRUCTION</small><p>${detailValue(e.construction_details)}</p></div>
    <div><small>SEASONALITY</small><p>${detailValue(e.seasonality)}</p></div>
    <div><small>SIZE CHART / MEASUREMENTS</small><p>${detailValue(e.measurements_or_size_chart)}</p></div>
   </div>
   ${enrichmentSources(e.sources)}
   <div class="research-actions"><button class="primary" onclick="applyEnrichment(${g.id})">Apply to blank fields</button><button class="ghost" onclick="researchGarment(${g.id})">Refresh research</button><button class="text-button" onclick="ignoreEnrichment(${g.id})">Ignore</button></div>
   <small class="research-disclaimer">Web-enriched information is advisory. It never silently overwrites details you entered yourself.</small>
  </div>`;
}

async function loadGarmentDetail(id){
 clearTimeout(enrichmentPollTimer);
 const box=$("garmentDetailContent");
 if(!box)return;
 box.innerHTML='<div class="card v4-thinking"><span class="spinner"></span> Loading garment…</div>';

 try{
  const g=await api(`/api/garments/${id}/detail`);
  currentGarmentDetail=g;
  detailGarmentId=id;
  const title=esc((g.brand?g.brand+" ":"")+(g.garment_type||g.category||"Garment"));
  const hist=(g.outfit_history||[]).length
   ? `<div class="detail-history"><small>RECENT OUTFIT FEEDBACK</small>${g.outfit_history.map(h=>`<div><b>${esc(h.label)}</b><span>${esc(h.rating||"")}</span></div>`).join("")}</div>`
   : `<div class="detail-history"><small>OUTFIT HISTORY</small><p>This garment has not appeared in rated outfits yet.</p></div>`;

  box.innerHTML=`<div class="garment-detail-hero">
    <div class="detail-image-wrap"><img src="${g.image_path}" alt="${title}"></div>
    <div class="detail-summary">
     <small>${esc(g.category||"WARDROBE ITEM")}</small>
     <h2>${title}</h2>
     <p>${esc([g.colour,g.material,g.labelled_size].filter(Boolean).join(" · "))}</p>
     <div class="detail-pills"><span>${esc(g.fit_feedback||"Fit unknown")}</span>${g.season?`<span>${esc(g.season)}</span>`:""}${g.formality?`<span>${esc(g.formality)}</span>`:""}</div>
     <div class="detail-actions"><button class="primary" onclick="buildAround(${g.id})">Build an outfit</button><button class="ghost" onclick="cleanupPhoto(${g.id})">Clean up photo</button></div>
    </div>
   </div>
   <div class="detail-columns">
    <div class="detail-card"><small>GARMENT DETAILS</small>
     <dl>
      <div><dt>Brand</dt><dd>${detailValue(g.brand)}</dd></div>
      <div><dt>Model / line</dt><dd>${detailValue(g.model_line)}</dd></div>
      <div><dt>Size</dt><dd>${detailValue(g.labelled_size)}</dd></div>
      <div><dt>Colour</dt><dd>${detailValue(g.colour)}</dd></div>
      <div><dt>Material</dt><dd>${detailValue(g.material)}</dd></div>
      <div><dt>Pattern</dt><dd>${detailValue(g.pattern)}</dd></div>
      <div><dt>Fit / cut</dt><dd>${detailValue(g.fit_cut)}</dd></div>
      <div><dt>Notes</dt><dd>${detailValue(g.notes)}</dd></div>
     </dl>
    </div>
    ${hist}
   </div>
   <div id="fitReviewPanel">${renderFitReviewPanel(g)}</div>
   <div id="brandIntelligencePanel">${renderEnrichmentPanel(g)}</div>`;

  $("detailEdit").onclick=()=>editGarment(g.id);

  if(g.enrichment_status==="researching"){
   enrichmentPollTimer=setTimeout(()=>pollGarmentEnrichment(id),2200);
  }
 }catch(err){
  box.innerHTML=`<div class="notice">${esc(err.message)}</div>`;
 }
}


async function pollGarmentEnrichment(id){
 clearTimeout(enrichmentPollTimer);
 if(detailGarmentId!==id)return;
 try{
  const g=await api(`/api/garments/${id}/detail`);
  if(detailGarmentId!==id)return;
  const panel=$("brandIntelligencePanel");
  if(panel)panel.innerHTML=renderEnrichmentPanel(g);
  if(g.enrichment_status==="researching"){
   enrichmentPollTimer=setTimeout(()=>pollGarmentEnrichment(id),2200);
  }
 }catch(err){
  const panel=$("brandIntelligencePanel");
  if(panel)panel.innerHTML=`<div class="detail-research"><small>BRAND INTELLIGENCE</small><p>${esc(err.message)}</p></div>`;
 }
}

async function researchGarment(id){
 try{
  await api(`/api/garments/${id}/enrich`,{method:"POST"});
  const panel=$("brandIntelligencePanel");
  if(panel){
   panel.innerHTML=`<div class="detail-research">
    <div class="research-head"><div><small>BRAND INTELLIGENCE</small><h4>Researching garment…</h4></div><span class="spinner"></span></div>
    <p>I’m checking brand/line fit information, sizing, fabric and construction in the background.</p>
   </div>`;
  }
  enrichmentPollTimer=setTimeout(()=>pollGarmentEnrichment(id),1200);
 }catch(err){alert(err.message)}
}

async function applyEnrichment(id){
 try{
  const x=await api(`/api/garments/${id}/apply-enrichment`,{method:"POST"});
  await loadGarments();
  await loadGarmentDetail(id);
  alert(x.applied_fields?.length?`Added researched detail to: ${x.applied_fields.join(", ")}.`:"Your existing fields already contained the researched details, so nothing was overwritten.");
 }catch(err){alert(err.message)}
}

async function ignoreEnrichment(id){
 try{
  await api(`/api/garments/${id}/ignore-enrichment`,{method:"POST"});
  await loadGarmentDetail(id);
 }catch(err){alert(err.message)}
}

async function cleanupPhoto(id){
 const g=garments.find(x=>x.id===id);
 if(!g||cleanupInProgress.has(id))return;
 if(!confirm("Clean up this photo? The original will be kept so you can restore it later."))return;

 cleanupInProgress.add(id);
 renderGarments();
 try{
  await api(`/api/garments/${id}/cleanup-image`,{method:"POST"});
  await loadGarments();
  alert("Photo cleaned up. Your original is still safely stored.");
 }catch(err){
  alert(err.message);
 }finally{
  cleanupInProgress.delete(id);
  renderGarments();
 }
}

async function restoreOriginal(id){
 if(!confirm("Show the original uploaded photo again?"))return;
 try{
  await api(`/api/garments/${id}/restore-original`,{method:"POST"});
  await loadGarments();
 }catch(err){
  alert(err.message);
 }
}

function editGarment(id){
 const g=garments.find(x=>x.id===id);
 if(!g)return;
 editingGarmentId=id;
 $("editPreview").src=g.image_path;

 const map={
  category:"e_category",
  garment_type:"e_garment_type",
  brand:"e_brand",
  model_line:"e_model_line",
  labelled_size:"e_labelled_size",
  colour:"e_colour",
  material:"e_material",
  pattern:"e_pattern",
  fit_cut:"e_fit_cut",
  fit_feedback:"e_fit_feedback",
  season:"e_season",
  formality:"e_formality",
  notes:"e_notes"
 };
 Object.entries(map).forEach(([k,id])=>{$(id).value=g[k]||""});
 go("edit");
}

$("saveEdit").addEventListener("click",async()=>{
 if(!editingGarmentId)return;
 const body={
  category:$("e_category").value,
  garment_type:$("e_garment_type").value,
  brand:$("e_brand").value,
  model_line:$("e_model_line").value,
  labelled_size:$("e_labelled_size").value,
  colour:$("e_colour").value,
  material:$("e_material").value,
  pattern:$("e_pattern").value,
  fit_cut:$("e_fit_cut").value,
  fit_feedback:$("e_fit_feedback").value,
  season:$("e_season").value,
  formality:$("e_formality").value,
  notes:$("e_notes").value
 };
 await api(`/api/garments/${editingGarmentId}`,{
  method:"PUT",
  headers:{"Content-Type":"application/json"},
  body:JSON.stringify(body)
 });
 const savedId=editingGarmentId;
 const brandForResearch=body.brand.trim();
 editingGarmentId=null;
 await loadGarments();
 detailGarmentId=savedId;
 go("garmentdetail");
 if(brandForResearch){
  api(`/api/garments/${savedId}/enrich`,{method:"POST"})
   .then(()=>loadGarmentDetail(savedId))
   .catch(()=>{});
 }
});

async function del(id){if(confirm("Remove this garment?")){await api(`/api/garments/${id}`,{method:"DELETE"});await loadGarments()}}
function buildAround(id){go("stylistv4");populateV4Anchor();$("v4Anchor").value=String(id);const g=garments.find(x=>x.id===id);if(g&&!$("v4Request").value.trim())$("v4Request").value=`Build me an outfit around my ${(g.brand?g.brand+" ":"")+(g.garment_type||g.category||"garment")}.`; }
function clearGarmentFields(){
 const ids=["category","garment_type","brand","model_line","labelled_size","colour","material","pattern","fit_cut","season","formality","notes"];
 ids.forEach(id=>$(id).value="");
 $("fit_feedback").value="Unknown";
 uploadedPath="";
 aiConfidence=0;
}

function updateBatchUI(){
 const status=$("batchStatus");
 const skip=$("skipGarment");
 if(batchMode && photoQueue.length){
   status.classList.remove("hidden");
   status.innerHTML=`<b>Batch upload:</b> item ${currentPhotoIndex+1} of ${photoQueue.length}. Check the AI details, then Save & Next.`;
   $("saveGarment").textContent=currentPhotoIndex < photoQueue.length-1 ? "Save & Next" : "Save final item";
   skip.classList.remove("hidden");
 }else{
   status.classList.add("hidden");
   $("saveGarment").textContent="Save to wardrobe";
   skip.classList.add("hidden");
 }
}

async function handleGarmentPhoto(file){
 if(!file)return;
 clearGarmentFields();
 $("preview").src=URL.createObjectURL(file);
 $("preview").classList.remove("hidden");
 const fd=new FormData();
 fd.append("file",file);
 $("analysisMsg").classList.remove("hidden");
 $("analysisMsg").textContent="Analysing garment…";
 updateBatchUI();
 try{
  const x=await api("/api/analyse-garment",{method:"POST",body:fd});
  uploadedPath=x.image_path;
  if(x.analysis){
   Object.entries(x.analysis).forEach(([k,v])=>{if($(k)&&k!=="confidence")$(k).value=v||""});
   aiConfidence=x.analysis.confidence||0;
   $("analysisMsg").textContent=`AI analysis complete (${Math.round(aiConfidence*100)}% confidence). Please check and correct anything before saving.`;
  }else{
   $("analysisMsg").textContent="Photo saved. AI is not connected yet, so enter the garment details manually.";
  }
 }catch(err){
  $("analysisMsg").textContent=err.message;
 }
}

async function startBatch(files){
 photoQueue=Array.from(files||[]);
 if(!photoQueue.length)return;
 batchMode=photoQueue.length>1;
 currentPhotoIndex=0;
 await handleGarmentPhoto(photoQueue[currentPhotoIndex]);
}

async function advanceBatch(){
 if(batchMode && currentPhotoIndex < photoQueue.length-1){
   currentPhotoIndex++;
   await handleGarmentPhoto(photoQueue[currentPhotoIndex]);
   return true;
 }
 photoQueue=[];
 currentPhotoIndex=-1;
 batchMode=false;
 updateBatchUI();
 return false;
}

async function importProductUrl(){const url=($("productUrl").value||"").trim(),status=$("urlImportStatus");if(!url)return alert("Paste a retailer product link first.");clearGarmentFields();photoQueue=[];currentPhotoIndex=-1;batchMode=false;status.textContent="Reading retailer page and preparing the garment…";$("importProductUrl").disabled=true;$("analysisMsg").classList.remove("hidden");$("analysisMsg").textContent="Importing product information…";try{const x=await api("/api/import-product-url",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({url})});uploadedPath=x.image_path||"";if(x.image_path){$("preview").src=x.image_path;$("preview").classList.remove("hidden");}else{$("preview").classList.add("hidden");}const a=x.analysis||{};Object.entries(a).forEach(([k,v])=>{if($(k)&&k!=="confidence")$(k).value=v||""});aiConfidence=Number(a.confidence||0);$("notes").value=[$("notes").value,`Product page: ${x.source_url}`].filter(Boolean).join("\n");status.textContent=x.image_available?"Imported. Check the details below before saving.":"Imported. No usable retailer image was exposed; save it now and add your own photo later if you want.";$("analysisMsg").textContent="Product page analysed. Please check the details before saving.";}catch(err){status.textContent=err.message;$("analysisMsg").textContent=err.message}finally{$("importProductUrl").disabled=false}}
$("importProductUrl").addEventListener("click",importProductUrl);$("productUrl").addEventListener("keydown",e=>{if(e.key==="Enter"){e.preventDefault();importProductUrl()}});const urlDropZone=$("urlDropZone");urlDropZone.addEventListener("dragover",e=>{e.preventDefault();urlDropZone.classList.add("dragging")});urlDropZone.addEventListener("dragleave",()=>urlDropZone.classList.remove("dragging"));urlDropZone.addEventListener("drop",e=>{e.preventDefault();urlDropZone.classList.remove("dragging");const raw=e.dataTransfer.getData("text/uri-list")||e.dataTransfer.getData("text/plain")||"";const url=raw.split(/\r?\n/).find(x=>/^https?:\/\//i.test(x.trim()))||raw.trim();if(url){$("productUrl").value=url;importProductUrl()}});
$("cameraPhoto").addEventListener("change",async e=>{
 photoQueue=[]; currentPhotoIndex=-1; batchMode=false;
 await handleGarmentPhoto(e.target.files[0]);
});
$("libraryPhoto").addEventListener("change",async e=>{await startBatch(e.target.files);});
$("skipGarment").addEventListener("click",async()=>{if(batchMode)await advanceBatch();});

$("saveGarment").addEventListener("click",async()=>{
 if(!uploadedPath && !$("productUrl").value.trim())return alert("Add a photo or import a product page first.");
 const ids=["category","garment_type","brand","model_line","labelled_size","colour","material","pattern","fit_cut","fit_feedback","season","formality","notes"];
 const fd=new FormData();
 fd.append("image_path",uploadedPath);
 ids.forEach(id=>fd.append(id,$(id).value));
 fd.append("ai_confidence",aiConfidence);
 const saved=await api("/api/garments",{method:"POST",body:fd});
 if($("brand").value.trim() && saved?.id){
  api(`/api/garments/${saved.id}/enrich`,{method:"POST"}).catch(()=>{});
 }
 await loadGarments();
 const moved=await advanceBatch();
 if(!moved){
   clearGarmentFields();
   $("cameraPhoto").value="";
   $("libraryPhoto").value="";
   $("productUrl").value="";$("urlImportStatus").textContent="";
   $("preview").classList.add("hidden");
   $("analysisMsg").classList.add("hidden");
   go("wardrobe");
 }
});


async function loadStyleLearning(){
 try{
  const x=await api("/api/style-learning");
  const ratings=x.ratings||{};
  const total=x.feedback_count||0;
  const brands=(x.perfect_fit_brands||[]).map(b=>`${esc(b.brand)} (${b.count})`).join(", ");
  const ratingText=total
    ? `Based on ${total} outfit rating${total===1?"":"s"}: ${["Love it","Like it","Not for me","Too smart","Too casual"].filter(k=>ratings[k]).map(k=>`${k}: ${ratings[k]}`).join(" · ")}`
    : "No outfit feedback yet.";
  const brandText=brands ? `<br><b>Perfect-fit brands:</b> ${brands}` : "";
  $("styleLearning").innerHTML=`<p>${ratingText}${brandText}</p><small>${esc(x.message||"")}</small>`;
 }catch{
  $("styleLearning").innerHTML="<small>Style learning data is temporarily unavailable.</small>";
 }
}


async function loadModelPhotos(){
 try{
  const photos=await api("/api/model-photos");
  $("modelPhotos").innerHTML=photos.length?photos.map(p=>`
    <div class="model-photo-card">
      <img src="${p.image_path}" alt="Saved reference photo">
      <button type="button" class="danger tiny-btn" onclick="deleteModelPhoto(${p.id})">Remove</button>
    </div>`).join(""):'<div class="empty-model">No reference photos yet.</div>';
 }catch(err){
  $("modelPhotos").innerHTML=`<small>${esc(err.message)}</small>`;
 }
}
async function deleteModelPhoto(id){
 if(!confirm("Remove this reference photo?"))return;
 await api(`/api/model-photos/${id}`,{method:"DELETE"});
 await loadModelPhotos();
}
const modelPhotoInput=$("modelPhotoInput");
if(modelPhotoInput){
 modelPhotoInput.addEventListener("change",async e=>{
  const files=Array.from(e.target.files||[]).slice(0,4);
  const status=$("modelUploadStatus");
  if(!files.length)return;

  status.classList.remove("hidden");
  status.textContent=`Preparing ${files.length} photo${files.length===1?"":"s"}…`;

  try{
   for(let i=0;i<files.length;i++){
    status.textContent=`Uploading photo ${i+1} of ${files.length}…`;
    const fd=new FormData();
    fd.append("file",files[i]);
    fd.append("label",`Reference ${i+1}`);
    await api("/api/model-photos",{method:"POST",body:fd});
   }

   modelPhotoInput.value="";
   await loadModelPhotos();
   status.textContent=`Uploaded ${files.length} reference photo${files.length===1?"":"s"} successfully.`;
   setTimeout(()=>status.classList.add("hidden"),2500);
  }catch(err){
   status.textContent=`Upload failed: ${err.message}`;
  }
 });
}
async function loadProfile(){
 const p=await api("/api/profile");Object.entries(p).forEach(([k,v])=>{if($(k)&&v!==null)$(k).value=v});if(p.name)$("greeting").textContent=`Good morning, ${p.name}`;
}
$("saveProfile").addEventListener("click",async()=>{
 const keys=["name","height_cm","chest_cm","waist_cm","hips_cm","thigh_cm","inseam_cm","sleeve_cm","neck_cm","preferred_fit","style_notes","brand_notes"],p={};
 keys.forEach(k=>{let v=$(k).value;p[k]=["height_cm","chest_cm","waist_cm","hips_cm","thigh_cm","inseam_cm","sleeve_cm","neck_cm"].includes(k)?(v?Number(v):null):v});
 await api("/api/profile",{method:"PUT",headers:{"Content-Type":"application/json"},body:JSON.stringify(p)});alert("Profile saved.");await loadProfile();
});

function populateV4Anchor(){
 const el=$("v4Anchor");
 if(!el)return;
 const current=el.value;
 el.innerHTML='<option value="">Let the stylist choose</option>'+
  garments.map(g=>`<option value="${g.id}">${esc((g.brand?g.brand+" ":"")+(g.garment_type||g.category||"Garment"))} — ${esc(g.colour||"")}</option>`).join("");
 if([...el.options].some(o=>o.value===current))el.value=current;
}

function setupV4Dictation(){
 const btn=$("v4Dictate"), field=$("v4Request"), status=$("dictationStatus");
 if(!btn||!field||!status)return;

 const Recognition=window.SpeechRecognition||window.webkitSpeechRecognition;
 if(!Recognition){
  btn.textContent="🎙️ Use keyboard mic";
  btn.addEventListener("click",()=>{
   field.focus();
   status.classList.remove("hidden");
   status.textContent="Use the microphone on your iPhone or Mac keyboard to dictate into this box.";
  });
  return;
 }

 let recognition=null;
 let listening=false;
 let dictationEnabled=false;
 let restartTimer=null;
 let sessionBase="";
 let committed="";

 function scheduleRestart(delay=250){
  clearTimeout(restartTimer);
  if(!dictationEnabled || document.hidden)return;
  restartTimer=setTimeout(()=>{
   if(dictationEnabled && !listening && !document.hidden){
    startRecognition();
   }
  },delay);
 }

 function startRecognition(){
  if(listening || !dictationEnabled || document.hidden)return;

  recognition=new Recognition();
  recognition.lang="en-GB";
  recognition.interimResults=true;
  recognition.continuous=true;

  sessionBase=field.value.trim();
  committed="";

  recognition.onstart=()=>{
   listening=true;
   btn.textContent="■ Stop";
   btn.classList.add("recording");
   status.classList.remove("hidden");
   status.textContent="Listening…";
  };

  recognition.onresult=e=>{
   let interim="";
   for(let i=e.resultIndex;i<e.results.length;i++){
    const t=e.results[i][0].transcript;
    if(e.results[i].isFinal){
     committed += (committed ? " " : "") + t.trim();
    }else{
     interim += (interim ? " " : "") + t.trim();
    }
   }

   const spoken=[committed,interim].filter(Boolean).join(" ").trim();
   field.value=[sessionBase,spoken].filter(Boolean).join(sessionBase&&spoken?" ":"");
   status.textContent=interim ? "Listening…" : "Listening…";
  };

  recognition.onerror=e=>{
   listening=false;
   btn.textContent="🎙️ Dictate";
   btn.classList.remove("recording");
   status.classList.remove("hidden");

   if(e.error==="not-allowed" || e.error==="service-not-allowed"){
    dictationEnabled=false;
    status.textContent="Microphone permission was not granted. You can still use the keyboard microphone.";
    return;
   }

   if(e.error==="no-speech"){
    status.textContent="Still listening…";
    scheduleRestart(150);
    return;
   }

   if(document.hidden){
    status.textContent="Dictation paused because this window lost focus. It will resume when you return.";
    return;
   }

   status.textContent="Dictation paused briefly. Resuming…";
   scheduleRestart(300);
  };

  recognition.onend=()=>{
   listening=false;
   btn.textContent=dictationEnabled?"■ Stop":"🎙️ Dictate";
   btn.classList.toggle("recording",dictationEnabled);

   if(dictationEnabled){
    if(document.hidden){
     status.classList.remove("hidden");
     status.textContent="Dictation paused because this window lost focus. It will resume when you return.";
    }else{
     status.classList.remove("hidden");
     status.textContent="Resuming dictation…";
     scheduleRestart(200);
    }
   }
  };

  try{
   recognition.start();
  }catch{
   listening=false;
   scheduleRestart(300);
  }
 }

 btn.addEventListener("click",()=>{
  if(dictationEnabled){
   dictationEnabled=false;
   clearTimeout(restartTimer);
   if(recognition && listening){
    try{recognition.stop()}catch{}
   }
   listening=false;
   btn.textContent="🎙️ Dictate";
   btn.classList.remove("recording");
   status.classList.remove("hidden");
   status.textContent="Dictation stopped.";
   return;
  }

  dictationEnabled=true;
  status.classList.remove("hidden");
  status.textContent="Starting dictation…";
  startRecognition();
 });

 document.addEventListener("visibilitychange",()=>{
  if(document.hidden){
   if(dictationEnabled){
    status.classList.remove("hidden");
    status.textContent="Dictation paused because this window lost focus. It will resume when you return.";
   }
  }else if(dictationEnabled && !listening){
   status.classList.remove("hidden");
   status.textContent="Resuming dictation…";
   scheduleRestart(250);
  }
 });
}

const v4VisualCache=new Map();
const sourcedProductContexts=new Map();

function renderV4Outfit(o,index){
 const pieces=(o.owned_garment_ids||[]).map(id=>garments.find(g=>g.id===id)).filter(Boolean);
 const pieceHtml=pieces.map(g=>`<div class="v4-piece">
  <img src="${g.image_path}" alt="">
  <div><b>${esc((g.brand?g.brand+" ":"")+(g.garment_type||g.category||"Garment"))}</b><small>${esc([g.colour,g.material,g.labelled_size].filter(Boolean).join(" · "))}</small></div>
 </div>`).join("");

 const gap=o.missing_piece?`<div class="v4-missing"><b>Suggested addition:</b> ${esc(o.missing_piece)}<br><small>${esc(o.missing_piece_reason||"")}</small></div>`:"";
 const payload=encodeURIComponent(JSON.stringify(o));

 setTimeout(()=>v4Visualise(payload,index,true),0);

 return `<div class="card v4-outfit">
  <div class="row between"><div><span class="rank-pill">#${o.rank}</span><h3>${esc(o.label)}</h3></div><div class="v4-score"><b>${o.score}</b><span>/100</span></div></div>
  ${pieceHtml}
  ${gap}
  <p><b>Why it works:</b> ${esc(o.why_it_works)}</p>
  <div class="v4-notes">
   <small><b>Occasion:</b> ${esc(o.occasion_fit)}</small>
   <small><b>Weather:</b> ${esc(o.weather_fit)}</small>
   <small><b>Formality:</b> ${esc(o.formality_fit)}</small>
   <small><b>Stylist note:</b> ${esc(o.style_note)}</small>
  </div>
  <div class="v4-actions">
   <button class="ghost" type="button" onclick="v4Visualise('${payload}',${index},false)">See on model</button>
   ${o.missing_piece?`<button class="ghost" type="button" onclick="v4FindPiece('${payload}',${index})">Find this piece</button>`:""}
  </div>
  <div id="v4Visual-${index}" class="model-visual"><div class="visual-loading">Creating your look…</div></div>
  <div id="v4Products-${index}" class="product-results"></div>
 </div>`;
}

async function v4Visualise(encoded,index,useMyLikeness){
 const o=JSON.parse(decodeURIComponent(encoded));
 const box=$(`v4Visual-${index}`);
 const cacheKey=JSON.stringify({
  ids:o.owned_garment_ids||[],
  label:o.label||"",
  extra:o.missing_piece||"",
  likeness:useMyLikeness
 });

 if(v4VisualCache.has(cacheKey)){
  const x=v4VisualCache.get(cacheKey);
  box.classList.remove("hidden");
  box.innerHTML=`<img src="${x.image_path}" alt="AI outfit visualisation"><div class="visual-caption"><b>${esc(x.label)}</b><br>${esc(x.notice)}</div>`;
  return;
 }

 box.classList.remove("hidden");
 box.innerHTML=`<div class="visual-loading">${useMyLikeness?"Creating your look…":"Creating outfit visual…"} this can take a little while.</div>`;

 try{
  const x=await api("/api/outfit-visualisation",{
   method:"POST",
   headers:{"Content-Type":"application/json"},
   body:JSON.stringify({
    garment_ids:o.owned_garment_ids||[],
    label:o.label||"Outfit",
    reason:o.why_it_works||"",
    occasion:o.occasion_fit||"",
    temperature_c:null,
    use_my_likeness:useMyLikeness,
    requested_extra_piece:o.missing_piece||""
   })
  });
  v4VisualCache.set(cacheKey,x);
  box.innerHTML=`<img src="${x.image_path}" alt="AI outfit visualisation"><div class="visual-caption"><b>${esc(x.label)}</b><br>${esc(x.notice)}</div>`;
 }catch(err){
  box.innerHTML=`<div class="notice">${esc(err.message)}</div>`;
 }
}

async function v4FindPiece(encoded,index){
 const o=JSON.parse(decodeURIComponent(encoded));
 const box=$(`v4Products-${index}`);

 box.innerHTML=`<div class="retailer-search-state">
   <span class="retailer-search-spinner"></span>
   <div>
    <b>Searching UK retailers…</b>
    <p>I’m checking current products, prices and fit information for <strong>${esc(o.missing_piece||"this piece")}</strong>.</p>
    <small>Please wait — a live retailer search can take a little while.</small>
   </div>
  </div>`;

 try{
  const x=await api("/api/source-products",{
   method:"POST",
   headers:{"Content-Type":"application/json"},
   body:JSON.stringify({
    search_phrase:o.missing_piece||"",
    shopping_spec:[o.missing_piece,o.missing_piece_reason,o.style_note].filter(Boolean).join(". "),
    budget:"",
    category:"",
    size_fit_guidance:"Use my saved profile and fit history where relevant."
   })
  });

  if(!(x.products||[]).length){
   box.innerHTML=`<div class="notice">I couldn't find a sufficiently reliable current match. ${esc(x.search_note||"")}</div>`;
   return;
  }

  sourcedProductContexts.set(index,o);

  // Render locally here so the retailer result path has no dependency on
  // a separate product-card renderer being present in the browser scope.
  const productCards=x.products.map((p,pi)=>{
   const url=safeProductUrl(p.url||"");
   const payload=encodeURIComponent(JSON.stringify(p));
   const thumbId=`productThumb-${index}-${pi}`;
   const image=p.image_url
    ? `<img id="${thumbId}" class="live-product-img live-product-thumb" src="${esc(p.image_url)}" alt="" onerror="productThumbUnavailable('${thumbId}')">`
    : `<div class="product-image-unavailable" id="${thumbId}"><span>Product image unavailable</span></div>`;

   setTimeout(()=>resolveProductThumbnail('${payload}','${thumbId}'),0);

   return `<div class="live-product-card selectable-product">${image}<div class="live-product-body">
    <div class="row between">
     <div><small>${esc(p.brand||p.retailer||"")}</small><h4>${esc(p.name||"Product")}</h4></div>
     <b>${esc(p.price||"Price check")}</b>
    </div>
    <p>${esc(p.why_it_matches||"")}</p>
    <div class="product-meta">${[p.colour,p.material,p.fit].filter(Boolean).map(esc).join(" · ")}</div>
    <small><b>Size:</b> ${esc(p.size_note||"Confirm sizing with retailer.")}</small>
    <div class="row between product-footer">
     <span class="confidence">${esc(p.confidence||"")} confidence</span>
     <div class="product-actions">
      <button class="primary try-product-btn" type="button" onclick="tryProductOnMe('${payload}',${index},${pi})">Try on me</button>
      <button class="ghost" type="button" onclick="saveToShortlist('${payload}',${index},this)">Save</button>
      <a class="ghost product-link" href="${url}" target="_blank" rel="noopener">View retailer</a>
     </div>
    </div>
    <div id="productTryOn-${index}-${pi}" class="product-tryon-result"></div>
   </div></div>`;
  }).join("");

  box.innerHTML=`<div class="retailer-search-complete">
    <span>Retailer search complete</span>
    <small>${esc(x.search_note||"Current matches found for your recommendation.")}</small>
   </div>${productCards}`;

 }catch(err){
  box.innerHTML=`<div class="notice"><b>Retailer search couldn't finish.</b><br>${esc(err.message)}</div>`;
 }
}


function safeProductUrl(url){
 try{
  const u=new URL(url);
  return (u.protocol==="https:"||u.protocol==="http:")?u.href:"#";
 }catch{return "#";}
}

function renderLiveProduct(p){
 const url=safeProductUrl(p.url||"");
 const image=p.image_url?`<img class="live-product-img" src="${esc(p.image_url)}" alt="" onerror="this.style.display='none'">`:"";
 return `<div class="live-product-card">${image}<div class="live-product-body">
  <div class="row between"><div><small>${esc(p.brand||p.retailer||"")}</small><h4>${esc(p.name||"Product")}</h4></div><b>${esc(p.price||"Price check")}</b></div>
  <p>${esc(p.why_it_matches||"")}</p>
  <div class="product-meta">${[p.colour,p.material,p.fit].filter(Boolean).map(esc).join(" · ")}</div>
  <small><b>Size:</b> ${esc(p.size_note||"Confirm sizing with retailer.")}</small>
  <div class="product-footer"><a class="ghost product-link" href="${url}" target="_blank" rel="noopener">View retailer</a></div>
 </div></div>`;
}

function renderLiveProductWithTryOn(p,contextIndex,productIndex){
 const url=safeProductUrl(p.url||"");
 const image=p.image_url?`<img class="live-product-img" src="${esc(p.image_url)}" alt="" onerror="this.style.display='none'">`:"";
 const payload=encodeURIComponent(JSON.stringify(p));
 return `<div class="live-product-card selectable-product">${image}<div class="live-product-body">
  <div class="row between"><div><small>${esc(p.brand||p.retailer||"")}</small><h4>${esc(p.name||"Product")}</h4></div><b>${esc(p.price||"Price check")}</b></div>
  <p>${esc(p.why_it_matches||"")}</p>
  <div class="product-meta">${[p.colour,p.material,p.fit].filter(Boolean).map(esc).join(" · ")}</div>
  <small><b>Size:</b> ${esc(p.size_note||"Confirm sizing with retailer.")}</small>
  <div class="row between product-footer"><span class="confidence">${esc(p.confidence||"")} confidence</span><div class="product-actions">
   <button class="primary try-product-btn" type="button" onclick="tryProductOnMe('${payload}',${contextIndex},${productIndex})">Try on me</button>
   <a class="ghost product-link" href="${url}" target="_blank" rel="noopener">View retailer</a>
  </div></div>
  <div id="productTryOn-${contextIndex}-${productIndex}" class="product-tryon-result"></div>
 </div></div>`;
}


async function resolveProductThumbnail(encoded,elementId){
 const p=JSON.parse(decodeURIComponent(encoded));
 const el=$(elementId);
 if(!el)return;
 if(el.tagName==="IMG" && el.complete && el.naturalWidth>0)return;
 try{
  const x=await api("/api/product-thumbnail",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({url:p.url||"",image_url:p.image_url||""})});
  if(!x.image_url){productThumbUnavailable(elementId);return;}
  if(el.tagName==="IMG"){el.onerror=()=>productThumbUnavailable(elementId);el.src=x.image_url;}
  else{
   const img=document.createElement("img");
   img.id=elementId;img.className="live-product-img live-product-thumb";img.alt="";img.src=x.image_url;
   img.onerror=()=>productThumbUnavailable(elementId);
   el.replaceWith(img);
  }
 }catch{productThumbUnavailable(elementId)}
}

function productThumbUnavailable(elementId){
 const el=$(elementId);
 if(!el || el.classList.contains("product-image-unavailable"))return;
 const note=document.createElement("div");
 note.id=elementId;note.className="product-image-unavailable";
 note.innerHTML="<span>Product image unavailable</span>";
 el.replaceWith(note);
}


async function saveToShortlist(encoded,contextIndex,button){
 const p=JSON.parse(decodeURIComponent(encoded));
 const context=sourcedProductContexts.get(contextIndex)||{};
 if(button){button.disabled=true;button.textContent="Saving…";}
 try{
  await api("/api/shopping-shortlist",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({product:p,context})});
  if(button)button.textContent="Saved ✓";
 }catch(err){if(button){button.disabled=false;button.textContent="Save";}alert(err.message);}
}

async function loadShortlist(){
 const box=$("shortlistResults");
 if(!box)return;
 box.innerHTML='<div class="card v4-thinking"><span class="spinner"></span> Loading shortlist…</div>';
 try{
  const items=await api("/api/shopping-shortlist");
  if(!items.length){box.innerHTML='<div class="notice">Nothing saved yet. Use <b>Find this piece</b> and press <b>Save</b> beside anything you want to compare.</div>';return;}
  box.innerHTML=`<div class="shortlist-compare-note"><b>${items.length} saved ${items.length===1?"piece":"pieces"}</b><span>Compare fit, material and price before deciding.</span></div>
  <div class="shortlist-grid">${items.map(renderShortlistItem).join("")}</div>`;
 }catch(err){box.innerHTML=`<div class="notice">${esc(err.message)}</div>`}
}

function renderShortlistItem(p){
 const url=safeProductUrl(p.url||"");
 const image=p.image_url?`<div class="shortlist-image"><img src="${esc(p.image_url)}" alt="" onerror="this.parentElement.innerHTML='<div class=\'shortlist-no-image compact\'>Product image unavailable</div>';this.parentElement.classList.add('compact')"></div>`:`<div class="shortlist-no-image compact">Product image unavailable</div>`;
 return `<article class="shortlist-card">${image}<div class="shortlist-body">
  <small>${esc(p.brand||p.retailer||"")}</small><h3>${esc(p.name||"Product")}</h3><strong>${esc(p.price||"Price check")}</strong>
  <p>${esc(p.why_it_matches||"")}</p>
  <dl class="compare-specs">
   <div><dt>Retailer</dt><dd>${esc(p.retailer||"—")}</dd></div><div><dt>Colour</dt><dd>${esc(p.colour||"—")}</dd></div>
   <div><dt>Material</dt><dd>${esc(p.material||"—")}</dd></div><div><dt>Fit</dt><dd>${esc(p.fit||"—")}</dd></div>
   <div><dt>Size guidance</dt><dd>${esc(p.size_note||"—")}</dd></div><div><dt>Confidence</dt><dd>${esc(p.confidence||"—")}</dd></div>
  </dl>
  <div class="shortlist-actions"><button class="primary" onclick="addShortlistToWardrobe(${p.id})">I bought this</button>
   <a class="ghost product-link" href="${url}" target="_blank" rel="noopener">View retailer</a>
   <button class="text-button danger-text" onclick="removeShortlist(${p.id})">Remove</button></div>
 </div></article>`;
}


async function addShortlistToWardrobe(id){
 const type=prompt("What should I call this in your wardrobe? (e.g. Unstructured blazer, Oxford shirt, suede loafer)","");
 if(type===null)return;
 const size=prompt("What labelled size did you buy?","");
 if(size===null)return;
 try{
  const x=await api(`/api/shopping-shortlist/${id}/add-to-wardrobe`,{
   method:"POST",headers:{"Content-Type":"application/json"},
   body:JSON.stringify({category:"Other",garment_type:type||"Purchased item",labelled_size:size||""})
  });
  alert("Added to your wardrobe. I’ve marked it as awaiting a real-world fit review.");
  go("wardrobe");
  await loadGarments();
 }catch(err){alert(err.message)}
}

async function removeShortlist(id){
 try{await api(`/api/shopping-shortlist/${id}`,{method:"DELETE"});loadShortlist();}catch(err){alert(err.message)}
}

async function tryProductOnMe(encoded,contextIndex,productIndex){
 const p=JSON.parse(decodeURIComponent(encoded));
 const context=sourcedProductContexts.get(contextIndex);
 const box=$(`productTryOn-${contextIndex}-${productIndex}`);
 if(!context){
  box.innerHTML='<div class="notice">Run this Stylist recommendation again so I have the outfit context.</div>'; return;
 }
 box.innerHTML='<div class="tryon-loading"><span class="spinner"></span><div><b>Creating your look with this product…</b><small>This can take a little while.</small></div></div>';
 try{
  const x=await api("/api/product-tryon",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({
   garment_ids:context.owned_garment_ids||[],
   product_name:p.name||"",product_brand:p.brand||"",product_retailer:p.retailer||"",
   product_image_url:p.image_url||"",product_description:p.why_it_matches||"",
   product_colour:p.colour||"",product_material:p.material||"",product_fit:p.fit||"",
   outfit_label:context.label||"Outfit",outfit_reason:context.why_it_works||"",use_my_likeness:true
  })});
  box.innerHTML=`<div class="model-visual product-tryon-visual"><img src="${x.image_path}" alt="AI try-on of selected product"><div class="visual-caption">${esc(x.notice)}</div></div>`;
 }catch(err){box.innerHTML=`<div class="notice">${esc(err.message)}</div>`}
}

const runStylistV4Btn=$("runStylistV4");
if(runStylistV4Btn){
 runStylistV4Btn.addEventListener("click",async()=>{
  const text=$("v4Request").value.trim();
  if(!text){alert("Tell me what you are dressing for.");return;}

  const box=$("v4Results");
  const controller=new AbortController();
  let statusTimer=null;
  let timeoutTimer=null;

  box.innerHTML='<div class="card v4-thinking"><span class="spinner"></span><div><b>Styling from your wardrobe…</b><small id="v4WaitNote">This usually takes under a minute.</small></div></div>';
  runStylistV4Btn.disabled=true;

  statusTimer=setTimeout(()=>{
   const note=$("v4WaitNote");
   if(note)note.textContent="Still working — this request is taking longer than usual.";
  },25000);

  timeoutTimer=setTimeout(()=>{
   controller.abort();
  },75000);

  try{
   const r=await fetch("/api/stylist-v4",{
    method:"POST",
    headers:{"Content-Type":"application/json"},
    body:JSON.stringify({
     request_text:text,
     anchor_garment_id:$("v4Anchor").value?Number($("v4Anchor").value):null,
     owned_only:$("v4Shopping").value==="owned",
     max_options:3
    }),
    signal:controller.signal
   });

   let payload=null;
   try{payload=await r.json()}catch{}

   if(!r.ok){
    const msg=payload?.detail||payload?.message||`Stylist request failed (${r.status}).`;
    throw new Error(msg);
   }

   const x=payload;
   box.innerHTML=`<div class="notice"><b>Stylist view:</b> ${esc(x.summary||"")}</div>`+
    (x.outfits||[]).map((o,i)=>renderV4Outfit(o,i)).join("");

   if(!(x.outfits||[]).length){
    box.innerHTML+='<div class="notice">The stylist completed the request but did not return any outfit options. Please try wording the request slightly differently.</div>';
   }
  }catch(err){
   if(err.name==="AbortError"){
    box.innerHTML='<div class="notice"><b>This is taking too long.</b> The request was stopped after 75 seconds so the app cannot sit spinning indefinitely. Please tap Style me to try again.</div>';
   }else{
    box.innerHTML=`<div class="notice">${esc(err.message||"Something went wrong.")}</div>`;
   }
  }finally{
   clearTimeout(statusTimer);
   clearTimeout(timeoutTimer);
   runStylistV4Btn.disabled=false;
  }
 });
}
setupV4Dictation();

init();


$("makePackingPlan")?.addEventListener("click",async()=>{
 const box=$("packingResults");
 box.innerHTML='<div class="card">Building the most useful capsule from your wardrobe…</div>';
 try{
  const payload={destination:$("pack_destination").value,days:Number($("pack_days").value||5),trip_type:$("pack_trip_type").value,weather:$("pack_weather").value,activities:$("pack_activities").value,dress_needs:$("pack_dress_needs").value,laundry:$("pack_laundry").value,shopping_allowed:$("pack_shopping").value==="Yes",notes:$("pack_notes").value};
  const x=await api("/api/help-me-pack",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify(payload)});
  const packed=(x.packing_list||[]).map(p=>{const g=garments.find(z=>z.id===p.garment_id);return g?`<div class="outfitPiece"><img src="${g.image_path}"><div><b>${esc((g.brand?g.brand+" ":"")+g.garment_type)}</b><small>${esc(p.why_pack)} · wear ~${p.wear_count}×</small></div></div>`:""}).join("");
  const days=(x.outfit_plan||[]).map(d=>`<div class="pack-day"><b>${esc(d.day)} — ${esc(d.occasion)}</b><div class="mini-strip">${(d.garment_ids||[]).map(id=>{const g=garments.find(z=>z.id===id);return g?`<div class="mini-garment"><img src="${g.image_path}"><span>${esc(g.garment_type)}</span></div>`:""}).join("")}</div><small>${esc(d.note)}</small></div>`).join("");
  const missing=(x.missing_items||[]).length?`<div class="notice"><b>Useful gaps:</b> ${x.missing_items.map(esc).join(" · ")}</div>`:"";
  box.innerHTML=`<div class="notice">${esc(x.summary)}</div><div class="card"><h3>Pack these</h3>${packed}</div><div class="card"><h3>Outfit plan</h3>${days}</div>${missing}<div class="card"><b>Packing tip</b><p>${esc(x.packing_tip)}</p></div>`;
 }catch(err){box.innerHTML=`<div class="card">${esc(err.message)}</div>`}
});
