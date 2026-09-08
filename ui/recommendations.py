import os


def get_recommendation(sessions: list[dict]) -> str:
    if not sessions:
        return "创建你的第一个录制 -> 选择 AI 观察"

    latest = max(sessions, key=lambda s: os.path.getmtime(s["dir"]), default=None)
    if not latest:
        return ""

    name = latest["name"]

    if not latest["has_recording"]:
        return f"会话 '{name}' 需要录制 -> 选择 AI 观察"

    if not latest["has_prompt"]:
        return f"会话 '{name}' 已录制({latest['event_count']}事件)，推荐运行 AI 推理"

    if not latest["has_domain"] and not latest["has_params"]:
        return f"会话 '{name}' 已推理，推荐配置领域规则和参数"

    if latest["has_prompt"]:
        return f"会话 '{name}' 已就绪，可以执行 -> 选择 AI 执行"

    return "所有会话已完成，可以创建新录制"
