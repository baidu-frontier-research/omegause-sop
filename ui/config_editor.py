import re

import questionary

from ui.menu import MENU_STYLE
from ui.theme import console, print_success


def parse_markdown_table(path: str) -> tuple[list[str], list[dict]]:
    with open(path, "r", encoding="utf-8") as f:
        lines = [line.rstrip() for line in f.readlines() if line.strip()]

    if len(lines) < 2:
        return [], []

    headers = [h.strip() for h in lines[0].strip().strip("|").split("|")]
    rows = []
    for line in lines[2:]:
        if not line.strip() or re.match(r"^\|[-\s|]+\|$", line):
            continue
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        if len(cells) == len(headers):
            rows.append(dict(zip(headers, cells)))
    return headers, rows


def write_markdown_table(path: str, headers: list[str], rows: list[dict]):
    if not rows or not headers:
        return
    col_widths = {h: max(len(h), max((len(row.get(h, "")) for row in rows), default=0)) for h in headers}
    lines = []
    lines.append("| " + " | ".join(h.ljust(col_widths[h]) for h in headers) + " |")
    lines.append("|" + "|".join("-" * (col_widths[h] + 2) for h in headers) + "|")
    for row in rows:
        lines.append("| " + " | ".join(row.get(h, "").ljust(col_widths[h]) for h in headers) + " |")
    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")


def edit_params(params_path: str):
    console.print("[bold]编辑用户参数[/] (按 Enter 保留当前值，输入新值覆盖)\n")

    headers, rows = parse_markdown_table(params_path)
    if not rows:
        console.print("[yellow]params.md 为空或格式不正确，跳过编辑[/]")
        return

    value_key = headers[-1] if headers else "值"

    for row in rows:
        category = row.get(headers[0], "") if headers else ""
        param_name = row.get(headers[1], "") if len(headers) > 1 else ""
        current_val = row.get(value_key, "")

        new_val = questionary.text(
            f"  {category} / {param_name}:",
            default=current_val,
            style=MENU_STYLE,
        ).ask()

        if new_val is not None:
            row[value_key] = new_val.strip()

    write_markdown_table(params_path, headers, rows)
    print_success("params.md 已更新")


def parse_domain_rules(path: str) -> list[str]:
    with open(path, "r", encoding="utf-8") as f:
        content = f.read()
    rules = re.split(r"\n(?=\d+\.)", content.strip())
    return [r.strip() for r in rules if r.strip()]


def write_domain_rules(path: str, rules: list[str]):
    renumbered = []
    for i, rule in enumerate(rules):
        text = re.sub(r"^\d+\.\s*", "", rule)
        renumbered.append(f"{i + 1}. {text}")
    with open(path, "w", encoding="utf-8") as f:
        f.write("\n\n".join(renumbered) + "\n")


def edit_domain(domain_path: str):
    console.print("[bold]编辑领域 SOP 规则[/]\n")

    rules = parse_domain_rules(domain_path)
    if not rules:
        console.print("[yellow]domain.md 为空，跳过编辑[/]")
        return

    updated = []
    for i, rule in enumerate(rules):
        preview = rule[:80] + "..." if len(rule) > 80 else rule
        console.print(f"  [cyan]规则 {i + 1}:[/] {preview}")

        action = questionary.select(
            "  操作:",
            choices=["保留原样", "编辑修改", "删除此规则"],
            style=MENU_STYLE,
        ).ask()

        if action == "保留原样":
            updated.append(rule)
        elif action == "编辑修改":
            new_rule = questionary.text("  新内容:", default=rule, style=MENU_STYLE).ask()
            if new_rule:
                updated.append(new_rule.strip())
        console.print()

    while True:
        add = questionary.confirm("  添加新规则？", default=False, style=MENU_STYLE).ask()
        if not add:
            break
        new_rule = questionary.text("  新规则内容:", style=MENU_STYLE).ask()
        if new_rule:
            updated.append(new_rule.strip())

    write_domain_rules(domain_path, updated)
    print_success("domain.md 已更新")
