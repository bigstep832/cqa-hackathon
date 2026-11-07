"use strict";
const $ = (id) => document.getElementById(id);
const token = document.querySelector('meta[name="review-token"]').content;
let state = null, currentId = null, current = null, annotationDirty = false, selectionDirty = false, busy = false, loadId = 0;
const roles = {operator:"Оператор",client:"Клиент",bot:"Бот",system:"Система"};
function el(tag, text = "", attrs = {}) {
  const node = document.createElement(tag);
  node.textContent = text;
  for (const [key, value] of Object.entries(attrs)) node.setAttribute(key, value);
  return node;
}
function notice(text, error = false) {
  $("notice").textContent = text;
  $("notice").classList.toggle("error", error);
  $("notice").setAttribute("role", error ? "alert" : "status");
}
async function api(path, body) {
  const response = await fetch(path, body === undefined ? {cache:"no-store"} : {
    method:"POST", headers:{"Content-Type":"application/json","X-Review-Token":token}, body:JSON.stringify(body)
  });
  const data = await response.json();
  if (!response.ok) throw new Error(typeof data.detail === "string" ? data.detail : "Не удалось выполнить действие.");
  return data;
}
function controls() {
  for (const node of document.querySelectorAll("input,select,textarea,button")) node.disabled = busy;
  for (const id of ["save-selection","preview","freeze","grouped-by","seed","holdout-groups"]) $(id).disabled = busy || Boolean(state?.frozen);
}
async function run(action) {
  if (busy) return;
  busy = true; controls();
  notice("Выполняется локальное действие…");
  try { await action(); }
  catch (error) { notice(error.message || "Сервер недоступен. Введённые данные остаются в форме.", true); }
  finally { busy = false; controls(); }
}
function changed() { annotationDirty = true; $("save-state").textContent = "Есть несохранённые изменения"; }
function overview() {
  const selected = state.rows.filter(r => r.selected);
  $("all-count").textContent = state.rows.length;
  $("selected-count").textContent = selected.length;
  $("done-count").textContent = selected.filter(r => r.complete).length;
  $("selection-tools").hidden = state.frozen;
  $("export-tools").hidden = !state.frozen;
  $("grouped-by").value = state.grouped_by || "";
  $("seed").value = state.seed; $("holdout-groups").value = state.holdout_groups;
  if (state.frozen) {
    $("step").textContent = "02 / НЕЗАВИСИМАЯ РАЗМЕТКА";
    $("stage-title").textContent = "Прочитайте. Оцените. Подтвердите.";
    $("stage-help").textContent = "Состав выборки зафиксирован. Заполните ответы по рубрике и отметьте реплики, которые их подтверждают.";
    $("split-counts").textContent = `Настройка: ${selected.filter(r=>r.split==="tune").length} · Проверка: ${selected.filter(r=>r.split==="holdout").length} · Ревизия ${state.revision}`;
  }
  renderList(); controls();
}
function renderList() {
  const root = $("list"); root.replaceChildren();
  const query = $("search").value.trim().toLowerCase();
  const rows = state.rows.filter(r => (!state.frozen || r.selected) &&
    (!$("language").value || r.language === $("language").value) &&
    (!$("channel").value || r.channel === $("channel").value) &&
    (!query || r.conversation_id.toLowerCase().includes(query)));
  $("list-count").textContent = rows.length;
  if (!rows.length) root.append(el("p", "По этим фильтрам диалогов нет.", {class:"hint"}));
  for (const row of rows) {
    const box = el("article", "", {class:`dialog-row${row.conversation_version_id === currentId ? " active" : ""}`});
    const head = el("div", "", {class:"dialog-row-head"});
    if (!state.frozen) {
      const wrap = el("label", "", {class:"row-check"});
      const check = el("input", "", {type:"checkbox", "aria-label":`Включить ${row.conversation_id}`});
      check.checked = row.selected;
      check.addEventListener("change", () => { row.selected = check.checked; selectionDirty = true; $("selected-count").textContent = state.rows.filter(r=>r.selected).length; });
      wrap.append(check); head.append(wrap);
    }
    const open = el("button", row.conversation_id, {type:"button",class:"open-dialog"});
    open.setAttribute("aria-pressed", String(row.conversation_version_id === currentId));
    open.addEventListener("click", () => openRecord(row.conversation_version_id)); head.append(open); box.append(head);
    const meta = el("div", "", {class:"row-meta"});
    for (const text of [row.language, row.channel === "voice call" ? "Звонок" : "Чат", `${row.message_count} репл.`]) meta.append(el("span", text, {class:"badge"}));
    if (row.split) meta.append(el("span", row.split === "tune" ? "Настройка" : "Проверка", {class:"badge"}));
    if (row.complete) meta.append(el("span", "Завершён", {class:"badge done"})); box.append(meta);
    if (!state.frozen) {
      const label = el("label", "Группа сценария", {class:"group-label"});
      const input = el("input", "", {placeholder:"Например: вход в кабинет", "aria-label":`Группа ${row.conversation_id}`});
      input.value = row.scenario_group || "";
      input.addEventListener("input", () => { row.scenario_group = input.value.trim() || null; selectionDirty = true; });
      label.append(input); box.append(label);
    } else box.append(el("p", row.scenario_group, {class:"hint"}));
    root.append(box);
  }
}
async function openRecord(id, force = false) {
  if (busy && !force) return;
  if (!force && annotationDirty && !window.confirm("Перейти без сохранения изменений разметки?")) return;
  const generation = ++loadId;
  if (!force) { busy = true; controls(); }
  try {
    const data = await api(`/api/record/${encodeURIComponent(id)}`);
    if (generation !== loadId) return;
    currentId = id; current = data; annotationDirty = false;
    $("record-title").textContent = data.conversation.conversation_id;
    $("record-meta").textContent = `${data.conversation.language} · ${data.conversation.channel === "voice call" ? "Звонок" : "Чат"} · ${data.conversation.messages.length} реплик`;
    const messages = $("messages"); messages.replaceChildren();
    for (const message of data.conversation.messages) {
      const item = el("article", "", {class:`message ${message.speaker}`, "data-order":message.order});
      const header = el("header"); header.append(el("span",`#${message.order+1}`,{class:"message-number"}), el("strong",roles[message.speaker] || message.speaker),el("span",message.timestamp_raw || ""));
      item.append(header, el("p",message.text)); messages.append(item);
    }
    messages.scrollTop = 0; renderAnnotation(); renderList(); controls();
  } catch(error) { notice(error.message, true); }
  finally { if (!force) { busy = false; controls(); } }
}
function field(text, node) { const label = el("label", text); label.append(node); return label; }
function textarea(value, cls) { const input = el("textarea", "", {class:cls, rows:"2"}); input.value = value || ""; return input; }
function evidenceSelect(value, label) {
  const select = el("select", "", {"aria-label":label}); select.append(el("option","Выберите реплику",{value:""}));
  for (const m of current.conversation.messages) select.append(el("option",`#${m.order+1} · ${roles[m.speaker]}`,{value:m.message_id}));
  select.value = value || ""; return select;
}
function highlight(start, end) {
  const a = current.conversation.messages.findIndex(m=>m.message_id===start);
  const b = current.conversation.messages.findIndex(m=>m.message_id===end);
  for (const node of $("messages").children) node.classList.toggle("highlight", a>=0 && b>=a && Number(node.dataset.order)>=a && Number(node.dataset.order)<=b);
  const first = $("messages").querySelector(".highlight"); if (first) first.scrollIntoView({block:"nearest"});
}
function addRange(root, evidence = null) {
  const row = el("div", "", {class:"evidence-row"});
  const start = evidenceSelect(evidence?.start_message_id,"Начало доказательства"); start.className="range-start";
  const end = evidenceSelect(evidence?.end_message_id,"Конец доказательства"); end.className="range-end";
  for (const input of [start,end]) input.addEventListener("change", () => highlight(start.value,end.value));
  const remove = el("button","×",{type:"button",class:"remove-range","aria-label":"Удалить диапазон"});
  remove.addEventListener("click",()=>{if(busy)return;row.remove();changed();});
  row.append(field("От реплики",start),field("До реплики",end),remove); root.append(row);
}
function renderAnnotation() {
  const annotation = current.annotation;
  $("annotation-form").hidden = !annotation; $("annotation-empty").hidden = Boolean(annotation);
  if (!annotation) return;
  const root = $("criteria"); root.replaceChildren();
  for (const criterion of current.checklist.criteria) {
    const saved = annotation.answers.find(a=>a.criterion_id===criterion.id);
    const card = el("fieldset","",{class:"criterion","data-criterion":criterion.id});
    const legend = el("legend"); legend.append(el("h3",`${criterion.id} · ${criterion.title}`)); card.append(legend);
    card.append(el("p",criterion.description,{class:"criterion-description"}));
    const select = el("select","",{class:"answer-select"}); select.append(el("option","Выберите ответ",{value:""}));
    for (const option of criterion.options) select.append(el("option",option.label,{value:JSON.stringify({type:criterion.answer_type,value:option.value})}));
    if (criterion.allow_na) select.append(el("option","N/A — неприменимо",{value:"na"}));
    select.value = saved.answer ? (saved.answer.type === "na" ? "na" : JSON.stringify(saved.answer)) : "";
    const help = el("p","",{class:"option-help"});
    const reason = textarea(saved.answer?.type === "na" ? saved.answer.reason : "","reason");
    const reasonLabel = field("Почему неприменимо",reason); reasonLabel.className="na-reason";
    function optionChanged() {
      reasonLabel.classList.toggle("visible",select.value==="na");
      const selected = select.value && select.value!=="na" ? JSON.parse(select.value) : null;
      help.textContent = select.value==="na" ? criterion.na_condition : selected ? criterion.options.find(o=>o.value===selected.value)?.description || "" : `Вес: ${criterion.weight ?? "отдельный флаг"}. Выберите ответ самостоятельно.`;
    }
    select.addEventListener("change",optionChanged); optionChanged();
    card.append(field("Ответ",select),help,reasonLabel,field("Обоснование",textarea(saved.explanation,"explanation")));
    card.append(el("p","Подтверждающие реплики",{class:"evidence-heading"}));
    const ranges = el("div","",{class:"ranges"});
    for (const evidence of saved.evidence.length ? saved.evidence : [null]) addRange(ranges,evidence);
    const add = el("button","Добавить диапазон",{type:"button",class:"add-range"}); add.addEventListener("click",()=>{if(busy)return;addRange(ranges);changed();});
    card.append(ranges,add);
    const details = el("details"); details.open = Boolean(saved.ambiguity_note || saved.resolution_note);
    details.append(el("summary","Спорный случай"),field("В чём неоднозначность",textarea(saved.ambiguity_note,"ambiguity")),field("Как решили спор",textarea(saved.resolution_note,"resolution"))); card.append(details); root.append(card);
  }
  $("annotated-by").value = annotation.review.annotated_by || "";
  $("language-understood").checked = annotation.review.language_understood;
  $("reviewed-by").value = annotation.review.reviewed_by || "";
  $("review-confirmed").checked = false;
  $("save-state").textContent = state.rows.find(r=>r.conversation_version_id===currentId)?.complete ? "Разметка завершена и сохранена" : "Черновик · ответы сохраняются только по кнопке";
}
function selectionBody() {
  return {revision:state.revision, selections:state.rows.map(({conversation_version_id,selected,scenario_group})=>({conversation_version_id,selected,scenario_group})),seed:Number($("seed").value),holdout_groups:Number($("holdout-groups").value),grouped_by:$("grouped-by").value.trim() || null};
}
function annotationBody(finish) {
  const answers = [...$("criteria").children].map(card=>{
    const value = card.querySelector(".answer-select").value;
    const answer = value === "na" ? {type:"na",reason:card.querySelector(".reason").value.trim()} : value ? JSON.parse(value) : null;
    const evidence = [...card.querySelectorAll(".evidence-row")].map(row=>({conversation_version_id:currentId,start_message_id:row.querySelector(".range-start").value,end_message_id:row.querySelector(".range-end").value})).filter(row=>row.start_message_id || row.end_message_id);
    return {criterion_id:card.dataset.criterion,answer,explanation:card.querySelector(".explanation").value.trim() || null,evidence,ambiguity_note:card.querySelector(".ambiguity").value.trim() || null,resolution_note:card.querySelector(".resolution").value.trim() || null};
  });
  return {revision:state.revision,conversation_version_id:currentId,answers,annotated_by:$("annotated-by").value.trim() || null,language_understood:$("language-understood").checked,reviewed_by:$("reviewed-by").value.trim() || null,review_confirmed:$("review-confirmed").checked,finish};
}
$("save-selection").addEventListener("click",()=>run(async()=>{state=await api("/api/selection",selectionBody());selectionDirty=false;overview();notice("Выбор и группы сохранены. Ответы ещё не заполняются.");}));
$("preview").addEventListener("click",()=>run(async()=>{
  const result=await api("/api/preview",selectionBody());
  const format=part=>`${part.count} диалогов (${Object.entries(part.languages).map(([k,v])=>`${k}: ${v}`).join(", ")}; ${Object.entries(part.channels).map(([k,v])=>`${k==="voice call"?"звонки":"чаты"}: ${v}`).join(", ")})`;
  $("preview-result").textContent=`Настройка: ${format(result.tune)}. Проверка: ${format(result.holdout)}.`;
  notice("Предпросмотр готов. Состав ещё не зафиксирован.");
}));
$("freeze").addEventListener("click",()=>{
  if (!window.confirm("Зафиксировать выбранные диалоги и группы? После этого их нельзя будет менять в этой рабочей папке.")) return;
  run(async()=>{state=await api("/api/freeze",selectionBody());selectionDirty=false;overview();notice("Выборка зафиксирована. Начните независимую разметку.");const row=state.rows.find(r=>r.selected);if(row)await openRecord(row.conversation_version_id,true);});
});
for (const [id,finish] of [["save-draft",false],["finish",true]]) $(id).addEventListener("click",()=>run(async()=>{
  state=await api("/api/annotation",annotationBody(finish));annotationDirty=false;overview();await openRecord(currentId,true);notice(finish ? "Разметка завершена. Проверено заполнение; смысл ответов подтверждает человек." : "Черновик сохранён. Можно продолжить позже.");
}));
for (const split of ["tune","holdout"]) $(`export-${split}`).addEventListener("click",()=>run(async()=>{
  if(annotationDirty)throw new Error("Сначала сохраните изменения текущего диалога.");
  const result=await api("/api/export",{revision:state.revision,split}); notice(`Экспортировано ${result.count} диалогов. Приватная папка: ${result.folder}`);
}));
for (const id of ["search","language","channel"]) $(id).addEventListener("input",()=>state && renderList());
for (const id of ["grouped-by","seed","holdout-groups"]) $(id).addEventListener("input",()=>{selectionDirty=true;});
$("selection-tools").addEventListener("input",()=>{$("preview-result").textContent="Параметры изменены. Проверьте разделение ещё раз.";});
$("list").addEventListener("input",()=>{if(!state.frozen)$("preview-result").textContent="Выбор или группы изменены. Проверьте разделение ещё раз.";});
$("annotation-form").addEventListener("input",changed);
$("annotation-form").addEventListener("submit",event=>event.preventDefault());
window.addEventListener("beforeunload",event=>{if(annotationDirty || selectionDirty){event.preventDefault();event.returnValue="";}});
run(async()=>{state=await api("/api/state");overview();notice(state.frozen ? "Рабочая папка восстановлена. Выберите диалог, чтобы продолжить." : "Данные загружены. Группы и ответы заполняет человек; автоматически ничего не выставлено.");});
