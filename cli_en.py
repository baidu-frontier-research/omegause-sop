#!/usr/bin/env python
# coding=utf-8
"""Desktop Automation Tool - CLI Wizard (English)

Pipeline: Observe -> Reason -> Configure -> Execute

All files are organized around the session directory:
  sop/{app_name}/{session_name}/
    ├── screenshots/       # produced by observe
    ├── clicked_boxes/     # produced by observe
    ├── recording.json     # produced by observe
    ├── prompt.json        # produced by reason
    ├── domain.md          # user-edited (optional, read by execute)
    ├── params.md          # user-edited (optional, read by execute)
    ├── replay_logs/       # execution logs from execute
    └── replay_temp/       # temporary screenshots from execute

Usage: python cli_en.py
"""

import glob
import json
import os
import sys
import time
from datetime import datetime

from dotenv import load_dotenv

load_dotenv()

from ui_en import (
    EscapePressed,
    confirm,
    console,
    get_recommendation,
    main_menu,
    print_banner,
    print_error,
    print_step_header,
    print_step_log,
    print_success,
    print_warning,
    run_env_check,
    select_session_interactive,
    show_session_status,
    text_input,
)


# ---------------------------------------------------------------------------
# Session directory management
# ---------------------------------------------------------------------------


def _scan_session_dir(session_dir: str, session_name: str, app_name: str = "") -> dict | None:
    if not os.path.isdir(session_dir):
        return None

    info = {
        "dir": session_dir,
        "name": session_name,
        "app_name": app_name,
        "has_recording": os.path.exists(os.path.join(session_dir, "recording.json")),
        "has_prompt": os.path.exists(os.path.join(session_dir, "prompt.json")),
        "has_domain": os.path.exists(os.path.join(session_dir, "domain.md")),
        "has_params": os.path.exists(os.path.join(session_dir, "params.md")),
        "event_count": 0,
        "mtime": 0.0,
    }

    if not info["has_recording"]:
        old_files = glob.glob(os.path.join(session_dir, "mouse_recording_*.json"))
        if old_files:
            info["has_recording"] = True
            info["old_recording"] = old_files[0]

    if info["has_recording"]:
        rec_path = os.path.join(session_dir, "recording.json")
        if not os.path.exists(rec_path) and "old_recording" in info:
            rec_path = info["old_recording"]
        try:
            info["mtime"] = os.path.getmtime(rec_path)
        except OSError:
            try:
                info["mtime"] = os.path.getmtime(session_dir)
            except OSError:
                pass
        try:
            with open(rec_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            info["event_count"] = len(data.get("events", []))
        except Exception:
            pass

    return info if info["has_recording"] else None


def find_sessions() -> list[dict]:
    results = []

    sop_dir = "sop"
    if os.path.isdir(sop_dir):
        for app_name in os.listdir(sop_dir):
            app_dir = os.path.join(sop_dir, app_name)
            if not os.path.isdir(app_dir):
                continue
            for session_name in os.listdir(app_dir):
                session_dir = os.path.join(app_dir, session_name)
                info = _scan_session_dir(session_dir, session_name, app_name)
                if info:
                    results.append(info)

    old_dir = "recordings"
    if os.path.isdir(old_dir):
        for session_name in os.listdir(old_dir):
            session_dir = os.path.join(old_dir, session_name)
            info = _scan_session_dir(session_dir, session_name, app_name="(legacy recording)")
            if info:
                results.append(info)

    # Sort by directory last-modified time, newest first, matching the
    # "Last modified" column in the table.
    results.sort(key=lambda r: r.get("mtime", 0.0), reverse=True)
    return results


# ---------------------------------------------------------------------------
# Step 1: AI Observe (recording)
# ---------------------------------------------------------------------------


def run_observe() -> str | None:
    print_step_header(
        "AI Observe",
        "Record your mouse and keyboard actions. The AI saves screenshots and the "
        "event sequence as input data for later reasoning and execution.",
    )

    app_name = text_input("Application name (e.g. pvsyst, excel)")
    if not app_name:
        print_error("Application name cannot be empty")
        return None

    session_name = text_input("Session name (leave blank to auto-generate)")
    if not session_name:
        session_name = datetime.now().strftime("%Y%m%d_%H%M%S")

    output_dir = os.path.join("sop", app_name)
    session_dir = os.path.join(output_dir, session_name)

    while os.path.isdir(session_dir) and os.listdir(session_dir):
        print_warning(f"Session '{session_name}' already exists and is not empty; it cannot be overwritten")
        session_name = text_input("Enter a new session name (leave blank to auto-generate)")
        if not session_name:
            session_name = datetime.now().strftime("%Y%m%d_%H%M%S")
        session_dir = os.path.join(output_dir, session_name)

    console.print(f"  Session directory: [bold]{session_dir}[/]")
    console.print()
    console.print("  [bold yellow]!! Press Ctrl+Alt+R to stop recording !![/]")
    console.print()

    if not confirm("Ready? Start recording"):
        return None

    console.print("  Starting the recorder...")

    try:
        from agents.observe.observer import Observer

        observer = Observer(output_dir=output_dir)
        result = observer.start_session(session_name)
        console.print(f"  {result['message']}")
        console.print()
        console.print("  [bold green][Recording][/] Use your computer normally; press Ctrl+Alt+R to stop when done")
        console.print()

        observer._stop_event.wait()
        result = observer.stop_session()

        console.print()
        print_success(result["message"])

        print_step_log("AI Observe complete", {
            "Session directory": session_dir,
            "Recording file": os.path.join(session_dir, "recording.json"),
            "Screenshots directory": os.path.join(session_dir, "screenshots"),
            "Event count": result.get("summary", {}).get("total_events", "?"),
        }, next_step="Run AI Reason to turn the recording into action descriptions")
        return session_dir

    except ImportError as e:
        print_error(f"Missing dependency - {e}", fix="pip install pynput keyboard pyautogui")
        return None
    except Exception as e:
        print_error(str(e))
        return None


# ---------------------------------------------------------------------------
# Step 2: AI Reason
# ---------------------------------------------------------------------------


def run_reason(session_dir: str | None = None) -> str | None:
    print_step_header(
        "AI Reason",
        "The AI analyzes the recorded screenshots and generates a semantic "
        "description for each action step, used as input instructions for the "
        "execution stage.",
    )

    if not session_dir:
        sessions = find_sessions()
        session_dir = select_session_interactive(sessions, "AI Reason")
        if not session_dir:
            return None

    recording_path = os.path.join(session_dir, "recording.json")
    if not os.path.exists(recording_path):
        old_files = glob.glob(os.path.join(session_dir, "mouse_recording_*.json"))
        if old_files:
            console.print("  Found a legacy-format file; copying it to recording.json")
            import shutil
            shutil.copy2(old_files[0], recording_path)
        else:
            print_error(f"{recording_path} does not exist", fix="Run AI Observe first to record")
            return None

    show_session_status(session_dir)
    console.print()

    api_key = os.environ.get("QIANFAN_API_KEY", "")
    if not api_key:
        print_warning("QIANFAN_API_KEY is not set")
        if not confirm("Continue anyway?", default=False):
            return None

    model_name = os.environ.get(
        "REASON_MODEL_NAME",
        os.environ.get("ENRICHER_MODEL_NAME", "qwen3-vl-235b-a22b-instruct"),
    )
    console.print(f"  VLM model: [bold]{model_name}[/]")
    console.print()

    if not confirm("Start AI Reason?"):
        return None

    console.print()

    try:
        from agents.reason.reasoner import Reasoner, ReasonerConfig

        config = ReasonerConfig()
        reasoner = Reasoner(config)
        reasoner.enrich(session_dir)

        print_step_log("AI Reason complete", {
            "Output file": os.path.join(session_dir, "prompt.json"),
            "Model": config.model_id,
            "API": config.api_base_url,
        }, next_step="Edit the configuration: create/modify domain.md and params.md")
        return session_dir

    except ImportError as e:
        print_error(f"Missing dependency - {e}", fix="pip install openai tqdm")
        return None
    except Exception as e:
        print_error(str(e))
        return None


# ---------------------------------------------------------------------------
# Step 2.5: Edit domain SOP and user parameters
# ---------------------------------------------------------------------------


def _open_in_editor(file_path: str) -> None:
    """Open a file in the system default editor, then return once the user is done."""
    import subprocess
    import platform

    abs_path = os.path.abspath(file_path)
    if platform.system() == "Darwin":
        subprocess.call(["open", "-t", "-W", abs_path])
    elif platform.system() == "Windows":
        os.startfile(abs_path)
        input("  Press Enter when you have finished editing...")
    else:
        editor = os.environ.get("EDITOR", "xdg-open")
        subprocess.call([editor, abs_path])


def run_edit_prompts(session_dir: str | None = None) -> str | None:
    print_step_header(
        "Customize",
        "Edit the domain rules (domain.md) and user parameters (params.md) so the "
        "execution stage fits your specific needs.",
    )

    if not session_dir:
        sessions = find_sessions()
        session_dir = select_session_interactive(sessions, "Customize")
        if not session_dir:
            return None

    show_session_status(session_dir)
    console.print()

    # Domain SOP
    domain_path = os.path.join(session_dir, "domain.md")
    if not os.path.exists(domain_path):
        template = "domain_knowledge_template/pvsyst_domain.md"
        if os.path.exists(template):
            if confirm(f"Copy domain.md from template {template}?"):
                import shutil
                shutil.copy2(template, domain_path)
                print_success(f"Copied to {domain_path}")
        else:
            if confirm("Create an empty domain.md?"):
                with open(domain_path, "w", encoding="utf-8") as f:
                    f.write("1. Write your domain-specific operation rules here\n")
                print_success(f"Created {domain_path}")

    if os.path.exists(domain_path):
        console.print(f"  [bold]domain.md[/]: {domain_path}")
        if confirm("Open domain.md for editing?"):
            _open_in_editor(domain_path)
            print_success("Finished editing domain.md")

    console.print()

    # User Params
    params_path = os.path.join(session_dir, "params.md")
    if not os.path.exists(params_path):
        template = "domain_knowledge_template/pvsyst_params.md"
        if os.path.exists(template):
            if confirm(f"Copy params.md from template {template}?"):
                import shutil
                shutil.copy2(template, params_path)
                print_success(f"Copied to {params_path}")
        else:
            if confirm("Create an empty params.md?"):
                with open(params_path, "w", encoding="utf-8") as f:
                    f.write("| Category | Parameter | Value |\n|----------|-----------|-------|\n")
                print_success(f"Created {params_path}")

    if os.path.exists(params_path):
        console.print(f"  [bold]params.md[/]: {params_path}")
        if confirm("Open params.md for editing?"):
            _open_in_editor(params_path)
            print_success("Finished editing params.md")

    console.print()
    print_step_log("Configuration", {
        "Domain SOP": domain_path + (" (exists)" if os.path.exists(domain_path) else " (to be created)"),
        "User parameters": params_path + (" (exists)" if os.path.exists(params_path) else " (to be created)"),
    }, next_step="Run AI Execute")
    return session_dir


# ---------------------------------------------------------------------------
# Step 3: AI Execute
# ---------------------------------------------------------------------------


def run_execute(session_dir: str | None = None) -> None:
    print_step_header(
        "AI Execute",
        "The AI operates the desktop automatically from the reasoned descriptions, "
        "reproducing the recorded workflow step by step. Do not move the mouse "
        "during execution.",
    )

    if not session_dir:
        sessions = find_sessions()
        session_dir = select_session_interactive(sessions, "AI Execute")
        if not session_dir:
            return

    prompt_path = os.path.join(session_dir, "prompt.json")
    if not os.path.exists(prompt_path):
        print_error(f"{prompt_path} does not exist", fix="Run AI Reason first")
        return

    show_session_status(session_dir)
    console.print()

    with open(prompt_path, "r", encoding="utf-8") as f:
        data = json.load(f)
    events = data.get("events", [])
    console.print(f"  {len(events)} action step(s) total")

    missing = sum(1 for e in events if not e.get("prompt"))
    if missing > 0:
        print_warning(f"{missing} step(s) have no description; consider running AI Reason first")

    domain_path = os.path.join(session_dir, "domain.md")
    params_path = os.path.join(session_dir, "params.md")
    if os.path.exists(domain_path):
        console.print(f"  Domain SOP: [green]{domain_path}[/]")
    else:
        console.print("  Domain SOP: [dim]not configured (using generic rules)[/]")
    if os.path.exists(params_path):
        console.print(f"  User parameters: [green]{params_path}[/]")
    else:
        console.print("  User parameters: [dim]not configured[/]")

    console.print()
    console.print("  [bold yellow]Execution is about to start! Do not move the mouse.[/]")

    for i in range(3, 0, -1):
        console.print(f"  [bold]{i}...[/]")
        time.sleep(1)
    console.print()

    try:
        from agents.execute.executor import Executor, ExecutorConfig

        config = ExecutorConfig()
        executor = Executor(session_dir, config)
        result = executor.run(start_from=0)

        log_dir = os.path.join(session_dir, "replay_logs")
        os.makedirs(log_dir, exist_ok=True)
        log_path = os.path.join(log_dir, f"replay_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json")
        executor.save_log(log_path)

        print_step_log("AI Execute complete", {
            "Total steps": result.total_events,
            "Succeeded": result.executed_events,
            "Failed": len(result.failed_events),
            "Duration": f"{result.duration:.1f} s",
            "Execution log": log_path,
            "Model": config.model_id,
            "Domain SOP": executor.config.domain_prompt_file or "(not configured)",
            "User parameters": executor.config.user_params_file or "(not configured)",
        })

    except ImportError as e:
        print_error(f"Missing dependency - {e}", fix="pip install openai pyautogui")
    except Exception as e:
        print_error(str(e))
        import traceback
        traceback.print_exc()


# ---------------------------------------------------------------------------
# Full pipeline
# ---------------------------------------------------------------------------


def run_full_flow():
    print_step_header(
        "Full Pipeline",
        "Automatically chains the four stages Observe -> Reason -> Configure -> "
        "Execute. Good for first-time use or running the whole flow quickly.",
    )

    # Phase 1: Observe
    console.print("[bold]Stage 1/4: AI Observe[/]")
    session_dir = run_observe()
    if not session_dir:
        print_error("Observe stage aborted; pipeline stopped")
        return

    # Phase 2: Reason
    console.print("\n[bold]Stage 2/4: AI Reason[/]")
    session_dir = run_reason(session_dir)
    if not session_dir:
        print_error("Reason stage failed; pipeline stopped")
        return

    # Phase 3: Configure
    console.print("\n[bold]Stage 3/4: Customize[/]")
    run_edit_prompts(session_dir)

    # Phase 4: Execute
    if confirm("Configuration done. Start execution?"):
        console.print("\n[bold]Stage 4/4: AI Execute[/]")
        run_execute(session_dir)
    else:
        console.print("  [dim]Execution stage skipped[/]")


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------


def main():
    os.system("cls" if os.name == "nt" else "clear")
    print_banner()

    # Check key dependencies on first launch.
    try:
        __import__("pyautogui")
        __import__("openai")
    except ImportError:
        console.print("  [yellow]Key dependencies are missing; running the environment check...[/]\n")
        run_env_check()
        console.print()

    while True:
        sessions = find_sessions()
        recommendation = get_recommendation(sessions)
        choice = main_menu(recommendation)

        dispatch = {
            "observe": run_observe,
            "reason": run_reason,
            "configure": run_edit_prompts,
            "execute": run_execute,
            "full_flow": run_full_flow,
            "env_check": run_env_check,
        }

        if choice == "exit":
            console.print("  [dim]Goodbye![/]")
            sys.exit(0)

        handler = dispatch.get(choice)
        if handler:
            try:
                handler()
            except EscapePressed:
                console.print("\n  [dim]Returned to the main menu[/]")
        else:
            print_error("Invalid option")

        console.print()
        try:
            input("  Press Enter to return to the main menu...")
        except (KeyboardInterrupt, EOFError):
            pass
        os.system("cls" if os.name == "nt" else "clear")
        print_banner()


if __name__ == "__main__":
    main()
