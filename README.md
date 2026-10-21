# Overseas

Windows 本地外贸工作台。此仓库同步 4.0.3 试用交付对应的应用源码：客户管理、真实搜索与网页采集、证据和实体分类、模型分析及草稿、OCR/RAG 接口、备份与桌面启动。

使用 Python 3.12 运行核心服务，核心依赖见 requirements.txt。可选 OCR、RAG、正文提取和结构化校验依赖独立运行环境，不包含在源码仓库中。

```powershell
python run_production.py --config launch.example.json --auto-port --no-browser
```

启动后按终端显示的本地 URL 打开界面。示例配置使用仓库内独立 workspace/，新建空白数据库；不要将开发实例指向已有业务工作区。按 Ctrl+C 退出。首次启动不自动恢复 Key、不调用模型、不自动下载模型。

用户操作见 [使用说明](docs/USER_GUIDE.md)，开发与打包见 [构建说明](packaging/README.md)，来源、脱敏差异和验证范围见 [同步说明](docs/SOURCE_SYNC_4.0.3.md)。原有知识库接口文档保留供参考，具体行为以当前代码为准。

```powershell
python -m unittest discover -s tests -p "test_*.py" -v
```

测试使用独立临时数据库和合成数据。现场业务回放测试及其输入未纳入仓库。部分测试需要 Windows DPAPI、独立提取运行时或浏览器环境；本次结果见同步说明。不要对正式运行实例执行测试。

仓库不包含客户资料、原始业务文档、知识语料、数据库、Key、日志、模型权重、依赖二进制、安装包或内部审计资料。第三方前端源码的 LICENSE/NOTICE 随资源保留。本次源码同步不代表安装包全部再分发许可已完成核查。
