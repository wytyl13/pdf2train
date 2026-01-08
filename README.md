pdf2train 开发日志

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