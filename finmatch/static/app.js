const $ = (s) => document.querySelector(s);
const NOOP = { onclick: null, addEventListener() {}, style: {}, classList: { toggle() {} } };
const $o = (s) => document.querySelector(s) || NOOP; // 绑定事件专用：元素不存在时静默跳过
const esc = (s) => String(s ?? "").replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
// ---- 角色：客户经理 / 政府人员 ----
const ROLE_PAGES = {
  manager: ["products", "chat", "profile", "gov", "report", "solutions"],
  gov: ["profile", "gov", "report", "solutions"],
};
let role = ROLE_PAGES[localStorage.getItem("finmatch_role")] ? localStorage.getItem("finmatch_role") : "manager";
async function api(path, opts = {}) {
  opts.headers = { ...(opts.headers || {}), "X-Role": role };
  const r = await fetch(path, opts);
  let j = null;
  try { j = await r.json(); } catch (e) { /* 非 JSON 响应 */ }
  if (!r.ok) {
    let m = j && (j.error || j.detail);
    if (Array.isArray(m)) m = m.map((x) => (x.loc ? x.loc.slice(1).join(".") + "：" : "") + (x.msg || JSON.stringify(x))).join("；");
    else if (m && typeof m === "object") m = JSON.stringify(m);
    throw new Error(m || ("请求失败 " + r.status));
  }
  return j;
}
const tagFor = (c) => c === "准入符合" ? "ok" : c === "准入不符" ? "bad" : "warn";

// ---- 路由 ----
const loaders = { products: loadProducts, chat: initChat, gov: loadGov, report: loadReport, solutions: loadSolutions, system: loadSystem };
function applyRole() {
  const allowed = ROLE_PAGES[role];
  document.querySelectorAll("#nav a").forEach((a) => (a.style.display = allowed.includes(a.dataset.p) ? "" : "none"));
  document.querySelectorAll("#roleSwitch button").forEach((b) => b.classList.toggle("on", b.dataset.role === role));
}
function route() {
  const allowed = ROLE_PAGES[role];
  let p = (location.hash || "").slice(1);
  if (!allowed.includes(p)) { p = allowed[0]; if (location.hash !== "#" + p) { location.hash = "#" + p; return; } }
  applyRole();
  document.querySelectorAll(".page").forEach((e) => e.classList.toggle("active", e.id === p));
  document.querySelectorAll("#nav a").forEach((e) => e.classList.toggle("active", e.dataset.p === p));
  if (loaders[p]) loaders[p]();
}
window.addEventListener("hashchange", route);
$("#roleSwitch").onclick = (e) => {
  const r = e.target.dataset.role;
  if (!r || r === role) return;
  role = r;
  localStorage.setItem("finmatch_role", r);
  location.hash = "#" + ROLE_PAGES[r][0];
  route();
};

// ---- 产品管理 ----
async function loadProducts() {
  const { products } = await api("/api/products");
  if (!products.length) { $("#productTable").innerHTML = "<p class='hint'>产品表为空。请先上传说明书 PDF。</p>"; return; }
  const rows = products.map((p) => {
    const c = p.conditions;
    const v = (k) => (c[k] ? c[k].value : "—");
    return `<tr><td>${esc(p.product_code)}</td><td><b>${esc(p.product_name)}</b><div class="src">${esc(p.institution)}</div></td>
      <td>${p.amount_min}–${p.amount_max} 万</td><td>${p.rate_min}%–${p.rate_max}%</td><td>${p.term_min}–${p.term_max} 个月</td>
      <td>${esc(p.purposes.join("、"))}</td><td>${esc(p.regions.join("、") || "不限")}</td><td>${esc(p.industries.join("、") || "不限")}</td>
      <td>${esc(p.guarantees.join("、"))}</td><td>${esc(p.repay_method)}</td><td>${p.subsidy_supported ? "支持" : "不支持"}</td>
      <td>${esc(v("rating"))}</td><td>${esc(v("insured"))}</td><td>${esc(v("founded"))} 年</td><td>${esc(v("tax_paid"))} 万</td>
      <td>${c.tech ? "要求" : "—"}</td><td>${c.taxpayer ? esc(c.taxpayer.value) : "—"}</td>
      <td class="src">${esc(p.source_file)}<br>第 ${p.upload_count} 次上传<br>${esc(p.updated_at)}</td>
      <td><button class="ghost" onclick="delProduct('${esc(p.product_code)}')">移除</button></td></tr>`;
  }).join("");
  $("#productTable").innerHTML = `<p class="small">已入库 ${products.length} 份。表内数字来自说明书第三条至第六条。</p>
    <div style="overflow:auto"><table><tr><th>编号</th><th>产品</th><th>额度</th><th>年化利率</th><th>期限</th><th>用途</th><th>区域</th><th>行业</th><th>担保</th><th>还款</th><th>制造业贴息</th><th>纳税等级≥</th><th>参保≥</th><th>成立满</th><th>纳税额≥</th><th>科技资质</th><th>纳税人资格</th><th>来源</th><th></th></tr>${rows}</table></div>`;
}
async function delProduct(code) {
  if (!confirm("从产品表移除 " + code + "？移除后不再参与预审。")) return;
  await api("/api/products/" + encodeURIComponent(code), { method: "DELETE" });
  loadProducts();
}
$o("#btnUpload").onclick = async () => {
  const files = $("#pdfs").files;
  const out = [];
  for (const f of files) {
    const fd = new FormData(); fd.append("file", f);
    try { const j = await api("/api/products/upload", { method: "POST", body: fd }); out.push("✓ " + f.name + "：" + j.message); }
    catch (e) { out.push("✗ " + f.name + "：" + e.message); }
  }
  $("#uploadMsg").className = "msg"; $("#uploadMsg").innerText = out.join("\n") || "请先选择 PDF 文件";
  $("#uploadMsg").style.whiteSpace = "pre-wrap";
  loadProducts();
};
$o("#btnSamples").onclick = async () => {
  const j = await api("/api/products/upload-samples", { method: "POST" });
  $("#uploadMsg").className = "msg ok"; $("#uploadMsg").innerText = "已入库 " + j.ingested.length + " 份" + (j.errors.length ? "，失败 " + j.errors.length + " 份" : "");
  loadProducts();
};

// ---- 对话工作台 ----
function bubble(role, content, extra) {
  const d = document.createElement("div");
  d.className = "bubble " + role;
  d.innerText = content;
  if (extra) d.appendChild(extra);
  $("#log").appendChild(d);
  $("#log").scrollTop = $("#log").scrollHeight;
}
function traceBox(out) {
  const wrap = document.createElement("div");
  wrap.style.whiteSpace = "normal";
  let html = "";
  const r = out.result;
  if (r) {
    html += `<details open><summary>十一份产品预审明细（${r.results.length} 份）</summary><table><tr><th>产品</th><th>结论</th><th>原因</th></tr>` +
      r.results.map((x) => `<tr><td>${esc(x.product_name)}</td><td><span class="tag ${tagFor(x.conclusion)}">${esc(x.conclusion)}</span></td><td>${esc(x.reasons.concat(x.unknowns).join("、") || "—")}</td></tr>`).join("") + "</table></details>";
    if (r.cards.length) {
      html += `<div class="cards">` + r.cards.map((c) => `<div class="card"><h4>${c.rank}. ${esc(c.product_name)}</h4>
        额度区间 ${esc(c.amount_range)}<br>年化利率区间 ${esc(c.rate_range)}<br>担保方式 ${esc(c.guarantee)}　还款方式 ${esc(c.repay_method)}
        ${c.subsidy ? `<br><span class="tag ok">${esc(c.subsidy_note)}</span>` : ""}<div class="small">${esc(c.note)}</div></div>`).join("") + "</div>";
    }
    if (r.solution_id) html += `<p class="small">方案 ${esc(r.solution_id)}（待确认时可点下方按钮）</p>`;
  }
  const t = out.trace || {};
  const u = t.usage || {};
  const head = t.trace_id ? `<div class="small">链路 ${esc(t.trace_id)} · ${t.total_ms} ms` +
    (u.calls ? ` · 模型 ${u.calls} 次 ${u.total_tokens} tokens（≈¥${u.cost_cny}，估算）` : " · 未调用模型") +
    (t.error ? ` · <span class="tag bad">出错节点 ${esc(t.error.node)}</span>` : "") +
    ((t.degraded || []).length ? ` · <span class="tag warn">降级 ${t.degraded.length} 处</span>` : "") + "</div>" : "";
  const flow = (t.flow || []).length ? `<div class="small">状态：${esc(t.flow.join(" → "))}</div>` : "";
  const spans = (t.spans || []).length ? `<div class="small">节点耗时：${t.spans.map((x) => esc(x.node + (x.agent ? "/" + x.agent : "") + " " + x.ms + "ms" + (x.status === "error" ? " ✗" : ""))).join("；")}</div>` : "";
  const plan = (t.plan || []).length ? `<div class="small">计划：${t.plan.map((p) => esc(p.step + (p.status === "done" ? "✓" : p.status === "skipped" ? "（跳过：" + (p.reason || "") + "）" : ""))).join(" ")}</div>` : "";
  html += `<details><summary>调用过程（工具 ${(t.tools || []).length} 次 · 规则 ${(t.rules || []).length} 条）</summary>${head}${flow}${plan}${spans}
    <table><tr><th>工具</th><th>参数</th><th>返回</th></tr>${(t.tools || []).map((x) => `<tr><td>${esc(x.name)}</td><td class="small">${esc(JSON.stringify(x.args))}</td><td>${esc(x.result_summary)}${x.attempts > 1 ? "（重试 " + x.attempts + " 次）" : ""}</td></tr>`).join("")}</table>
    <div class="small">规则：${esc((t.rules || []).join("；"))}</div><div class="small">Skill：${esc((t.skills || []).join("、"))}</div></details>`;
  wrap.innerHTML = html;
  return wrap;
}
// ---- 对话目录：按企业名称命名，可新增对话 ----
let currentSid = localStorage.getItem("finmatch_sid") || "";
const jsonPost = (body) => ({ method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });

let convCache = [];
const fmtTime = (t) => (t || "").replace("T", " ").slice(5, 16);
function renderConvs() {
  const q = (($("#convSearch") || {}).value || "").trim();
  const list = convCache.filter((c) => !q || c.title.includes(q));
  $("#convCount").innerText = convCache.length + " 个会话";
  $("#convList").innerHTML = list.length ? list.map((c) =>
    `<li data-sid="${esc(c.session_id)}" class="${c.session_id === currentSid ? "active" : ""}" title="${esc(c.title)}">
      <div class="t"><span>${esc(c.title)}</span>${c.pending_auth ? '<span class="tag warn">待授权</span>' : ""}</div>
      <div class="m">${c.turns} 轮 · 更新 ${esc(fmtTime(c.updated_at))}</div>
      <div class="l">${c.last ? esc(c.last) : "（还没有对话）"}</div></li>`).join("") : '<li class="empty">没有匹配的会话</li>';
}
async function refreshConvs() {
  convCache = (await api("/api/conversations")).conversations;
  renderConvs();
  const cur = convCache.find((c) => c.session_id === currentSid);
  if (cur) setHead(cur);
  return convCache;
}
function setHead(c) {
  $("#convTitle").innerText = c.title;
  $("#convMeta").innerText = c.created_at ? "新建于 " + c.created_at.replace("T", " ").slice(0, 16) + " · " + (c.turns ?? 0) + " 轮对话" : "";
}
function welcome() {
  $("#log").innerHTML = '<div class="welcome">在这个会话里可以连续多轮提问，后一句只会修改你说到的条件。<br>例如：华峻精密制造有限公司要流动资金 100 万，期限 12 个月</div>';
}
function authCard(pa) {
  const d = document.createElement("div");
  d.className = "auth-card";
  d.innerHTML = `<b>需要企业数据授权</b><div class="small">「${esc(pa.name)}」尚未授权，授权前不会调取其税务、社保、司法和历史融资数据，也不会预审。授权范围：工商、税务、社保、司法、历史融资。</div>
    <div class="row"><button data-d="grant">同意授权并继续预审</button><button class="ghost" data-d="deny">不授权</button></div>`;
  d.querySelectorAll("button").forEach((b) => (b.onclick = async () => {
    d.querySelectorAll("button").forEach((x) => (x.disabled = true));
    try {
      const out = await api("/api/conversations/" + encodeURIComponent(currentSid) + "/authorize", jsonPost({ decision: b.dataset.d }));
      d.remove();
      bubble("user", "（页面操作）" + (b.dataset.d === "grant" ? "已授权" : "不授权"));
      showReply(out);
    } catch (e) { bubble("assistant", "出错：" + e.message); d.querySelectorAll("button").forEach((x) => (x.disabled = false)); }
  }));
  return d;
}
function solCard(sol) {
  const d = document.createElement("div");
  d.className = "auth-card sol-card";
  const rec = sol.recommended || [];
  const info = rec.length ? `准入符合 ${sol.eligible_count} 个产品，推荐名单：${esc(rec.map((p) => p.name).join("、"))}` : "无准入符合产品";
  d.innerHTML = `<b>方案 ${esc(sol.id)}（${esc(sol.enterprise)}）待确认</b><div class="small">${info}，请确认或放弃。</div>
    <div class="row"><button data-a="confirm">确认方案</button><button class="ghost" data-a="abandon">放弃</button></div>`;
  d.querySelectorAll("button").forEach((b) => (b.onclick = async () => {
    d.querySelectorAll("button").forEach((x) => (x.disabled = true));
    try {
      const out = await api("/api/conversations/" + encodeURIComponent(currentSid) + "/solution", jsonPost({ action: b.dataset.a }));
      d.remove();
      showReply(out);
    } catch (e) { bubble("assistant", "出错：" + e.message); d.querySelectorAll("button").forEach((x) => (x.disabled = false)); }
  }));
  return d;
}
function showSol(sol) {
  document.querySelectorAll(".sol-card").forEach((n) => n.remove());
  if (sol && sol.status === "待确认" && role === "manager") { $("#log").appendChild(solCard(sol)); $("#log").scrollTop = $("#log").scrollHeight; }
}
function showReply(out) {
  bubble("assistant", out.reply, traceBox(out));
  document.querySelectorAll(".auth-card").forEach((n) => n.remove());
  showSol(out.solution);
  if (out.pending_auth) { $("#log").appendChild(authCard(out.pending_auth)); $("#log").scrollTop = $("#log").scrollHeight; }
  refreshConvs().catch(() => {});
}
async function openConv(sid) {
  currentSid = sid;
  localStorage.setItem("finmatch_sid", sid);
  const c = await api("/api/conversations/" + encodeURIComponent(sid));
  $("#convTitle").innerText = c.title;
  $("#log").innerHTML = "";
  if (!c.messages.length) welcome();
  c.messages.forEach((m) => bubble(m.role, m.content, m.role === "assistant" ? traceBox({ trace: m.trace }) : null));
  if (c.pending_auth) $("#log").appendChild(authCard(c.pending_auth));
  showSol(c.solution);
  await refreshConvs();
}
let creating = null; // 新建会话尚未返回时，发送要等它，避免消息进到旧会话
function newConv() {
  creating = (async () => {
    const { session_id } = await api("/api/conversations", { method: "POST" });
    await openConv(session_id);
    $("#msg").focus();
  })().finally(() => { creating = null; });
  return creating;
}
async function initChat() {
  const list = await refreshConvs();
  if (currentSid && list.some((c) => c.session_id === currentSid)) await openConv(currentSid);
  else if (list.length) await openConv(list[0].session_id);
  else await newConv();
}
async function send(text) {
  text = (text ?? $("#msg").value).trim();
  if (!text) return;
  if (creating) await creating.catch(() => {});
  if (!currentSid) await newConv();
  const w = $("#log .welcome");
  if (w) w.remove();
  bubble("user", text);
  $("#msg").value = "";
  try {
    const out = await api("/api/chat", jsonPost({ session_id: currentSid, message: text }));
    showReply(out);
  } catch (e) { bubble("assistant", "出错：" + e.message); }
}
$o("#btnSend").onclick = () => send();
$o("#msg").addEventListener("keydown", (e) => { if (e.key === "Enter" && !e.shiftKey && !e.isComposing) { e.preventDefault(); send(); } });
$o("#convSearch").addEventListener("input", renderConvs);
$o("#btnNew").onclick = () => newConv().catch((e) => bubble("assistant", "出错：" + e.message));
$o("#convList").onclick = (e) => { const li = e.target.closest("li"); if (li) openConv(li.dataset.sid); };
document.querySelectorAll(".quick").forEach((b) => (b.onclick = () => send(b.dataset.q)));
initChat().catch(() => {});

// ---- 企业画像 ----
async function loadProfile() {
  const name = $("#pname").value.trim();
  if (!name) return;
  const p = await api("/api/profile?name=" + encodeURIComponent(name));
  if (!p.found) { $("#profileBody").innerHTML = `<p class="miss">${esc(p.message)}</p>`; return; }
  let html = `<p>${esc(p.name)}（${esc(p.enterprise_id)}）${p.authorized ? '<span class="tag ok">已授权</span>' : '<span class="tag warn">待授权</span>'}</p>`;
  if (p.message) html += `<p class="miss">${esc(p.message)}</p>`;
  html += '<div class="blocks">';
  for (const [k, rows] of Object.entries(p.blocks)) {
    html += `<div><h3>${esc(k)}</h3><table><tr><th>事实</th><th>值</th><th>来源（文件 · 表.字段）</th></tr>` +
      rows.map((r) => `<tr><td>${esc(r.label)}${r.used_in_precheck ? "" : '<div class="src">不参与预审</div>'}</td><td class="${r.missing ? "miss" : ""}">${esc(r.value)}</td><td class="src">${esc(r.source)}</td></tr>`).join("") + "</table>";
    if (k === "历史融资" && p.history_rows && p.history_rows.length) {
      html += `<table><tr>${p.history_rows[0].map((c) => `<th>${esc(c.label)}</th>`).join("")}</tr>` +
        p.history_rows.map((row) => "<tr>" + row.map((c) => `<td title="${esc(c.source)}">${esc(c.value)}</td>`).join("") + "</tr>").join("") + "</table>";
    }
    html += "</div>";
  }
  $("#profileBody").innerHTML = html + "</div>";
}
$o("#btnProfile").onclick = loadProfile;

// ---- 政府视图 / 报告 ----
async function loadGov() {
  const s = await api("/api/gov-view");
  if (s.empty) { $("#govBody").innerHTML = "<p class='big'>暂无已确认方案</p>"; return; }
  const list = (arr, k) => "<table>" + arr.map((x) => `<tr><td>${esc(x[k])}</td><td>${x.count}</td></tr>`).join("") + "</table>";
  $("#govBody").innerHTML = `<p>已确认方案 <b>${s.confirmed_count}</b> 笔</p><div class="blocks">
    <div><h3>区域融资热度</h3>${list(s.region_heat, "region")}</div>
    <div><h3>行业分布</h3>${list(s.industry_distribution, "industry")}</div>
    <div><h3>产品排行</h3>${s.product_ranking.length ? list(s.product_ranking, "product_name") : "<p class='hint'>暂无准入符合产品</p>"}</div>
    <div><h3>供需匹配率</h3><p class="big">${esc(s.match_rate.text)}</p><p class="small">至少含 1 个准入符合产品的已确认方案数 / 已确认方案数（${s.match_rate.percent}%）</p></div></div>`;
}
async function loadReport() {
  const r = await api("/api/report");
  if (r.empty) { $("#reportBody").innerHTML = "<p class='big'>暂无已确认方案</p>"; return; }
  const list = (xs) => esc((xs || []).join("、") || "无");
  $("#reportBody").innerHTML = `<p>共 ${r.confirmed_count} 笔已确认方案，供需匹配率 ${esc(r.match_rate.text)}。报告不收录未确认需求。</p>
   <h3>一、企业</h3><table><tr><th>企业</th><th>区域</th><th>行业</th><th>准入符合产品</th></tr>${r.enterprise_section.map((e) => `<tr><td>${esc(e.enterprise_name)}</td><td>${esc(e.region)}</td><td>${esc(e.industry)}</td><td>${list(e.eligible_products)}</td></tr>`).join("")}</table>
   <h3>二、产品</h3><table><tr><th>产品</th><th>次数</th><th>企业名称</th></tr>${r.product_section.map((p) => `<tr><td>${esc(p.product_name)}</td><td>${p.count}</td><td>${list(p.enterprises)}</td></tr>`).join("") || "<tr><td colspan=3>暂无准入符合产品</td></tr>"}</table>
   <h3>三、区域</h3><table><tr><th>区域</th><th>数量</th><th>企业名称</th></tr>${r.region_section.map((g) => `<tr><td>${esc(g.region)}</td><td>${g.count}</td><td>${list(g.enterprises)}</td></tr>`).join("")}</table>`;
}

// ---- 方案列表 ----
async function loadSolutions() {
  const st = $("#solFilter").value;
  const { solutions } = await api("/api/solutions" + (st ? "?status=" + encodeURIComponent(st) : ""));
  const cls = { "待确认": "pend", "已确认": "ok", "已放弃": "bad" };
  $("#solBody").innerHTML = solutions.length ? `<table><tr><th>方案</th><th>企业</th><th>需求</th><th>主条件</th><th>准入符合</th><th>状态</th><th>人工确认的产品</th><th>确认时间</th></tr>` + solutions.map((s) => `<tr>
    <td>${esc(s.solution_id)}</td><td>${esc(s.enterprise_name)}<div class="src">${esc(s.region)} · ${esc(s.industry)}</div></td>
    <td>${s.request.amount} 万 / ${s.request.term} 个月 / ${esc(s.request.purpose)}${s.request.guarantee ? " / " + esc(s.request.guarantee) : ""}</td>
    <td>${esc(s.primary_condition)}</td><td>${s.eligible_count} 个（${esc((s.recommended.cards || []).map((c) => c.product_name).join("、") || "无")}）</td>
    <td><span class="tag ${cls[s.status]}">${esc(s.status)}</span></td>
    <td>${s.status === "已确认" ? esc(s.confirmed_product || "无（无准入符合产品）") : "—"}</td>
    <td>${s.confirmed_at ? esc(s.confirmed_at.replace("T", " ")) : "—"}</td></tr>`).join("") + "</table>" : "<p class='hint'>暂无方案</p>";
}
$("#solFilter").onchange = loadSolutions;
$o("#btnSolRefresh").onclick = loadSolutions;

// ---- 记忆与技能 ----
async function loadSystem() {
  const m = await api("/api/memory");
  $("#memBody").innerHTML = "<table><tr><th>范围</th><th>键</th><th>值</th><th>来源</th><th></th></tr>" + m.items.map((i) => `<tr><td>${esc(i.scope)}</td><td>${esc(i.key)}</td><td>${esc(i.value)}</td><td class="src">${esc(i.source)}</td>
    <td><button class="ghost" onclick="memDel('${esc(i.scope)}','${esc(i.key)}')">清除</button></td></tr>`).join("") + "</table>";
  const s = await api("/api/skills");
  $("#skillBody").innerHTML = "<table><tr><th>Skill</th><th>触发条件</th><th>输入</th><th>输出</th><th>契约</th></tr>" + s.skills.map((k) => `<tr><td><b>${esc(k.name)}</b><div class="src">${esc(k.id)}</div></td><td>${esc(k.trigger)}</td><td>${esc(k.input)}</td><td>${esc(k.output)}</td><td class="small">${esc(k.contract || "")}</td></tr>`).join("") + "</table>";
}
async function memDel(scope, key) { await api(`/api/memory?scope=${encodeURIComponent(scope)}&key=${encodeURIComponent(key)}`, { method: "DELETE" }); loadSystem(); }
$o("#btnMemSave").onclick = async () => {
  const key = $("#mkey").value.trim(), value = $("#mval").value.trim();
  if (!key) return;
  await api("/api/memory", { method: "PUT", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ scope: "global", key, value }) });
  loadSystem();
};
$o("#btnMemClear").onclick = async () => { if (confirm("清除全部记忆？")) { await api("/api/memory", { method: "DELETE" }); loadSystem(); } };

route();
