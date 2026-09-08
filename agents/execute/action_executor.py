#!/usr/bin/env python
# coding=utf-8
"""
Action Executor

执行具体的鼠标和键盘操作。
"""

import time
from typing import List, Optional, Tuple

import pyautogui
import keyboard as kb
from PIL import Image


class ActionExecutor:
    """
    操作执行器,负责执行鼠标点击、键盘输入等操作。
    """

    def __init__(self, default_delay: float = 0.5):
        """
        初始化操作执行器。

        Args:
            default_delay: 操作之间的默认延迟(秒)
        """
        self.default_delay = default_delay

        # 禁用pyautogui的failsafe
        pyautogui.FAILSAFE = False

        print(f"🎮 ActionExecutor initialized with delay: {default_delay}s")

    def click(
        self,
        x: int,
        y: int,
        button: str = "left",
        delay_after: Optional[float] = None,
    ) -> None:
        """
        执行鼠标点击操作。

        Args:
            x: X坐标
            y: Y坐标
            button: 按钮类型 ('left', 'right', 'middle')
            delay_after: 点击后的延迟时间,None则使用默认延迟
        """
        try:
            # 移动到目标位置
            pyautogui.moveTo(x, y, duration=0.2)

            # 执行点击
            pyautogui.click(x, y, button=button)

            print(f"🖱️  Click: ({x}, {y}), button: {button}")

            # 延迟
            delay = delay_after if delay_after is not None else self.default_delay
            if delay > 0:
                time.sleep(delay)

        except Exception as e:
            print(f"❌ Click failed: {e}")
            raise

    def type_text(
        self, text: str, interval: float = 0.05, delay_after: Optional[float] = None
    ) -> None:
        """
        输入文本。

        Args:
            text: 要输入的文本
            interval: 字符之间的间隔时间
            delay_after: 输入后的延迟时间
        """
        try:
            pyautogui.write(text, interval=interval)

            print(f"⌨️  Typing text: '{text}'")

            # 延迟
            delay = delay_after if delay_after is not None else self.default_delay
            if delay > 0:
                time.sleep(delay)

        except Exception as e:
            print(f"❌ Text input failed: {e}")
            raise

    def press_key(
        self,
        key: str,
        modifiers: Optional[List[str]] = None,
        delay_after: Optional[float] = None,
    ) -> None:
        """
        按下键盘按键(支持组合键)。

        Args:
            key: 按键名称
            modifiers: 修饰键列表 (如 ['ctrl', 'shift'])
            delay_after: 按键后的延迟时间
        """
        try:
            if modifiers:
                # 组合键
                hotkey = "+".join(modifiers + [key])
                kb.press_and_release(hotkey)
                print(f"⌨️  Pressing hotkey: {hotkey}")
            else:
                # 单个按键
                kb.press_and_release(key)
                print(f"⌨️  Pressing key: {key}")

            # 延迟
            delay = delay_after if delay_after is not None else self.default_delay
            if delay > 0:
                time.sleep(delay)

        except Exception as e:
            print(f"❌ Keyboard action failed: {e}")
            raise

    def wait(self, seconds: float) -> None:
        """
        等待指定时间。

        Args:
            seconds: 等待秒数
        """
        print(f"⏳ Waiting {seconds:.2f}s...")
        time.sleep(seconds)

    def calculate_target_position(
        self, bbox: List[float], screenshot_path: str
    ) -> Tuple[int, int]:
        """
        根据归一化bbox计算目标像素坐标(box中心点)。

        Args:
            bbox: 归一化坐标 [x1, y1, x2, y2] in [0, 1]
            screenshot_path: 截图路径(用于获取分辨率)

        Returns:
            (x, y): 像素坐标
        """
        # 获取屏幕分辨率
        with Image.open(screenshot_path) as img:
            width, height = img.size

        # 转换为像素坐标
        left = int(bbox[0] * width)
        top = int(bbox[1] * height)
        right = int(bbox[2] * width)
        bottom = int(bbox[3] * height)

        # 计算中心点
        center_x = (left + right) // 2
        center_y = (top + bottom) // 2

        return center_x, center_y

    def set_default_delay(self, delay: float) -> None:
        """
        设置默认延迟时间。

        Args:
            delay: 延迟秒数
        """
        self.default_delay = delay
        print(f"⚙️  Default delay set to: {delay}s")
