import glob
import os
from datetime import datetime

import questionary
from questionary import Choice
from rich.table import Table

from ui.menu import MENU_STYLE, EscapePressed, select_with_escape
from ui.theme import console


def _build_status_tags(session: dict) -> str:
    tags = []
    if session["has_recording"]:
        tags.append("[green]已录制[/]")
    if session["has_prompt"]:
        tags.append("[green]已推理[/]")
    else:
        tags.append("[yellow]待推理[/]")
    if session["has_domain"]:
        tags.append("[green]有SOP[/]")
    if session["has_params"]:
        tags.append("[green]有参数[/]")
    return " ".join(tags)


def _get_session_mtime(session_dir: str) -> str:
    try:
        rec_path = os.path.join(session_dir, "recording.json")
        if not os.path.exists(rec_path):
            old = glob.glob(os.path.join(session_dir, "mouse_recording_*.json"))
            rec_path = old[0] if old else session_dir
        mtime = os.path.getmtime(rec_path)
        return datetime.fromtimestamp(mtime).strftime("%m-%d %H:%M")
    except OSError:
        return ""


def render_sessions_table(sessions: list[dict]) -> Table:
    table = Table(title="可用会话", show_lines=False, padding=(0, 1))
    table.add_column("#", style="bold cyan", width=3)
    table.add_column("应用", style="bold")
    table.add_column("会话名", style="white")
    table.add_column("事件数", justify="right")
    table.add_column("状态", no_wrap=True)
    table.add_column("最后修改", style="dim")

    for i, s in enumerate(sessions):
        tags = _build_status_tags(s)
        mtime = _get_session_mtime(s["dir"])
        table.add_row(
            str(i),
            s.get("app_name", ""),
            s["name"],
            str(s["event_count"]),
            tags,
            mtime,
        )
    return table


def show_session_status(session_dir: str):
    files = {
        "recording.json": "录制文件",
        "prompt.json": "推理结果",
        "domain.md": "领域 SOP",
        "params.md": "用户参数",
    }
    console.print(f"  [bold]会话目录:[/] {session_dir}")
    for filename, label in files.items():
        path = os.path.join(session_dir, filename)
        if os.path.exists(path):
            console.print(f"    [green]●[/] {label} ({filename})")
        else:
            console.print(f"    [dim]○[/] {label} ({filename})")


def select_session_interactive(sessions: list[dict], purpose: str = "操作") -> str | None:
    if not sessions:
        console.print("  [yellow]未找到任何会话目录[/]")
        path = questionary.text("请输入会话目录路径:", style=MENU_STYLE).ask()
        if path is None:
            raise EscapePressed()
        if path and os.path.isdir(path.strip()):
            return path.strip()
        return None

    console.print()
    console.print(render_sessions_table(sessions))
    console.print()

    choices = []
    for s in sessions:
        label = f"[{s.get('app_name', '')}] {s['name']}  ({s['event_count']}事件)"
        choices.append(Choice(title=label, value=s["dir"]))
    choices.append(Choice(title="手动输入路径...", value="__manual__"))

    result = select_with_escape(
        f"选择会话 (用于{purpose}, Esc返回):",
        choices=choices,
        use_arrow_keys=True,
    )

    if result is None:
        raise EscapePressed()
    if result == "__manual__":
        path = questionary.text("请输入会话目录路径:", style=MENU_STYLE).ask()
        if path is None:
            raise EscapePressed()
        if path and os.path.isdir(path.strip()):
            return path.strip()
        return None
    return result
