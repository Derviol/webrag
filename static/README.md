# static — 前端页面

> 原生 JS 实现（无框架，FastAPI 静态托管）：问答页 + 管理后台。
> 静态目录已由 main.py 挂载（`app.mount("/", StaticFiles(...))`），改完刷新即生效。

## 交付物

| 文件 | 内容 |
| --- | --- |
| index.html | 问答页：输入框 + 回答区 + 来源列表 |
| css/app.css | 页面样式（单文件即可） |
| js/api.js | POST /ask 与 POST /ask/stream 的 fetch 封装（含错误处理，错误码见 api.md §1.1；/ask/stream 为 SSE 流式，见 §1.3） |
| js/render.js | 渲染 answer + sources：sources 渲染为可点击 [n] 链接（index ↔ url） |
| js/vendor/purify.min.js | 回答渲染前 HTML 消毒（LLM 输出不可信，防 XSS） |

## 接口契约（api.md §1，权威）

- POST /ask：请求 `{"question": "...", "temperature": 0.5, "use_web_search": true, "web_top_n": 5}`（temperature / use_web_search / web_top_n 可选；use_web_search=false 时后端仅检索本地知识库，未查到返回 EMPTY_RESULT「信息不足」；web_top_n 为联网搜索的网页数量 1–20，缺省 settings.crawler.top_urls，仅开启联网时生效；client_time 恒附带——前端宿主机本地时间 ISO 8601，后端处理「近日/近期/今天」等时效性问题的时间基准，api.js 每次请求实时取 `new Date()`）；成功响应 `{"answer": "...[1]...[2]", "sources": [{"index":1,"title":"...","url":"..."}]}`；
- 失败响应统一 `{"error": {"code", "message"}}`，前端按 code 显示提示（TIMEOUT / LLM_FAILED / EMPTY_RESULT / SEARCH_FAILED / INTERNAL_ERROR）；
- 渲染规则：answer 中的 [n] 与 sources[].index 一一对应；sources 按 index 排序，title 为链接文本、url 为 href。

## 启用方式

静态目录已在 src/webrag/main.py 中挂载（`app.mount("/", StaticFiles(directory=..., html=True), name="static")`），
无需额外配置；容器部署时 static/ 以 volume 挂载到 /app/static，宿主机改完刷新即生效。

## 验收标准

- [x] 输入问题回车提交，回答区展示 answer（[n] 引用保留）；
- [x] 来源列表可点击跳转真实 URL，序号与 answer 中 [n] 对应；
- [x] /ask 失败时按错误码展示提示，页面不白屏；
- [x] 回答含 `<script>` 等恶意 HTML 时被消毒、不执行。

## 打磨记录

- 视觉：渐变背景、卡片阴影、引用上标徽标、来源序号圆标、空状态引导、页脚；
- 交互：示例问题 chips（点击即提交）、加载 spinner + 按钮态切换、完成时显示耗时、成功/失败绿色/红色状态条、自动滚动到回答；
- 健壮性：`api.js` 客户端超时兑底（120s → TIMEOUT 错误码），服务端挂起不再无限等待；
- 无障碍：`aria-live` 状态播报、`aria-busy` 回答区、`prefers-reduced-motion` 尊重减弱动效。
- 温度参数：左侧「生成参数」栏温度滑杆（0–2、步进 0.1），初始值加载时读 /health 下发的 llm_temperature（settings.llm.temperature，兜底 0.3），随 /ask、/ask/stream 请求透传影响生成。
- 联网搜索开关：左侧「生成参数」栏新增 switch（默认**关闭**，opt-in）——开启时本地知识库未命中继续联网兜底；关闭时仅检索本地（问答缓存 + 离线知识库），未查到提示「信息不足」；随 /ask、/ask/stream 请求透传 use_web_search，请求中禁用；离线库来源（offline://）以非链接样式展示（标注「本地知识库文档」）。
- 搜索网页数量：左侧「生成参数」栏新增滑杆（1–20，默认读 /health 下发的 web_top_n 即 settings.crawler.top_urls，兜底 5）——联网搜索时抓取参考的网页数量，随 /ask、/ask/stream 请求透传 web_top_n，请求中禁用；滑杆始终可拖动（与温度滑杆一致），web_top_n 仅在开启「联网搜索」时随请求生效（后端忽略其余情况）。

## 打磨记录（UI 视觉&交互优化）

- 设计系统：主色 #2f6fed token 化（primary / hover / active / weak / ring），辅助色浅灰（muted）、淡红（error）做提示区分；圆角统一（8/12px），阴影分层（侧栏 shadow-sm < 卡片 shadow-md < 按钮悬浮 shadow-btn-hover）；低饱和浅蓝渐变背景，仅 static/index.html 使用本样式表。
- 布局：左侧参数面板 sticky 固定侧边栏，主内容 flex 自适应；模块间距统一为 1rem / 1.25rem；新增 920 / 560 / 420px 断点，浏览器缩放与窄屏无挤压错位。
- 细节：温度滑杆自绘轨道/滑块（webkit+moz），已选区间渐变填充由 render.js 同步 --fill（含 /health 下发默认值）；开关 checked 高亮+微光晕、按压滑块变宽（iOS 式）；说明小字 line-height 1.7；输入框聚焦高亮边框+光环+caret-color；提问按钮 hover 上浮+蓝影、active 按压反馈；快捷标签 hover 变色、active 缩放反馈；报错提示栏浅红底色柔和、文案居中（loading/ok 同步居中排版）。
- 字体：统一无衬线字体栈（-apple-system / Segoe UI / PingFang SC / Microsoft YaHei / Noto Sans SC）；标题全部加粗（h1/h2 700，修复原 h2 非加粗），字号梯度 h1 1.5 / h2 1.05 / body 1 / 说明 0.92 / 辅助 0.8 / hint 0.78rem。
- 交互：全部可点击元素补齐 hover / active / focus-visible（键盘焦点环），保留 prefers-reduced-motion 与 aria 语义。核心功能模块（参数面板、头部、问答表单、快捷标签、状态提示、页脚）与全部 DOM id/class 契约未变。
- 纵向紧凑化：`.layout` min-height 100vh + align-items stretch（左右面板高度对齐），主内容以首/尾子元素 auto margin 垂直居中（溢出时不裁剪、自然滚动）；侧栏 max-height 100vh + 内容纵向居中 + sticky；模块间距统一收紧（header 1rem / form 0.85rem / chips 1rem / status 1rem / card margin 1rem、padding 1.3rem / footer 1rem），全局上下 padding 压缩至 1.5rem；移动端恢复自然高度与顶部排布。仅调整纵向间距与页面高度，配色、左右布局与全部功能不变。
