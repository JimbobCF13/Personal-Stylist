
const $=id=>document.getElementById(id);
let garments=[], uploadedPath="", aiConfidence=0;
let editingGarmentId=null;
let photoQueue=[], currentPhotoIndex=-1, batchMode=false;
const cleanupInProgress=new Set();

async function api(url,opts={}){
 const r=await fetch(url,opts); const data=await r.json().catch(()=>({}));
 if(!r.ok) throw new Error(data.detail||"Something went wrong");
 return data;
}
function go(id){document.querySelectorAll(".screen").forEach(x=>x.classList.remove("active"));$(id).classList.add("active");scrollTo(0,0);if(id==="wardrobe")loadGarments();if(id==="outfits")populateAnchor();if(id==="stylistv4")populateV4Anchor();if(id==="profile"){loadProfile();loadStyleLearning();loadModelPhotos()}}
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
  return `<div class="garment${cleaning?" is-cleaning":""}"><div class="garment-photo-wrap"><img class="garment-photo" src="${g.image_path}" alt="${label}" onclick="editGarment(${g.id})" title="Open garment" onerror="this.classList.add('image-missing')">${cleaning?`<div class="cleanup-overlay" role="status" aria-live="polite"><span class="cleanup-spinner" aria-hidden="true"></span><b>Cleaning up photo…</b><small>Removing the background and preparing your catalogue image.</small></div>`:""}</div><div class="meta"><b>${label}</b><small>${esc([g.colour,g.material,g.labelled_size].filter(Boolean).join(" · "))}</small><div><span class="pill">${esc(g.fit_feedback||"Fit unknown")}</span></div><div class="row" style="margin-top:9px"><button class="secondary" onclick="buildAround(${g.id})" ${cleaning?"disabled":""}>Build around</button><button class="ghost" onclick="editGarment(${g.id})" ${cleaning?"disabled":""}>Edit</button><button class="ghost cleanup-btn" onclick="cleanupPhoto(${g.id})" ${cleaning?"disabled":""}>${cleaning?'<span class="inline-spinner" aria-hidden="true"></span> Cleaning…':'Clean up photo'}</button>${g.original_image_path&&g.image_path!==g.original_image_path?`<button class="ghost" onclick="restoreOriginal(${g.id})" ${cleaning?"disabled":""}>Original photo</button>`:""}<button class="danger" onclick="del(${g.id})" ${cleaning?"disabled":""}>Delete</button></div></div></div>`;
 }).join(""):'<div class="empty" style="grid-column:1/-1">No garments yet. Add your first real item.</div>';
}
$("search").addEventListener("input",renderGarments);$("filter").addEventListener("change",renderGarments);


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
 editingGarmentId=null;
 await loadGarments();
 go("wardrobe");
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
 await api("/api/garments",{method:"POST",body:fd});
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

 let recognition=null, listening=false;

 btn.addEventListener("click",()=>{
  if(listening&&recognition){recognition.stop();return;}

  recognition=new Recognition();
  recognition.lang="en-GB";
  recognition.interimResults=true;
  recognition.continuous=false;

  const original=field.value.trim();
  let finalText="";

  recognition.onstart=()=>{
   listening=true;
   btn.textContent="■ Stop";
   btn.classList.add("recording");
   status.classList.remove("hidden");
   status.textContent="Listening… speak naturally.";
  };

  recognition.onresult=e=>{
   let interim="";
   for(let i=e.resultIndex;i<e.results.length;i++){
    const t=e.results[i][0].transcript;
    if(e.results[i].isFinal)finalText+=t;
    else interim+=t;
   }
   const spoken=(finalText||interim).trim();
   field.value=[original,spoken].filter(Boolean).join(original&&spoken?" ":"");
   status.textContent=interim?"Listening…":"Got it.";
  };

  recognition.onerror=e=>{
   status.classList.remove("hidden");
   status.textContent=e.error==="not-allowed"
    ?"Microphone permission was not granted. You can still use the keyboard microphone."
    :"Dictation stopped. Try again or use the keyboard microphone.";
  };

  recognition.onend=()=>{
   listening=false;
   btn.textContent="🎙️ Dictate";
   btn.classList.remove("recording");
  };

  try{recognition.start()}catch{}
 });
}

function renderV4Outfit(o){
 const pieces=(o.owned_garment_ids||[]).map(id=>garments.find(g=>g.id===id)).filter(Boolean);
 const pieceHtml=pieces.map(g=>`<div class="v4-piece">
  <img src="${g.image_path}" alt="">
  <div><b>${esc((g.brand?g.brand+" ":"")+(g.garment_type||g.category||"Garment"))}</b><small>${esc([g.colour,g.material,g.labelled_size].filter(Boolean).join(" · "))}</small></div>
 </div>`).join("");

 const gap=o.missing_piece?`<div class="v4-missing"><b>Suggested addition:</b> ${esc(o.missing_piece)}<br><small>${esc(o.missing_piece_reason||"")}</small></div>`:"";

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
 </div>`;
}

function populateAnchor(){$("anchor").innerHTML='<option value="">Let the stylist choose</option>'+garments.map(g=>`<option value="${g.id}">${esc((g.brand?g.brand+" ":"")+g.garment_type+" — "+(g.colour||""))}</option>`).join("")}
$("makeOutfits").addEventListener("click",async()=>{
 $("results").innerHTML='<div class="card">Stylist is thinking…</div>';
 try{
  const x=await api("/api/outfits",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({occasion:$("occasion").value,temperature_c:Number($("temperature").value),weather:$("weather").value,location:$("location").value,anchor_id:$("anchor").value?Number($("anchor").value):null,dress_code:$("dress_code").value,smartness:$("smartness").value,season:$("outfit_season").value,wardrobe_mode:$("wardrobe_mode").value,context_notes:$("context_notes").value})});
  $("results").innerHTML=`<div class="notice">${esc(x.summary)}</div>`+x.outfits.map((o,i)=>renderOutfit(o,i)).join("");
 }catch(err){$("results").innerHTML=`<div class="card">${esc(err.message)}</div>`}
});
function renderOutfit(o,i){
 const pieces=o.garment_ids.map(id=>garments.find(g=>g.id===id)).filter(Boolean).map(g=>`<div class="outfitPiece"><img src="${g.image_path}"><div><b>${esc((g.brand?g.brand+" ":"")+g.garment_type)}</b><small>${esc([g.colour,g.material,g.labelled_size].filter(Boolean).join(" · "))}</small></div></div>`).join("");
 const gap=o.missing_piece?`<div class="notice"><b>Potential wardrobe gap:</b> ${esc(o.missing_piece)} · shopping priority: ${esc(o.shopping_priority)}</div>`:"";
 const outfitPayload=encodeURIComponent(JSON.stringify(o));
 return `<div class="card outfit-card">
   <div class="row between"><h3 style="margin:0">${esc(o.label)}</h3><span class="pill">Wardrobe first</span></div>
   ${pieces}
   <p><b>Why it works:</b> ${esc(o.reason)}</p>
   <small>${esc(o.weather_note)} · ${esc(o.occasion_note)}</small>
   ${gap}
   <div class="visual-actions">
     <button class="primary" type="button" onclick="seeOnModel('${outfitPayload}',${i},false)">See on model</button>
     <button class="ghost" type="button" onclick="seeOnModel('${outfitPayload}',${i},true)">View on me</button>
   </div>
   <div id="modelVisual-${i}" class="model-visual hidden"></div>
   <div class="feedback">${["Love it","Like it","Not for me","Too smart","Too casual"].map(r=>`<button onclick='rate(${JSON.stringify(JSON.stringify(o))},${JSON.stringify(r)})'>${r}</button>`).join("")}</div>
 </div>`;
}

async function seeOnModel(encoded,index,useMyLikeness=false){
 const o=JSON.parse(decodeURIComponent(encoded));
 const box=$(`modelVisual-${index}`);
 box.classList.remove("hidden");
 box.innerHTML=`<div class="visual-loading">${useMyLikeness?"Creating your personalised outfit visualisation…":"Creating your outfit visualisation…"} this can take a little while.</div>`;

 try{
   const payload={
     garment_ids:o.garment_ids||[],
     label:o.label||"Outfit",
     reason:o.reason||"",
     occasion:$("occasion").value,
     temperature_c:Number($("temperature").value||0),
     use_my_likeness:useMyLikeness
   };
   const x=await api("/api/outfit-visualisation",{
     method:"POST",
     headers:{"Content-Type":"application/json"},
     body:JSON.stringify(payload)
   });
   box.innerHTML=`<img src="${x.image_path}" alt="AI model wearing a visualisation of the suggested outfit">
     <div class="visual-caption"><b>${esc(x.label)}</b><br>${esc(x.notice)}</div>`;
 }catch(err){
   box.innerHTML=`<div class="notice">${esc(err.message)}</div>`;
 }
}

async function rate(s,r){await api("/api/feedback",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({outfit:JSON.parse(s),rating:r})});alert("Feedback saved. The stylist will use repeated feedback patterns in future recommendations.");}
function esc(s){return String(s||"").replace(/[&<>"']/g,m=>({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[m]))}

$("analyseGaps").addEventListener("click",async()=>{
 const box=$("gapResults");
 box.innerHTML='<div class="card">Analysing your wardrobe and looking for the highest-value gaps…</div>';
 try{
  const x=await api("/api/wardrobe-gaps",{
   method:"POST",
   headers:{"Content-Type":"application/json"},
   body:JSON.stringify({
    goal:$("shopGoal").value,
    budget:$("shopBudget").value,
    occasion:$("shopOccasion").value,
    season:$("shopSeason").value,
    max_recommendations:4
   })
  });
  box.innerHTML=`<div class="notice"><b>Wardrobe analysis:</b> ${esc(x.summary)}</div>`+
   (x.recommendations||[]).map((r,i)=>renderGapRecommendation(r,i)).join("");
 }catch(err){
  box.innerHTML=`<div class="card">${esc(err.message)}</div>`;
 }
});

function garmentMini(id){
 const g=garments.find(x=>x.id===id);
 if(!g)return "";
 return `<div class="mini-garment"><img src="${g.image_path}" alt=""><span>${esc((g.brand?g.brand+" ":"")+(g.garment_type||g.category||"Garment"))}</span></div>`;
}


function synergyLabel(score){
 if(score>=75)return "Excellent";
 if(score>=55)return "Good";
 if(score>=35)return "Moderate";
 return "Low";
}

async function sourceProducts(serialised,index){
 const r=JSON.parse(serialised);
 const box=$(`productResults-${index}`);
 box.innerHTML='<div class="product-loading">Searching current UK retailers…</div>';
 try{
  const x=await api("/api/source-products",{
   method:"POST",
   headers:{"Content-Type":"application/json"},
   body:JSON.stringify({
    search_phrase:r.search_phrase||"",
    shopping_spec:r.shopping_spec||"",
    budget:$("shopBudget").value||"",
    category:r.category||"",
    size_fit_guidance:r.size_fit_guidance||""
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

function safeProductUrl(url){
 try{
  const u=new URL(url);
  return (u.protocol==="https:"||u.protocol==="http:")?u.href:"#";
 }catch{return "#";}
}

function renderLiveProduct(p){
 const url=safeProductUrl(p.url||"");
 const image=p.image_url?`<img class="live-product-img" src="${esc(p.image_url)}" alt="" onerror="this.style.display='none'">`:"";
 return `<div class="live-product-card">
   ${image}
   <div class="live-product-body">
    <div class="row between"><div><small>${esc(p.brand||p.retailer)}</small><h4>${esc(p.name)}</h4></div><b>${esc(p.price||"Price check")}</b></div>
    <p>${esc(p.why_it_matches)}</p>
    <div class="product-meta">${[p.colour,p.material,p.fit].filter(Boolean).map(esc).join(" · ")}</div>
    <small><b>Size:</b> ${esc(p.size_note||"Confirm sizing with retailer.")}</small>
    <div class="row between product-footer"><span class="confidence">${esc(p.confidence)} confidence</span><a class="primary product-link" href="${url}" target="_blank" rel="noopener">View at ${esc(p.retailer||"retailer")}</a></div>
   </div>
  </div>`;
}

function renderGapRecommendation(r,i){
 const owned=[...(r.owned_garment_ids||[])].slice(0,6).map(garmentMini).join("");
 const outfitIdeas=(r.outfit_ideas||[]).map(o=>{
   const imgs=(o.owned_garment_ids||[]).map(garmentMini).join("");
   return `<div class="shop-outfit"><div class="mini-row">${imgs}</div><small>${esc(o.description)}</small></div>`;
 }).join("");

 return `<div class="card gap-card">
   <div class="row between">
    <div><span class="priority ${esc(r.priority)}">${esc(r.priority)} priority</span><h3>${esc(r.title)}</h3></div>
    <div class="synergy"><b>${synergyLabel(Number(r.wardrobe_synergy_score||0))}</b><span>${Number(r.wardrobe_synergy_score||0)}/100</span></div>
   </div>
   <div class="spec-grid">
    <div><small>COLOUR</small><b>${esc(r.ideal_colour)}</b></div>
    <div><small>MATERIAL</small><b>${esc(r.ideal_material)}</b></div>
    <div><small>FIT</small><b>${esc(r.ideal_fit)}</b></div>
    <div><small>FORMALITY</small><b>${esc(r.formality)}</b></div>
   </div>
   <p><b>Why it adds value:</b> ${esc(r.why_this_adds_value)}</p>
   <p><b>Fit guidance:</b> ${esc(r.size_fit_guidance)}</p>
   ${owned?`<div><small>WORKS WITH ITEMS YOU OWN</small><div class="mini-row">${owned}</div></div>`:""}
   ${outfitIdeas?`<div class="shopping-outfits"><small>OUTFIT IDEAS</small>${outfitIdeas}</div>`:""}
   <div class="shopping-spec"><small>SHOPPING SPECIFICATION</small><p>${esc(r.shopping_spec)}</p></div>
   <div class="future-search"><b>Suggested retailer search:</b> ${esc(r.search_phrase)}</div>
   <button class="primary full source-products-btn" type="button" onclick='sourceProducts(${JSON.stringify(JSON.stringify(r))},${i})'>Find products to buy</button>
   <div id="productResults-${i}" class="product-results"></div>
  </div>`;
}


const runStylistV4Btn=$("runStylistV4");
if(runStylistV4Btn){
 runStylistV4Btn.addEventListener("click",async()=>{
  const text=$("v4Request").value.trim();
  if(!text){alert("Tell me what you are dressing for.");return;}

  const box=$("v4Results");
  box.innerHTML='<div class="card v4-thinking"><span class="spinner"></span> Styling from your wardrobe…</div>';
  runStylistV4Btn.disabled=true;

  try{
   const x=await api("/api/stylist-v4",{
    method:"POST",
    headers:{"Content-Type":"application/json"},
    body:JSON.stringify({
     request_text:text,
     anchor_garment_id:$("v4Anchor").value?Number($("v4Anchor").value):null,
     owned_only:$("v4Shopping").value==="owned",
     max_options:3
    })
   });
   box.innerHTML=`<div class="notice"><b>Stylist view:</b> ${esc(x.summary)}</div>`+
    (x.outfits||[]).map(renderV4Outfit).join("");
  }catch(err){
   box.innerHTML=`<div class="notice">${esc(err.message)}</div>`;
  }finally{
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
