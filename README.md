# Overseas

本地工厂外贸工作台，包含客户开发、成交推进、模型设置和资料知识库。技术栈为 Python HTTP、SQLite、原生 HTML/CSS/JavaScript。

## 本地启动

需要 Python 3.9 或更新版本。在项目目录运行：

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe server.py
```

打开启动终端提示的本地地址。默认演示模式仅提供虚构练习数据。保持终端运行，Ctrl+C 停止。

使用独立本地工作区：

```powershell
.\.venv\Scripts\python.exe server.py --mode local --port 8878
```

按启动终端提示访问独立工作区。运行时 SQLite 存储在 `data/`，不随源码分发。客户开发 `/`、成交推进 `/deals`、模型配置 `/settings`、知识库 `/knowledge`。

知识库审核和可追溯引用见 [KNOWLEDGE.md](KNOWLEDGE.md)，接口见 [KNOWLEDGE_API_CONTRACT.md](KNOWLEDGE_API_CONTRACT.md)。

## 使用边界

客户池支持 CSV/XLSX 导入、映射、预览、明确确认入库、筛选和分页。模板仅含空表头或明确标注的虚构样例。

成交推进支持沟通、报价、反馈、卡点、下一步动作和本地回复草稿。草稿需要人工审核，平台不会自动发送邮件或正式报价，也不会识别真实购买意图或计算成交概率。离线建议明确标注为演示。

模型调用需先在本地设置页配置并对本次发送的数据明确授权。API Key 仅存服务进程内存，重启后清除；不放入源码、浏览器存储或仓库。知识库内容需核实来源、主体和用途；待核实内容保持标记。

服务绑定本地地址。本仓库不包含客户库、分析结果、日志、企业原始资料、OCR、截图或凭据。

## 验证

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
```

保留的测试使用临时 SQLite 和合成数据；模型传输使用测试替身，不发送真实客户资料或发起付费模型请求。

浏览器合成测试：先运行 `python tests/knowledge_review_ui_instance.py`，再在使用临时浏览器配置、启用 CDP 的测试环境运行 `node tests/knowledge_review_ui.mjs`。该脚本要求 CDP 测试端口9224，结果写入忽略的 `evidence/` 目录；先创建此目录。

## 发布来源

这是从当前工作台源码抽取的独立初始发布副本，不继承任何工作区 Git 历史。逐文件 SHA-256 记录见 `SOURCE_MANIFEST.json`。真实运行数据和用户资料仅保留在原本地工作区。
