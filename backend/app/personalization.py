"""Apply only clear, whole-message personal instructions from the human record."""
import re
from .schemas import ButlerSettings


def settings_instruction(text, current):
    text = text.strip().rstrip("。！!.")
    change = {}
    match = re.fullmatch(r"(?:请)?(?:以后)?只在(下午|晚上|上午)提醒我", text)
    if match:
        start, end = {"下午": ("12:00", "18:00"), "晚上": ("18:00", "22:00"), "上午": ("08:00", "12:00")}[match[1]]
        change = {"notification_start": start, "notification_end": end}
    match = re.fullmatch(r"(?:请)?(?:以后)?只在(\d{1,2}:\d{2})(?:到|至|[-～~])(\d{1,2}:\d{2})提醒我", text)
    if match:
        change = {"notification_start": match[1].zfill(5), "notification_end": match[2].zfill(5)}
    match = re.fullmatch(r"(?:请)?(?:以后)?(?:回复|回答)(?:要|请)?(简短|简洁|温暖|详细)(?:一点|一些)?", text)
    if match:
        change = {"style": {"简短": "concise", "简洁": "concise", "温暖": "warm", "详细": "detailed"}[match[1]]}
    match = re.fullmatch(r"(?:以后你叫|以后叫你|就叫你)([^，。！？\n]{1,40})", text)
    if match:
        change = {"name": match[1].strip()}
    if not change:
        return None
    return ButlerSettings.model_validate({**current, **change}).model_dump(), change


def memory_instruction(text):
    # Do not interpret quoted requests, questions or a negative as authorization.
    match = re.fullmatch(r"(?:请)?(?:帮我)?记住[：:，, ]?(.+)", text.strip(), re.S)
    if not match or text.strip().endswith(("?", "？")):
        return None
    return match[1].strip()
