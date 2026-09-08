#!/usr/bin/env python
# coding=utf-8
"""English UI layer for the CLI wizard.

Reuses the pure-logic helpers from the ``ui`` package (console, theme,
prompt-toolkit wiring, markdown parsing, mtime formatting) and re-implements
only the user-facing, string-bearing functions in English. This keeps the
original Chinese ``ui`` package untouched and fully working.
"""

import glob
import os
from datetime import datetime

import questionary
from questionary import Choice
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

# Reuse non-string logic from the existing ui package.
from ui.menu import (
    MENU_STYLE,
    EscapePressed,
    confirm,
    select_with_escape,
    text_input,
)
from ui.session_ui import _get_session_mtime
from ui.theme import console, print_step_header

__all__ = [
    "EscapePressed",
    "console",
    "confirm",
    "get_recommendation",
    "main_menu",
    "print_banner",
    "print_error",
    "print_step_header",
    "print_step_log",
    "print_success",
    "print_warning",
    "render_sessions_table",
    "run_env_check",
    "select_session_interactive",
    "show_session_status",
    "text_input",
]


# ---------------------------------------------------------------------------
# Banner
# ---------------------------------------------------------------------------


def print_banner():
    flow = Text(justify="center")
    flow.append("Observe", style="bold green")
    flow.append("  -->  ", style="flow.arrow")
    flow.append("Reason", style="bold yellow")
    flow.append("  -->  ", style="flow.arrow")
    flow.append("Configure", style="bold cyan")
    flow.append("  -->  ", style="flow.arrow")
    flow.append("Execute", style="bold red")

    panel = Panel(
        flow,
        title="[banner.title]LLM Software-Operation Agent[/]",
        border_style="blue",
        padding=(1, 4),
    )
    console.print()
    console.print(panel)


# ---------------------------------------------------------------------------
# Theme messages (English prefixes)
# ---------------------------------------------------------------------------


def print_error(message: str, fix: str = ""):
    console.print(f"  [bold red]Error:[/] {message}")
    if fix:
        console.print(f"  [dim]Fix:[/] {fix}")


def print_warning(message: str):
    console.print(f"  [bold yellow]Warning:[/] {message}")


def print_success(message: str):
    console.print(f"  [bold green]Done:[/] {message}")


def print_step_log(title: str, info: dict, next_step: str = ""):
    console.print()
    console.rule(f"[bold]{title}[/]", style="green")
    for k, v in info.items():
        console.print(f"  [cyan]{k}:[/] {v}")
    if next_step:
        console.print()
        console.print(f"  [bold green]>>> Next:[/] {next_step}")
    console.rule(style="green")
    console.print()


# ---------------------------------------------------------------------------
# Main menu
# ---------------------------------------------------------------------------


MENU_CHOICES = [
    Choice(title="AI Observe     Record mouse and keyboard actions", value="observe"),
    Choice(title="AI Reason      Analyze the recording into action descriptions", value="reason"),
    Choice(title="Customize      Edit domain SOP and user parameters", value="configure"),
    Choice(title="AI Execute     Replay the actions from the descriptions", value="execute"),
    Choice(title="Full Pipeline  Observe -> Reason -> Configure -> Execute", value="full_flow"),
    Choice(title="Env Check      Check dependencies and configuration", value="env_check"),
    Choice(title="Exit", value="exit"),
]


def main_menu(recommendation: str = "") -> str:
    if recommendation:
        console.print(f"\n  [bold green]>>> Recommended next step:[/] {recommendation}\n")

    choice = select_with_escape(
        "Choose an action (arrow keys to move, Enter to confirm, Esc to exit):",
        choices=MENU_CHOICES,
        use_arrow_keys=True,
        use_shortcuts=False,
    )

    return choice or "exit"


# ---------------------------------------------------------------------------
# Recommendations
# ---------------------------------------------------------------------------


def get_recommendation(sessions: list[dict]) -> str:
    if not sessions:
        return "Create your first recording -> choose AI Observe"

    latest = max(sessions, key=lambda s: os.path.getmtime(s["dir"]), default=None)
    if not latest:
        return ""

    name = latest["name"]

    if not latest["has_recording"]:
        return f"Session '{name}' needs a recording -> choose AI Observe"

    if not latest["has_prompt"]:
        return f"Session '{name}' is recorded ({latest['event_count']} events); run AI Reason next"

    if not latest["has_domain"] and not latest["has_params"]:
        return f"Session '{name}' is reasoned; configure domain rules and parameters next"

    if latest["has_prompt"]:
        return f"Session '{name}' is ready; you can execute -> choose AI Execute"

    return "All sessions are complete; you can create a new recording"


# ---------------------------------------------------------------------------
# Session table / status
# ---------------------------------------------------------------------------


def _build_status_tags(session: dict) -> str:
    tags = []
    if session["has_recording"]:
        tags.append("[green]recorded[/]")
    if session["has_prompt"]:
        tags.append("[green]reasoned[/]")
    else:
        tags.append("[yellow]pending[/]")
    if session["has_domain"]:
        tags.append("[green]SOP[/]")
    if session["has_params"]:
        tags.append("[green]params[/]")
    return " ".join(tags)


def render_sessions_table(sessions: list[dict]) -> Table:
    table = Table(title="Available sessions", show_lines=False, padding=(0, 1))
    table.add_column("#", style="bold cyan", width=3)
    table.add_column("App", style="bold")
    table.add_column("Session", style="white")
    table.add_column("Events", justify="right")
    table.add_column("Status", no_wrap=True)
    table.add_column("Last modified", style="dim")

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
        "recording.json": "Recording",
        "prompt.json": "Reasoning result",
        "domain.md": "Domain SOP",
        "params.md": "User parameters",
    }
    console.print(f"  [bold]Session directory:[/] {session_dir}")
    for filename, label in files.items():
        path = os.path.join(session_dir, filename)
        if os.path.exists(path):
            console.print(f"    [green]●[/] {label} ({filename})")
        else:
            console.print(f"    [dim]○[/] {label} ({filename})")


def select_session_interactive(sessions: list[dict], purpose: str = "operation") -> str | None:
    if not sessions:
        console.print("  [yellow]No session directories found[/]")
        path = questionary.text("Enter the session directory path:", style=MENU_STYLE).ask()
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
        label = f"[{s.get('app_name', '')}] {s['name']}  ({s['event_count']} events)"
        choices.append(Choice(title=label, value=s["dir"]))
    choices.append(Choice(title="Enter a path manually...", value="__manual__"))

    result = select_with_escape(
        f"Select a session (for {purpose}, Esc to go back):",
        choices=choices,
        use_arrow_keys=True,
    )

    if result is None:
        raise EscapePressed()
    if result == "__manual__":
        path = questionary.text("Enter the session directory path:", style=MENU_STYLE).ask()
        if path is None:
            raise EscapePressed()
        if path and os.path.isdir(path.strip()):
            return path.strip()
        return None
    return result


# ---------------------------------------------------------------------------
# Environment check
# ---------------------------------------------------------------------------


def run_env_check():
    console.print()
    console.rule("[bold]Environment Check[/]", style="cyan")
    console.print()

    deps = {
        "pyautogui": ("Screenshots and mouse control", "pip install pyautogui"),
        "pynput": ("Mouse event listening", "pip install pynput"),
        "keyboard": ("Keyboard event listening", "pip install keyboard"),
        "openai": ("VLM API calls", "pip install openai"),
        "PIL": ("Image processing", "pip install pillow"),
        "tqdm": ("Progress bars", "pip install tqdm"),
        "dotenv": ("Environment variables", "pip install python-dotenv"),
        "rich": ("Terminal formatting", "pip install rich"),
        "questionary": ("Interactive prompts", "pip install questionary"),
    }

    table = Table(title="Dependency check", show_lines=False, padding=(0, 1))
    table.add_column("Status", width=6, justify="center")
    table.add_column("Module", style="bold", min_width=14)
    table.add_column("Description")
    table.add_column("Install command", style="dim")

    all_ok = True
    missing_cmds = []

    for mod, (desc, install_cmd) in deps.items():
        import_name = mod
        if mod == "PIL":
            import_name = "PIL"
        elif mod == "dotenv":
            import_name = "dotenv"
        try:
            __import__(import_name)
            table.add_row("[green]●[/]", mod, desc, "")
        except ImportError:
            table.add_row("[red]●[/]", mod, desc, install_cmd)
            all_ok = False
            missing_cmds.append(install_cmd)

    console.print(table)
    console.print()

    env_table = Table(title="Environment variables", show_lines=False, padding=(0, 1))
    env_table.add_column("Status", width=6, justify="center")
    env_table.add_column("Variable", style="bold", min_width=22)
    env_table.add_column("Value", style="dim")

    env_vars = [
        "QIANFAN_API_KEY", "API_KEY", "REASON_API_KEY",
        "REASON_MODEL_NAME", "MODEL_NAME", "API_URL", "OMNIPARSER_URL",
    ]
    for var in env_vars:
        val = os.environ.get(var)
        if val:
            display = val[:8] + "..." if ("key" in var.lower() and len(val) > 12) else val
            env_table.add_row("[green]●[/]", var, display)
        else:
            env_table.add_row("[dim]○[/]", var, "[dim]not set[/]")

    console.print(env_table)
    console.print()

    if all_ok:
        console.print("  [bold green]All dependencies are installed![/]")
    else:
        console.print("  [bold yellow]Missing dependencies, install with:[/]")
        for cmd in missing_cmds:
            console.print(f"    {cmd}")

    return all_ok
