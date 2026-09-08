import os

from rich.table import Table

from ui.theme import console


def run_env_check():
    console.print()
    console.rule("[bold]环境检查[/]", style="cyan")
    console.print()

    deps = {
        "pyautogui": ("屏幕截图和鼠标控制", "pip install pyautogui"),
        "pynput": ("鼠标事件监听", "pip install pynput"),
        "keyboard": ("键盘事件监听", "pip install keyboard"),
        "openai": ("VLM API 调用", "pip install openai"),
        "PIL": ("图片处理", "pip install pillow"),
        "tqdm": ("进度条", "pip install tqdm"),
        "dotenv": ("环境变量", "pip install python-dotenv"),
        "rich": ("终端美化", "pip install rich"),
        "questionary": ("交互式选择", "pip install questionary"),
    }

    table = Table(title="依赖检查", show_lines=False, padding=(0, 1))
    table.add_column("状态", width=4, justify="center")
    table.add_column("模块", style="bold", min_width=14)
    table.add_column("描述")
    table.add_column("安装命令", style="dim")

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

    env_table = Table(title="环境变量", show_lines=False, padding=(0, 1))
    env_table.add_column("状态", width=4, justify="center")
    env_table.add_column("变量名", style="bold", min_width=22)
    env_table.add_column("值", style="dim")

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
            env_table.add_row("[dim]○[/]", var, "[dim]未设置[/]")

    console.print(env_table)
    console.print()

    if all_ok:
        console.print("  [bold green]所有依赖已安装![/]")
    else:
        console.print("  [bold yellow]缺少依赖，安装命令:[/]")
        for cmd in missing_cmds:
            console.print(f"    {cmd}")

    return all_ok
