# config — 配置管理

## 职责

- 统一管理运行配置：分块参数、检索 Top-k、模型名称、连接地址、重排开关等；
- 提供 `.env.example` 模板，规范密钥与连接信息的注入方式。

## 所属角色

- 项目协调 / 架构（#1）负责维护；
- 各模块负责人向 config 提出配置项需求，不在代码里硬编码参数。

## 交付物

- settings.yaml：环境无关的可调参数（分块大小、Top-k、模型名、超时等）；
- .env.example：密钥与连接信息模板（真实值放本地 .env，已 gitignore）。

## 约定

- 密钥（API Key、密码）只进 .env，绝不进 settings.yaml 或代码；
- 新增配置项需在 docs/CHANGELOG.md 说明用途与默认值。
