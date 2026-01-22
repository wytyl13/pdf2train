# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

**PDF2Train** is a FastAPI-based system for converting PDF documents into training data for LLMs. It processes PDFs through a multi-stage pipeline: PDF parsing (via MinerU), document chunking, instruction generation, and vector embedding for RAG (Retrieval-Augmented Generation).

**Architecture**: Router-Manager-Service (RMS) pattern with strict layer separation
**Version**: v2.0 (undergoing refactoring)
**Tech Stack**: FastAPI, PostgreSQL, MinIO, Qdrant (vector DB), asyncpg

## Architecture Principles

### Three-Layer Architecture (RMS)

```
Router Layer (api/routers/)
  ↓ Converts Schema → DTO
Manager Layer (core/manager/)
  ↓ Orchestrates business logic
Service Layer (core/service/)
  ↓ Database operations only
```

**Critical Rules:**
1. **Router**: Only parameter parsing and response wrapping. NO business logic.
2. **Manager**: All business logic, cross-service orchestration, transaction control.
3. **Service**: Pure database operations. NO business decisions.
4. **Schema vs DTO**:
   - `api/schema/`: Frontend contracts (can use Enums)
   - `core/schema/`: Database DTOs (strings only, no Enums)

### Data Flow Pattern

```python
# Router receives Schema with Enums
@router.post("/create")
async def create_config(req: LLMConfigCreateReq, manager: LLMConfigManager):
    # Router converts: Schema(Enum) → DTO(String)
    dto = LLMConfigCoreDTO(
        model_type=req.model_type.value,  # Enum → Str
        provider=req.provider.value
    )
    result = await manager.create_config(dto)
    return make_response(True, "Success", result)
```

## Project Structure

```
src/pdf2train/
├── api/                          # API Layer
│   ├── routers/                  # Route handlers
│   │   ├── llm_config_router.py  # ✅ Refactored (reference)
│   │   ├── pdf_document_router.py
│   │   └── storage_router.py
│   ├── schema/                   # Request/Response schemas
│   │   ├── llm_config_schema.py  # ✅ Refactored
│   │   └── pdf_document_schema.py
│   ├── server/                   # Legacy servers (being phased out)
│   ├── table/                    # ORM models (SQLAlchemy)
│   └── dependencies.py           # Dependency injection
│
├── core/                         # Core Layer
│   ├── manager/                  # Business logic
│   │   └── llm_config_manager.py # ✅ Refactored (reference)
│   ├── service/                  # Data access
│   │   ├── llm_config_service.py
│   │   ├── pdf_document_service.py
│   │   ├── knowledge_base_service.py
│   │   ├── document_chunk_service.py
│   │   ├── instruction_datum_service.py
│   │   ├── embedding_service.py
│   │   └── minio_service.py
│   ├── schema/                   # DTOs
│   │   └── llm_config_dto.py     # ✅ Refactored
│   ├── table/                    # ORM models
│   ├── provider/                 # Database providers
│   │   └── sql_provider.py
│   └── configs/                  # Configuration
│
├── tool/                         # Utility scripts
└── utils/                        # Helper functions

configs/                          # YAML configs (outside src/)
├── postgresql_config.yaml
├── minio_config.yaml
└── deepseek_config.yaml

tests/                            # Test suite
└── core/                         # Core layer tests
```

## Development Commands

### Environment Setup

```bash
# Install dependencies
pip install -r requirements.txt

# Set PYTHONPATH
export PYTHONPATH=/path/to/pdf2train/src

# Configure environment
cp .env.template .env
# Edit .env with your settings
```

### Database Operations

```bash
# Initialize database tables
python -m pdf2train.api.table.init_tables

# Run with table initialization (production)
python -m pdf2train.api.table.init_tables -n && \
uvicorn pdf2train.api.server.main_server:app --host 0.0.0.0 --port 8000
```

### Running the Server

```bash
# Development mode (with auto-reload)
uvicorn pdf2train.api.server.main_server:app --host 0.0.0.0 --port 8000 --reload

# Using the startup script
./api_server.sh

# Docker Compose (full stack)
docker-compose up -d
```

### Testing

```bash
# Run all tests
pytest

# Run specific test file
pytest tests/core/test_llm_config.py

# Run with verbose output
pytest -v

# Run with coverage
pytest --cov=pdf2train
```

**Test Database**: Uses separate PostgreSQL instance on port 5433 (see docker-compose.yaml)

## Key Modules

### 1. LLM Config Module (✅ Reference Implementation)

Manages LLM API configurations (OpenAI, DeepSeek, etc.)

**Files:**
- Router: `api/routers/llm_config_router.py`
- Schema: `api/schema/llm_config_schema.py`
- Manager: `core/manager/llm_config_manager.py`
- Service: `core/service/llm_config_service.py`
- DTO: `core/schema/llm_config_dto.py`

**Endpoints:** All POST with JSON body
- `/api/llm_config/create` - Create config
- `/api/llm_config/update` - Update config
- `/api/llm_config/delete` - Delete config
- `/api/llm_config/list` - Paginated list
- `/api/llm_config/type_list` - Get model types
- `/api/llm_config/provider_list` - Get providers

### 2. PDF Document Module

Handles PDF upload, parsing (MinerU), metadata extraction, and content management.

**Key Operations:**
- Upload PDF → MinIO storage
- Trigger PDF2MD conversion (MinerU API)
- Manage document metadata
- Export to JSONL format

**Storage:** MinIO buckets for PDFs and parsed Markdown

### 3. Knowledge Base Module

Manages knowledge base collections and document associations.

**Features:**
- Create/update/delete knowledge bases
- Associate documents with knowledge bases
- Sync metadata to Qdrant vector DB

### 4. Document Chunk Module

Semantic chunking of parsed Markdown documents.

**Process:**
1. Read Markdown from MinIO
2. Apply chunking algorithm (respects h1-h6 hierarchy)
3. Store chunks in PostgreSQL
4. Optionally trigger embedding

### 5. Instruction Generation Module

LLM-based Q&A pair generation from document chunks.

**Strategy:**
- Physical limit: Token-based context window
- Logical completeness: Preserve heading hierarchy
- Reference tracking: Links answers to source chunks

### 6. Embedding & Retrieval Module

Vector embedding and hybrid search.

**Vector DB:** Qdrant
- Collection naming: `{embedding_model_name}` (e.g., "bge-large-zh")
- Payload includes: doc_id, kb_id, content, metadata, data_type (chunk/instruction)

**Search:** Semantic + keyword (optional) + reranking

## Database Schema

### Core Tables

**knowledge_base**: Knowledge base metadata
- `id`, `name`, `embedding_model`, `user_id`, `_settings` (JSONB)

**pdf_document**: PDF file records
- `id`, `kb_id` (FK), `file_name`, `file_hash`, `status`
- LLM configs: `instruction_gen_llm_config`, `h_title_llm_config`, `embedding_llm_config`

**document_chunk**: Document chunks
- `chunk_id` (PK), `doc_id` (FK), `kb_id`, `content`, `metadata` (JSONB), `is_indexed`

**instruction_datum**: Generated Q&A pairs
- `id`, `doc_id` (FK), `kb_id`, `instruction`, `output`, `references` (JSONB), `is_indexed`

**pipeline_task**: Async task tracking
- `id`, `doc_id` (FK), `task_type` (1:PDF2MD, 2:Chunk, 3:Instruction, 4:Embedding)
- `status` (0:pending, 1:running, 2:success, 3:failed), `result_data` (JSONB)

**llm_config**: LLM API configurations
- `id`, `name`, `model_type` (chat/embedding/rerank), `provider`, `api_key`, `is_default`

### Cascade Delete Rules

When deleting a document:
1. Delete MinIO files (PDF + Markdown)
2. Delete Qdrant vectors (filter: doc_id)
3. CASCADE delete: instruction_datum, document_chunk, pipeline_task
4. Delete pdf_document record

## Configuration Files

### configs/postgresql_config.yaml
```yaml
host: localhost
port: 5432
user: admin
password: password123
database: file_metadata
```

### configs/minio_config.yaml
```yaml
endpoint: localhost:9000
access_key: admin
secret_key: password123
```

### .env
```bash
API_PORT=9039
MINERU_API_URL=http://mineru-api:8000/file_parse
MINIO_BASE_URL=http://localhost:9000
CONDA_ENVIRONMENT=pdf2train
```

## Docker Services

```yaml
services:
  postgres:       # Port 5432 (production)
  postgres_test:  # Port 5433 (testing)
  minio:          # Ports 9000 (API), 9001 (Console)
  pdf2train:      # Port 9039, GPU-enabled
```

**Network:** `soft_default` (external bridge network)

## Refactoring Status (v2.0)

**Completed:**
- ✅ LLM Config module (reference implementation)

**In Progress:**
- 🔄 PDF Document module
- 🔄 Knowledge Base module

**Pending:**
- ⏳ Document Chunk module
- ⏳ Instruction module
- ⏳ Embedding & Retrieval module
- ⏳ Storage module

**Reference:** See `REFACTOR_DESIGN.md` for detailed refactoring plan

## Important Notes

### MinIO Dual-Service Pattern

The project uses TWO MinIO service instances to solve a networking conflict:
1. **Upload service**: Docker container-to-container communication
2. **Signing service**: Browser-to-host communication (uses `region="us-east-1"` to avoid connection)

This resolves the issue where signed URLs work in Docker but fail in browsers.

### API Style

All endpoints use **POST with JSON request body** (Pan-RPC style), even for queries:
```python
# ❌ NOT this
GET /api/llm_config/list?page=1&size=20

# ✅ Use this
POST /api/llm_config/list
Body: {"page": 1, "page_size": 20}
```

### Sensitive Data Handling

API keys are masked in responses:
```python
# Original: sk-1234567890abcdef
# Returned: sk-****cdef
```

### Async/Await Pattern

All database operations and external API calls use async/await:
```python
async def create_config(self, dto: LLMConfigCoreDTO) -> int:
    config_id = await self.service.create(dto)
    if dto.is_default:
        await self.service.reset_defaults_except(config_id)
    return config_id
```

## Common Patterns

### Creating a New Module

1. **Define Schema** (`api/schema/module_schema.py`):
   - Use Pydantic BaseModel
   - Can use Enums for type safety
   - Include all request parameters

2. **Define DTO** (`core/schema/module_dto.py`):
   - Database fields only
   - Use strings (no Enums)
   - Separate DTOs for create/update if needed

3. **Create Service** (`core/service/module_service.py`):
   - Pure CRUD operations
   - Use SqlProvider for database access
   - No business logic

4. **Create Manager** (`core/manager/module_manager.py`):
   - Business logic orchestration
   - Call multiple services if needed
   - Handle transactions

5. **Create Router** (`api/routers/module_router.py`):
   - Convert Schema → DTO
   - Call manager methods
   - Return standardized responses

6. **Add Dependency** (`api/dependencies.py`):
   - Create `get_module_manager()` function
   - Handle service initialization

### Response Format

```python
def make_response(success: bool, message: str = "", data: Any = None):
    return {
        "success": success,
        "message": message,
        "data": jsonable_encoder(data) if data else None,
        "timestamp": datetime.now().isoformat()
    }
```

## Troubleshooting

### Database Connection Issues
- Check `configs/postgresql_config.yaml`
- Verify PostgreSQL is running: `docker ps | grep postgres`
- Test connection: `psql -h localhost -U admin -d file_metadata`

### MinIO Access Issues
- Verify MinIO is running on ports 9000/9001
- Check bucket permissions
- For signed URL issues, ensure dual-service pattern is configured

### Import Errors
- Ensure `PYTHONPATH` includes `src/` directory
- Check for circular imports between modules
- Verify all `__init__.py` files exist

### Test Database Issues
- Use separate test database on port 5433
- Test DB has no persistent volumes (clean slate each restart)
- Configure via `configs/postgresql_config_test.yaml`

## Development Workflow

1. **Read existing code** before making changes (especially for refactored modules)
2. **Follow RMS architecture** strictly - no business logic in routers
3. **Use the LLM Config module** as reference for new modules
4. **Write tests** for new functionality
5. **Update REFACTOR_DESIGN.md** when completing module refactoring
6. **Never commit** sensitive data (API keys, passwords) - use templates

## External Dependencies

- **MinerU API**: PDF parsing service (separate container)
- **Qdrant**: Vector database for embeddings
- **OpenAI/DeepSeek/Zhipu**: LLM providers for instruction generation
