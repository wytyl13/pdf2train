#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
@Time    : 2026/01/19 15:00
@Author  : weiyutao
@File    : pdf_utils.py
"""

import io
import logging
from typing import Dict, Optional, Any
import fitz  # PyMuPDF
from pypdf import PdfReader

# 配置日志
logger = logging.getLogger(__name__)

class PdfUtils:
    """
    PDF 处理工具类 (纯 CPU 计算)
    负责：元数据提取、封面生成、文本清洗
    不负责：IO 操作、数据库、MinIO 上传
    """

    @staticmethod
    def _clean_str(text: Any) -> Optional[str]:
        """
        [内部辅助] 清洗字符串
        去除 \x00 空字节（Postgres不支持），去除首尾空格
        """
        if not text:
            return None
        # 强转 str 并清洗
        return str(text).replace('\x00', '').strip()

    @staticmethod
    def extract_metadata(file_data: bytes) -> Dict[str, str]:
        """
        提取 PDF 元数据
        返回字典的 Key 遵循 MinIO Header 规范 (kebab-case)
        """
        metadata = {}
        
        try:
            # 使用 pypdf 读取内存中的 bytes
            with io.BytesIO(file_data) as stream:
                reader = PdfReader(stream)
                
                # 1. 提取页数
                num_pages = len(reader.pages)
                metadata["pages"] = str(num_pages)

                # 2. 提取标准信息 (Info Dict)
                info = reader.metadata
                if info:
                    # 作者
                    if info.author:
                        cleaned = PdfUtils._clean_str(info.author)
                        if cleaned:
                            metadata["author"] = cleaned
                    
                    # 标题 (MinIO 推荐使用 original-title)
                    if info.title:
                        cleaned = PdfUtils._clean_str(info.title)
                        if cleaned:
                            metadata["original-title"] = cleaned

                    # 创建者/工具
                    if info.creator:
                        cleaned = PdfUtils._clean_str(info.creator)
                        if cleaned:
                            metadata["creator"] = cleaned

            logger.info(f"PDF 元数据提取成功: {metadata}")
            return metadata

        except Exception as e:
            logger.warning(f"PDF 元数据提取失败: {str(e)}")
            return {}

    @staticmethod
    def generate_cover(file_data: bytes) -> Optional[bytes]:
        """
        提取 PDF 第一页为 JPEG 图片字节流
        使用 PyMuPDF (fitz)
        """
        try:
            # fitz 打开内存流
            with fitz.open(stream=file_data, filetype="pdf") as doc:
                if doc.page_count < 1:
                    logger.warning("PDF 为空，无法生成封面")
                    return None
                
                # 获取第一页
                page = doc[0]
                
                # 渲染为像素图 
                # matrix=fitz.Matrix(1, 1) 代表原比例
                # 如果想要更高清封面，可以改为 fitz.Matrix(2, 2)
                pix = page.get_pixmap(matrix=fitz.Matrix(1, 1))
                
                # 转为 JPEG 字节
                img_bytes = pix.tobytes("jpeg")
                
                return img_bytes

        except Exception as e:
            logger.error(f"封面提取失败: {e}")
            return None