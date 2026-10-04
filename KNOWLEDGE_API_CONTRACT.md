# 工厂知识库接口契约

本阶段仅本地资料导入、审核、分类、关键词检索和两页分析可追溯引用。没有OCR、向量/嵌入、联网、邮件发送或新供应商请求。

## 数据与状态

类别值（category）：product 产品、capability 能力边界、certification 认证、case 成功案例、sales_rule 销售规则。

资料默认review_status=unverified（未核实）；用户可改为confirmed（用户确认）或rejected（不采用）。用户确认不等于外部认证或真实性证明。enabled可停用/恢复。expires_on为可选YYYY-MM-DD，过期/同类别同主题冲突或多版本冲突会提示，不自动合并。更新创建新版本、保留旧原件和旧历史引用。

## 导入预览 / 确认

GET `/api/knowledge/meta`：categories、formats、max_file_bytes、max_text_chars、max_selected_chunks、network:false、ocr:false、notice，供UI据实显示能力。

POST `/api/knowledge/preview`：`{filename?, content_base64?, text?, title, category, expires_on?, document_id?, expected_version?}`。文件或粘贴text二选一；text粘贴默认TXT。更新时提供document_id与expected_version。原文件名不作为文件路径。文件≤5MiB，提取正文≤120000字符，PDF≤80页；DOCX限定ZIP条目/解压量，无宏或外链抓取。PDF仅文本，扫描/无文本明确拒绝，不做OCR。

返回 `{job_id, title, filename, category, expires_on, extracted_chars, page_count, paragraph_count, chunk_count, preview_chunks, warnings, expires_at}`。preview_chunks含 `{text,location}`，最多显示前12块，说明总块数；预览不入库，不外发。

POST `/api/knowledge/confirm`：`{job_id,consent:true}`。返回已入库document；同job重试幂等。新版本默认未核实，更新版本CAS冲突409不覆盖。原件与提取文本/分块都留在本地SQLite。

POST `/api/knowledge/discard`：`{job_id}`，释放未确认预览。

## 文档列表 / 阅读 / 审核

GET `/api/knowledge/documents?page=1&page_size=25&category=&q=&include_disabled=0`：分页items，含id,title,category,current_version,enabled,review_status,expires_on,expired,conflict,warnings,filename,created_at,updated_at。未核实/过期/冲突清楚标记。

GET `/api/knowledge/document?id=...&version=...`：选定版本元数据与完整提取文本/chunks/版本列表，不含原件base64。原件下载 GET `/api/knowledge/original?id=...&version=...`，仅明确文档ID/整数版本，通过Content-Disposition下载，不允许任意路径。

POST `/api/knowledge/status`：`{document_id,expected_version,enabled?:bool,review_status?:"unverified"|"confirmed"|"rejected"}`。版本CAS；审核只针对当前版本。停用不永久删除，不改旧分析。

## 本地检索

GET `/api/knowledge/search?q=...&category=&limit=10`：`{items,total,method:"local_keywords",notice}`。空查询用于浏览启用的当前资料片段；有查询按本地关键词匹配，无命中返回空列表。没有语义检索承诺。只返回启用资料的当前版本；拒绝采用版本不进入检索，未核实/过期可以显示但醒目标记，用户自行决定是否选入。

items含 `{document_id,version,chunk_id,title,filename,category,review_status,expired,conflict,text,location,score}`。location给出页码或段落定位。selection载荷只用 `{document_id,version,chunk_id}`，不允许前端伪造片段文本。

## 两页分析整合

GET `/api/model/context?customer_id=...&knowledge_selections=<JSON数组>`：最多6个所选片段。返回原有范围及 `knowledge:{items:[上述来源/片段],warnings,selection_token}`；input_bytes包含所选片段及引用目录。无selection保持兼容，默认不发送工厂知识库。发送前显示所选原文、来源、版本/审核状态、目标模型和费用提醒。

POST `/api/analyze`：原body加 `knowledge_selections:[{document_id,version,chunk_id}]`；真实调用仍需consent=true、revision、context_token与request_id。不得默认勾选全部资料；预览和实际选取一致，由后端核对版本/停用/审核变化，改变则409需重新预览。

模型通过同一evidence_catalog选择短ref_id，后端区分source=knowledge与source=customer/factory/record；知识引用保留 `{source:"knowledge",document_id,version,chunk_id,title,filename,location,review_status,quote}`。UI不得把知识片段当客户需求/采购事实；未核实资料或能力边界仍如实标注。

历史input_scope保存所选文档版本/片段、必要来源信息和引用，旧历史不随停用/更新改写。新检索不再返回停用或旧版本。模型调用需明确授权，Key仅存进程内存。


实现补充：document 返回 {id,title,version,current_version,revision,...,selected_version,content:{extracted_text,...},chunks,versions}。status 支持可选 expected_revision；保存版本用 expected_version。读取 meta.formats；PDF解析需要 requirements.txt 中的 pypdf；扫描PDF拒绝。

来源主体补充：preview 支持可选 source_company（最多200字符），文档、版本、检索、所选片段、引用和历史保留该文本。兄弟公司关系仅按用户确认；不推断股权/认证/专利/能力共享。


## 原文核对状态 source-review-v1

新增 source_checked：已核对原文（企业自述），只覆盖 review_info.reviewed_fields，不能证明能力、适配、有效认证、专利权属或现时供货。旧confirmed保持用户确认（非独立证明），不自动降级；本轮不提供自动授予独立有效证明的入口。

meta 新增 review_protocol 和 review_statuses；documents/search 支持 review_status 筛选。status 的 source_checked 请求必须带 expected_version、expected_revision、review_reason（≤2000字符）、reviewed_fields（1–30项）和 review_source:{filename,sha256,pdf_pages,printed_pages}。页码1–80、文件名不含路径、SHA256为64位十六进制。审核时间由服务端记录。

返回 review_info、review_label、independently_proven:false，检索、所选片段、引用及历史保留语义。kb_reviews 只追加审核事件，包含前后状态/版本/理由/页码/时间；触发器拒绝更新或删除。GET document 返回对应版本review_history。新版本重置为需核实，旧版本审计和旧分析正文不改写。
