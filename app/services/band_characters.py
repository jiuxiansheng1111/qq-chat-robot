"""MyGO!!!!! / Ave Mujica 角色与用户提供的 RVC 模型对应关系。"""

BAND_CHARACTERS = (
    ("mygo_tomori", "高松灯", ("高松燈",), "mygo_deng.pth", "mygo_deng.index"),
    ("mygo_anon", "千早爱音", ("爱音", "千早愛音"), "mygo_aiyi.pth", "mygo_aiyi.index"),
    ("mygo_rana", "要乐奈", ("乐奈", "要楽奈"), "mygo_lenai.pth", "mygo_lenai.index"),
    ("mygo_soyo", "长崎素世", ("素世", "长崎爽世", "長崎そよ"), "mygo_sushi.pth", "mygo_sushi.index"),
    ("mygo_taki", "椎名立希", ("立希",), "mygo_lixi.pth", "mygo_lixi.index"),
    ("mujica_uika", "三角初华", ("初华", "三角初華"), "mujica_chuhua.pth", ""),
    ("mujica_mutsumi", "若叶睦", ("若葉睦", "小睦"), "mujica_nuoyemu.pth", ""),
    ("mujica_umiri", "八幡海铃", ("海铃", "海玲", "八幡海鈴"), "mujica_hailin.pth", ""),
    ("mujica_nyamu", "祐天寺若麦", ("若麦", "祐天寺若麥", "祐天寺にゃむ"), "mujica_miaomeng.pth", ""),
    ("mujica_sakiko", "丰川祥子", ("祥子", "豊川祥子"), "mujica_xiangzi.pth", ""),
)


def band_profiles(rvc_root: str) -> dict[str, dict]:
    """仅生成配置；模型存在性由安装脚本和运行时分别检查。"""
    from pathlib import Path

    root = Path(rvc_root)
    return {
        profile_id: {
            "label": label,
            "languages": "文字聊天 / 翻唱",
            "tts_enabled": False,
            "singing_provider": "rvc",
            "rvc_model_path": str(root / "assets" / "weights" / model),
            "rvc_index_path": str(root / "assets" / "indices" / index) if index else "",
            "rvc_index_rate": 0.75 if index else 0.0,
            "rvc_speaker_id": 0,
        }
        for profile_id, label, _, model, index in BAND_CHARACTERS
    }
