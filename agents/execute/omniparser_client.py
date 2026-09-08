#!/usr/bin/env python
# coding=utf-8
"""
Omniparser Client

封装对omniparser API的调用,提供屏幕解析功能。
"""

from dotenv import load_dotenv

load_dotenv()  # reads variables from a .env file and sets them in os.environ

import hashlib
import os
from collections import OrderedDict
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

from gradio_client import Client, handle_file
from PIL import Image

from ..observe.omniparser_parser import parse_omniparser_box_data


@dataclass
class ParseResult:
    """Omniparser解析结果"""

    boxes: List[Dict[str, Any]]  # 解析出的所有boxes
    annotated_image_path: Optional[str] = None  # 标注后的图像路径
    raw_box_text: Optional[str] = None  # 原始box数据文本


class OmniparserClient:
    """
    Omniparser客户端,负责调用API并解析结果。
    """

    def __init__(
        self,
        url: str = None,
        cache_enabled: bool = True,
        cache_size: int = 10,
    ):
        """
        初始化Omniparser客户端。

        Args:
            url: Omniparser服务URL,默认从环境变量读取
            cache_enabled: 是否启用缓存
            cache_size: 缓存大小
        """
        self.url = url or os.environ.get("OMNIPARSER_URL", "http://127.0.0.1:8101/")
        self.cache_enabled = cache_enabled
        self.cache_size = cache_size

        # 初始化缓存
        if cache_enabled:
            self._cache: OrderedDict[str, ParseResult] = OrderedDict()

        print(f"🔧 OmniparserClient initialized with URL: {self.url}")

    def parse_screenshot(
        self,
        screenshot_path: str,
        box_threshold: float = 0.05,
        iou_threshold: float = 0.1,
        use_paddleocr: bool = True,
        imgsz: int = 640,
    ) -> ParseResult:
        """
        解析截图,获取所有的UI元素boxes。

        Args:
            screenshot_path: 截图文件路径
            box_threshold: Box检测阈值
            iou_threshold: IOU阈值
            use_paddleocr: 是否使用PaddleOCR
            imgsz: 图像大小

        Returns:
            ParseResult: 解析结果
        """
        # 检查缓存
        if self.cache_enabled:
            cache_key = self._get_cache_key(screenshot_path)
            if cache_key in self._cache:
                print(f"✅ Using cached parse result: {cache_key[:8]}...")
                return self._cache[cache_key]

        print(f"🔍 Parsing screenshot: {screenshot_path}")

        try:
            # 创建Gradio客户端
            client = Client(self.url)

            # 调用API
            result = client.predict(
                image_input=handle_file(screenshot_path),
                box_threshold=box_threshold,
                iou_threshold=iou_threshold,
                use_paddleocr=use_paddleocr,
                imgsz=imgsz,
                api_name="/process",
            )

            # 解析结果
            if len(result) >= 2:
                annotated_image_path = result[0]  # 标注后的图像
                box_data_text = result[1]  # Box数据文本

                # 使用现有的解析器解析box数据
                boxes = parse_omniparser_box_data(box_data_text)

                parse_result = ParseResult(
                    boxes=boxes,
                    annotated_image_path=annotated_image_path,
                    raw_box_text=box_data_text,
                )

                print(f"✅ Parse complete: found {len(boxes)} elements")

                # 缓存结果
                if self.cache_enabled:
                    self._put_cache(screenshot_path, parse_result)

                return parse_result
            else:
                print("⚠️ Unexpected Omniparser response format")
                return ParseResult(boxes=[])

        except Exception as e:
            print(f"❌ Omniparser parse failed: {e}")
            raise

    def get_boxes(self, parse_result: ParseResult) -> List[Dict[str, Any]]:
        """
        从解析结果中获取boxes列表。

        Args:
            parse_result: 解析结果

        Returns:
            boxes列表
        """
        return parse_result.boxes

    def _get_cache_key(self, screenshot_path: str) -> str:
        """
        生成缓存key(使用图像内容哈希)。

        Args:
            screenshot_path: 截图路径

        Returns:
            缓存key
        """
        try:
            with Image.open(screenshot_path) as img:
                # 使用图像字节数据生成哈希
                return hashlib.md5(img.tobytes()).hexdigest()
        except Exception as e:
            # 如果无法读取图像,使用文件路径作为key
            print(f"⚠️ Could not hash image, using file path: {e}")
            return screenshot_path

    def _put_cache(self, screenshot_path: str, result: ParseResult) -> None:
        """
        将结果放入缓存。

        Args:
            screenshot_path: 截图路径
            result: 解析结果
        """
        cache_key = self._get_cache_key(screenshot_path)
        self._cache[cache_key] = result

        # 如果缓存超过大小限制,删除最旧的
        if len(self._cache) > self.cache_size:
            self._cache.popitem(last=False)

    def clear_cache(self) -> None:
        """清除缓存。"""
        if self.cache_enabled:
            self._cache.clear()
            print("🗑️ Cache cleared")
