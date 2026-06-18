#!/usr/bin/env python3
"""Claude Code status line: model | effort | context usage.

Reads the session JSON from stdin (see https://code.claude.com/docs/en/statusline)
and prints a single status line. Kept dependency-free (stdlib only) and
cross-platform; invoked relative to the project root as `python .claude/statusline.py`
so it travels with the repo to any machine (no absolute/user-specific path).
"""
import sys
import json


def main():
    # Block/middle-dot glyphs need UTF-8 stdout (Windows defaults to cp1252).
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass

    try:
        data = json.load(sys.stdin)
    except Exception:
        print("")
        return

    RESET, DIM = "\033[0m", "\033[2m"
    GREEN, YELLOW, RED = "\033[32m", "\033[33m", "\033[31m"

    model = (data.get("model") or {}).get("display_name") or "?"
    effort = (data.get("effort") or {}).get("level")  # absent if model has no effort param

    cw = data.get("context_window") or {}
    pct = cw.get("used_percentage")
    pct = int(pct) if pct is not None else 0
    used = (cw.get("total_input_tokens") or 0) + (cw.get("total_output_tokens") or 0)
    size = cw.get("context_window_size") or 0

    color = RED if pct >= 80 else YELLOW if pct >= 50 else GREEN
    width = 10
    filled = max(0, min(width, round(pct / 100 * width)))
    bar = "█" * filled + "░" * (width - filled)

    def k(n):
        return f"{n / 1000:.0f}k" if n >= 1000 else str(n)

    sep = f" {DIM}·{RESET} "
    parts = [model]
    if effort:
        parts.append(f"{DIM}effort{RESET} {effort}")
    ctx = f"{DIM}ctx{RESET} {color}{bar} {pct}%{RESET}"
    if size:
        ctx += f" {DIM}{k(used)}/{k(size)}{RESET}"
    parts.append(ctx)

    # Add Plan usage (five_hour or seven_day rate limits)
    rl = data.get("rate_limits") or {}
    plan_info = rl.get("five_hour") or rl.get("seven_day") or {}
    plan_pct = plan_info.get("used_percentage")
    if plan_pct is not None:
        plan_pct_val = int(plan_pct)
        plan_color = RED if plan_pct_val >= 80 else YELLOW if plan_pct_val >= 50 else GREEN
        plan_filled = max(0, min(width, round(plan_pct_val / 100 * width)))
        plan_bar = "█" * plan_filled + "░" * (width - plan_filled)
        plan_str = f"{DIM}plan{RESET} {plan_color}{plan_bar} {plan_pct_val}%{RESET}"
        parts.append(plan_str)

    print(sep.join(parts))


if __name__ == "__main__":
    main()
