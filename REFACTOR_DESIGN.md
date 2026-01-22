# PDF2Train 项目重构设计文档

## 版本信息
- **版本**: v2.0
- **架构模式**: Router-Manager-Service (RMS)
- **接口风格**: 泛RPC风格 (POST-only + JSON Request Object)
- **重构日期**: 2026-01-20

---

## 1. 核心重构规则

### 1.1 分层职责定义

```
┌─────────────────────────────────────────────────────────────┐
│  Router Layer (api/routers/)                                │
│  - 接收前端请求，解析 Request Schema                          │
│  - 将 Schema 转换为 DTO 传递给 Manager                        │
│  - 封装统一响应格式返回前端                                    │
│  - 禁止包含任何业务逻辑                                        │
└─────────────────────────────────────────────────────────────┘
                            ↓
┌─────────────────────────────────────────────────────────────┐
│  Manager Layer (core/manager/)                              │
│  - 业务逻辑编排中心                                           │
│  - 处理单Service或跨Service操作                               │
│  - 负责数据脱敏、格式转换、事务控制                            │
│  - 接收和返回 DTO 对象                                        │
└─────────────────────────────────────────────────────────────┘
                            ↓
┌─────────────────────────────────────────────────────────────┐
│  Service Layer (core/service/)                              │
│  - 数据库交互层                                               │
│  - 负责单表或跨表的数据库操作                                  │
│  - 不包含业务逻辑判断                                         │
│  - 使用 DTO 与数据库交互                                      │
└─────────────────────────────────────────────────────────────┘
```

### 1.2 Schema vs DTO 设计原则

#### Router Schema (api/schema/)
- **用途**: 定义前端交互契约
- **特点**:
  - 可以使用 Enum 类型 (如 ModelType, LLMProvider)
  - 必须为所有传参创建实例化对象 (包括 page, page_size 等业务参数)
  - 可以引用 core/schema 中的 DTO
  - 使用泛RPC风格: POST-only + JSON Request Object

#### Core DTO (core/schema/)
- **用途**: 定义数据库传输对象
- **特点**:
  - 只包含数据库存储字段，使用字符串类型 (不使用 Enum)
  - 不包含业务参数 (如 page, page_size)
  - 不能引用 api/schema 中的 Schema
  - Manager 和 Service 如果存储参数只有一个，不需要创建 DTO

### 1.3 数据流转规则

```python
# 1. Router 接收前端 Schema (包含 Enum)
@router.post("/create")
async def create_config(req: LLMConfigCreateReq, manager: LLMConfigManager):
    # 2. Router 负责转换: Schema(Enum) -> DTO(String)
    dto = LLMConfigCoreDTO(
        name=req.name,
        model_type=req.model_type.value,  # Enum -> Str
        provider=req.provider.value,       # Enum -> Str
        ...
    )
    # 3. 调用 Manager，传递 DTO
    new_id = await manager.create_config(dto)
    return make_response(True, "创建成功", {"id": new_id})

# 4. Manager 接收 DTO，处理业务逻辑
async def create_config(self, dto: LLMConfigCoreDTO) -> int:
    # 业务逻辑: 如果设为默认，重置其他配置
    config_id = await self.service.create(dto)
    if dto.is_default:
        await self.service.reset_defaults_except(...)
    return config_id

# 5. Service 接收 DTO，直接操作数据库
async def create(self, dto: LLMConfigCoreDTO) -> int:
    return await self.sql_provider.add_record(dto.model_dump())
```

---

## 2. 项目目录结构

```
src/pdf2train/
├── api/                                    # API 层
│   ├── routers/                            # 路由定义
│   │   ├── __init__.py
│   │   ├── llm_config_router.py           # ✅ 已重构 (参考案例)
│   │   ├── pdf_document_router.py         # 待重构
│   │   ├── knowledge_base_router.py       # 待重构
│   │   ├── document_chunk_router.py       # 待重构
│   │   ├── instruction_router.py          # 待重构
│   │   ├── embedding_router.py            # 待重构
│   │   ├── pipeline_task_router.py        # 待重构
│   │   └── storage_router.py              # 待重构
│   │
│   ├── schema/                             # Request/Response Schema
│   │   ├── llm_config_schema.py           # ✅ 已重构
│   │   ├── pdf_document_schema.py         # 待创建
│   │   ├── knowledge_base_schema.py       # 待创建
│   │   ├── document_chunk_schema.py       # 待创建
│   │   ├── instruction_schema.py          # 待创建
│   │   ├── embedding_schema.py            # 待创建
│   │   ├── pipeline_task_schema.py        # 待创建
│   │   └── storage_schema.py              # 待创建
│   │
│   └── dependencies.py                     # 依赖注入
│
├── core/                                   # 核心层
│   ├── manager/                            # 业务逻辑层
│   │   ├── __init__.py
│   │   ├── llm_config_manager.py          # ✅ 已重构
│   │   ├── pdf_document_manager.py        # 部分重构
│   │   ├── knowledge_base_manager.py      # 部分重构
│   │   ├── document_chunk_manager.py      # 待重构
│   │   ├── instruction_manager.py         # 待创建
│   │   ├── embedding_manager.py           # 待创建
│   │   ├── pipeline_task_manager.py       # 待创建
│   │   └── storage_manager.py             # 待重构
│   │
│   ├── service/                            # 数据服务层
│   │   ├── llm_config_service.py          # ✅ 已重构
│   │   ├── pdf_document_service.py        # 部分重构
│   │   ├── knowledge_base_service.py      # 部分重构
│   │   ├── document_chunk_service.py      # 待重构
│   │   ├── instruction_datum_service.py   # 待重构
│   │   ├── instruction_gen_service.py     # 待重构
│   │   ├── embedding_service.py           # 待重构
│   │   ├── pipeline_task_service.py       # 待重构
│   │   ├── minio_service.py               # 待重构
│   │   └── search_service.py              # 待重构
│   │
│   ├── schema/                             # DTO 定义
│   │   ├── llm_config_dto.py              # ✅ 已重构
│   │   ├── pdf_document_dto.py            # 部分重构
│   │   ├── knowledge_base_dto.py          # 待创建
│   │   ├── document_chunk_dto.py          # 待创建
│   │   ├── instruction_dto.py             # 待创建
│   │   └── embedding_dto.py               # 待创建
│   │
│   ├── table/                              # ORM 模型 (不需要修改)
│   │   ├── llm_config.py
│   │   ├── pdf_document.py
│   │   ├── knowledge_base.py
│   │   ├── document_chunk.py
│   │   ├── instruction_datum.py
│   │   └── pipeline_task.py
│   │
│   ├── provider/                           # 数据库提供者
│   │   └── sql_provider.py
│   │
│   └── configs/                            # 配置
│       └── sql_config.py
│
└── utils/                                  # 工具类
    ├── log.py
    └── pdf_utils.py
```

---

## 3. 模块重构设计

### 3.1 LLM Config 模块 (✅ 已完成 - 参考案例)

#### 文件结构
```
api/schema/llm_config_schema.py          # Request Schema
api/routers/llm_config_router.py         # Router
core/schema/llm_config_dto.py            # DTO
core/manager/llm_config_manager.py       # Manager
core/service/llm_config_service.py       # Service
core/table/llm_config.py                 # ORM (不变)
```

#### 接口列表
```python
POST /api/llm_config/type_list           # 获取模型类型列表
POST /api/llm_config/provider_list       # 获取提供商列表
POST /api/llm_config/create              # 创建配置
POST /api/llm_config/update              # 更新配置
POST /api/llm_config/delete              # 删除配置
POST /api/llm_config/list                # 分页列表
POST /api/llm_config/default_config      # 获取默认配置详情
POST /api/llm_config/default_config_name # 获取默认配置名称
POST /api/llm_config/get_config_by_doc_id # 根据文档ID获取配置
```

---

### 3.2 PDF Document 模块

#### 文件结构
```
api/schema/pdf_document_schema.py        # 待创建
api/routers/pdf_document_router.py       # 待重构
core/schema/pdf_document_dto.py          # 已存在，需完善
core/manager/pdf_document_manager.py     # 已存在，需重构
core/service/pdf_document_service.py     # 已存在，需重构
core/table/pdf_document.py               # ORM (不变)
```

#### Schema 设计 (api/schema/pdf_document_schema.py)

```python
from pydantic import BaseModel, Field
from typing import Optional, List

# 1. 文档列表查询
class PdfDocListReq(BaseModel):
    page: int = 1
    page_size: int = 20
    kb_id: Optional[int] = None
    keyword: Optional[str] = None
    filter_step_type: Optional[int] = None
    filter_step_status: Optional[int] = None

# 2. 文档删除
class PdfDocDeleteReq(BaseModel):
    id: int = Field(..., description="文档ID")

# 3. 文档更新
class PdfDocUpdateReq(BaseModel):
    id: int = Field(..., description="文档ID")
    kb_id: Optional[int] = None
    file_name: Optional[str] = None
    original_title: Optional[str] = None
    author: Optional[str] = None
    summary: Optional[str] = None
    instruction_gen_llm_config: Optional[str] = None
    h_title_llm_config: Optional[str] = None
    embedding_llm_config: Optional[str] = None

# 4. 获取未分配文档
class PdfDocUnassignedReq(BaseModel):
    page: int = 1
    page_size: int = 20
    keyword: Optional[str] = None

# 5. 保存Markdown内容
class PdfDocContentSaveReq(BaseModel):
    doc_id: int = Field(..., description="文档ID")
    content: str = Field(..., description="Markdown内容")

# 6. 导出书籍
class PdfDocExportReq(BaseModel):
    kb_id: Optional[int] = None
    doc_ids: Optional[List[int]] = None

# 7. 根据知识库统计文档数
class PdfDocCountByKbReq(BaseModel):
    kb_id: int = Field(..., description="知识库ID")

# 8. PDF转Markdown
class Pdf2MdConvertReq(BaseModel):
    doc_id: int = Field(..., description="文档ID")
```

#### DTO 设计 (core/schema/pdf_document_dto.py)

```python
from pydantic import BaseModel
from typing import Optional, Dict, Any

# 1. 创建文档 DTO
class PdfDocCoreDTO(BaseModel):
    """用于创建文档记录"""
    kb_id: Optional[int] = None
    file_name: str
    file_hash: str
    file_size: int
    bucket_name: str
    object_name: str
    content_type: Optional[str] = None
    user_name: str
    status: int = 0
    page_count: int = 0
    author: Optional[str] = None
    original_title: Optional[str] = None
    cover_info: Optional[Dict[str, Any]] = None
    process_error: Optional[str] = None

# 2. 更新文档 DTO
class PdfDocUpdateDTO(BaseModel):
    """用于更新文档记录"""
    kb_id: Optional[int] = None
    file_name: Optional[str] = None
    original_title: Optional[str] = None
    author: Optional[str] = None
    summary: Optional[str] = None
    status: Optional[int] = None
    progress: Optional[int] = None
    process_error: Optional[str] = None
    instruction_gen_llm_config: Optional[str] = None
    h_title_llm_config: Optional[str] = None
    embedding_llm_config: Optional[str] = None

# 3. 文档筛选 DTO
class PdfDocFilterDTO(BaseModel):
    """用于复杂查询条件"""
    kb_id: Optional[int] = None
    keyword: Optional[str] = None
    filter_step_type: Optional[int] = None
    filter_step_status: Optional[int] = None
```

#### Router 设计 (api/routers/pdf_document_router.py)

```python
from fastapi import APIRouter, Depends
from pdf2train.api.schema.pdf_document_schema import *
from pdf2train.core.schema.pdf_document_dto import *
from pdf2train.core.manager.pdf_document_manager import PdfDocumentManager

router = APIRouter(prefix="/api/pdf_document", tags=["PDF Document"])

@router.post("/list")
async def list_docs(
    req: PdfDocListReq,
    manager: PdfDocumentManager = Depends(get_pdf_document_manager)
):
    """文档列表"""
    # Router 转换: Schema -> DTO
    filter_dto = PdfDocFilterDTO(
        kb_id=req.kb_id,
        keyword=req.keyword,
        filter_step_type=req.filter_step_type,
        filter_step_status=req.filter_step_status
    )
    result = await manager.list_documents(req.page, req.page_size, filter_dto)
    return make_response(True, "查询成功", result)

@router.post("/delete")
async def delete_doc(
    req: PdfDocDeleteReq,
    manager: PdfDocumentManager = Depends(get_pdf_document_manager)
):
    """删除文档"""
    success = await manager.delete_document(req.id)
    return make_response(True, "删除成功" if success else "删除失败")

@router.post("/update")
async def update_doc(
    req: PdfDocUpdateReq,
    manager: PdfDocumentManager = Depends(get_pdf_document_manager)
):
    """更新文档"""
    # Router 转换: Schema -> DTO
    update_dto = PdfDocUpdateDTO(
        kb_id=req.kb_id,
        file_name=req.file_name,
        original_title=req.original_title,
        author=req.author,
        summary=req.summary,
        instruction_gen_llm_config=req.instruction_gen_llm_config,
        h_title_llm_config=req.h_title_llm_config,
        embedding_llm_config=req.embedding_llm_config
    )
    success = await manager.update_document(req.id, update_dto)
    return make_response(True, "更新成功")

@router.post("/content/save")
async def save_content(
    req: PdfDocContentSaveReq,
    manager: PdfDocumentManager = Depends(get_pdf_document_manager)
):
    """保存Markdown内容"""
    success = await manager.save_markdown_content(req.doc_id, req.content)
    return make_response(True, "保存成功")

# ... 其他接口
```

#### Manager 职责 (core/manager/pdf_document_manager.py)

```python
class PdfDocumentManager:
    """
    业务逻辑:
    1. upload_and_create: 文件上传 + 判重 + 元数据提取 + MinIO上传 + 数据库创建
    2. list_documents: 分页查询 + 可选的URL生成
    3. update_document: 更新文档 + 可能触发重新切片
    4. delete_document: 级联删除 (MinIO + 数据库)
    5. get_markdown_content: 从MinIO读取Markdown
    6. save_markdown_content: 更新MinIO + 标记需重新切片
    7. export_books_jsonl: 导出JSONL格式
    """
```

#### Service 职责 (core/service/pdf_document_service.py)

```python
class PdfDocumentService:
    """
    数据库操作:
    1. create: 创建记录
    2. update: 更新记录
    3. delete: 删除记录
    4. get_by_id: 根据ID查询
    5. get_by_hash: 根据Hash查询 (判重)
    6. get_with_relations: 预加载关联数据
    7. search_paginated: 分页查询 + 复杂筛选
    """
```

---

### 3.3 Knowledge Base 模块

#### Schema 设计 (api/schema/knowledge_base_schema.py)

```python
from pydantic import BaseModel, Field
from typing import Optional, List, Dict, Any

# 1. 创建知识库
class KBCreateReq(BaseModel):
    name: str = Field(..., description="知识库名称")
    description: Optional[str] = None
    avatar_url: Optional[str] = None
    embedding_model: str = Field(default="bge-large-zh")
    user_id: int = Field(..., description="创建者ID")
    settings: Optional[Dict[str, Any]] = None  # 检索配置

# 2. 更新知识库
class KBUpdateReq(BaseModel):
    id: int = Field(..., description="知识库ID")
    name: Optional[str] = None
    description: Optional[str] = None
    avatar_url: Optional[str] = None
    settings: Optional[Dict[str, Any]] = None

# 3. 删除知识库
class KBDeleteReq(BaseModel):
    id: int = Field(..., description="知识库ID")

# 4. 知识库列表
class KBListReq(BaseModel):
    page: int = 1
    page_size: int = 20
    keyword: Optional[str] = None

# 5. 关联文档到知识库
class KBUpdateDocsReq(BaseModel):
    kb_id: int = Field(..., description="知识库ID")
    doc_ids: List[int] = Field(..., description="文档ID列表")
```

#### DTO 设计 (core/schema/knowledge_base_dto.py)

```python
from pydantic import BaseModel
from typing import Optional, Dict, Any

# 1. 创建知识库 DTO
class KnowledgeBaseCoreDTO(BaseModel):
    """用于创建知识库"""
    name: str
    description: Optional[str] = None
    avatar_url: Optional[str] = None
    embedding_model: str
    user_id: int
    _settings: Optional[Dict[str, Any]] = None  # 注意字段名

# 2. 更新知识库 DTO
class KnowledgeBaseUpdateDTO(BaseModel):
    """用于更新知识库"""
    name: Optional[str] = None
    description: Optional[str] = None
    avatar_url: Optional[str] = None
    _settings: Optional[Dict[str, Any]] = None
```

---

### 3.4 Document Chunk 模块

#### Schema 设计 (api/schema/document_chunk_schema.py)

```python
from pydantic import BaseModel, Field
from typing import Optional

# 1. 切片列表
class ChunkListReq(BaseModel):
    page: int = 1
    page_size: int = 20
    doc_id: Optional[int] = None
    kb_id: Optional[int] = None
    keyword: Optional[str] = None

# 2. 更新切片
class ChunkUpdateReq(BaseModel):
    chunk_id: str = Field(..., description="切片ID")
    content: str = Field(..., description="切片内容")

# 3. 删除切片
class ChunkDeleteReq(BaseModel):
    chunk_id: str = Field(..., description="切片ID")

# 4. 执行切片任务
class ChunkRunReq(BaseModel):
    doc_id: int = Field(..., description="文档ID")
    chunk_size: int = Field(default=512, description="切片大小")
    overlap: int = Field(default=50, description="重叠大小")

# 5. 导出预训练数据
class ChunkExportPretrainReq(BaseModel):
    kb_id: int = Field(..., description="知识库ID")
```

#### DTO 设计 (core/schema/document_chunk_dto.py)

```python
from pydantic import BaseModel
from typing import Optional

# 1. 创建切片 DTO
class DocumentChunkCoreDTO(BaseModel):
    """用于创建切片"""
    chunk_id: str
    doc_id: int
    kb_id: Optional[int] = None
    content: str
    chunk_index: int
    token_count: int
    metadata: Optional[dict] = None

# 2. 更新切片 DTO
class DocumentChunkUpdateDTO(BaseModel):
    """用于更新切片"""
    content: Optional[str] = None
    token_count: Optional[int] = None
    is_indexed: Optional[bool] = None

# 3. 切片筛选 DTO
class DocumentChunkFilterDTO(BaseModel):
    """用于查询条件"""
    doc_id: Optional[int] = None
    kb_id: Optional[int] = None
    keyword: Optional[str] = None
```

---

### 3.5 Instruction 模块

#### Schema 设计 (api/schema/instruction_schema.py)

```python
from pydantic import BaseModel, Field
from typing import Optional

# 1. 指令列表
class InstructionListReq(BaseModel):
    page: int = 1
    page_size: int = 20
    doc_id: Optional[int] = None
    kb_id: Optional[int] = None
    keyword: Optional[str] = None

# 2. 生成指令
class InstructionRunReq(BaseModel):
    doc_id: int = Field(..., description="文档ID")
    llm_config_name: str = Field(..., description="LLM配置名称")

# 3. 更新指令
class InstructionUpdateReq(BaseModel):
    id: int = Field(..., description="指令ID")
    instruction: Optional[str] = None
    input: Optional[str] = None
    output: Optional[str] = None

# 4. 删除指令
class InstructionDeleteReq(BaseModel):
    id: int = Field(..., description="指令ID")

# 5. 清空文档的所有指令
class InstructionClearByDocReq(BaseModel):
    doc_id: int = Field(..., description="文档ID")

# 6. 导出指令
class InstructionExportReq(BaseModel):
    doc_id: Optional[int] = None
    kb_id: Optional[int] = None
```

---

### 3.6 Storage 模块

#### Schema 设计 (api/schema/storage_schema.py)

```python
from pydantic import BaseModel, Field
from typing import Optional

# 1. 获取预签名URL
class StorageUrlReq(BaseModel):
    bucket_name: str = Field(..., description="存储桶名称")
    object_name: str = Field(..., description="对象路径")
    expires: int = Field(default=3600, description="过期时间(秒)")

# 2. 删除文件
class StorageDeleteReq(BaseModel):
    bucket_name: str = Field(..., description="存储桶名称")
    object_name: str = Field(..., description="对象路径")
```

---

## 4. 重构实施计划

### 阶段一: 核心模块重构 (优先级: 高)

1. **LLM Config** ✅ 已完成
2. **PDF Document** - 3天
   - 创建 pdf_document_schema.py
   - 重构 pdf_document_router.py
   - 完善 pdf_document_dto.py
   - 重构 pdf_document_manager.py
   - 重构 pdf_document_service.py

3. **Knowledge Base** - 2天
   - 创建 knowledge_base_schema.py
   - 重构 knowledge_base_router.py (目前不存在)
   - 创建 knowledge_base_dto.py
   - 重构 knowledge_base_manager.py
   - 重构 knowledge_base_service.py

### 阶段二: 数据处理模块 (优先级: 中)

4. **Document Chunk** - 2天
5. **Instruction** - 2天
6. **Storage** - 1天

### 阶段三: 高级功能模块 (优先级: 低)

7. **Embedding & Retrieval** - 3天
8. **Pipeline Task** - 1天

---

## 5. 重构检查清单

### 每个模块重构完成后需要检查:

- [ ] Router 层不包含任何业务逻辑
- [ ] Router 正确将 Schema 转换为 DTO
- [ ] Router 使用统一的响应格式
- [ ] Manager 层包含所有业务逻辑
- [ ] Manager 层正确处理跨 Service 调用
- [ ] Service 层只包含数据库操作
- [ ] Service 层不包含业务判断
- [ ] Schema 使用 Enum 类型
- [ ] Schema 为所有参数创建实例化对象
- [ ] DTO 只包含数据库字段
- [ ] DTO 使用字符串类型 (不使用 Enum)
- [ ] DTO 不包含业务参数
- [ ] 所有接口使用 POST 方法
- [ ] 所有接口使用 JSON Request Body

---

## 6. 注意事项

### 6.1 向后兼容
- 重构过程中保持 API 接口路径不变
- 逐步迁移，确保每个模块独立可测试

### 6.2 测试策略
- 每个模块重构后编写单元测试
- 使用 pytest 进行自动化测试
- 测试覆盖率目标: 80%+

### 6.3 文档更新
- 同步更新 API 文档
- 更新部署文档
- 更新开发者指南

---

## 7. 参考案例: LLM Config 模块

详细实现请参考:
- `api/schema/llm_config_schema.py`
- `api/routers/llm_config_router.py`
- `core/schema/llm_config_dto.py`
- `core/manager/llm_config_manager.py`
- `core/service/llm_config_service.py`

这是标准的重构模板，其他模块应遵循相同的模式。

---

## 8. 数据库表结构设计

### 8.1 核心表关系图

```
┌─────────────────┐
│ knowledge_base  │
│ - id (PK)       │
│ - name          │
│ - embedding_model│
│ - settings      │
└────────┬────────┘
         │ 1:N
         ↓
┌─────────────────┐       ┌──────────────────┐
│ pdf_document    │       │ pipeline_task    │
│ - id (PK)       │←──────│ - id (PK)        │
│ - kb_id (FK)    │  1:N  │ - doc_id (FK)    │
│ - file_name     │       │ - task_type      │
│ - file_hash     │       │ - status         │
│ - status        │       │ - result_data    │
└────────┬────────┘       └──────────────────┘
         │ 1:N
         ├──────────────────┬──────────────────┐
         ↓                  ↓                  ↓
┌─────────────────┐  ┌──────────────────┐  ┌──────────────────┐
│ document_chunk  │  │ instruction_datum│  │ llm_config       │
│ - chunk_id (PK) │  │ - id (PK)        │  │ - id (PK)        │
│ - doc_id (FK)   │  │ - doc_id (FK)    │  │ - name           │
│ - kb_id         │  │ - kb_id          │  │ - model_type     │
│ - content       │  │ - instruction    │  │ - provider       │
│ - is_indexed    │  │ - input          │  │ - api_key        │
└─────────────────┘  │ - output         │  └──────────────────┘
                     │ - references     │
                     │ - is_indexed     │
                     └──────────────────┘
```

### 8.2 表字段详细说明

#### knowledge_base (知识库表)
```sql
CREATE TABLE knowledge_base (
    id SERIAL PRIMARY KEY,
    name VARCHAR(255) NOT NULL UNIQUE,
    description TEXT,
    avatar_url VARCHAR(512),
    embedding_model VARCHAR(100) DEFAULT 'bge-large-zh',
    user_id INTEGER NOT NULL,
    _settings JSONB,  -- 检索配置 (top_k, rerank等)
    create_time TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    update_time TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
```

#### pdf_document (PDF文档表)
```sql
CREATE TABLE pdf_document (
    id SERIAL PRIMARY KEY,
    kb_id INTEGER REFERENCES knowledge_base(id),
    file_name VARCHAR(255) NOT NULL,
    file_hash VARCHAR(64) UNIQUE NOT NULL,
    file_size BIGINT NOT NULL,
    bucket_name VARCHAR(100) NOT NULL,
    object_name VARCHAR(512) NOT NULL,
    content_type VARCHAR(100),
    user_name VARCHAR(100),
    status INTEGER DEFAULT 0,  -- 0:待处理, 1:处理中, 2:成功, 3:失败
    page_count INTEGER DEFAULT 0,
    author VARCHAR(255),
    original_title VARCHAR(512),
    summary TEXT,
    cover_info JSONB,
    process_error TEXT,
    instruction_gen_llm_config VARCHAR(100),  -- 指令生成配置名
    h_title_llm_config VARCHAR(100),          -- 标题生成配置名
    embedding_llm_config VARCHAR(100),        -- 向量化配置名
    create_time TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    update_time TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
```

#### document_chunk (文档切片表)
```sql
CREATE TABLE document_chunk (
    chunk_id VARCHAR(100) PRIMARY KEY,
    doc_id INTEGER NOT NULL REFERENCES pdf_document(id) ON DELETE CASCADE,
    kb_id INTEGER REFERENCES knowledge_base(id),
    content TEXT NOT NULL,
    chunk_index INTEGER NOT NULL,
    chunk_index_description VARCHAR(255),  -- 如 "第1章-第3节"
    token_count INTEGER DEFAULT 0,
    metadata JSONB,  -- 包含 h1~h6 标题信息
    is_indexed BOOLEAN DEFAULT FALSE,
    create_time TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    update_time TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(doc_id, chunk_index)
);
CREATE INDEX idx_chunk_doc_id ON document_chunk(doc_id);
CREATE INDEX idx_chunk_kb_id ON document_chunk(kb_id);
CREATE INDEX idx_chunk_indexed ON document_chunk(is_indexed);
```

#### instruction_datum (指令数据表)
```sql
CREATE TABLE instruction_datum (
    id SERIAL PRIMARY KEY,
    doc_id INTEGER NOT NULL REFERENCES pdf_document(id) ON DELETE CASCADE,
    kb_id INTEGER REFERENCES knowledge_base(id),
    instruction TEXT NOT NULL,  -- 问题
    input TEXT,                 -- 上下文 (可选)
    output TEXT NOT NULL,       -- 答案
    references JSONB,           -- 引用的 chunk_id 列表
    chunk_index_description VARCHAR(255),
    metadata JSONB,
    is_indexed BOOLEAN DEFAULT FALSE,
    create_time TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    update_time TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX idx_instruction_doc_id ON instruction_datum(doc_id);
CREATE INDEX idx_instruction_kb_id ON instruction_datum(kb_id);
CREATE INDEX idx_instruction_indexed ON instruction_datum(is_indexed);
```

#### pipeline_task (流水线任务表)
```sql
CREATE TABLE pipeline_task (
    id SERIAL PRIMARY KEY,
    doc_id INTEGER NOT NULL REFERENCES pdf_document(id) ON DELETE CASCADE,
    task_type INTEGER NOT NULL,  -- 1:PDF2MD, 2:Chunk, 3:Instruction, 4:Embedding
    status INTEGER DEFAULT 0,    -- 0:待执行, 1:执行中, 2:成功, 3:失败
    progress INTEGER DEFAULT 0,  -- 0-100
    result_data JSONB,           -- 任务结果数据
    error_message TEXT,
    start_time TIMESTAMP,
    end_time TIMESTAMP,
    create_time TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    update_time TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(doc_id, task_type)
);
CREATE INDEX idx_task_doc_id ON pipeline_task(doc_id);
CREATE INDEX idx_task_status ON pipeline_task(status);
```

#### llm_config (LLM配置表)
```sql
CREATE TABLE llm_config (
    id SERIAL PRIMARY KEY,
    name VARCHAR(100) NOT NULL UNIQUE,
    model_type VARCHAR(50) NOT NULL,  -- 'chat', 'embedding', 'rerank'
    provider VARCHAR(50) NOT NULL,    -- 'openai', 'deepseek', 'zhipu'
    model_name VARCHAR(100) NOT NULL,
    api_key VARCHAR(255) NOT NULL,
    base_url VARCHAR(512),
    temperature FLOAT DEFAULT 0.7,
    max_tokens INTEGER,
    is_default BOOLEAN DEFAULT FALSE,
    is_active BOOLEAN DEFAULT TRUE,
    extra_params JSONB,
    create_time TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    update_time TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
```

---

## 9. 向量数据库设计 (Qdrant)

### 9.1 Collection 命名规范

```
collection_name = {embedding_model_name}
例如: "bge-large-zh", "bge-m3", "text-embedding-3-large"
```

### 9.2 Payload 结构

#### Chunk 向量 Payload
```json
{
    "chunk_id": "doc123_chunk_001",
    "doc_id": 123,
    "kb_id": 5,
    "content": "这是切片内容...",
    "chunk_index": 1,
    "chunk_index_description": "第1章-引言",
    "token_count": 512,
    "metadata": {
        "h1": "第一章",
        "h2": "引言",
        "h3": null
    },
    "data_type": "chunk",
    "create_time": "2026-01-20T10:00:00"
}
```

#### Instruction 向量 Payload
```json
{
    "instruction_id": 456,
    "doc_id": 123,
    "kb_id": 5,
    "instruction": "什么是机器学习?",
    "output": "机器学习是...",
    "references": ["doc123_chunk_001", "doc123_chunk_002"],
    "chunk_index_description": "第2章-第1节",
    "data_type": "instruction",
    "create_time": "2026-01-20T10:00:00"
}
```

### 9.3 检索过滤条件

```python
# 按知识库检索
filter = {
    "must": [
        {"key": "kb_id", "match": {"value": 5}}
    ]
}

# 按文档检索
filter = {
    "must": [
        {"key": "doc_id", "match": {"value": 123}}
    ]
}

# 按数据类型检索
filter = {
    "must": [
        {"key": "data_type", "match": {"value": "chunk"}}
    ]
}

# 组合检索
filter = {
    "must": [
        {"key": "kb_id", "match": {"value": 5}},
        {"key": "data_type", "match": {"value": "instruction"}}
    ]
}
```

---

## 10. 业务流程设计

### 10.1 文档处理完整流程

```
┌──────────────┐
│ 1. 上传PDF   │
│ - 文件判重   │
│ - MinIO存储  │
│ - 创建记录   │
└──────┬───────┘
       ↓
┌──────────────┐
│ 2. PDF2MD    │
│ - MinerU解析 │
│ - 提取元数据 │
│ - 保存MD     │
└──────┬───────┘
       ↓
┌──────────────┐
│ 3. 文档切片  │
│ - 读取MD     │
│ - 语义切分   │
│ - 保存Chunks │
└──────┬───────┘
       ↓
┌──────────────┐
│ 4. 指令生成  │
│ - 组装上下文 │
│ - LLM生成    │
│ - 保存指令   │
└──────┬───────┘
       ↓
┌──────────────┐
│ 5. 向量化    │
│ - Embedding  │
│ - Upsert向量 │
│ - 标记索引   │
└──────────────┘
```

### 10.2 知识库管理流程

#### 创建知识库
```python
1. 验证名称唯一性
2. 创建 knowledge_base 记录
3. 初始化默认检索配置
4. 返回 kb_id
```

#### 删除知识库
```python
1. 检查是否有关联文档
2. 解绑所有文档 (pdf_document.kb_id = NULL)
3. 删除 Qdrant 中该 kb_id 的所有向量
4. 删除 knowledge_base 记录
```

#### 文档关联到知识库
```python
1. 验证 kb_id 和 doc_ids 存在
2. 更新 pdf_document.kb_id
3. 更新 document_chunk.kb_id
4. 更新 instruction_datum.kb_id
5. 如果已向量化，更新 Qdrant Payload 中的 kb_id
```

### 10.3 删除操作级联规则

#### 删除文档
```python
1. 删除 MinIO 中的原始文件和 MD 文件
2. 删除 Qdrant 中的向量 (filter: doc_id)
3. 删除 instruction_datum (CASCADE)
4. 删除 document_chunk (CASCADE)
5. 删除 pipeline_task (CASCADE)
6. 删除 pdf_document
```

#### 删除切片
```python
1. 删除 Qdrant 中的向量 (filter: chunk_id)
2. 删除 document_chunk 记录
3. 更新 pipeline_task.result_data (chunk_count)
4. 如果 chunk_count = 0，重置任务状态
```

#### 删除指令
```python
1. 删除 Qdrant 中的向量 (filter: instruction_id)
2. 删除 instruction_datum 记录
3. 更新 pipeline_task.result_data (instruction_count)
4. 如果 instruction_count = 0，重置任务状态
```

---

## 11. 错误处理与日志规范

### 11.1 统一响应格式

```python
from pydantic import BaseModel
from typing import Optional, Any

class ApiResponse(BaseModel):
    success: bool
    message: str
    data: Optional[Any] = None
    error_code: Optional[str] = None

def make_response(
    success: bool,
    message: str,
    data: Any = None,
    error_code: str = None
) -> ApiResponse:
    return ApiResponse(
        success=success,
        message=message,
        data=data,
        error_code=error_code
    )
```

### 11.2 错误码定义

```python
class ErrorCode:
    # 通用错误 (1xxx)
    INVALID_PARAMS = "1001"
    RESOURCE_NOT_FOUND = "1002"
    DUPLICATE_RESOURCE = "1003"
    PERMISSION_DENIED = "1004"

    # 文档相关 (2xxx)
    DOC_UPLOAD_FAILED = "2001"
    DOC_PARSE_FAILED = "2002"
    DOC_NOT_FOUND = "2003"
    DOC_ALREADY_EXISTS = "2004"

    # 知识库相关 (3xxx)
    KB_NOT_FOUND = "3001"
    KB_NAME_EXISTS = "3002"
    KB_HAS_DOCUMENTS = "3003"

    # 向量相关 (4xxx)
    EMBEDDING_FAILED = "4001"
    VECTOR_SEARCH_FAILED = "4002"

    # LLM相关 (5xxx)
    LLM_CONFIG_NOT_FOUND = "5001"
    LLM_REQUEST_FAILED = "5002"
    LLM_QUOTA_EXCEEDED = "5003"
```

### 11.3 日志规范

```python
import logging

# 日志级别使用
logger.debug("详细调试信息")
logger.info("关键业务节点")
logger.warning("潜在问题警告")
logger.error("错误信息", exc_info=True)
logger.critical("严重错误")

# 日志格式
# [时间] [级别] [模块] [函数] - 消息
# 2026-01-20 10:00:00 INFO pdf_document_manager create_document - 创建文档成功: doc_id=123
```

---

## 12. 性能优化建议

### 12.1 数据库优化

1. **索引优化**
   - 为高频查询字段添加索引
   - 复合索引: (doc_id, chunk_index), (kb_id, is_indexed)

2. **查询优化**
   - 使用分页查询避免全表扫描
   - 使用 select_related/joinedload 预加载关联数据
   - 避免 N+1 查询问题

3. **连接池配置**
   ```python
   pool_size = 20
   max_overflow = 10
   pool_timeout = 30
   pool_recycle = 3600
   ```

### 12.2 向量检索优化

1. **批量操作**
   - Embedding: 批量大小 32-64
   - Upsert: 批量大小 100-500

2. **检索参数调优**
   ```python
   search_params = {
       "hnsw_ef": 128,  # 提高召回率
       "exact": False   # 使用近似搜索
   }
   ```

3. **缓存策略**
   - 缓存热门查询的 Embedding 结果
   - 使用 Redis 缓存检索结果 (TTL: 5分钟)

### 12.3 异步任务优化

1. **使用消息队列**
   - Celery + Redis
   - 任务优先级: 高(PDF解析) > 中(切片) > 低(向量化)

2. **并发控制**
   - PDF解析: 最大并发 2 (GPU限制)
   - 切片任务: 最大并发 5
   - 向量化: 最大并发 3

---

## 13. 安全性设计

### 13.1 API 安全

1. **认证授权**
   - JWT Token 认证
   - 基于角色的访问控制 (RBAC)

2. **输入验证**
   - Pydantic Schema 自动验证
   - 文件类型白名单: ['.pdf']
   - 文件大小限制: 100MB

3. **SQL注入防护**
   - 使用 ORM 参数化查询
   - 禁止拼接 SQL 字符串

### 13.2 数据安全

1. **敏感信息加密**
   - API Key 使用 AES-256 加密存储
   - 传输使用 HTTPS

2. **MinIO 访问控制**
   - 使用预签名 URL (有效期: 1小时)
   - 私有桶 + IAM 策略

---

## 14. 部署架构

### 14.1 Docker Compose 服务

```yaml
services:
  postgres:
    image: postgres:15
    environment:
      POSTGRES_DB: pdf2train
      POSTGRES_USER: admin
      POSTGRES_PASSWORD: ${DB_PASSWORD}
    volumes:
      - postgres_data:/var/lib/postgresql/data
    ports:
      - "5432:5432"

  minio:
    image: minio/minio:latest
    command: server /data --console-address ":9001"
    environment:
      MINIO_ROOT_USER: ${MINIO_USER}
      MINIO_ROOT_PASSWORD: ${MINIO_PASSWORD}
    volumes:
      - minio_data:/data
    ports:
      - "9000:9000"
      - "9001:9001"

  qdrant:
    image: qdrant/qdrant:latest
    volumes:
      - qdrant_data:/qdrant/storage
    ports:
      - "6333:6333"

  redis:
    image: redis:7-alpine
    ports:
      - "6379:6379"

  pdf2train:
    build: .
    depends_on:
      - postgres
      - minio
      - qdrant
      - redis
    environment:
      DATABASE_URL: postgresql://admin:${DB_PASSWORD}@postgres:5432/pdf2train
      MINIO_ENDPOINT: minio:9000
      QDRANT_URL: http://qdrant:6333
      REDIS_URL: redis://redis:6379
    ports:
      - "8000:8000"
    volumes:
      - ./src:/app/src
```

### 14.2 环境变量配置

```bash
# .env
DB_PASSWORD=your_secure_password
MINIO_USER=admin
MINIO_PASSWORD=your_minio_password

# LLM API Keys
OPENAI_API_KEY=sk-xxx
DEEPSEEK_API_KEY=sk-xxx
ZHIPU_API_KEY=xxx

# Application
APP_ENV=production
LOG_LEVEL=INFO
```

---

## 15. 重构实施步骤

### 第一周: 基础设施
- [ ] 完善数据库表结构
- [ ] 配置 Docker Compose
- [ ] 搭建开发环境
- [ ] 编写基础工具类

### 第二周: 核心模块 (阶段一)
- [ ] LLM Config 模块 (已完成)
- [ ] PDF Document 模块
- [ ] Knowledge Base 模块

### 第三周: 数据处理 (阶段二)
- [ ] Document Chunk 模块
- [ ] Instruction 模块
- [ ] Storage 模块

### 第四周: 高级功能 (阶段三)
- [ ] Embedding & Retrieval 模块
- [ ] Pipeline Task 模块
- [ ] Dashboard 模块

### 第五周: 测试与优化
- [ ] 单元测试编写
- [ ] 集成测试
- [ ] 性能测试与优化
- [ ] 文档完善

---

## 附录 A: 常见问题

### Q1: 为什么 DTO 不使用 Enum?
A: DTO 直接映射数据库字段，数据库存储的是字符串。使用 Enum 会增加转换复杂度，违反 DTO 的设计初衷。

### Q2: 什么时候需要创建 DTO?
A: 当 Manager/Service 的参数超过 2 个时，建议创建 DTO。单参数可以直接传递。

### Q3: Router 可以直接调用 Service 吗?
A: 不可以。Router 只能调用 Manager，Manager 负责编排 Service。

### Q4: 如何处理跨模块调用?
A: 在 Manager 层通过依赖注入其他 Manager 或 Service，避免循环依赖。

### Q5: 删除操作如何保证数据一致性?
A: 使用数据库事务 + 外键级联删除 + 手动清理外部资源 (MinIO, Qdrant)。

---

## 附录 B: 术语表

| 术语 | 英文 | 说明 |
|------|------|------|
| 路由层 | Router Layer | 接收HTTP请求，参数验证，响应封装 |
| 管理层 | Manager Layer | 业务逻辑编排，跨服务调用 |
| 服务层 | Service Layer | 数据库CRUD操作 |
| 请求模式 | Schema | API层的请求/响应数据模型 |
| 数据传输对象 | DTO | 核心层的数据传输对象 |
| 对象关系映射 | ORM | SQLAlchemy 数据库模型 |
| 知识库 | Knowledge Base | 文档集合的逻辑分组 |
| 文档切片 | Document Chunk | 文档的语义分块单元 |
| 指令数据 | Instruction Datum | LLM生成的问答对 |
| 向量化 | Embedding | 文本转换为向量表示 |
| 流水线任务 | Pipeline Task | 文档处理的异步任务 |

---

**文档版本**: v2.0
**最后更新**: 2026-01-20
**维护者**: PDF2Train 开发团队
