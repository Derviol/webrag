/* POST /ask 封装（api.md §1.1）：成功返回 {answer, sources}；失败抛带 code 的 Error */
"use strict";
window.WebRAG = window.WebRAG || {};

/* 请求体：question 必填；temperature 为有限数值时附带（0–2，后端校验）；
 * use_web_search 为布尔时附带（联网搜索开关：False 时后端仅检索本地知识库）；
 * web_top_n 为 1–20 整数时附带（联网搜索的网页数量，后端校验；缺省用 settings.crawler.top_urls）；
 * client_time 恒附带：本次对话所在宿主机的本地时间（ISO 8601 带时区偏移），
 * 后端处理「近日/近期/今天」等时效性问题时以此作为当前时间基准（联网搜索词锚定 + LLM 时间基准）。 */
function clientTimeIso() {
  const d = new Date();
  const off = -d.getTimezoneOffset(); // UTC 偏移（分钟，东八区 = -480 → +480）
  const pad = (n) => String(n).padStart(2, "0");
  const sign = off >= 0 ? "+" : "-";
  const abs = Math.abs(off);
  return (
    `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}` +
    `T${pad(d.getHours())}:${pad(d.getMinutes())}:${pad(d.getSeconds())}` +
    `${sign}${pad(Math.floor(abs / 60))}:${pad(abs % 60)}`
  );
}

function buildPayload(question, temperature, useWebSearch, webTopN) {
  const payload = { question, client_time: clientTimeIso() };
  if (typeof temperature === "number" && isFinite(temperature)) {
    payload.temperature = temperature;
  }
  if (typeof useWebSearch === "boolean") {
    payload.use_web_search = useWebSearch;
  }
  if (Number.isInteger(webTopN) && webTopN >= 1 && webTopN <= 20) {
    payload.web_top_n = webTopN;
  }
  return payload;
}

WebRAG.ask = async function (question, temperature, timeoutMs = 200000, useWebSearch, webTopN) {
  // 客户端兜底超时：服务端挂起/失败时避免无限等待（映射为 TIMEOUT 错误码）。
  // 200s > server.ask_timeout_seconds(105s) 预算，服务端必然先返回（成功或 TIMEOUT），
  // 客户端只在服务端真正挂死时兜底。
  const ctrl = new AbortController();
  const timer = setTimeout(() => ctrl.abort(), timeoutMs);
  let res;
  try {
    // /ask 需登录（401 未登录）——必须携带 Bearer token（与主前端 webrag_token 共用登录态）
    const headers = { "Content-Type": "application/json" };
    const token = localStorage.getItem("webrag_token");
    if (token) headers["Authorization"] = "Bearer " + token;
    res = await fetch("/ask", {
      method: "POST",
      headers,
      body: JSON.stringify(buildPayload(question, temperature, useWebSearch, webTopN)),
      signal: ctrl.signal,
    });
  } catch (e) {
    clearTimeout(timer);
    const timedOut = e.name === "AbortError";
    const err = new Error(timedOut ? "请求超时，请稍后重试" : "网络异常，请稍后重试");
    err.code = timedOut ? "TIMEOUT" : "INTERNAL_ERROR";
    throw err;
  }
  clearTimeout(timer);

  const data = await res.json().catch(() => ({}));
  if (!res.ok || data.error) {
    const err = new Error(
      (data.error && data.error.message) || "请求失败，请稍后重试"
    );
    err.code = (data.error && data.error.code) || "INTERNAL_ERROR";
    throw err;
  }
  return data; // {answer, sources: [{index, title, url}]}
};

/* POST /ask/stream 封装（SSE，api.md §1.3）：
 * 事件流：status（检索阶段进度）* → delta（文本增量）* → done | error；
 * 回调：{onStatus(msg), onDelta(text), onDone(data), onError(err)}；超时/网络异常映射为错误码。
 * 非 SSE 响应（422 校验失败 / 500 等）回退解析 JSON 错误信封。
 */
WebRAG.askStream = async function (
  question,
  { onDelta, onStatus, onDone, onError, temperature, useWebSearch, webTopN, timeoutMs = 200000 } = {}
) {
  const ctrl = new AbortController();
  const timer = setTimeout(() => ctrl.abort(), timeoutMs);
  let res;
  try {
    // /ask/stream 需登录（401 未登录）——必须携带 Bearer token（与主前端 webrag_token 共用登录态）
    const headers = {
      "Content-Type": "application/json",
      Accept: "text/event-stream",
    };
    const token = localStorage.getItem("webrag_token");
    if (token) headers["Authorization"] = "Bearer " + token;
    res = await fetch("/ask/stream", {
      method: "POST",
      headers,
      body: JSON.stringify(buildPayload(question, temperature, useWebSearch, webTopN)),
      signal: ctrl.signal,
    });
  } catch (e) {
    clearTimeout(timer);
    const timedOut = e.name === "AbortError";
    onError &&
      onError({
        code: timedOut ? "TIMEOUT" : "INTERNAL_ERROR",
        message: timedOut ? "请求超时，请稍后重试" : "网络异常，请稍后重试",
      });
    return;
  }
  clearTimeout(timer);

  if (!res.ok || !res.body) {
    const data = await res.json().catch(() => ({}));
    onError &&
      onError({
        code: (data.error && data.error.code) || "INTERNAL_ERROR",
        message: (data.error && data.error.message) || "请求失败，请稍后重试",
      });
    return;
  }

  const reader = res.body.getReader();
  const decoder = new TextDecoder("utf-8");
  let buf = "";

  // 解析单帧（event: + data: 行），data 多行按 \n 拼接还原
  const dispatchFrame = (frame) => {
    let event = "message";
    const datas = [];
    for (const line of frame.split("\n")) {
      if (line.startsWith("event:")) event = line.slice(6).trim();
      else if (line.startsWith("data:"))
        datas.push(line.slice(5).replace(/^ /, "")); // 剥一个前导空格（SSE 规范）
    }
    if (!datas.length) return;
    const data = datas.join("\n");
    if (event === "delta") {
      onDelta && onDelta(data);
    } else if (event === "status") {
      onStatus && onStatus(data);
    } else if (event === "done") {
      try {
        onDone && onDone(JSON.parse(data));
      } catch (e) {
        onError && onError({ code: "INTERNAL_ERROR", message: "响应解析失败" });
      }
    } else if (event === "error") {
      try {
        onError && onError(JSON.parse(data));
      } catch (e) {
        onError && onError({ code: "INTERNAL_ERROR", message: data });
      }
    }
  };

  try {
    for (;;) {
      const { done, value } = await reader.read();
      if (done) break;
      buf += decoder.decode(value, { stream: true });
      let idx;
      while ((idx = buf.indexOf("\n\n")) >= 0) {
        const frame = buf.slice(0, idx);
        buf = buf.slice(idx + 2);
        if (frame.trim()) dispatchFrame(frame);
      }
    }
    if (buf.trim()) dispatchFrame(buf); // 末帧无空行结尾
  } catch (e) {
    onError && onError({ code: "INTERNAL_ERROR", message: "网络异常，请稍后重试" });
  }
};
