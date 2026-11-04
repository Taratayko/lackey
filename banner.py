"""
Приветственный экран при запуске бота — ascii-арт + инфо о проекте,
как motd в линуксе.
"""
from rich.console import Console, Group
from rich.text import Text
from rich.columns import Columns

LOGO_LINES = [
    "  .d8888b.  .d88888b. 888b     d888888b     d888 ",
    " d88P  Y88bd88P\" \"Y88b8888b   d88888888b   d8888 ",
    " 888    888888     88888888b.d8888888888b.d88888 ",
    " 888       888     888888Y88888P888888Y88888P888 ",
    " 888       888     888888 Y888P 888888 Y888P 888 ",
    " 888    888888     888888  Y8P  888888  Y8P  888 ",
    " Y88b  d88PY88b. .d88P888   \"   888888   \"   888 ",
    "  \"Y8888P\"  \"Y88888P\" 888       888888       888 ",
]

PROJECT_NAME = "Commune Mesh's"
PROJECT_ALIAS = "Lackey"
PROJECT_DESCRIPTION = "Я межчатовый бот-почтальон и помощник!"
AUTHOR = "Митя Таратайко t.me/thetrtk"
STACK = "Python 3.13 · aiogram 3 · MongoDB"


def print_banner(version: str = "MARATHON") -> None:
    """Печатает ascii-арт (слева) и информацию о проекте (справа) перед запуском бота."""
    console = Console()
    console.width = max(console.size.width, 100)
    logo = Text("\n".join(LOGO_LINES), style="bright_black")
    info_lines = [
        Text(""),
        Text.assemble((PROJECT_NAME, "bold white"), (f" {PROJECT_ALIAS}", "green1")),
        Text(PROJECT_DESCRIPTION, style="dim white"),
        Text(""),
        Text.assemble(("Автор:     ", "bold"), (AUTHOR, "light_coral")),
        Text.assemble(("Версия:    ", "bold"), (version, "red")),
        Text.assemble(("Стек:      ", "bold"), (STACK, "dim cyan")),
    ]
    info = Group(*info_lines)
    console.print()
    console.print(Columns([logo, info], padding=(0, 4), equal=False, expand=False))
    console.print()
