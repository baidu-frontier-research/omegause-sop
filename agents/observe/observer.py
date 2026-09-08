#!/usr/bin/env python
# coding=utf-8
"""
Observer - AI 观察模块

录制用户的鼠标和键盘操作，截取屏幕截图，
通过 OmniParser 解析点击区域，保存操作序列。

这是 observe -> reason -> execute 流水线的第一步。
"""

import json
import os
import threading
import time
from dataclasses import asdict, dataclass
from datetime import datetime
from queue import Queue
from typing import Any, Dict, List, Optional

import keyboard
import pyautogui
from gradio_client import Client, handle_file
from PIL import Image
from pynput import mouse

try:
    from .omniparser_parser import parse_omniparser_box_data
except ImportError:
    from omniparser_parser import parse_omniparser_box_data


@dataclass
class Event:
    """Base class for all recorded events."""

    timestamp: str
    event_type: str
    sequence: int  # Event sequence number for ordering


@dataclass
class MouseEvent(Event):
    """Represents a single mouse event with associated data."""

    x: int
    y: int
    screenshot_path: str
    clicked_box_path: Optional[str] = None
    box_coordinates: Optional[Dict[str, int]] = None
    omniparser_result: Optional[Dict[str, Any]] = None


@dataclass
class KeyboardEvent(Event):
    """Represents a single keyboard event."""

    key: str
    event_subtype: str  # 'key_press', 'key_release'
    is_special_key: bool = False
    modifiers: Optional[List[str]] = None


@dataclass
class TextInputEvent(Event):
    """Represents a complete text input session."""

    text: str  # The complete input text
    key_count: int  # Number of keys pressed
    duration: float  # Duration of input in seconds
    start_timestamp: str  # When input started
    end_timestamp: str  # When input ended


class Observer:
    """
    AI 观察器 - 录制鼠标和键盘操作，截图并解析点击区域。

    支持会话管理，可直接通过 start_session / stop_session 使用。
    """

    def __init__(self, output_dir: str = "recordings"):
        """
        Initialize the event recorder.

        Args:
            output_dir: Directory to save recordings and screenshots
        """
        self.output_dir = output_dir
        self.events: List[Event] = []  # Now supports both MouseEvent and KeyboardEvent
        self.recording_thread: Optional[threading.Thread] = None
        self.screenshot_dir = os.path.join(output_dir, "screenshots")
        self.clicked_boxes_dir = os.path.join(output_dir, "clicked_boxes")

        # Track active key presses to avoid duplicate recordings
        self.active_keys: set = set()

        # Event for recording state control
        # When clear (False): recording is active
        # When set (True): recording should stop
        self._stop_event = threading.Event()
        self._stop_event.clear()
        # Text input aggregation
        self._input_buffer: List[str] = []  # Buffer for collecting text input
        self._input_start_time: Optional[float] = None
        self._input_start_timestamp: Optional[str] = None
        self._input_sequence: Optional[int] = None
        self._input_timer: Optional[threading.Timer] = None
        self._input_timeout = 2.0  # 2 seconds of inactivity triggers save
        self._last_key_time: Optional[float] = None

        # Event sequencing and locking for thread safety
        self._event_sequence = 0
        self._events_lock = threading.Lock()

        # Event processing queue for asynchronous handling
        self._event_queue = Queue()
        self._processing_thread = None
        self._should_process_events = False

        # Mouse hook listener
        self._mouse_listener: Optional[mouse.Listener] = None
        self._screenshot_lock = threading.Lock()  # Prevent concurrent screenshots

        # Double click detection
        self._last_click_time = 0.0
        self._last_click_pos = None
        self._double_click_threshold = 0.5  # seconds
        self._click_distance_threshold = 20  # pixels

        # Create directories
        os.makedirs(self.screenshot_dir, exist_ok=True)
        self.clicked_boxes_dir = os.path.join(output_dir, "clicked_boxes")
        os.makedirs(self.clicked_boxes_dir, exist_ok=True)

        # Disable pyautogui failsafe
        pyautogui.FAILSAFE = False

    def start_recording(self) -> None:
        """Start recording mouse events."""
        self.events = []
        self._stop_event.clear()  # Clear = start recording

        # Start event processing thread
        self._start_event_processing()

        # Start recording in a separate thread
        self.recording_thread = threading.Thread(target=self._record_events)
        self.recording_thread.daemon = True
        self.recording_thread.start()

        print("🎥 Mouse event recording started")
        print("Press 'Ctrl+Alt+R' to stop recording")

    def _start_event_processing(self) -> None:
        """Start the asynchronous event processing thread."""
        self._should_process_events = True
        self._processing_thread = threading.Thread(target=self._process_events_worker)
        self._processing_thread.daemon = True
        self._processing_thread.start()
        print("🔧 Asynchronous event processing started")

    def _process_events_worker(self) -> None:
        """Worker thread that processes events from the queue asynchronously."""
        from queue import Empty

        while self._should_process_events or not self._event_queue.empty():
            try:
                event_package = self._event_queue.get(timeout=1.0)
                event, screenshot_path, sequence, timestamp = event_package

                print(f"🔧 Processing event [Seq: {sequence}] asynchronously...")

                # Process with omniparser asynchronously
                self._process_with_omniparser_async(event, screenshot_path)

                # Mark task as done
                self._event_queue.task_done()

            except Empty:
                continue
            except Exception as e:
                print(f"❌ Error in event processing worker: {e}")

    def _process_with_omniparser_async(self, mouse_event: MouseEvent, screenshot_path: str) -> None:
        """Asynchronously process the screenshot with omniparser."""
        try:
            # This is the original synchronous processing method, now called asynchronously
            self._process_with_omniparser(mouse_event)
            print(f"✅ Omnoparser async processing completed for [Seq: {mouse_event.sequence}]")
        except Exception as e:
            print(f"❌ Error in async omniparser processing for [Seq: {mouse_event.sequence}]: {e}")

    def _stop_event_processing(self) -> None:
        """Stop the asynchronous event processing."""
        if self._should_process_events:
            self._should_process_events = False
            print("🛑 Stopping asynchronous event processing...")

            # Wait for processing thread to finish
            if self._processing_thread and self._processing_thread.is_alive():
                # Wait for queue to be processed
                self._event_queue.join()
                self._processing_thread.join(timeout=5.0)
                print("✅ Asynchronous event processing stopped")

            print(f"📊 Queue stats: {self._event_queue.qsize()} events remaining")

    def _signal_stop(self) -> None:
        """仅设置停止信号，不做保存（由 stop_session 统一处理）"""
        self._stop_event.set()

    def stop_recording(self) -> None:
        """Stop recording mouse events."""
        if self._stop_event.is_set():
            print("No recording in progress")
            return

        # Save any pending text input before stopping
        self._save_text_input()

        # Signal the recording thread to stop
        self._stop_event.set()

        # Stop event processing first
        self._stop_event_processing()

        if self.recording_thread and self.recording_thread.is_alive():
            self.recording_thread.join(timeout=2)

        print("🛑 Mouse event recording stopped")

        # 自动保存录制结果
        self.save_recording()

    def _record_events(self) -> None:
        """Main recording loop that listens for both mouse and keyboard events."""
        # Set up keyboard listener for stopping recording
        # 只设置 stop event，让调用方（cli）负责调用 stop_session
        keyboard.add_hotkey("ctrl+alt+r", self._signal_stop)

        # Set up global keyboard event listener
        keyboard.hook(self._handle_keyboard_event)

        # Start mouse hook listener
        self._start_mouse_listener()

        try:
            self._stop_event.wait()
        except Exception as e:
            print(f"Error during recording: {e}")
        finally:
            keyboard.unhook_all()
            self._stop_mouse_listener()

    def _start_mouse_listener(self) -> None:
        """Start the mouse hook listener."""
        try:
            print("listen")
            # Create and start the mouse listener
            self._mouse_listener = mouse.Listener(on_click=self._on_mouse_click)
            self._mouse_listener.start()
            print("🖱️  Mouse hook listener started")
        except Exception as e:
            print(f"❌ Error starting mouse listener: {e}")

    def _stop_mouse_listener(self) -> None:
        """Stop the mouse hook listener."""
        if self._mouse_listener:
            try:
                self._mouse_listener.stop()
                self._mouse_listener = None
                print("🛑 Mouse hook listener stopped")
            except Exception as e:
                print(f"❌ Error stopping mouse listener: {e}")

    def _on_mouse_click(self, x: int, y: int, button, pressed: bool) -> None:
        """
        Mouse hook callback - called BEFORE the actual click is processed.

        Args:
            x: X coordinate of the click
            y: Y coordinate of the click
            button: Mouse button (Button.left, Button.right, Button.middle)
            pressed: True if button was pressed, False if released
        """
        if self._stop_event.is_set():
            return

        # Only process button press events (not release)
        if not pressed:
            return

        try:
            # Use lock to prevent concurrent screenshots
            with self._screenshot_lock:
                # Determine click type
                if button == mouse.Button.left:
                    event_type = "left_click"
                elif button == mouse.Button.right:
                    event_type = "right_click"
                elif button == mouse.Button.middle:
                    event_type = "middle_click"
                else:
                    event_type = "unknown_click"

                print(f"🎯 Mouse hook intercepted: {event_type} at ({x}, {y})")

                # Capture screenshot BEFORE the click is processed by the system
                # This is the key feature - we intercept and screenshot first
                self._handle_mouse_click_at_position(x, y, event_type)

        except Exception as e:
            print(f"❌ Error in mouse hook callback: {e}")

        # Return True to allow the event to propagate (not blocking the click)
        # If we return False, the click would be blocked
        return True

    def _handle_mouse_click(self) -> None:
        """Handle a mouse click event."""
        try:
            # Get current mouse position
            x, y = pyautogui.position()
            self._handle_mouse_click_at_position(x, y)
        except Exception as e:
            print(f"Error handling mouse click: {e}")

    def _handle_mouse_click_at_position(self, x: int, y: int, event_type: str = "left_click") -> None:
        """Handle a mouse click at a specific position."""
        try:
            current_time = time.time()
            is_double_click = False

            # Check for double click
            if (
                event_type == "left_click"  # Only consider left clicks for double clicks
                and self._last_click_pos is not None
                and (current_time - self._last_click_time) < self._double_click_threshold
            ):
                is_double_click = True

            # If double click detected, update previous event and skip new recording
            if is_double_click:
                with self._events_lock:
                    # Find the last mouse event
                    # We look backwards for the last left_click event
                    for i in range(len(self.events) - 1, -1, -1):
                        evt = self.events[i]
                        if isinstance(evt, MouseEvent) and evt.event_type == "left_click":
                            # Update the event type to double_click
                            evt.event_type = "double_click"
                            print(f"🔄 Merged into double_click [Seq: {evt.sequence}]")

                            # Reset double click state to prevent "triple click" being merged again
                            self._last_click_time = 0.0
                            self._last_click_pos = None
                            return

            # Update last click state for future double click detection
            if event_type == "left_click":
                self._last_click_time = current_time
                self._last_click_pos = (x, y)
            else:
                # Reset for other click types
                self._last_click_time = 0.0
                self._last_click_pos = None

            # Capture screenshot before processing (only for new/single clicks)
            screenshot_path = self._capture_screenshot()

            # Create mouse event with sequence number
            with self._events_lock:
                self._event_sequence += 1
                sequence = self._event_sequence
                timestamp = datetime.now().isoformat()

            mouse_event = MouseEvent(
                timestamp=timestamp,
                event_type=event_type,
                x=x,
                y=y,
                screenshot_path=screenshot_path,
                sequence=sequence,
            )

            # Quick add to events list first (for immediate recording)
            with self._events_lock:
                self.events.append(mouse_event)
                # Sort events by sequence number to ensure temporal order
                self.events.sort(key=lambda e: getattr(e, "sequence", 0))

            # Queue the event for asynchronous omniparser processing
            try:
                self._event_queue.put((mouse_event, screenshot_path, sequence, timestamp))
                print(f"🖱️  {event_type} recorded at ({x}, {y}) [Seq: {sequence}] - Queued for omniparser")
            except Exception as queue_error:
                print(f"❌ Failed to queue event for omniparser: {queue_error}")

        except Exception as e:
            print(f"Error handling mouse click at position: {e}")

    def _capture_screenshot(self) -> str:
        """Capture a screenshot and save it."""
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")[:-3]
        filename = f"screenshot_{timestamp}.png"
        filepath = os.path.join(self.screenshot_dir, filename)

        # Capture screenshot
        screenshot = pyautogui.screenshot()
        screenshot.save(filepath)

        return filepath

    def _handle_keyboard_event(self, event: keyboard.KeyboardEvent) -> None:
        """Handle keyboard events and aggregate text input."""
        if self._stop_event.is_set():
            return

        try:
            # Avoid recording the stop recording hotkey
            if event.name in ["r"] and (keyboard.is_pressed("ctrl") and keyboard.is_pressed("alt")):
                return

            # Handle KEY_UP to clear active keys
            if event.event_type == keyboard.KEY_UP:
                if event.name in self.active_keys:
                    self.active_keys.remove(event.name)
                return

            # Only process key press events for text input
            if event.event_type != keyboard.KEY_DOWN:
                return

            # Track key state to avoid duplicates
            if event.name in self.active_keys:
                return  # Already recording this key press
            self.active_keys.add(event.name)

            key_name = event.name or ""

            # Ignore modifier key presses themselves (they'll be used with other keys)
            if key_name in ["ctrl", "alt", "shift", "windows"]:
                return

            # Check for modifier keys (but distinguish shift for text input)
            has_ctrl = keyboard.is_pressed("ctrl")
            has_alt = keyboard.is_pressed("alt")
            has_shift = keyboard.is_pressed("shift")

            # Only treat as modifier combination if Ctrl or Alt is pressed
            # Shift alone is for capitalization, not a modifier combination
            is_modifier_combination = has_ctrl or has_alt

            modifiers = []
            if has_ctrl:
                modifiers.append("ctrl")
            if has_shift and is_modifier_combination:  # Only include shift if with ctrl/alt
                modifiers.append("shift")
            if has_alt:
                modifiers.append("alt")

            # Determine if it's a special key (excluding regular letters/numbers and space)
            # Space is NOT a special key - it's a regular text character
            is_special_key = (
                (len(key_name) > 1 and key_name != "space")  # Multi-char keys like F1, enter, etc. (but not space)
                or event.scan_code > 127  # Extended keys
            )

            # Check if this is a terminating key (Enter, Escape, Tab)
            is_terminating_key = key_name in ["enter", "esc", "tab"]

            # If there are real modifiers (Ctrl/Alt), save current input and record as special
            if is_modifier_combination:
                self._save_text_input()  # Save any pending text input
                # Record special key combination
                with self._events_lock:
                    self._event_sequence += 1
                    sequence = self._event_sequence
                    timestamp = datetime.now().isoformat()

                keyboard_event = KeyboardEvent(
                    timestamp=timestamp,
                    event_type="keyboard",
                    key=event.name,
                    event_subtype="key_press",
                    is_special_key=True,
                    modifiers=modifiers,
                    sequence=sequence,
                )

                with self._events_lock:
                    self.events.append(keyboard_event)
                    self.events.sort(key=lambda e: getattr(e, "sequence", 0))

                modifier_str = "+".join(modifiers) + "+"
                print(f"⌨️  {modifier_str}{event.name} pressed [Seq: {sequence}]")
                return

            # Handle terminating keys
            if is_terminating_key:
                self._save_text_input()  # Save any pending text input
                # Record the terminating key as a separate event
                with self._events_lock:
                    self._event_sequence += 1
                    sequence = self._event_sequence
                    timestamp = datetime.now().isoformat()

                keyboard_event = KeyboardEvent(
                    timestamp=timestamp,
                    event_type="keyboard",
                    key=event.name,
                    event_subtype="key_press",
                    is_special_key=True,
                    modifiers=None,
                    sequence=sequence,
                )

                with self._events_lock:
                    self.events.append(keyboard_event)
                    self.events.sort(key=lambda e: getattr(e, "sequence", 0))

                print(f"⌨️  {event.name} pressed [Seq: {sequence}]")
                return

            # Handle text input accumulation
            if not is_special_key:
                # Start new input session if needed
                if self._input_start_time is None:
                    self._input_start_time = time.time()
                    self._input_start_timestamp = datetime.now().isoformat()

                    # Pre-assign sequence number at start of input
                    with self._events_lock:
                        self._event_sequence += 1
                        self._input_sequence = self._event_sequence

                # Add character to buffer
                char = self._get_character_from_key(key_name)
                if char:
                    self._input_buffer.append(char)
                    self._last_key_time = time.time()

                    # Cancel existing timer
                    if self._input_timer:
                        self._input_timer.cancel()

                    # Start new timer for input timeout
                    self._input_timer = threading.Timer(self._input_timeout, self._save_text_input)
                    self._input_timer.daemon = True
                    self._input_timer.start()

                    print(f"⌨️  Buffering: {char}")
            else:
                # For other special keys (arrows, function keys, etc.)
                self._save_text_input()  # Save any pending text input first
                with self._events_lock:
                    self._event_sequence += 1
                    sequence = self._event_sequence
                    timestamp = datetime.now().isoformat()

                keyboard_event = KeyboardEvent(
                    timestamp=timestamp,
                    event_type="keyboard",
                    key=event.name,
                    event_subtype="key_press",
                    is_special_key=True,
                    modifiers=None,
                    sequence=sequence,
                )

                with self._events_lock:
                    self.events.append(keyboard_event)
                    self.events.sort(key=lambda e: getattr(e, "sequence", 0))

                print(f"⌨️  {event.name} pressed [Seq: {sequence}]")

        except Exception as e:
            print(f"Error handling keyboard event: {e}")

    def _get_character_from_key(self, key_name: str) -> Optional[str]:
        """Convert key name to actual character, considering Shift modifier."""
        # Handle backspace
        if key_name == "backspace":
            if self._input_buffer:
                self._input_buffer.pop()
            return None

        # Handle space
        if key_name == "space":
            return " "

        # Check if shift is pressed for capitalization
        has_shift = keyboard.is_pressed("shift")

        # Regular single characters (letters)
        if len(key_name) == 1:
            if key_name.isalpha():
                # Apply shift for uppercase
                return key_name.upper() if has_shift else key_name
            elif key_name.isdigit():
                # Shift+number gives special characters
                if has_shift:
                    shift_number_map = {
                        "1": "!",
                        "2": "@",
                        "3": "#",
                        "4": "$",
                        "5": "%",
                        "6": "^",
                        "7": "&",
                        "8": "*",
                        "9": "(",
                        "0": ")",
                    }
                    return shift_number_map.get(key_name, key_name)
                else:
                    return key_name
            else:
                # Other single character keys (punctuation, etc.)
                # Handle common punctuation with shift
                if has_shift:
                    shift_char_map = {
                        "-": "_",
                        "=": "+",
                        "[": "{",
                        "]": "}",
                        "\\": "|",
                        ";": ":",
                        "'": '"',
                        ",": "<",
                        ".": ">",
                        "/": "?",
                        "`": "~",
                    }
                    return shift_char_map.get(key_name, key_name)
                else:
                    return key_name

        return None

    def _save_text_input(self) -> None:
        """Save the accumulated text input as a TextInputEvent."""
        try:
            # Cancel any pending timer
            if self._input_timer:
                self._input_timer.cancel()
                self._input_timer = None

            # Only save if there's actual input
            if not self._input_buffer or self._input_start_time is None:
                # Reset state
                self._input_buffer = []
                self._input_start_time = None
                self._input_start_timestamp = None
                self._last_key_time = None
                self._input_sequence = None
                return

            # Calculate duration
            end_time = time.time()
            duration = end_time - self._input_start_time
            end_timestamp = datetime.now().isoformat()

            # Create text from buffer
            text = "".join(self._input_buffer)

            # Create TextInputEvent
            # Use pre-assigned sequence if available, otherwise assign new one
            if self._input_sequence is not None:
                sequence = self._input_sequence
            else:
                with self._events_lock:
                    self._event_sequence += 1
                    sequence = self._event_sequence

            text_input_event = TextInputEvent(
                timestamp=self._input_start_timestamp,
                event_type="text_input",
                text=text,
                key_count=len(self._input_buffer),
                duration=duration,
                start_timestamp=self._input_start_timestamp,
                end_timestamp=end_timestamp,
                sequence=sequence,
            )

            # Add to events list
            with self._events_lock:
                self.events.append(text_input_event)
                self.events.sort(key=lambda e: getattr(e, "sequence", 0))

            print(
                f'📝 Text input recorded: "{text}" ({len(self._input_buffer)} keys, {duration:.2f}s) [Seq: {sequence}]'
            )

            # Reset state
            self._input_buffer = []
            self._input_start_time = None
            self._input_start_timestamp = None
            self._last_key_time = None
            self._input_sequence = None

        except Exception as e:
            print(f"Error saving text input: {e}")
            # Reset state on error
            self._input_buffer = []
            self._input_start_time = None
            self._input_start_timestamp = None
            self._last_key_time = None
            self._input_sequence = None

    def _process_with_omniparser(self, mouse_event: MouseEvent) -> None:
        """
        Process the screenshot with Gradio Omniparser to get the clicked box.

        This implementation connects to a Gradio-hosted omniparser service
        and retrieves the actual parsed boxes for the clicked position.
        """
        annotated_image_path = None
        try:
            screenshot_path = mouse_event.screenshot_path
            x, y = mouse_event.x, mouse_event.y

            print(f"Processing screenshot with omniparser at position ({x}, {y})...")

            # Create Gradio client
            try:
                # Get omniparser URL from environment variable, fallback to localhost
                omniparser_url = os.environ.get("OMNIPARSER_URL", "http://127.0.0.1:8101/")
                client = Client(omniparser_url)

                # Process the screenshot with omniparser
                result = client.predict(
                    image_input=handle_file(screenshot_path),
                    box_threshold=0.05,
                    iou_threshold=0.1,
                    use_paddleocr=True,
                    imgsz=640,
                    api_name="/process",
                )

                # Parse the result - first element is annotated image, rest are box data
                if len(result) >= 2:
                    annotated_image_path = result[0]  # First element is the annotated webp
                    box_data_text = result[1]  # Second element is box data as text

                    print(f"✅ Omniparser returned annotated image and {len(box_data_text)} characters of box data")

                    # Parse the box data text using the new parser
                    boxes = parse_omniparser_box_data(box_data_text)
                    print(f"📦 Parsed {len(boxes)} boxes from text data")

                    # Get image dimensions for coordinate conversion
                    image = Image.open(screenshot_path)
                    img_width, img_height = image.size

                    # Find all boxes that contain the clicked position
                    matched_boxes = []
                    for box_data in boxes:
                        if self._is_point_in_box(x, y, box_data, img_width, img_height):
                            matched_boxes.append(box_data)

                    # Prioritize yolo boxes over ocr boxes
                    clicked_box = None
                    if matched_boxes:
                        # First try to find a yolo box
                        for box in matched_boxes:
                            if isinstance(box, dict) and box.get("source") == "box_yolo_content_yolo":
                                clicked_box = box
                                break

                        # If no yolo box found, use the first matched box (likely ocr)
                        if clicked_box is None:
                            clicked_box = matched_boxes[0]

                    if clicked_box:
                        print(f"🎯 Found clicked box: {clicked_box}")
                        self._save_clicked_box(
                            screenshot_path,
                            mouse_event,
                            clicked_box,
                            annotated_image_path,
                        )
                    else:
                        # Fallback to basic box if no matching box found, but save annotated webp anyway
                        print("⚠️  No matching box found, using fallback but saving annotated webp")
                        self._save_fallback_box(screenshot_path, mouse_event, annotated_image_path)
                else:
                    print(
                        """⚠️  Unexpected result format from omniparser, 
                        using fallback but saving any available annotated webp"""
                    )
                    # Try to get annotated image if it's there but in different format
                    if len(result) >= 1 and result[0]:
                        annotated_image_path = result[0]
                    self._save_fallback_box(screenshot_path, mouse_event, annotated_image_path)

            except Exception as client_error:
                print(f"❌ Error connecting to omniparser: {client_error}")
                # Fallback to basic box if omniparser fails
                self._save_fallback_box(screenshot_path, mouse_event, annotated_image_path)

        except Exception as e:
            print(f"❌ Error processing with omniparser: {e}")
            # Fallback to basic box if any error occurs
            self._save_fallback_box(screenshot_path, mouse_event, annotated_image_path)

    def _is_point_in_box(self, x: int, y: int, box_data, image_width: int, image_height: int) -> bool:
        """Check if a point is inside a box returned by omniparser."""
        try:
            # Handle omniparser box format
            if isinstance(box_data, dict) and "bbox" in box_data:
                # Omniparser returns normalized coordinates [0-1]
                bbox = box_data["bbox"]
                if len(bbox) >= 4:
                    # Convert normalized coordinates to pixel coordinates
                    left = int(bbox[0] * image_width)
                    top = int(bbox[1] * image_height)
                    right = int(bbox[2] * image_width)
                    bottom = int(bbox[3] * image_height)

                    return left <= x <= right and top <= y <= bottom
            return False

        except Exception as e:
            print(f"Error checking point in box: {e}")
            return False

    def _save_clicked_box(
        self,
        screenshot_path: str,
        mouse_event: MouseEvent,
        box_data,
        annotated_image_path: str = None,
    ) -> None:
        """Save the clicked box based on omniparser result."""
        try:
            # Load the screenshot
            image = Image.open(screenshot_path)
            img_width, img_height = image.size

            # Extract box coordinates from omniparser result
            if isinstance(box_data, dict) and "bbox" in box_data:
                bbox = box_data["bbox"]
                if len(bbox) == 4:
                    # Convert normalized coordinates to pixel coordinates
                    left = int(bbox[0] * img_width)
                    top = int(bbox[1] * img_height)
                    right = int(bbox[2] * img_width)
                    bottom = int(bbox[3] * img_height)

                    # Crop the clicked box
                    clicked_box = image.crop((left, top, right, bottom))

                    # Save the clicked box
                    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")[:-3]
                    box_filename = f"clicked_box_{timestamp}.png"
                    box_filepath = os.path.join(self.clicked_boxes_dir, box_filename)
                    clicked_box.save(box_filepath)

                    # Save annotated image if available
                    annotated_filepath = None
                    if annotated_image_path:
                        try:
                            # Copy annotated image to our directory
                            import shutil

                            annotated_filename = f"annotated_{timestamp}.webp"
                            annotated_filepath = os.path.join(self.clicked_boxes_dir, annotated_filename)
                            shutil.copy2(annotated_image_path, annotated_filepath)
                        except Exception as e:
                            print(f"Warning: Could not save annotated image: {e}")

                    # Update mouse event with box information
                    mouse_event.clicked_box_path = box_filepath
                    mouse_event.box_coordinates = {
                        "left": left,
                        "top": top,
                        "right": right,
                        "bottom": bottom,
                    }

                    # Create omniparser result with actual data
                    mouse_event.omniparser_result = {
                        "box": mouse_event.box_coordinates,
                        "omniparser_data": box_data,
                        "annotated_image": annotated_filepath,
                        "confidence": box_data.get("confidence", 0.9) if isinstance(box_data, dict) else 0.9,
                        "fallback": False,
                    }

                    print(f"💾 Saved clicked box: {box_filepath}")
                    if annotated_filepath:
                        print(f"💾 Saved annotated image: {annotated_filepath}")

                    return

            # If we get here, the box data format is not recognized
            print("⚠️  Unrecognized box data format, using fallback")
            self._save_fallback_box(screenshot_path, mouse_event)

        except Exception as e:
            print(f"Error saving clicked box: {e}")
            self._save_fallback_box(screenshot_path, mouse_event)

    def _save_fallback_box(
        self,
        screenshot_path: str,
        mouse_event: MouseEvent,
        annotated_image_path: str = None,
    ) -> None:
        """Save a fallback box when omniparser fails or no matching box is found."""
        try:
            # Load the screenshot
            image = Image.open(screenshot_path)
            x, y = mouse_event.x, mouse_event.y

            # Define a box around the click position (100x100 pixels)
            box_size = 100
            left = max(0, x - box_size // 2)
            top = max(0, y - box_size // 2)
            right = min(image.width, x + box_size // 2)
            bottom = min(image.height, y + box_size // 2)

            # Crop the clicked box
            clicked_box = image.crop((left, top, right, bottom))

            # Save the clicked box
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")[:-3]
            box_filename = f"clicked_box_{timestamp}.png"
            box_filepath = os.path.join(self.clicked_boxes_dir, box_filename)
            clicked_box.save(box_filepath)

            # Save annotated image if available (even in fallback)
            annotated_filepath = None
            if annotated_image_path:
                try:
                    # Copy annotated image to our directory
                    import shutil

                    annotated_filename = f"annotated_{timestamp}.webp"
                    annotated_filepath = os.path.join(self.clicked_boxes_dir, annotated_filename)
                    shutil.copy2(annotated_image_path, annotated_filepath)
                    print(f"💾 Saved fallback annotated image: {annotated_filepath}")
                except Exception as e:
                    print(f"Warning: Could not save annotated image in fallback: {e}")

            # Update mouse event with box information
            mouse_event.clicked_box_path = box_filepath
            mouse_event.box_coordinates = {
                "left": left,
                "top": top,
                "right": right,
                "bottom": bottom,
            }

            # Create fallback omniparser result
            mouse_event.omniparser_result = {
                "confidence": 0.8,
                "element_type": "unknown",
                "element_text": "Fallback",
                "box": mouse_event.box_coordinates,
                "annotated_image": annotated_filepath,
                "fallback": True,
            }

            print(f"💾 Saved fallback box: {box_filepath}")

        except Exception as e:
            print(f"Error in fallback box processing: {e}")

    def save_recording(self, filename: Optional[str] = None) -> str:
        """
        Save the recorded events to a JSON file.

        Args:
            filename: Optional filename for the recording file

        Returns:
            Path to the saved recording file
        """
        if not self.events:
            print("No events to save")
            return ""

        if filename is None:
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            filename = f"mouse_recording_{timestamp}.json"

        filepath = os.path.join(self.output_dir, filename)

        # Convert events to dictionary format
        recording_data = {
            "recording_info": {
                "start_time": self.events[0].timestamp if self.events else None,
                "end_time": self.events[-1].timestamp if self.events else None,
                "total_events": len(self.events),
                "created_at": datetime.now().isoformat(),
            },
            "events": [asdict(event) for event in self.events],
        }

        with open(filepath, "w", encoding="utf-8") as f:
            json.dump(recording_data, f, indent=2, ensure_ascii=False)

        print(f"💾 Recording saved to: {filepath}")
        return filepath

    def get_events_summary(self) -> Dict[str, Any]:
        """Get a summary of recorded events."""
        if not self.events:
            return {"total_events": 0}

        return {
            "total_events": len(self.events),
            "start_time": self.events[0].timestamp,
            "end_time": self.events[-1].timestamp,
            "event_types": list(set(event.event_type for event in self.events)),
            "click_positions": [(event.x, event.y) for event in self.events if hasattr(event, "x")],
        }

    def clear_events(self) -> None:
        """Clear all recorded events."""
        self.events = []
        print("🗑️  All events cleared")

    # ------------------------------------------------------------------
    # 会话管理（合并自原 RecorderAgent）
    # ------------------------------------------------------------------

    def start_session(self, session_name: Optional[str] = None) -> Dict[str, Any]:
        """
        启动一个录制会话。

        Args:
            session_name: 会话名称，留空自动生成

        Returns:
            包含 status, session_name, output_dir, message 的字典
        """
        if not session_name:
            session_name = datetime.now().strftime("%Y%m%d_%H%M%S")

        session_dir = os.path.join(self.output_dir, session_name)

        # 重新初始化目录
        self.screenshot_dir = os.path.join(session_dir, "screenshots")
        self.clicked_boxes_dir = os.path.join(session_dir, "clicked_boxes")
        os.makedirs(self.screenshot_dir, exist_ok=True)
        os.makedirs(self.clicked_boxes_dir, exist_ok=True)

        # 更新 output_dir 为会话目录
        self._session_output_dir = session_dir
        self.start_recording()

        return {
            "status": "recording",
            "session_name": session_name,
            "output_dir": session_dir,
            "message": f"Recording started: {session_name}",
        }

    def stop_session(self) -> Dict[str, Any]:
        """
        停止当前录制会话并保存。

        Returns:
            包含 status, recording_file, summary, message 的字典
        """
        if not hasattr(self, '_session_output_dir'):
            return {"status": "error", "message": "No recording in progress"}

        self._save_text_input()
        if not self._stop_event.is_set():
            self._stop_event.set()
        self._stop_event_processing()

        if self.recording_thread and self.recording_thread.is_alive():
            self.recording_thread.join(timeout=2)

        # 保存到会话目录
        recording_file = self._save_session_recording()
        summary = self.get_events_summary()

        return {
            "status": "completed",
            "recording_file": recording_file,
            "summary": summary,
            "message": f"Recording complete, {summary.get('total_events', 0)} events total",
        }

    def _save_session_recording(self) -> str:
        """保存录制到会话目录"""
        if not self.events:
            return ""

        session_dir = getattr(self, '_session_output_dir', self.output_dir)
        os.makedirs(session_dir, exist_ok=True)

        filepath = os.path.join(session_dir, "recording.json")

        recording_data = {
            "recording_info": {
                "start_time": self.events[0].timestamp if self.events else None,
                "end_time": self.events[-1].timestamp if self.events else None,
                "total_events": len(self.events),
                "created_at": datetime.now().isoformat(),
            },
            "events": [asdict(event) for event in self.events],
        }

        with open(filepath, "w", encoding="utf-8") as f:
            json.dump(recording_data, f, indent=2, ensure_ascii=False)

        print(f"Recording saved: {filepath}")
        return filepath

    def get_recording_status(self) -> Dict[str, Any]:
        """
        查询当前录制状态。

        Returns:
            包含 status, message（recording 时还含 events_recorded）的字典
        """
        # 未调用过 start_session
        if not getattr(self, "_session_output_dir", None):
            return {"status": "idle", "message": "No recording in progress"}

        if not self._stop_event.is_set():
            summary = self.get_events_summary()
            return {
                "status": "recording",
                "events_recorded": summary["total_events"],
                "message": f"Recording in progress, {summary['total_events']} events captured",
            }
        else:
            return {"status": "stopped", "message": "Recording session stopped"}

    def load_recording(self, recording_file: str) -> Dict[str, Any]:
        """
        读回已保存的录制文件。

        Args:
            recording_file: 录制 JSON 文件路径

        Returns:
            包含 status, recording_info, events, total_events, message 的字典
        """
        try:
            with open(recording_file, "r", encoding="utf-8") as f:
                data = json.load(f)

            return {
                "status": "loaded",
                "recording_info": data.get("recording_info", {}),
                "events": data.get("events", []),
                "total_events": len(data.get("events", [])),
                "message": f"Read {len(data.get('events', []))} events from {recording_file}",
            }

        except Exception as e:
            return {"status": "error", "message": f"Failed to read recording: {str(e)}"}

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        if not self._stop_event.is_set():
            self.stop_recording()


def main():
    print("AI Observer - record mouse and keyboard actions")
    print("Press Ctrl+Alt+R to stop recording")
    print()

    observer = Observer()
    result = observer.start_session()
    print(result["message"])

    observer._stop_event.wait()

    result = observer.stop_session()
    print(result["message"])

    summary = result.get("summary", {})
    if summary.get("total_events", 0) > 0:
        print(f"Event types: {', '.join(summary.get('event_types', []))}")


if __name__ == "__main__":
    main()
