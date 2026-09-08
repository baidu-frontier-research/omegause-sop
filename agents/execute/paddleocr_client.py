#!/usr/bin/env python
# coding=utf-8
"""
PaddleOCR 远程客户端

调用远程 PaddleOCR API 服务进行 OCR 识别。
"""

import base64
import json
import os
from typing import Any, Dict, List, Optional, Tuple

import numpy as np
import requests
from dotenv import load_dotenv
from PIL import Image

load_dotenv()


class PaddleOCRClient:
    """
    PaddleOCR 远程客户端，调用远程 API 服务。
    """

    def __init__(self, api_url: Optional[str] = None):
        """
        初始化 PaddleOCR 远程客户端。

        Args:
            api_url: PaddleOCR API 服务地址，默认从环境变量 PADDLEX_URL 读取
        """
        self.api_url = api_url or os.environ.get("PADDLEX_URL", "http://127.0.0.1:8101/ocr")
        # 精简输出

    def recognize_text(self, image_path: str, bbox: Optional[List[float]] = None) -> Tuple[str, float]:
        """
        识别图像中的文本（使用远程 PaddleOCR API）。

        Args:
            image_path: 图像文件路径
            bbox: 可选的归一化坐标 [x1, y1, x2, y2]，如果提供则只识别该区域的文本

        Returns:
            (识别的文本, 置信度)
        """
        try:
            # 加载图像并转换为base64
            with open(image_path, "rb") as file:
                file_bytes = file.read()
                file_data = base64.b64encode(file_bytes).decode("ascii")

            # 如果提供了 bbox，裁剪指定区域并重新编码
            if bbox and len(bbox) == 4:
                with Image.open(image_path) as img:
                    width, height = img.size
                    x1 = int(bbox[0] * width)
                    y1 = int(bbox[1] * height)
                    x2 = int(bbox[2] * width)
                    y2 = int(bbox[3] * height)

                    # 确保坐标在有效范围内
                    x1 = max(0, min(x1, width - 1))
                    y1 = max(0, min(y1, height - 1))
                    x2 = max(0, min(x2, width))
                    y2 = max(0, min(y2, height))

                    if x2 > x1 and y2 > y1:
                        img = img.crop((x1, y1, x2, y2))

                        # 将裁剪后的图像转换为base64
                        import io

                        buffer = io.BytesIO()
                        img.save(buffer, format="PNG")
                        file_data = base64.b64encode(buffer.getvalue()).decode("ascii")

            # 准备请求数据（根据用户提供的示例）
            payload = {"file": file_data, "fileType": 1}

            # 使用用户提供的 `/ocr` 端点
            endpoint = self.api_url.rstrip("/")
            # 删精简API端点输出

            # 发送JSON格式请求
            response = requests.post(endpoint, json=payload, timeout=30)

            if response.status_code == 200:
                result = response.json()
                # 精简API返回数据输出

                # 根据用户示例解析返回结果
                if result and isinstance(result, dict):
                    texts = []
                    confidences = []

                    # 尝试多种返回格式
                    if "result" in result and "ocrResults" in result["result"]:
                        ocr_results = result["result"]["ocrResults"]
                    elif "ocrResults" in result:
                        ocr_results = result["ocrResults"]
                    else:
                        ocr_results = result.get("data", [])

                    # 如果没有找到标准结构，尝试直接搜索文本字段
                    if not ocr_results:
                        # 深度搜索可能包含文本的字段
                        def find_text_in_dict(d, path=""):
                            found = []
                            if isinstance(d, dict):
                                if "text" in d and isinstance(d["text"], str) and d["text"].strip():
                                    found.append((path + ".text", d["text"]))
                                if "content" in d and isinstance(d["content"], str) and d["content"].strip():
                                    found.append((path + ".content", d["content"]))
                                if (
                                    "prunedResult" in d
                                    and isinstance(d["prunedResult"], str)
                                    and d["prunedResult"].strip()
                                ):
                                    found.append((path + ".prunedResult", d["prunedResult"]))
                                for key, value in d.items():
                                    found.extend(find_text_in_dict(value, path + f".{key}"))
                            elif isinstance(d, list):
                                for i, item in enumerate(d):
                                    found.extend(find_text_in_dict(item, path + f"[{i}]"))
                            return found

                        text_fields = find_text_in_dict(result)
                        if text_fields:
                            print(f"  🔍 Found text fields: {text_fields}")
                            for field_path, text_content in text_fields:
                                if text_content.strip() and len(text_content) > 1:  # 排除过短的文本
                                    texts.append(text_content)
                                    confidences.append(0.9)  # 默认置信度

                    # 遍历OCR结果
                    for res in ocr_results:
                        if isinstance(res, dict):
                            pruned_result = res.get("prunedResult", {})

                            # 从prunedResult中提取实际的OCR结果
                            if isinstance(pruned_result, dict):
                                # 提取识别文本
                                rec_texts = pruned_result.get("rec_texts", [])
                                rec_scores = pruned_result.get("rec_scores", [])

                                if rec_texts and rec_scores:
                                    # 合并所有识别文本
                                    texts.extend([str(t) for t in rec_texts])
                                    confidences.extend([float(s) for s in rec_scores])
                                else:
                                    # 尝试其他可能的文本字段
                                    possible_text_fields = [
                                        pruned_result.get("text", ""),
                                        pruned_result.get("content", ""),
                                        pruned_result.get("prunedText", ""),
                                        pruned_result.get("recognizedText", ""),
                                        pruned_result.get("ocrText", ""),
                                    ]

                                    for text_field in possible_text_fields:
                                        if isinstance(text_field, str) and text_field.strip() and len(text_field) > 1:
                                            texts.append(text_field)
                                            confidence = pruned_result.get("confidence", 0.9)
                                            confidences.append(float(confidence))
                                            break
                            else:
                                # 如果prunedResult不是字典，尝试直接解析
                                possible_text_fields = [
                                    res.get("text", ""),
                                    res.get("content", ""),
                                    res.get("prunedText", ""),
                                    res.get("recognizedText", ""),
                                    res.get("ocrText", ""),
                                    str(res.get("prunedResult", "")),
                                ]

                                for text_field in possible_text_fields:
                                    if isinstance(text_field, str) and text_field.strip() and len(text_field) > 1:
                                        texts.append(text_field)
                                        confidence = res.get("confidence", 0.9)
                                        confidences.append(float(confidence))
                                        break

                    recognized_text = " ".join(texts) if texts else ""
                    avg_confidence = float(np.mean(confidences)) if confidences else 0.0

                    if recognized_text:
                        pass  # 精简OCR识别成功输出
                    else:
                        pass  # 精简未识别到文本输出

                    return recognized_text, avg_confidence
                else:
                    print(f"  ⚠️ Unexpected API response format: {type(result)}")
            else:
                print(f"  ❌ API call failed: {response.status_code}")
                return "", 0.0

            return "", 0.0

        except requests.exceptions.RequestException as e:
            print(f"  ❌ Network connection error")
            return "", 0.0
        except Exception as e:
            print(f"  ❌ OCR recognition error")
            return "", 0.0

    def batch_recognize(self, image_path: str, boxes: List[Dict[str, Any]]) -> List[Tuple[str, float]]:
        """
        批量识别多个box区域的文本（使用本地 PaddleOCR）。

        Args:
            image_path: 图像文件路径
            boxes: box列表，每个box包含 bbox 和 type 等信息

        Returns:
            每个box的识别结果列表 [(文本, 置信度), ...]
        """
        results = []

        for i, box in enumerate(boxes):
            bbox = box.get("bbox")
            if bbox:
                text, confidence = self.recognize_text(image_path, bbox)
                results.append((text, confidence))
                # 精简box识别输出
            else:
                results.append(("", 0.0))

        return results
