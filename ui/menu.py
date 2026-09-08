import questionary
from questionary import Choice, Style

from ui.theme import console


class EscapePressed(Exception):
    """用户按下 ESC，返回主菜单。"""


MENU_STYLE = Style([
    ("qmark", "fg:cyan bold"),
    ("question", "bold"),
    ("pointer", "fg:cyan bold"),
    ("highlighted", "fg:cyan bold"),
    ("selected", "fg:green"),
])

MENU_CHOICES = [
    Choice(title="AI 观察      录制鼠标和键盘操作", value="observe"),
    Choice(title="AI 推理      分析录制内容生成操作描述", value="reason"),
    Choice(title="用户定制     编辑领域SOP和用户参数", value="configure"),
    Choice(title="AI 执行      根据描述重新执行操作", value="execute"),
    Choice(title="一键全流程   Observe -> Reason -> Configure -> Execute", value="full_flow"),
    Choice(title="环境检查     检查依赖和配置", value="env_check"),
    Choice(title="退出", value="exit"),
]


def select_with_escape(message: str, choices, **kwargs):
    """questionary.select 包装：补上 Esc 取消（返回 None）。

    questionary 的 select 默认只绑定 Ctrl+C，不响应 Esc；这里给底层
    prompt_toolkit Application 补一个 Esc 绑定，让 Esc 与 Ctrl+C 一样取消。
    非 eager 绑定不会影响方向键（方向键是以 Esc 开头的转义序列）。
    """
    question = questionary.select(message, choices=choices, style=MENU_STYLE, **kwargs)

    app = question.application
    app.timeoutlen = 0.1  # 将 Esc 等待时间从默认 1s 降到 100ms

    @app.key_bindings.add("escape")
    def _(event):
        event.app.exit(result=None)

    return question.ask()


def main_menu(recommendation: str = "") -> str:
    if recommendation:
        console.print(f"\n  [bold green]>>> 推荐下一步:[/] {recommendation}\n")

    choice = select_with_escape(
        "请选择操作 (方向键选择, Enter确认, Esc退出):",
        choices=MENU_CHOICES,
        use_arrow_keys=True,
        use_shortcuts=False,
    )

    return choice or "exit"


def confirm(question: str, default: bool = True) -> bool:
    result = questionary.confirm(question, default=default, style=MENU_STYLE).ask()
    if result is None:
        raise EscapePressed()
    return result


def text_input(label: str, default: str = "") -> str:
    result = questionary.text(label, default=default, style=MENU_STYLE).ask()
    if result is None:
        raise EscapePressed()
    return result.strip()
