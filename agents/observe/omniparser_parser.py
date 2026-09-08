#!/usr/bin/env python
# coding=utf-8
"""
Omniparser Data Parser

This module provides functions to parse the text format returned by omniparser.
"""

import re
from typing import Any, Dict, List


def parse_omniparser_box_data(box_data_text: str) -> List[Dict[str, Any]]:
    """
    Parse the box data text returned by omniparser.

    Format: "icon 0: {'type': 'text' 'bbox': [x1, y1, x2, y2]
    'interactivity': False 'content': '文本' 'source': 'box_ocr_content_ocr'}"

    Args:
        box_data_text: The text data returned by omniparser

    Returns:
        List of parsed box dictionaries
    """
    boxes = []

    try:
        # Split by lines and parse each box entry
        lines = box_data_text.split("\n")

        for line in lines:
            line = line.strip()
            if not line:
                continue

            # Parse format: "icon 1: {'type': 'text' 'bbox': [x1, y1, x2, y2] ...}"
            if ":" in line and "bbox" in line:
                try:
                    # Extract the dictionary part after the colon
                    dict_start = line.find("{")
                    dict_end = line.rfind("}") + 1

                    if dict_start != -1 and dict_end != -1:
                        dict_str = line[dict_start:dict_end]

                        # Parse using simple string operations
                        box_dict = parse_simple_dict(dict_str)

                        if (
                            box_dict
                            and "bbox" in box_dict
                            and len(box_dict["bbox"]) == 4
                        ):
                            boxes.append(box_dict)

                except Exception as e:
                    print(f"Error parsing box line: {line}, error: {e}")
                    continue

    except Exception as e:
        print(f"Error parsing box data text: {e}")

    print(f"?? Parsed {len(boxes)} boxes from text data")
    return boxes


def parse_simple_dict(dict_str: str) -> Dict[str, Any]:
    """
    Parse the dictionary string using simple string operations.

    Args:
        dict_str: Dictionary string like "{'type': 'text' 'bbox': [x1, y1, x2, y2] ...}"

    Returns:
        Parsed dictionary
    """
    try:
        # Remove braces
        content = dict_str.strip("{}")
        box_dict = {}

        # Use regex to extract key-value pairs more accurately
        # Pattern: 'key': followed by either a bbox array or a string value
        pattern = r"'([^']+)':\s*(\[[^\]]*\]|'[^']*'|[^'\s]+)"
        matches = re.findall(pattern, content)

        for key, value in matches:
            # Handle bbox values specially
            if key == "bbox":
                # Parse bbox as list of floats
                if value.startswith("[") and value.endswith("]"):
                    bbox_str = value.strip("[]")
                    # Split by spaces and commas, then convert to floats
                    # Handle both space-separated and comma-separated values
                    bbox_values = []
                    for x in bbox_str.replace(",", " ").split():
                        try:
                            bbox_values.append(float(x))
                        except ValueError:
                            continue
                    if len(bbox_values) >= 4:
                        box_dict[key] = bbox_values[:4]
                else:
                    print(f"Warning: Invalid bbox format: {value}")
            else:
                # Handle other values
                value = value.strip()
                if value.startswith("'") and value.endswith("'"):
                    value = value[1:-1]  # Remove quotes

                if value.lower() == "true":
                    box_dict[key] = True
                elif value.lower() == "false":
                    box_dict[key] = False
                else:
                    # Try to convert to number, otherwise keep as string
                    try:
                        box_dict[key] = float(value)
                    except ValueError:
                        box_dict[key] = value

        return box_dict

    except Exception as e:
        print(f"Error parsing simple dict: {e}")
        return {}
