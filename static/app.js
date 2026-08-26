
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
function go(id){document.querySelectorAll(".screen").forEach(x=>x.classList.remove("active"));$(id).classList.add("active");scrollTo(0,0);if(id==="wardrobe")loadGarments();if(id==="garmentdetail"&&detailGarmentId)loadGarmentDetail(detailGarmentId);if(id==="outfits")populateAnchor();if(id==="stylistv4")populateV4Anchor();if(id==="profile"){loadProfile();loadStyleLearning();loadModelPhotos()}}
document.addEventListener("click",e=>{const b=e.target.closest("[data-go]");if(b)go(b.dataset.go)});
async function init(){
 try{const h=await api("/api/health");$("status").textContent=h.ai_enabled?"AI stylist connected":"Working prototype · AI key not connected"}catch{$("status").textContent="App offline"}
 await loadGarments(); await loadProfile();
}
async function loadGarments(){
 garments=await api("/api/garments");$("count").textContent=`${garments.length} saved item${garments.length===1?"":"s"}`;
 const cats=[...new Set(garments.map(g=>g.category).filter(Boolean))].sort();$("filter").innerHTML='<option value="">All categories</option>'+cats.map(c=>`<option>${esc(c)}</option>`).join("");
 renderGarments();
}
function renderGarments(){
 const q=$("search").value.toLowerCase(),f=$("filter").value;
 const list=garments.filter(g=>(!f||g.category===f)&&(!q||JSON.stringify(g).toLowerCase().includes(q)));
 $("garments").innerHTML=list.length?list.map(g=>{
  const cleaning=cleanupInProgress.has(g.id);
  const label=esc((g.brand?g.brand+" ":"")+g.garment_type);
  return `<div class="garment${cleaning?" is-cleaning":""}"><div class="garment-photo-wrap"><img class="garment-photo" src="${g.image_path}" alt="${label}" onclick="openGarment(${g.id})" title="Open garment" onerror="this.classList.add('image-missing')">${cleaning?`<div class="cleanup-overlay" role="status" aria-live="polite"><span class="cleanup-spinner" aria-hidden="true"></span><b>Cleaning up photo…</b><small>Removing the background and preparing your catalogue image.</small></div>`:""}</div><div class="meta"><b>${label}</b><small>${esc([g.colour,g.material,g.labelled_size].filter(Boolean).join(" · "))}</small><div><span class="pill">${esc(g.fit_feedback||"Fit unknown")}</span></div><div class="row" style="margin-top:9px"><button class="secondary" onclick="buildAround(${g.id})" ${cleaning?"disabled":""}>Build around</button><button class="ghost" onclick="editGarment(${g.id})" ${cleaning?"disabled":""}>Edit</button><button class="ghost cleanup-btn" onclick="cleanupPhoto(${g.id})" ${cleaning?"disabled":""}>${cleaning?'<span class="inline-spinner" aria-hidden="true"></span> Cleaning…':'Clean up photo'}</button>${g.original_image_path&&g.image_path!==g.original_image_path?`<button class="ghost" onclick="restoreOriginal(${g.id})" ${cleaning?"disabled":""}>Original photo</button>`:""}<button class="danger" onclick="del(${g.id})" ${cleaning?"disabled":""}>Delete</button></div></div></div>`;
 }).join(""):'<div class="empty" style="grid-column:1/-1">No garments yet. Add your first real item.</div>';
}
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

$("cameraPhoto").addEventListener("change",async e=>{
 photoQueue=[]; currentPhotoIndex=-1; batchMode=false;
 await handleGarmentPhoto(e.target.files[0]);
});
$("libraryPhoto").addEventListener("change",async e=>{await startBatch(e.target.files);});
$("skipGarment").addEventListener("click",async()=>{if(batchMode)await advanceBatch();});

$("saveGarment").addEventListener("click",async()=>{
 if(!uploadedPath)return alert("Add a photo first.");
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
 box.innerHTML='<div class="product-loading">Searching current UK retailers…</div>';
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
  box.innerHTML=`<div class="live-search-note">${esc(x.search_note||"")}</div>`+
   x.products.map(renderLiveProduct).join("");
 }catch(err){
  box.innerHTML=`<div class="notice">${esc(err.message)}</div>`;
 }
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
