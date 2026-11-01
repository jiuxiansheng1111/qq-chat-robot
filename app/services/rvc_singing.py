"""校验按角色选择的 RVC 翻唱模型，不在机器人进程加载权重。"""
from app.config import Settings
from app.services.rvc import RVCProfile, configured_rvc_profile
from app.services.voice import voice_profiles


def rvc_profile(settings: Settings, profile_id: str) -> RVCProfile | None:
    profile = voice_profiles(settings).get(profile_id, {})
    if profile.get("singing_provider") != "rvc":
        return None
    return configured_rvc_profile(settings, profile)
