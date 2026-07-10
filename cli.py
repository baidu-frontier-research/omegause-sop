#!/usr/bin/env python
# coding=utf-8
"""
桌面操作自动化工具 - CLI 向导

流程: 观察(Observe) → 推理(Reason) → 配置(Configure) → 执行(Execute)

所有文件围绕会话目录组织:
  sop/{app_name}/{session_name}/
    ├── screenshots/       # observe 产出
    ├── clicked_boxes/     # observe 产出
    ├── recording.json     # observe 产出
    ├── prompt.json        # reason 产出
    ├── domain.md          # 用户编辑（可选，execute 读取）
    ├── params.md          # 用户编辑（可选，execute 读取）
    ├── replay_logs/       # execute 执行日志
    └── replay_temp/       # execute 临时截图

用法: python cli.py
"""

import glob
import json
import os
import sys
import time
from datetime import datetime

from dotenv import load_dotenv

load_dotenv()

from ui import (
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
# 会话目录管理
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
            info = _scan_session_dir(session_dir, session_name, app_name="(旧录制)")
            if info:
                results.append(info)

    # 按目录最后修改时间倒序（最近的在最上），与表格“最后修改”列一致
    results.sort(key=lambda r: r.get("mtime", 0.0), reverse=True)
    return results


# ---------------------------------------------------------------------------
# Step 1: AI 观察（录制）
# ---------------------------------------------------------------------------


def run_observe() -> str | None:
    print_step_header(
        "AI 观察",
        "录制你的鼠标和键盘操作。AI 将保存截图和事件序列，作为后续推理和执行的输入数据。",
    )

    app_name = text_input("应用名称（如 pvsyst, excel 等）")
    if not app_name:
        print_error("应用名称不能为空")
        return None

    session_name = text_input("会话名称（留空自动生成）")
    if not session_name:
        session_name = datetime.now().strftime("%Y%m%d_%H%M%S")

    output_dir = os.path.join("sop", app_name)
    session_dir = os.path.join(output_dir, session_name)

    while os.path.isdir(session_dir) and os.listdir(session_dir):
        print_warning(f"会话 '{session_name}' 已存在且包含文件，不能覆盖")
        session_name = text_input("请输入新的会话名称（留空自动生成）")
        if not session_name:
            session_name = datetime.now().strftime("%Y%m%d_%H%M%S")
        session_dir = os.path.join(output_dir, session_name)

    console.print(f"  会话目录: [bold]{session_dir}[/]")
    console.print()
    console.print("  [bold yellow]!! 按 Ctrl+Alt+R 停止录制 !![/]")
    console.print()

    if not confirm("准备好了吗？开始录制"):
        return None

    console.print("  正在启动录制...")

    try:
        from agents.observe.observer import Observer

        observer = Observer(output_dir=output_dir)
        result = observer.start_session(session_name)
        console.print(f"  {result['message']}")
        console.print()
        console.print("  [bold green][录制中][/] 请正常操作电脑，完成后按 Ctrl+Alt+R 停止")
        console.print()

        observer._stop_event.wait()
        result = observer.stop_session()

        console.print()
        print_success(result["message"])

        print_step_log("AI 观察完成", {
            "会话目录": session_dir,
            "录制文件": os.path.join(session_dir, "recording.json"),
            "截图目录": os.path.join(session_dir, "screenshots"),
            "事件数": result.get("summary", {}).get("total_events", "?"),
        }, next_step="运行 AI 推理，将录制内容转为操作描述")
        return session_dir

    except ImportError as e:
        print_error(f"缺少依赖 - {e}", fix="pip install pynput keyboard pyautogui")
        return None
    except Exception as e:
        print_error(str(e))
        return None


# ---------------------------------------------------------------------------
# Step 2: AI 推理
# ---------------------------------------------------------------------------


def run_reason(session_dir: str | None = None) -> str | None:
    print_step_header(
        "AI 推理",
        "AI 分析录制截图，为每个操作步骤生成语义描述，作为执行阶段的输入指令。",
    )

    if not session_dir:
        sessions = find_sessions()
        session_dir = select_session_interactive(sessions, "AI 推理")
        if not session_dir:
            return None

    recording_path = os.path.join(session_dir, "recording.json")
    if not os.path.exists(recording_path):
        old_files = glob.glob(os.path.join(session_dir, "mouse_recording_*.json"))
        if old_files:
            console.print("  发现旧格式文件，将复制为 recording.json")
            import shutil
            shutil.copy2(old_files[0], recording_path)
        else:
            print_error(f"{recording_path} 不存在", fix="请先运行 AI 观察 进行录制")
            return None

    show_session_status(session_dir)
    console.print()

    api_key = os.environ.get("QIANFAN_API_KEY", "")
    if not api_key:
        print_warning("未设置 QIANFAN_API_KEY")
        if not confirm("仍然继续吗？", default=False):
            return None

    model_name = os.environ.get(
        "REASON_MODEL_NAME",
        os.environ.get("ENRICHER_MODEL_NAME", "qwen3-vl-235b-a22b-instruct"),
    )
    console.print(f"  VLM 模型: [bold]{model_name}[/]")
    console.print()

    if not confirm("开始 AI 推理？"):
        return None

    console.print()

    try:
        from agents.reason.reasoner import Reasoner, ReasonerConfig

        config = ReasonerConfig()
        reasoner = Reasoner(config)
        reasoner.enrich(session_dir)

        print_step_log("AI 推理完成", {
            "输出文件": os.path.join(session_dir, "prompt.json"),
            "模型": config.model_id,
            "API": config.api_base_url,
        }, next_step="编辑配置，创建/修改 domain.md 和 params.md")
        return session_dir

    except ImportError as e:
        print_error(f"缺少依赖 - {e}", fix="pip install openai tqdm")
        return None
    except Exception as e:
        print_error(str(e))
        return None


# ---------------------------------------------------------------------------
# Step 2.5: 编辑领域 SOP 和用户参数
# ---------------------------------------------------------------------------


def _open_in_editor(file_path: str) -> None:
    """用系统默认编辑器打开文件，等待用户编辑完成后返回。"""
    import subprocess
    import platform

    abs_path = os.path.abspath(file_path)
    if platform.system() == "Darwin":
        subprocess.call(["open", "-t", "-W", abs_path])
    elif platform.system() == "Windows":
        os.startfile(abs_path)
        input("  编辑完成后按 Enter 继续...")
    else:
        editor = os.environ.get("EDITOR", "xdg-open")
        subprocess.call([editor, abs_path])


def run_edit_prompts(session_dir: str | None = None) -> str | None:
    print_step_header(
        "用户定制",
        "编辑领域规则(domain.md)和用户参数(params.md)，让执行阶段适配你的具体需求。",
    )

    if not session_dir:
        sessions = find_sessions()
        session_dir = select_session_interactive(sessions, "编辑配置")
        if not session_dir:
            return None

    show_session_status(session_dir)
    console.print()

    # Domain SOP
    domain_path = os.path.join(session_dir, "domain.md")
    if not os.path.exists(domain_path):
        template = "domain_knowledge_template/pvsyst_domain.md"
        if os.path.exists(template):
            if confirm(f"从模板 {template} 复制 domain.md？"):
                import shutil
                shutil.copy2(template, domain_path)
                print_success(f"已复制到 {domain_path}")
        else:
            if confirm("创建空的 domain.md？"):
                with open(domain_path, "w", encoding="utf-8") as f:
                    f.write("1. 在此编写领域特定的操作规则\n")
                print_success(f"已创建 {domain_path}")

    if os.path.exists(domain_path):
        console.print(f"  [bold]domain.md[/]: {domain_path}")
        if confirm("打开 domain.md 进行编辑？"):
            _open_in_editor(domain_path)
            print_success("domain.md 编辑完成")

    console.print()

    # User Params
    params_path = os.path.join(session_dir, "params.md")
    if not os.path.exists(params_path):
        template = "domain_knowledge_template/pvsyst_params.md"
        if os.path.exists(template):
            if confirm(f"从模板 {template} 复制 params.md？"):
                import shutil
                shutil.copy2(template, params_path)
                print_success(f"已复制到 {params_path}")
        else:
            if confirm("创建空的 params.md？"):
                with open(params_path, "w", encoding="utf-8") as f:
                    f.write("| 分类 | 参数名 | 值 |\n|------|--------|-----|\n")
                print_success(f"已创建 {params_path}")

    if os.path.exists(params_path):
        console.print(f"  [bold]params.md[/]: {params_path}")
        if confirm("打开 params.md 进行编辑？"):
            _open_in_editor(params_path)
            print_success("params.md 编辑完成")

    console.print()
    print_step_log("配置编辑", {
        "领域 SOP": domain_path + (" (已存在)" if os.path.exists(domain_path) else " (待创建)"),
        "用户参数": params_path + (" (已存在)" if os.path.exists(params_path) else " (待创建)"),
    }, next_step="运行 AI 执行")
    return session_dir


# ---------------------------------------------------------------------------
# Step 3: AI 执行
# ---------------------------------------------------------------------------


def run_execute(session_dir: str | None = None) -> None:
    print_step_header(
        "AI 执行",
        "AI 根据推理描述自动操作桌面，逐步重现录制的操作流程。执行期间请不要移动鼠标。",
    )

    if not session_dir:
        sessions = find_sessions()
        session_dir = select_session_interactive(sessions, "AI 执行")
        if not session_dir:
            return

    prompt_path = os.path.join(session_dir, "prompt.json")
    if not os.path.exists(prompt_path):
        print_error(f"{prompt_path} 不存在", fix="请先运行 AI 推理")
        return

    show_session_status(session_dir)
    console.print()

    with open(prompt_path, "r", encoding="utf-8") as f:
        data = json.load(f)
    events = data.get("events", [])
    console.print(f"  共 [bold]{len(events)}[/] 个操作步骤")

    missing = sum(1 for e in events if not e.get("prompt"))
    if missing > 0:
        print_warning(f"{missing} 个步骤缺少描述，建议先运行 AI 推理")

    domain_path = os.path.join(session_dir, "domain.md")
    params_path = os.path.join(session_dir, "params.md")
    if os.path.exists(domain_path):
        console.print(f"  领域 SOP: [green]{domain_path}[/]")
    else:
        console.print("  领域 SOP: [dim]未配置（使用通用规则）[/]")
    if os.path.exists(params_path):
        console.print(f"  用户参数: [green]{params_path}[/]")
    else:
        console.print("  用户参数: [dim]未配置[/]")

    console.print()
    console.print("  [bold yellow]执行即将开始！请不要移动鼠标。[/]")

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

        print_step_log("AI 执行完成", {
            "总步骤": result.total_events,
            "成功": result.executed_events,
            "失败": len(result.failed_events),
            "耗时": f"{result.duration:.1f} 秒",
            "执行日志": log_path,
            "模型": config.model_id,
            "领域SOP": executor.config.domain_prompt_file or "(未配置)",
            "用户参数": executor.config.user_params_file or "(未配置)",
        })

    except ImportError as e:
        print_error(f"缺少依赖 - {e}", fix="pip install openai pyautogui")
    except Exception as e:
        print_error(str(e))
        import traceback
        traceback.print_exc()


# ---------------------------------------------------------------------------
# 一键全流程
# ---------------------------------------------------------------------------


def run_full_flow():
    print_step_header(
        "一键全流程",
        "自动串联 观察 -> 推理 -> 配置 -> 执行 四个阶段，适合首次使用或快速完成完整流程。",
    )

    # Phase 1: Observe
    console.print("[bold]阶段 1/4: AI 观察[/]")
    session_dir = run_observe()
    if not session_dir:
        print_error("观察阶段中止，全流程终止")
        return

    # Phase 2: Reason
    console.print("\n[bold]阶段 2/4: AI 推理[/]")
    session_dir = run_reason(session_dir)
    if not session_dir:
        print_error("推理阶段失败，全流程终止")
        return

    # Phase 3: Configure
    console.print("\n[bold]阶段 3/4: 配置定制[/]")
    run_edit_prompts(session_dir)

    # Phase 4: Execute
    if confirm("配置完成，开始执行？"):
        console.print("\n[bold]阶段 4/4: AI 执行[/]")
        run_execute(session_dir)
    else:
        console.print("  [dim]已跳过执行阶段[/]")


# ---------------------------------------------------------------------------
# 主入口
# ---------------------------------------------------------------------------


def main():
    os.system("cls" if os.name == "nt" else "clear")
    print_banner()

    # 首次启动检查关键依赖
    try:
        __import__("pyautogui")
        __import__("openai")
    except ImportError:
        console.print("  [yellow]检测到缺少关键依赖，自动运行环境检查...[/]\n")
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
            console.print("  [dim]再见![/]")
            sys.exit(0)

        handler = dispatch.get(choice)
        if handler:
            try:
                handler()
            except EscapePressed:
                console.print("\n  [dim]已返回主菜单[/]")
        else:
            print_error("无效选项")

        console.print()
        try:
            input("  按 Enter 返回主菜单...")
        except (KeyboardInterrupt, EOFError):
            pass
        os.system("cls" if os.name == "nt" else "clear")
        print_banner()


if __name__ == "__main__":
    main()
