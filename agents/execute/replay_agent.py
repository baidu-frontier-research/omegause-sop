#!/usr/bin/env python
# coding=utf-8
"""
Replay Agent

主控制器,负责加载录制数据并执行replay流程。
"""

import json
import os
import time
from datetime import datetime
from dataclasses import dataclass, asdict, field
from typing import Any, Dict, List, Optional, Callable

import pyautogui
from PIL import Image

from .omniparser_client import OmniparserClient, ParseResult
from .element_matcher import ElementMatcher, ElementFeatures, MatchResult
from .action_executor import ActionExecutor


# 自定义异常
class ReplayException(Exception):
    """Replay过程中的异常"""

    pass


class ElementNotFoundError(ReplayException):
    """无法找到匹配元素"""

    pass


class ConfidenceTooLowError(ReplayException):
    """匹配置信度过低"""

    pass


@dataclass
class ReplayResult:
    """Replay结果"""

    success: bool
    total_events: int
    successful_events: int
    failed_events: List[Dict] = field(default_factory=list)
    replay_log: List[Dict] = field(default_factory=list)
    duration: float = 0.0
    error_message: str = ""


class ReplayAgent:
    """
    Replay Agent - 主控制器

    负责加载录制数据,解析当前屏幕,匹配元素并执行操作。
    """

    def __init__(
        self,
        recording_file: str,
        omniparser_url: str = None,
        min_confidence: float = 0.6,
        max_retries: int = 3,
        retry_delay: float = 1.0,
        similarity_method: str = "template_matching",
    ):
        """
        初始化Replay Agent。

        Args:
            recording_file: 录制文件路径
            omniparser_url: Omniparser服务URL
            min_confidence: 最低匹配置信度
            max_retries: 最大重试次数
            retry_delay: 重试间隔(秒)
            similarity_method: 图像相似度算法 ("template_matching", "ssim", "histogram")
        """
        self.recording_file = recording_file
        self.min_confidence = min_confidence
        self.max_retries = max_retries
        self.retry_delay = retry_delay

        # 初始化组件
        self.omniparser_client = OmniparserClient(url=omniparser_url)
        self.element_matcher = ElementMatcher(
            min_confidence=min_confidence,
            similarity_method=similarity_method,
        )
        self.action_executor = ActionExecutor()

        # 录制数据
        self.recording_data: Optional[Dict] = None
        self.events: List[Dict] = []
        self.recording_info: Optional[Dict] = None
        self.original_resolution: Optional[tuple] = None

        # Replay状态
        self.is_replaying = False
        self.current_sequence = 0
        self.replay_log: List[Dict] = []

        # 临时文件目录 (用于保存replay时的截图)
        recording_dir = os.path.dirname(recording_file)
        self.temp_dir = os.path.join(recording_dir, "replay_temp")
        os.makedirs(self.temp_dir, exist_ok=True)

        # Debug目录 (保存annotated图片和matched box)
        self.debug_dir = os.path.join(recording_dir, "replay_debug")
        os.makedirs(self.debug_dir, exist_ok=True)
        print(f"   Debug directory: {self.debug_dir}")

        print(f"🎬 ReplayAgent initialized")
        print(f"   Recording file: {recording_file}")
        print(f"   Min confidence: {min_confidence}")
        print(f"   Max retries: {max_retries}")

    def load_recording(self) -> Dict:
        """
        加载录制数据。

        Returns:
            录制数据字典
        """
        print(f"\n📂 Loading recording file: {self.recording_file}")

        if not os.path.exists(self.recording_file):
            raise FileNotFoundError(f"Recording file not found: {self.recording_file}")

        with open(self.recording_file, "r", encoding="utf-8") as f:
            self.recording_data = json.load(f)

        self.recording_info = self.recording_data.get("recording_info", {})
        self.events = self.recording_data.get("events", [])

        print(f"✅ Recording data loaded")
        print(f"   Total events: {len(self.events)}")
        print(f"   Recorded at: {self.recording_info.get('start_time', 'Unknown')}")

        # 提取原始分辨率
        self._extract_original_resolution()

        return self.recording_data

    def _extract_original_resolution(self) -> None:
        """从第一个截图提取原始分辨率。"""
        for event in self.events:
            screenshot_path = event.get("screenshot_path", "")
            if screenshot_path and os.path.exists(screenshot_path):
                try:
                    with Image.open(screenshot_path) as img:
                        self.original_resolution = img.size
                        print(
                            f"   Original resolution: {self.original_resolution[0]}x{self.original_resolution[1]}"
                        )
                        return
                except Exception as e:
                    print(f"⚠️ Could not read screenshot: {e}")

        print("⚠️ Could not determine original resolution")

    def start_replay(
        self,
        speed_factor: float = 1.0,
        start_from_sequence: int = 0,
        stop_at_sequence: Optional[int] = None,
        on_event_callback: Optional[Callable] = None,
        step_by_step: bool = False,  # 新增：单步执行模式
    ) -> ReplayResult:
        """
        开始replay。

        Args:
            speed_factor: 速度倍率 (1.0=原速, 2.0=2倍速)
            start_from_sequence: 从哪个序列号开始
            stop_at_sequence: 在哪个序列号停止
            on_event_callback: 事件回调函数
            step_by_step: 是否单步执行（每步需要回车确认）

        Returns:
            ReplayResult: Replay结果
        """
        if not self.recording_data:
            self.load_recording()

        print(f"\n{'=' * 60}")
        print(f"🎬 Starting replay")
        print(f"{'=' * 60}")

        self.is_replaying = True
        self.replay_log = []
        start_time = time.time()

        successful_events = 0
        failed_events = []

        try:
            # 过滤事件
            events_to_replay = [
                e
                for e in self.events
                if e.get("sequence", 0) >= start_from_sequence
                and (
                    stop_at_sequence is None or e.get("sequence", 0) <= stop_at_sequence
                )
            ]

            print(f"📋 Will replay {len(events_to_replay)} events")
            print(f"   Speed factor: {speed_factor}x")
            print(f"{'=' * 60}\n")

            # 逐个处理事件
            for event in events_to_replay:
                if not self.is_replaying:
                    print("⏸️  Replay paused")
                    break

                self.current_sequence = event.get("sequence", 0)
                event_type = event.get("event_type", "")

                print(f"\n[Seq {self.current_sequence}] Handling event: {event_type}")

                # 单步执行模式：等待用户确认
                if step_by_step:
                    input("  ⏸️  Press Enter to continue to the next step...")
                time.sleep(10)
                try:
                    # 根据事件类型分发处理
                    if event_type in ["left_click", "right_click", "middle_click"]:
                        self._replay_mouse_event(event, speed_factor)
                    elif event_type == "text_input":
                        self._replay_text_input(event, speed_factor)
                    elif event_type == "keyboard":
                        self._replay_keyboard_event(event, speed_factor)
                    else:
                        print(f"⚠️ Unknown event type: {event_type}")
                        continue

                    successful_events += 1

                    # 调用回调
                    if on_event_callback:
                        on_event_callback(event, success=True)

                except Exception as e:
                    print(f"❌ Event handling failed: {e}")
                    failed_events.append(
                        {
                            "sequence": self.current_sequence,
                            "event_type": event_type,
                            "error": str(e),
                        }
                    )

                    # 调用回调
                    if on_event_callback:
                        on_event_callback(event, success=False, error=str(e))

                    # 如果是关键错误,停止replay
                    if isinstance(e, (ElementNotFoundError, ConfidenceTooLowError)):
                        print(f"\n❌ Critical error, stopping replay")
                        break

            # 计算耗时
            duration = time.time() - start_time

            # 生成结果
            success = len(failed_events) == 0
            result = ReplayResult(
                success=success,
                total_events=len(events_to_replay),
                successful_events=successful_events,
                failed_events=failed_events,
                replay_log=self.replay_log,
                duration=duration,
            )

            # 打印总结
            self._print_summary(result)

            return result

        except Exception as e:
            duration = time.time() - start_time
            print(f"\n❌ Replay aborted with error: {e}")

            return ReplayResult(
                success=False,
                total_events=len(events_to_replay)
                if "events_to_replay" in locals()
                else 0,
                successful_events=successful_events,
                failed_events=failed_events,
                replay_log=self.replay_log,
                duration=duration,
                error_message=str(e),
            )

        finally:
            self.is_replaying = False

    def _replay_mouse_event(self, event: Dict, speed_factor: float) -> None:
        """
        Replay鼠标点击事件 - 混合匹配版本

        Args:
            event: 事件数据
            speed_factor: 速度倍率
        """
        # 带重试的执行
        for attempt in range(self.max_retries):
            try:
                # 1. 截取当前屏幕
                current_screenshot = self._capture_current_screenshot()

                # 2. 调用omniparser解析当前屏幕
                print("  🔍 Parsing current screen...")
                parse_result = self.omniparser_client.parse_screenshot(
                    current_screenshot
                )
                current_boxes = parse_result.boxes

                # 3. 提取录制时的元素特征（clicked_box图像+文本）
                print("  📋 Extracting element features...")
                recorded_features = self.element_matcher.extract_features(event)

                # 4. 在候选boxes中查找最佳匹配（混合图像+文本）
                match_result = self.element_matcher.find_matching_element(
                    recorded_features, current_boxes, current_screenshot
                )

                # 5. 验证匹配结果
                if match_result.confidence < self.min_confidence:
                    error_msg = f"Match confidence too low: {match_result.confidence:.3f} < {self.min_confidence}"
                    raise ConfidenceTooLowError(error_msg)

                # 6. 计算目标坐标（从matched_box的bbox）
                target_x, target_y = self.action_executor.calculate_target_position(
                    match_result.matched_box["bbox"], current_screenshot
                )

                # 6.5. 保存调试图片
                self._save_debug_images(
                    event, parse_result, match_result, current_screenshot
                )

                # 7. 执行点击操作
                button_type = event["event_type"].split("_")[0]  # left/right/middle
                self.action_executor.click(
                    target_x,
                    target_y,
                    button=button_type,
                    delay_after=0.3 / speed_factor,
                )

                # 8. 记录日志
                self._log_replay_action(
                    event, match_result, target_x, target_y, success=True
                )

                print(f"  ✅ Click succeeded: ({target_x}, {target_y})")

                return  # 成功，退出重试循环

            except Exception as e:
                if attempt < self.max_retries - 1:
                    print(f"  ⚠️ Attempt {attempt + 1}/{self.max_retries} failed, retrying...")
                    time.sleep(self.retry_delay)
                else:
                    # 所有重试都失败
                    self._log_replay_action(
                        event, None, 0, 0, success=False, error=str(e)
                    )
                    raise

    def _replay_text_input(self, event: Dict, speed_factor: float) -> None:
        """
        Replay文本输入事件。

        Args:
            event: 事件数据
            speed_factor: 速度倍率
        """
        text = event.get("text", "")
        print(f"  ⌨️  Typing text: '{text}'")

        self.action_executor.type_text(text, interval=0.05 / speed_factor)

        # 记录日志
        self._log_replay_action(event, None, 0, 0, success=True)

    def _replay_keyboard_event(self, event: Dict, speed_factor: float) -> None:
        """
        Replay键盘按键事件。

        Args:
            event: 事件数据
            speed_factor: 速度倍率
        """
        key = event.get("key", "")
        modifiers = event.get("modifiers", [])

        if modifiers:
            print(f"  ⌨️  Pressing hotkey: {'+'.join(modifiers + [key])}")
        else:
            print(f"  ⌨️  Pressing key: {key}")

        self.action_executor.press_key(key, modifiers, delay_after=0.3 / speed_factor)

        # 记录日志
        self._log_replay_action(event, None, 0, 0, success=True)

    def _capture_current_screenshot(self) -> str:
        """
        截取当前屏幕。

        Returns:
            截图文件路径
        """
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")[:-3]
        filename = f"replay_screenshot_{timestamp}.png"
        filepath = os.path.join(self.temp_dir, filename)

        screenshot = pyautogui.screenshot()
        screenshot.save(filepath)

        return filepath

    def _save_debug_images(
        self,
        event: Dict,
        parse_result: Any,
        match_result: MatchResult,
        screenshot_path: str,
    ) -> None:
        """
        保存调试图片：annotated图片和matched box的crop图片

        Args:
            event: 事件数据
            parse_result: omniparser解析结果
            match_result: 匹配结果
            screenshot_path: 当前截图路径
        """
        try:
            import shutil
            import cv2

            sequence = event.get("sequence", 0)

            # 1. 保存annotated图片（如果有）
            if (
                hasattr(parse_result, "annotated_image_path")
                and parse_result.annotated_image_path
            ):
                if os.path.exists(parse_result.annotated_image_path):
                    annotated_dest = os.path.join(
                        self.debug_dir, f"{sequence:03d}_annotated.png"
                    )
                    shutil.copy2(parse_result.annotated_image_path, annotated_dest)

            # 2. 保存matched box的crop图片
            if match_result.matched_box:
                # 加载当前截图
                current_screen = cv2.imread(screenshot_path)
                if current_screen is not None:
                    screen_height, screen_width = current_screen.shape[:2]

                    # 获取matched box的bbox
                    bbox = match_result.matched_box.get("bbox")
                    if bbox and len(bbox) == 4:
                        # 归一化坐标转像素
                        x1 = int(bbox[0] * screen_width)
                        y1 = int(bbox[1] * screen_height)
                        x2 = int(bbox[2] * screen_width)
                        y2 = int(bbox[3] * screen_height)

                        # 确保坐标在有效范围内
                        x1 = max(0, min(x1, screen_width - 1))
                        y1 = max(0, min(y1, screen_height - 1))
                        x2 = max(0, min(x2, screen_width))
                        y2 = max(0, min(y2, screen_height))

                        # Crop matched box
                        if x2 > x1 and y2 > y1:
                            matched_crop = current_screen[y1:y2, x1:x2]
                            matched_dest = os.path.join(
                                self.debug_dir, f"{sequence:03d}_matched_box.png"
                            )
                            cv2.imwrite(matched_dest, matched_crop)

        except Exception as e:
            # 保存调试图片失败不应该影响replay，只记录警告
            print(f"    ⚠️ Failed to save debug image: {e}")

    def _log_replay_action(
        self,
        event: Dict,
        match_result: Optional[MatchResult],
        x: int,
        y: int,
        success: bool,
        error: str = "",
    ) -> None:
        """
        记录replay操作日志。

        Args:
            event: 事件数据
            match_result: 匹配结果
            x: 实际点击的X坐标
            y: 实际点击的Y坐标
            success: 是否成功
            error: 错误信息
        """
        log_entry = {
            "timestamp": datetime.now().isoformat(),
            "sequence": event.get("sequence", 0),
            "event_type": event.get("event_type", ""),
            "success": success,
        }

        if event.get("event_type") in ["left_click", "right_click", "middle_click"]:
            log_entry["recorded_position"] = (event.get("x", 0), event.get("y", 0))
            log_entry["replay_position"] = (x, y)

            if match_result:
                log_entry["match_confidence"] = match_result.confidence
                log_entry["matched_element"] = {
                    "type": match_result.matched_box.get("type", ""),
                    "content": match_result.matched_box.get("content", ""),
                    "bbox": match_result.matched_box.get("bbox", []),
                }

        if not success:
            log_entry["error"] = error

        self.replay_log.append(log_entry)

    def _log_replay_action_image(
        self,
        event: Dict,
        match_result: Optional[MatchResult],
        x: int,
        y: int,
        success: bool,
        error: str = "",
    ) -> None:
        """
        记录replay操作日志 - 纯图像匹配版本

        Args:
            event: 事件数据
            match_result: 匹配结果
            x: 实际点击的X坐标
            y: 实际点击的Y坐标
            success: 是否成功
            error: 错误信息
        """
        log_entry = {
            "timestamp": datetime.now().isoformat(),
            "sequence": event.get("sequence", 0),
            "event_type": event.get("event_type", ""),
            "success": success,
        }

        if event.get("event_type") in ["left_click", "right_click", "middle_click"]:
            log_entry["recorded_position"] = (event.get("x", 0), event.get("y", 0))
            log_entry["replay_position"] = (x, y)

            if match_result:
                log_entry["match_confidence"] = match_result.confidence
                log_entry["match_bbox"] = match_result.match_bbox

        if not success:
            log_entry["error"] = error

        self.replay_log.append(log_entry)

    def _print_summary(self, result: ReplayResult) -> None:
        """
        打印replay总结。

        Args:
            result: Replay结果
        """
        print(f"\n{'=' * 60}")
        if result.success:
            print(f"✨ Replay complete!")
        else:
            print(f"❌ Replay failed")

        print(f"{'=' * 60}")
        print(f"Total events: {result.total_events}")
        print(f"Successful events: {result.successful_events}")
        print(f"Failed events: {len(result.failed_events)}")
        print(f"Duration: {result.duration:.2f}s")

        if result.failed_events:
            print(f"\nFailure details:")
            for failed in result.failed_events:
                print(
                    f"  - Seq {failed['sequence']}: {failed['event_type']} - {failed['error']}"
                )

        print(f"{'=' * 60}\n")

    def pause_replay(self) -> None:
        """暂停replay。"""
        self.is_replaying = False
        print("⏸️  Replay paused")

    def resume_replay(self) -> None:
        """恢复replay。"""
        self.is_replaying = True
        print("▶️  Replay resumed")

    def stop_replay(self) -> None:
        """停止replay。"""
        self.is_replaying = False
        print("⏹️  Replay stopped")

    def save_replay_log(self, output_file: Optional[str] = None) -> str:
        """
        保存replay日志。

        Args:
            output_file: 输出文件路径

        Returns:
            保存的文件路径
        """
        if output_file is None:
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            output_file = f"replay_log_{timestamp}.json"

        log_dir = os.path.join(os.path.dirname(self.recording_file), "replay_logs")
        os.makedirs(log_dir, exist_ok=True)
        filepath = os.path.join(log_dir, output_file)

        with open(filepath, "w", encoding="utf-8") as f:
            json.dump(self.replay_log, f, indent=2, ensure_ascii=False)

        print(f"💾 Replay log saved: {filepath}")
        return filepath
