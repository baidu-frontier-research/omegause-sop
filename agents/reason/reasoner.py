#!/usr/bin/env python
# coding=utf-8
"""
Reasoner - AI 推理模块

读取录制 JSON，通过 VLM 分析截图为每个事件生成 prompt 描述，
输出 prompt.json 供 execute 模块使用。

这是 observe -> reason -> execute 流水线的第二步。
"""

import base64
import json
import os
import time
from dataclasses import dataclass
from typing import Dict, List, Optional

from dotenv import load_dotenv
from openai import OpenAI
from PIL import Image
from tqdm import tqdm

load_dotenv()


@dataclass
class ReasonerConfig:
    """推理模块配置"""

    model_id: str = os.environ.get("REASON_MODEL_NAME", os.environ.get("ENRICHER_MODEL_NAME", "qwen3-vl-235b-a22b-instruct"))
    api_base_url: str = os.environ.get("REASON_API_URL", os.environ.get("ENRICHER_API_URL", "https://qianfan.baidubce.com/v2"))
    api_key: str = os.environ.get("QIANFAN_API_KEY", "")
    temperature: float = 0.1
    request_interval: float = 3.0


def _encode_image(image_path: str) -> str:
    with open(image_path, "rb") as f:
        return base64.b64encode(f.read()).decode("utf-8")


class Reasoner:
    """
    AI 推理器

    对齐 computer_use.ipynb 的原始逻辑：
    - system prompt: "You are a GUI understanding agent."
    - 多轮对话上下文（历史只保留文本，不保留图片）
    - click 事件: 发送截图 + clicked_box + 下一步截图
    - text_input / keyboard 事件: 使用上一步截图
    """

    def __init__(self, config: Optional[ReasonerConfig] = None):
        self.config = config or ReasonerConfig()
        self.client = OpenAI(
            api_key=self.config.api_key,
            base_url=self.config.api_base_url,
        )
        self.conversation_history: List[Dict] = []

    def enrich(self, session_dir: str) -> str:
        """
        为会话目录中的录制文件生成 prompt 描述。

        读取 {session_dir}/recording.json，输出 {session_dir}/prompt.json

        Args:
            session_dir: 会话目录路径

        Returns:
            输出文件路径
        """
        recording_file = os.path.join(session_dir, "recording.json")
        output_file = os.path.join(session_dir, "prompt.json")

        print(f"Loading recording file: {recording_file}")
        with open(recording_file, "r", encoding="utf-8") as f:
            recordings = json.load(f)

        events = recordings.get("events", [])
        if not events:
            print("No events in the recording file")
            return ""

        print(f"{len(events)} events total, generating action descriptions...")
        self.conversation_history = []

        prev_screenshot = None

        for i, event in enumerate(tqdm(events, desc="Generating descriptions")):
            if event.get("prompt") and not event["prompt"].startswith("[生成失败]"):
                if "screenshot_path" in event:
                    prev_screenshot = event["screenshot_path"]
                continue

            try:
                event["prompt"] = self._process_event(event, events, i, prev_screenshot)
            except Exception as e:
                print(f"\n  Event #{event.get('sequence', i)} failed: {e}")
                event["prompt"] = f"[生成失败] {event.get('event_type', 'unknown')}"

            if "screenshot_path" in event:
                prev_screenshot = event["screenshot_path"]

            if i < len(events) - 1:
                time.sleep(self.config.request_interval)

        with open(output_file, "w", encoding="utf-8") as f:
            json.dump(recordings, f, ensure_ascii=False, indent=4)

        print(f"Done! Saved to: {output_file}")
        return output_file

    def _process_event(
        self,
        event: Dict,
        all_events: List[Dict],
        index: int,
        prev_screenshot: Optional[str],
    ) -> str:
        """处理单个事件，对齐 notebook cell 66f612c6 的逻辑"""
        event_type = event.get("event_type", "")

        if "click" not in event_type:
            # text_input / keyboard 事件
            if "text" in event:
                user_query = f'用户输入了{event["text"]},需要调用computeruse的type功能,要求:简要介绍这次点击的对象，给出用于下次复现该操作的提示'
            else:
                user_query = f'用户输入了{event.get("key", "")},需要调用computeruse的type功能,要求:简要介绍这次点击的对象，给出用于下次复现该操作的提示'
            screenshot = prev_screenshot
            return self._call_vlm(screenshot, user_query)

        # click 事件
        screenshot = event.get("screenshot_path")
        clickbox = event.get("clicked_box_path")

        # 获取下一个事件的截图
        nextscreen = None
        if index + 1 < len(all_events) and "screenshot_path" in all_events[index + 1]:
            nextscreen = all_events[index + 1]["screenshot_path"]

        # 计算归一化坐标
        input_image = Image.open(screenshot)
        x = int(event["x"] / input_image.width * 1000)
        y = int(event["y"] / input_image.height * 1000)

        # 主查询（带 clickbox + nextscreen）
        try:
            user_query = f'用户在图1屏幕截图中点击了坐标是{(x, y)}的对象，如图2所示，图3是点击后的屏幕截图。要求:根据按钮信息及相对位置给出用于下次复现该操作的明确提示,按照<hint></hint>输出'
            return self._call_vlm(screenshot, user_query, clickbox=clickbox, nextscreen=nextscreen)
        except Exception:
            # fallback: 不带 clickbox
            user_query = f'用户在图1屏幕截图中点击了坐标是{(x, y)}的对象,图2是点击后的预期画面。要求:根据按钮信息及相对位置给出用于下次复现该操作的明确提示，按照<hint></hint>输出'
            return self._call_vlm(screenshot, user_query, clickbox=None, nextscreen=nextscreen)

    def _call_vlm(
        self,
        screenshot_path: Optional[str],
        user_query: str,
        clickbox: Optional[str] = None,
        nextscreen: Optional[str] = None,
    ) -> str:
        """
        调用 VLM，保持多轮对话上下文。

        对齐 notebook cell 9ee15e3d 的逻辑：
        - system: "You are a GUI understanding agent."
        - 当前轮带图片，历史轮只保留文本
        """
        system_message = {
            "role": "system",
            "content": [{"type": "text", "text": "You are a GUI understanding agent."}],
        }

        # 构建当前轮用户消息（带图片）
        user_content = [{"type": "text", "text": user_query}]

        if screenshot_path and os.path.exists(screenshot_path):
            user_content.append({
                "type": "image_url",
                "image_url": {"url": f"data:image/png;base64,{_encode_image(screenshot_path)}"},
            })

        if clickbox and os.path.exists(clickbox):
            user_content.append({
                "type": "image_url",
                "image_url": {"url": f"data:image/png;base64,{_encode_image(clickbox)}"},
            })

        if nextscreen and os.path.exists(nextscreen):
            user_content.append({
                "type": "image_url",
                "image_url": {"url": f"data:image/png;base64,{_encode_image(nextscreen)}"},
            })

        current_user_message = {"role": "user", "content": user_content}

        # system + 历史（纯文本）+ 当前（带图片）
        messages = [system_message] + self.conversation_history + [current_user_message]

        completion = self.client.chat.completions.create(
            model=self.config.model_id,
            messages=messages,
            temperature=self.config.temperature,
        )
        output_text = completion.choices[0].message.content

        # 历史只保留文本，不保留图片
        self.conversation_history.append({"role": "user", "content": user_query})
        self.conversation_history.append({"role": "assistant", "content": output_text})

        return output_text


def main():
    import argparse

    parser = argparse.ArgumentParser(description="Reasoner - AI 推理分析录制操作")
    parser.add_argument("session_dir", type=str, help="会话目录路径（包含 recording.json）")
    parser.add_argument("--model", type=str, default=None, help="VLM 模型名称")
    parser.add_argument("--api-url", type=str, default=None, help="VLM API 地址")
    parser.add_argument("--interval", type=float, default=3.0, help="请求间隔（秒）")

    args = parser.parse_args()

    config = ReasonerConfig(request_interval=args.interval)
    if args.model:
        config.model_id = args.model
    if args.api_url:
        config.api_base_url = args.api_url

    reasoner = Reasoner(config)
    reasoner.enrich(args.session_dir)


if __name__ == "__main__":
    main()
