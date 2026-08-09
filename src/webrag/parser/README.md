# parser — 清洗与正文提取

## 职责

- HTML → 干净正文：去除导航、广告、脚本、评论区等噪声；
- 抽取元数据：标题、发布时间、作者、URL；
- 编码与格式异常兜底（乱码、动态页空正文）。

## 接口约定

- 输入：HTML 文本 + URL → 输出：Document（正文 + 元数据）；
- 下游：chunker（切块）、milvus_store（元数据入库）。

## 验收标准

- [x] 测试网页集覆盖：正文/表格提取、meta 日期、标题、广告（aside）/页脚（footer）剔除（tests/test_parser.py）；
- [x] 空正文/失败页面有明确标记，不静默丢弃。

## 已知局限

- trafilatura 对 `<nav>` 的取舍随页面结构与版本变化：本仓库锁定版本实测保留 nav 文本（见 tests/test_parser.py 语料），正文去噪以 trafilatura 默认启发式为准；如需强剔除，请在语料评测后调整。
