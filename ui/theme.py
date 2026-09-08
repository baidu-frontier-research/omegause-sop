from rich.console import Console
from rich.panel import Panel
from rich.theme import Theme

CUSTOM_THEME = Theme({
    "step.complete": "bold green",
    "step.pending": "bold yellow",
    "step.error": "bold red",
    "menu.key": "bold cyan",
    "menu.desc": "dim white",
    "flow.arrow": "bold blue",
    "banner.title": "bold magenta",
})

console = Console(theme=CUSTOM_THEME)


def print_error(message: str, fix: str = ""):
    console.print(f"  [bold red]错误:[/] {message}")
    if fix:
        console.print(f"  [dim]修复建议:[/] {fix}")


def print_warning(message: str):
    console.print(f"  [bold yellow]警告:[/] {message}")


def print_success(message: str):
    console.print(f"  [bold green]完成:[/] {message}")


def print_step_header(title: str, description: str):
    panel = Panel(
        f"[bold]{title}[/]\n[dim]{description}[/]",
        border_style="cyan",
        padding=(0, 2),
    )
    console.print()
    console.print(panel)
    console.print()


def print_step_log(title: str, info: dict, next_step: str = ""):
    console.print()
    console.rule(f"[bold]{title}[/]", style="green")
    for k, v in info.items():
        console.print(f"  [cyan]{k}:[/] {v}")
    if next_step:
        console.print()
        console.print(f"  [bold green]>>> 下一步:[/] {next_step}")
    console.rule(style="green")
    console.print()
