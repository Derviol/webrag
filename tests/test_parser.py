"""parser 单测：正文提取、噪声去除、空页面标记。trafilatura 离线可用。"""

from src.webrag.parser import parse

SAMPLE_HTML = """<!DOCTYPE html>
<html lang="zh-CN">
<head>
  <title>测试标题</title>
  <meta name="date" content="2025-06-01">
  <script>var junk = 1;</script>
</head>
<body>
  <!-- 这是注释噪音 -->
  <nav><a href="/">首页</a> 导航链接导航链接</nav>
  <article>
    <h1>正文标题</h1>
    <p>这是正文第一段，包含真实有效的内容信息。</p>
    <p>第二段正文，继续补充细节。</p>
  </article>
  <aside>广告横幅广告横幅</aside>
  <footer>页脚版权信息</footer>
</body>
</html>"""


def test_parse_extracts_body_and_url():
    doc = parse(SAMPLE_HTML, "https://example.com/p")
    assert "正文第一段" in doc.text
    assert doc.url == "https://example.com/p"


def test_parse_removes_script_and_comment_noise():
    doc = parse(SAMPLE_HTML, "https://example.com/p")
    assert "var junk" not in doc.text
    assert "注释噪音" not in doc.text


def test_parse_extracts_title():
    doc = parse(SAMPLE_HTML, "https://example.com/p")
    assert doc.title  # <title>/<meta> 至少取到一个


def test_parse_empty_page_marked_clearly():
    doc = parse("<html><body><script>var x=1;</script></body></html>", "https://example.com/empty")
    assert doc.text == ""  # 空正文明确标记，调用方跳过而非静默


def test_parse_blank_input():
    doc = parse("", "https://example.com/x")
    assert doc.text == ""
    assert doc.url == "https://example.com/x"


# 真实网页形态语料：表格 / meta 日期 / nav / aside 广告 / footer（对应 README 验收「测试网页集」）
CORPUS_HTML = """<!DOCTYPE html>
<html lang="zh-CN">
<head>
  <title>伦敦大师赛落幕</title>
  <meta name="date" content="2026-06-20">
</head>
<body>
  <nav>导航链接导航</nav>
  <main>
    <article>
      <h1>伦敦大师赛落幕</h1>
      <p>PRX 与 LEV 会师总决赛，最终 PRX 夺冠。</p>
      <table><tr><th>战队</th><th>比分</th></tr><tr><td>PRX</td><td>3:1</td></tr></table>
      <p>本次赛事在伦敦举行，观赛人数创纪录。</p>
    </article>
  </main>
  <aside>广告横幅</aside>
  <footer>版权所有</footer>
</body>
</html>"""


def test_parse_realistic_article_keeps_table_and_metadata():
    doc = parse(CORPUS_HTML, "https://vct.qq.com/news/1")
    assert "PRX" in doc.text and "3:1" in doc.text  # include_tables=True 保留表格
    assert doc.title  # <title>/<h1> 取到标题
    assert doc.publish_time == "2026-06-20"  # <meta date> 提取
    assert doc.url == "https://vct.qq.com/news/1"


def test_parse_realistic_article_removes_ads_and_footer():
    doc = parse(CORPUS_HTML, "https://vct.qq.com/news/1")
    assert "广告横幅" not in doc.text  # <aside> 广告剔除
    assert "版权所有" not in doc.text  # <footer> 剔除
