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

