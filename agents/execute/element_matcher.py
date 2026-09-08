#!/usr/bin/env python
# coding=utf-8
"""
Element Matcher - OCR优先策略

结合omniparser解析的boxes、PaddleOCR和图像相似度来找到最佳匹配元素。
支持跨分辨率匹配，对图像大小差异鲁棒。
"""

import os
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

from PIL import Image
import numpy as np
from difflib import SequenceMatcher

# 导入 PaddleOCR 客户端
from .paddleocr_client import PaddleOCRClient


@dataclass
class ElementFeatures:
    """元素特征"""

    # 录制时的clicked_box图像
    clicked_box_image: Optional[np.ndarray] = None  # OpenCV格式 (BGR)
    clicked_box_path: Optional[str] = None

    # 文本内容
    content: str = ""

    # 元素类型（可选，用于过滤）
    element_type: str = ""

    # 原始坐标（参考）
    original_x: int = 0
    original_y: int = 0


@dataclass
class MatchResult:
    """匹配结果"""

    matched_box: Optional[Dict[str, Any]] = None  # 匹配的box数据
    confidence: float = 0.0  # 综合置信度
    image_similarity: float = 0.0  # 图像相似度
    text_similarity: float = 0.0  # 文本相似度
    matching_strategy: str = "hybrid"
    candidates: List[Tuple[Dict, float, float, float]] = field(
        default_factory=list
    )  # (box, 总分, 图像分, 文本分)


class ElementMatcher:
    """
    元素匹配器 - 混合匹配策略

    结合omniparser boxes + 图像相似度 + 文本匹配
    """

    def __init__(
        self,
        min_confidence: float = 0.1,
        similarity_method: str = "template_matching",  # "histogram", "ssim", or "template_matching"
    ):
        """
        初始化元素匹配器。

        Args:
            min_confidence: 最低匹配置信度阈值 (0-1)
            similarity_method: 图像相似度算法
                - "histogram": HSV直方图比较(快速但不够精确)
                - "ssim": 结构相似性比较(精确，需要scikit-image)
                - "template_matching": OpenCV模板匹配(推荐，快速且精确)
        """
        self.min_confidence = min_confidence
        self.similarity_method = similarity_method

        # 匹配权重配置
        # OCR优先策略：文本匹配为主，图像匹配为辅
        self.weights = {
            "image": 0.3,  # 图像相似度权重（OCR有歧义时启用）
            "text": 0.7,  # 文本相似度权重（OCR优先）
        }

        # 初始化 PaddleOCR 客户端
        self.paddleocr_client = PaddleOCRClient()

        # 导入cv2用于图像处理
        try:
            import cv2

            self.cv2 = cv2

            # 尝试导入sklearn的SSIM
            try:
                from skimage.metrics import structural_similarity

                self.ssim = structural_similarity
                self.has_ssim = True
            except ImportError:
                self.has_ssim = False
                if similarity_method == "ssim":
                    print("⚠️ scikit-image not installed; falling back to template_matching")
                    self.similarity_method = "template_matching"

            print(f"🎯 ElementMatcher initialized (hybrid matching mode)")
            print(
                f"   Image weight: {self.weights['image']:.1f}, text weight: {self.weights['text']:.1f}"
            )
            print(f"   Image algorithm: {self.similarity_method}")
            print(f"   Min confidence: {min_confidence}")
        except ImportError:
            print("❌ opencv-python is required")
            print("   Run: uv pip install opencv-python")
            raise

    def extract_features(self, event: Dict[str, Any]) -> ElementFeatures:
        """
        从录制事件中提取元素特征 - 使用PaddleOCR实时提取文本。

        Args:
            event: 录制的事件数据

        Returns:
            ElementFeatures: 提取的特征
        """
        features = ElementFeatures()

        # 提取原始坐标
        features.original_x = event.get("x", 0)
        features.original_y = event.get("y", 0)

        # 使用PaddleOCR实时提取文本内容
        clicked_box_path = event.get("clicked_box_path", "")
        ocr_successful = False

        if clicked_box_path and os.path.exists(clicked_box_path):
            try:
                # 加载点击区域图像
                pil_image = Image.open(clicked_box_path)
                template_rgb = np.array(pil_image.convert("RGB"))
                features.clicked_box_image = self.cv2.cvtColor(
                    template_rgb, self.cv2.COLOR_RGB2BGR
                )
                features.clicked_box_path = clicked_box_path

                # 使用PaddleOCR实时识别文本
                try:
                    recognized_text, confidence = self.paddleocr_client.recognize_text(
                        clicked_box_path
                    )
                    if recognized_text and confidence > 0:
                        features.content = recognized_text
                        ocr_successful = True
                        print(
                            f"    ✅ Live OCR extracted: '{recognized_text[:30]}' (confidence: {confidence:.3f})"
                        )
                    else:
                        print("    ⚠️  Live OCR returned no valid result")
                except Exception as e:
                    print(f"    ❌ Live OCR extraction failed: {e}")

            except Exception as e:
                print(f"    ⚠️  Failed to load clicked_box image: {e}")
        else:
            print(f"    ⚠️  clicked_box not found: {clicked_box_path}")

        # 如果OCR没有成功，设置文本内容为空，不使用录制时的OCR数据
        if not ocr_successful:
            features.content = ""
            print("    ⚠️  No OCR result; text content set to empty")

        return features

    def find_matching_element(
        self,
        features: ElementFeatures,
        current_boxes: List[Dict[str, Any]],
        screenshot_path: str,
    ) -> MatchResult:
        """
        在候选boxes中查找最佳匹配 - 基于文本相似度的策略。

        Args:
            features: 录制时的元素特征（使用实时提取的文本内容）
            current_boxes: 当前屏幕解析出的所有boxes
            screenshot_path: 当前屏幕截图路径

        Returns:
            MatchResult: 匹配结果
        """
        if not current_boxes:
            print("⚠️ No elements found on the current screen")
            return MatchResult(confidence=0.0)

        # 加载当前屏幕
        try:
            current_screen_pil = Image.open(screenshot_path)
            current_screen_rgb = np.array(current_screen_pil.convert("RGB"))
            current_screen = self.cv2.cvtColor(
                current_screen_rgb, self.cv2.COLOR_RGB2BGR
            )
            screen_height, screen_width = current_screen.shape[:2]
        except Exception as e:
            print(f"❌ Could not load screenshot: {e}")
            return MatchResult(confidence=0.0)

        # 第一步：使用PaddleOCR识别所有候选boxes的文本
        print("  📋 Running live OCR on current-screen candidate boxes...")

        # 批量识别所有boxes的文本
        ocr_results = self.paddleocr_client.batch_recognize(
            screenshot_path, current_boxes
        )

        # 收集所有OCR识别的候选结果
        similarity_candidates = []
        valid_ocr_count = 0
        recording_content = features.content  # 录制box的实时OCR结果

        for i, (box, (recognized_text, ocr_confidence)) in enumerate(
            zip(current_boxes, ocr_results)
        ):
            try:
                if recognized_text and recognized_text.strip():  # 忽略空文本
                    valid_ocr_count += 1

                    # 计算录制文本与当前屏幕候选文本的相似度
                    if recording_content and recognized_text:
                        text_similarity = self._calculate_content_similarity(
                            recording_content, recognized_text
                        )
                    else:
                        text_similarity = 0.0

                    similarity_candidates.append(
                        (box, text_similarity, ocr_confidence, recognized_text)
                    )

                    content_preview = recognized_text[:20] if recognized_text else ""
                    print(
                        f"    Box {i}: '{content_preview}' (similarity: {text_similarity:.3f}, OCR confidence: {ocr_confidence:.3f})"
                    )
                else:
                    print(
                        f"    Box {i}: no text recognized (OCR confidence: {ocr_confidence:.3f})"
                    )
            except Exception as e:
                print(f"    Box {i}: recognition failed - {e}")
                continue

        if not similarity_candidates:
            print("❌ No candidate boxes with valid text were recognized")
            return MatchResult(confidence=0.0)

        print(f"  ✅ Recognized {valid_ocr_count} candidate boxes with text")
        if recording_content:
            print(f"    Recorded content: '{recording_content}'")

        # 按文本相似度排序（最高相似度优先）
        similarity_candidates.sort(key=lambda x: x[1], reverse=True)

        # 获取最高文本相似度
        best_similarity = similarity_candidates[0][1]
        best_candidates = [c for c in similarity_candidates if c[1] == best_similarity]

        if len(best_candidates) == 1:
            # 唯一最高相似度结果
            best_box, best_sim, best_conf, best_text = best_candidates[0]

            print(f"\n✅ Text-similarity match succeeded (unique top similarity)")
            print(f"   Text similarity: {best_sim:.3f}")
            print(f"   Candidate content: '{best_text[:30]}'")
            print(
                f"   Recorded content: '{recording_content[:30] if recording_content else 'N/A'}'"
            )

            # 结合图像相似度计算最终分数
            img_score = 0.0
            if features.clicked_box_image is not None:
                candidate_image = self._crop_box_from_screen(
                    best_box, current_screen, screen_width, screen_height
                )
                if candidate_image is not None and candidate_image.size > 0:
                    img_score = self._calculate_image_similarity(
                        features.clicked_box_image, candidate_image
                    )

            # 唯一最高相似度时，只使用文本相似度
            total_confidence = best_sim

            return MatchResult(
                matched_box=best_box,
                confidence=total_confidence,
                image_similarity=img_score,
                text_similarity=best_sim,
                matching_strategy="text_similarity_unique",
            )
        else:
            # 多个相同相似度候选，使用图像相似度辅助选择
            print(f"\n⚠️  Found {len(best_candidates)} candidates with equal similarity")
            print(f"    Using image similarity to break the tie...")

            refined_candidates = []
            for box, sim, conf, text in best_candidates:
                img_score = 0.0
                if features.clicked_box_image is not None:
                    candidate_image = self._crop_box_from_screen(
                        box, current_screen, screen_width, screen_height
                    )
                    if candidate_image is not None and candidate_image.size > 0:
                        img_score = self._calculate_image_similarity(
                            features.clicked_box_image, candidate_image
                        )

                # 当文本相似度为0时，使用图像相似度；否则主要为文本相似度
                if sim == 0:
                    total_score = img_score  # 当文本相似度为0时，只使用图像相似度
                else:
                    total_score = 0.8 * sim + 0.2 * img_score
                refined_candidates.append(
                    (box, total_score, sim, img_score, conf, text)
                )

            # 按综合分数排序
            refined_candidates.sort(key=lambda x: x[1], reverse=True)

            # 显示所有高相似度候选
            print("\n📊 Combined scores of high-similarity candidates:")
            for i, (box, total, sim, img_s, conf, text) in enumerate(
                refined_candidates
            ):
                text_preview = text[:30] if text else ""
                print(
                    f"  #{i + 1}: total={total:.3f} (similarity={sim:.3f}, image={img_s:.3f}, OCR confidence={conf:.3f}) "
                    f"content='{text_preview}'"
                )

            best_box, best_score, best_sim, best_img_score, best_conf, best_text = (
                refined_candidates[0]
            )

            if best_score < self.min_confidence:
                print(
                    f"\n⚠️ Best match score {best_score:.3f} is below threshold {self.min_confidence}"
                )

            print(f"\n✅ Found best matching element (text + image), confidence: {best_score:.3f}")

            return MatchResult(
                matched_box=best_box,
                confidence=best_score,
                image_similarity=best_img_score,
                text_similarity=best_sim,
                matching_strategy="text_similarity_with_image",
                candidates=refined_candidates,
            )

    def _calculate_match_score(
        self,
        features: ElementFeatures,
        candidate_box: Dict[str, Any],
        screenshot: np.ndarray,
        screen_width: int,
        screen_height: int,
    ) -> Tuple[float, float, float]:
        """
        计算候选box与录制特征的匹配分数（传统混合匹配，OCR分数过低时备用）。

        Args:
            features: 录制时的特征
            candidate_box: 候选box
            screenshot: 当前屏幕(OpenCV格式)
            screen_width: 屏幕宽度
            screen_height: 屏幕高度

        Returns:
            (总分, 图像相似度, 文本相似度)
        """
        # 1. 计算文本相似度（OCR）
        text_score = self._calculate_content_similarity(
            features.content, candidate_box.get("content", "")
        )

        # 2. 计算图像相似度
        img_score = 0.0
        if features.clicked_box_image is not None:
            # 从当前截图crop出候选box区域
            candidate_image = self._crop_box_from_screen(
                candidate_box, screenshot, screen_width, screen_height
            )

            if candidate_image is not None and candidate_image.size > 0:
                img_score = self._calculate_image_similarity(
                    features.clicked_box_image, candidate_image
                )
        else:
            # 没有图像，文本权重提高到100%
            return text_score, 0.0, text_score

        # 3. 综合评分（OCR优先策略下的备用评分）
        total_score = (
            self.weights["image"] * img_score + self.weights["text"] * text_score
        )

        return total_score, img_score, text_score

    def _crop_box_from_screen(
        self,
        box: Dict[str, Any],
        screenshot: np.ndarray,
        screen_width: int,
        screen_height: int,
    ) -> Optional[np.ndarray]:
        """
        从截图中crop出box区域。

        Args:
            box: box数据(包含归一化的bbox)
            screenshot: 当前截图
            screen_width: 屏幕宽度
            screen_height: 屏幕高度

        Returns:
            裁剪的图像(OpenCV格式)
        """
        bbox = box.get("bbox")
        if not bbox or len(bbox) != 4:
            return None

        try:
            # 归一化bbox转像素坐标
            x1 = int(bbox[0] * screen_width)
            y1 = int(bbox[1] * screen_height)
            x2 = int(bbox[2] * screen_width)
            y2 = int(bbox[3] * screen_height)

            # 确保坐标在有效范围内
            x1 = max(0, min(x1, screen_width - 1))
            y1 = max(0, min(y1, screen_height - 1))
            x2 = max(0, min(x2, screen_width))
            y2 = max(0, min(y2, screen_height))

            # 确保有效的区域
            if x2 <= x1 or y2 <= y1:
                return None

            # 裁剪
            cropped = screenshot[y1:y2, x1:x2]
            return cropped

        except Exception:
            return None

    def _calculate_image_similarity(
        self,
        template: np.ndarray,
        candidate: np.ndarray,
    ) -> float:
        """
        计算图像相似度。

        Args:
            template: 录制的clicked_box图像
            candidate: 候选box图像

        Returns:
            相似度 [0, 1]
        """
        if self.similarity_method == "ssim" and self.has_ssim:
            return self._calculate_ssim(template, candidate)
        elif self.similarity_method == "template_matching":
            return self._calculate_template_matching_similarity(template, candidate)
        else:
            return self._calculate_histogram_similarity(template, candidate)

    def _calculate_template_matching_similarity(
        self,
        template: np.ndarray,
        candidate: np.ndarray,
    ) -> float:
        """
        使用OpenCV matchTemplate计算图像相似度。

        Args:
            template: 模板图像
            candidate: 候选图像

        Returns:
            相似度 [0, 1]
        """
        try:
            # Resize candidate到template的大小
            if template.shape[:2] != candidate.shape[:2]:
                candidate = self.cv2.resize(
                    candidate, (template.shape[1], template.shape[0])
                )

            # 使用相关系数归一化方法 (TM_CCOEFF_NORMED)
            result = self.cv2.matchTemplate(
                candidate, template, self.cv2.TM_CCOEFF_NORMED
            )

            # 因为大小一致，result是一个1x1的矩阵
            score = result[0][0]

            # 将[-1, 1]映射到[0, 1]，负相关视为0
            return float(max(0.0, score))

        except Exception:
            return 0.0

    def _calculate_ssim(
        self,
        template: np.ndarray,
        candidate: np.ndarray,
    ) -> float:
        """
        使用SSIM计算图像相似度(更精确)。

        Args:
            template: 模板图像
            candidate: 候选图像

        Returns:
            相似度 [0, 1]
        """
        try:
            # Resize candidate到template的大小
            if template.shape[:2] != candidate.shape[:2]:
                candidate = self.cv2.resize(
                    candidate, (template.shape[1], template.shape[0])
                )

            # 转换为灰度图
            template_gray = self.cv2.cvtColor(template, self.cv2.COLOR_BGR2GRAY)
            candidate_gray = self.cv2.cvtColor(candidate, self.cv2.COLOR_BGR2GRAY)

            # 计算SSIM
            score = self.ssim(template_gray, candidate_gray)

            # SSIM范围是[-1, 1]，转换到[0, 1]
            return float((score + 1.0) / 2.0)

        except Exception:
            # SSIM失败时fallback到histogram
            return self._calculate_histogram_similarity(template, candidate)

    def _calculate_histogram_similarity(
        self,
        template: np.ndarray,
        candidate: np.ndarray,
    ) -> float:
        """
        使用直方图比较计算图像相似度。

        Args:
            template: 模板图像
            candidate: 候选图像

        Returns:
            相似度 [0, 1]
        """
        try:
            # 使用HSV色彩空间
            template_hsv = self.cv2.cvtColor(template, self.cv2.COLOR_BGR2HSV)
            candidate_hsv = self.cv2.cvtColor(candidate, self.cv2.COLOR_BGR2HSV)

            # 计算H和S通道的直方图
            h_bins = 50
            s_bins = 60
            hist_size = [h_bins, s_bins]
            h_ranges = [0, 180]
            s_ranges = [0, 256]
            ranges = h_ranges + s_ranges

            hist1 = self.cv2.calcHist([template_hsv], [0, 1], None, hist_size, ranges)
            hist2 = self.cv2.calcHist([candidate_hsv], [0, 1], None, hist_size, ranges)

            # 归一化直方图
            self.cv2.normalize(
                hist1, hist1, alpha=0, beta=1, norm_type=self.cv2.NORM_MINMAX
            )
            self.cv2.normalize(
                hist2, hist2, alpha=0, beta=1, norm_type=self.cv2.NORM_MINMAX
            )

            # 比较直方图
            similarity = self.cv2.compareHist(hist1, hist2, self.cv2.HISTCMP_CORREL)

            # 转换到[0, 1]
            similarity = (similarity + 1.0) / 2.0

            return float(max(0.0, min(1.0, similarity)))

        except Exception:
            return 0.0

    def _calculate_content_similarity(self, content1: str, content2: str) -> float:
        """
        计算文本内容相似度。

        Args:
            content1: 文本1
            content2: 文本2

        Returns:
            相似度 [0, 1]
        """
        if not content1 and not content2:
            return 1.0  # 都为空，视为相同

        if not content1 or not content2:
            return 0.0  # 一个为空，一个不为空

        # 完全匹配
        if content1 == content2:
            return 1.0

        # 模糊匹配
        similarity = SequenceMatcher(None, content1, content2).ratio()

        return similarity
