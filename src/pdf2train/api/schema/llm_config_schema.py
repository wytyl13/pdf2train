#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
@Time    : 2026/01/19 17:31
@Author  : weiyutao
@File    : llm_config_schema.py
router层schema定义和前端交互
router层schema可以引用core/schema中的dto，符合上层可以引用下层
但是core/schema中的dto不能引用router层的schema
manager层负责使用dto从router层接受数据，router需要将前端的schema转换为dto
manager层直接将dto传递给service层，service直接和数据库交互
manager层负责定义业务逻辑：单service或跨service操作
service层负责定义数据库交互逻辑：单表或跨表操作
dto层面不需要为业务参数定义对应的实例化对象，比如page page_size
dto层面只需要为对应的数据库存储参数创建对应的dto即可
manager和service层面如果存储参数只有一个不需要为其创建对应的dto
router schema层面需要为所有的传参创建对应的实例化对象
router schema的设计使用泛RPC风格（POST-only + JSON Request Object）
针对数据库的交互（传参和输出），传参为数据库多个字段并涉及数据库操作的，输出为整个数据结构实例化对象的
    service层直接返回实例化对象
    manager层直接返回实例化对象
    service层传参使用dto定义
    manager层传参使用dto定义
调用规则：
    table
    service
    manager
    router
    上层可以调用任何下层，同层次不可互相调用，保证不会出现循环调用的情况
    如：
    1、service可以调用任何table，但是一般service只负责当前table的数据库服务
    2、manager可以调用任何service服务
    3、router一般只可以调用自己的manager服务，除非有特殊情况
    4、manager不可以互相调用
    5、api schema仅可以被自己的router调用
    6、dto可以被service及以上任何层次调用
    7、manager、service不可以调用router层使用的schema
dto层面可以设置前端渲染的富文本数据，但是不应该设置前端传递过来的参数或者前端的业务参数
比如page page_size不应该在dto层面设置，但是前端需要额外的字段，比如专为前端渲染或者二次请求设计的
而不是为了前端的其余操作等设置的。

service层的分页查询返回字典 {"items": table_object, "total": int, "page": int, "size": int}
service层的分页查询请求 {"filter_dto": filter_dto_object, "page": int, "page_size": int}
service层支持操作所有的table，但是必须有一个重新定义：
所有的service都可以直接操作所有的table，如果serviceA定义了自己tableA的操作接口
然后如果在serviceB中需要操作serviceA对应的tableA，需要直接操作sqlprovider接口，不能调用serviceA已经定义好的接口
否则会产生相互调用，那么是否有重复定义的问题？这里有一个约定，serviceA主要负责对tableA的请求，serviceB主要负责对
tableB的操作请求，但是如果tableB的操作需要同步到tableA，则属于service的范围，注意这里不能再managerB中去作为业务定义
因为如果managerC要调用该操作双表格的方法，不能直接调用managerB，只能调用serviceB，managerB可以直接调用serviceB去对routerB
开放对应的双标操作接口。

数据校验：
    A函数输出a，直接作为B函数的输入，B函数需要区分如下情况对a参数做校验
    1、AB是同步调用，可以只assert，如果涉及到数据库校验，比如a参数是一个数据库id，需要确保id存在，同步AB函数也不需要做数据库验证
    2、AB是异步调用，并且顺序调用，可以只验证a参数有效性，不做数据库校验
    3、B是后台调用，因为不知道队列何时执行，所以必须爽判断，判断a参数的有效性，做数据库校验
router层：如果涉及到router层接口调用的有效性，必须先独立出来一个验证函数，然后再执行后续的处理任务，不可将验证和处理混合在一个函数内
抛出异常：service层不捕获任何异常，manager层捕获并处理异常（根据业务逻辑决定什么异常需要抛出到应用层），router层捕获所有异常并返回给用户
    如果manager层业务复杂，还需要捕获报错代码行并抛出，否则调试会很困难
但是如果在router层外放修改和删除接口的时候，不需要在router层验证参数的有效性，直接在manager层验证即可

如果存在父子表：先操作子表，再操作父表
如果存在聚合计算（软关联或者因果关系）：先更新因后更新果

异常抛出，主要在manager层抛出，router层捕获
manager层在最外层抛出所有异常并给出异常内容头，然后拼接每个步骤的异常内容
每一步骤都要抛出详细异常内容，如果是数据表操作异常，前缀使用[PdfDocument]  如果异常需要提示操作传参，使用-{doc_id}-

级联删除：如果是某一个router中删除某个数据或者某个批次的数据，同时要删除另一个大模块的绑定的数据，则需要再router中进行
级联删除的操作，否则应该在manager层面考虑数据库的同步
    比如：document_chunk的delete接口。该接口在删除某个chunk的时候需要同步删除：
    1、绑定该chunk的所有instruction数据
    2、删除该chunk对应的向量数据
    3、更新任务状态或者同步知识库
    以上三种级联删除或者更新，2和3需要绑定到对应的manager接口中，而1需要再router中进行操作级联删除，这样不会出现manager互相调用的问题
"""

from typing import Optional, Any
from pydantic import BaseModel, Field

from pdf2train.core.table.llm_enum import LLMProvider, ModelType

class LLMConfigCreateReq(BaseModel):
    name: str = Field(..., description="配置显示名称")
    model_type: ModelType = Field(ModelType.LLM)
    provider: LLMProvider = Field(...)
    model_name: str = Field(...)
    api_key: str = Field(...)
    base_url: Optional[str] = None
    is_default: bool = False

class LLMConfigUpdateReq(BaseModel):
    """[POST] 更新配置请求"""
    id: int = Field(..., description="配置ID")
    name: Optional[str] = None
    model_type: Optional[ModelType] = None
    provider: Optional[LLMProvider] = None
    model_name: Optional[str] = None
    api_key: Optional[str] = None
    base_url: Optional[str] = None
    is_default: Optional[bool] = None

class LLMConfigDeleteReq(BaseModel):
    """[POST] 删除配置请求 (对象封装)"""
    id: int = Field(..., description="配置ID")
    
class LLMConfigListReq(BaseModel):
    """[POST] 列表查询请求"""
    page: int = 1
    page_size: int = 20
    model_type: Optional[ModelType] = None
    
class GetLLMConfigByDocIdReq(BaseModel):
    """[POST] 根据文档ID查询配置请求"""
    doc_id: int = 1
    llm_name: str = "instruction_gen_llm_config"
    
class LLMConfigDefaultReq(BaseModel):
    """
    [POST] 获取默认配置请求
    特点：不需要分页，只需要指定类型
    """
    model_type: Optional[ModelType] = None

