# Copyright (c) 2026 Baidu, Inc. and its affiliates
# Copyright the Qwen team, Alibaba Group
#
# Adapted from QwenLM/Qwen3-VL, cookbooks/utils/agent_function_call.py
# https://github.com/QwenLM/Qwen3-VL
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Modifications from the upstream version: added docstrings, and extracted the
# click-action list in ComputerUse.call into a local variable. Tool behaviour,
# action schemas and prompt text are unchanged.

"""Agent function call tools for mobile and computer use interactions."""

from typing import Union, Tuple, List

from qwen_agent.tools.base import BaseTool, register_tool


@register_tool("mobile_use")
class MobileUse(BaseTool):
    """Mobile device interaction tool with touchscreen capabilities."""

    @property
    def description(self):
        """获取移动设备交互工具的描述信息

        返回:
            str: 包含移动设备交互功能的详细描述字符串，包括支持的
                 操作类型、分辨率信息和操作指导说明
        """
        return f"""
Use a touchscreen to interact with a mobile device, and take screenshots.
* This is an interface to a mobile device with touchscreen. You can perform actions like clicking, typing, swiping, etc.
* Some applications may take time to start or process actions, so you may need to wait and take successive screenshots to see the results of your actions.
* The screen's resolution is {self.display_width_px}x{self.display_height_px}.
* Make sure to click any buttons, links, icons, etc with the cursor tip in the center of the element. Don't click boxes on their edges unless asked.
""".strip()

    parameters = {
        "properties": {
            "action": {
                "description": """
The action to perform. The available actions are:
* `key`: Perform a key event on the mobile device.
    - This supports adb's `keyevent` syntax.
    - Examples: "volume_up", "volume_down", "power", "camera", "clear".
* `click`: Click the point on the screen with coordinate (x, y).
* `long_press`: Press the point on the screen with coordinate (x, y) for specified seconds.
* `swipe`: Swipe from the starting point with coordinate (x, y) to the end point with coordinates2 (x2, y2).
* `type`: Input the specified text into the activated input box.
* `system_button`: Press the system button.
* `open`: Open an app on the device.
* `wait`: Wait specified seconds for the change to happen.
* `terminate`: Terminate the current task and report its completion status.
""".strip(),
                "enum": [
                    "key",
                    "click",
                    "long_press",
                    "swipe",
                    "type",
                    "system_button",
                    "open",
                    "wait",
                    "terminate",
                ],
                "type": "string",
            },
            "coordinate": {
                "description": """(x, y): The x (pixels from the left edge) and y 
                (pixels from the top edge) coordinates to move the mouse to. Required 
                only by `action=click`, `action=long_press`, and `action=swipe`.""",
                "type": "array",
            },
            "coordinate2": {
                "description": """(x, y): The x (pixels from the left edge) and y 
                (pixels from the top edge) coordinates to move the mouse to. Required 
                only by `action=swipe`.""",
                "type": "array",
            },
            "text": {
                "description": "Required only by `action=key`, `action=type`, and `action=open`.",
                "type": "string",
            },
            "time": {
                "description": "The seconds to wait. Required only by `action=long_press` and `action=wait`.",
                "type": "number",
            },
            "button": {
                "description": """Back means returning to the previous interface, Home 
                means returning to the desktop, Menu means opening the application 
                background menu, and Enter means pressing the enter. Required only by 
                `action=system_button`""",
                "enum": [
                    "Back",
                    "Home",
                    "Menu",
                    "Enter",
                ],
                "type": "string",
            },
            "status": {
                "description": "The status of the task. Required only by `action=terminate`.",
                "type": "string",
                "enum": ["success", "failure"],
            },
        },
        "required": ["action"],
        "type": "object",
    }

    def __init__(self, cfg=None):
        """Initialize MobileUse tool with display configuration.

        Args:
            cfg: Configuration dictionary containing display dimensions.
        """
        self.display_width_px = cfg["display_width_px"]
        self.display_height_px = cfg["display_height_px"]
        super().__init__(cfg)

    def call(self, params: Union[str, dict], **kwargs):
        """Execute the specified mobile action.

        Args:
            params: Action parameters either as string or dict.
            **kwargs: Additional keyword arguments.

        Returns:
            Result of the action execution.
        """
        params = self._verify_json_format_args(params)
        action = params["action"]
        if action == "key":
            return self._key(params["text"])
        elif action == "click":
            return self._click(coordinate=params["coordinate"])
        elif action == "long_press":
            return self._long_press(
                coordinate=params["coordinate"], time=params["time"]
            )
        elif action == "swipe":
            return self._swipe(
                coordinate=params["coordinate"], coordinate2=params["coordinate2"]
            )
        elif action == "type":
            return self._type(params["text"])
        elif action == "system_button":
            return self._system_button(params["button"])
        elif action == "open":
            return self._open(params["text"])
        elif action == "wait":
            return self._wait(params["time"])
        elif action == "terminate":
            return self._terminate(params["status"])
        else:
            raise ValueError(f"Unknown action: {action}")

    def _key(self, text: str):
        """Perform a key event on the mobile device.

        Args:
            text: Key event text (e.g., "volume_up", "power").
        """
        raise NotImplementedError()

    def _click(self, coordinate: Tuple[int, int]):
        """Click at the specified coordinate.

        Args:
            coordinate: (x, y) pixel coordinates.
        """
        raise NotImplementedError()

    def _long_press(self, coordinate: Tuple[int, int], time: int):
        """Long press at the specified coordinate for given time.

        Args:
            coordinate: (x, y) pixel coordinates.
            time: Press duration in seconds.
        """
        raise NotImplementedError()

    def _swipe(self, coordinate: Tuple[int, int], coordinate2: Tuple[int, int]):
        """Swipe from start to end coordinate.

        Args:
            coordinate: Starting (x, y) pixel coordinates.
            coordinate2: Ending (x, y) pixel coordinates.
        """
        raise NotImplementedError()

    def _type(self, text: str):
        """Type text into the activated input box.

        Args:
            text: Text to input.
        """
        raise NotImplementedError()

    def _system_button(self, button: str):
        """Press a system button.

        Args:
            button: System button name (Back, Home, Menu, Enter).
        """
        raise NotImplementedError()

    def _open(self, text: str):
        """Open an app on the device.

        Args:
            text: App name or package to open.
        """
        raise NotImplementedError()

    def _wait(self, time: int):
        """Wait for specified time.

        Args:
            time: Wait duration in seconds.
        """
        raise NotImplementedError()

    def _terminate(self, status: str):
        """Terminate the current task.

        Args:
            status: Task completion status ("success" or "failure").
        """
        raise NotImplementedError()


@register_tool("computer_use")
class ComputerUse(BaseTool):
    """Computer interaction tool with mouse and keyboard capabilities."""

    @property
    def description(self):
        """获取计算机交互工具的描述信息

        返回:
            str: 包含计算机交互功能的详细描述字符串，包括
                 - 支持的鼠标键盘操作类型
                 - 屏幕分辨率信息 ({self.display_width_px}x{self.display_height_px})
                 - 桌面GUI交互的操作指导说明
                 - 等待应用启动的处理建议
                 - 光标定位和点击操作的最佳实践
        """
        return f"""
Use a mouse and keyboard to interact with a computer, and take screenshots.
* This is an interface to a desktop GUI. You do not have access to a terminal or applications menu. You must click on desktop icons to start applications.
* Some applications may take time to start or process actions, so you may need to wait and take successive screenshots to see the results of your actions. E.g. if you click on Firefox and a window doesn't open, try wait and taking another screenshot.
* The screen's resolution is {self.display_width_px}x{self.display_height_px}.
* Whenever you intend to move the cursor to click on an element like an icon, you should consult a screenshot to determine the coordinates of the element before moving the cursor.
* If you tried clicking on a program or link but it failed to load, even after waiting, try adjusting your cursor position so that the tip of the cursor visually falls on the element that you want to click.
* Make sure to click any buttons, links, icons, etc with the cursor tip in the center of the element. Don't click boxes on their edges.
""".strip()

    parameters = {
        "properties": {
            "action": {
                "description": """
The action to perform. The available actions are:
* `key`: Performs key down presses on the arguments passed in order, then performs key releases in reverse order.
* `type`: Type a string of text on the keyboard.
* `mouse_move`: Move the cursor to a specified (x, y) pixel coordinate on the screen.
* `left_click`: Click the left mouse button at a specified (x, y) pixel coordinate on the screen.
* `left_click_drag`: Click and drag the cursor to a specified (x, y) pixel coordinate on the screen.
* `right_click`: Click the right mouse button at a specified (x, y) pixel coordinate on the screen.
* `middle_click`: Click the middle mouse button at a specified (x, y) pixel coordinate on the screen.
* `double_click`: Double-click the left mouse button at a specified (x, y) pixel coordinate on the screen.
* `triple_click`: Triple-click the left mouse button at a specified (x, y) pixel coordinate on the screen (simulated as double-click since it's the closest action).
* `scroll`: Performs a scroll of the mouse scroll wheel.
* `hscroll`: Performs a horizontal scroll (mapped to regular scroll).
* `wait`: Wait specified seconds for the change to happen.
* `terminate`: Terminate the current task and report its completion status.
* `answer`: Answer a question.
""".strip(),
                "enum": [
                    "key",
                    "type",
                    "mouse_move",
                    "left_click",
                    "left_click_drag",
                    "right_click",
                    "middle_click",
                    "double_click",
                    "triple_click",
                    "scroll",
                    "hscroll",
                    "wait",
                    "terminate",
                    "answer",
                ],
                "type": "string",
            },
            "keys": {
                "description": "Required only by `action=key`.",
                "type": "array",
            },
            "text": {
                "description": "Required only by `action=type` and `action=answer`.",
                "type": "string",
            },
            "coordinate": {
                "description": """(x, y): The x (pixels from the left edge) and y 
                (pixels from the top edge) coordinates to move the mouse to.""",
                "type": "array",
            },
            "pixels": {
                "description": """The amount of scrolling to perform. Positive values
                scroll up, negative values scroll down. Required only by 
                `action=scroll` and `action=hscroll`.""",
                "type": "number",
            },
            "time": {
                "description": "The seconds to wait. Required only by `action=wait`.",
                "type": "number",
            },
            "status": {
                "description": "The status of the task. Required only by `action=terminate`.",
                "type": "string",
                "enum": ["success", "failure"],
            },
        },
        "required": ["action"],
        "type": "object",
    }

    def __init__(self, cfg=None):
        """Initialize ComputerUse tool with display configuration.

        Args:
            cfg: Configuration dictionary containing display dimensions.
        """
        self.display_width_px = cfg["display_width_px"]
        self.display_height_px = cfg["display_height_px"]
        super().__init__(cfg)

    def call(self, params: Union[str, dict], **kwargs):
        """Execute the specified computer action.

        Args:
            params: Action parameters either as string or dict.
            **kwargs: Additional keyword arguments.

        Returns:
            Result of the action execution.
        """
        params = self._verify_json_format_args(params)
        action = params["action"]
        click_actions = [
            "left_click",
            "right_click",
            "middle_click",
            "double_click",
            "triple_click",
        ]
        if action in click_actions:
            return self._mouse_click(action)
        elif action == "key":
            return self._key(params["keys"])
        elif action == "type":
            return self._type(params["text"])
        elif action == "mouse_move":
            return self._mouse_move(params["coordinate"])
        elif action == "left_click_drag":
            return self._left_click_drag(params["coordinate"])
        elif action == "scroll":
            return self._scroll(params["pixels"])
        elif action == "hscroll":
            return self._hscroll(params["pixels"])
        elif action == "answer":
            return self._answer(params["text"])
        elif action == "wait":
            return self._wait(params["time"])
        elif action == "terminate":
            return self._terminate(params["status"])
        else:
            raise ValueError(f"Invalid action: {action}")

    def _mouse_click(self, button: str):
        """Perform mouse click action.

        Args:
            button: Click type (left_click, right_click, etc.).
        """
        raise NotImplementedError()

    def _key(self, keys: List[str]):
        """Perform key press actions.

        Args:
            keys: List of keys to press.
        """
        raise NotImplementedError()

    def _type(self, text: str):
        """Type text on the keyboard.

        Args:
            text: Text to type.
        """
        raise NotImplementedError()

    def _mouse_move(self, coordinate: Tuple[int, int]):
        """Move mouse to specified coordinate.

        Args:
            coordinate: (x, y) pixel coordinates.
        """
        raise NotImplementedError()

    def _left_click_drag(self, coordinate: Tuple[int, int]):
        """Perform left click drag to coordinate.

        Args:
            coordinate: Destination (x, y) pixel coordinates.
        """
        raise NotImplementedError()

    def _scroll(self, pixels: int):
        """Perform vertical scroll.

        Args:
            pixels: Number of pixels to scroll.
        """
        raise NotImplementedError()

    def _hscroll(self, pixels: int):
        """Perform horizontal scroll.

        Args:
            pixels: Number of pixels to scroll.
        """
        raise NotImplementedError()

    def _answer(self, text: str):
        """Answer a question.

        Args:
            text: Answer text.
        """
        raise NotImplementedError()

    def _wait(self, time: int):
        """Wait for specified time.

        Args:
            time: Wait duration in seconds.
        """
        raise NotImplementedError()

    def _terminate(self, status: str):
        """Terminate the current task.

        Args:
            status: Task completion status ("success" or "failure").
        """
        raise NotImplementedError()
