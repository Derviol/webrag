/* WebRAG 管理后台前端（static/admin/）：登录 + 离线知识文档入库管理。
 * 接口契约见 docs/api.md §1.4；错误信封统一 {error: {code, message}}。
 * 登录态：localStorage 存 Bearer token（与前端主页面共享同一登录态 webrag_token）；
 * 用户组判断：启动时调 GET /admin/auth/me——admin 放行、普通用户（403）显示无权限页。
 */
"use strict";

const TOKEN_KEY = "webrag_token"; // 与前端主页面（账户模块）共用登录态
const USER_KEY = "webrag_admin_user";

const $ = (id) => document.getElementById(id);

/* ---------- 通用 fetch 封装 ---------- */

async function api(path, options = {}) {
  const headers = Object.assign({}, options.headers || {});
  const token = localStorage.getItem(TOKEN_KEY);
  if (token) headers["Authorization"] = "Bearer " + token;
  const ctrl = new AbortController();
  const timer = setTimeout(() => ctrl.abort(), 300000); // 入库/上传最长等 5min
  try {
    const res = await fetch(path, Object.assign({}, options, { headers, signal: ctrl.signal }));
    let data = {};
    try { data = await res.json(); } catch (_) { /* 空响应 */ }
    if (res.status === 401 && !path.includes("/auth/login")) {
      clearAuth(); // 凭证失效 → 回登录视图
      showBanner("error", "登录已过期，请重新登录");
      throw new Error("UNAUTHORIZED");
    }
    if (!res.ok || data.error) {
      const err = new Error((data.error && data.error.message) || "请求失败，请稍后重试");
      err.code = (data.error && data.error.code) || "INTERNAL_ERROR";
      err.status = res.status;
      throw err;
    }
    return data;
  } catch (e) {
    if (e.name === "AbortError") {
      const err = new Error("请求超时，请稍后重试");
      err.code = "TIMEOUT";
      throw err;
    }
    throw e;
  } finally {
    clearTimeout(timer);
  }
}

function showBanner(kind, msg) {
  const b = $("banner");
  b.className = kind; // error | info
  b.textContent = msg;
}
function clearBanner() { $("banner").className = ""; $("banner").textContent = ""; }

/* ---------- 登录 / 登出 / 用户组判断 ---------- */

function showBlocked() {
  // 已登录但非管理员 → 无权限页（直接输 URL 访问后台时拦截）
  $("login-view").classList.add("hidden");
  $("app-view").classList.add("hidden");
  $("blocked-view").classList.remove("hidden");
}

function clearAuth() {
  localStorage.removeItem(TOKEN_KEY);
  localStorage.removeItem(USER_KEY);
  $("login-view").classList.remove("hidden");
  $("app-view").classList.add("hidden");
  $("blocked-view").classList.add("hidden");
}

function restoreSession() {
  $("login-view").classList.add("hidden");
  $("blocked-view").classList.add("hidden");
  $("app-view").classList.remove("hidden");
  let name = "admin";
  try {
    const u = JSON.parse(localStorage.getItem(USER_KEY) || "null");
    if (u && u.username) name = u.username;
  } catch (_) { /* 旧格式纯用户名 */ }
  $("cur-user").textContent = "👤 " + name;
  refreshList();
}

$("login-form").addEventListener("submit", async (ev) => {
  ev.preventDefault();
  const errBox = $("login-error");
  errBox.style.display = "none";
  const btn = $("login-btn");
  btn.disabled = true;
  try {
    const data = await api("/admin/auth/login", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ username: $("login-username").value.trim(), password: $("login-password").value }),
    });
    localStorage.setItem(TOKEN_KEY, data.token);
    localStorage.setItem(USER_KEY, JSON.stringify({ username: data.username, role: data.role, uid: data.uid }));
    $("login-password").value = "";
    restoreSession();
    showBanner("info", "登录成功");
  } catch (e) {
    errBox.textContent = e.message || "登录失败";
    errBox.style.display = "block";
  } finally {
    btn.disabled = false;
  }
});

$("logout-btn").addEventListener("click", () => { clearAuth(); clearBanner(); });
$("blocked-logout").addEventListener("click", () => { clearAuth(); clearBanner(); });

/* ---------- 入库 ---------- */

function pollUntilDone(docId, noteEl) {
  // 入库为后台异步：每 2s 轮询详情直至状态非 processing（超时 10min 放弃，列表刷新兜底）
  const started = Date.now();
  const timer = setInterval(async () => {
    try {
      const { document: d } = await api("/admin/documents/" + docId);
      if (d.status !== "processing") {
        clearInterval(timer);
        if (noteEl) noteEl.textContent = d.status === "done"
          ? `✅ 入库完成：${d.chunk_count} 块`
          : `❌ 入库失败：${d.error_message || "未知错误"}`;
        refreshList();
      } else if (Date.now() - started > 10 * 60 * 1000) {
        clearInterval(timer);
        refreshList();
      }
    } catch (_) { /* 网络抖动忽略，下轮重试 */ }
  }, 2000);
}

async function submitIngest(bodyOrFormData, noteEl) {
  clearBanner();
  noteEl.textContent = "入库中…（后台解析/切块/嵌入，请稍候）";
  try {
    const data = await api("/admin/documents", { method: "POST", body: bodyOrFormData });
    showBanner("info", `文档「${data.title}」已提交，正在后台入库（ID ${data.id}）…`);
    noteEl.textContent = "入库中…";
    pollUntilDone(data.id, noteEl);
    return data;
  } catch (e) {
    noteEl.textContent = "";
    showBanner("error", e.message);
    return null;
  }
}

$("up-btn").addEventListener("click", async () => {
  const file = $("up-file").files[0];
  if (!file) { showBanner("error", "请先选择要上传的文件"); return; }
  const fd = new FormData();
  fd.append("file", file);
  const title = $("up-title").value.trim();
  if (title) fd.append("title", title);
  await submitIngest(fd, $("ingest-note"));
  $("up-file").value = ""; $("up-title").value = "";
});

$("txt-btn").addEventListener("click", async () => {
  const content = $("txt-content").value;
  if (!content.trim()) { showBanner("error", "内容为空，请先粘贴文本"); return; }
  const body = JSON.stringify({
    title: $("txt-title").value.trim(),
    content: content,
  });
  await submitIngest(body, $("ingest-note"));
  $("txt-content").value = ""; $("txt-title").value = "";
});

/* ---------- 列表 / 详情 / 删除 ---------- */

const STATUS_LABEL = { processing: "入库中", done: "已完成", failed: "失败" };

function statusHtml(s, errMsg) {
  if (s === "processing") return `<span class="status"><span class="dot processing"></span>入库中…</span>`;
  if (s === "done") return `<span class="status"><span class="dot done"></span>已完成</span>`;
  return `<span class="status"><span class="dot failed"></span>失败</span>
          <div class="err-msg" title="${escapeHtml(errMsg || "")}">${escapeHtml((errMsg || "").slice(0, 60))}</div>`;
}

function escapeHtml(s) {
  return String(s).replace(/[&<>"']/g, (c) => (
    { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]
  ));
}

async function refreshList() {
  try {
    const { documents, total } = await api("/admin/documents?limit=200");
    $("doc-total").textContent = `共 ${total} 篇`;
    const body = $("list-body");
    if (!documents.length) {
      body.innerHTML = '<div class="empty">暂无入库文档——从上方上传文件或粘贴文本开始</div>';
      return;
    }
    body.innerHTML = `<table>
      <thead><tr><th>ID</th><th>标题</th><th>类型</th><th>状态</th><th>块数</th><th>字符</th><th>创建时间</th><th>操作</th></tr></thead>
      <tbody>${documents.map(docRow).join("")}</tbody>
    </table>`;
  } catch (_) { /* 401 已由 api() 处理 */ }
}

function docRow(d) {
  const typeLabel = { text: "文本", md: "Markdown", html: "HTML" }[d.source_type] || d.source_type;
  return `<tr>
    <td>${d.id}</td>
    <td class="title-cell" title="${escapeHtml(d.title)}">${escapeHtml(d.title)}</td>
    <td>${typeLabel}</td>
    <td>${statusHtml(d.status, d.error_message)}</td>
    <td>${d.chunk_count}</td>
    <td>${d.char_count}</td>
    <td style="white-space:nowrap;">${escapeHtml(d.created_at || "")}</td>
    <td class="ops">
      <button class="ghost" data-act="detail" data-id="${d.id}">详情</button>
      <button class="danger" data-act="del" data-id="${d.id}">删除</button>
    </td>
  </tr>`;
}

$("list-body").addEventListener("click", async (ev) => {
  const btn = ev.target.closest("button[data-act]");
  if (!btn) return;
  const id = btn.dataset.id;
  if (btn.dataset.act === "del") {
    if (!confirm(`确认删除文档 #${id}？将同时清除其在离线知识库中的全部知识块。`)) return;
    try {
      const r = await api("/admin/documents/" + id, { method: "DELETE" });
      showBanner("info", `已删除：离线库清除 ${r.deleted_chunks} 个知识块`);
      refreshList();
    } catch (e) { showBanner("error", e.message); }
    return;
  }
  try {
    const { document: d } = await api("/admin/documents/" + id);
    const panel = $("detail-panel");
    panel.classList.remove("hidden");
    panel.innerHTML = `
      <h2 style="display:flex;justify-content:space-between;align-items:center;">
        <span>文档详情 #${d.id}</span>
        <button class="ghost" id="close-detail">关闭</button>
      </h2>
      <dl class="dl">
        <dt>标题</dt><dd>${escapeHtml(d.title)}</dd>
        <dt>状态</dt><dd>${statusHtml(d.status, d.error_message)}</dd>
        <dt>类型</dt><dd>${d.source_type}${d.file_name ? "（" + escapeHtml(d.file_name) + "）" : ""}</dd>
        <dt>块数 / 字符</dt><dd>${d.chunk_count} / ${d.char_count}</dd>
        <dt>入库时间</dt><dd>${escapeHtml(d.created_at || "")}</dd>
      </dl>
      <label>原文（入库内容备份）</label>
      <pre>${escapeHtml(d.content || "")}</pre>`;
    panel.querySelector("#close-detail").addEventListener("click", () => panel.classList.add("hidden"));
    panel.scrollIntoView({ behavior: "smooth", block: "nearest" });
  } catch (e) { showBanner("error", e.message); }
});

/* ---------- 启动 ---------- */
// 已有登录态 → 先调 /admin/auth/me 判断用户组：admin 放行；普通用户（403）显示无权限页；
// 无登录态 → 登录视图（index.html 两个视图初始均 hidden，此处兜底展示）
(async function init() {
  const token = localStorage.getItem(TOKEN_KEY);
  if (!token) { clearAuth(); return; }
  try {
    const me = await api("/admin/auth/me");
    localStorage.setItem(USER_KEY, JSON.stringify({ username: me.username, role: me.role, uid: me.uid }));
    restoreSession();
  } catch (e) {
    if (e.status === 403) showBlocked(); // 已登录但非管理员 → 拦截
    // 401 已由 api() 的 clearAuth 处理（凭证失效回登录视图）
  }
})();
