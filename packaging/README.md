# 源码运行与 Windows 打包

核心应用使用 Python 3.12、openpyxl 和 pypdf。打包需预先准备 PyInstaller。本次同步没有执行依赖安装、模型下载或重新构建安装包。

Overseas.spec 从当前仓库收集后端、RAG 桥接代码、模板和前端，生成 Overseas.exe 与 OverseasService.exe：

```powershell
python -m PyInstaller packaging/Overseas.spec --clean --noconfirm
```

Validator.spec 是独立结构化输出校验器，需要另行准备带 Pydantic v2 的兼容 Python 环境。OCR/RAG/正文提取的独立环境及模型不由上述 spec 自动获取。OCR 使用 config/ocr-profile.example.json 的 v2 路径约定；复制并调整本地配置后，通过 --ocr-profile 指定。本地 RAG 路径字段见 backend/launcher_config.py，通过受信任的本地配置指定，不能从 HTTP 请求选择可执行程序。

Overseas.iss 来自 4.0.3 的实际压缩安装脚本，已将原机器绝对路径改为本目录下的 full-payload/ 和 overlay/。分别准备脱敏的完整应用/组件载荷以及用户说明、许可证和 NOTICE 覆盖层。使用预先安装的兼容 Inno Setup 编译器：

```powershell
ISCC.exe packaging/Overseas.iss
```

完整载荷应包含 app/Overseas.exe、服务程序、所需 _internal 资源和已准备的独立组件；安装脚本会在目标机器生成启动配置。PayloadBytes 默认值来自该次交付，修改载荷后必须重新计算。安装产物输出到 packaging/output/，不得提交到源码仓库。

prepare_trial_bundle.py 是源码 ZIP 准备工具，不是完整安装载荷生成器，文件白名单已调整为当前仓库。运行前仍需人工审阅文件范围。

源码仓库不能直接复刻完整 4.0.3 安装包：缺少运行时、模型、精确依赖锁定和完整载荷生成流水线，也未执行干净机器重建或代码签名。原交付中 Microsoft/Intel 等二进制的再分发证据仍有待核验；不要据此宣称已全面完成许可清关。前端随附的许可证、版权通知和来源文件必须保留。
