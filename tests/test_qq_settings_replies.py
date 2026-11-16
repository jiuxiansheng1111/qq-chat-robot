import pytest

from app.astrbot_settings_reply import is_simple_setting_success, setting_ack


@pytest.mark.parametrize(("command", "reply", "simplify"), [
    ("设置模板 modern", "✅ 报告模板已设置为: modern", True),
    ("设置格式 图片", "✅ 输出格式已设置为: image", True),
    ("陪伴群 开启观察", "已开启群聊观察", True),
    ("设置模板 missing", "没有找到模板 missing", False),
    ("陪伴群 开启观察", "已开启，但配置保存失败", False),
    ("设置模板 modern", "需要管理员权限", False),
    ("查看模板", "当前模板已设置为 modern，其他可用模板如下", False),
    ("这个设置是什么意思", "设置成功以后可以使用", False),
])
def test_only_successful_setting_actions_are_simplified(command, reply, simplify):
    assert is_simple_setting_success(command, reply) is simplify


def test_qq_receipt_keeps_availability_notice_and_other_platform_receipt():
    assert setting_ack({}, "旧的长确认") == "好的，我知道了"
    assert setting_ack({}, "旧的长确认", notice="这个音色暂时还不能朗读") == "好的，我知道了\n这个音色暂时还不能朗读"
    assert setting_ack({"wechat_private": False}, "微信原确认") == "微信原确认"
