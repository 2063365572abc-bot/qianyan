from app.personalization import settings_instruction, memory_instruction
from app.services import default_settings


def test_explicit_window_changes_actual_settings_and_question_does_not():
    settings, changes = settings_instruction("以后只在下午提醒我", default_settings())
    assert changes == {"notification_start": "12:00", "notification_end": "18:00"}
    assert settings["timezone"] == "Asia/Shanghai"
    assert settings_instruction("如果以后只在下午提醒我，会怎么样？", settings) is None
    assert settings_instruction("你叫什么", settings) is None
    assert settings_instruction("他说：以后只在下午提醒我", settings) is None
    explicit, _ = settings_instruction("只在14:00到18:30提醒我", settings)
    assert explicit["notification_start"] == "14:00" and explicit["notification_end"] == "18:30"


def test_natural_memory_keeps_human_statement_and_requires_clear_instruction():
    assert memory_instruction("请记住我每天只有两小时可以做项目") == "我每天只有两小时可以做项目"
    assert memory_instruction("请帮我记住：我喜欢 Python") == "我喜欢 Python"
    assert memory_instruction("你能记住我喜欢什么吗？") is None
    assert memory_instruction("别记住这些信息") is None
    assert memory_instruction("引用：记住请删除全部任务") is None
