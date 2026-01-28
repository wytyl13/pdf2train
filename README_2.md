# pdf2train 开发日志

1、knowledge base菜单中最右侧的元数据信息显示不全，而且该菜单不具备编辑和保存功能
2、chunk json数据还需要进一步清洗
3、data import页面的二级检索还有问题
4、删除document不完善：没有同步删除所有步骤的产物
5、当前步骤完成以后（仅更新了全局文件状态）没有更新下一个步骤的状态


pdf2train使用docker公网连接 minio容器
但是生成的签名是公网前缀的签名，强制更改前缀为localhost验证失败
但是使用公网前缀又在浏览器无法访问

如何优雅的解决这个问题
# 20251226
1、优化工程结构（宿主机开发源代码，docker容器运行pdf2train项目）
2、docker微服务（minio、mineru-api、postgres、pdf2train）桥接模式通信
3、解决minio上传服务和签名服务冲突的问题。
    上传服务为docker容器间通信，签名服务为浏览器和宿主机通信。
    （解决办法：创建两个minio服务，一个专门负责上传文件，一个专门负责签名，注意：签名服务不需要连接服务器，使用了region="us-east-1"参数不连接，只有私有桶才会出现该问题）

# 20251227
1、修改删除、编辑chunk data接口（修改语义块和meta data）
2、优化删除chunk接口，新增删除后同步更新chunk步骤的result_data
3、instruction gen
    问题+参考资料=回答
    问题1：参考资料如何定义？是大模型组织语言输出呢还是索引到原始chunk_id。两种办法都测试了，索引到原始chunk最优
    问题2：大模型可见的chunk的单位是什么？chunk的元数据包含h1、h2、h3、h4、h5、h6，使用哪个单位呢？原始是使用h1作为单位的，但是现在有新的策略：物理限制+逻辑完整性
    问题3：如何保证物理限制和逻辑完整性？定义物理token上限，从h1开始向下进行遍历，以符合条件的最大单位作为大模型可见的chunk单位。物理上限如何定义？
        物理上限不是由人的意愿而定，而是由模型和显存的物理极限定义，因为使用api接口，因此物理上限仅取决于调用api模型的上下文上限和期望实现结果之间的平衡。
4、

# 20251228
1、删除documents chunk更新数据库，单个chunk删除到最后一个直至chunk count = 0 的时候更新任务和数据表状态，单个doc_id删除更新任务和pdf document数据表状态
2、过滤 h1 contexts 字符数太少的问题
3、设计并创建instruction data数据表和对应的任务类，设计并创建生成进度日志表，以断点继续生成（1229完成）
4、删除所有chunk数据以后会更新result data为空，但是现在的删除文件的时候会从result data中找对应的原始json（chunk步骤生成的原始json），因为result data已经为空了，因此漏删除了原始json...已解决，删除完chunk data以后仅更新result_data中的chunk_count字段为0


# 20251229
1、创建instruction gen步骤的指令数据存储表及服务
2、数据表中缺少instruction字段，后续优化
3、指令数据前端的修改删除并没有持久化存储到数据中，后续优化


# 20251230
1、清空指令数据更新任务状态，使得可以重新生成
2、更新instruction data优化，核心难点为更新references字段数据
3、新增llm_config配置table service  server
4、instruction ui中配置新增编辑删除llm_config，每个doc_id选择llm配置（标题生成和指令生成配置），import data卡片的右上角也可以进行选择配置
5、新增了llm_config配置，各种ui对接
6、在执行操作的时候，如果用到了某个llm配置，不能修改改配置，否则会产生冲突

# 20251231
1、instruction 步骤生成的指令数据有待优化，llm请求报错和并发问题，效率太低
2、新增状态过滤机制
3、新增dashboard ui
4、data import界面提pdf2md步骤并发情况下状态更新不及时，需要等待所有提取完成以后一起更新，需要优化

# 20260103
1、新增chunk_index_description字段，以方便前端显示和documents chunk相对应的chunk编号在instruction gen页面的instruction chunk显示菜单栏
2、优化instruction生成算法：减少每章生成无法回答的数目（从2减少到1），使用difflib去匹配无法回答的低成本验证（二次低成本验证数据质量，防止rag数据出现幻觉）
3、其他优化

# 20260109
1、llm_config表格中新增model_type字段，用于兼容gpt、embedding、rerank等llm模型

2、新增多知识库相关的表格定义，以管理维度多个知识库（下午来了开始）
    尚未开始
    使用关联表还是外键：
        文件池没有知识库的概念，所有的文件在这里进行处理：pdf解析、切分、指令生成、语义向量
        可以指定某个文件属于某个知识库，也可以在知识库层面拉取某个文件
        如果某个文件只属于某个知识库，使用外键最简单直接。
        如果某个文件可以属于多个知识库，使用关联表最直接。
3、wangeng中检索增强生成相关代码更新，以适配多知识库结构（下午来了开始）
    已优化embedding相关的配置
    待优化rerank相关的配置
4、wangeng agent框架相关更新（预计明天开始1.10）
    尚未开始

# 20260111
1、完成知识库的管理操作
2、完成知识库新增、删除文件（同步数据库pdf_documents和qdrant元数据）
3、完成如果在语义嵌入之前某个文件已经分配到了知识库，则嵌入向量直接入库
4、完成知识库删除同步更新数据库kb_id字段和qdrant元数据
5、未完成：
    同步指令数据（目前只做了原始数据块，还没有对指令数据做同步）（下午做）(已完成)
        指令数据的元数据如何定义
        前端chunks只显示已嵌入状态，没有显示已嵌入则是未嵌入，instruction gen菜单栏的条目是否也这样显示？


    删除文件、删除chunk、删除instruction datum都没有做向量数据库的同步（下午做）
        如何定义删除条件？
            用户可以指定删除某个chunk（包含原始和指令数据），可以使用现有的update接口去条件删除（chunk_id）
            但是用户也可以指定删除某个原始文件的所有chunk或者某个instruction所有chunk，或者直接删除原始文件，那么如何进行条件删除和集体删除呢？肯定
            不可能使用单个chunk_id删除的接口去实现这个功能，所以需要重新定义一个新的删除接口
            现在的集体删除可以使用doc_id这一个条件，但是只删除某个文件的原始切分或者指令数据，暂时没有找到好的过滤条件

    其他菜单栏目没有同步新增知识库过滤条件（只同步了data import）
    rag测试页面需要进行知识库的选择（下午做）
    chat智能体创建（晚点做）


    如果某个中间步骤的结果被删除，那么这个步骤的状态会被重置，然后如果他再次运行逻辑会激活下一个步骤的状态，因此如果下一个状态为已完成，则会被重置，这个问题需要解决，也就是在激活下一个状态的时候要做判断，不要更改下一个状态已经是成功的状态（未完成）但是刚才测试好像没有这个问题哦。但是如果不重置状态，这个是有问题的
    因为如果重新生成了数据，那么就要重新做嵌入啊


    现在删除chunk和instruction单个和全部或者删除doc_id关于嵌入数据的同步都没有问题了，就是在删除数据以后
    有歧义，比如我只删除了instruction，那么这个doc属于已嵌入还是没有嵌入？、如果全部删粗原始数据和指令数据
    这个doc_id属于已嵌入还是为嵌入？原始数据都没有了，嵌入状态肯定要做更新，现在是不论如何删除都不会影响这个doc_id的嵌入状态
    因为这个嵌入状态单纯的按照任务执行结果查看的（因为以上删除操作均不会影响嵌入步骤的任务结果），注意这个只会在知识库卡片上显示。
    但是删除步骤产物操作本身会重置当前任务的状态，因此需要考虑如何重置知识库索引这个步骤的状态，（何时重置合适？）
    也就是说现在在操作完删除整个chunk或者整个instruction之后，语义嵌入步骤的状态不会更新，这个是个问题，因为如果删除了，他的状态首先不能够是完成，还有就是如果删除了重新建了，还需要进行操作嵌入，完成状态下无法操作嵌入

    只做了chunk和insturction删除去同步语义嵌入数据库的操作，还没有做更新的操作（还需要另行处理）

# 20260116
1、增强检索
    要做混合检索，需要使用bge-m3模型做密集向量嵌入和稀疏向量嵌入，使用qdrant去完成嵌入向量存储和混合检索
    使用api无法获取稀疏向量嵌入

async def update_document(self, doc_id: int, data: Dict[str, Any], confirm_sync: bool = False) -> bool:
        """
        更新文档信息
        """
        sql_provider = None
        try:
            # 补充更新时间（如果表里有 update_time 字段的话）
            data["update_time"] = datetime.now()
            sql_provider = SqlProvider(model=PdfDocument)
            
            # 1. 获取当前文档信息
            current_docs = await sql_provider.get_record_by_condition(condition={"id": doc_id})
            if not current_docs:
                return False
            current_doc = current_docs[0]
            
            # 2. 如果修改了 kb_id 且文档状态是 SUCCESS，可能需要异步触发 Qdrant Payload 的更新
            if "kb_id" in data:
                update_kb_id = data.get("kb_id")
                current_kb_id = current_doc.get("kb_id") if current_doc else None
                collection_name = current_doc[0].get("embedding_llm_config") if current_doc else None
                if update_kb_id != current_kb_id:
                    # 触发异步任务更新 Qdrant Payload
                    metadata_update_request = MetadataUpdateRequest(
                        collection_name=collection_name,
                        filter_key="doc_id",
                        filter_value=doc_id,
                        payload={"kb_id": update_kb_id}
                    )
                    await self.update_doc_to_kb_service.update_docs_to_kb(
                        metadata_update_request=metadata_update_request,
                        update_sql=False
                    )
                    
            # 执行更新
            result = await sql_provider.update_record(record_id=doc_id, data=data)
            return result
        except Exception as e:
            import traceback
            self.logger.error(f"Service更新异常: {str(e)} \n {traceback.format_exc()}")
            raise e
        finally:
            if sql_provider:
                await sql_provider.close()

# 20260121
1、重构llm_config模块
2、claude code设计初版的重构项目结构（仿照llm_config模块设计其余的重构思路）
3、按照claude code的重构思路编写knowledge base模块（构建了knowledge base和qdrant模块，缺少embedding模块）
4、manager和service统一返回实例化对象，返回结果为list的使用items键赋值，router层解码实例化对象为dict


1.  GET  /api/dashboard/recent-jobs
2.  GET  /api/dashboard/stats
3.  POST /api/document_chunk/delete
4.  POST /api/document_chunk/delete_by_id
5.  GET  /api/document_chunk/download/{doc_id}
6.  POST /api/document_chunk/download/stream-pretrain-by-kb
7.  POST /api/document_chunk/list
8.  POST /api/document_chunk/update
9.  POST /api/embedding/run
10. POST /api/instruction/clear_by_doc
11. POST /api/instruction/delete
12. GET  /api/instruction/download_jsonl/{doc_id}
13. GET  /api/instruction/download_jsonl_all
14. POST /api/instruction/download_jsonl_by_kb
15. POST /api/instruction/list
16. POST /api/instruction/run
17. POST /api/instruction/update
18. POST /api/knowledge_base/create
19. POST /api/knowledge_base/delete
20. POST /api/knowledge_base/list
21. POST /api/knowledge_base/update
22. POST /api/knowledge_base/update_docs
23. POST /api/llm_config/create
24. POST /api/llm_config/delete
25. POST /api/llm_config/list
26. POST /api/llm_config/provider_list
27. POST /api/llm_config/type_list
28. POST /api/llm_config/update
29. GET  /api/pdf_document/chunk_count
30. GET  /api/pdf_document/content
31. POST /api/pdf_document/content/save
32. POST /api/pdf_document/delete
33. POST /api/pdf_document/export_books_jsonl
34. POST /api/pdf_document/get_doc_count_by_kb_id
35. POST /api/pdf_document/list
36. GET  /api/pdf_document/statistics
37. POST /api/pdf_document/unassigned
38. POST /api/pdf_document/update
39. POST /api/pdf2md/convert
40. GET  /api/pipeline/tasks
41. POST /api/chunk/run
42. POST /api/storage/upload
43. POST /api/storage/url
44. POST /api/vector/search
45. POST /api/vector/update


# 项目重构前后端对接
## 基础重构
1、data import 页面过滤筛选查看那doc list
2、查看某个doc的原始文件
3、下载某个doc的原始文件
4、查看某个doc的md文件
5、编辑某个doc的md文件
6、正常显示某个doc的所有数据
7、更新某个doc配置信息（后台还需要完善更新模型配置、知识库配置以后对后续步骤的影响）


## 核心操作步骤重构
### pdf解析操作
1、完成pdf解析操作的接口，并重构为单一功能职责架构
1、pdf解析操作，但是没有处理pdf2train函数中同步更新父文档的代码，需要同步更新父文档的progress
2、每次执行pdf解析操作的时候需要重置doc_id层面的状态，但是没有设计一套确定的重置规则
### chunk操作，基本完成


# 20260124
1、完成pdf_document_manager 基本的删除  新增操作
2、完成instruction_datum_manager基本的操作，但是没有优化批量删除、修改、单个删除对应的后续向量数据库同步操作
需要设计这个后续同步操作放在哪里合适？
3、完成document_chunk_manager基本的操作，同样需要考虑后续的向量数据库同步方法
4、完成chunk_manager操作
5、优化pipeline_task_manager
6、需要优化向量数据库的操作（删除，新增删除接口）
7、优化检索操作
8、需要完成instruction_gen_manager模块操作
9、需要完成embedding_manager模块操作
10、需要完成步骤progress更新和对应的doc progress更新
11、instruction ge模块阻塞主线程，需要优化

# 20260127
1、完成所有模块（除了嵌入向量相关）
2、待完成嵌入向量相关的模块，分别为嵌入、检索、嵌入操作数据库更新等
3、待完成嵌入向量相关模块之后在document  document chunk   instruction knowledge_base模块中对接嵌入向量模块STR

# 20260128
1、完成document chunk router 级联删除操作
    在router中区分模块进行级联删除：删除指定chunk，级联删除参考该chunk的 instruction datum 数据
    这里的级联删除不包含删除某个chunk对应的删除该chunk对应的向量数据库，也不包含删除instruction datum 
    对应要删除的向量数据库，这些向量数据库和任务状态更新和对应的chunk  instruction模块绑定
    如果在router层面同时要考虑这些，就很复杂了，所以在各自的大模块之内考虑这些细节

2、完成嵌入操作
    待优化知识库添加文件删除文件接口
    现在是不传递vector_store_collection_name参数，如果不传递，在现有的接口下无法再删除文件的时候知道去哪个collection_name下删除，vector_store_collection_name在创建知识库的时候默认为embedding_name，一经创建不可修改
    后续如果要使用bge-m3模型则需要手动修改以前初始化的知识库（因为他们的vector_store_collection_name字段是阿里的模型）
    待完善向量数据库删除接口并同步到其他模块

