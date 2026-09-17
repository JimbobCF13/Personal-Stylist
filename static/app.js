let currentGarmentDetail=null;

const $=id=>document.getElementById(id);
let garments=[], uploadedPath="", originalUploadedPath="", aiConfidence=0, importedProductSourceUrl="";
let addFlowHasUserPhoto=false;
let editingGarmentId=null;
let detailGarmentId=null;
let enrichmentPollTimer=null;
let photoQueue=[], currentPhotoIndex=-1, batchMode=false, analysisInProgress=false, garmentAnalysisController=null, previewObjectUrl="";
const cleanupInProgress=new Set();
let quickWardrobeItems=[];
let quickWardrobeRecognition=null;
let buildLookSelected=new Set();
let productLookContext=null;
let authState={user:null,bootstrap_available:false};
let garmentsLoadedAt=0;
const DATA_FRESH_MS=30000;
function userCacheKey(name){return `ghd.${authState.user?.id||"anon"}.${name}`;}
function readUserCache(name){
 try{return JSON.parse(localStorage.getItem(userCacheKey(name))||"null")}catch{return null}
}
function writeUserCache(name,value){
 try{localStorage.setItem(userCacheKey(name),JSON.stringify(value))}catch{}
}
function garmentThumbUrl(g){
 const version=encodeURIComponent(g?.image_path||g?.original_image_path||"");
 return `/api/garments/${g.id}/thumbnail?v=${version}`;
}





function esc(value){
 return String(value??"")
  .replaceAll("&","&amp;")
  .replaceAll("<","&lt;")
  .replaceAll(">","&gt;")
  .replaceAll('"',"&quot;")
  .replaceAll("'","&#039;");
}

async function api(url,opts={}){
 let r;
 try{
  r=await fetch(url,opts);
 }catch(err){
  throw new Error("Connection lost. Check your internet connection and try again.");
 }
 const data=await r.json().catch(()=>({}));
 if(r.status===401 && !url.startsWith("/api/auth/")){
  showAuthGate({bootstrap_available:false});
 }
 if(!r.ok){
  if(r.status===429)throw new Error(data.detail||"Too many attempts. Please wait a little and try again.");
  if(r.status===502 || r.status===503)throw new Error(data.detail||"That service is temporarily unavailable. Your saved data has not been changed.");
  throw new Error(data.detail||`Something went wrong (${r.status}).`);
 }
 return data;
}

let activeAiDictation=null;
let currentPackingPlan=null;
let currentPackingRequest=null;
let currentSavedTripId=null;
let currentTripChecklist={};
let currentTripContext=null;
const appActivities=new Map();
const packingVisualCache=new Map();
let lastPackingBriefParsed="";
const backgroundVisualQueue=[];
const backgroundVisualKeys=new Set();
let backgroundVisualActive=0;
const BACKGROUND_VISUAL_CONCURRENCY=2;

function enqueueBackgroundVisual(key,task){
 if(backgroundVisualKeys.has(key))return;
 backgroundVisualKeys.add(key);
 backgroundVisualQueue.push({key,task});
 runBackgroundVisualQueue();
}
function runBackgroundVisualQueue(){
 while(backgroundVisualActive<BACKGROUND_VISUAL_CONCURRENCY && backgroundVisualQueue.length){
  const item=backgroundVisualQueue.shift();
  backgroundVisualActive++;
  Promise.resolve().then(item.task).catch(()=>{}).finally(()=>{
   backgroundVisualActive--;
   backgroundVisualKeys.delete(item.key);
   runBackgroundVisualQueue();
  });
 }
}

function renderAppActivity(){
 const overlay=$("appActivityOverlay");
 if(!overlay)return;
 const values=[...appActivities.values()];
 if(!values.length){overlay.classList.add("hidden");return;}
 const item=values[values.length-1];
 overlay.classList.remove("hidden");
 $("activityTitle").textContent=item.title||"Working…";
 $("activityDetail").textContent=item.detail||"Please wait a moment.";
 $("activityVisual").className=`activity-visual ${item.mode||"working"}`;
 const actions=$("activityDictationActions");
 if(actions){
  const listening=item.mode==="listening";
  actions.classList.toggle("hidden",!listening);
  actions.setAttribute("aria-hidden",listening?"false":"true");
 }
}
function beginAppActivity(key,title,detail="",mode="working"){
 appActivities.delete(key);
 appActivities.set(key,{title,detail,mode});
 renderAppActivity();
}
function updateAppActivity(key,title,detail="",mode=null){
 if(!appActivities.has(key))return;
 const old=appActivities.get(key);
 appActivities.set(key,{title:title||old.title,detail:detail||old.detail,mode:mode||old.mode});
 renderAppActivity();
}
function endAppActivity(key){
 appActivities.delete(key);
 renderAppActivity();
}

$("activityStopDictation")?.addEventListener("click",()=>{
 const rec=activeAiDictation?.recorder;
 if(rec?.state==="recording"){
  activeAiDictation.cancelled=false;
  try{rec.stop()}catch{}
 }
});

function cancelActiveDictation(){
 const active=activeAiDictation;
 if(!active)return;
 active.cancelled=true;
 const rec=active.recorder;
 if(rec?.state==="recording"){
  try{rec.stop()}catch{}
 }else{
  try{active.stream?.getTracks()?.forEach(t=>t.stop())}catch{}
  activeAiDictation=null;
  endAppActivity("dictation");
 }
}

$("activityCancelDictation")?.addEventListener("click",cancelActiveDictation);

function dictationMimeType(){
 const candidates=[
  "audio/webm;codecs=opus",
  "audio/mp4",
  "audio/webm",
  "audio/ogg;codecs=opus"
 ];
 if(!window.MediaRecorder)return "";
 for(const type of candidates){
  try{
   if(!MediaRecorder.isTypeSupported || MediaRecorder.isTypeSupported(type))return type;
  }catch{}
 }
 return "";
}

function dictationFilename(type){
 if((type||"").includes("mp4"))return "dictation.mp4";
 if((type||"").includes("ogg"))return "dictation.ogg";
 return "dictation.webm";
}

function appendDictationText(field,text){
 const spoken=String(text||"").trim();
 if(!spoken)return;
 const existing=String(field.value||"").trim();
 field.value=[existing,spoken].filter(Boolean).join(existing&&spoken?" ":"");
 field.dispatchEvent(new Event("input",{bubbles:true}));
 field.focus();
}

async function toggleAiDictation(button,field,status){
 if(!button||!field||!status)return;

 if(activeAiDictation){
  if(activeAiDictation.button===button){
   try{activeAiDictation.recorder.stop()}catch{}
   return;
  }
  status.classList.remove("hidden");
  status.textContent="Another dictation is recording. Stop that one first.";
  return;
 }

 if(!navigator.mediaDevices?.getUserMedia || !window.MediaRecorder){
  field.focus();
  status.classList.remove("hidden");
  status.textContent="Browser recording is unavailable here. You can still use the microphone on your phone or Mac keyboard.";
  return;
 }

 let stream;
 try{
  stream=await navigator.mediaDevices.getUserMedia({
   audio:{echoCancellation:true,noiseSuppression:true,autoGainControl:true}
  });
 }catch(err){
  field.focus();
  status.classList.remove("hidden");
  status.textContent="Microphone access wasn't available. Check browser permission, or use the keyboard microphone.";
  return;
 }

 const mime=dictationMimeType();
 let recorder;
 try{
  recorder=mime?new MediaRecorder(stream,{mimeType:mime}):new MediaRecorder(stream);
 }catch(err){
  stream.getTracks().forEach(t=>t.stop());
  status.classList.remove("hidden");
  status.textContent="I couldn't start the microphone recorder in this browser.";
  return;
 }

 const chunks=[];
 let safetyTimer=null;
 activeAiDictation={button,field,status,recorder,stream,cancelled:false,kind:"plain"};

 recorder.ondataavailable=e=>{
  if(e.data && e.data.size)chunks.push(e.data);
 };

 recorder.onerror=()=>{
  endAppActivity("dictation");
  status.classList.remove("hidden");
  status.textContent="Recording stopped unexpectedly. Please try again.";
 };

 recorder.onstart=()=>{
  button.textContent="■ Stop";
  button.classList.add("recording");
  status.classList.remove("hidden");
  status.textContent="Listening… tap Stop when you've finished.";
  beginAppActivity("dictation","Listening…","Speak naturally, then press the large Stop dictation button below.","listening");
  
  safetyTimer=setTimeout(()=>{
   if(recorder.state==="recording"){
    try{recorder.stop()}catch{}
   }
  },90000);
 };

 recorder.onstop=async()=>{
  clearTimeout(safetyTimer);
  stream.getTracks().forEach(t=>t.stop());
  const active=activeAiDictation?.recorder===recorder?activeAiDictation:null;
  const cancelled=Boolean(active?.cancelled);
  if(active)activeAiDictation=null;

  button.classList.remove("recording");
  status.classList.remove("hidden");

  if(cancelled){
   endAppActivity("dictation");
   button.disabled=false;
   button.textContent="🎙️ Dictate";
   status.textContent="Dictation cancelled — nothing was added.";
   return;
  }

  updateAppActivity("dictation","Transcribing…","Turning your recording into text.","transcribing");
  button.disabled=true;
  button.textContent="Transcribing…";
  status.textContent="Turning your recording into text…";

  try{
   const contentType=recorder.mimeType||mime||"audio/webm";
   const blob=new Blob(chunks,{type:contentType});
   if(!blob.size)throw new Error("No speech was recorded.");

   const fd=new FormData();
   fd.append("file",blob,dictationFilename(contentType));
   const x=await api("/api/transcribe-audio",{method:"POST",body:fd});
   appendDictationText(field,x.text);
   status.textContent="Dictation added. You can edit the text before continuing.";
  }catch(err){
   status.textContent=`Dictation couldn't be transcribed: ${err.message}`;
  }finally{
   endAppActivity("dictation");
   button.disabled=false;
   button.textContent="🎙️ Dictate";
  }
 };

 try{
  recorder.start(300);
 }catch(err){
  stream.getTracks().forEach(t=>t.stop());
  activeAiDictation=null;
  endAppActivity("dictation");
  button.classList.remove("recording");
  button.textContent="🎙️ Dictate";
  status.classList.remove("hidden");
  status.textContent="I couldn't start recording. Please try again.";
 }
}

function setupAiDictation(buttonId,fieldId,statusId){
 const button=$(buttonId),field=$(fieldId),status=$(statusId);
 if(!button||!field||!status)return;
 button.addEventListener("click",()=>toggleAiDictation(button,field,status));
}


function setFieldValue(id,value){
 const el=$(id);
 if(!el || value===undefined || value===null || String(value).trim()==="")return false;
 const v=String(value).trim();

 if(el.tagName==="SELECT"){
  const options=[...el.options];
  const exact=options.find(o=>o.value.toLowerCase()===v.toLowerCase() || o.textContent.trim().toLowerCase()===v.toLowerCase());
  if(exact)el.value=exact.value;
  else return false;
 }else if(el.type==="number"){
  const n=v.replace(/[^\d.-]/g,"");
  if(!n)return false;
  el.value=n;
 }else{
  el.value=v;
 }
 el.dispatchEvent(new Event("input",{bubbles:true}));
 el.dispatchEvent(new Event("change",{bubbles:true}));
 return true;
}

const SMART_DICTATION_MAPS={
 packing:{
  destination:"pack_destination",start_date:"pack_start_date",end_date:"pack_end_date",
  days:"pack_days",trip_type:"pack_trip_type",weather:"pack_weather",
  activities:"pack_activities",dress_needs:"pack_dress_needs",laundry:"pack_laundry",
  shopping_allowed:"pack_shopping",notes:"pack_notes"
 },
 stylist:{
  request_text:"v4Request",location:"v4Location",when:"v4When",shopping:"v4Shopping"
 },
 outfit:{
  occasion:"occasion",dress_code:"dress_code",smartness:"smartness",season:"outfit_season",
  temperature:"temperature",weather:"weather",location:"location",
  wardrobe_mode:"wardrobe_mode",context_notes:"context_notes"
 },
 shopping:{
  goal:"shopGoal",budget:"shopBudget",season:"shopSeason",occasion:"shopOccasion",shopping_mode:"shopMode"
 },
 profile:{
  name:"name",height_cm:"height_cm",chest_cm:"chest_cm",waist_cm:"waist_cm",
  hips_cm:"hips_cm",thigh_cm:"thigh_cm",inseam_cm:"inseam_cm",sleeve_cm:"sleeve_cm",
  neck_cm:"neck_cm",preferred_fit:"preferred_fit",style_notes:"style_notes",brand_notes:"brand_notes",
  usual_top_size:"usual_top_size",usual_bottom_size:"usual_bottom_size",usual_dress_size:"usual_dress_size",
  usual_shoe_size:"usual_shoe_size",bra_size:"bra_size",preferred_rise:"preferred_rise",
  preferred_hem_length:"preferred_hem_length",heel_preference:"heel_preference",accessory_notes:"accessory_notes"
 },
 garment:{
  category:"category",garment_type:"garment_type",brand:"brand",model_line:"model_line",
  labelled_size:"labelled_size",colour:"colour",material:"material",pattern:"pattern",
  fit_cut:"fit_cut",fit_feedback:"fit_feedback",season:"season",formality:"formality",notes:"notes"
 }
};

function normaliseSmartValue(mode,field,value){
 if(mode==="packing" && field==="shopping_allowed"){
  return /^yes|true|allow|open/i.test(String(value))?"Yes":"No";
 }
 if(mode==="stylist" && field==="shopping"){
  return String(value).toLowerCase().includes("owned")?"owned":"open";
 }
 return value;
}

async function applySmartTranscript(mode,transcript,status){
 updateAppActivity("dictation","Understanding your brief…","Filling the relevant fields for you.","working");
 try{
  if(mode==="packing" && $("pack_brief")){
   $("pack_brief").value=String(transcript||"").trim();
   $("pack_brief").dispatchEvent(new Event("input",{bubbles:true}));
  }
  if(mode==="outfit" && $("outfit_brief")){
   $("outfit_brief").value=String(transcript||"").trim();
   $("outfit_brief").dispatchEvent(new Event("input",{bubbles:true}));
  }
  const parsed=await api("/api/voice-form/parse",{
   method:"POST",headers:{"Content-Type":"application/json"},
   body:JSON.stringify({mode,transcript})
  });
  const map=SMART_DICTATION_MAPS[mode]||{};
  let applied=0;
  for(const item of parsed.fields||[]){
   const id=map[item.field];
   if(id && setFieldValue(id,normaliseSmartValue(mode,item.field,item.value)))applied++;
  }
  if(mode==="packing" && typeof packingDateSync==="function"){
   packingDateSync();
   lastPackingBriefParsed=($("pack_brief")?.value||"").trim();
  }

  status.classList.remove("hidden");
  status.innerHTML=`<b>Voice brief added.</b> ${esc(parsed.summary||"")} <small>${applied} field${applied===1?"":"s"} filled — check anything you want before continuing.</small>`;
 }catch(err){
  status.classList.remove("hidden");
  status.textContent=`I couldn't organise that brief: ${err.message}`;
 }finally{
  endAppActivity("dictation");
 }
}

async function toggleSmartDictation(button,status,mode){
 if(!button||!status)return;

 if(activeAiDictation){
  if(activeAiDictation.button===button){
   try{activeAiDictation.recorder.stop()}catch{}
   return;
  }
  status.classList.remove("hidden");
  status.textContent="Another dictation is already recording. Stop that one first.";
  return;
 }

 if(!navigator.mediaDevices?.getUserMedia || !window.MediaRecorder){
  status.classList.remove("hidden");
  status.textContent="Browser recording is unavailable here. You can still use your device keyboard microphone.";
  return;
 }

 let stream;
 try{
  stream=await navigator.mediaDevices.getUserMedia({audio:{echoCancellation:true,noiseSuppression:true,autoGainControl:true}});
 }catch{
  status.classList.remove("hidden");
  status.textContent="Microphone access wasn't available. Check browser permission.";
  return;
 }

 const mime=dictationMimeType();
 let recorder;
 try{
  recorder=mime?new MediaRecorder(stream,{mimeType:mime}):new MediaRecorder(stream);
 }catch{
  stream.getTracks().forEach(t=>t.stop());
  status.classList.remove("hidden");
  status.textContent="I couldn't start the microphone recorder.";
  return;
 }

 const chunks=[];
 let timer=null;
 activeAiDictation={button,status,recorder,stream,cancelled:false,kind:"smart"};

 recorder.ondataavailable=e=>{if(e.data&&e.data.size)chunks.push(e.data)};
 recorder.onstart=()=>{
  button.textContent="■ Stop";
  button.classList.add("recording");
  status.classList.remove("hidden");
  status.textContent="Listening… tell me everything in one go.";
  beginAppActivity("dictation","Listening…","Tell me the whole brief naturally, then press Stop dictation below.","listening");
  
  timer=setTimeout(()=>{if(recorder.state==="recording")try{recorder.stop()}catch{}},90000);
 };
 recorder.onerror=()=>{
  endAppActivity("dictation");
  status.textContent="Recording stopped unexpectedly. Please try again.";
 };
 recorder.onstop=async()=>{
  clearTimeout(timer);
  stream.getTracks().forEach(t=>t.stop());
  const active=activeAiDictation?.recorder===recorder?activeAiDictation:null;
  const cancelled=Boolean(active?.cancelled);
  if(active)activeAiDictation=null;
  button.classList.remove("recording");
  status.classList.remove("hidden");

  if(cancelled){
   endAppActivity("dictation");
   button.disabled=false;
   button.textContent=button.dataset.smartLabel||"🎙️ Dictate everything";
   status.textContent="Dictation cancelled — nothing was added.";
   return;
  }

  button.disabled=true;
  button.textContent="Understanding…";
  updateAppActivity("dictation","Transcribing…","Then I’ll organise the information into the form.","transcribing");

  try{
   const contentType=recorder.mimeType||mime||"audio/webm";
   const blob=new Blob(chunks,{type:contentType});
   if(!blob.size)throw new Error("No speech was recorded.");
   const fd=new FormData();
   fd.append("file",blob,dictationFilename(contentType));
   const x=await api("/api/transcribe-audio",{method:"POST",body:fd});
   await applySmartTranscript(mode,x.text,status);
  }catch(err){
   endAppActivity("dictation");
   status.textContent=`Dictation couldn't be processed: ${err.message}`;
  }finally{
   button.disabled=false;
   button.textContent=button.dataset.smartLabel||"🎙️ Dictate everything";
  }
 };
 button.dataset.smartLabel=button.textContent;
 try{recorder.start(300)}
 catch{
  stream.getTracks().forEach(t=>t.stop());
  activeAiDictation=null;
  endAppActivity("dictation");
  status.textContent="I couldn't start recording. Please try again.";
 }
}

function setupSmartDictation(buttonId,statusId,mode){
 const button=$(buttonId),status=$(statusId);
 if(!button||!status)return;
 button.addEventListener("click",()=>toggleSmartDictation(button,status,mode));
}


function setAuthMessage(text){
 const box=$("authMessage");
 if(!box)return;
 box.textContent=text||"";
 box.classList.toggle("hidden",!text);
}
function showAuthGate(status={}){
 authState.bootstrap_available=Boolean(status.bootstrap_available);
 $("authGate")?.classList.remove("hidden");
 document.body.classList.add("auth-locked");
 if($("authInviteLabel"))$("authInviteLabel").classList.toggle("hidden",authState.bootstrap_available);
 if($("authBootstrapNote"))$("authBootstrapNote").textContent=authState.bootstrap_available
   ?"First account becomes the owner/admin and keeps the existing wardrobe already on this app."
   :"Registration is invite-only during testing.";
}
function hideAuthGate(){
 $("authGate")?.classList.add("hidden");
 document.body.classList.remove("auth-locked");
}
function selectAuthTab(which){
 const login=which==="login";
 $("authLoginTab")?.classList.toggle("active",login);
 $("authRegisterTab")?.classList.toggle("active",!login);
 $("authLoginPane")?.classList.toggle("hidden",!login);
 $("authRegisterPane")?.classList.toggle("hidden",login);
 setAuthMessage("");
}
$("authLoginTab")?.addEventListener("click",()=>selectAuthTab("login"));
$("authRegisterTab")?.addEventListener("click",()=>selectAuthTab("register"));

async function loginAccount(){
 const btn=$("authLoginBtn");
 btn.disabled=true;btn.textContent="Signing in…";setAuthMessage("");
 try{
  const x=await api("/api/auth/login",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({
   email:$("authLoginEmail").value.trim(),password:$("authLoginPassword").value
  })});
  authState.user=x.user;
  location.reload();
 }catch(err){setAuthMessage(err.message)}
 finally{btn.disabled=false;btn.textContent="Sign in"}
}
$("authLoginBtn")?.addEventListener("click",loginAccount);

async function registerAccount(){
 const btn=$("authRegisterBtn");
 btn.disabled=true;btn.textContent="Creating account…";setAuthMessage("");
 try{
  const x=await api("/api/auth/register",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({
   display_name:$("authRegisterName").value.trim(),
   email:$("authRegisterEmail").value.trim(),
   password:$("authRegisterPassword").value,
   invite_code:$("authInviteCode").value.trim(),
   styling_profile:$("authStylingProfile").value||"menswear"
  })});
  authState.user=x.user;
  try{localStorage.setItem(`ghd.${x.user.id}.onboarding.pending`,"1")}catch{}
  location.reload();
 }catch(err){setAuthMessage(err.message)}
 finally{btn.disabled=false;btn.textContent="Create account"}
}
$("authRegisterBtn")?.addEventListener("click",registerAccount);

async function loadAccount(){
 if(!authState.user)return;
 const u=authState.user;
 applyStylingProfileUI(u);
 $("accountName").textContent=u.display_name||"Account";
 $("accountEmail").textContent=u.email||"";
 $("accountRole").textContent=u.role==="admin"?"Owner / Admin":"Tester";
 $("accountStylingProfile").textContent=(u.styling_profile||"menswear")==="womenswear"?"Womenswear":"Menswear";
 $("accountInitial").textContent=(u.display_name||"G").trim().charAt(0).toUpperCase();
 $("adminInviteCard").classList.toggle("hidden",u.role!=="admin");
 $("adminUsersCard").classList.toggle("hidden",u.role!=="admin");
 $("adminFeedbackCard").classList.toggle("hidden",u.role!=="admin");
 $("adminSystemCard").classList.toggle("hidden",u.role!=="admin");
 if(u.role==="admin")await Promise.all([loadInvites(),loadAdminUsers(),loadAdminFeedback(),loadSystemStatus()]);
}

function formatLastActive(value){
 if(!value)return "Never";
 try{return new Date(value).toLocaleString()}catch{return value}
}
async function loadAdminUsers(){
 const box=$("adminUsersResults"); if(!box)return;
 box.innerHTML='<div class="visual-loading">Loading testers…</div>';
 try{
  const rows=await api("/api/admin/users");
  box.innerHTML=rows.map(u=>`<div class="admin-user-row ${u.active===0?"disabled-user":""}">
   <div class="admin-user-main"><div class="admin-user-avatar">${esc((u.display_name||"U").charAt(0).toUpperCase())}</div><div><b>${esc(u.display_name||"User")}</b><small>${esc(u.email||"")}</small><span>${u.role==="admin"?"Owner / Admin":"Tester"} · ${u.active===0?"Disabled":"Active"}</span></div></div>
   <div class="admin-user-stats"><span><b>${u.wardrobe_items||0}</b> wardrobe</span><span><b>${u.saved_looks||0}</b> saved looks</span><span><b>${u.fit_reviews||0}</b> fit reviews</span><span><b>${u.feedback_count||0}</b> feedback</span></div>
   <div class="admin-user-meta"><small>Joined ${formatLastActive(u.created_at)}</small><small>Last session ${formatLastActive(u.last_session_at)}</small></div>
   ${u.role!=="admin"?`<button class="${u.active===0?"primary":"ghost"} admin-user-toggle" type="button" onclick="toggleTesterAccess(${u.id},${u.active===0?"true":"false"},this)">${u.active===0?"Re-enable tester":"Disable access"}</button>`:""}
  </div>`).join("");
 }catch(err){box.innerHTML=`<div class="notice">${esc(err.message)}</div>`}
}
async function toggleTesterAccess(id,enable,button){
 if(!enable && !confirm("Disable this tester's access? Their wardrobe and data will be kept."))return;
 button.disabled=true;
 try{await api(`/api/admin/users/${id}/${enable?"enable":"disable"}`,{method:"POST"});await loadAdminUsers()}
 catch(err){alert(err.message);button.disabled=false}
}
$("refreshAdminUsers")?.addEventListener("click",loadAdminUsers);

async function loadSystemStatus(){
 const box=$("systemStatusResults"); if(!box)return;
 box.innerHTML='<div class="visual-loading">Running checks…</div>';
 try{
  const x=await api("/api/admin/system-status");
  const checks=[
   ["Storage",x.storage_writable,"Writable","Problem"],
   ["Wardrobe images",Number(x.missing_image_items||0)===0,`${x.wardrobe_items||0} items OK`,`${x.missing_image_items} item(s) need attention`],
   ["AI stylist",x.ai_enabled,"Connected","Not configured"],
   ["Photo cleanup",x.photo_cleanup_enabled,"Connected","Not configured"]
  ];
  box.innerHTML=`<div class="system-check-grid">${checks.map(([label,ok,good,bad])=>`
   <div class="system-check ${ok?"ok":"warn"}"><span>${ok?"✓":"!"}</span><div><b>${esc(label)}</b><small>${esc(ok?good:bad)}</small></div></div>`).join("")}</div>`;
 }catch(err){box.innerHTML=`<div class="notice">${esc(err.message)}</div>`}
}
$("refreshSystemStatus")?.addEventListener("click",loadSystemStatus);

async function loadAdminFeedback(){
 const box=$("adminFeedbackResults"); if(!box)return;
 box.innerHTML='<div class="visual-loading">Loading feedback…</div>';
 try{
  const rows=await api("/api/admin/feedback");
  box.innerHTML=rows.length?rows.map(f=>`<div class="admin-feedback-row"><div class="row between"><b>${esc(f.display_name||"Tester")}</b><span>${f.rating?`${f.rating}/5`:"No rating"}</span></div><small>${esc(f.category||"general")} · ${formatLastActive(f.created_at)}</small><p>${esc(f.message||"")}</p></div>`).join(""):'<p class="muted-copy">No tester feedback yet.</p>';
 }catch(err){box.innerHTML=`<div class="notice">${esc(err.message)}</div>`}
}
$("refreshAdminFeedback")?.addEventListener("click",loadAdminFeedback);

$("sendTesterFeedback")?.addEventListener("click",async()=>{
 const btn=$("sendTesterFeedback"),status=$("testerFeedbackStatus"),message=$("testerFeedbackMessage").value.trim();
 if(!message){status.textContent="Add a feedback note first.";return}
 btn.disabled=true;btn.textContent="Sending…";status.textContent="";
 try{
  await api("/api/tester-feedback",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({category:$("testerFeedbackCategory").value,rating:$("testerFeedbackRating").value?Number($("testerFeedbackRating").value):null,message})});
  $("testerFeedbackMessage").value="";$("testerFeedbackRating").value="";status.textContent="Thanks — feedback sent.";
  if(authState.user?.role==="admin")await loadAdminFeedback();
 }catch(err){status.textContent=err.message}
 finally{btn.disabled=false;btn.textContent="Send feedback"}
});
async function revokeInvite(code){
 if(!confirm(`Revoke invite ${code}?`))return;
 try{await api(`/api/account/invites/${encodeURIComponent(code)}`,{method:"DELETE"});await loadInvites()}
 catch(err){alert(err.message)}
}

async function loadInvites(){
 try{
  const rows=await api("/api/account/invites");
  $("inviteResults").innerHTML=rows.length?rows.map(r=>`<div class="invite-row"><code>${esc(r.code)}</code><span>${r.uses}/${r.max_uses} used</span><small>Expires ${new Date(r.expires_at).toLocaleDateString()}</small>${r.uses===0?`<button class="text-button danger-text" type="button" onclick="revokeInvite('${esc(r.code)}')">Revoke</button>`:""}</div>`).join(""):'<p class="muted-copy">No active invites yet.</p>';
 }catch(err){$("inviteResults").innerHTML=`<small>${esc(err.message)}</small>`}
}
$("createInviteBtn")?.addEventListener("click",async()=>{
 const btn=$("createInviteBtn");btn.disabled=true;btn.textContent="Creating…";
 try{
  const x=await api("/api/account/invites",{method:"POST"});
  await loadInvites();
  alert(`Invite code: ${x.code}`);
 }catch(err){alert(err.message)}
 finally{btn.disabled=false;btn.textContent="Create invite"}
});
$("logoutBtn")?.addEventListener("click",async()=>{
 await api("/api/auth/logout",{method:"POST"});
 location.reload();
});

function go(id){
 document.querySelectorAll(".screen").forEach(x=>x.classList.remove("active"));
 $(id).classList.add("active");
 document.querySelectorAll("nav [data-go]").forEach(x=>x.classList.toggle("nav-active",x.dataset.go===id));
 if(id!=="wardrobe" || !wardrobeRestorePending)scrollTo(0,0);
 if(id==="wardrobe")loadGarments(false);
 if(id==="shortlist")loadShortlist();
 if(id==="savedlooks")loadSavedLooks();
 if(id==="packing")loadSavedTrips();
 if(id==="stylistv4"&&latestStylistSession)requestAnimationFrame(renderLatestStylistSession);
 if(id==="garmentdetail"&&detailGarmentId)loadGarmentDetail(detailGarmentId);
 if(id==="outfits")populateAnchor();
 if(id==="stylistv4")populateV4Anchor();
 if(id==="profile"){loadProfile();loadStyleLearning();loadModelPhotos()}
 if(id==="account")loadAccount();
 if(id==="intelligence")loadWardrobeIntelligence();
 if(id==="fitintel")loadFitIntelligence();
 if(id==="quickwardrobe")renderQuickWardrobeResults();
 if(id==="add"||id==="edit")populateGarmentCategorySelects();
 if(id==="buildlook")renderBuildLookPicker();
}
document.querySelector('nav [data-go="home"]')?.classList.add("nav-active");

document.addEventListener("click",e=>{
 const b=e.target.closest("[data-go]");
 if(!b)return;
 const activeAdd=$("add")?.classList.contains("active");
 if(activeAdd && b.dataset.go!=="add" && b.id!=="cancelAdd")resetAddFlow();
 go(b.dataset.go);
});

function quickWardrobeField(item,index,key,label,wide=false){
 const value=esc(item[key]||"");
 return `<label class="${wide?"quick-wide":""}">${label}<input data-quick-index="${index}" data-quick-key="${key}" value="${value}"></label>`;
}

function renderQuickWardrobeResults(){
 const box=$("quickWardrobeResults");
 if(!box)return;
 if(!quickWardrobeItems.length){box.innerHTML="";return;}

 box.innerHTML=`<div class="quick-review-head">
   <div><small class="eyebrow">REVIEW BEFORE SAVING</small><h3>${quickWardrobeItems.length} item${quickWardrobeItems.length===1?"":"s"} found</h3><p>Edit anything that needs correcting, untick anything you don't want, then save.</p></div>
   <button id="quickSaveSelected" class="primary" type="button">Save selected items</button>
  </div>
  <div class="quick-review-list">${quickWardrobeItems.map((item,index)=>`
   <article class="card quick-item" data-quick-card="${index}">
    <div class="row between quick-item-top">
     <label class="quick-include"><input type="checkbox" data-quick-include="${index}" ${item._include===false?"":"checked"}> Add this item</label>
     <div><span class="pill">${esc(item.confidence||"")}${item.confidence?" confidence":""}</span><button class="text-delete" type="button" data-quick-remove="${index}">Remove</button></div>
    </div>
    <div class="quick-grid">
     ${quickWardrobeField(item,index,"garment_type","GARMENT TYPE")}
     ${quickWardrobeField(item,index,"category","CATEGORY")}
     ${quickWardrobeField(item,index,"brand","BRAND")}
     ${quickWardrobeField(item,index,"model_line","MODEL / LINE")}
     ${quickWardrobeField(item,index,"labelled_size","SIZE")}
     ${quickWardrobeField(item,index,"colour","COLOUR")}
     ${quickWardrobeField(item,index,"material","MATERIAL")}
     ${quickWardrobeField(item,index,"fit_cut","FIT / CUT")}
     ${quickWardrobeField(item,index,"season","SEASON")}
     ${quickWardrobeField(item,index,"formality","FORMALITY")}
     ${quickWardrobeField(item,index,"notes","NOTES",true)}
    </div>
   </article>`).join("")}</div>`;

 $("quickSaveSelected")?.addEventListener("click",saveQuickWardrobeSelected);
}

async function analyseQuickWardrobe(){
 const text=$("quickWardrobeText").value.trim();
 const btn=$("quickWardrobeAnalyse"),box=$("quickWardrobeResults");
 if(!text){box.innerHTML='<div class="notice">Describe at least one item first.</div>';return;}
 const original=btn.textContent;
 btn.disabled=true;btn.innerHTML='<span class="inline-spinner"></span> Building your list…';
 box.innerHTML='<div class="shopping-working"><span class="retailer-search-spinner"></span><div><b>Reading your wardrobe description…</b><p>I’m separating the garments and filling only the details you actually gave me.</p><small>You’ll review everything before it is saved.</small></div></div>';
 try{
  const x=await api("/api/quick-wardrobe/parse",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({description:text})});
  quickWardrobeItems=(x.items||[]).map(item=>({...item,_include:true}));
  renderQuickWardrobeResults();
  requestAnimationFrame(()=>$("quickWardrobeResults")?.scrollIntoView({behavior:"smooth",block:"start"}));
 }catch(err){
  box.innerHTML=`<div class="notice"><b>I couldn't build the list.</b><br>${esc(err.message)}</div>`;
 }finally{
  btn.disabled=false;btn.textContent=original;
 }
}

async function saveQuickWardrobeSelected(){
 const btn=$("quickSaveSelected");
 document.querySelectorAll("[data-quick-key]").forEach(input=>{
  const i=Number(input.dataset.quickIndex),key=input.dataset.quickKey;
  if(quickWardrobeItems[i])quickWardrobeItems[i][key]=input.value.trim();
 });
 document.querySelectorAll("[data-quick-include]").forEach(input=>{
  const i=Number(input.dataset.quickInclude);
  if(quickWardrobeItems[i])quickWardrobeItems[i]._include=input.checked;
 });
 const selected=quickWardrobeItems.filter(x=>x._include!==false && String(x.garment_type||"").trim());
 if(!selected.length){$("quickWardrobeResults").insertAdjacentHTML("afterbegin",'<div class="notice">Select at least one valid item to save.</div>');return;}

 const original=btn.textContent;
 btn.disabled=true;btn.innerHTML='<span class="inline-spinner"></span> Saving…';
 try{
  const payload=selected.map(({confidence,_include,...item})=>({...item,pattern:item.pattern||"",fit_feedback:item.fit_feedback||"Unknown"}));
  const x=await api("/api/quick-wardrobe/save",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({items:payload})});
  quickWardrobeItems=[];
  $("quickWardrobeText").value="";
  await loadGarments();
  go("wardrobe");
  setTimeout(()=>{const nav=$("categoryNav");if(nav)nav.insertAdjacentHTML("afterend",`<div class="notice quick-saved-notice">${x.saved_count} item${x.saved_count===1?"":"s"} added. You can open any item later to add a photo or refine its details.</div>`);},50);
 }catch(err){
  btn.disabled=false;btn.textContent=original;
  $("quickWardrobeResults").insertAdjacentHTML("afterbegin",`<div class="notice"><b>I couldn't save the list.</b><br>${esc(err.message)}</div>`);
 }
}

function startQuickWardrobeDictation(){
 toggleAiDictation($("quickWardrobeDictate"),$("quickWardrobeText"),$("quickWardrobeDictationStatus"));
}


function buildLookGarmentTile(g){
 const selected=buildLookSelected.has(g.id);
 return `<button type="button" class="build-garment${selected?" selected":""}" data-build-garment="${g.id}" aria-pressed="${selected}">
  <span class="build-check">${selected?"✓":""}</span>
  ${g.image_path?`<img src="${g.image_path}" alt="">`:`<div class="build-no-photo">No photo</div>`}
  <b>${esc((g.brand?g.brand+" ":"")+(g.garment_type||g.category||"Garment"))}</b>
  <small>${esc([g.colour,g.labelled_size].filter(Boolean).join(" · "))}</small>
 </button>`;
}

function renderBuildLookPicker(){
 const box=$("buildLookPicker");if(!box)return;
 box.innerHTML=WARDROBE_ORDER.map(cat=>{
  const items=garments.filter(g=>g.category===cat);
  if(!items.length)return "";
  return `<section class="build-category"><div class="wardrobe-group-head"><h4>${esc(cat)}</h4><span>${items.length}</span></div>
   <div class="build-picker-grid">${items.map(buildLookGarmentTile).join("")}</div></section>`;
 }).join("");
 renderBuildLookTray();
}

function renderBuildLookTray(){
 const tray=$("buildLookTray");if(!tray)return;
 const chosen=[...buildLookSelected].map(id=>garments.find(g=>g.id===id)).filter(Boolean);
 if(!chosen.length){tray.innerHTML='<div class="notice">Select pieces above to start building your look.</div>';return;}
 tray.innerHTML=`<div class="card"><div class="row between"><div><small class="eyebrow">YOUR LOOK</small><h3>${chosen.length} selected piece${chosen.length===1?"":"s"}</h3></div><button class="text-button" id="clearBuildLook">Clear</button></div>
  <div class="build-selected-strip">${chosen.map(g=>`<div>${g.image_path?`<img src="${g.image_path}" alt="">`:""}<span>${esc(g.garment_type||g.category)}</span></div>`).join("")}</div>
  <div class="build-look-actions"><button id="showBuiltLook" class="primary">Show on me</button><button class="ghost" data-look-critique="analyse">Analyse this look</button><button class="ghost" data-look-critique="improve">Improve this look</button><button class="ghost" data-look-critique="alternatives">Give me alternatives</button></div>
 </div>`;
}

async function showBuiltLook(){
 const box=$("buildLookVisual"),ids=[...buildLookSelected];
 if(!ids.length)return;
 const activityKey="built-look-image";
 beginAppActivity(activityKey,"Creating your look…","Using the exact pieces you selected and your saved model photos.","image");
 box.innerHTML='<div class="shopping-working"><span class="retailer-search-spinner"></span><div><b>Creating your look…</b><p>Using the exact pieces you selected and your saved model photos.</p></div></div>';
 try{
  const x=await api("/api/outfit-visualisation",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({
   garment_ids:ids,label:"My own look",reason:$("buildLookContext").value.trim(),occasion:$("buildLookContext").value.trim(),use_my_likeness:true,requested_extra_piece:""
  })});
  setDynamicImageHtml(box,`<div class="card built-look-result"><img class="dynamic-ai-image" src="${x.image_path}" loading="eager" decoding="async" onload="stabiliseImagePaint(this)" alt="Your outfit visualisation"><div class="row"><button class="ghost" id="refreshBuiltLook">Regenerate image</button><button class="primary" data-look-critique="analyse">Ask the stylist</button></div><small>${esc(x.notice||"AI visualisation")}</small></div>`);
 }catch(err){box.innerHTML=`<div class="notice"><b>I couldn't create the visual.</b><br>${esc(err.message)}</div>`}
 finally{endAppActivity(activityKey)}
}

async function critiqueBuiltLook(mode){
 const box=$("buildLookCritique"),ids=[...buildLookSelected];if(!ids.length)return;
 box.innerHTML='<div class="shopping-working"><span class="retailer-search-spinner"></span><div><b>Stylist is reviewing your look…</b><p>I’ll keep your choices intact unless a change genuinely improves it.</p></div></div>';
 try{
  const x=await api("/api/look-critique",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({garment_ids:ids,request_text:$("buildLookContext").value.trim(),mode})});
  const changes=x.small_changes||[];
  box.innerHTML=`<article class="card look-critique"><div class="row between"><div><small class="eyebrow">STYLIST VIEW</small><h3>${esc(x.verdict)}</h3></div><span class="score">${x.score}/100</span></div>
   ${(x.what_works||[]).length?`<div class="critique-section"><b>What works</b>${x.what_works.map(t=>`<p>✓ ${esc(t)}</p>`).join("")}</div>`:""}
   ${changes.length?`<div class="critique-section"><b>${mode==="improve"?"Small improvements":"Ideas to consider"}</b>${changes.map(c=>`<p>${esc(c.change)} <small>${esc(c.reason)}</small></p>`).join("")}</div>`:`<div class="notice">I wouldn't change anything just for the sake of it.</div>`}
   <p>${esc(x.stylist_note||"")}</p></article>`;
 }catch(err){box.innerHTML=`<div class="notice">${esc(err.message)}</div>`}
}

function productLookCard(o,index,product){
 const payload=encodeURIComponent(JSON.stringify({o,product}));
 const owned=(o.owned_garment_ids||[]).map(id=>garments.find(g=>g.id===id)).filter(Boolean);
 return `<article class="card product-wardrobe-look"><div class="row between"><div><small>OPTION ${index+1}</small><h3>${esc(o.label)}</h3></div><span class="score">${o.score}/100</span></div>
  <p>${esc(o.why_it_works)}</p><div class="build-selected-strip">${owned.map(g=>`<div>${g.image_path?`<img src="${garmentThumbUrl(g)}" loading="lazy" decoding="async" onload="stabiliseImagePaint(this)" alt="">`:""}<span>${esc(g.garment_type||g.category)}</span></div>`).join("")}</div>
  <p class="style-note">${esc(o.style_note||"")}</p>
  <button class="primary" data-product-look-try="${payload}" data-product-look-index="${index}">Show this on me</button>
  <div id="productLookVisual-${index}"></div></article>`;
}

async function buildProductWardrobeLooks(){
 const url=$("productLookUrl").value.trim(),box=$("productLookResults"),btn=$("productLookBuild");
 if(!url){box.innerHTML='<div class="notice">Paste a retailer product URL first.</div>';return;}
 const original=btn.textContent;btn.disabled=true;btn.textContent="Building looks…";
 box.innerHTML='<div class="shopping-working"><span class="retailer-search-spinner"></span><div><b>Reading the product and your wardrobe…</b><p>I’m finding combinations that show whether this item genuinely works with what you own.</p></div></div>';
 try{
  const x=await api("/api/product-wardrobe-looks",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({url,occasion:$("productLookOccasion").value.trim(),max_options:3})});
  productLookContext=x;
  const p=x.product||{},r=x.result||{};
  box.innerHTML=`<div class="card product-found"><small class="eyebrow">PRODUCT FOUND</small><h3>${esc([p.brand,p.model_line||p.garment_type].filter(Boolean).join(" ")||"Retailer item")}</h3><p>${esc([p.colour,p.material,p.fit_cut].filter(Boolean).join(" · "))}</p></div>
   <div class="notice"><b>Stylist view:</b> ${esc(r.summary||"")} <small>Personalised visuals are preparing in the background.</small></div>${(r.outfits||[]).map((o,i)=>productLookCard(o,i,p)).join("")}`;
  setTimeout(()=>{
   box.querySelectorAll("[data-product-look-try]").forEach((b,i)=>{
    const encoded=b.dataset.productLookTry;
    const idx=Number(b.dataset.productLookIndex);
    enqueueBackgroundVisual(`product-look-${idx}-${encoded.slice(0,30)}`,()=>tryProductWardrobeLook(encoded,idx,null,true));
   });
  },120);
 }catch(err){box.innerHTML=`<div class="notice"><b>I couldn't build looks from that product.</b><br>${esc(err.message)}</div>`}
 finally{btn.disabled=false;btn.textContent=original}
}

async function tryProductWardrobeLook(encoded,index,button=null,silent=false){
 const data=JSON.parse(decodeURIComponent(encoded)),o=data.o,p=data.product,box=$(`productLookVisual-${index}`);
 const original=button?.textContent||"Show on me";
 if(button){button.disabled=true;button.textContent="Creating…";}
 box.innerHTML=`<div class="shopping-working"><span class="retailer-search-spinner"></span><div><b>${silent?"Preparing this look…":"Showing this on you…"}</b></div></div>`;
 try{
  const x=await api("/api/product-tryon",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({
   garment_ids:o.owned_garment_ids||[],product_name:p.model_line||p.garment_type||"Retailer product",product_brand:p.brand||"",
   product_retailer:"Retailer",product_image_url:productLookContext?.page_image_url||"",product_description:p.notes||"",
   product_colour:p.colour||"",product_material:p.material||"",product_fit:p.fit_cut||"",outfit_label:o.label,outfit_reason:o.why_it_works,use_my_likeness:true
  })});
  setDynamicImageHtml(box,`<div class="built-look-result"><img class="dynamic-ai-image" src="${x.image_path}" loading="eager" decoding="async" onload="stabiliseImagePaint(this)" alt=""><button class="ghost" data-product-look-try="${encoded}" data-product-look-index="${index}">Regenerate image</button><small>${esc(x.notice||"")}</small></div>`);
 }catch(err){box.innerHTML=`<div class="notice">${esc(err.message)}</div>`}
 finally{if(button){button.disabled=false;button.textContent=original}}
}


function applyStylingProfileUI(user){
 const women=(user?.styling_profile||"menswear")==="womenswear";
 WARDROBE_ORDER=[...(women?WOMENSWEAR_ORDER:MENSWEAR_ORDER)];
 populateGarmentCategorySelects();
 const brand=women?"Get Her Dressed":"Get Him Dressed";
 document.title=brand;
 if($("appBrandName"))$("appBrandName").textContent=brand;
 if($("homeBrandKicker"))$("homeBrandKicker").textContent=`${brand.toUpperCase()} · PRIVATE AI WARDROBE`;
 if($("accountStylingProfile"))$("accountStylingProfile").textContent=women?"Womenswear":"Menswear";
 if($("fitIntelIntro"))$("fitIntelIntro").textContent=women
  ?"Review how your real clothes fit. Get Her Dressed learns top, bottom, dress and shoe sizing separately — plus rise, proportion, length and brand/cut patterns."
  :"Review how your real clothes fit. Get Him Dressed will learn brand, size and cut patterns for future shopping.";
 document.body.dataset.stylingProfile=women?"womenswear":"menswear";
 if($("profileChestLabel"))$("profileChestLabel").textContent=women?"BUST / CHEST (cm)":"CHEST (cm)";
 if($("profileHipsLabel"))$("profileHipsLabel").textContent=women?"HIPS (cm)":"HIPS / SEAT (cm)";
 $("womenswearSizeFields")?.classList.toggle("hidden",!women);
 $("profileNeckField")?.classList.toggle("profile-secondary-measurement",women);
}
document.querySelectorAll("[data-profile-choice]").forEach(btn=>{
 btn.addEventListener("click",()=>{
  $("authStylingProfile").value=btn.dataset.profileChoice;
  document.querySelectorAll("[data-profile-choice]").forEach(x=>x.classList.toggle("active",x===btn));
 });
});


const ONBOARDING_STEPS=[
 {
  icon:"✦",eyebrow:"WELCOME",title:"Your wardrobe becomes the intelligence.",screen:"home",
  body:"The more the app knows about what you own, how things fit and which looks you like, the more personal every recommendation becomes.",
  tips:["Start with the clothes you genuinely wear.","You do not need to set everything up in one sitting."]
 },
 {
  icon:"◎",eyebrow:"STEP 1 · YOU",title:"Set up your fit & preferences.",screen:"profile",
  body:"Add your measurements, preferred fit, brand-size notes and a couple of clear model photos. This improves sizing advice and lets you see outfits on yourself.",
  tips:["Dictate the profile if that is quicker.","A front-facing portrait plus a full-body photo works well."]
 },
 {
  icon:"▦",eyebrow:"STEP 2 · YOUR CLOTHES",title:"Build enough wardrobe to be useful.",screen:"quickwardrobe",
  body:"Quick Add is the fastest start: describe or dictate several items at once. You can add photos and refine individual garments afterwards.",
  tips:["Brand, colour and garment type are the most useful early details.","Add favourites and frequently worn pieces first."]
 },
 {
  icon:"✦",eyebrow:"STEP 3 · GET DRESSED",title:"Ask the stylist naturally.",screen:"stylistv4",
  body:"Tell the stylist where you are going, how smart you want to be, the weather or any piece you want to wear. It builds around your actual wardrobe.",
  tips:["You can say “my wardrobe only” or allow one useful new piece.","Save strong looks so the app learns your taste."]
 },
 {
  icon:"⌁",eyebrow:"STEP 4 · FIT LEARNING",title:"Teach it what actually fits.",screen:"fitintel",
  body:"Review the fit of real garments. The app learns your size by brand, line and cut instead of assuming one size always works.",
  tips:["A few honest reviews are more useful than generic brand sizing.","Update a review whenever you learn something new."]
 },
 {
  icon:"◇",eyebrow:"YOU'RE READY",title:"Use the whole wardrobe, not isolated features.",screen:"home",
  body:"Shop only for useful gaps, build packing capsules, save repeatable looks and use Wardrobe Insights to see what your collection is actually doing.",
  tips:["Saved Looks are available from the bottom bar.","You can restart this walkthrough anytime from My Account."]
 }
];

let onboardingIndex=0;

function onboardingSeenKey(){
 return `ghd.${authState.user?.id||"anon"}.onboarding.v1`;
}
function onboardingPendingKey(){
 return `ghd.${authState.user?.id||"anon"}.onboarding.pending`;
}
function markOnboardingSeen(){
 try{
  localStorage.setItem(onboardingSeenKey(),"1");
  localStorage.removeItem(onboardingPendingKey());
 }catch{}
}
function renderOnboarding(){
 const step=ONBOARDING_STEPS[onboardingIndex];
 if(!step)return;
 $("onboardingStepText").textContent=`${onboardingIndex+1} of ${ONBOARDING_STEPS.length}`;
 $("onboardingProgressBar").style.width=`${((onboardingIndex+1)/ONBOARDING_STEPS.length)*100}%`;
 $("onboardingIcon").textContent=step.icon;
 $("onboardingEyebrow").textContent=step.eyebrow;
 $("onboardingTitle").textContent=step.title;
 $("onboardingBody").textContent=step.body;
 $("onboardingTips").innerHTML=(step.tips||[]).map(t=>`<div><span>✓</span><p>${esc(t)}</p></div>`).join("");
 $("onboardingBack").disabled=onboardingIndex===0;
 $("onboardingGo").classList.toggle("hidden",!step.screen || step.screen==="home");
 $("onboardingNext").textContent=onboardingIndex===ONBOARDING_STEPS.length-1?"Finish":"Next";
}
function startOnboarding(force=false){
 if(!authState.user)return;
 if(!force){
  try{if(localStorage.getItem(onboardingSeenKey())==="1")return}catch{}
 }
 onboardingIndex=0;
 $("onboardingOverlay")?.classList.remove("hidden");
 document.body.classList.add("onboarding-open");
 renderOnboarding();
}
function closeOnboarding(completed=false){
 $("onboardingOverlay")?.classList.add("hidden");
 document.body.classList.remove("onboarding-open");
 if(completed)markOnboardingSeen();
}
$("onboardingNext")?.addEventListener("click",()=>{
 if(onboardingIndex<ONBOARDING_STEPS.length-1){
  onboardingIndex++;
  renderOnboarding();
 }else{
  closeOnboarding(true);
  go("home");
 }
});
$("onboardingBack")?.addEventListener("click",()=>{
 if(onboardingIndex>0){onboardingIndex--;renderOnboarding()}
});
$("onboardingGo")?.addEventListener("click",()=>{
 const step=ONBOARDING_STEPS[onboardingIndex];
 closeOnboarding(false);
 if(step?.screen)go(step.screen);
});
$("onboardingSkip")?.addEventListener("click",()=>closeOnboarding(true));
$("onboardingClose")?.addEventListener("click",()=>closeOnboarding(false));
$("restartOnboardingBtn")?.addEventListener("click",()=>startOnboarding(true));

function maybeStartOnboarding(bootstrap){
 if(!authState.user)return;
 let pending=false,seen=false;
 try{
  pending=localStorage.getItem(onboardingPendingKey())==="1";
  seen=localStorage.getItem(onboardingSeenKey())==="1";
 }catch{}
 // Newly-created accounts always get the tour. As a fallback, zero-wardrobe
 // tester accounts get it once even if registration happened on another device.
 if(!seen && (pending || (authState.user.role!=="admin" && Number(bootstrap?.wardrobe_count||0)===0))){
  setTimeout(()=>startOnboarding(false),250);
 }
}

async function init(){
 let status;
 try{
  const r=await fetch("/api/auth/status");
  status=await r.json();
 }catch{
  showAuthGate({bootstrap_available:false});
  setAuthMessage("The app is temporarily unavailable.");
  return;
 }
 if(!status.authenticated){
  showAuthGate(status);
  selectAuthTab(status.bootstrap_available?"register":"login");
  return;
 }
 authState.user=status.user;
 applyStylingProfileUI(authState.user);
 hideAuthGate();

 const cacheOwner=localStorage.getItem("ghd.cacheOwner");
 if(cacheOwner!==String(authState.user.id)){
  localStorage.removeItem("personalStylist.latestStylistSession.v1");
  localStorage.removeItem("personalStylist.v4VisualCache.v1");
  localStorage.setItem("ghd.cacheOwner",String(authState.user.id));
 }
 loadPersistentStylistState();

 // Paint cached wardrobe metadata immediately. A fresh server copy follows quietly.
 const cachedWardrobe=readUserCache("wardrobe");
 if(Array.isArray(cachedWardrobe) && cachedWardrobe.length){
  garments=cachedWardrobe;
  garments.forEach(g=>g.category=normalisedCategory(g.category));
  $("count").textContent=`${garments.length} saved item${garments.length===1?"":"s"}`;
  renderWardrobeCategoryNav();
  renderGarments();
  populateV4Anchor();
 }

 // Home is interactive now; these requests no longer block one another.
 const healthPromise=api("/api/health").then(h=>{
  $("status").textContent=`${authState.user.display_name} · ${h.ai_enabled?"AI stylist connected":"AI key not connected"}`;
 }).catch(()=>{$("status").textContent=`${authState.user.display_name} · Connected`});

 const bootPromise=api("/api/bootstrap").then(b=>{
  if(b.name)$("greeting").textContent=`Good morning, ${b.name}`;
  if(!garments.length)$("count").textContent=`${b.wardrobe_count||0} saved item${Number(b.wardrobe_count)===1?"":"s"}`;
  maybeStartOnboarding(b);
 }).catch(()=>{});

 const wardrobePromise=loadGarments(true).catch(()=>{});
 const profilePromise=loadProfile().catch(()=>{});

 if(latestStylistSession)requestAnimationFrame(renderLatestStylistSession);
 Promise.allSettled([healthPromise,bootPromise,wardrobePromise,profilePromise]);
}
const MENSWEAR_ORDER=["Blazers & Tailoring","Overshirts & Shirt Jackets","Jackets & Coats","Knitwear","Sweatshirts & Hoodies","Shirts","Polos & T-Shirts","Trousers","Shorts","Footwear","Accessories","Other"];
const WOMENSWEAR_ORDER=["Dresses","Skirts","Jumpsuits & Playsuits","Blazers & Tailoring","Jackets","Coats","Knitwear","Sweatshirts & Hoodies","Blouses & Shirts","Tops & T-Shirts","Trousers & Jeans","Shorts","Activewear","Footwear","Bags","Jewellery","Accessories","Other"];
let WARDROBE_ORDER=[...MENSWEAR_ORDER];
let selectedWardrobeCategory="";
let wardrobeReturnGarmentId=null;
let wardrobeReturnCategory="";
let wardrobeRestorePending=false;

function normalisedCategory(c){
 const raw=String(c||"Other").trim().toLowerCase();
 if((authState.user?.styling_profile||"menswear")==="menswear"){
  if(["jackets","coats","jackets & outerwear","outerwear","jackets & coats"].includes(raw))return "Jackets & Coats";
  if(["overshirts","overshirt","shirt jackets","shirt jacket"].includes(raw))return "Overshirts & Shirt Jackets";
 }
 return WARDROBE_ORDER.find(x=>x.toLowerCase()===raw)||"Other";
}

function wardrobeCategoryLabel(cat){
 return cat;
}
function populateGarmentCategorySelects(){
 ["category","e_category"].forEach(id=>{
  const el=$(id);if(!el)return;
  const current=el.value;
  el.innerHTML=WARDROBE_ORDER.map(cat=>`<option value="${esc(cat)}">${esc(cat)}</option>`).join("");
  if(WARDROBE_ORDER.includes(current))el.value=current;
  else if(current)el.value=normalisedCategory(current);
 });
}

function renderWardrobeCategoryNav(){
 const nav=$("categoryNav");
 if(!nav)return;
 const present=WARDROBE_ORDER.filter(cat=>garments.some(g=>g.category===cat));
 const cats=["",...present];
 nav.innerHTML=cats.map(cat=>{
  const count=cat?garments.filter(g=>g.category===cat).length:garments.length;
  const active=selectedWardrobeCategory===cat;
  return `<button type="button" class="wardrobe-category-btn${active?" active":""}" data-wardrobe-category="${esc(cat)}" aria-pressed="${active}">
   <span>${esc(cat?wardrobeCategoryLabel(cat):"All")}</span><small>${count}</small>
  </button>`;
 }).join("");
}

async function loadGarments(force=true){
 if(!force && garments.length && (Date.now()-garmentsLoadedAt)<DATA_FRESH_MS){
  renderWardrobeCategoryNav();
  renderGarments();
  return garments;
 }
 const fresh=await api("/api/garments");
 garments=fresh;
 garments.forEach(g=>g.category=normalisedCategory(g.category));
 garmentsLoadedAt=Date.now();
 writeUserCache("wardrobe",garments);
 $("count").textContent=`${garments.length} saved item${garments.length===1?"":"s"}`;

 if(selectedWardrobeCategory && !garments.some(g=>g.category===selectedWardrobeCategory)){
  selectedWardrobeCategory="";
 }
 renderWardrobeCategoryNav();
 renderGarments();
 populateV4Anchor();

 if(wardrobeRestorePending){
  requestAnimationFrame(()=>requestAnimationFrame(()=>restoreWardrobePosition()));
 }
 return garments;
}


function stabiliseImagePaint(img){
 if(!img)return;

 const repaint=()=>{
  if(!img.isConnected)return;
  img.classList.add("image-paint-ready");

  // Safari can occasionally decode a dynamically inserted image without
  // repainting its composited layer until a resize occurs. Force one local
  // layout read + compositor refresh rather than relying on window resize.
  void img.offsetHeight;
  const parent=img.parentElement;
  if(parent){
   parent.classList.add("force-image-repaint");
   void parent.offsetHeight;
  }

  requestAnimationFrame(()=>{
   img.style.webkitTransform="translateZ(0)";
   img.style.transform="translateZ(0)";
   if(parent){
    parent.style.webkitTransform="translateZ(0)";
    parent.style.transform="translateZ(0)";
   }
   requestAnimationFrame(()=>{
    if(parent)parent.classList.remove("force-image-repaint");
   });
  });
 };

 if(img.complete && img.naturalWidth>0){
  repaint();
  return;
 }

 img.addEventListener("load",repaint,{once:true});
}

function stabiliseDynamicImages(root=document){
 const scope=root?.querySelectorAll ? root : document;
 scope.querySelectorAll("img.dynamic-ai-image,img.saved-look-visual,img.saved-piece-image").forEach(stabiliseImagePaint);
}

function setDynamicImageHtml(container,html){
 if(!container)return;
 container.innerHTML=html;
 requestAnimationFrame(()=>stabiliseDynamicImages(container));
}

window.addEventListener("pageshow",()=>requestAnimationFrame(()=>stabiliseDynamicImages(document)));
document.addEventListener("visibilitychange",()=>{
 if(!document.hidden)requestAnimationFrame(()=>stabiliseDynamicImages(document));
});

function retryableImageSrc(src){
 if(!src)return "";
 const joiner=src.includes("?")?"&":"?";
 return `${src}${joiner}img_retry=${Date.now()}`;
}

function handleWardrobeImageError(img){
 if(!img)return;
 const attempts=Number(img.dataset.retryCount||0);
 const original=img.dataset.originalSrc||"";
 const currentBase=(img.dataset.baseSrc||img.getAttribute("src")||"").split("?")[0];

 // One retry of the current image path handles occasional delayed/static-file responses.
 if(attempts===0 && currentBase){
  img.dataset.retryCount="1";
  setTimeout(()=>{img.src=retryableImageSrc(currentBase)},350);
  return;
 }

 // If the cleaned/catalogue image is unavailable, fall back to the original upload.
 if(attempts<=1 && original && original!==currentBase){
  img.dataset.retryCount="2";
  img.dataset.baseSrc=original;
  img.src=retryableImageSrc(original);
  return;
 }

 img.classList.add("image-missing");
 const wrap=img.closest(".garment-photo-wrap,.detail-image-wrap");
 if(wrap && !wrap.querySelector(".image-load-fallback")){
  const fallback=document.createElement("button");
  fallback.type="button";
  fallback.className="image-load-fallback";
  fallback.innerHTML="<b>Photo unavailable</b><small>Tap to retry · if this persists, add the photo again</small>";
  fallback.addEventListener("click",()=>{
   img.classList.remove("image-missing");
   fallback.remove();
   img.dataset.retryCount="0";
   const src=img.dataset.baseSrc||img.dataset.originalSrc||"";
   if(src)img.src=retryableImageSrc(src);
  });
  wrap.appendChild(fallback);
 }
}

function garmentCard(g){const cleaning=cleanupInProgress.has(g.id);const label=esc((g.brand?g.brand+" ":"")+(g.garment_type||"Garment"));const image=(g.image_path && g.image_available!==false)?`<img class="garment-photo" src="${garmentThumbUrl(g)}" loading="lazy" decoding="async" data-base-src="${garmentThumbUrl(g)}" data-original-src="${esc(g.original_image_path||"")}" data-retry-count="0" alt="${label}" onclick="openGarment(${g.id})" title="Open garment" onerror="handleWardrobeImageError(this)">`:`<button class="garment-no-photo" onclick="openGarment(${g.id})" type="button"><span>No photo yet</span><small>Open garment</small></button>`;return `<div class="garment${cleaning?" is-cleaning":""}" data-garment-id="${g.id}"><div class="garment-photo-wrap">${image}${cleaning?`<div class="cleanup-overlay"><span class="cleanup-spinner"></span><b>Cleaning up photo…</b><small>Preparing your catalogue image.</small></div>`:""}</div><div class="meta"><b>${label}</b><small>${esc([g.colour,g.material,g.labelled_size].filter(Boolean).join(" · "))}</small><div><span class="pill">${esc(g.fit_feedback||"Fit unknown")}</span></div><div class="row" style="margin-top:9px"><button class="secondary" onclick="buildAround(${g.id})">Build around</button><button class="ghost" onclick="editGarment(${g.id})">Edit</button>${g.image_path?`<button class="ghost cleanup-btn" onclick="cleanupPhoto(${g.id})">${cleaning?"Cleaning…":"Clean up photo"}</button>`:""}${g.original_image_path&&g.image_path!==g.original_image_path?`<button class="ghost" onclick="restoreOriginal(${g.id})">Original photo</button>`:""}<button class="danger" onclick="del(${g.id})">Delete</button></div></div></div>`;}
function categorySlug(cat){
 return String(cat||"other").toLowerCase().replace(/&/g,"and").replace(/[^a-z0-9]+/g,"-").replace(/^-|-$/g,"");
}

function renderGarments(){
 const q=$("search").value.toLowerCase();
 const f=selectedWardrobeCategory;
 const list=garments.filter(g=>(!f||g.category===f)&&(!q||JSON.stringify(g).toLowerCase().includes(q)));

 if(!list.length){
  $("garments").innerHTML='<div class="empty">No garments match this view.</div>';
  return;
 }

 const cats=f?[f]:WARDROBE_ORDER;
 $("garments").innerHTML=cats.map(cat=>{
  const items=list.filter(g=>g.category===cat);
  if(!items.length)return "";
  return `<section id="wardrobe-group-${categorySlug(cat)}" class="wardrobe-group" data-category="${esc(cat)}">
   <div class="wardrobe-group-head"><h4>${esc(cat)}</h4><span>${items.length} item${items.length===1?"":"s"}</span></div>
   <div class="garments wardrobe-group-grid">${items.map(garmentCard).join("")}</div>
  </section>`;
 }).join("");
}

function rememberWardrobePosition(id){
 const g=garments.find(x=>x.id===id);
 wardrobeReturnGarmentId=id||null;
 wardrobeReturnCategory=g?.category||selectedWardrobeCategory||"";
 wardrobeRestorePending=true;
}

function restoreWardrobePosition(){
 if(!wardrobeRestorePending)return;

 let target=null;
 if(wardrobeReturnGarmentId){
  target=document.querySelector(`[data-garment-id="${wardrobeReturnGarmentId}"]`);
 }

 if(!target && wardrobeReturnCategory){
  target=document.querySelector(`[data-category="${CSS.escape(wardrobeReturnCategory)}"]`);
 }

 if(target){
  target.scrollIntoView({block:"center",behavior:"auto"});
 }

 wardrobeRestorePending=false;
}

$("search").addEventListener("input",renderGarments);
$("categoryNav").addEventListener("click",e=>{
 const btn=e.target.closest("[data-wardrobe-category]");
 if(!btn)return;
 selectedWardrobeCategory=btn.dataset.wardrobeCategory||"";
 wardrobeRestorePending=false;
 renderWardrobeCategoryNav();
 renderGarments();
 scrollTo(0,0);
});



function openGarment(id){
 rememberWardrobePosition(id);
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
 if(status==="confirmed"){
  return `<div class="detail-research fit-confirmed">
   <div class="research-head"><div><small>FIT LEARNING</small><h4>Fit confirmed</h4></div><span class="fit-status-pill">Learned</span></div>
   <p><b>${esc(g.brand||"This garment")} ${esc(g.labelled_size||"")}</b>${g.fit_rating?` · ${esc(g.fit_rating)}/5`:""}</p>
   <div class="fit-summary-grid">
    <span>${authState.user?.styling_profile==="womenswear"?"Bust / chest":"Chest"} <b>${esc(g.fit_chest||"—")}</b></span><span>Waist <b>${esc(g.fit_waist||"—")}</b></span>
    <span>Hips <b>${esc(g.fit_hips||"—")}</b></span><span>Length <b>${esc(g.fit_length||"—")}</b></span><span>Sleeve <b>${esc(g.fit_sleeve||"—")}</b></span>
    <span>Shoulders <b>${esc(g.fit_shoulders||"—")}</b></span>
   </div>
   ${g.fit_notes?`<p>${esc(g.fit_notes)}</p>`:""}
   <button class="ghost" onclick="openFitReview(${g.id})">Update fit review</button>
  </div>`;
 }
 return `<div class="detail-research fit-awaiting">
  <div class="research-head"><div><small>FIT LEARNING</small><h4>Teach me how this fits</h4></div><span class="fit-status-pill awaiting">Not reviewed</span></div>
  <p>Confirm the labelled size and how this garment fits you. I’ll use it as real-world evidence for future size and shopping recommendations.</p>
  <button class="primary" onclick="openFitReview(${g.id})">Review this fit</button>
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
   <label>${authState.user?.styling_profile==="womenswear"?"Bust / chest":"Chest"}<select id="fit-chest">${fitOptions(g.fit_chest)}</select></label>
   <label>Waist<select id="fit-waist">${fitOptions(g.fit_waist)}</select></label>
   <label>Hips / seat<select id="fit-hips">${fitOptions(g.fit_hips)}</select></label>
   <label>${authState.user?.styling_profile==="womenswear"?"Body / hem length":"Body / leg length"}<select id="fit-length">${fitOptions(g.fit_length)}</select></label>
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
    fit_chest:$("fit-chest").value,fit_waist:$("fit-waist").value,fit_hips:$("fit-hips").value,
    fit_length:$("fit-length").value,fit_sleeve:$("fit-sleeve").value,
    fit_shoulders:$("fit-shoulders").value,fit_notes:$("fit-notes").value.trim()
   })
  });
  await loadGarments();
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
  const conciseMeta=[g.colour,g.fit_cut,g.material,g.labelled_size].filter(Boolean);
  const hist=(g.outfit_history||[]).length
   ? `<details class="detail-accordion detail-history-accordion">
        <summary><span><small>OUTFIT HISTORY</small><b>${g.outfit_history.length} rated look${g.outfit_history.length===1?"":"s"}</b></span><i>›</i></summary>
        <div class="detail-history">${g.outfit_history.map(h=>`<div><b>${esc(h.label)}</b><span>${esc(h.rating||"")}</span></div>`).join("")}</div>
      </details>`
   : `<div class="detail-history detail-history-empty"><span>Outfit history</span><p>Not used in a rated outfit yet.</p></div>`;

  box.innerHTML=`<div class="garment-detail-hero">
    <div class="detail-image-wrap">${(g.image_path && g.image_available!==false)?`<img src="/api/garments/${g.id}/image" data-base-src="/api/garments/${g.id}/image" data-original-src="${esc(g.original_image_path||"")}" data-retry-count="0" alt="${title}" onerror="handleWardrobeImageError(this)">`:`<div class="detail-no-photo"><b>Photo unavailable</b><small>The garment is safe. Use Edit to add the photo again if retrying does not restore it.</small></div>`}</div>
    <div class="detail-summary">
     <small>${esc(g.category||"WARDROBE ITEM")}</small>
     <h2>${title}</h2>
     <p class="detail-meta-line">${esc(conciseMeta.join(" · "))}</p>
     <div class="detail-pills"><span>${esc(g.fit_feedback||"Fit unknown")}</span>${g.season?`<span>${esc(g.season)}</span>`:""}${g.formality?`<span>${esc(g.formality)}</span>`:""}</div>
     <div class="detail-actions"><button class="primary" onclick="buildAround(${g.id})">Build an outfit</button><button class="ghost" onclick="cleanupPhoto(${g.id})">Clean up photo</button></div>
    </div>
   </div>

   <details class="detail-accordion garment-details-accordion">
    <summary><span><small>GARMENT DETAILS</small><b>View all details</b></span><i>›</i></summary>
    <div class="detail-card detail-card-inside">
     <dl>
      <div><dt>Brand</dt><dd>${detailValue(g.brand)}</dd></div>
      <div><dt>Model / line</dt><dd>${detailValue(g.model_line)}</dd></div>
      <div><dt>Size</dt><dd>${detailValue(g.labelled_size)}</dd></div>
      <div><dt>Colour</dt><dd>${detailValue(g.colour)}</dd></div>
      <div><dt>Material</dt><dd>${detailValue(g.material)}</dd></div>
      <div><dt>Pattern</dt><dd>${detailValue(g.pattern)}</dd></div>
      <div><dt>Fit / cut</dt><dd>${detailValue(g.fit_cut)}</dd></div>
      <div class="detail-notes-row"><dt>Notes</dt><dd>${detailValue(g.notes)}</dd></div>
     </dl>
    </div>
   </details>

   ${hist}
   <div id="fitReviewPanel">${renderFitReviewPanel(g)}</div>
   <details id="brandIntelligenceAccordion" class="detail-accordion brand-intelligence-accordion">
    <summary><span><small>BRAND INTELLIGENCE</small><b>${esc(g.brand||"Fit, sizing & construction")}</b></span><i>›</i></summary>
    <div id="brandIntelligencePanel">${renderEnrichmentPanel(g)}</div>
   </details>`;

  $("detailEdit").textContent=g.image_path?"Edit":"Add photo";
  $("detailEdit").onclick=()=>editGarment(g.id);

  // Desktop can show the supporting detail open; mobile starts with a calm,
  // product-first view and lets the user expand information on demand.
  const mobile=window.matchMedia("(max-width: 700px)").matches;
  box.querySelectorAll(".detail-accordion").forEach(d=>d.open=!mobile);

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
 // Direct Edit from the wardrobe should return to this exact garment.
 if($("wardrobe")?.classList.contains("active"))rememberWardrobePosition(id);
 editingGarmentId=id;
 if(g.image_path){
  $("editPreview").src=g.image_path;
  $("editPreview").classList.remove("hidden");
  $("editNoPhoto").classList.add("hidden");
 }else{
  $("editPreview").removeAttribute("src");
  $("editPreview").classList.add("hidden");
  $("editNoPhoto").classList.remove("hidden");
 }
 $("editPhotoStatus").textContent="";

 populateGarmentCategorySelects();
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


async function replaceGarmentPhoto(file){
 if(!file || !editingGarmentId)return;
 const status=$("editPhotoStatus");
 const camera=$("editCameraPhoto"),library=$("editLibraryPhoto");
 status.textContent="Uploading and preparing photo…";
 camera.disabled=true;library.disabled=true;
 try{
  const fd=new FormData();
  fd.append("file",file);
  const x=await api(`/api/garments/${editingGarmentId}/photo`,{method:"POST",body:fd});
  $("editPreview").src=x.image_path;
  $("editPreview").classList.remove("hidden");
  $("editNoPhoto").classList.add("hidden");
  status.textContent="Photo added. Your garment details have been kept.";
  await loadGarments();
  const fresh=garments.find(g=>g.id===editingGarmentId);
  if(fresh){
   fresh.image_path=x.image_path;
   fresh.original_image_path=x.original_image_path;
  }
 }catch(err){
  status.textContent=`Could not add photo: ${err.message}`;
  alert(`I couldn't add that photo: ${err.message}`);
 }finally{
  camera.disabled=false;library.disabled=false;
  camera.value="";library.value="";
 }
}

$("editCameraPhoto").addEventListener("change",e=>replaceGarmentPhoto(e.target.files?.[0]));
$("editLibraryPhoto").addEventListener("change",e=>replaceGarmentPhoto(e.target.files?.[0]));

$("saveEdit").addEventListener("click",async()=>{
 if(!editingGarmentId)return;
 const body={
  category:$("e_category").value,
  category_manual:true,
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
 wardrobeReturnGarmentId=savedId;
 wardrobeReturnCategory=normalisedCategory(body.category);
 wardrobeRestorePending=true;
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
 originalUploadedPath="";
 aiConfidence=0;
 importedProductSourceUrl="";
 addFlowHasUserPhoto=false;
}


function releasePreviewObjectUrl(){
 if(previewObjectUrl){
  try{URL.revokeObjectURL(previewObjectUrl)}catch{}
  previewObjectUrl="";
 }
}

function resetAddFlow(){
 if(garmentAnalysisController){
  try{garmentAnalysisController.abort()}catch{}
  garmentAnalysisController=null;
 }
 analysisInProgress=false;
 releasePreviewObjectUrl();
 photoQueue=[];
 currentPhotoIndex=-1;
 batchMode=false;
 clearGarmentFields();

 $("cameraPhoto").value="";
 $("libraryPhoto").value="";
 if($("productUrl"))$("productUrl").value="";
 if($("urlImportStatus"))$("urlImportStatus").textContent="";
 $("preview").removeAttribute("src");
 $("preview").classList.add("hidden");
 $("analysisMsg").textContent="";
 $("analysisMsg").classList.add("hidden");
 $("batchStatus").textContent="";
 $("batchStatus").classList.add("hidden");
 $("skipGarment").classList.add("hidden");
 $("skipGarment").disabled=false;
 $("saveGarment").disabled=false;
 $("saveGarment").textContent="Save to wardrobe";
}

function updateBatchUI(){
 const status=$("batchStatus");
 const skip=$("skipGarment");
 const save=$("saveGarment");

 if(batchMode && photoQueue.length){
   status.classList.remove("hidden");
   status.innerHTML=`<b>Batch upload:</b> item ${currentPhotoIndex+1} of ${photoQueue.length}. ${analysisInProgress?"Analysing this photo — please wait…":"Check the AI details, then Save & Next."}`;
   skip.classList.remove("hidden");
   skip.disabled=analysisInProgress;
   save.disabled=analysisInProgress;
   save.textContent=analysisInProgress
     ? "Analysing…"
     : (currentPhotoIndex < photoQueue.length-1 ? "Save & Next" : "Save final item");
 }else{
   status.classList.add("hidden");
   skip.classList.add("hidden");
   skip.disabled=false;
   if(!analysisInProgress){
     save.disabled=false;
     save.textContent="Save to wardrobe";
   }
 }
}

async function handleGarmentPhoto(file,{preserveDetails=false}={}){
 if(!file || analysisInProgress)return false;

 const retainedSource=importedProductSourceUrl;
 if(!preserveDetails)clearGarmentFields();

 releasePreviewObjectUrl();
 previewObjectUrl=URL.createObjectURL(file);
 $("preview").src=previewObjectUrl;
 $("preview").classList.remove("hidden");

 const fd=new FormData();
 fd.append("file",file);

 analysisInProgress=true;
 garmentAnalysisController=new AbortController();
 $("analysisMsg").classList.remove("hidden");
 $("analysisMsg").textContent=preserveDetails
   ?"Adding your photo. The product-page details will be kept; the photo analysis will only fill missing fields."
   :"Analysing garment… Please wait before moving to the next photo.";
 updateBatchUI();

 let timeoutId;
 try{
  timeoutId=setTimeout(()=>garmentAnalysisController?.abort(),75000);
  const x=await api("/api/analyse-garment",{method:"POST",body:fd,signal:garmentAnalysisController.signal});

  uploadedPath=x.image_path||uploadedPath;
  originalUploadedPath=x.original_image_path||originalUploadedPath||uploadedPath;
  addFlowHasUserPhoto=true;
  if(preserveDetails)importedProductSourceUrl=retainedSource;

  releasePreviewObjectUrl();
  if(uploadedPath)$("preview").src=uploadedPath;

  if(x.analysis){
   Object.entries(x.analysis).forEach(([k,v])=>{
    if(!$(k)||k==="confidence")return;
    const existing=String($(k).value||"").trim();
    if(!preserveDetails || !existing)$(k).value=v||"";
   });
   aiConfidence=Math.max(Number(aiConfidence||0),Number(x.analysis.confidence||0));
   $("analysisMsg").textContent=preserveDetails
    ?"Photo added successfully. I kept the imported web details and only used the photo to fill blanks. Please check everything before saving."
    :`AI analysis complete (${Math.round(Number(x.analysis.confidence||0)*100)}% confidence). Please check and correct anything before saving.`;
  }else{
   $("analysisMsg").textContent=preserveDetails
    ?"Photo added successfully. Your imported product details have been kept."
    :"Photo saved. AI is not connected yet, so enter the garment details manually.";
  }
  return true;
 }catch(err){
  const aborted=err?.name==="AbortError";
  $("analysisMsg").textContent=aborted
   ?"This photo took too long to analyse. Your existing garment details are still here; you can retry or save them as they are."
   :`This photo could not be analysed: ${err.message}`;
  if(preserveDetails)importedProductSourceUrl=retainedSource;
  return false;
 }finally{
  if(timeoutId)clearTimeout(timeoutId);
  garmentAnalysisController=null;
  analysisInProgress=false;
  updateBatchUI();
 }
}

async function startBatch(files){
 if(analysisInProgress)return;

 const selectedFiles=Array.from(files||[]);
 if(!selectedFiles.length)return;

 const addingPhotoToImportedItem=Boolean(importedProductSourceUrl) && selectedFiles.length===1;

 if(!addingPhotoToImportedItem){
  resetAddFlow();
 }else{
  releasePreviewObjectUrl();
  photoQueue=[];
  currentPhotoIndex=-1;
  batchMode=false;
 }

 photoQueue=selectedFiles;
 batchMode=photoQueue.length>1;
 currentPhotoIndex=0;
 updateBatchUI();
 await handleGarmentPhoto(photoQueue[currentPhotoIndex],{preserveDetails:addingPhotoToImportedItem});
}

async function advanceBatch(){
 if(analysisInProgress)return false;

 if(batchMode && currentPhotoIndex < photoQueue.length-1){
   currentPhotoIndex++;
   await handleGarmentPhoto(photoQueue[currentPhotoIndex]);
   return true;
 }

 releasePreviewObjectUrl();
 photoQueue=[];
 currentPhotoIndex=-1;
 batchMode=false;
 updateBatchUI();
 return false;
}

async function importProductUrl(){
 const url=($("productUrl").value||"").trim(),status=$("urlImportStatus");
 if(!url)return alert("Paste a retailer product link first.");

 const keepUserPhoto=Boolean(addFlowHasUserPhoto && uploadedPath);
 const retainedPhotoPath=uploadedPath;
 const retainedOriginalPhotoPath=originalUploadedPath;
 const retainedPreview=$("preview").getAttribute("src")||"";

 if(!keepUserPhoto)resetAddFlow();

 $("productUrl").value=url;
 status.textContent=keepUserPhoto
  ?"Reading the retailer page. Your own photo will be kept."
  :"Reading retailer page and preparing the garment…";
 $("importProductUrl").disabled=true;
 $("analysisMsg").classList.remove("hidden");
 $("analysisMsg").textContent="Importing product information…";

 try{
  const x=await api("/api/import-product-url",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({url})});
  importedProductSourceUrl=x.source_url||url;

  if(keepUserPhoto){
   uploadedPath=retainedPhotoPath;
   originalUploadedPath=retainedOriginalPhotoPath||retainedPhotoPath;
   addFlowHasUserPhoto=true;
   if(retainedPreview){
    $("preview").src=retainedPreview;
    $("preview").classList.remove("hidden");
   }
  }else{
   uploadedPath=x.image_path||"";
   originalUploadedPath=x.original_image_path||x.image_path||"";
   addFlowHasUserPhoto=false;
   if(x.image_path){
    $("preview").src=x.image_path;
    $("preview").classList.remove("hidden");
   }else{
    $("preview").classList.add("hidden");
   }
  }

  const a=x.analysis||{};
  Object.entries(a).forEach(([k,v])=>{
   if(!$(k)||k==="confidence"||v===null||v===undefined||v==="")return;
   if(keepUserPhoto && (k==="labelled_size"||k==="fit_feedback") && String($(k).value||"").trim() && $(k).value!=="Unknown")return;
   $(k).value=v||"";
  });
  aiConfidence=Math.max(Number(aiConfidence||0),Number(a.confidence||0));

  const sourceLine=`Product page: ${x.source_url||url}`;
  const existingNotes=$("notes").value||"";
  if(!existingNotes.includes(sourceLine))$("notes").value=[existingNotes,sourceLine].filter(Boolean).join("\n");

  if(keepUserPhoto){
   status.textContent="Product details imported and your own photo has been kept. Check the combined details below before saving.";
   $("analysisMsg").textContent="Your photo and retailer information are now combined into one garment.";
  }else if(x.direct_page_blocked){
   status.textContent=x.image_available
    ?"The retailer blocked direct access, so I found the product through live web search instead. You can still add your own photo before saving."
    :"The retailer blocked direct access, so I found the product through live web search instead. Add your own photo now if you want; these details will be preserved.";
   $("analysisMsg").textContent="Product identified through live web search. You can now add your own photo without losing these details.";
  }else{
   status.textContent=x.image_available
    ?"Imported. Keep the retailer image or add your own photo before saving."
    :"Imported. No usable retailer image was exposed, so you can add your own photo now without losing these details.";
   $("analysisMsg").textContent="Product page imported. You can now add your own photo without losing these details.";
  }
 }catch(err){
  status.textContent=`Import failed: ${err.message}`;
  $("analysisMsg").textContent=keepUserPhoto
   ?"The web import failed, but your photo and its existing details are still here."
   :"The product page could not be imported.";
 }finally{
  $("importProductUrl").disabled=false;
 }
}
$("importProductUrl").addEventListener("click",importProductUrl);$("productUrl").addEventListener("keydown",e=>{if(e.key==="Enter"){e.preventDefault();importProductUrl()}});const urlDropZone=$("urlDropZone");urlDropZone.addEventListener("dragover",e=>{e.preventDefault();urlDropZone.classList.add("dragging")});urlDropZone.addEventListener("dragleave",()=>urlDropZone.classList.remove("dragging"));urlDropZone.addEventListener("drop",e=>{e.preventDefault();urlDropZone.classList.remove("dragging");const raw=e.dataTransfer.getData("text/uri-list")||e.dataTransfer.getData("text/plain")||"";const url=raw.split(/\r?\n/).find(x=>/^https?:\/\//i.test(x.trim()))||raw.trim();if(url){$("productUrl").value=url;importProductUrl()}});

$("cancelAdd").addEventListener("click",()=>{
 resetAddFlow();
});

$("cameraPhoto").addEventListener("change",async e=>{
 const file=e.target.files?.[0];
 if(!file)return;
 resetAddFlow();
 await handleGarmentPhoto(file);
});
$("libraryPhoto").addEventListener("change",async e=>{
 const selected=Array.from(e.target.files||[]);
 await startBatch(selected);
});
$("skipGarment").addEventListener("click",async()=>{if(batchMode && !analysisInProgress)await advanceBatch();});

$("saveGarment").addEventListener("click",async()=>{
 if(analysisInProgress)return;
 const saveBtn=$("saveGarment");
 const hasImport=Boolean(importedProductSourceUrl || ($("productUrl")?.value||"").trim());
 if(!uploadedPath && !hasImport)return alert("Add a photo or import a product page first.");

 const ids=["category","garment_type","brand","model_line","labelled_size","colour","material","pattern","fit_cut","fit_feedback","season","formality","notes"];
 const fd=new FormData();
 fd.append("image_path",uploadedPath||"");
 fd.append("original_image_path",originalUploadedPath||uploadedPath||"");
 ids.forEach(id=>fd.append(id,$(id).value||""));
 fd.append("ai_confidence",String(aiConfidence||0));

 const previousText=saveBtn.textContent;
 saveBtn.disabled=true;
 saveBtn.textContent="Saving to wardrobe…";
 const status=$("urlImportStatus");
 if(status && hasImport)status.textContent="Saving this product to your wardrobe…";

 try{
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
   if($("productUrl"))$("productUrl").value="";
   if(status)status.textContent="";
   $("preview").classList.add("hidden");
   $("analysisMsg").classList.add("hidden");
   go("wardrobe");
  }
 }catch(err){
  console.error("Save garment failed",err);
  if(status && hasImport)status.textContent=`Could not save: ${err.message}`;
  alert(`I couldn't save this garment: ${err.message}`);
 }finally{
  saveBtn.disabled=false;
  if(!batchMode)saveBtn.textContent="Save to wardrobe";
  else saveBtn.textContent=previousText;
 }
});



function renderIntelligenceMetric(value,label,note=""){
 return `<div class="intel-metric"><strong>${esc(value)}</strong><b>${esc(label)}</b>${note?`<small>${esc(note)}</small>`:""}</div>`;
}

function intelList(items,empty="No strong pattern yet."){
 return items?.length
  ? `<ul>${items.map(x=>`<li>${esc(x)}</li>`).join("")}</ul>`
  : `<p class="muted-copy">${esc(empty)}</p>`;
}

function renderWardrobeIntelligence(x){
 const box=$("wardrobeIntelligenceResults");
 if(!box)return;
 const m=x.metrics||{};
 const a=x.analysis||{};
 const categories=x.category_counts||[];
 const colours=x.colour_counts||[];
 const saved=x.saved_item_counts||[];

 const maxCat=Math.max(1,...categories.map(c=>Number(c.count||0)));
 const categoryBars=categories.map(c=>`<div class="intel-bar-row">
  <span>${esc(c.name)}</span>
  <div class="intel-bar-track"><i style="width:${Math.max(4,(Number(c.count||0)/maxCat)*100)}%"></i></div>
  <b>${c.count}</b>
 </div>`).join("");

 const colourChips=colours.map(c=>`<span class="intel-chip">${esc(c.name)} <b>${c.count}</b></span>`).join("");

 const savedItems=saved.length?saved.map(item=>`<div class="intel-saved-item">
  <img src="/api/garments/${item.id}/image" loading="eager" decoding="async" onload="stabiliseImagePaint(this)" alt="">
  <div><b>${esc(item.label||"Garment")}</b><small>${esc([item.colour,item.category].filter(Boolean).join(" · "))}</small></div>
  <span>${item.count}× saved</span>
 </div>`).join(""):`<p class="muted-copy">Save a few outfits and this will start showing which pieces recur in looks you deliberately keep.</p>`;

 const gaps=(a.gaps||[]).map(g=>`<article class="intel-gap ${esc(g.priority||"low")}">
  <div class="row between"><b>${esc(g.title)}</b><span>${esc(g.priority||"")}</span></div>
  <p>${esc(g.reason)}</p>
 </article>`).join("")||`<p class="muted-copy">No clear wardrobe gap identified yet.</p>`;

 box.innerHTML=`
  <div class="intel-metrics">
   ${renderIntelligenceMetric(m.total_items||0,"Wardrobe items")}
   ${renderIntelligenceMetric(m.categories||0,"Categories")}
   ${renderIntelligenceMetric(m.saved_looks||0,"Saved looks")}
   ${renderIntelligenceMetric(m.perfect_fit_items||0,"Perfect-fit items")}
  </div>

  <div class="card intel-summary-card">
   <small class="eyebrow">STYLIST VIEW</small>
   <h4>${esc(a.summary||"Wardrobe overview")}</h4>
   ${a.variety_nudge?`<div class="intel-variety"><b>Keep it varied</b><p>${esc(a.variety_nudge)}</p></div>`:""}
  </div>

  <div class="intel-grid">
   <div class="card intel-panel"><small class="eyebrow">WHAT'S WORKING</small><h4>Wardrobe strengths</h4>${intelList(a.strengths)}</div>
   <div class="card intel-panel"><small class="eyebrow">REAL GAPS</small><h4>Where to improve</h4><div class="intel-gap-list">${gaps}</div></div>
   <div class="card intel-panel"><small class="eyebrow">YOUR TASTE</small><h4>Patterns from Saved Looks</h4>${intelList(a.saved_style_patterns)}</div>
   <div class="card intel-panel"><small class="eyebrow">VERSATILITY</small><h4>Pieces doing useful work</h4>${intelList(a.versatility_wins)}</div>
  </div>

  <div class="card intel-next-purchase">
   <small class="eyebrow">NEXT PURCHASE</small>
   <h4>${esc(a.next_purchase?.item||"No clear purchase needed")}</h4>
   <p>${esc(a.next_purchase?.why||"")}</p>
   ${a.next_purchase?.unlock_estimate?`<small>${esc(a.next_purchase.unlock_estimate)}</small>`:""}
  </div>

  <div class="intel-grid intel-data-grid">
   <div class="card intel-panel"><small class="eyebrow">WARDROBE MIX</small><h4>Categories</h4><div class="intel-bars">${categoryBars}</div></div>
   <div class="card intel-panel"><small class="eyebrow">COLOUR MIX</small><h4>Most common colours</h4><div class="intel-chips">${colourChips||'<span class="muted-copy">No colour data yet.</span>'}</div></div>
  </div>

  <div class="card intel-panel">
   <small class="eyebrow">SAVED-LOOK SIGNAL</small>
   <h4>Pieces recurring in looks you save</h4>
   <div class="intel-saved-list">${savedItems}</div>
   <small class="intel-evidence-note">${esc(x.evidence_note||"")}</small>
  </div>`;
 requestAnimationFrame(()=>stabiliseDynamicImages(box));
}

async function loadWardrobeIntelligence(){
 const box=$("wardrobeIntelligenceResults");
 if(!box)return;
 box.innerHTML='<div class="card v4-thinking"><span class="spinner"></span><div><b>Analysing your wardrobe…</b><small>Looking at composition, saved looks and fit feedback.</small></div></div>';
 beginAppActivity("wardrobe-intel","Analysing your wardrobe…","Looking for strengths, genuine gaps and useful style patterns.","working");
 try{
  const x=await api("/api/wardrobe-intelligence");
  renderWardrobeIntelligence(x);
 }catch(err){
  box.innerHTML=`<div class="notice"><b>I couldn't analyse the wardrobe.</b><br>${esc(err.message)}</div>`;
 }finally{
  endAppActivity("wardrobe-intel");
 }
}

$("refreshWardrobeIntelligence")?.addEventListener("click",loadWardrobeIntelligence);


function fitConfidenceLabel(c){
 return c==="high"?"Strong evidence":c==="medium"?"Building confidence":"Early learning";
}

function renderFitPattern(pattern){
 const sizes=(pattern.sizes||[]).slice(0,4).map(s=>`${esc(s.size)} (${s.count})`).join(" · ");
 const issues=(pattern.issues||[]).slice(0,3).map(x=>esc(x.issue)).join(" · ");
 return `<div class="fit-pattern-row">
  <div><b>${esc(pattern.name)}</b><small>${pattern.reviews} review${pattern.reviews===1?"":"s"}${pattern.average_rating?` · ${pattern.average_rating}/5 avg`:""}</small></div>
  <div>${sizes?`<span>${sizes}</span>`:""}${issues?`<small>${issues}</small>`:""}</div>
 </div>`;
}

function renderFitIntelligence(x){
 const box=$("fitIntelResults");
 if(!box)return;
 const m=x.metrics||{},a=x.analysis||{};
 const nextIds=a.next_reviews||[];
 const nextItems=nextIds.map(id=>garments.find(g=>g.id===id)).filter(Boolean);

 const lessons=(a.brand_lessons||[]).map(b=>`<div class="fit-brand-lesson">
  <div class="row between"><b>${esc(b.brand)}</b><span>${esc(fitConfidenceLabel(b.confidence))}</span></div>
  <p>${esc(b.lesson)}</p>
 </div>`).join("")||'<p class="muted-copy">Review a few branded items and brand-specific lessons will appear here.</p>';

 const reviewCards=nextItems.length?nextItems.map(g=>`<button class="fit-review-item" type="button" onclick="openFitItem(${g.id})">
  <img src="${garmentThumbUrl(g)}" loading="lazy" decoding="async" alt="">
  <span><b>${esc((g.brand?g.brand+" ":"")+(g.garment_type||g.category||"Garment"))}</b><small>${esc([g.labelled_size,g.fit_cut].filter(Boolean).join(" · ")||"Tap to review fit")}</small></span>
  <i>Review →</i>
 </button>`).join(""):'<p class="muted-copy">No priority reviews right now.</p>';

 box.innerHTML=`
  <div class="fit-intel-metrics">
   <div><strong>${m.confirmed_reviews||0}</strong><span>Fit reviews</span></div>
   <div><strong>${m.brands_learned||0}</strong><span>Brands learned</span></div>
   <div><strong>${m.categories_learned||0}</strong><span>Categories</span></div>
   <div><strong>${m.unreviewed_items||0}</strong><span>Still to review</span></div>
  </div>

  <div class="card fit-intel-summary">
   <div class="row between"><small class="eyebrow">YOUR FIT MODEL</small><span class="fit-confidence">${esc(fitConfidenceLabel(a.confidence||"low"))}</span></div>
   <h4>${esc(a.summary||"Fit learning is getting started.")}</h4>
  </div>

  <div class="fit-intel-grid">
   <div class="card fit-intel-panel"><small class="eyebrow">WHAT WORKS</small><h4>Best fit signals</h4>${intelList(a.what_fits_best||[],"Add fit reviews to learn what consistently works.")}</div>
   <div class="card fit-intel-panel"><small class="eyebrow">WATCH FOR</small><h4>Recurring issues</h4>${intelList(a.watch_out_for||[],"No repeated fit problem identified yet.")}</div>
  </div>

  <div class="card fit-intel-panel"><small class="eyebrow">BRAND LESSONS</small><h4>What your wardrobe is teaching me</h4><div class="fit-brand-lessons">${lessons}</div></div>

  <div class="card fit-intel-panel"><small class="eyebrow">SHOPPING RULES</small><h4>How I'll use this when you buy</h4>${intelList(a.shopping_rules||[],"As you review garments, buying guidance will become more specific.")}</div>

  ${(x.brand_patterns||[]).length?`<details class="card fit-patterns"><summary><b>Fit history by brand</b><span>View evidence</span></summary><div>${x.brand_patterns.map(renderFitPattern).join("")}</div></details>`:""}

  <div class="card fit-intel-panel">
   <small class="eyebrow">TEACH THE STYLIST</small><h4>Useful items to review next</h4>
   <p class="fit-review-explainer">It only takes a few seconds per item. Prioritising repeated brands and common categories makes the sizing model useful faster.</p>
   <div class="fit-review-list">${reviewCards}</div>
  </div>`;
}

async function loadFitIntelligence(){
 const box=$("fitIntelResults");
 if(!box)return;
 box.innerHTML='<div class="card v4-thinking"><span class="spinner"></span><div><b>Learning your fit…</b><small>Looking at confirmed garment reviews and measurements.</small></div></div>';
 beginAppActivity("fit-intel","Learning your fit…","Building brand, size and cut patterns from your wardrobe.","working");
 try{
  const x=await api("/api/fit-intelligence");
  renderFitIntelligence(x);
 }catch(err){
  box.innerHTML=`<div class="notice">${esc(err.message)}</div>`;
 }finally{
  endAppActivity("fit-intel");
 }
}

function openFitItem(id){
 detailGarmentId=id;
 go("garmentdetail");
 loadGarmentDetail(id).then(()=>{
  setTimeout(()=>{
   const panel=$("fitReviewPanel");
   if(panel)panel.scrollIntoView({behavior:"smooth",block:"center"});
  },150);
 });
}

$("refreshFitIntel")?.addEventListener("click",loadFitIntelligence);

async function loadStyleLearning(){
 try{
  const x=await api("/api/style-learning");
  const ratings=x.ratings||{};
  const total=x.feedback_count||0;
  const saved=x.saved_look_count||0;
  const brands=(x.perfect_fit_brands||[]).map(b=>`${esc(b.brand)} (${b.count})`).join(", ");
  const colours=(x.saved_colours||[]).map(c=>`${esc(c.name)} (${c.count})`).join(", ");
  const garmentTypes=(x.saved_garment_types||[]).map(c=>`${esc(c.name)} (${c.count})`).join(", ");
  const wornColours=(x.worn_colours||[]).map(c=>`${esc(c.name)} (${c.count} wear${c.count===1?"":"s"})`).join(", ");
  const wornTypes=(x.worn_garment_types||[]).map(c=>`${esc(c.name)} (${c.count} wear${c.count===1?"":"s"})`).join(", ");
  const recordedWears=Number(x.recorded_wears||0);
  const wornLooks=Number(x.worn_look_count||0);

  const signals=[];
  if(recordedWears)signals.push(`${recordedWears} recorded wear${recordedWears===1?"":"s"} across ${wornLooks} look${wornLooks===1?"":"s"}`);
  if(saved)signals.push(`${saved} saved look${saved===1?"":"s"}`);
  if(total)signals.push(`${total} outfit reaction${total===1?"":"s"}`);

  let html=`<p><b>Learning from:</b> ${signals.length?signals.join(" · "):"No style signals yet."}</p>`;
  if(ratings["Works for me"]||ratings["Loved"]||ratings["Less like this"]){
   html+=`<p><b>Reactions:</b> ${
    [
     ratings["Loved"]?`Favourites: ${ratings["Loved"]}`:"",
     ratings["Works for me"]?`Works for me: ${ratings["Works for me"]}`:"",
     ratings["Less like this"]?`Less like this: ${ratings["Less like this"]}`:""
    ].filter(Boolean).join(" · ")
   }</p>`;
  }
  if(wornColours)html+=`<p><b>Colours you actually wear:</b> ${wornColours}</p>`;
  if(wornTypes)html+=`<p><b>Pieces you actually wear:</b> ${wornTypes}</p>`;
  if(colours)html+=`<p><b>Colours recurring in saved looks:</b> ${colours}</p>`;
  if(garmentTypes)html+=`<p><b>Pieces recurring in saved looks:</b> ${garmentTypes}</p>`;
  if(brands)html+=`<p><b>Perfect-fit brands:</b> ${brands}</p>`;
  html+=`<small>${esc(x.message||"")}</small>`;
  $("styleLearning").innerHTML=html;
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
 const keys=["name","height_cm","chest_cm","waist_cm","hips_cm","thigh_cm","inseam_cm","sleeve_cm","neck_cm","preferred_fit","style_notes","brand_notes","usual_top_size","usual_bottom_size","usual_dress_size","usual_shoe_size","bra_size","preferred_rise","preferred_hem_length","heel_preference","accessory_notes"],p={};
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
 setupAiDictation("v4Dictate","v4Request","dictationStatus");
}


const v4VisualCache=new Map();
let latestStylistSession=null;
const STYLIST_SESSION_KEY="personalStylist.latestStylistSession.v1";
const STYLIST_VISUAL_KEY="personalStylist.v4VisualCache.v1";

function loadPersistentStylistState(){
 try{
  const raw=localStorage.getItem(STYLIST_SESSION_KEY);
  if(raw)latestStylistSession=JSON.parse(raw);
 }catch{}
 try{
  const raw=localStorage.getItem(STYLIST_VISUAL_KEY);
  if(raw){
   const saved=JSON.parse(raw);
   Object.entries(saved||{}).forEach(([k,v])=>v4VisualCache.set(k,v));
  }
 }catch{}
}

function persistStylistSession(){
 try{
  if(latestStylistSession)localStorage.setItem(STYLIST_SESSION_KEY,JSON.stringify(latestStylistSession));
 }catch{}
}

function persistVisualCache(){
 try{
  const obj={};
  for(const [k,v] of v4VisualCache.entries())obj[k]=v;
  localStorage.setItem(STYLIST_VISUAL_KEY,JSON.stringify(obj));
 }catch{}
}

const sourcedProductContexts=new Map();
const gapRecommendationContexts=new Map();


function v4VisualCacheKey(o,useMyLikeness){
 return JSON.stringify({
  ids:o.owned_garment_ids||[],
  label:o.label||"",
  extra:o.missing_piece||"",
  likeness:useMyLikeness
 });
}

function currentVisualPathForOutfit(o){
 const preferred=v4VisualCache.get(v4VisualCacheKey(o,true));
 const generic=v4VisualCache.get(v4VisualCacheKey(o,false));
 return preferred?.image_path||generic?.image_path||"";
}

function renderStylistRefineBar(show=true){
 $("v4RefineBar")?.classList.toggle("hidden",!show);
}

function stylistResultHtml(x){
 return `<div class="notice stylist-session-note"><b>Current stylist suggestions</b><span>Replace one you dislike, or refine the whole set below.</span></div>`+
  `<div class="notice"><b>Stylist view:</b> ${esc(x.summary||"")}</div>`+
  (x.outfits||[]).map((o,i)=>renderV4Outfit(o,i)).join("");
}

function renderLatestStylistSession(){
 if(!latestStylistSession)return;
 const box=$("v4Results");
 if(!box)return;
 const x=latestStylistSession.result||{};
 if(latestStylistSession.request_text)$("v4Request").value=latestStylistSession.request_text;
 if($("v4Location"))$("v4Location").value=latestStylistSession.location||"";
 if($("v4When"))$("v4When").value=latestStylistSession.when||"";
 if(latestStylistSession.weather?.summary){
  const w=latestStylistSession.weather;
  $("v4WeatherStatus").classList.remove("hidden");
  $("v4WeatherStatus").innerHTML=`<b>Forecast used:</b> ${esc(w.summary)}${w.styling_context?`<small>${esc(w.styling_context)}</small>`:""}`;
 }
 box.innerHTML=stylistResultHtml(x);
 renderStylistRefineBar(Boolean((x.outfits||[]).length));
}


async function reactToOutfit(encoded,rating,button){
 const o=JSON.parse(decodeURIComponent(encoded));
 const original=button?.textContent||rating;
 if(button){button.disabled=true;button.textContent="Saved";}
 try{
  await api("/api/feedback",{
   method:"POST",headers:{"Content-Type":"application/json"},
   body:JSON.stringify({outfit:o,rating})
  });
  if(button){
   button.classList.add("reaction-saved");
   button.textContent=rating==="Works for me"?"✓ Works for me":"↘ Less like this";
   setTimeout(()=>{button.disabled=false},350);
  }
 }catch(err){
  if(button){button.disabled=false;button.textContent=original}
  alert(err.message);
 }
}

async function saveFavouriteOutfit(encoded,index,button){
 const o=JSON.parse(decodeURIComponent(encoded));
 const visual=currentVisualPathForOutfit(o);
 const original=button?.textContent||"☆ Favourite";
 if(button){button.disabled=true;button.textContent="Saving…";}
 try{
  await api("/api/outfit-favourites",{
   method:"POST",headers:{"Content-Type":"application/json"},
   body:JSON.stringify({
    outfit:o,
    request_text:latestStylistSession?.request_text||"",
    weather_context:latestStylistSession?.weather?.summary||"",
    visual_path:visual
   })
  });
  await api("/api/feedback",{
   method:"POST",headers:{"Content-Type":"application/json"},
   body:JSON.stringify({outfit:o,rating:"Loved"})
  }).catch(()=>{});
  localStorage.removeItem(userCacheKey("savedLooks"));
  if(button)button.textContent="★ Saved";
 }catch(err){
  if(button){button.disabled=false;button.textContent=original;}
  alert(err.message);
 }
}

let savedLooksRows=[];

function savedLookSearchText(row){
 const o=row.outfit||{};
 const pieces=(o.owned_garment_ids||[]).map(id=>garments.find(g=>g.id===id)).filter(Boolean);
 return [
  row.label,o.label,row.request_text,row.weather_context,row.occasion,row.season,row.notes,
  ...(row.tags||[]),
  ...pieces.flatMap(g=>[g.brand,g.garment_type,g.category,g.colour,g.material])
 ].filter(Boolean).join(" ").toLowerCase();
}

function savedLookFilteredRows(){
 const q=($("savedLookSearch")?.value||"").trim().toLowerCase();
 const occasion=$("savedLookOccasionFilter")?.value||"";
 const season=$("savedLookSeasonFilter")?.value||"";
 const history=$("savedLookHistoryFilter")?.value||"";
 return savedLooksRows.filter(row=>{
  if(q && !savedLookSearchText(row).includes(q))return false;
  if(occasion && (row.occasion||"")!==occasion)return false;
  if(season && (row.season||"")!==season)return false;
  if(history==="pinned" && !row.is_pinned)return false;
  if(history==="worn" && !(Number(row.wore_count||0)>0))return false;
  if(history==="unworn" && Number(row.wore_count||0)>0)return false;
  return true;
 });
}

function populateSavedLookFilters(){
 const select=$("savedLookOccasionFilter");
 if(!select)return;
 const current=select.value;
 const values=[...new Set(savedLooksRows.map(x=>(x.occasion||"").trim()).filter(Boolean))].sort();
 select.innerHTML='<option value="">All occasions</option>'+values.map(x=>`<option>${esc(x)}</option>`).join("");
 if(values.includes(current))select.value=current;
}

function renderSavedLooksCollection(){
 const box=$("savedLooksResults");
 if(!box)return;
 const rows=savedLookFilteredRows();
 const total=savedLooksRows.length;
 const worn=savedLooksRows.filter(x=>Number(x.wore_count||0)>0).length;
 const pinned=savedLooksRows.filter(x=>x.is_pinned).length;
 if($("savedLooksSummary"))$("savedLooksSummary").innerHTML=`<span><b>${total}</b> saved</span><span><b>${worn}</b> worn</span><span><b>${pinned}</b> pinned</span>`;
 if(!total){
  box.innerHTML='<div class="notice">No saved looks yet. Favourite an outfit from Ask My Stylist and it will appear here.</div>';
  return;
 }
 if(!rows.length){
  box.innerHTML='<div class="notice">No saved looks match those filters.</div>';
  return;
 }
 box.innerHTML=rows.map(renderSavedLook).join("");
 requestAnimationFrame(()=>stabiliseDynamicImages(box));
}

function savedLookTagsInput(row){
 return esc((row.tags||[]).join(", "));
}

function savedLookEditPanel(row){
 const seasons=["","Spring","Summer","Autumn","Winter","Transitional","All-season"];
 return `<div id="savedLookEdit-${row.id}" class="saved-look-edit hidden">
  <div class="two">
   <label>NAME<input id="savedLabel-${row.id}" value="${esc(row.label||row.outfit?.label||"Saved look")}"></label>
   <label>OCCASION<input id="savedOccasion-${row.id}" value="${esc(row.occasion||"")}" placeholder="e.g. Smart casual dinner"></label>
  </div>
  <div class="two">
   <label>SEASON<select id="savedSeason-${row.id}">${seasons.map(s=>`<option ${s===(row.season||"")?"selected":""}>${esc(s||"Not set")}</option>`).join("")}</select></label>
   <label>TAGS<input id="savedTags-${row.id}" value="${savedLookTagsInput(row)}" placeholder="e.g. dinner, travel, easy"></label>
  </div>
  <label>NOTES<textarea id="savedNotes-${row.id}" rows="2" placeholder="Anything worth remembering about this look">${esc(row.notes||"")}</textarea></label>
  <div class="row"><button class="primary" type="button" onclick="saveSavedLookDetails(${row.id},this)">Save details</button><button class="ghost" type="button" onclick="toggleSavedLookEdit(${row.id})">Cancel</button></div>
 </div>`;
}

function toggleSavedLookEdit(id){
 $(`savedLookEdit-${id}`)?.classList.toggle("hidden");
}

async function saveSavedLookDetails(id,button){
 const row=savedLooksRows.find(x=>x.id===id);if(!row)return;
 const seasonValue=$(`savedSeason-${id}`)?.value||"";
 const payload={
  label:$(`savedLabel-${id}`)?.value.trim()||"Saved look",
  occasion:$(`savedOccasion-${id}`)?.value.trim()||"",
  season:seasonValue==="Not set"?"":seasonValue,
  tags:($(`savedTags-${id}`)?.value||"").split(",").map(x=>x.trim()).filter(Boolean),
  notes:$(`savedNotes-${id}`)?.value.trim()||"",
  is_pinned:Boolean(row.is_pinned)
 };
 const original=button?.textContent||"Save details";
 if(button){button.disabled=true;button.textContent="Saving…"}
 try{
  await api(`/api/outfit-favourites/${id}`,{method:"PUT",headers:{"Content-Type":"application/json"},body:JSON.stringify(payload)});
  localStorage.removeItem(userCacheKey("savedLooks"));
  await loadSavedLooks();
 }catch(err){alert(err.message);if(button){button.disabled=false;button.textContent=original}}
}

async function markSavedLookWorn(id,button){
 const original=button?.textContent||"I wore this";
 if(button){
  button.disabled=true;
  button.classList.add("saving-wear");
  button.textContent="Saving wear…";
 }
 try{
  const updated=await api(`/api/outfit-favourites/${id}/wore`,{method:"POST"});
  const count=Number(updated.wore_count||1);
  if(button){
   button.classList.remove("saving-wear");
   button.classList.add("wear-confirmed");
   button.textContent=count>1?"+ Add another wear":"✓ Recorded";
  }
  showWearLearningToast(count);
  localStorage.removeItem(userCacheKey("savedLooks"));
  setTimeout(()=>loadSavedLooks(),700);
 }catch(err){
  alert(err.message);
  if(button){
   button.disabled=false;
   button.classList.remove("saving-wear");
   button.textContent=original;
  }
 }
}

async function undoSavedLookWear(id,button){
 const original=button?.textContent||"− Undo last wear";
 if(button){button.disabled=true;button.textContent="Undoing…"}
 try{
  const updated=await api(`/api/outfit-favourites/${id}/undo-wear`,{method:"POST"});
  const count=Number(updated.wore_count||0);
  showWearLearningToast(count,true);
  localStorage.removeItem(userCacheKey("savedLooks"));
  await loadSavedLooks();
 }catch(err){
  alert(err.message);
  if(button){button.disabled=false;button.textContent=original}
 }
}

function showWearLearningToast(count,undone=false){
 let toast=$("wearLearningToast");
 if(!toast){
  toast=document.createElement("div");
  toast.id="wearLearningToast";
  toast.className="wear-learning-toast";
  document.body.appendChild(toast);
 }
 toast.innerHTML=undone
  ? `<div><span>↶</span><div><b>Last wear removed</b><p>The wear count is now ${count}. Your style-learning evidence has been corrected too.</p></div></div>`
  : `<div><span>✓</span><div><b>Wear recorded</b><p>This look now carries more weight in your style learning because you've actually worn it${count>1?` ${count} times`:""}.</p></div></div>`;
 toast.classList.add("show");
 clearTimeout(window._wearToastTimer);
 window._wearToastTimer=setTimeout(()=>toast.classList.remove("show"),3200);
}

async function toggleSavedLookPinned(id,button){
 const row=savedLooksRows.find(x=>x.id===id);if(!row)return;
 const payload={
  label:row.label||row.outfit?.label||"Saved look",
  occasion:row.occasion||"",season:row.season||"",tags:row.tags||[],notes:row.notes||"",
  is_pinned:!row.is_pinned
 };
 if(button)button.disabled=true;
 try{
  await api(`/api/outfit-favourites/${id}`,{method:"PUT",headers:{"Content-Type":"application/json"},body:JSON.stringify(payload)});
  localStorage.removeItem(userCacheKey("savedLooks"));
  await loadSavedLooks();
 }catch(err){alert(err.message);if(button)button.disabled=false}
}

function useSavedLookAgain(encoded,rowId){
 const data=JSON.parse(decodeURIComponent(encoded));
 const row=savedLooksRows.find(x=>x.id===Number(rowId));
 const o=data.outfit||{};
 const pieceNames=(o.owned_garment_ids||[]).map(id=>{
  const g=garments.find(x=>x.id===id);
  return g?`${g.brand?g.brand+" ":""}${g.garment_type||g.category||"garment"}`:"";
 }).filter(Boolean);
 const prompt=[
  `I want to wear my saved look "${row?.label||o.label||"Saved look"}" again.`,
  pieceNames.length?`The saved pieces are: ${pieceNames.join(", ")}.`:"",
  "Use the saved look as the starting point. Keep it intact if it still works, or suggest only small useful changes for today's context."
 ].filter(Boolean).join(" ");
 $("v4Request").value=prompt;
 go("stylistv4");
 setTimeout(()=>$("v4Request")?.focus(),80);
}

["savedLookSearch","savedLookOccasionFilter","savedLookSeasonFilter","savedLookHistoryFilter"].forEach(id=>{
 $(id)?.addEventListener(id==="savedLookSearch"?"input":"change",renderSavedLooksCollection);
});

async function loadSavedLooks(){
 const box=$("savedLooksResults");
 if(!box)return;

 const cached=readUserCache("savedLooks");
 if(Array.isArray(cached)){
  savedLooksRows=cached;
  populateSavedLookFilters();
  renderSavedLooksCollection();
 }else{
  box.innerHTML='<div class="card v4-thinking"><span class="spinner"></span><div><b>Loading saved looks…</b></div></div>';
 }

 try{
  const rows=await api("/api/outfit-favourites");
  savedLooksRows=rows;
  writeUserCache("savedLooks",rows);
  populateSavedLookFilters();
  renderSavedLooksCollection();
 }catch(err){
  if(!Array.isArray(cached))box.innerHTML=`<div class="notice">${esc(err.message)}</div>`;
 }
}

function renderSavedLook(row){
 const o=row.outfit||{};
 const pieces=(o.owned_garment_ids||[]).map(id=>garments.find(g=>g.id===id)).filter(Boolean);
 const strip=pieces.map(g=>g.image_path?`<div class="saved-piece"><img class="saved-piece-image" src="${garmentThumbUrl(g)}" loading="lazy" decoding="async" onload="stabiliseImagePaint(this)" alt=""><span>${esc((g.brand?g.brand+" ":"")+(g.garment_type||g.category||"Garment"))}</span></div>`:"").join("");
 const visual=row.visual_path?`<img class="saved-look-visual dynamic-ai-image" src="${row.visual_path}" loading="lazy" decoding="async" onload="stabiliseImagePaint(this)" alt="Saved outfit visualisation">`:"";
 const payload=encodeURIComponent(JSON.stringify({outfit:o,request_text:row.request_text||"",weather_context:row.weather_context||""}));
 const worn=Number(row.wore_count||0);
 const lastWorn=row.last_worn_at?new Date(row.last_worn_at).toLocaleDateString():"";
 const tags=(row.tags||[]).map(t=>`<span>${esc(t)}</span>`).join("");
 const meta=[row.occasion,row.season,worn?`${worn} wear${worn===1?"":"s"}`:"Not worn yet"].filter(Boolean);
 const woreButtonClass="ghost saved-wore-btn";
 const woreButtonLabel=worn>0?"+ Add another wear":"I wore this";
 const undoWearButton=worn>0?`<button class="text-button undo-wear-btn" type="button" onclick="undoSavedLookWear(${row.id},this)">− Undo last wear</button>`:"";
 return `<article class="card saved-look-card ${row.is_pinned?"saved-look-pinned":""}">
  <div class="row between saved-look-title-row">
   <div><small>${row.is_pinned?"PINNED LOOK":"SAVED LOOK"}</small><h3>${esc(row.label||o.label||"Outfit")}</h3></div>
   <div class="saved-title-actions"><button class="text-button" type="button" onclick="toggleSavedLookPinned(${row.id},this)">${row.is_pinned?"★ Pinned":"☆ Pin"}</button><button class="text-button danger-text" onclick="deleteSavedLook(${row.id})">Remove</button></div>
  </div>
  ${meta.length?`<div class="saved-look-meta">${meta.map(x=>`<span>${esc(x)}</span>`).join("")}</div>`:""}
  ${tags?`<div class="saved-look-tags">${tags}</div>`:""}
  ${visual}
  <div class="saved-piece-strip">${strip}</div>
  ${o.why_it_works?`<p>${esc(o.why_it_works)}</p>`:""}
  ${row.notes?`<div class="saved-look-note"><b>Your note:</b> ${esc(row.notes)}</div>`:""}
  ${worn>0?`<div class="saved-wear-status"><div class="saved-wear-status-head"><div><span>✓</span><b>Worn ${worn} time${worn===1?"":"s"}</b></div>${undoWearButton}</div>${lastWorn?`<small>Last worn ${esc(lastWorn)}</small>`:""}<p>Real wears are stronger learning evidence than saved favourites, so this helps the stylist understand what genuinely works in your day-to-day wardrobe.</p></div>`:""}
  ${row.weather_context?`<div class="saved-weather"><b>Weather context:</b> ${esc(row.weather_context)}</div>`:""}
  ${row.request_text?`<small class="saved-request">Originally asked: ${esc(row.request_text)}</small>`:""}
  <div class="saved-look-actions saved-look-primary-actions">
   <button class="primary" type="button" onclick="useSavedLookAgain('${payload}',${row.id})">Wear / style again</button>
   <button class="${woreButtonClass}" type="button" onclick="markSavedLookWorn(${row.id},this)">${woreButtonLabel}</button>
   <button class="ghost" type="button" onclick="toggleSavedLookEdit(${row.id})">Edit details</button>
  </div>
  <div class="saved-look-actions">
   <button class="ghost" type="button" onclick="savedLookVariations('${payload}',${row.id},'similar',this)">More like this</button>
   <button class="ghost" type="button" onclick="savedLookVariations('${payload}',${row.id},'inspiration',this)">Use as inspiration</button>
   <button class="ghost reaction-btn positive" type="button" onclick="reactToOutfit('${encodeURIComponent(JSON.stringify(o))}','Works for me',this)">✓ Works for me</button>
  </div>
  ${savedLookEditPanel(row)}
  <div id="savedLookVariations-${row.id}" class="saved-look-variations"></div>
 </article>`;
}

async function savedLookVariations(encoded,rowId,mode,button){
 const data=JSON.parse(decodeURIComponent(encoded));
 const box=$(`savedLookVariations-${rowId}`);
 if(!box)return;
 const original=button?.textContent||"More like this";
 if(button){button.disabled=true;button.textContent="Creating…";}
 box.innerHTML='<div class="visual-loading">Building variations from this saved look…</div>';

 const requestText=mode==="inspiration"
  ? `${data.request_text||""}\n\nUse this saved outfit as inspiration. Preserve the overall taste and level of polish, but feel free to change the colour palette and key pieces more substantially so it feels fresh rather than nearly identical.`
  : `${data.request_text||""}\n\nCreate close variations of this saved look. Keep the same overall character and make only useful changes.`;

 try{
  const x=await api("/api/stylist-v4/more-like-this",{
   method:"POST",headers:{"Content-Type":"application/json"},
   body:JSON.stringify({
    base_outfit:data.outfit,
    request_text:requestText,
    weather_context:data.weather_context||"",
    owned_only:false,
    max_options:3
   })
  });
  const baseIndex=5000+(Number(rowId)*10);
  box.innerHTML=(x.outfits||[]).map((o,j)=>renderV4Outfit(o,baseIndex+j,true,rowId)).join("")||
    '<div class="notice">No useful variations found.</div>';
 }catch(err){
  box.innerHTML=`<div class="notice">${esc(err.message)}</div>`;
 }finally{
  if(button){button.disabled=false;button.textContent=original;}
 }
}


async function deleteSavedLook(id){
 if(!confirm("Remove this saved look?"))return;
 await api(`/api/outfit-favourites/${id}`,{method:"DELETE"});
 localStorage.removeItem(userCacheKey("savedLooks"));
 loadSavedLooks();
}

function renderStoredMoreLike(baseIndex){
 const stored=latestStylistSession?.more_like?.[String(baseIndex)]||[];
 if(!stored.length)return "";
 return `<div class="more-like-heading"><small>MORE LIKE THIS</small><b>${stored.length} variation${stored.length===1?"":"s"} based on this look</b></div>`+
  stored.map((o,j)=>renderV4Outfit(o,1000+(Number(baseIndex)*10)+j,true,baseIndex)).join("");
}

function renderV4Outfit(o,index,isVariant=false,baseIndex=null){
 const pieces=(o.owned_garment_ids||[]).map(id=>garments.find(g=>g.id===id)).filter(Boolean);
 const pieceHtml=pieces.map(g=>`<div class="v4-piece">
  <img src="${garmentThumbUrl(g)}" loading="lazy" decoding="async" onload="stabiliseImagePaint(this)" alt="">
  <div><b>${esc((g.brand?g.brand+" ":"")+(g.garment_type||g.category||"Garment"))}</b><small>${esc([g.colour,g.material,g.labelled_size].filter(Boolean).join(" · "))}</small></div>
 </div>`).join("");

 const gap=o.missing_piece?`<div class="v4-missing"><b>Suggested addition:</b> ${esc(o.missing_piece)}<br><small>${esc(o.missing_piece_reason||"")}</small></div>`:"";
 const payload=encodeURIComponent(JSON.stringify(o));
 const rankLabel=isVariant?`Variation ${o.rank||""}`:`#${o.rank}`;

 setTimeout(()=>v4Visualise(payload,index,true),0);

 return `<div class="card v4-outfit${isVariant?" v4-variation":""}">
  <div class="row between"><div><span class="rank-pill">${esc(rankLabel)}</span><h3>${esc(o.label)}</h3></div><div class="v4-score"><b>${o.score}</b><span>/100</span></div></div>
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
   <button class="primary favourite-look-btn" type="button" onclick="saveFavouriteOutfit('${payload}',${index},this)">☆ Favourite</button>
   <button class="ghost reaction-btn positive" type="button" onclick="reactToOutfit('${payload}','Works for me',this)">✓ Works for me</button>
   <button class="ghost reaction-btn soft-negative" type="button" onclick="reactToOutfit('${payload}','Less like this',this)">↘ Less like this</button>
   ${!isVariant?`<button class="ghost replace-look-btn" type="button" onclick="replaceStylistOutfit(${index},this)">↻ Replace this outfit</button>`:""}
   <button class="ghost" type="button" onclick="v4Regenerate('${payload}',${index},true,this)">Regenerate image</button>
   <button class="ghost" type="button" onclick="v4Visualise('${payload}',${index},false)">See on model</button>
   ${!isVariant?`<button class="ghost more-like-btn" type="button" onclick="v4MoreLike('${payload}',${index},this)">More like this</button>`:""}
   ${o.missing_piece?`<button class="ghost find-piece-btn" type="button" onclick="v4FindPiece('${payload}',${index},this)">Find this piece</button>`:""}
  </div>
  <div id="v4Products-${index}" class="product-results v4-product-results"></div>
  <div id="v4Visual-${index}" class="model-visual"><div class="visual-loading">Creating your look…</div></div>
  ${!isVariant?`<div id="v4More-${index}" class="v4-more-results">${renderStoredMoreLike(index)}</div>`:""}
 </div>`;
}

async function replaceStylistOutfit(index,button,feedback=""){
 const session=latestStylistSession;
 const outfits=session?.result?.outfits||[];
 const base=outfits[index];
 if(!base)return;
 const original=button?.textContent||"↻ Replace this outfit";
 if(button){button.disabled=true;button.textContent="Finding another…"}
 const activity=`replace-look-${index}`;
 beginAppActivity(activity,"Finding another option…",feedback||"Keeping your brief, but taking this outfit in a different direction.","working");
 try{
  const x=await api("/api/stylist-v4/replace-one",{
   method:"POST",headers:{"Content-Type":"application/json"},
   body:JSON.stringify({
    base_outfit:base,
    request_text:session.request_text||"",
    feedback:feedback||"",
    weather_context:session.weather?.summary||"",
    owned_only:Boolean(session.owned_only),
    other_outfits:outfits.filter((_,i)=>i!==index)
   })
  });
  x.outfit.rank=base.rank||index+1;
  outfits[index]=x.outfit;
  session.result.summary=x.summary||session.result.summary;
  session.more_like={};
  persistStylistSession();
  v4VisualCache.clear();
  persistVisualCache();
  $("v4Results").innerHTML=stylistResultHtml(session.result);
  renderStylistRefineBar(true);
 }catch(err){alert(err.message)}
 finally{endAppActivity(activity);if(button){button.disabled=false;button.textContent=original}}
}

async function refineStylistSet(text){
 const refinement=String(text||"").trim();
 if(!refinement || !latestStylistSession)return;
 const session=latestStylistSession;
 const request=[session.request_text,`REFINEMENT FOR THIS SET: ${refinement}`].filter(Boolean).join("\n\n");
 const box=$("v4Results");
 beginAppActivity("refine-set","Refining your options…",refinement,"working");
 box.innerHTML='<div class="card v4-thinking"><span class="spinner"></span><div><b>Restyling the set…</b><small>Keeping the original occasion and applying your new preference.</small></div></div>';
 try{
  const x=await api("/api/stylist-v4",{
   method:"POST",headers:{"Content-Type":"application/json"},
   body:JSON.stringify({request_text:request,anchor_garment_id:null,owned_only:Boolean(session.owned_only),max_options:3})
  });
  session.result=x;
  session.refinement=refinement;
  session.more_like={};
  persistStylistSession();
  v4VisualCache.clear();persistVisualCache();
  box.innerHTML=stylistResultHtml(x);
  renderStylistRefineBar(true);
 }catch(err){box.innerHTML=`<div class="notice">${esc(err.message)}</div>`}
 finally{endAppActivity("refine-set")}
}

$("v4ApplyRefine")?.addEventListener("click",()=>{
 const input=$("v4RefineText");
 refineStylistSet(input?.value||"");
 if(input)input.value="";
});
document.querySelectorAll("[data-refine]").forEach(b=>b.addEventListener("click",()=>refineStylistSet(b.dataset.refine)));

async function v4Visualise(encoded,index,useMyLikeness,options={}){
 const activityKey=`image-${index}-${useMyLikeness?"me":"model"}`;

 const o=JSON.parse(decodeURIComponent(encoded));
 const box=$(`v4Visual-${index}`);
 const cacheKey=v4VisualCacheKey(o,useMyLikeness);

 if(!options.force && v4VisualCache.has(cacheKey)){
  const x=v4VisualCache.get(cacheKey);
  box.classList.remove("hidden");
  setDynamicImageHtml(box,`<img class="dynamic-ai-image" src="${x.image_path}" loading="eager" decoding="async" onload="stabiliseImagePaint(this)" alt="AI outfit visualisation"><div class="visual-caption"><b>${esc(x.label)}</b><br>${esc(x.notice)}</div>`);
  return;
 }

 if(options.force){
  v4VisualCache.delete(cacheKey);
  persistVisualCache();
 }

 box.classList.remove("hidden");
 beginAppActivity(activityKey,useMyLikeness?"Creating your outfit…":"Creating outfit image…","The image model is dressing the look. Image quality is unchanged.","image");
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
  persistVisualCache();
  setDynamicImageHtml(box,`<img class="dynamic-ai-image" src="${x.image_path}" loading="eager" decoding="async" onload="stabiliseImagePaint(this)" alt="AI outfit visualisation"><div class="visual-caption"><b>${esc(x.label)}</b><br>${esc(x.notice)}</div>`);
 }catch(err){
  box.innerHTML=`<div class="notice">${esc(err.message)}</div>`;
 }finally{
  endAppActivity(activityKey);
 }
}


async function v4Regenerate(encoded,index,useMyLikeness=true,button=null){
 const original=button?.textContent||"Regenerate image";
 if(button){button.disabled=true;button.textContent="Regenerating…";}
 try{
  await v4Visualise(encoded,index,useMyLikeness,{force:true});
 }finally{
  if(button){button.disabled=false;button.textContent=original;}
 }
}

async function v4MoreLike(encoded,index,button){
 const base=JSON.parse(decodeURIComponent(encoded));
 const box=$(`v4More-${index}`);
 if(!box)return;
 const original=button?.textContent||"More like this";
 if(button){button.disabled=true;button.textContent="Creating variations…";}

 box.innerHTML=`<div class="card more-like-working"><span class="spinner"></span><div><b>Building variations from this look…</b><small>I’ll keep the character of the outfit and make only useful changes.</small></div></div>`;

 try{
  const x=await api("/api/stylist-v4/more-like-this",{
   method:"POST",
   headers:{"Content-Type":"application/json"},
   body:JSON.stringify({
    base_outfit:base,
    request_text:latestStylistSession?.request_text||$("v4Request")?.value||"",
    weather_context:latestStylistSession?.weather?.summary||"",
    owned_only:Boolean(latestStylistSession?.owned_only),
    max_options:3
   })
  });

  latestStylistSession=latestStylistSession||{result:{outfits:[]}};
  latestStylistSession.more_like=latestStylistSession.more_like||{};
  latestStylistSession.more_like[String(index)]=x.outfits||[];
  persistStylistSession();

  const variants=x.outfits||[];
  box.innerHTML=variants.length
   ? `<div class="more-like-heading"><small>MORE LIKE THIS</small><b>${variants.length} variations based on this outfit</b><p>${esc(x.summary||"")}</p></div>`+
      variants.map((o,j)=>renderV4Outfit(o,1000+(Number(index)*10)+j,true,index)).join("")
   : `<div class="notice">I couldn't find a useful variation without weakening the original outfit.</div>`;
 }catch(err){
  box.innerHTML=`<div class="notice"><b>I couldn't create variations.</b><br>${esc(err.message)}</div>`;
 }finally{
  if(button){button.disabled=false;button.textContent=original;}
 }
}

async function v4FindPiece(encoded,index,button){
 const o=JSON.parse(decodeURIComponent(encoded));
 const box=$(`v4Products-${index}`);
 const original=button?.textContent||"Find this piece";
 if(button){button.disabled=true;button.innerHTML='<span class="inline-spinner" aria-hidden="true"></span> Searching…';}

 box.innerHTML=`<div class="retailer-search-state">
   <span class="retailer-search-spinner"></span>
   <div>
    <b>Searching UK retailers…</b>
    <p>I’m checking current products, prices and fit information for <strong>${esc(o.missing_piece||"this piece")}</strong>.</p>
    <small>Please wait — a live retailer search can take a little while.</small>
   </div>
  </div>`;
 requestAnimationFrame(()=>box.scrollIntoView({behavior:"smooth",block:"center"}));

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
  ${productWardrobeMatchStrip(p)}
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
 }finally{
  if(button){button.disabled=false;button.textContent=original;}
 }
}



function productWardrobeMatchStrip(p){
 const ids=(p.best_with_owned_ids||[]).map(Number).filter(Boolean);
 const pieces=ids.map(id=>garments.find(g=>g.id===id)).filter(Boolean).slice(0,5);
 if(!pieces.length)return "";
 return `<div class="product-owned-match"><small>WORKS WITH YOUR WARDROBE</small><div>${pieces.map(g=>`<img src="${garmentThumbUrl(g)}" loading="lazy" decoding="async" alt="${esc(g.garment_type||g.category||"Garment")}">`).join("")}</div></div>`;
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
  ${p.wardrobe_utility?`<div class="shopping-intel-note"><b>Wardrobe value:</b> ${esc(p.wardrobe_utility)}</div>`:""}
  <div class="shopping-intel-strip">
   <span class="duplicate-risk duplicate-${esc(p.duplicate_risk||"low")}">Duplicate risk: ${esc(p.duplicate_risk||"low")}</span>
   <span class="fit-confidence">Fit confidence: ${esc(p.fit_confidence||"low")}</span>
   <span class="audience-verified">${esc(p.audience==="unisex"?"Unisex":"Audience verified")}</span>
  </div>
  <div class="product-meta">${[p.colour,p.material,p.fit].filter(Boolean).map(esc).join(" · ")}</div>
  <small><b>Size:</b> ${esc(p.size_note||"Confirm sizing with retailer.")}</small>
  ${productWardrobeMatchStrip(p)}
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
  ${p.wardrobe_utility?`<div class="shopping-intel-note"><b>Wardrobe value:</b> ${esc(p.wardrobe_utility)}</div>`:""}
  <div class="shopping-intel-strip">
   <span class="duplicate-risk duplicate-${esc(p.duplicate_risk||"low")}">Duplicate risk: ${esc(p.duplicate_risk||"low")}</span>
   <span class="fit-confidence">Fit confidence: ${esc(p.fit_confidence||"low")}</span>
   <span class="audience-verified">${esc(p.audience==="unisex"?"Unisex":"Audience verified")}</span>
  </div>
  <div class="product-meta">${[p.colour,p.material,p.fit].filter(Boolean).map(esc).join(" · ")}</div>
  <small><b>Size:</b> ${esc(p.size_note||"Confirm sizing with retailer.")}</small>
  ${productWardrobeMatchStrip(p)}
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


function renderGapRecommendation(rec,index){
 gapRecommendationContexts.set(index,rec);
 const linked=(rec.owned_garment_ids||[]).map(id=>garments.find(g=>g.id===id)).filter(Boolean);
 const wardrobeStrip=linked.length?`<div class="gap-wardrobe-strip">${linked.slice(0,6).map(g=>`
  <div class="gap-owned-piece">${g.image_path?`<img src="${g.image_path}" alt="">`:""}<span>${esc((g.brand?g.brand+" ":"")+(g.garment_type||g.category||"Garment"))}</span></div>`).join("")}</div>`:"";
 const specs=[
  rec.ideal_colour&&`Colour: ${rec.ideal_colour}`,
  rec.ideal_material&&`Material: ${rec.ideal_material}`,
  rec.ideal_fit&&`Fit: ${rec.ideal_fit}`,
  rec.formality&&`Formality: ${rec.formality}`
 ].filter(Boolean);

 return `<article class="card gap-recommendation">
  <div class="row between gap-title-row"><div><span class="rank-pill">#${index+1}</span><h3>${esc(rec.title||"Recommended addition")}</h3></div>
   <div class="gap-score"><b>${esc(rec.wardrobe_synergy_score??"")}</b><small>/100 synergy</small></div></div>
  <p>${esc(rec.why_this_adds_value||"")}</p>
  ${specs.length?`<div class="gap-specs">${specs.map(s=>`<span>${esc(s)}</span>`).join("")}</div>`:""}
  <div class="shopping-intel-strip">
   <span class="purchase-role">${esc((rec.purchase_role||"new capability").replace(/_/g," "))}</span>
   <span class="duplicate-risk duplicate-${esc(rec.duplicate_risk||"low")}">Duplicate risk: ${esc(rec.duplicate_risk||"low")}</span>
  </div>
  ${rec.duplicate_reason?`<div class="shopping-intel-note"><b>Overlap check:</b> ${esc(rec.duplicate_reason)}</div>`:""}
  ${rec.versatility_note?`<div class="shopping-intel-note"><b>Versatility:</b> ${esc(rec.versatility_note)}</div>`:""}
  ${wardrobeStrip}
  ${(rec.outfit_ideas||[]).length?`<div class="gap-outfit-ideas"><small>HOW IT WORKS WITH YOUR WARDROBE</small>${rec.outfit_ideas.slice(0,3).map(x=>`<p>${esc(x.description||"")}</p>`).join("")}</div>`:""}
  ${rec.size_fit_guidance?`<div class="notice"><b>Fit guidance:</b> ${esc(rec.size_fit_guidance)}</div>`:""}
  <div class="gap-actions"><button class="primary gap-product-search-btn" type="button" data-gap-search="${index}">Find current products</button></div>
  <div id="gapProducts-${index}" class="product-results"></div>
 </article>`;
}

async function analyseWardrobeGaps(){
 const btn=$("analyseGaps"),box=$("gapResults");
 if(!btn||!box)return;
 const goal=$("shopGoal").value.trim();
 const original=btn.textContent;
 const controller=new AbortController();
 let timeoutId=null;

 btn.disabled=true;
 btn.innerHTML='<span class="inline-spinner" aria-hidden="true"></span> Analysing your wardrobe…';
 box.innerHTML=`<div class="shopping-working"><span class="retailer-search-spinner"></span><div>
  <b>Analysing your wardrobe…</b>
  <p>I’m comparing what you own with what you’re looking for, your fit information and how useful a new piece would be across your wardrobe.</p>
  <small>This can take a little while.</small></div></div>`;
 requestAnimationFrame(()=>box.scrollIntoView({behavior:"smooth",block:"center"}));

 const slowNote=setTimeout(()=>{
  const small=box.querySelector(".shopping-working small");
  if(small)small.textContent="Still working — your wardrobe is quite large, so this analysis can occasionally take longer.";
 },45000);

 try{
  timeoutId=setTimeout(()=>controller.abort(),105000);
  const r=await fetch("/api/wardrobe-gaps",{
   method:"POST",
   headers:{"Content-Type":"application/json"},
   body:JSON.stringify({
    goal:goal||"Identify the most useful addition to my wardrobe",
    budget:$("shopBudget").value||"",
    season:$("shopSeason").value||"",
    occasion:$("shopOccasion").value.trim(),
    shopping_mode:$("shopMode")?.value||"best_addition",
    max_recommendations:4
   }),
   signal:controller.signal
  });
  let x={};try{x=await r.json()}catch{}
  if(!r.ok)throw new Error(x.detail||`Wardrobe analysis failed (${r.status}).`);
  const recs=x.recommendations||[];
  gapRecommendationContexts.clear();
  box.innerHTML=`<div class="notice gap-summary"><b>Wardrobe analysis</b><p>${esc(x.summary||"")}</p></div>`+
   (recs.length?recs.map(renderGapRecommendation).join(""):'<div class="notice">The analysis completed but did not return a useful recommendation. Try making the request more specific.</div>');
 }catch(err){
  box.innerHTML=err.name==="AbortError"
   ? '<div class="notice"><b>The analysis took too long.</b><br>I stopped it after 105 seconds rather than leaving the page spinning. Please try again.</div>'
   : `<div class="notice"><b>I couldn't analyse the wardrobe.</b><br>${esc(err.message||"Something went wrong.")}</div>`;
 }finally{
  clearTimeout(slowNote);
  if(timeoutId)clearTimeout(timeoutId);
  btn.disabled=false;
  btn.textContent=original;
 }
}

async function searchGapProducts(rec,index,button){
 const box=$(`gapProducts-${index}`);
 if(!box)return;
 const original=button?.textContent||"Find current products";
 if(button){button.disabled=true;button.textContent="Searching retailers…";}
 box.innerHTML=`<div class="retailer-search-state"><span class="retailer-search-spinner"></span><div>
  <b>Searching UK retailers…</b>
  <p>I’m looking for current products matching <strong>${esc(rec.title||rec.search_phrase||"this recommendation")}</strong>.</p>
  <small>Please wait — this is a live retailer search.</small></div></div>`;
 requestAnimationFrame(()=>box.scrollIntoView({behavior:"smooth",block:"center"}));

 try{
  const x=await api("/api/source-products",{
   method:"POST",headers:{"Content-Type":"application/json"},
   body:JSON.stringify({
    search_phrase:rec.search_phrase||rec.title||"",
    shopping_spec:rec.shopping_spec||[rec.title,rec.ideal_colour,rec.ideal_material,rec.ideal_fit,rec.formality].filter(Boolean).join(". "),
    budget:$("shopBudget").value||"",
    category:rec.category||"",
    size_fit_guidance:rec.size_fit_guidance||"Use my saved profile and fit history where relevant.",
    owned_garment_ids:rec.owned_garment_ids||[]
   })
  });
  const products=x.products||[];
  if(!products.length){
   box.innerHTML=`<div class="notice">I couldn't find a sufficiently reliable current match. ${esc(x.search_note||"")}</div>`;
   return;
  }
  box.innerHTML=`<div class="retailer-search-complete"><span>Retailer search complete</span><small>${esc(x.search_note||"Current matches found.")}</small></div>`+
   products.map(p=>renderLiveProduct(p)).join("");
 }catch(err){
  box.innerHTML=`<div class="notice"><b>Retailer search couldn't finish.</b><br>${esc(err.message)}</div>`;
 }finally{
  if(button){button.disabled=false;button.textContent=original;}
 }
}


$("makeOutfits")?.addEventListener("click",()=>{
 const brief=($("outfit_brief")?.value||"").trim();
 const structured=[
  $("occasion")?.value,
  $("dress_code")?.value && `Dress code: ${$("dress_code").value}`,
  $("smartness")?.value && `Smartness: ${$("smartness").value}`,
  $("outfit_season")?.value && $("outfit_season").value!=="Auto / current" ? `Season: ${$("outfit_season").value}` : "",
  $("location")?.value ? `Location: ${$("location").value}` : "",
  $("context_notes")?.value
 ].filter(Boolean).join(". ");
 const request=brief || structured;
 if(!request){alert("Tell me what you're dressing for.");return;}
 $("v4Request").value=request;
 if($("location")?.value)$("v4Location").value=$("location").value;
 if($("wardrobe_mode")?.value==="My wardrobe only")$("v4Shopping").value="owned";
 go("stylistv4");
 setTimeout(()=>$("runStylistV4")?.click(),60);
});

const analyseGapsBtn=$("analyseGaps");
if(analyseGapsBtn)analyseGapsBtn.addEventListener("click",analyseWardrobeGaps);

$("gapResults")?.addEventListener("click",e=>{
 const button=e.target.closest("[data-gap-search]");
 if(!button)return;
 const index=Number(button.dataset.gapSearch);
 const rec=gapRecommendationContexts.get(index);
 if(!rec){
  const box=$(`gapProducts-${index}`);
  if(box)box.innerHTML='<div class="notice">This recommendation is no longer available. Please run Analyse my wardrobe again.</div>';
  return;
 }
 searchGapProducts(rec,index,button);
});

const runStylistV4Btn=$("runStylistV4");
if(runStylistV4Btn){
 runStylistV4Btn.addEventListener("click",async()=>{
  const text=$("v4Request").value.trim();
  if(!text){alert("Tell me what you are dressing for.");return;}

  const box=$("v4Results");
  const controller=new AbortController();
  let statusTimer=null;
  let timeoutTimer=null;
  let weatherData=null;
  const location=($("v4Location")?.value||"").trim();
  const when=($("v4When")?.value||"today").trim()||"today";

  runStylistV4Btn.disabled=true;
  beginAppActivity("stylist-plan","Stylist is working…","Checking your request, wardrobe, fit history and context.","working");

  if(location){
   const weatherBox=$("v4WeatherStatus");
   weatherBox.classList.remove("hidden");
   weatherBox.innerHTML='<span class="spinner"></span> Checking the live forecast before styling…';
   try{
    weatherData=await api("/api/weather-context",{
     method:"POST",headers:{"Content-Type":"application/json"},
     body:JSON.stringify({location,when})
    });
    weatherBox.innerHTML=`<b>Forecast:</b> ${esc(weatherData.summary||"")}${weatherData.styling_context?`<small>${esc(weatherData.styling_context)}</small>`:""}`;
   }catch(err){
    weatherBox.innerHTML=`<b>Weather lookup unavailable.</b> <small>${esc(err.message)} I’ll style from your written request instead.</small>`;
   }
  }else{
   $("v4WeatherStatus")?.classList.add("hidden");
  }

  box.innerHTML='<div class="card v4-thinking"><span class="spinner"></span><div><b>Styling from your wardrobe…</b><small id="v4WaitNote">This usually takes under a minute.</small></div></div>';

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
     request_text:weatherData
       ? `${text}\n\nLIVE WEATHER CONTEXT FOR ${location} (${when}): ${weatherData.summary}. ${weatherData.styling_context||""} Temperature range: ${weatherData.temperature_low_c??"?"}–${weatherData.temperature_high_c??"?"}°C. Rain: ${weatherData.rain||"unknown"}. Wind: ${weatherData.wind||"unknown"}.`
       : text,
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
   latestStylistSession={
    request_text:text,
    location,
    when,
    weather:weatherData,
    owned_only:$("v4Shopping").value==="owned",
    result:x,
    more_like:{},
    saved_at:new Date().toISOString()
   };
   persistStylistSession();

   box.innerHTML=stylistResultHtml(x);
   renderStylistRefineBar(Boolean((x.outfits||[]).length));

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
   endAppActivity("stylist-plan");
   runStylistV4Btn.disabled=false;
  }
 });
}
setupV4Dictation();
setupAiDictation("buildLookDictate","buildLookContext","buildLookDictationStatus");
setupAiDictation("productLookDictate","productLookOccasion","productLookDictationStatus");

setupSmartDictation("packSmartDictate","packSmartDictationStatus","packing");
setupSmartDictation("stylistSmartDictate","stylistSmartDictationStatus","stylist");
setupSmartDictation("outfitSmartDictate","outfitSmartDictationStatus","outfit");
setupSmartDictation("shoppingSmartDictate","shoppingSmartDictationStatus","shopping");
setupSmartDictation("profileSmartDictate","profileSmartDictationStatus","profile");
setupSmartDictation("garmentSmartDictate","garmentSmartDictationStatus","garment");

init();


async function organisePackingBrief(){
 const brief=($("pack_brief")?.value||"").trim();
 if(!brief)return null;
 if(brief===lastPackingBriefParsed)return {cached:true};

 updateAppActivity("packing-brief","Understanding your trip…","Pulling out destination, dates, activities and dress needs.","working");
 try{
  const parsed=await api("/api/voice-form/parse",{
   method:"POST",headers:{"Content-Type":"application/json"},
   body:JSON.stringify({mode:"packing",transcript:brief})
  });
  const map=SMART_DICTATION_MAPS.packing||{};
  for(const item of parsed.fields||[]){
   const id=map[item.field];
   if(id)setFieldValue(id,normaliseSmartValue("packing",item.field,item.value));
  }
  packingDateSync();
  lastPackingBriefParsed=brief;
  return parsed;
 }catch(err){
  // Keep the free-text brief usable even if structured extraction temporarily fails.
  return null;
 }finally{
  endAppActivity("packing-brief");
 }
}

function packingDateSync(){
 const start=$("pack_start_date")?.value;
 const end=$("pack_end_date")?.value;
 if(!start||!end)return;
 const a=new Date(start+"T12:00:00");
 const b=new Date(end+"T12:00:00");
 if(Number.isNaN(a.getTime())||Number.isNaN(b.getTime())||b<a)return;
 $("pack_days").value=String(Math.round((b-a)/86400000)+1);
}
$("pack_brief")?.addEventListener("input",()=>{lastPackingBriefParsed=""});
$("pack_start_date")?.addEventListener("change",packingDateSync);
$("pack_end_date")?.addEventListener("change",packingDateSync);

function safeExternalUrl(url){
 try{
  const x=new URL(url);
  return ["http:","https:"].includes(x.protocol)?x.href:"";
 }catch{return ""}
}

function renderTripContext(ctx){
 if(!ctx)return "";
 const hasTemps=ctx.temperature_low_c!==null&&ctx.temperature_low_c!==undefined&&
                ctx.temperature_high_c!==null&&ctx.temperature_high_c!==undefined;
 const temps=hasTemps?`${Math.round(ctx.temperature_low_c)}–${Math.round(ctx.temperature_high_c)}°C`:"";
 const labels={
  forecast:"Current forecast",
  seasonal:"Seasonal conditions",
  "user-provided":"Your weather note",
  unavailable:"Weather lookup unavailable"
 };
 const places=(ctx.named_places||[]).length
  ? `<div class="trip-place-list">${ctx.named_places.map(p=>`<div>
      <b>${esc(p.name)}</b><span>${esc(p.place_type||"")}</span>
      <p>${esc(p.dress_context||p.note||"")}</p>
      <small>${p.evidence_level==="verified"?"Verified dress information":p.evidence_level==="inferred"?"Stylist inference from venue context":"General destination context"}</small>
     </div>`).join("")}</div>`
  : "";
 const sources=(ctx.sources||[]).filter(s=>safeExternalUrl(s.url)).slice(0,6);
 return `<div class="card trip-context-card">
  <div class="row between">
   <div><small class="eyebrow">TRIP RESEARCH</small><h3>${esc(ctx.destination_summary||"Destination context")}</h3></div>
   <span class="trip-mode">${esc(labels[ctx.weather_mode]||ctx.weather_mode||"")}</span>
  </div>
  <div class="trip-weather"><b>${esc([temps,ctx.weather_summary].filter(Boolean).join(" · "))}</b><p>${esc(ctx.packing_weather_note||"")}</p></div>
  ${ctx.dress_context?`<div class="trip-context-line"><b>Dress context</b><p>${esc(ctx.dress_context)}</p></div>`:""}
  ${ctx.activity_context?`<div class="trip-context-line"><b>Practical context</b><p>${esc(ctx.activity_context)}</p></div>`:""}
  ${places}
  ${sources.length?`<details class="trip-sources"><summary>Research sources</summary>${sources.map(s=>`<a href="${safeExternalUrl(s.url)}" target="_blank" rel="noopener"><b>${esc(s.title)}</b><small>${esc(s.supports)}</small></a>`).join("")}</details>`:""}
  ${ctx.research_note?`<small class="trip-research-disclaimer">${esc(ctx.research_note)}</small>`:""}
 </div>`;
}

function packingOutfitObject(d){
 return {
  label:[d.day,d.occasion].filter(Boolean).join(" — ")||"Trip outfit",
  score:90,
  owned_garment_ids:d.garment_ids||[],
  missing_piece:"",
  missing_piece_reason:"",
  why_it_works:d.note||"",
  occasion_fit:d.occasion||"",
  weather_fit:currentTripContext?.weather_summary||"",
  formality_fit:currentTripContext?.dress_context||"",
  style_note:d.reuse_note||""
 };
}

function packingDayKey(d,index){
 return d.date||d.day||`day-${index+1}`;
}

function renderPackingLook(d,index){
 const pieces=(d.garment_ids||[]).map(id=>{
  const g=garments.find(z=>z.id===id);
  return g?`<div class="mini-garment">
   <img src="/api/garments/${g.id}/image" alt="">
   <span>${esc(g.garment_type||g.category)}</span>
  </div>`:"";
 }).join("");

 const when=[d.time_of_day,d.occasion].filter(Boolean).join(" · ");
 return `<article class="pack-look-card" data-pack-look="${esc(d.look_id||String(index))}">
  <div class="pack-look-head">
   <div>
    <small>${esc(d.time_of_day||"Outfit")}</small>
    <h5>${esc(d.occasion||"Planned outfit")}</h5>
   </div>
   <span class="look-number">Look ${index+1}</span>
  </div>
  <div class="mini-strip pack-look-strip">${pieces}</div>
  <p>${esc(d.note||"")}</p>
  ${d.reuse_note?`<small class="reuse-note">↻ ${esc(d.reuse_note)}</small>`:""}
  <div class="pack-look-actions">
   <button class="primary" type="button" onclick="packingVisualise(${index},false,this)">Show this look on me</button>
   <button class="ghost" type="button" onclick="packingVisualise(${index},true,this)">Regenerate this image</button>
   <button class="ghost" type="button" onclick="packingMoreLike(${index},this)">More like this look</button>
   <button class="ghost" type="button" onclick="replacePackingLook(${index},this)">↻ Replace this look</button>
  </div>
  <div id="packingVisual-${index}" class="packing-visual"></div>
  <div id="packingMore-${index}" class="packing-more"></div>
 </article>`;
}


function packingChecklistFromPlan(plan){
 const checked=currentTripChecklist||{};
 const items=(plan?.packing_list||[]).map(p=>({
  key:`g-${p.garment_id}`,garment_id:p.garment_id,checked:Boolean(checked[`g-${p.garment_id}`])
 }));
 const travelIds=new Set();
 (plan?.outfit_plan||[]).filter(x=>(x.time_of_day||"").toLowerCase()==="travel" || (x.occasion||"").toLowerCase().includes("travel"))
  .forEach(x=>(x.garment_ids||[]).forEach(id=>travelIds.add(Number(id))));
 return {items,travelIds};
}

function renderPackingChecklist(plan){
 const {items,travelIds}=packingChecklistFromPlan(plan);
 const packed=items.filter(x=>x.checked).length;
 const rows=items.map(item=>{
  const g=garments.find(x=>x.id===item.garment_id);if(!g)return "";
  const travel=travelIds.has(g.id);
  return `<label class="trip-check-item ${item.checked?"checked":""}">
   <input type="checkbox" data-trip-check="${item.key}" ${item.checked?"checked":""}>
   <img src="${garmentThumbUrl(g)}" loading="lazy" alt="">
   <span><b>${esc((g.brand?g.brand+" ":"")+(g.garment_type||g.category||"Garment"))}</b><small>${travel?"Wear on travel day":"Pack in luggage"}</small></span>
  </label>`;
 }).join("");
 return `<div class="card trip-checklist-card">
  <div class="row between"><div><small class="eyebrow">PACKING CHECKLIST</small><h3>${packed}/${items.length} ready</h3></div><span class="pill">${esc(currentPackingRequest?.luggage||"Luggage not specified")}</span></div>
  <div class="trip-checklist">${rows}</div>
 </div>`;
}

function currentTripPayload(){
 return {
  title:[currentPackingRequest?.destination,currentPackingRequest?.start_date].filter(Boolean).join(" · ")||"Saved trip",
  destination:currentPackingRequest?.destination||"",
  start_date:currentPackingRequest?.start_date||"",
  end_date:currentPackingRequest?.end_date||"",
  luggage:currentPackingRequest?.luggage||"",
  request:currentPackingRequest||{},
  trip_context:currentTripContext||{},
  plan:currentPackingPlan||{},
  checklist:currentTripChecklist||{}
 };
}

async function saveCurrentTrip(button){
 if(!currentPackingPlan)return;
 const original=button?.textContent||"Save trip";
 if(button){button.disabled=true;button.textContent="Saving…"}
 try{
  const url=currentSavedTripId?`/api/saved-trips/${currentSavedTripId}`:"/api/saved-trips";
  const method=currentSavedTripId?"PUT":"POST";
  const x=await api(url,{method,headers:{"Content-Type":"application/json"},body:JSON.stringify(currentTripPayload())});
  currentSavedTripId=x.id;
  if(button)button.textContent="✓ Trip saved";
  await loadSavedTrips();
 }catch(err){alert(err.message);if(button){button.disabled=false;button.textContent=original}}
}

async function loadSavedTrips(){
 const box=$("savedTripsResults");if(!box)return;
 try{
  const rows=await api("/api/saved-trips");
  if(!rows.length){box.innerHTML='<small class="muted-copy">Save a packing plan and it will appear here.</small>';return}
  box.innerHTML=rows.map(t=>`<div class="saved-trip-row">
   <div><b>${esc(t.title||t.destination||"Saved trip")}</b><small>${esc([t.start_date,t.end_date,t.luggage].filter(Boolean).join(" · "))}</small></div>
   <div><button class="ghost" type="button" onclick="openSavedTrip(${t.id})">Open</button><button class="text-button danger-text" type="button" onclick="deleteSavedTrip(${t.id})">Remove</button></div>
  </div>`).join("");
  window._savedTrips=rows;
 }catch(err){box.innerHTML=`<div class="notice">${esc(err.message)}</div>`}
}
$("refreshSavedTrips")?.addEventListener("click",loadSavedTrips);

function fillTripForm(req={}){
 const map={
  destination:"pack_destination",trip_brief:"pack_brief",start_date:"pack_start_date",end_date:"pack_end_date",
  days:"pack_days",trip_type:"pack_trip_type",weather:"pack_weather",activities:"pack_activities",
  dress_needs:"pack_dress_needs",laundry:"pack_laundry",notes:"pack_notes",luggage:"pack_luggage"
 };
 Object.entries(map).forEach(([k,id])=>{if($(id)&&req[k]!==undefined&&req[k]!==null)$(id).value=String(req[k])});
 if($("pack_shopping"))$("pack_shopping").value=req.shopping_allowed===false?"No":"Yes";
}

function openSavedTrip(id){
 const t=(window._savedTrips||[]).find(x=>x.id===Number(id));if(!t)return;
 currentSavedTripId=t.id;
 currentPackingRequest=t.request||{};
 currentTripContext=t.trip_context||{};
 currentPackingPlan=t.plan||{};
 currentTripChecklist=t.checklist||{};
 fillTripForm(currentPackingRequest);
 renderPackingPlan(currentPackingPlan);
 $("packingResults")?.scrollIntoView({behavior:"smooth",block:"start"});
}

async function deleteSavedTrip(id){
 if(!confirm("Remove this saved trip?"))return;
 await api(`/api/saved-trips/${id}`,{method:"DELETE"});
 if(currentSavedTripId===id)currentSavedTripId=null;
 loadSavedTrips();
}

async function refreshCurrentTripWeather(button){
 if(!currentPackingRequest)return;
 const original=button?.textContent||"Refresh weather";
 if(button){button.disabled=true;button.textContent="Refreshing…"}
 try{
  currentTripContext=await api("/api/trip-context",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify(currentPackingRequest)});
  if(currentPackingPlan)renderPackingPlan(currentPackingPlan);
  if(currentSavedTripId)await saveCurrentTrip(null);
 }catch(err){alert(err.message)}
 finally{if(button){button.disabled=false;button.textContent=original}}
}

function renderPackingPlan(x){
 const box=$("packingResults");
 currentPackingPlan=x;
 currentTripContext=x.trip_context||currentTripContext||{};
 packingVisualCache.clear();

 const packed=(x.packing_list||[]).map(p=>{
  const g=garments.find(z=>z.id===p.garment_id);
  return g?`<div class="outfitPiece">
   <img src="/api/garments/${g.id}/image" alt="">
   <div><b>${esc((g.brand?g.brand+" ":"")+(g.garment_type||g.category))}</b><small>${esc(p.why_pack)} · wear ~${p.wear_count}×</small></div>
  </div>`:"";
 }).join("");

 const groups=[];
 const groupMap=new Map();
 (x.outfit_plan||[]).forEach((d,index)=>{
  const key=packingDayKey(d,index);
  if(!groupMap.has(key)){
   const group={key,date:d.date||"",day:d.day||key,looks:[]};
   groupMap.set(key,group);
   groups.push(group);
  }
  groupMap.get(key).looks.push({d,index});
 });

 const planHtml=groups.map(group=>`<section class="pack-day-group">
  <div class="pack-day-group-head">
   <div><small>${esc(group.date||"")}</small><h4>${esc(group.day)}</h4></div>
   <span>${group.looks.length} ${group.looks.length===1?"look":"looks"}</span>
  </div>
  <div class="pack-day-look-list">
   ${group.looks.map(({d,index})=>renderPackingLook(d,index)).join("")}
  </div>
 </section>`).join("");

 const missing=(x.missing_items||[]).length
  ? `<div class="notice"><b>Useful gaps:</b> ${x.missing_items.map(esc).join(" · ")}</div>`
  : "";

 box.innerHTML=
  `<div class="card trip-save-actions"><div><small class="eyebrow">TRIP PLAN</small><b>${esc(currentPackingRequest?.destination||x.destination||"Your trip")}</b></div><div><button class="ghost" type="button" onclick="refreshCurrentTripWeather(this)">Refresh weather</button><button class="primary" type="button" onclick="saveCurrentTrip(this)">${currentSavedTripId?"Update saved trip":"Save trip"}</button></div></div>`+
  renderTripContext(currentTripContext)+
  `<div class="notice packing-summary"><b>Your capsule</b><p>${esc(x.summary||"")}</p>${x.capsule_strategy?`<small>${esc(x.capsule_strategy)}</small>`:""}</div>
   ${renderPackingChecklist(x)}
   <div class="card"><h3>Pack these</h3>${packed}</div>
   <div class="card pack-plan-card">
    <div class="row between"><h3>Outfit plan</h3><span id="packingVisualPrep" class="pill">Preparing visuals…</span></div>
    ${planHtml}
   </div>
   ${missing}
   <div class="card"><b>Packing tip</b><p>${esc(x.packing_tip||"")}</p></div>`;
 setTimeout(preGeneratePackingVisuals,120);
}

function preGeneratePackingVisuals(){
 const outfits=currentPackingPlan?.outfit_plan||[];
 const status=$("packingVisualPrep");
 if(status && outfits.length)status.textContent=`Preparing ${outfits.length} outfit visual${outfits.length===1?"":"s"} in the background…`;
 let completed=0;
 outfits.forEach((d,index)=>{
  const key=`packing-auto-${d.look_id||index}-${(d.garment_ids||[]).join("-")}`;
  enqueueBackgroundVisual(key,async()=>{
   await packingVisualise(index,false,null,true);
   completed++;
   if(status){
    status.textContent=completed>=outfits.length
     ? "Outfit visuals ready"
     : `Preparing visuals… ${completed}/${outfits.length}`;
   }
  });
 });
}

async function packingVisualise(index,force=false,button=null,silent=false){
 const d=currentPackingPlan?.outfit_plan?.[index];
 if(!d)return;
 const box=$(`packingVisual-${index}`);
 const key=[
  currentPackingPlan?.destination||"",
  d.look_id||`look-${index}`,
  d.date||d.day||"",
  d.time_of_day||"",
  d.occasion||"",
  d.note||"",
  (d.garment_ids||[]).join(",")
 ].join("|");
 const original=button?.textContent||"Show on me";

 if(!force&&packingVisualCache.has(key)){
  const x=packingVisualCache.get(key);
  setDynamicImageHtml(box,`<div class="packing-generated"><img class="dynamic-ai-image" src="${x.image_path}" loading="eager" decoding="async" onload="stabiliseImagePaint(this)" alt="Packing outfit on you"><small>${esc(x.notice||"")}</small></div>`);
  return;
 }

 if(button){button.disabled=true;button.textContent=force?"Regenerating…":"Creating…";}
 const activityKey=`packing-image-${index}`;
 if(!silent)beginAppActivity(activityKey,"Creating your trip outfit…",`${d.day||"Trip look"} · ${d.occasion||""}`,"image");
 box.innerHTML=`<div class="visual-loading">${silent?"Preparing this look in the background…":"Creating your personalised outfit visual…"}</div>`;

 try{
  const x=await api("/api/outfit-visualisation",{
   method:"POST",headers:{"Content-Type":"application/json"},
   body:JSON.stringify({
    garment_ids:d.garment_ids||[],
    label:[d.day,d.time_of_day,d.occasion].filter(Boolean).join(" — "),
    reason:`THIS IS ONE DISTINCT PACKING LOOK ONLY. Use exactly these garment IDs for this look: ${(d.garment_ids||[]).join(", ")}. ${d.note||""}`,
    occasion:[d.time_of_day,d.occasion].filter(Boolean).join(" · "),
    temperature_c:null,
    use_my_likeness:true,
    requested_extra_piece:""
   })
  });
  packingVisualCache.set(key,x);
  setDynamicImageHtml(box,`<div class="packing-generated"><img class="dynamic-ai-image" src="${x.image_path}" loading="eager" decoding="async" onload="stabiliseImagePaint(this)" alt="Packing outfit on you"><small>${esc(x.notice||"")}</small></div>`);
 }catch(err){
  box.innerHTML=`<div class="notice">${esc(err.message)}</div>`;
 }finally{
  if(!silent)endAppActivity(activityKey);
  if(button){button.disabled=false;button.textContent=original;}
 }
}

async function replacePackingLook(index,button,feedback=""){
 const d=currentPackingPlan?.outfit_plan?.[index];if(!d)return;
 const original=button?.textContent||"↻ Replace this look";
 if(button){button.disabled=true;button.textContent="Finding another…"}
 const activity=`packing-replace-${index}`;
 beginAppActivity(activity,"Replacing this trip look…",`${d.day||"Trip"} · ${d.occasion||""}`,"working");
 try{
  const base=packingOutfitObject(d);
  const others=(currentPackingPlan.outfit_plan||[]).filter((_,i)=>i!==index).map(packingOutfitObject);
  const x=await api("/api/stylist-v4/replace-one",{
   method:"POST",headers:{"Content-Type":"application/json"},
   body:JSON.stringify({
    base_outfit:base,
    request_text:[currentPackingRequest?.trip_brief,currentTripContext?.dress_context].filter(Boolean).join(" "),
    feedback:feedback||`Replace only the ${d.day||""} ${d.occasion||""} look. Keep it suitable for this exact day and trip.`,
    weather_context:currentTripContext?.weather_summary||"",
    owned_only:true,
    other_outfits:others
   })
  });
  const o=x.outfit;
  currentPackingPlan.outfit_plan[index]={
   ...d,
   garment_ids:o.owned_garment_ids||[],
   note:o.why_it_works||d.note,
   reuse_note:o.style_note||d.reuse_note
  };
  packingVisualCache.clear();
  renderPackingPlan(currentPackingPlan);
  if(currentSavedTripId)await saveCurrentTrip(null);
 }catch(err){alert(err.message)}
 finally{endAppActivity(activity);if(button){button.disabled=false;button.textContent=original}}
}

async function packingMoreLike(index,button){
 const d=currentPackingPlan?.outfit_plan?.[index];
 if(!d)return;
 const box=$(`packingMore-${index}`);
 const original=button?.textContent||"More like this";
 if(button){button.disabled=true;button.textContent="Creating…";}
 beginAppActivity(`packing-more-${index}`,"Styling alternatives…","Keeping the same trip context and character of this look.","working");
 box.innerHTML='<div class="visual-loading">Building a couple of useful variations…</div>';

 try{
  const base=packingOutfitObject(d);
  const x=await api("/api/stylist-v4/more-like-this",{
   method:"POST",headers:{"Content-Type":"application/json"},
   body:JSON.stringify({
    base_outfit:base,
    request_text:`Packing for ${$("pack_destination").value}. This is one distinct ${d.time_of_day||""} look for ${d.occasion||"the trip"}. Keep it separate from the other packing-plan outfits. ${d.note||""}`,
    weather_context:currentTripContext?.weather_summary||"",
    owned_only:true,max_options:3
   })
  });
  box.innerHTML=(x.outfits||[]).map((o,j)=>{
   const pieces=(o.owned_garment_ids||[]).map(id=>{
    const g=garments.find(z=>z.id===id);
    return g?`<span>${esc((g.brand?g.brand+" ":"")+(g.garment_type||g.category))}</span>`:"";
   }).join("");
   const payload=encodeURIComponent(JSON.stringify(o));
   const visualIndex=3000+index*10+j;
   return `<div class="packing-alt">
    <b>${esc(o.label||`Variation ${j+1}`)}</b>
    <div class="packing-alt-pieces">${pieces}</div>
    <p>${esc(o.why_it_works||"")}</p>
    <button class="ghost" type="button" onclick="v4Visualise('${payload}',${visualIndex},true)">Show variation on me</button>
    <div id="v4Visual-${visualIndex}" class="model-visual hidden"></div>
   </div>`;
  }).join("")||'<div class="notice">No useful variation found.</div>';
 }catch(err){
  box.innerHTML=`<div class="notice">${esc(err.message)}</div>`;
 }finally{
  endAppActivity(`packing-more-${index}`);
  if(button){button.disabled=false;button.textContent=original;}
 }
}

let currentWeekPlan=null;
let currentWeekWeather=null;

function weekOutfitObject(d){
 return {
  label:[d.day,d.occasion].filter(Boolean).join(" — ")||"Weekly outfit",
  score:90,owned_garment_ids:d.garment_ids||[],
  missing_piece:"",missing_piece_reason:"",
  why_it_works:d.note||"",occasion_fit:d.occasion||"",
  weather_fit:currentWeekWeather?.summary||"",
  formality_fit:"",style_note:d.reuse_note||""
 };
}

function renderWeekLook(d,index){
 const pieces=(d.garment_ids||[]).map(id=>{
  const g=garments.find(x=>x.id===id);
  return g?`<div class="mini-garment"><img src="${garmentThumbUrl(g)}" loading="lazy" alt=""><span>${esc(g.garment_type||g.category)}</span></div>`:"";
 }).join("");
 return `<article class="pack-look-card week-look-card">
  <div class="pack-look-head"><div><small>${esc(d.date||d.time_of_day||"")}</small><h5>${esc([d.day,d.occasion].filter(Boolean).join(" · "))}</h5></div><span class="look-number">${index+1}</span></div>
  <div class="mini-strip pack-look-strip">${pieces}</div>
  <p>${esc(d.note||"")}</p>${d.reuse_note?`<small class="reuse-note">↻ ${esc(d.reuse_note)}</small>`:""}
  <div class="pack-look-actions">
   <button class="primary" type="button" onclick="weekVisualise(${index},this)">Show this look on me</button>
   <button class="ghost" type="button" onclick="replaceWeekLook(${index},this)">↻ Replace this day</button>
  </div>
  <div id="weekVisual-${index}" class="packing-visual"></div>
 </article>`;
}

function renderWeekPlan(plan){
 currentWeekPlan=plan;
 const box=$("weekPlanResults");if(!box)return;
 const looks=plan?.outfit_plan||[];
 box.innerHTML=`<div class="notice packing-summary"><b>Your week at a glance</b><p>${esc(plan.summary||"")}</p>${plan.capsule_strategy?`<small>${esc(plan.capsule_strategy)}</small>`:""}</div>
  <div class="week-plan-grid">${looks.map(renderWeekLook).join("")}</div>
  ${plan.packing_tip?`<div class="card"><b>Prep once</b><p>${esc(plan.packing_tip)}</p></div>`:""}`;
}

async function weekVisualise(index,button){
 const d=currentWeekPlan?.outfit_plan?.[index];if(!d)return;
 const box=$(`weekVisual-${index}`);
 const original=button?.textContent||"Show this look on me";
 if(button){button.disabled=true;button.textContent="Creating…"}
 try{
  const x=await api("/api/outfit-visualisation",{
   method:"POST",headers:{"Content-Type":"application/json"},
   body:JSON.stringify({garment_ids:d.garment_ids||[],label:[d.day,d.occasion].filter(Boolean).join(" — "),
    reason:d.note||"",occasion:d.occasion||"Workday",temperature_c:null,use_my_likeness:true,requested_extra_piece:""})
  });
  setDynamicImageHtml(box,`<div class="packing-generated"><img class="dynamic-ai-image" src="${x.image_path}" loading="eager" decoding="async" onload="stabiliseImagePaint(this)" alt=""><small>${esc(x.notice||"")}</small></div>`);
 }catch(err){box.innerHTML=`<div class="notice">${esc(err.message)}</div>`}
 finally{if(button){button.disabled=false;button.textContent=original}}
}

async function replaceWeekLook(index,button){
 const d=currentWeekPlan?.outfit_plan?.[index];if(!d)return;
 const original=button?.textContent||"↻ Replace this day";
 if(button){button.disabled=true;button.textContent="Finding another…"}
 try{
  const x=await api("/api/stylist-v4/replace-one",{
   method:"POST",headers:{"Content-Type":"application/json"},
   body:JSON.stringify({
    base_outfit:weekOutfitObject(d),
    request_text:$("weekBrief")?.value||"",
    feedback:`Replace only this ${d.day||"day"} outfit. Keep it suitable for ${d.occasion||"the planned day"}.`,
    weather_context:currentWeekWeather?.summary||"",
    owned_only:!$("weekShopping")?.checked,
    other_outfits:(currentWeekPlan.outfit_plan||[]).filter((_,i)=>i!==index).map(weekOutfitObject)
   })
  });
  const o=x.outfit;
  currentWeekPlan.outfit_plan[index]={...d,garment_ids:o.owned_garment_ids||[],note:o.why_it_works||d.note,reuse_note:o.style_note||d.reuse_note};
  renderWeekPlan(currentWeekPlan);
 }catch(err){alert(err.message)}
 finally{if(button){button.disabled=false;button.textContent=original}}
}

$("buildWeekPlan")?.addEventListener("click",async()=>{
 const brief=($("weekBrief")?.value||"").trim();
 if(!brief){alert("Tell me what your week looks like.");return}
 const box=$("weekPlanResults");
 const location=($("weekLocation")?.value||"").trim();
 const start=$("weekStart")?.value||"";
 const days=Number($("weekDays")?.value||5);
 beginAppActivity("week-plan","Planning your week…","Balancing variety, practicality and what you actually wear.","working");
 box.innerHTML='<div class="card v4-thinking"><span class="spinner"></span><div><b>Planning the week…</b><small>Choosing a distinct outfit for each day.</small></div></div>';
 try{
  currentWeekWeather=null;
  if(location){
   try{
    currentWeekWeather=await api("/api/weather-context",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({location,when:start?`week starting ${start}`:"this week"})});
   }catch{}
  }
  const plan=await api("/api/plan-my-week",{
   method:"POST",headers:{"Content-Type":"application/json"},
   body:JSON.stringify({
    start_date:start,days,location,brief,
    work_context:$("weekWorkContext")?.value||"",
    dress_needs:$("weekDressNeeds")?.value||"",
    weather_context:currentWeekWeather||{},
    shopping_allowed:Boolean($("weekShopping")?.checked)
   })
  });
  renderWeekPlan(plan);
 }catch(err){box.innerHTML=`<div class="notice">${esc(err.message)}</div>`}
 finally{endAppActivity("week-plan")}
});

$("makePackingPlan")?.addEventListener("click",async()=>{
 const box=$("packingResults");
 currentSavedTripId=null;
 currentTripChecklist={};
 const tripBrief=($("pack_brief")?.value||"").trim();
 if(!tripBrief && !$("pack_destination").value.trim()){
  alert("Tell me about your trip first.");
  return;
 }

 await organisePackingBrief();
 packingDateSync();

 const destination=$("pack_destination").value.trim();
 const payload={
  destination,
  trip_brief:tripBrief,
  start_date:$("pack_start_date").value||"",
  end_date:$("pack_end_date").value||"",
  days:Number($("pack_days").value||5),
  trip_type:$("pack_trip_type").value,
  weather:$("pack_weather").value,
  activities:$("pack_activities").value,
  dress_needs:$("pack_dress_needs").value,
  laundry:$("pack_laundry").value,
  shopping_allowed:$("pack_shopping").value==="Yes",
  luggage:$("pack_luggage")?.value||"",
  notes:$("pack_notes").value
 };
 currentPackingRequest=payload;

 beginAppActivity("packing-plan","Researching your trip…","Checking weather, destination and any named hotels, restaurants or venues.","research");
 box.innerHTML='<div class="card v4-thinking"><span class="spinner"></span><div><b>Researching the trip…</b><small>I’ll use a real forecast when the dates are close enough; otherwise I’ll use seasonal conditions.</small></div></div>';

 try{
  currentTripContext=await api("/api/trip-context",{
   method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify(payload)
  });

  box.innerHTML=renderTripContext(currentTripContext)+
   '<div class="card v4-thinking"><span class="spinner"></span><div><b>Building your capsule…</b><small>Now matching the trip context to your actual wardrobe.</small></div></div>';
  updateAppActivity("packing-plan","Building your capsule…","Choosing versatile pieces and planning intentional re-wears.","working");

  const x=await api("/api/help-me-pack",{
   method:"POST",headers:{"Content-Type":"application/json"},
   body:JSON.stringify({...payload,trip_context:currentTripContext})
  });
  x.destination=destination||tripBrief;
  renderPackingPlan(x);
 }catch(err){
  box.innerHTML=`<div class="notice"><b>I couldn't complete the packing plan.</b><br>${esc(err.message)}</div>`;
 }finally{
  endAppActivity("packing-plan");
 }
});

$("packingResults")?.addEventListener("change",async e=>{
 const input=e.target.closest("[data-trip-check]");if(!input)return;
 currentTripChecklist[input.dataset.tripCheck]=input.checked;
 renderPackingPlan(currentPackingPlan);
 if(currentSavedTripId){
  try{await api(`/api/saved-trips/${currentSavedTripId}`,{method:"PUT",headers:{"Content-Type":"application/json"},body:JSON.stringify(currentTripPayload())})}catch{}
 }
});
$("quickWardrobeAnalyse")?.addEventListener("click",analyseQuickWardrobe);
$("quickWardrobeDictate")?.addEventListener("click",startQuickWardrobeDictation);
$("quickWardrobeResults")?.addEventListener("input",e=>{
 const input=e.target.closest("[data-quick-key]");
 if(input){
  const i=Number(input.dataset.quickIndex);
  if(quickWardrobeItems[i])quickWardrobeItems[i][input.dataset.quickKey]=input.value;
 }
 const include=e.target.closest("[data-quick-include]");
 if(include){
  const i=Number(include.dataset.quickInclude);
  if(quickWardrobeItems[i])quickWardrobeItems[i]._include=include.checked;
 }
});
$("quickWardrobeResults")?.addEventListener("click",e=>{
 const remove=e.target.closest("[data-quick-remove]");
 if(!remove)return;
 quickWardrobeItems.splice(Number(remove.dataset.quickRemove),1);
 renderQuickWardrobeResults();
});

$("buildLookPicker")?.addEventListener("click",e=>{
 const tile=e.target.closest("[data-build-garment]");if(!tile)return;
 const id=Number(tile.dataset.buildGarment);
 buildLookSelected.has(id)?buildLookSelected.delete(id):buildLookSelected.add(id);
 renderBuildLookPicker();
});
$("buildLookTray")?.addEventListener("click",e=>{
 if(e.target.closest("#clearBuildLook")){buildLookSelected.clear();renderBuildLookPicker();$("buildLookVisual").innerHTML="";$("buildLookCritique").innerHTML="";return;}
 if(e.target.closest("#showBuiltLook"))showBuiltLook();
 const c=e.target.closest("[data-look-critique]");if(c)critiqueBuiltLook(c.dataset.lookCritique);
});
$("buildLookVisual")?.addEventListener("click",e=>{
 if(e.target.closest("#refreshBuiltLook"))showBuiltLook();
 const c=e.target.closest("[data-look-critique]");if(c)critiqueBuiltLook(c.dataset.lookCritique);
});
$("productLookBuild")?.addEventListener("click",buildProductWardrobeLooks);
$("productLookResults")?.addEventListener("click",e=>{
 const b=e.target.closest("[data-product-look-try]");if(!b)return;
 tryProductWardrobeLook(b.dataset.productLookTry,Number(b.dataset.productLookIndex),b);
});
