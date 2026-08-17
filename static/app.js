
const $=id=>document.getElementById(id);
let garments=[], uploadedPath="", aiConfidence=0;

async function api(url,opts={}){
 const r=await fetch(url,opts); const data=await r.json().catch(()=>({}));
 if(!r.ok) throw new Error(data.detail||"Something went wrong");
 return data;
}
function go(id){document.querySelectorAll(".screen").forEach(x=>x.classList.remove("active"));$(id).classList.add("active");scrollTo(0,0);if(id==="wardrobe")loadGarments();if(id==="outfits")populateAnchor();if(id==="profile")loadProfile()}
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
 $("garments").innerHTML=list.length?list.map(g=>`<div class="garment"><img src="${g.image_path}"><div class="meta"><b>${esc((g.brand?g.brand+" ":"")+g.garment_type)}</b><small>${esc([g.colour,g.material,g.labelled_size].filter(Boolean).join(" · "))}</small><div><span class="pill">${esc(g.fit_feedback||"Fit unknown")}</span></div><div class="row" style="margin-top:9px"><button class="secondary" onclick="buildAround(${g.id})">Build around</button><button class="danger" onclick="del(${g.id})">Delete</button></div></div></div>`).join(""):'<div class="empty" style="grid-column:1/-1">No garments yet. Add your first real item.</div>';
}
$("search").addEventListener("input",renderGarments);$("filter").addEventListener("change",renderGarments);
async function del(id){if(confirm("Remove this garment?")){await api(`/api/garments/${id}`,{method:"DELETE"});await loadGarments()}}
function buildAround(id){go("outfits");$("anchor").value=String(id)}
$("photo").addEventListener("change",async e=>{
 const file=e.target.files[0];if(!file)return;
 $("preview").src=URL.createObjectURL(file);$("preview").classList.remove("hidden");
 const fd=new FormData();fd.append("file",file);$("analysisMsg").classList.remove("hidden");$("analysisMsg").textContent="Analysing garment…";
 try{
  const x=await api("/api/analyse-garment",{method:"POST",body:fd});uploadedPath=x.image_path;
  if(x.analysis){Object.entries(x.analysis).forEach(([k,v])=>{if($(k)&&k!=="confidence")$(k).value=v||""});aiConfidence=x.analysis.confidence||0;$("analysisMsg").textContent=`AI analysis complete (${Math.round(aiConfidence*100)}% confidence). Please check and correct anything before saving.`}
  else $("analysisMsg").textContent="Photo saved. AI is not connected yet, so enter the garment details manually.";
 }catch(err){$("analysisMsg").textContent=err.message}
});
$("saveGarment").addEventListener("click",async()=>{
 if(!uploadedPath)return alert("Add a photo first.");
 const ids=["category","garment_type","brand","model_line","labelled_size","colour","material","pattern","fit_cut","fit_feedback","season","formality","notes"];
 const fd=new FormData();fd.append("image_path",uploadedPath);ids.forEach(id=>fd.append(id,$(id).value));fd.append("ai_confidence",aiConfidence);
 await api("/api/garments",{method:"POST",body:fd});ids.forEach(id=>$(id).value=id==="fit_feedback"?"Unknown":"");$("photo").value="";$("preview").classList.add("hidden");$("analysisMsg").classList.add("hidden");uploadedPath="";aiConfidence=0;await loadGarments();go("wardrobe");
});
async function loadProfile(){
 const p=await api("/api/profile");Object.entries(p).forEach(([k,v])=>{if($(k)&&v!==null)$(k).value=v});if(p.name)$("greeting").textContent=`Good morning, ${p.name}`;
}
$("saveProfile").addEventListener("click",async()=>{
 const keys=["name","height_cm","chest_cm","waist_cm","hips_cm","thigh_cm","inseam_cm","sleeve_cm","neck_cm","preferred_fit","style_notes","brand_notes"],p={};
 keys.forEach(k=>{let v=$(k).value;p[k]=["height_cm","chest_cm","waist_cm","hips_cm","thigh_cm","inseam_cm","sleeve_cm","neck_cm"].includes(k)?(v?Number(v):null):v});
 await api("/api/profile",{method:"PUT",headers:{"Content-Type":"application/json"},body:JSON.stringify(p)});alert("Profile saved.");await loadProfile();
});
function populateAnchor(){$("anchor").innerHTML='<option value="">Let the stylist choose</option>'+garments.map(g=>`<option value="${g.id}">${esc((g.brand?g.brand+" ":"")+g.garment_type+" — "+(g.colour||""))}</option>`).join("")}
$("makeOutfits").addEventListener("click",async()=>{
 $("results").innerHTML='<div class="card">Stylist is thinking…</div>';
 try{
  const x=await api("/api/outfits",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({occasion:$("occasion").value,temperature_c:Number($("temperature").value),weather:$("weather").value,location:$("location").value,anchor_id:$("anchor").value?Number($("anchor").value):null})});
  $("results").innerHTML=`<div class="notice">${esc(x.summary)}</div>`+x.outfits.map((o,i)=>renderOutfit(o,i)).join("");
 }catch(err){$("results").innerHTML=`<div class="card">${esc(err.message)}</div>`}
});
function renderOutfit(o,i){
 const pieces=o.garment_ids.map(id=>garments.find(g=>g.id===id)).filter(Boolean).map(g=>`<div class="outfitPiece"><img src="${g.image_path}"><div><b>${esc((g.brand?g.brand+" ":"")+g.garment_type)}</b><small>${esc([g.colour,g.material,g.labelled_size].filter(Boolean).join(" · "))}</small></div></div>`).join("");
 const gap=o.missing_piece?`<div class="notice"><b>Potential wardrobe gap:</b> ${esc(o.missing_piece)} · shopping priority: ${esc(o.shopping_priority)}</div>`:"";
 return `<div class="card"><div class="row between"><h3 style="margin:0">${esc(o.label)}</h3><span class="pill">Wardrobe first</span></div>${pieces}<p><b>Why it works:</b> ${esc(o.reason)}</p><small>${esc(o.weather_note)} · ${esc(o.occasion_note)}</small>${gap}<div class="feedback">${["Love it","Like it","Not for me","Too smart","Too casual"].map(r=>`<button onclick='rate(${JSON.stringify(JSON.stringify(o))},${JSON.stringify(r)})'>${r}</button>`).join("")}</div></div>`;
}
async function rate(s,r){await api("/api/feedback",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({outfit:JSON.parse(s),rating:r})});alert("Feedback saved.");}
function esc(s){return String(s||"").replace(/[&<>"']/g,m=>({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[m]))}
init();
