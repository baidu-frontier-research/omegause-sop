#!/usr/bin/env python
# coding=utf-8
"""
Executor - AI 执行模块

基于 VLM 视觉定位的 GUI 操作执行器。
读取 reason 模块生成的 prompt.json，逐步执行操作。

这是 observe -> reason -> execute 流水线的第三步。
"""

import base64
import json
import os
import time
from dataclasses import dataclass, field
from datetime import datetime
from typing import Dict, List, Optional, Tuple

from dotenv import load_dotenv

load_dotenv()
import pyautogui
from openai import OpenAI
from PIL import Image, ImageDraw
from qwen_agent.llm.fncall_prompts.nous_fncall_prompt import (
    ContentItem,
    Message,
    NousFnCallPrompt,
)

# 导入项目模块
from utils.agent_function_call import ComputerUse
from utils.notify import ask_user_action, toast

# 禁用 pyautogui 的 failsafe
pyautogui.FAILSAFE = False


class UserAbortReplay(Exception):
    """用户在验证警报中选择停止，主动终止 AI 执行（不应被验证的 try/except 吞掉）"""

    pass


class RetryExecution(Exception):
    """用户在验证警报中选择重试，从头重新开始 AI 执行（不应被验证的 try/except 吞掉）"""

    pass


@dataclass
class ExecutorConfig:
    """执行器配置"""

    model_id: str = os.environ.get("MODEL_NAME", "qwen3-vl-235b-a22b-instruct")
    api_base_url: str = os.environ.get("API_URL", "https://qianfan.baidubce.com/v2")
    display_width: int = 1000
    display_height: int = 1000
    min_pixels: int = 3136
    max_pixels: int = 12845056
    default_wait_time: float = 1.0
    use_recorded_timing: bool = True
    speed_factor: float = 1.0
    step_by_step: bool = False
    domain_prompt_file: str = ""  # 领域 SOP 文件路径（如 PVsyst 操作规则）
    user_params_file: str = ""  # 用户参数 JSON 文件路径（输入值替换规则）


@dataclass
class ExecutionResult:
    """重放结果"""

    success: bool = True
    total_events: int = 0
    executed_events: int = 0
    failed_events: List[Dict] = field(default_factory=list)
    execution_log: List[Dict] = field(default_factory=list)
    duration: float = 0.0
    error_message: str = ""


# The three helpers below (encode_image / smart_resize / draw_point) are adapted
# from the Qwen-VL cookbooks (https://github.com/QwenLM/Qwen3-VL), Copyright the
# Qwen team, Alibaba Group, licensed under the Apache License, Version 2.0.
# See http://www.apache.org/licenses/LICENSE-2.0 and the NOTICE file.
def encode_image(image_path: str) -> str:
    """将图片编码为 base64"""
    with open(image_path, "rb") as image_file:
        return base64.standard_b64encode(image_file.read()).decode("utf-8")


def smart_resize(
    height: int,
    width: int,
    factor: int = 32,
    min_pixels: int = 3136,
    max_pixels: int = 12845056,
) -> Tuple[int, int]:
    """智能调整图片尺寸"""
    if height < factor or width < factor:
        raise ValueError(f"height:{height} or width:{width} must be larger than factor:{factor}")
    elif max(height, width) / min(height, width) > 200:
        raise ValueError(
            f"absolute aspect ratio must be smaller than 200, got {max(height, width) / min(height, width)}"
        )

    h_bar = round(height / factor) * factor
    w_bar = round(width / factor) * factor

    if h_bar * w_bar > max_pixels:
        beta = (height * width / max_pixels) ** 0.5
        h_bar = int(height / beta / factor) * factor
        w_bar = int(width / beta / factor) * factor
    elif h_bar * w_bar < min_pixels:
        beta = (min_pixels / height / width) ** 0.5
        h_bar = int(height * beta / factor) * factor
        w_bar = int(width * beta / factor) * factor

    return h_bar, w_bar


def draw_point(image: Image.Image, coordinate: List[float], color: str = "green", radius: int = 10) -> Image.Image:
    """在图片上绘制点"""
    draw = ImageDraw.Draw(image)
    x, y = coordinate
    draw.ellipse([x - radius, y - radius, x + radius, y + radius], fill=color, outline=color)
    return image


class Executor:
    """
    AI 执行器 - 基于 VLM 视觉定位执行录制的 GUI 操作序列。

    读取 Reasoner 生成的 prompt.json，通过 VLM 在当前屏幕定位目标元素并执行操作。
    """

    def __init__(self, session_dir: str, config: Optional[ExecutorConfig] = None):
        """
        初始化执行器

        Args:
            session_dir: 会话目录路径，自动查找 prompt.json、domain.md、params.md
            config: 执行器配置
        """
        self.session_dir = session_dir
        self.config = config or ExecutorConfig()

        # 自动发现文件
        self.recording_file = os.path.join(session_dir, "prompt.json")

        # 如果 config 没有指定 domain/params，尝试从会话目录加载
        if not self.config.domain_prompt_file:
            candidate = os.path.join(session_dir, "domain.md")
            if os.path.exists(candidate):
                self.config.domain_prompt_file = candidate
        if not self.config.user_params_file:
            candidate = os.path.join(session_dir, "params.md")
            if os.path.exists(candidate):
                self.config.user_params_file = candidate

        # 录制数据
        self.recording_data: Optional[Dict] = None
        self.events: List[Dict] = []
        self.recording_info: Optional[Dict] = None

        # OpenAI 客户端
        self.client = OpenAI(
            api_key=os.getenv("QIANFAN_API_KEY", ""),
            base_url=self.config.api_base_url,
        )

        # ComputerUse 实例（用于参考，实际操作使用 pyautogui）
        self.computer_use = ComputerUse(
            cfg={
                "display_width_px": self.config.display_width,
                "display_height_px": self.config.display_height,
            }
        )

        # 执行日志
        self.execution_log: List[Dict] = []

        # 上一次事件的信息（用于非点击事件）
        self.prev_screenshot_path: Optional[str] = None
        self.prev_clicked_box_path: Optional[str] = None

        # 多轮对话历史
        self.conversation_history: List[Dict] = []
        self.wait_conversation_history: List[Dict] = []  # wait 阶段独立的对话历史
        self.system_message: Optional[Dict] = None  # 缓存系统消息

        # 临时目录
        self.temp_dir = os.path.join(session_dir, "replay_temp")
        os.makedirs(self.temp_dir, exist_ok=True)

        print(f"Executor initialized")
        print(f"  Session directory: {session_dir}")
        print(f"  prompt: {self.recording_file}")
        if self.config.domain_prompt_file:
            print(f"  Domain SOP: {self.config.domain_prompt_file}")
        if self.config.user_params_file:
            print(f"  User parameters: {self.config.user_params_file}")
        print(f"  Model: {self.config.model_id}")

    def load_recording(self) -> Dict:
        """加载录制数据"""
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

        return self.recording_data

    def take_screenshot(self) -> str:
        """
        截取当前屏幕，包含真实的鼠标光标状态
        使用 Windows API 实现
        """
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")[:-3]
        screenshot_path = os.path.join(self.temp_dir, f"screenshot_{timestamp}.png")

        try:
            import win32api
            import win32con
            import win32gui
            import win32ui

            # 获取屏幕尺寸
            width = win32api.GetSystemMetrics(win32con.SM_CXVIRTUALSCREEN)
            height = win32api.GetSystemMetrics(win32con.SM_CYVIRTUALSCREEN)
            left = win32api.GetSystemMetrics(win32con.SM_XVIRTUALSCREEN)
            top = win32api.GetSystemMetrics(win32con.SM_YVIRTUALSCREEN)

            # 创建设备上下文
            hdesktop = win32gui.GetDesktopWindow()
            desktop_dc = win32gui.GetWindowDC(hdesktop)
            img_dc = win32ui.CreateDCFromHandle(desktop_dc)
            mem_dc = img_dc.CreateCompatibleDC()

            # 创建位图
            screenshot_bmp = win32ui.CreateBitmap()
            screenshot_bmp.CreateCompatibleBitmap(img_dc, width, height)
            mem_dc.SelectObject(screenshot_bmp)

            # 复制屏幕到内存
            mem_dc.BitBlt((0, 0), (width, height), img_dc, (left, top), win32con.SRCCOPY)

            # 绘制光标
            try:
                cursor_info = win32gui.GetCursorInfo()
                if cursor_info[0]:  # 光标可见
                    hcursor = cursor_info[1]
                    cursor_x, cursor_y = cursor_info[2]

                    # 获取光标热点偏移
                    icon_info = win32gui.GetIconInfo(hcursor)
                    hotspot_x = icon_info[1]
                    hotspot_y = icon_info[2]

                    # 在截图上绘制光标（考虑多显示器偏移）
                    draw_x = cursor_x - left - hotspot_x
                    draw_y = cursor_y - top - hotspot_y

                    win32gui.DrawIconEx(
                        mem_dc.GetSafeHdc(),
                        draw_x,
                        draw_y,
                        hcursor,
                        0,
                        0,  # 使用默认大小
                        0,
                        None,
                        win32con.DI_NORMAL,
                    )

                    # 清理光标资源
                    if icon_info[3]:
                        win32gui.DeleteObject(icon_info[3])
                    if icon_info[4]:
                        win32gui.DeleteObject(icon_info[4])
            except Exception as e:
                print(f"⚠️ Failed to draw cursor: {e}")

            # 转换为 PIL Image
            bmpinfo = screenshot_bmp.GetInfo()
            bmpstr = screenshot_bmp.GetBitmapBits(True)
            screenshot = Image.frombuffer(
                "RGB",
                (bmpinfo["bmWidth"], bmpinfo["bmHeight"]),
                bmpstr,
                "raw",
                "BGRX",
                0,
                1,
            )

            # 清理资源
            mem_dc.DeleteDC()
            win32gui.DeleteObject(screenshot_bmp.GetHandle())
            win32gui.ReleaseDC(hdesktop, desktop_dc)

            screenshot.save(screenshot_path)

        except ImportError:
            screenshot = pyautogui.screenshot()
            screenshot.save(screenshot_path)
        except Exception as e:
            print(f"Win32 screenshot failed: {e}, falling back to pyautogui")
            screenshot = pyautogui.screenshot()
            screenshot.save(screenshot_path)

        return screenshot_path

    def _init_system_message(self) -> Dict:
        """
        初始化系统消息（包含 function 定义）

        Returns:
            系统消息字典
        """
        if self.system_message is not None:
            return self.system_message

        # 获取 ComputerUse 工具的 function 定义
        computer_use_function = {
            "name": "computer_use",
            "description": self.computer_use.description,
            "parameters": self.computer_use.parameters,
        }

        # Layer 1: Base prompt（通用规则，写死）
        prompt = (
            "你是一个智能体分析专家，可以根据操作历史和当前状态图执行一系列操作来完成任务。\n"
            "你必须严格按照要求输出以下格式：\n"
            "<think>{think}</think>\n"
            "<answer>{action}</answer>\n\n"
            "其中：\n"
            "- {think} 是对你为什么选择这个操作的简短推理说明。\n"
            "- {action} 是本次执行的具体操作指令，必须严格遵循下方定义的指令格式。\n"
            "1. 如果是要求click箭头按钮调节数值，就点击按钮而不是文本框\n"
            "2. 执行任务过程中如果有多个可选择的项目栏，请逐个查找每个项目栏，直到完成任务，一定不要在同一项目栏多次查找，从而陷入死循环。\n"
            "3. 如果是按钮，要点在按钮的中心的可点击位置。\n"
        )

        # Layer 2: Domain prompt（领域 SOP，用户可编辑文件）
        domain_prompt = self._load_file(self.config.domain_prompt_file)
        if domain_prompt:
            app_name = os.path.basename(os.path.dirname(self.session_dir))
            renumbered = self._renumber_domain_prompt(domain_prompt)
            prompt += f"\n下边是针对{app_name}应用的要求：\n{renumbered}\n"

        # Layer 3: User params prompt（用户参数，用户可编辑文件）
        user_params = self._load_file(self.config.user_params_file)
        if user_params:
            prompt += (
                f"\n每次输入检查用户要输入的内容是否在以下表格定义中，如果存在，把输入的值替换为表格中对应的值\n"
                f"{user_params}\n"
            )

        system_message = NousFnCallPrompt().preprocess_fncall_messages(
            messages=[
                Message(
                    role="system",
                    content=[ContentItem(text=prompt)],
                ),
            ],
            functions=[computer_use_function],
            lang=None,
        )
        self.system_message = {
            "role": "system",
            "content": [{"type": "text", "text": msg["text"]} for msg in system_message[0].model_dump()["content"]],
        }
        return self.system_message

    @staticmethod
    def _load_file(path: str) -> str:
        """加载可选的文本文件，不存在则返回空"""
        if path and os.path.exists(path):
            with open(path, "r", encoding="utf-8") as f:
                return f.read().strip()
        return ""

    @staticmethod
    def _renumber_domain_prompt(text: str) -> str:
        """将 domain prompt 中的编号重新从 1 开始排列"""
        import re
        lines = text.split("\n")
        result = []
        counter = 1
        for line in lines:
            new_line = re.sub(r"^\d+\.\s*", f"{counter}. ", line)
            if new_line != line:
                counter += 1
            result.append(new_line)
        return "\n".join(result)

    def _compress_history_images(self, wait: bool = False) -> None:
        """
        压缩对话历史中的图片

        Args:
            wait: 是否压缩 wait 阶段的历史
        """
        # 根据 wait 参数选择要压缩的历史记录
        history = self.wait_conversation_history if wait else self.conversation_history

        for i, msg in enumerate(history):
            if msg["role"] != "user":
                continue

            content = msg.get("content", [])
            if not isinstance(content, list):
                continue

            # 统计并替换图片
            new_content = []
            text_parts = []

            for item in content:
                if isinstance(item, dict):
                    if item.get("type") == "image_url":
                        continue
                    elif item.get("type") == "text":
                        text_parts.append(item.get("text", ""))
                        new_content.append(item)
                    else:
                        new_content.append(item)
                else:
                    new_content.append(item)
            msg["content"] = new_content

    def clear_conversation_history(self) -> None:
        """
        清除对话历史

        在开始新的重放任务或需要重置上下文时调用
        """
        self.conversation_history = []
        print("🗑️ Conversation history cleared")

    def _get_image_format(self, path: str) -> str:
        """确定图片格式的辅助函数"""
        fmt = path.lower().split(".")[-1]
        if fmt == "jpg":
            return "jpeg"
        elif fmt not in ["png", "jpeg", "webp", "gif"]:
            return "png"
        return fmt

    def query_(
        self,
        screenshot_path: str,
        user_query: str,
        clicked_box_path: str | None = None,
        wait: bool = False,
        system_message: Dict | None = None,
        functions: List[Dict] | None = None,
    ) -> tuple[list[float] | None, str | None, Image.Image | None]:
        """
        使用 VLM 进行 GUI grounding（多轮对话模式）

        Args:
            screenshot_path: 截图路径
            user_query: 用户查询/操作描述
            clicked_box_path: 上一次点击的 box 图片路径（可选）

        Returns:
            (coordinate_absolute, output_text, display_image)
        """
        # try:
        # 打开并处理图片
        input_image = Image.open(screenshot_path)
        base64_image = encode_image(screenshot_path)
        resized_height, resized_width = smart_resize(
            input_image.height,
            input_image.width,
            factor=32,
            min_pixels=self.config.min_pixels,
            max_pixels=self.config.max_pixels,
        )
        resized_height, resized_width = input_image.height, input_image.width
        # 初始化系统消息（如果尚未初始化且未提供）
        if system_message is None:
            system_message = self._init_system_message()

        img_format = self._get_image_format(screenshot_path)

        # 构建用户消息内容
        user_content = []
        # 如果有 clicked_box 图片（目标元素截图），先添加它
        if clicked_box_path and os.path.exists(clicked_box_path):
            box_base64 = encode_image(clicked_box_path)
            box_format = self._get_image_format(clicked_box_path)
            print("append clicked_box")
            user_content.append(
                {
                    "type": "image_url",
                    "image_url": {"url": f"data:image/{box_format};base64,{box_base64}"},
                }
            )

        # 添加当前截图
        user_content.append(
            {
                "type": "image_url",
                "image_url": {"url": f"data:image/{img_format};base64,{base64_image}"},
            }
        )

        # 添加查询文本
        user_content.append({"type": "text", "text": user_query})

        # 创建当前轮的用户消息
        current_user_message = {
            "role": "user",
            "content": user_content,
        }
        # self.conversation_history = []
        # 将当前消息添加到对话历史
        if wait:
            # wait 阶段使用独立的上下文，包含已有操作上下文 + wait 阶段自己的累积
            self.wait_conversation_history = [current_user_message]
            messages = [system_message] + self.wait_conversation_history
        else:
            self.conversation_history.append(current_user_message)
            messages = [system_message] + self.conversation_history
        # 调用模型
        completion = self.client.chat.completions.create(
            model=self.config.model_id,
            messages=messages,
            temperature=0.1,
        )
        if len(completion.choices) == 0 or completion.choices[0].finish_reason != "stop":
            output_text = ""
        else:
            output_text: str = completion.choices[0].message.content
        # 将助手的响应添加到对话历史
        assistant_message = {
            "role": "assistant",
            "content": output_text,
        }
        if wait:
            self.wait_conversation_history.append(assistant_message)
        else:
            self.conversation_history.append(assistant_message)

        # 压缩历史图片，只保留最新一轮的图片
        self._compress_history_images(wait)

        # 解析动作
        print("wait think ", output_text)
        try:
            tool_call_part = output_text.split("<tool_call>\n")[1].split("\n</tool_call>")[0]
            action = json.loads(tool_call_part)
            if "click" not in action["arguments"]["action"]:
                return None, output_text, None
            coordinate_relative = action["arguments"]["coordinate"]
        except (IndexError, json.JSONDecodeError, KeyError):
            return None, output_text, None
        coordinate_absolute = [
            coordinate_relative[0] / 1000 * resized_width,
            coordinate_relative[1] / 1000 * resized_height,
        ]

        # 转换为实际屏幕坐标
        screen_width, screen_height = pyautogui.size()
        scale_x = screen_width / input_image.width
        scale_y = screen_height / input_image.height

        # 从 resized 坐标转换回原始图片坐标，再转换到屏幕坐标
        original_x = coordinate_absolute[0] * (input_image.width / resized_width)
        original_y = coordinate_absolute[1] * (input_image.height / resized_height)

        screen_x = int(original_x * scale_x)
        screen_y = int(original_y * scale_y)

        # 可视化
        display_image = input_image.resize((resized_width, resized_height))
        display_image = draw_point(display_image, coordinate_absolute, color="green")

        return [screen_x, screen_y], output_text, display_image

        # except Exception as e:
        #     print(f"❌ GUI grounding failed: {e}")
        #     return None, str(e), None

    def wait(self, seconds: float) -> None:
        """等待指定时间（使用 ComputerUse 的 wait 概念）"""
        if seconds > 0:
            print(f"⏳ Waiting {seconds:.2f}s...")
            time.sleep(seconds)

    def calculate_wait_time(self, prev_event: Dict, curr_event: Dict) -> float:
        """计算两个事件之间的等待时间"""
        if not self.config.use_recorded_timing:
            return self.config.default_wait_time

        try:
            prev_time = datetime.fromisoformat(prev_event["timestamp"])
            curr_time = datetime.fromisoformat(curr_event["timestamp"])
            delta = (curr_time - prev_time).total_seconds()

            # 应用速度倍率
            wait_time = delta / self.config.speed_factor

            # 限制最大等待时间
            return min(wait_time, 10.0)
        except (KeyError, ValueError):
            return self.config.default_wait_time

    def wait_for_page_ready(self, prev_event: Dict | None, curr_event: Dict, max_attempts: int = 3) -> bool:
        """
        等待页面加载完毕

        将当前截图和上一轮操作发给 VLM，让它判断页面是否加载完毕。
        如果 VLM 输出 wait 动作，提取等待时间并等待，然后继续询问。
        如果 VLM 判断页面已加载完毕，返回 True。

        Args:
            prev_event: 上一个事件
            curr_event: 当前要执行的事件
            max_attempts: 最大尝试次数

        Returns:
            bool: 页面是否已加载完毕
        """
        # 导入优化后的 Wait Prompt 模块
        from prompts.wait_prompts import build_wait_check_prompt

        # print(f"\n🔄 检查页面加载状态...")

        # 将已有的操作上下文复制到 wait 上下文（wait 阶段能看到之前的操作历史）
        self.wait_conversation_history = list(self.conversation_history)

        for attempt in range(max_attempts):
            # 截取当前屏幕
            time.sleep(1)
            screenshot_path = self.take_screenshot()

            # 获取上一步操作的描述和 clicked_box
            prev_prompt = prev_event.get("prompt", "无上一步操作") if prev_event else "无上一步操作"
            prev_clicked_box_path = None
            if prev_event:
                prev_clicked_box_path = prev_event.get("screenshot_path")
                if prev_clicked_box_path:
                    prev_clicked_box_path = prev_clicked_box_path.replace("\\", os.sep).replace("/", os.sep)
                    if not os.path.exists(prev_clicked_box_path):
                        prev_clicked_box_path = None

            # 获取当前事件的描述
            curr_prompt = curr_event.get("prompt", "")
            cur_clicked_box_path = curr_event.get("clicked_box_path", "")

            # 使用优化后的 Prompt 模块构建查询
            user_query = build_wait_check_prompt(
                prev_action=prev_prompt,
                next_action=curr_prompt,
                include_system=True,
                include_thinking=True,
                include_verification=True,
            )
            screenshot_path = self.take_screenshot()
            try:
                # 调用 VLM 判断页面状态
                try:
                    print("wait")
                    print(screenshot_path, cur_clicked_box_path)
                    _, output_text, _ = self.query_(screenshot_path, user_query, cur_clicked_box_path, True)
                except Exception as e:
                    print(f"❌ VLM wait call failed: {e}")
                    print(screenshot_path, cur_clicked_box_path)
                    _, output_text, _ = self.query_(screenshot_path, user_query, None, True)
                    print("sucessed faill back")
                # 解析 VLM 输出
                try:
                    action = json.loads(output_text.split("<tool_call>\n")[1].split("\n</tool_call>")[0])
                    action_type = action.get("arguments", {}).get("action", "")
                    # print think
                    print("wait think: ", output_text.split("<think>")[0].split("</think>"))
                    if action_type == "wait":
                        wait_time = action.get("arguments", {}).get("time", 1.0)
                        # 限制等待时间在合理范围内
                        wait_time = min(10, min(wait_time, 10.0))
                        print(f"sleep for {wait_time:.2f}s")
                        time.sleep(wait_time)
                        continue
                    else:
                        # 页面已加载完毕，清空 wait 上下文
                        self.wait_conversation_history = []
                        return True

                except (IndexError, KeyError, json.JSONDecodeError) as e:
                    print(f"⚠️ [attempt {attempt + 1}/{max_attempts}] Failed to parse VLM output: {e}")
                    # 解析失败时，短暂等待后重试
                    time.sleep(0.5)
                    continue

            except Exception as e:
                print(f"⚠️ [attempt {attempt + 1}/{max_attempts}] VLM call failed: {e}")
                time.sleep(1)
                continue

        # 清空 wait 上下文
        self.wait_conversation_history = []
        return True

    def build_user_query(self, event: Dict) -> Tuple[str, str, Optional[str]]:
        """
        根据事件类型构建 user_query

        Args:
            event: 事件数据

        Returns:
            (user_query, screenshot_path, clicked_box_path)
        """
        event_type = event.get("event_type", "")
        prompt = event.get("prompt", "")

        # 获取录制时的 clicked_box 图片路径（用于视觉匹配）
        clicked_box_path = event.get("clicked_box_path")

        # 如果路径存在，确保它是正确的绝对路径或相对路径
        if clicked_box_path:
            # 处理 Windows 路径分隔符
            clicked_box_path = clicked_box_path.replace("\\", os.sep).replace("/", os.sep)

            # 如果路径不存在，尝试基于录制文件目录解析
            if not os.path.exists(clicked_box_path):
                recording_dir = os.path.dirname(self.recording_file)
                # 尝试从录制文件目录的父目录开始查找
                base_dir = os.path.dirname(recording_dir) if recording_dir else "."
                alternative_path = os.path.join(
                    base_dir,
                    os.path.basename(recording_dir),
                    "clicked_boxes",
                    os.path.basename(clicked_box_path),
                )
                if os.path.exists(alternative_path):
                    clicked_box_path = alternative_path
                else:
                    # 直接尝试原始路径
                    if not os.path.exists(clicked_box_path):
                        print(f"⚠️ clicked_box path not found: {clicked_box_path}")
                        clicked_box_path = None

        user_query = ""
        screenshot_path = ""

        if "click" in event_type:
            # 点击事件：使用当前截图 + clicked_box 图片 + prompt
            screenshot_path = self.take_screenshot()

            if clicked_box_path:
                # 有 clicked_box 图片，让 VLM 通过视觉匹配找到目标
                user_query: str = f"""第一张图片是需要点击的目标icon截图，请在第二张是当前屏幕的截图，根据**操作说明（给出了这次操作指导的描述）**
                与**图1**中找到相同或相似的icon并点击。可以调用click,或click+type**操作说明**：{prompt}"""
                # user_query = f"请根据以下操作说明，在屏幕上找到目标元素并点击：{prompt}"

            else:
                # 没有 clicked_box 图片，仅使用 prompt 描述
                user_query = f"请根据以下操作说明，在屏幕上找到目标元素并点击：{prompt}"

        elif event_type == "text_input":
            # 文本输入事件：使用上一次的截图和 clicked_box
            text = event.get("text", "")
            screenshot_path = self.prev_screenshot_path or self.take_screenshot()
            clicked_box_path = self.prev_clicked_box_path  # 使用上一次点击的位置作为参考
            user_query = f"用户历史输入为:{text},调用type函数，操作说明：{prompt}"
            # 与系统提示第三层一致：把 params.md 的用户参数也拼进本次输入查询
            user_params = self._load_file(self.config.user_params_file)
            if user_params:
                user_query += (
                    f"\n每次输入检查用户要输入的内容是否在以下表格定义中，如果存在，把输入的值替换为表格中对应的值\n"
                    f"{user_params}\n"
                )

        elif event_type == "keyboard":
            # 键盘事件：使用上一次的截图和 clicked_box
            key = event.get("key", "")
            screenshot_path = self.prev_screenshot_path or self.take_screenshot()
            clicked_box_path = self.prev_clicked_box_path  # 使用上一次点击的位置作为参考
            user_query = f"调用keyboard函数，操作说明：{prompt}"

        else:
            # 其他事件
            screenshot_path = self.take_screenshot()
            clicked_box_path = self.prev_clicked_box_path
            user_query = f"请执行以下操作：{prompt}"

        return user_query, screenshot_path, clicked_box_path

    def execute_click(self, event: Dict) -> bool:
        """执行点击操作"""
        try:
            # 构建查询
            user_query, screenshot_path, clicked_box_path = self.build_user_query(event)

            print(f"🔍 Query: {user_query[:100]}...")

            # 使用 VLM 定位元素（同时发送 clicked_box 图片）
            try:
                print("click")
                coordinate, output_text, _ = self.query_(screenshot_path, user_query, clicked_box_path)
            except Exception as e:
                print(f"⚠️ VLM call failed: {e}, no clicked_box path")
                coordinate, output_text, _ = self.query_(screenshot_path, user_query, None)
            if coordinate:
                x, y = coordinate
                print(f"🎯 Located at: ({x}, {y})")
                print(output_text.split("<tool_call>\n"))
                # 执行点击
                event_type = event.get("event_type", "left_click")
                if event_type == "right_click":
                    pyautogui.click(x, y, button="right")
                elif event_type == "double_click":
                    pyautogui.doubleClick(x, y)
                else:
                    pyautogui.click(x, y)

                print(f"✅ Click done: ({x}, {y})")
                # 保存可视化结果
                # if display_image:
                #     vis_path = os.path.join(self.temp_dir, f"vis_{event.get('sequence', 0):03d}.png")
                #     display_image.save(vis_path)

                # 更新上一次的截图和 clicked_box 路径
                self.prev_screenshot_path = screenshot_path
                self.prev_clicked_box_path = event.get("clicked_box_path")

                return True
            else:
                # 如果 VLM 定位失败，使用录制的坐标作为回退
                # x = event.get("x")
                # y = event.get("y")
                # if x is not None and y is not None:
                #     print(f"⚠️ VLM grounding failed, using recorded coords: ({x}, {y})")
                #     pyautogui.click(x, y)
                #     return True
                # else:
                print(f"❌ Could not locate element")
                return False

        except Exception as e:
            print(f"❌ Click failed: {e}")
            return False

    def clear_text_field(self) -> None:
        """
        清空当前焦点文本框的内容
        使用多种方法确保兼容性
        """
        print("🧹 Clearing text field...")

        # 方法1: 尝试 Ctrl+A 全选（最常用）
        pyautogui.hotkey("ctrl", "a")
        time.sleep(0.1)

        # 方法2: 使用 Home + Shift+End 全选（更可靠）
        # 先移动到开头
        pyautogui.hotkey("ctrl", "Home")
        time.sleep(0.05)
        # 然后 Shift+Ctrl+End 选中到结尾
        pyautogui.hotkey("ctrl", "shift", "End")
        time.sleep(0.05)

        # 按 Delete 删除选中内容（或者直接输入会覆盖）
        pyautogui.press("delete")
        time.sleep(0.05)

    def execute_text_input(self, event: Dict) -> bool:
        """执行文本输入操作"""
        try:
            text = event.get("text", "")
            if not text:
                return False

            # 构建查询并调用 VLM（用于记录和理解操作）
            user_query, screenshot_path, clicked_box_path = self.build_user_query(event)
            print(f"🔍 Query: {user_query[:100]}...")
            output_text = ""
            # 调用 VLM 获取操作理解（可选，用于日志记录）
            try:
                _, output_text, _ = self.query_(screenshot_path, user_query, clicked_box_path)
                print(f"📝 VLM understanding: {output_text[:100]}...")
            except Exception as e:
                print(f"⚠️ VLM call failed (continuing): {e}")

            # 先清空文本框内容
            self.clear_text_field()
            print(output_text)
            action = json.loads(output_text.split("<tool_call>\n")[1].split("\n</tool_call>")[0])
            text = action["arguments"]["text"]
            # 执行文本输入
            print(f"⌨️ Typing text: '{text}'")
            pyautogui.write(text, interval=0.05)
            # type enter
            pyautogui.press("enter")
            return True
        except Exception as e:
            print(f"❌ Text input failed: {e}")
            return False

    def execute_keyboard(self, event: Dict) -> bool:
        """执行键盘操作"""
        try:
            key = event.get("key", "")
            modifiers = event.get("modifiers")

            if not key:
                return False

            # 构建查询并调用 VLM（用于记录和理解操作）
            user_query, screenshot_path, clicked_box_path = self.build_user_query(event)
            print(f"🔍 Query: {user_query[:100]}...")
            output_text = ""
            # 调用 VLM 获取操作理解（可选，用于日志记录）
            try:
                _, output_text, _ = self.query_(screenshot_path, user_query, clicked_box_path)
                print(f"📝 VLM understanding: {output_text[:100]}...")
            except Exception as e:
                print(f"⚠️ VLM call failed (continuing): {e}")

            # 执行键盘操作
            if modifiers:
                # 组合键
                hotkey = modifiers + [key]
                print(f"⌨️ Pressing hotkey: {'+'.join(hotkey)}")
                pyautogui.hotkey(*hotkey)
            else:
                # 单个按键
                print(f"⌨️ Pressing key: {key}")
                pyautogui.press(key)

            return True
        except Exception as e:
            print(f"❌ Keyboard action failed: {e}")
            return False

    def execute_event(self, event: Dict) -> bool:
        """执行单个事件"""
        event_type = event.get("event_type", "")
        sequence = event.get("sequence", 0)

        print(f"\n{'=' * 50}")
        print(f"📍 Executing event #{sequence}: {event_type}")
        print(f"   Description: {event.get('prompt', 'N/A')[:80]}...")

        if event_type in ["left_click", "right_click", "double_click"]:
            return self.execute_click(event)
        elif event_type == "text_input":
            return self.execute_text_input(event)
        elif event_type == "keyboard":
            return self.execute_keyboard(event)
        else:
            print(f"⚠️ Unknown event type: {event_type}")
            return False

    def observe_page(self, event: Dict | None, next_event: Optional[Dict] = None) -> None:
        """
        观察页面状态，验证当前步骤是否完成
        """
        import re

        # 如果没有上一个事件（即这是第一个事件），跳过验证
        if next_event is None:
            print("No previous event, skipping verification")
            return

        time.sleep(1)
        print(f"👀 Verifying step completion...")
        # current_screenshot_path = self.take_screenshot()
        current_screenshot_path = pyautogui.screenshot()
        current_screenshot_path.save("current_screenshot.png")
        current_screenshot_path = "current_screenshot.png"
        # 获取录制时的截图作为预期状态（优先使用下一个事件的 screenshot_path，即当前步骤的操作结果）
        target_event = event
        recording_screenshot_path = target_event.get("screenshot_path")

        print(f"🐛 Debug: Target Event Sequence: {target_event.get('sequence')}")
        print(f"🐛 Debug: Raw Screenshot Path: {recording_screenshot_path}")

        if recording_screenshot_path:
            recording_screenshot_path = recording_screenshot_path.replace("\\", os.sep).replace("/", os.sep)
            if not os.path.exists(recording_screenshot_path):
                recording_dir = os.path.dirname(self.recording_file)
                # 尝试相对路径
                p1 = os.path.join(recording_dir, recording_screenshot_path)
                # 尝试仅文件名
                p2 = os.path.join(recording_dir, os.path.basename(recording_screenshot_path))
                # 尝试 screenshots 目录
                p3 = os.path.join(recording_dir, "screenshots", os.path.basename(recording_screenshot_path))

                if os.path.exists(p1):
                    recording_screenshot_path = p1
                elif os.path.exists(p2):
                    recording_screenshot_path = p2
                elif os.path.exists(p3):
                    recording_screenshot_path = p3
                else:
                    recording_screenshot_path = None
        next_prmpt = event.get("prompt", "")
        if next_event:
            print("Has next event")
            prompt = next_event.get("prompt", "")
        else:
            print("No next event")
            return
        print(prompt)
        user_query = f"""
上一步操作：{prompt}
上一步操作后得到了图2，图1是上一步操作后应该得到的结果。
图1:预期状态的参考截图。
图2:当前操作后的屏幕截图。
上一步操作后得到了图2，图2和图1显示基本一致，说明执行成功。
请对比两张图，判断当前步骤是否已成功完成。
**输出格式**:
1. 请先在 <thinking></thinking> 标签中输出你的观察和推理过程。
2. 如果完成或无法判定失败，请回复：<answer>continue</answer>
3. 如果没有完成（例如：界面没有预期变化、出现错误提示、或者状态明显不一致），请回复：<answer>ask_user("具体问题描述")</answer>
4. **禁止输出其他动作**
"""

        # 用户参数（从 params.md 读取，替代硬编码 JSON）
        user_params = self._load_file(self.config.user_params_file)
        params_section = ""
        if user_params:
            params_section = (
                "\n---\n"
                "每次输入检查用户要输入的内容是否在以下json定义中，如果存在，如果上一步操作的值和以下json中定义不同, 以json中为准,一样则通过\n"
                f"{user_params}\n"
            )

        # 构造验证专用系统提示词
        verification_system_content = (
            r"""你是一个GUI操作验证专家。你的任务是严格对比两张图片（图1：预期正确结果，图2：当前实际结果），判断操作是否成功。

## 核心原则
1. **【强制规则】如果图2在关键特征上与图1一致，则认为验证通过，此时必须忽略上一步步骤的操作执行结果**。
2. **忽略次要差异**：**忽略时间、光标位置、动态数值、背景变化、项目名、文件名或配置空间名等非关键差异**。
3. **当前运行的项目可能不同**：忽略项目名，站点名，气象文件等文本框和标题的差异
4. 点击复制到剪切板后，下拉菜单会消失，应该判定为<answer>continue</answer>。
5. 当操作为关闭但是图1图2还是有关闭按钮时，思考是不是新界面也有关闭按钮，判定为<answer>continue</answer>。
6. **【强制规则】下拉框/列表选择差异必须忽略**：
   - ⚠️ **如果在进行**点击**下拉框选择或列表操作，且图2中显示的选中项与图1不一致（例如选中了不同的文字），必须直接判定为 `<answer>continue</answer>`。**
   - 认为这是中间状态，具体的数值验证留给后续步骤。
   - ⚠️当进行**输入**操作后下拉框收起时，如果验证不一致判定失败<answer>ask_user</answer>.
   - ⚠️当判断点击操作时:输入框被正确选中时，通常会时高亮一点，如果图2中被正确选中，判定失败<answer>ask_user</answer>.
   - 图2中的内容不一定与图1完全一致，只要和**上一步步骤的操作**符合即可
   - ⚠️**如果图1图2在UI结构和内容上完全相同，就应该判定通过，忽略操作内容失败<answer>continue</answer>**
   - ⚠️**当图1和图2的项目不同时，或配置空间不同，如果没有其他异常，判定为通过**
   - 点击调整按钮没有变化，判定为<answer>continue</answer>。
## 特别注意
- **如果下拉框或文本框与**上一步步骤的操作**不一致，视为“失败”**。
- 如果你无法确定，或者差异看起来微不足道，请优先回复 <answer>continue</answer> 以避免不必要的打断。
- 只有在确认**必然失败**（如明确的错误提示、界面显著差异）时才请求用户帮助。"""
            + params_section
            + r"""
---
## 输出格式
你必须严格遵守以下 XML 输出格式：

<thinking>
这里写下你的观察和推理过程：
1. 观察图1（预期状态）：描述关键UI特征（例如“无弹窗”、“显示了XX页面”）。
2. 观察图2（当前状态）：描述关键UI特征。
3. 对比差异：分析两者在功能层面上是否一致。忽略像素级差异。如果图1图2在UI结构和内容上完全相同，必须判定通过，忽略操作失败，如果界面显然不同，判定失败。
4. 结论：判断是否通过。
</thinking>

<answer>continue</answer> 或 <answer>ask_user("原因")</answer>


"""
        )

        verification_system_message = {
            "role": "system",
            "content": [{"type": "text", "text": verification_system_content}],
        }

        # 仅 VLM 调用这一段出错才忽略；用户的 stop 不在此 try 内，避免被吞掉
        try:
            # 图1: recording_screenshot_path (clicked_box_path arg)
            # 图2: current_screenshot_path (screenshot_path arg)
            # 使用 wait=True 避免污染主对话历史
            # 不传递 functions，防止模型调用工具

            _, output_text, _ = self.query_(
                screenshot_path=current_screenshot_path,
                user_query=user_query,
                clicked_box_path=recording_screenshot_path,
                wait=True,
                system_message=verification_system_message,
                functions=[],  # 禁用工具
            )
        except Exception as e:
            print(f"⚠️ Verification error (ignored): {e}")
            return

        print(f"🤔 Verification analysis: {output_text}")

        if "<answer>continue</answer>" in output_text:
            print("✅ Verification passed")
            return

        match = re.search(r'ask_user\("(.*?)"\)', output_text)
        reason = match.group(1) if match else "The step appears incomplete"
        print(f"❌ Verification failed: {reason}")

        # 在消息窗口中请求用户决定：继续 / 重试 / 停止
        action = ask_user_action("AI Execute - Verification Alert", reason)
        if action == "stop":
            raise UserAbortReplay("User stopped AI Execute")
        if action == "retry":
            raise RetryExecution("User chose to retry; restarting AI Execute from the beginning")
        # action == "continue" → 继续执行当前步骤

    def run(
        self,
        start_from: int = 0,
        stop_at: Optional[int] = None,
    ) -> ExecutionResult:
        """
        执行重放

        Args:
            start_from: 从第几个事件开始（序列号）
            stop_at: 在第几个事件停止（序列号）

        Returns:
            ExecutionResult: 重放结果
        """
        if not self.recording_data:
            self.load_recording()

        # 开始新的重放任务时，清除之前的对话历史
        self.clear_conversation_history()

        result = ExecutionResult(total_events=len(self.events))

        start_time = time.time()

        print(f"\n{'=' * 60}")
        print(f"🎬 Starting replay")
        print(f"{'=' * 60}")
        print(f"Total events: {len(self.events)}")
        print(f"Start event: {start_from}")
        print(f"Stop event: {stop_at or 'end'}")

        # 筛选要执行的事件
        events_to_execute = []
        for event in self.events:
            seq = event.get("sequence", 0)
            if seq >= start_from:
                if stop_at is None or seq <= stop_at:
                    events_to_execute.append(event)

        total = len(events_to_execute)
        print(f"Will execute {total} events\n")
        toast("AI Execute started", f"{total} steps total")

        # 初始等待
        print("⏳ Getting ready, starting in 1s...")
        time.sleep(1)

        aborted = False
        while True:
            restart = False
            # 每次（重新）开始：重置执行状态与计时，从头执行
            self.clear_conversation_history()
            self.execution_log = []
            result.executed_events = 0
            result.failed_events = []
            result.execution_log = []
            result.error_message = ""
            start_time = time.time()
            prev_event = None

            for i, event in enumerate(events_to_execute):
                # 验证上一步骤是否完成：使用 prev_event 的 prompt 和当前 event (即上一步的 next) 的 snapshot
                try:
                    self.observe_page(event, prev_event)
                except UserAbortReplay as e:
                    print(f"⏹️ {e}")
                    result.error_message = str(e)
                    toast("AI Execute stopped", str(e))
                    aborted = True
                    break
                except RetryExecution as e:
                    print(f"🔁 {e}")
                    toast("AI Execute - retry", str(e))
                    restart = True
                    break

                # 单步执行模式
                if self.config.step_by_step:
                    input(f"Press Enter to execute event #{event.get('sequence', i)}...")
                time.sleep(1)
                # 执行事件
                success = self.execute_event(event)

                # 通知进度
                step_num = i + 1
                if success:
                    toast("AI Execute", f"Step {step_num}/{total} done")
                else:
                    toast("AI Execute - failed", f"Step {step_num}/{total} failed")

                # 记录结果
                log_entry = {
                    "sequence": event.get("sequence", i),
                    "event_type": event.get("event_type"),
                    "success": success,
                    "timestamp": datetime.now().isoformat(),
                }
                self.execution_log.append(log_entry)
                result.execution_log.append(log_entry)

                if success:
                    result.executed_events += 1
                else:
                    result.failed_events.append(
                        {
                            "sequence": event.get("sequence", i),
                            "event": event,
                            "error": "Execution failed",
                        }
                    )

                prev_event = event

            if restart:
                # 用户选择重试：从头重新执行
                continue
            break

        result.duration = time.time() - start_time
        result.success = (not aborted) and len(result.failed_events) == 0

        print(f"\n{'=' * 60}")
        print(f"🏁 Replay complete")
        print(f"{'=' * 60}")
        print(f"Total time: {result.duration:.2f}s")
        print(f"Succeeded: {result.executed_events}/{result.total_events}")
        print(f"Failed: {len(result.failed_events)}")

        if result.success:
            toast("AI Execute complete", f"All {result.executed_events} steps succeeded in {result.duration:.1f}s")
        else:
            toast("AI Execute complete (with failures)", f"Succeeded {result.executed_events}/{result.total_events}, failed {len(result.failed_events)}")

        return result

    def save_log(self, output_path: str) -> None:
        """保存执行日志"""
        with open(output_path, "w", encoding="utf-8") as f:
            json.dump(
                {
                    "recording_file": self.recording_file,
                    "execution_log": self.execution_log,
                    "timestamp": datetime.now().isoformat(),
                },
                f,
                indent=2,
                ensure_ascii=False,
            )
        print(f"💾 Log saved: {output_path}")


def main():
    """主函数"""
    import argparse

    parser = argparse.ArgumentParser(description="Executor - AI 执行器")
    parser.add_argument("session_dir", type=str, help="会话目录路径")
    parser.add_argument("--start", type=int, default=0, help="起始事件序列号")
    parser.add_argument("--stop", type=int, default=None, help="结束事件序列号")
    parser.add_argument("--speed", type=float, default=1.0, help="速度倍率")
    parser.add_argument("--step", action="store_true", help="单步执行模式")
    parser.add_argument("--no-timing", action="store_true", help="不使用录制的时间间隔")
    parser.add_argument("--domain", type=str, default="", help="领域 SOP 文件路径")
    parser.add_argument("--params", type=str, default="", help="用户参数文件路径")

    args = parser.parse_args()

    config = ExecutorConfig(
        speed_factor=args.speed,
        step_by_step=args.step,
        use_recorded_timing=not args.no_timing,
        domain_prompt_file=args.domain,
        user_params_file=args.params,
    )

    replay = Executor(args.session_dir, config)

    # 执行重放
    result = replay.run(
        start_from=args.start,
        stop_at=args.stop,
    )

    # 保存日志
    log_dir = os.path.join(args.session_dir, "replay_logs")
    os.makedirs(log_dir, exist_ok=True)
    log_path = os.path.join(log_dir, f"replay_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json")
    replay.save_log(log_path)

    return result


if __name__ == "__main__":
    main()
