# PDF2Train 项目重构设计文档

## 版本信息
- 版本: v2.0
- 架构模式: Router-Manager-Service (RMS)
- 接口风格: Pan-RPC (POST-only + JSON Request Body)
- 重构日期: 2026-01-20

---
## 1. 核心设计原则
```
1. Router: 仅做参数解析与响应封装，禁止业务逻辑。

2. Manager: 业务逻辑编排中心，负责跨 Service 调用、事务控制、复杂计算。

3. Service: 原子数据访问层，直接操作数据库/ORM，不含业务分支判断。

4. Schema vs DTO: api/schema 定义前端交互，core/schema 定义数据库传输对象。
```

## 2. 项目目录结构 (Project Structure)
project_root/
├── api/                                  # [API Layer]
│   ├── routers/                          # 路由定义
│   │   ├── __init__.py
│   │   ├── dashboard_router.py           # 仪表盘
│   │   ├── pdf_document_router.py        # PDF文档与解析
│   │   ├── document_chunk_router.py      # 切片管理
│   │   ├── instruction_router.py         # 指令集管理
│   │   ├── knowledge_base_router.py      # 知识库
│   │   ├── embedding_router.py           # 向量化与检索
│   │   ├── llm_config_router.py          # 模型配置
│   │   ├── pipeline_task_router.py       # 任务流水线
│   │   └── storage_router.py             # 文件存储
│   │
│   ├── schema/                           # [Request/Response Schema]
│   │   ├── ... (对应每个Router的Schema文件)
│   │
│   └── dependencies.py                   # 依赖注入 (get_manager)
│
├── core/                                 # [Core Layer]
│   ├── manager/                          # 业务逻辑层
│   │   ├── __init__.py
│   │   ├── dashboard_manager.py
│   │   ├── pdf_document_manager.py
│   │   ├── document_chunk_manager.py
│   │   ├── instruction_manager.py
│   │   ├── knowledge_base_manager.py
│   │   ├── embedding_manager.py          # 包含 retrieval 逻辑
│   │   ├── llm_config_manager.py
│   │   └── pipeline_task_manager.py
│   │
│   ├── service/                          # 数据原子服务层
│   │   ├── ... (对应每个实体的 CRUD Service)
│   │
│   ├── schema/                           # [DTO] 数据库传输对象
│   │   ├── ... (对应数据库表的 Pydantic Model)
│   │
│   └── table/                            # SQLAlchemy ORM 模型
│       └── ...
│
└── main_server.py                        # 程序入口
## 3. 通用定义 (Common Definitions)
### 3.1 基础 Schema (api/schema/base_schema.py)
```python
from pydantic import BaseModel
from typing import Optional

class IDRequest(BaseModel):
    id: int | str

class PageRequest(BaseModel):
    page: int = 1
    page_size: int = 20
    keyword: Optional[str] = None
```
## 4. 模块详细设计 (Module Specifications)
### 4.1 仪表盘模块 (Dashboard)
职责: 提供全局统计信息和最近任务概览。
#### Router (api/routers/dashboard_router.py)
```python
# GET /api/dashboard/stats
async def get_stats(manager: DashboardManager): pass

# GET /api/dashboard/recent-jobs
async def get_recent_jobs(limit: int, manager: DashboardManager): pass
```
#### Manager (core/manager/dashboard_manager.py)
```python
class DashboardManager:
    async def get_global_stats(self) -> dict:
        """
        聚合统计：
        1. 调用 PdfDocumentService.count_all()
        2. 调用 DocumentChunkService.count_all()
        3. 调用 PipelineTaskService.count_running()
        """
        pass

    async def get_recent_jobs(self, limit: int) -> list:
        """调用 PipelineTaskService 获取最新记录"""
        pass
```
### 4.2 知识库模块 (Knowledge Base)
职责: 管理知识库元数据及其关联的文档。
#### Schema (api/schema/knowledge_base_schema.py)
```python
KBCreateRequest: name, desc, embedding_model, settings

KBUpdateRequest: id, name, desc, settings

KBDeleteRequest: id

KBListRequest: page, size, keyword

KBUpdateDocsRequest: kb_id, doc_ids: List[int]
```
#### Router (api/routers/knowledge_base_router.py)
```python
# POST /api/knowledge_base/create
async def create_kb(req: KBCreateRequest, manager: KnowledgeBaseManager): pass

# POST /api/knowledge_base/delete
async def delete_kb(req: KBDeleteRequest, manager: KnowledgeBaseManager): pass

# POST /api/knowledge_base/update
async def update_kb(req: KBUpdateRequest, manager: KnowledgeBaseManager): pass

# POST /api/knowledge_base/list
async def list_kb(req: KBListRequest, manager: KnowledgeBaseManager): pass

# POST /api/knowledge_base/update_docs
async def bind_docs_to_kb(req: KBUpdateDocsRequest, manager: KnowledgeBaseManager): pass
```
#### Manager (core/manager/knowledge_base_manager.py)
```python
class KnowledgeBaseManager:
    async def create_kb(self, dto: KnowledgeBaseDTO) -> int:
        """创建记录并初始化默认配置"""
        pass

    async def delete_kb(self, kb_id: int) -> bool:
        """
        1. 检查关联文档
        2. 解绑文档 (PdfService.unbind_kb)
        3. 清理远程向量 (EmbeddingManager.delete_collection)
        4. 物理删除记录
        """
        pass

    async def bind_docs(self, kb_id: int, doc_ids: List[int]):
        """批量更新文档的 kb_id 字段"""
        pass
```
### 4.3 PDF 文档模块 (PDF Document)
职责: 处理 PDF 上传、解析 (PDF2MD)、元数据管理、内容修正。
#### Schema (api/schema/pdf_document_schema.py)
```python
DocContentSaveRequest: doc_id, content

DocExportRequest: kb_id (导出该KB下所有书)

Pdf2MdConvertRequest: doc_id
```
#### Router (api/routers/pdf_document_router.py)
```python
# POST /api/pdf_document/list
async def list_docs(req: DocListRequest, manager: PdfDocumentManager): pass

# POST /api/pdf_document/delete
async def delete_doc(req: DocDeleteRequest, manager: PdfDocumentManager): pass

# POST /api/pdf_document/update
async def update_doc(req: DocUpdateRequest, manager: PdfDocumentManager): pass

# POST /api/pdf_document/unassigned
async def get_unassigned_docs(req: PageRequest, manager: PdfDocumentManager): pass

# GET /api/pdf_document/content?doc_id=...
async def get_content(doc_id: int, manager: PdfDocumentManager): pass

# POST /api/pdf_document/content/save
async def save_content(req: DocContentSaveRequest, manager: PdfDocumentManager): pass

# GET /api/pdf_document/statistics?doc_id=...
async def get_stats(doc_id: int, manager: PdfDocumentManager): pass

# GET /api/pdf_document/chunk_count?doc_id=...
async def get_chunk_count(doc_id: int, manager: PdfDocumentManager): pass

# POST /api/pdf_document/get_doc_count_by_kb_id
async def count_by_kb(req: IDRequest, manager: PdfDocumentManager): pass

# POST /api/pdf_document/export_books_jsonl
async def export_books(req: DocExportRequest, manager: PdfDocumentManager): pass

# POST /api/pdf2md/convert
async def run_pdf2md(req: Pdf2MdConvertRequest, manager: PdfDocumentManager): pass
```
#### Manager (core/manager/pdf_document_manager.py)
```
class PdfDocumentManager:
    async def trigger_parse_task(self, doc_id: int):
        """
        1. 创建/重置 PipelineTask (TaskType.MINERU_EXTRACT)
        2. 异步调用 GPU/Worker 进行解析
        """
        pass

    async def get_doc_content(self, doc_id: int) -> str:
        """从 MinIO 或 DB 读取 Markdown 全文"""
        pass

    async def save_doc_content(self, doc_id: int, content: str):
        """
        1. 更新 Markdown 内容
        2. 标记文档状态为 '需重新切片'
        """
        pass

    async def delete_doc(self, doc_id: int):
        """级联删除：Task -> Chunks -> Instructions -> Doc"""
        pass
```
### 4.4 文档切片模块 (Document Chunk)
职责: 切片列表、手动修正、触发切片任务、导出。
#### Router (api/routers/document_chunk_router.py)
```python
# POST /api/document_chunk/list
async def list_chunks(req: ChunkListRequest, manager: DocumentChunkManager): pass

# POST /api/document_chunk/update
async def update_chunk(req: ChunkUpdateRequest, manager: DocumentChunkManager): pass

# POST /api/document_chunk/delete
# POST /api/document_chunk/delete_by_id (别名)
async def delete_chunk(req: ChunkDeleteRequest, manager: DocumentChunkManager): pass

# GET /api/document_chunk/download/{doc_id}
async def download_json(doc_id: int, manager: DocumentChunkManager): pass

# POST /api/document_chunk/download/stream-pretrain-by-kb
async def stream_pretrain_data(req: IDRequest, manager: DocumentChunkManager): pass

# POST /api/chunk/run
async def run_chunking(req: ChunkRunRequest, manager: DocumentChunkManager): pass
```
#### Manager (core/manager/document_chunk_manager.py)
```python
class DocumentChunkManager:
    async def process_file_chunking(self, doc_id: int):
        """
        [核心业务]
        1. 读取 MD 内容
        2. 执行切片算法
        3. Service.batch_save_chunks
        4. 自动触发 Embedding (可选)
        """
        pass

    async def update_chunk(self, chunk_id: str, content: str):
        """
        1. 更新 DB 内容
        2. 调用 EmbeddingManager.sync_single_chunk 更新向量
        """
        pass
```
### 4.5 指令集模块 (Instruction)
职责: LLM 问答对生成、管理与导出。
#### Router (api/routers/instruction_router.py)
```python
# POST /api/instruction/list
async def list_instructions(req: InstListRequest, manager: InstructionManager): pass

# POST /api/instruction/run
async def run_generation(req: InstRunRequest, manager: InstructionManager): pass

# POST /api/instruction/update
async def update_instruction(req: InstUpdateRequest, manager: InstructionManager): pass

# POST /api/instruction/delete
async def delete_instruction(req: IDRequest, manager: InstructionManager): pass

# POST /api/instruction/clear_by_doc
async def clear_by_doc(req: IDRequest, manager: InstructionManager): pass

# GET /api/instruction/download_jsonl/{doc_id}
# GET /api/instruction/download_jsonl_all
# POST /api/instruction/download_jsonl_by_kb
async def download_dispatch(..., manager: InstructionManager): pass
```
#### Manager (core/manager/instruction_manager.py)
```python
class InstructionManager:
    async def trigger_generation(self, doc_id: int, config_id: int):
        """
        1. 获取 DocumentChunks
        2. 组装 Prompt
        3. 异步调用 LLM 生成
        4. 保存结果到 instruction_datum 表
        """
        pass
```
### 4.6 向量与检索模块 (Embedding & Retrieval)
职责: 向量化任务管理、向量库 CRUD、混合检索。
#### Router (api/routers/embedding_router.py)
```python
# POST /api/embedding/run
async def run_embedding_task(req: IDRequest, manager: EmbeddingManager): pass

# POST /api/vector/update
async def update_vector(req: VectorUpdateRequest, manager: EmbeddingManager): pass

# POST /api/vector/search (或 /api/retrieval/search)
async def search_vector(req: SearchRequest, manager: RetrievalManager): pass
```
#### Manager (core/manager/embedding_manager.py)
```python
class EmbeddingManager:
    async def run_doc_embedding(self, doc_id: int):
        """
        1. 获取未索引的 Chunks + Instructions
        2. 分批调用 Embedding 模型
        3. Upsert 到 Qdrant
        4. 更新 DB is_indexed=True
        """
        pass

class RetrievalManager:
    async def hybrid_search(self, query: str, kb_ids: List[int], top_k: int):
        """
        1. Embedding Query
        2. Vector Search (Semantic)
        3. Keyword Search (Lexical, Optional)
        4. Rerank Results
        """
        pass
```
### 4.7 任务流水线模块 (Pipeline Task)
职责: 查询异步任务状态。
#### Router (api/routers/pipeline_task_router.py)
```python
# GET /api/pipeline/tasks?doc_id=...
async def get_tasks(doc_id: int, manager: PipelineTaskManager): pass
```
#### Service (core/service/pipeline_task_service.py)
```python
class PipelineTaskService:
    async def get_tasks_by_doc(self, doc_id: int) -> List[PipelineTask]: pass
    async def update_status(self, task_id: int, status: int, detail: str): pass
```
### 4.8 LLM 配置模块 (LLM Config)
职责: 管理模型密钥、API地址、类型。
#### Router (api/routers/llm_config_router.py)
```python
# POST /api/llm_config/list
# POST /api/llm_config/create
# POST /api/llm_config/update
# POST /api/llm_config/delete
# POST /api/llm_config/type_list
# POST /api/llm_config/provider_list
```

### 4.9 存储模块 (Storage)
职责: 文件上传与 URL 签名。
#### Router (api/routers/storage_router.py)
```python
# POST /api/storage/upload
async def upload_file(file: UploadFile, manager: StorageManager): pass

# POST /api/storage/url
async def get_presigned_url(req: PathRequest, manager: StorageManager): pass
```
