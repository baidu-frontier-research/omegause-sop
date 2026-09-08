"""
系统通知工具 - 通过 Windows Toast 通知展示执行进度。

不会抢焦点、不影响鼠标操作、不干扰截图。
"""

import sys
import threading


def _notify_windows(title: str, message: str) -> None:
    try:
        from plyer import notification
        notification.notify(
            title=title,
            message=message,
            timeout=5,
            app_name="AI Executor",
        )
    except ImportError:
        pass
    except Exception:
        pass


def _notify_macos(title: str, message: str) -> None:
    try:
        import subprocess
        subprocess.Popen([
            "osascript", "-e",
            f'display notification "{message}" with title "{title}"',
        ], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except Exception:
        pass


def toast(title: str, message: str) -> None:
    """发送系统通知（非阻塞，不干扰自动化流程）。"""
    if sys.platform == "win32":
        fn = _notify_windows
    elif sys.platform == "darwin":
        fn = _notify_macos
    else:
        return

    t = threading.Thread(target=fn, args=(title, message), daemon=True)
    t.start()


def _ask_macos(title: str, message: str):
    """macOS 阻塞式对话框。返回 'continue'/'retry'/'stop' / None=无法弹窗。"""
    try:
        import subprocess

        safe_msg = message.replace("\\", "\\\\").replace('"', '\\"').replace("\n", " ")
        safe_title = title.replace("\\", "\\\\").replace('"', '\\"')
        script = (
            f'display dialog "{safe_msg}" with title "{safe_title}" '
            f'buttons {{"停止", "重试", "继续"}} default button "继续" with icon caution'
        )
        result = subprocess.run(
            ["osascript", "-e", script],
            capture_output=True,
            text=True,
        )
        if result.returncode != 0:
            # 用户取消/关闭对话框 → 视为停止
            return "stop"
        out = result.stdout
        if "继续" in out:
            return "continue"
        if "重试" in out:
            return "retry"
        return "stop"
    except Exception:
        return None


def _ask_windows(title: str, message: str):
    """Windows 阻塞式对话框。返回 'continue'/'retry'/'stop' / None=无法弹窗。"""
    # 优先 pymsgbox（可自定义按钮文案，随 pyautogui 一起安装）
    try:
        import pymsgbox

        choice = pymsgbox.confirm(text=message, title=title, buttons=("继续", "重试", "停止"))
        if choice == "继续":
            return "continue"
        if choice == "重试":
            return "retry"
        return "stop"  # "停止" 或关闭对话框
    except Exception:
        pass
    # 回退到原生 MessageBox（是=继续 / 否=重试 / 取消=停止）
    try:
        import ctypes

        MB_YESNOCANCEL = 0x3
        MB_ICONWARNING = 0x30
        MB_TOPMOST = 0x40000
        MB_SETFOREGROUND = 0x10000
        IDYES, IDNO = 6, 7
        ret = ctypes.windll.user32.MessageBoxW(
            0,
            f"{message}\n\n[是] 继续    [否] 重试    [取消] 停止",
            title,
            MB_YESNOCANCEL | MB_ICONWARNING | MB_TOPMOST | MB_SETFOREGROUND,
        )
        if ret == IDYES:
            return "continue"
        if ret == IDNO:
            return "retry"
        return "stop"
    except Exception:
        return None


def ask_user_action(title: str, message: str) -> str:
    """
    弹出阻塞式消息窗口，让用户选择：继续 / 重试 / 停止。

    返回 "continue"（继续当前步骤）、"retry"（从头重新执行）或 "stop"（终止）。
    无法弹窗的环境（如无图形界面）下回退到终端 input()。
    """
    if sys.platform == "darwin":
        result = _ask_macos(title, message)
        if result is not None:
            return result
    elif sys.platform == "win32":
        result = _ask_windows(title, message)
        if result is not None:
            return result

    # 回退：终端输入
    choice = input(f"\n❓ [{title}] {message}\n回车=继续 / 输入 r=重试 / 输入 stop=停止: ").strip().lower()
    if choice in ("stop", "s", "停止"):
        return "stop"
    if choice in ("r", "retry", "重试"):
        return "retry"
    return "continue"
