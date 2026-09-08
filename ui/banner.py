from rich.panel import Panel
from rich.text import Text

from ui.theme import console


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
        title="[banner.title]大模型软件调用智能体[/]",
        border_style="blue",
        padding=(1, 4),
    )
    console.print()
    console.print(panel)
