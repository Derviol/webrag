/* 渲染 answer + sources：
 * - answer 先经 DOMPurify 消毒（LLM 输出不可信，防 XSS）；
 * - [n] 渲染为可点击引用链接（n ↔ sources[].index）；
 * - 失败时按错误码显示提示（api.md §1.1）。
 */
"use strict";

const ERROR_MESSAGES = {
  VALIDATION_ERROR: "请求参数不合法，请检查输入",
  SEARCH_FAILED: "搜索失败，请稍后重试",
  TIMEOUT: "请求超时，请稍后重试",
  LLM_FAILED: "模型生成失败，请稍后重试",
  EMPTY_RESULT: "信息不足：未检索到相关内容，请换个问法或开启联网搜索",
  INTERNAL_ERROR: "服务内部错误，请稍后重试",
};

function safeUrl(url) {
  // 仅放行 http(s) 链接，并剔除引号/尖括号防止属性逃逸
  if (!/^https?:\/\//i.test(url || "")) return "#";
  return url.replace(/["'<>]/g, "");
}

// 离线知识库来源（offline://<doc_ref>）：本地文档，无可点击 URL
function isOfflineSource(url) {
  return /^offline:\/\//i.test(url || "");
}

function renderAnswer(answer, sources) {
  const clean = window.DOMPurify.sanitize(answer || "");
  const cited = clean.replace(/\[(\d+)\]/g, (m, n) => {
    const src = sources.find((s) => s.index === Number(n));
    if (!src) return m;
    // 离线知识库来源：非链接样式（本地文档，无 URL 可跳转）
    return isOfflineSource(src.url)
      ? `<span class="cite" title="本地知识库文档">[${n}]</span>`
      : `<a class="cite" href="${safeUrl(src.url)}" target="_blank" rel="noopener noreferrer">[${n}]</a>`;
  });
  document.getElementById("answer").innerHTML = cited;
}

function appendAnswerText(text) {
  // 流式增量追加：textContent 原文插入（无 HTML 解析，无 XSS 风险）；
  // 结束/失败后由 renderAnswer 统一消毒 + [n] 引用化覆盖。
  const el = document.getElementById("answer");
  if (el.dataset.streaming !== "1") {
    el.textContent = "";
    el.dataset.streaming = "1";
  }
  el.textContent += text;
}

function renderSources(sources) {
  const ol = document.getElementById("sources");
  ol.innerHTML = "";
  // 按 index 升序渲染，保证与回答中的 [n] 一一对应
  const ordered = [...sources].sort((a, b) => a.index - b.index);
  for (const s of ordered) {
    const li = document.createElement("li");

    const badge = document.createElement("span");
    badge.className = "src-badge";
    badge.textContent = s.index;

    const body = document.createElement("div");
    body.className = "src-body";
    // 离线知识库来源（offline://<doc_ref>）：无真实 URL，非链接样式并标注「本地知识库文档」
    const offline = isOfflineSource(s.url);
    const url = document.createElement("span");
    url.className = "src-url";
    url.textContent = offline ? "本地知识库文档" : s.url;
    if (offline) {
      const t = document.createElement("span");
      t.className = "src-title src-title-offline";
      t.textContent = s.title || "本地知识库文档";
      body.appendChild(t);
    } else {
      const a = document.createElement("a");
      a.className = "src-title";
      a.href = safeUrl(s.url);
      a.target = "_blank";
      a.rel = "noopener noreferrer";
      a.textContent = s.title || s.url;
      body.appendChild(a);
    }
    body.appendChild(url);
    li.appendChild(badge);
    li.appendChild(body);
    ol.appendChild(li);
  }
}

function setStatus(message, kind, showSpinner) {
  const el = document.getElementById("status");
  el.hidden = !message;
  el.className = "status " + (kind || "");
  el.innerHTML = "";
  if (showSpinner) {
    const spin = document.createElement("span");
    spin.className = "spinner";
    spin.setAttribute("aria-hidden", "true");
    el.appendChild(spin);
  }
  el.appendChild(document.createTextNode(message || ""));
}

/* P2: 反馈收集 */
let _currentSessionData = null; // 当前问答会话数据（用于反馈提交）

function showFeedback(data) {
  const area = document.getElementById("feedback-area");
  if (!area) return;
  // 直答兜底或已缓存回答也允许反馈
  _currentSessionData = data;
  area.hidden = false;
  // 重置按钮状态
  const goodBtn = area.querySelector(".fb-good");
  const badBtn = area.querySelector(".fb-bad");
  if (goodBtn) goodBtn.classList.remove("active");
  if (badBtn) badBtn.classList.remove("active");
}

function submitFeedback(type) {
  if (!_currentSessionData) return;
  const data = _currentSessionData;
  const payload = {
    question: data._question || "",
    answer: data.answer || "",
    sources: data.sources || [],
    feedback_type: type, // "good" | "bad"
    cached: data.cached || false,
    direct: data.direct || false,
    hallucination_risk: data.hallucination_risk || null,
  };

  // 按钮状态反馈
  const area = document.getElementById("feedback-area");
  const goodBtn = area.querySelector(".fb-good");
  const badBtn = area.querySelector(".fb-bad");
  goodBtn.classList.toggle("active", type === "good");
  badBtn.classList.toggle("active", type === "bad");

  fetch("/feedback", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  }).catch(() => {}); // 静默失败，不影响主流程
}

document.addEventListener("DOMContentLoaded", () => {
  /* ---- 侧边栏折叠/展开（localStorage 持久化） ---- */
  const layout = document.getElementById("layout");
  const sidebar = document.getElementById("sidebar");
  const toggleBtn = document.getElementById("sidebar-toggle");
  const showBtn = document.getElementById("sidebar-show-btn");
  const SIDEBAR_KEY = "webrag.sidebar.collapsed";

  function applySidebarState(collapsed) {
    layout.classList.toggle("sidebar-collapsed", collapsed);
    if (toggleBtn) {
      toggleBtn.setAttribute("aria-expanded", String(!collapsed));
      toggleBtn.title = collapsed ? "展开参数面板" : "收起参数面板";
    }
    if (showBtn) showBtn.classList.toggle("visible", collapsed);
  }

  // 读取持久化状态
  let sidebarCollapsed = false;
  try {
    sidebarCollapsed = localStorage.getItem(SIDEBAR_KEY) === "1";
  } catch (_) { /* localStorage 不可用时忽略 */ }
  applySidebarState(sidebarCollapsed);

  if (toggleBtn) {
    toggleBtn.addEventListener("click", () => {
      sidebarCollapsed = true;
      applySidebarState(true);
      try { localStorage.setItem(SIDEBAR_KEY, "1"); } catch (_) {}
    });
  }
  if (showBtn) {
    showBtn.addEventListener("click", () => {
      sidebarCollapsed = false;
      applySidebarState(false);
      try { localStorage.setItem(SIDEBAR_KEY, "0"); } catch (_) {}
    });
  }

  const form = document.getElementById("ask-form");
  const input = document.getElementById("question");
  const btn = document.getElementById("submit-btn");
  const answerArea = document.getElementById("answer-area");
  const sourcesArea = document.getElementById("sources-area");
  const emptyState = document.getElementById("empty-state");
  const tempSlider = document.getElementById("temperature");
  const tempValue = document.getElementById("temperature-value");
  const webSearchToggle = document.getElementById("use-web-search");
  const webSearchState = document.getElementById("web-search-state");
  const webTopNSlider = document.getElementById("web-top-n");
  const webTopNValue = document.getElementById("web-topn-value");
  const webTopNGroup = document.getElementById("web-topn-group");

  /* 联网开关 → 搜索网页数量滑杆联动：
   * 联网关闭时滑杆置灰不可拖动（值保留，重新开启后恢复可调）；
   * 联网开启时恢复正常。请求进行中的临时禁用（cursor:wait）不受影响。 */
  function updateWebTopNState() {
    if (!webTopNSlider || !webSearchToggle) return;
    const enabled = webSearchToggle.checked;
    webTopNSlider.disabled = !enabled;
    if (webTopNGroup) webTopNGroup.classList.toggle("param-disabled", !enabled);
  }

  async function submit(question) {
    if (!question) return;
    const temperature = tempSlider ? parseFloat(tempSlider.value) : undefined;
    const useWebSearch = webSearchToggle ? webSearchToggle.checked : false;
    const webTopN = webTopNSlider ? parseInt(webTopNSlider.value, 10) : undefined;
    const startedAt = performance.now();

    let cacheHit = false; // 命中问答缓存：流式输出已存摘要（状态提示不显示"生成中…"）
    btn.disabled = true;
    btn.textContent = "生成中…";
    input.disabled = true;
    if (tempSlider) tempSlider.disabled = true;
    if (webSearchToggle) webSearchToggle.disabled = true;
    if (webTopNSlider) webTopNSlider.disabled = true;
    emptyState.hidden = true;
    answerArea.hidden = true;
    sourcesArea.hidden = true;
    answerArea.setAttribute("aria-busy", "true");
    setStatus(
      useWebSearch
        ? "检索并生成中，请稍候…（联网问答约 1-2 分钟，命中历史问答缓存秒回）"
        : "正在检索本地知识库…",
      "loading",
      true
    );

    try {
      await new Promise((resolve, reject) => {
        WebRAG.askStream(question, {
          temperature,
          useWebSearch,
          webTopN,
          onStatus(msg) {
            // 检索/生成阶段进度实时展示（正在联网搜索… / 正在生成回答…）
            cacheHit = msg.includes("命中历史问答缓存");
            setStatus(msg, "loading", true);
          },
          onDelta(text) {
            appendAnswerText(text);
            answerArea.hidden = false;
            // 缓存命中流式输出时保留缓存状态提示，不覆盖为"生成中…"
            if (!cacheHit) setStatus("生成中…", "loading", true);
          },
          onDone(data) {
            renderAnswer(data.answer, data.sources || []);
            renderSources(data.sources || []);
            answerArea.hidden = false;
            sourcesArea.hidden = false;
            answerArea.setAttribute("aria-busy", "false");
            const secs = ((performance.now() - startedAt) / 1000).toFixed(0);
            // cached=true：命中历史问答缓存（未联网、未调 LLM，直接返回已存摘要+来源）
            setStatus(
              data.cached ? `⚡ 命中历史问答缓存，用时 ${secs} 秒` : `完成，用时 ${secs} 秒`,
              data.cached ? "ok cached" : "ok"
            );
            // P2: 显示反馈按钮（注入原始问题用于反馈）
            data._question = question;
            showFeedback(data);
            answerArea.scrollIntoView({ behavior: "smooth", block: "nearest" });
            resolve();
          },
          onError(err) {
            // 流式中断：已输出片段保留，仅追加错误状态（错误码见 api.md §1.1）
            answerArea.setAttribute("aria-busy", "false");
            setStatus(ERROR_MESSAGES[err.code] || err.message, "error");
            reject(err);
          },
        });
      });
    } catch (err) {
      // 错误状态已由 onError 设置，无需重复处理
    } finally {
      btn.disabled = false;
      btn.textContent = "提问";
      input.disabled = false;
      if (tempSlider) tempSlider.disabled = false;
      if (webSearchToggle) webSearchToggle.disabled = false;
      // 数量滑杆：请求结束后恢复，但联网关闭时仍保持置灰不可拖动
      updateWebTopNState();
      input.focus();
    }
  }

  form.addEventListener("submit", (e) => {
    e.preventDefault();
    submit(input.value.trim());
  });

  // 搜索网页数量滑杆：拖动时实时同步数值显示；默认值读服务端配置（/health → web_top_n）
  let webTopNTouched = false;
  const syncTopN = () => {
    if (!webTopNSlider || !webTopNValue) return;
    webTopNValue.textContent = String(webTopNSlider.value);
    const min = parseFloat(webTopNSlider.min) || 1;
    const max = parseFloat(webTopNSlider.max) || 20;
    const pct = ((parseFloat(webTopNSlider.value) - min) / (max - min)) * 100;
    webTopNSlider.style.setProperty(
      "--fill",
      Math.max(0, Math.min(100, pct)).toFixed(1) + "%"
    );
  };
  if (webTopNSlider) {
    webTopNSlider.addEventListener("input", () => {
      webTopNTouched = true;
      syncTopN();
    });
  }

  // 温度滑杆：拖动时实时同步数值显示；默认值读服务端配置（/health → settings.llm.temperature）
  let userTouched = false;
  if (tempSlider && tempValue) {
    const sync = () => {
      tempValue.textContent = parseFloat(tempSlider.value).toFixed(1);
      // 滑轨填充比例同步（app.css #temperature 的 --fill 渐变高亮已选区间）
      const min = parseFloat(tempSlider.min) || 0;
      const max = parseFloat(tempSlider.max) || 2;
      const pct = ((parseFloat(tempSlider.value) - min) / (max - min)) * 100;
      tempSlider.style.setProperty(
        "--fill",
        Math.max(0, Math.min(100, pct)).toFixed(1) + "%"
      );
    };
    tempSlider.addEventListener("input", () => {
      userTouched = true;
      sync();
    });
    // 5s 超时：Milvus 未启动时 /health 的 gRPC 探活可能拖慢响应，失败则保留 HTML 兜底值
    const ctrl = new AbortController();
    const timer = setTimeout(() => ctrl.abort(), 5000);
    fetch("/health", { signal: ctrl.signal })
      .then((r) => (r.ok ? r.json() : null))
      .then((data) => {
        const t = data && data.llm_temperature;
        if (!userTouched && typeof t === "number" && isFinite(t) && t >= 0 && t <= 2) {
          tempSlider.value = String(t);
          sync(); // 同步数值文本 + 滑轨填充比例
        }
        // 搜索网页数量默认值（settings.crawler.top_urls）：用户未手动拖动时应用
        const wn = data && data.web_top_n;
        if (webTopNSlider && !webTopNTouched && Number.isInteger(wn) && wn >= 1 && wn <= 20) {
          webTopNSlider.value = String(wn);
          syncTopN();
        }
      })
      .catch(() => {}) // 拉取失败：保持 HTML 默认值
      .finally(() => clearTimeout(timer));
  }

  // 联网搜索开关：切换时同步右侧状态文案 + 联动「搜索网页数量」滑杆可用性。
  // 联网关闭 → 滑杆置灰不可拖动（值保留不重置）；联网开启 → 恢复可调。
  if (webSearchToggle && webSearchState) {
    webSearchToggle.addEventListener("change", () => {
      webSearchState.textContent = webSearchToggle.checked ? "开启" : "关闭";
      updateWebTopNState();
    });
    // 页面初始状态：联网默认关闭 → 滑杆置灰
    updateWebTopNState();
  }

  // 示例问题 chips：点击即填充并提交
  document.querySelectorAll(".chip").forEach((chip) => {
    chip.addEventListener("click", () => {
      input.value = chip.dataset.q || "";
      submit(input.value.trim());
    });
  });
});
