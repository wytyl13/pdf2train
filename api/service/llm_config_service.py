#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
@Time    : 2025/12/30 11:35
@Author  : weiyutao
@File    : llm_config_service.py
"""


import logging
from typing import Dict, Any, List, Optional
from sqlalchemy import text

from api.table.base.llm_config import LLMConfig
from api.table.base.llm_enum import LLMProvider
from agent.provider.sql_provider import SqlProvider

from api.table.base.pdf_document import PdfDocument

class LLMConfigService:
    def __init__(self, sql_config_path: str):
        self.sql_config_path = sql_config_path
        self.logger = logging.getLogger(self.__class__.__name__)

    async def get_config_by_doc_id(self, doc_id: int, field_llm_name: str):
        sql_provider = None
        try:
            sql_provider = SqlProvider(model=PdfDocument, sql_config_path=self.sql_config_path)
            result = await sql_provider.get_record_by_condition(condition={"id": doc_id})
            if result:
                item = result[0]
                llm_name = item.get(field_llm_name)
                llm_config = await self.get_llm_config_by_name(llm_name=llm_name)
                return llm_config
            else:
                return None
        except Exception as e:
            raise ValueError(str(e)) from e
    
    async def _reset_other_defaults(self, exclude_id: int = None):
        """
        [内部方法] 重置其他配置的默认状态
        将表中除 exclude_id 外的所有 is_default=True 的记录设为 False
        """
        sql_provider = None
        try:
            sql_provider = SqlProvider(model=LLMConfig, sql_config_path=self.sql_config_path)
            # 这是一个批量更新操作，通常 SqlProvider 会提供 execute_sql 或 update_by_condition
            # 这里为了通用性，使用 update_by_condition (假设 SqlProvider 支持)
            # 或者先查出旧的默认值再更新 (性能稍低但稳健)
            
            # 策略：先查找当前是 True 的，全部置为 False
            condition = {"is_default": True}
            old_defaults = await sql_provider.get_record_by_condition(condition)
            
            for record in old_defaults:
                # 兼容 dict 和 object
                r_id = record.get("id") if isinstance(record, dict) else getattr(record, "id")
                # 如果是当前正在编辑的 ID，跳过（由主逻辑处理）
                if exclude_id is not None and int(r_id) == int(exclude_id):
                    continue
                
                await sql_provider.update_record(r_id, {"is_default": False})
                
        except Exception as e:
            self.logger.error(f"重置默认配置失败: {e}")
            raise e
        finally:
            if sql_provider: await sql_provider.close()

    async def create_llm_config(self, data: Dict[str, Any]) -> int:
        """创建 LLM 配置"""
        sql_provider = None
        try:
            # 1. 如果新创建的被设为默认，先重置其他
            if data.get("is_default", False):
                await self._reset_other_defaults(exclude_id=None)

            sql_provider = SqlProvider(model=LLMConfig, sql_config_path=self.sql_config_path)
            
            # 确保 provider 在枚举范围内 (可选校验)
            # if data.get("provider") not in [p.value for p in LLMProvider]:
            #     pass 

            res_id = await sql_provider.add_record(data)
            return res_id
        except Exception as e:
            self.logger.error(f"创建 LLM 配置异常: {str(e)}")
            raise e
        finally:
            if sql_provider: await sql_provider.close()

    async def update_llm_config(self, config_id: int, data: Dict[str, Any]) -> bool:
        """更新 LLM 配置"""
        sql_provider = None
        try:
            # 1. 如果更新为默认，先重置其他
            if data.get("is_default") is True:
                await self._reset_other_defaults(exclude_id=config_id)

            sql_provider = SqlProvider(model=LLMConfig, sql_config_path=self.sql_config_path)
            
            # 2. 执行更新
            result = await sql_provider.update_record(config_id, data)
            return result
        except Exception as e:
            self.logger.error(f"更新 LLM 配置异常: {str(e)}")
            raise e
        finally:
            if sql_provider: await sql_provider.close()

    async def delete_llm_config(self, config_id: int) -> bool:
        """删除 LLM 配置"""
        sql_provider = None
        try:
            sql_provider = SqlProvider(model=LLMConfig, sql_config_path=self.sql_config_path)
            # 物理删除
            result = await sql_provider.delete_record(config_id, hard_delete=True)
            return result
        except Exception as e:
            self.logger.error(f"删除 LLM 配置异常: {str(e)}")
            raise e
        finally:
            if sql_provider: await sql_provider.close()

    async def get_config_list(self, page: int = 1, page_size: int = 20) -> Dict[str, Any]:
        """获取配置列表 (含脱敏)"""
        sql_provider = None
        try:
            sql_provider = SqlProvider(model=LLMConfig, sql_config_path=self.sql_config_path)
            
            # 按 is_default 降序 (默认的排前面), 然后 id 降序
            # 注意：具体排序语法取决于 SqlProvider 实现，这里假设支持 order_by
            result = await sql_provider.get_records_paginated(
                page=page, 
                page_size=page_size, 
                order_by=LLMConfig.is_default.desc()
            )
            
            # === 敏感信息脱敏 ===
            items = result.get("items", []) if isinstance(result, dict) else result
            for item in items:
                # 兼容 SQLAlchemy 对象转 dict
                if not isinstance(item, dict):
                    item = item.__dict__
                
                api_key = item.get("api_key", "")
                if api_key and len(api_key) > 8:
                    item["api_key"] = f"{api_key[:3]}****{api_key[-4:]}"
                elif api_key:
                    item["api_key"] = "******"
            
            return result
        except Exception as e:
            self.logger.error(f"查询 LLM 配置列表异常: {str(e)}")
            raise e
        finally:
            if sql_provider: await sql_provider.close()

    async def get_active_config(self) -> Optional[Dict[str, Any]]:
        """
        [系统内部调用] 获取当前激活的默认配置
        用于 InstructionGenService 等服务初始化 OpenAI Client
        返回包含明文 API Key 的完整字典
        """
        sql_provider = None
        try:
            sql_provider = SqlProvider(model=LLMConfig, sql_config_path=self.sql_config_path)
            records = await sql_provider.get_record_by_condition({"is_default": True})
            if records:
                # 返回第一个默认配置
                record = records[0]
                return record if isinstance(record, dict) else record.__dict__
            return None
        except Exception as e:
            self.logger.error(f"获取激活配置失败: {e}")
            return None
        finally:
            if sql_provider: await sql_provider.close()
            
    async def get_active_config_name(self) -> str:
        sql_provider = None
        try:
            sql_provider = SqlProvider(model=LLMConfig, sql_config_path=self.sql_config_path)
            records = await sql_provider.get_record_by_condition({"is_default": True})
            if records:
                # 返回第一个默认配置
                record = records[0]
                return record["name"] if isinstance(record, dict) else record.__dict__
            return None
        except Exception as e:
            self.logger.error(f"获取激活名称: {e}")
            return None
        finally:
            if sql_provider: await sql_provider.close()
            
    async def get_llm_config_by_name(self, llm_name) -> str:
        sql_provider = None
        try:
            sql_provider = SqlProvider(model=LLMConfig, sql_config_path=self.sql_config_path)
            records = await sql_provider.get_record_by_condition({"name": llm_name})
            if records:
                # 返回第一个默认配置
                record = records[0]
                return record if isinstance(record, dict) else record.__dict__
            return None
        except Exception as e:
            self.logger.error(f"获取激活名称: {e}")
            return None
        finally:
            if sql_provider: await sql_provider.close()