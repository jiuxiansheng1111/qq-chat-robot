import json
from functools import lru_cache
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    app_env: str = "development"
    log_level: str = "INFO"
    database_path: str = "./data/qqchat.db"
    redis_url: str = ""
    event_dedupe_ttl_seconds: int = 300
    # 给所有群消息设较高的入口限流阈值。
    ingress_user_rate_limit_per_minute: int = 60
    ingress_group_rate_limit_per_minute: int = 300
    # 单独使用这些键，避免旧 .env 中的 USER_RATE_LIMIT_PER_MINUTE=5
    # 继续把普通 AI 对话限制在旧的低阈值。
    llm_user_rate_limit_per_minute: int = 20
    llm_group_rate_limit_per_minute: int = 120
    rate_limit_notice_cooldown_seconds: int = 10
    # 兼容旧 .env 配置，但旧键不再控制运行时限流。
    user_rate_limit_per_minute: int = 5
    group_rate_limit_per_minute: int = 60
    plugin_modules: str = "app.plugins.hello"

    jwt_secret_key: str = Field(default="change-me", repr=False)
    jwt_issuer: str = "qqchat-robot"
    jwt_audience: str = "qqchat-admin"
    access_token_expire_minutes: int = 20
    refresh_token_expire_days: int = 14
    admin_username: str = "admin"
    admin_password: str = Field(default="change-me-now", repr=False)

    onebot_api_base: str = ""
    onebot_access_token: str = Field(default="", repr=False)
    onebot_webhook_token: str = Field(default="", repr=False)
    onebot_self_id: str = ""
    # 可选的第二个 NapCat / QQ 账号。留空时仍按单机器人运行。
    onebot_api_base_2: str = ""
    onebot_access_token_2: str = Field(default="", repr=False)
    onebot_webhook_token_2: str = Field(default="", repr=False)
    onebot_self_id_2: str = ""

    llm_provider: str = "zhipu"
    llm_model: str = "glm-4.7-flash"
    llm_base_url: str = "https://open.bigmodel.cn/api/paas/v4"
    llm_api_key: str = Field(default="", repr=False)
    llm_fallback_provider: str = "groq"
    groq_base_url: str = "https://api.groq.com/openai/v1"
    groq_model: str = "openai/gpt-oss-20b"
    groq_api_key: str = Field(default="", repr=False)
    llm_timeout_seconds: float = 45
    llm_max_output_tokens: int = 3000
    llm_temperature: float = 0.7
    llm_max_concurrency: int = 10
    llm_queue_size: int = 30
    llm_queue_timeout_seconds: float = 8
    auto_web_search_enabled: bool = True
    auto_web_search_limit: int = 5
    # 可选出站代理，用于网页搜索和媒体访问。留空时自动探测本机
    # Clash Verge/Clash 混合端口。
    web_proxy_url: str = ""
    web_proxy_auto_detect: bool = True

    persona_name: str = "小丛雨"
    persona_prompt_file: str = "prompts/persona.txt"
    persona_examples_file: str = "prompts/examples.jsonl"
    max_context_messages: int = 100
    repeat_echo_enabled: bool = True
    group_context_history_count: int = 3000
    group_context_message_limit: int = 3000
    group_context_char_limit: int = 120000
    qq_send_chunk_chars: int = 1800
    media_max_bytes: int = 5 * 1024 * 1024
    media_timeout_seconds: float = 60
    media_retry_attempts: int = 2
    ultraman_image_resolve_timeout_seconds: float = 12
    ultraman_image_cache_dir: str = "./data/ultraman_image_cache"
    # 图片源 CDN 冷启动时可能要等几秒；解析器会在这个总超时内
    # 分阶段尝试备用来源。
    anime_image_resolve_timeout_seconds: float = 24
    anime_image_cache_dir: str = "./data/anime_image_cache"
    # 萌娘百科限制自动抓取和站外使用；未取得机器人用途所需授权前保持关闭。
    moegirl_image_provider_enabled: bool = False
    # 仅控制图片来源：开启后角色图片只使用萌娘百科，除非同时开启下方图片回退。
    # 文字档案仍可搜索其他来源并引用；缺少萌娘百科页面时不会换成不相关的角色设定。
    anime_moegirl_only: bool = False
    # 优先使用萌娘百科图片；若图片 CDN 或已核实页面图片不可用，则允许按限定目录回退。
    # 此项只影响图片。
    anime_moegirl_preferred_with_fallback: bool = False
    # 可选图片生成服务。接口响应需符合 OpenAI /v1/images/generations 格式
    # （data[].b64_json 或 data[].url）。
    image_generation_enabled: bool = False
    image_generation_api_url: str = ""
    image_generation_api_key: str = Field(default="", repr=False)
    image_generation_model: str = ""
    image_generation_size: str = "1024x1024"
    image_generation_timeout_seconds: float = 90
    image_generation_max_prompt_chars: int = 500
    image_generation_cooldown_seconds: int = 20
    # 本地丛雨图片/表情素材由用户提供。不会自动下载受版权保护的素材；把文件放进这些目录即可。
    murasame_asset_dir: str = "./data/murasame_assets"
    murasame_image_subdir: str = "images"
    murasame_emoji_subdir: str = "emotes"
    # 语音默认关闭，并按角色选择音色。每个角色可用同一配置支持多种语言。
    voice_enabled: bool = False
    voice_provider: str = "openai_compatible"
    voice_api_url: str = ""
    voice_api_key: str = Field(default="", repr=False)
    voice_model: str = ""
    voice_name: str = "alloy"
    voice_profile_default: str = "murasame"
    voice_supports_language_fields: bool = False
    # 逗号分隔的 profile ID 仅授权给配置中的主 OneBot 账号。
    # 事件缺少 self_id 或其值与 ONEBOT_SELF_ID 不同，受限音色会拒绝使用。
    voice_primary_account_profile_ids: str = ""
    # 按安全内部 ID 索引的 JSON 对象。菜单只显示角色名和支持的语言。
    voice_profiles_json: str = (
        '{"murasame":{"label":"小丛雨","voice":"murasame",'
        '"language":"zh","languages":"中 / 日 / 英","target_language":"zh",'
        '"prompt_lang":"ja","prompt_text":"",'
        '"ref_audio_path":"data/murasame_voice_dataset_ja/audio/murasame_0001.mp3"}}'
    )
    voice_timeout_seconds: float = 45
    voice_max_chars: int = 360
    voice_send_cooldown_seconds: int = 20
    # GPT-SoVITS 采样保持稳定；CPU 采样偶尔会在 HTTP 200 正常时
    # 返回几乎静音的短音频。
    voice_seed: int = 17
    voice_top_k: int = 15
    voice_temperature: float = 0.8
    voice_repetition_penalty: float = 1.35
    # 可选 JSON 映射，把 GPT-SoVITS 前端语言关联到本地 SoVITS 权重。
    # 留空时沿用单一配置/默认模型。
    voice_sovits_weights_by_language_json: str = ""
    # 可选的 profile -> language -> SoVITS 权重映射，避免不同角色误用对方的检查点。
    voice_sovits_weights_by_profile_json: str = ""
    # 可选 profile -> GPT 检查点映射。启用后，每个 profile（包括默认角色）都必须有明确路由。
    voice_gpt_weights_by_profile_json: str = ""
    daily_news_enabled: bool = True
    daily_news_hour: int = 12
    daily_news_minute: int = 0
    daily_news_timezone: str = "Asia/Shanghai"
    daily_news_group_lookback_days: int = 30
    cat_api_url: str = "https://cataas.com/cat/gif"
    cat_giphy_enabled: bool = True
    cat_giphy_page_url: str = "https://giphy.com/gifs/art-cat-HMDsITZh2SBGM"
    cat_timeout_seconds: float = 12
    cat_cache_size: int = 6
    pig_api_url: str = "https://commons.wikimedia.org/w/api.php"
    music_api_url: str = "https://api.deezer.com"
    music_timeout_seconds: float = 10
    music_search_limit: int = 8
    netease_music_api_url: str = "https://music.163.com/api/search/get"
    netease_member_enabled: bool = False
    netease_member_bridge_url: str = "http://127.0.0.1:3010"
    netease_member_token_path: str = "./data/netease/bridge-token.txt"
    # 翻唱在隔离的 CUDA 子进程中运行；日常 TTS 继续使用原配置。
    # QQ 语音留一点余量，最长 115 秒。
    singing_enabled: bool = False
    singing_hf_offline: bool = False
    singing_python: str = "./data/singing/runtime/.venv/Scripts/python.exe"
    singing_seed_root: str = "./data/singing/runtime/seed-vc"
    singing_ffmpeg_path: str = "./data/singing/runtime/bin/ffmpeg.exe"
    singing_ffprobe_path: str = "./data/singing/runtime/bin/ffprobe.exe"
    singing_use_trained_tts_reference: bool = True
    singing_prefer_recorded_reference: bool = True
    # 翻唱参考音频与日常 TTS 参考音频分开配置。审核过的翻唱录音可覆盖角色的原说话参考音频。
    singing_real_reference_profile_ids: str = "murasame"
    singing_reference_audio_by_profile_json: str = "{}"
    singing_reference_max_seconds: float = Field(default=8, ge=3, le=20)
    # 填角色 ID 时使用基础模型，原微调权重仍保留。
    singing_base_model_profile_ids: str = ""
    singing_semitone_shift_by_profile_json: str = "{}"
    singing_target_median_f0_by_profile_json: str = "{}"
    singing_vocal_target_rms: float = Field(default=0.20, ge=0.01, le=0.3)
    singing_master_gain: float = Field(default=0.93, gt=0, le=1)
    singing_chunk_seconds: float = Field(default=115, ge=5, le=115)
    singing_clip_seconds: float = Field(default=20, ge=5, le=115)
    singing_accompaniment_gain: float = Field(default=0.85, gt=0, le=1)
    singing_vocal_background_gap_db: float = Field(default=3, ge=0, le=24)
    singing_min_voiced_recall: float = Field(default=0.88, ge=0, le=1)
    singing_min_energy_recall: float = Field(default=0.90, ge=0, le=1)
    singing_max_missing_vocal_seconds: float = Field(default=1.2, ge=0, le=5)
    help_menu_background_path: str = "./assets/help-menu-background.png"
    singing_max_song_seconds: float = Field(default=600, gt=0, le=1800)
    singing_max_source_bytes: int = Field(default=104857600, gt=0, le=524288000)
    singing_model_timeout_seconds: float = Field(default=900, ge=30, le=3600)
    singing_job_timeout_seconds: float = Field(default=2400, ge=60, le=7200)
    singing_queue_size: int = Field(default=3, ge=1, le=10)
    singing_cooldown_seconds: float = Field(default=120, ge=0)
    singing_segment_pause_seconds: float = Field(default=1.5, ge=0.5, le=30)
    singing_diffusion_steps: int = Field(default=35, ge=10, le=80)
    singing_seed: int = Field(default=20261004, ge=0, le=4294967295)
    singing_repair_f0_spikes: bool = False
    singing_inference_cfg_rate: float = Field(default=0.7, ge=0, le=2)
    # 每个角色可以单独调引导强度，没填的仍用上面的值。
    singing_inference_cfg_rate_by_profile_json: str = Field(default="{}", max_length=8192)
    singing_separation_model: str = Field(default="htdemucs_ft", pattern=r"^htdemucs(_ft)?$")
    singing_max_pitch_error_cents: float = Field(default=100, gt=0, le=200)
    singing_min_voice_similarity: float = Field(default=0.35, ge=0, le=1)
    bilibili_search_url: str = "https://api.bilibili.com/x/web-interface/search/type"
    bilibili_timeout_seconds: float = 10
    bilibili_search_result_limit: int = 20
    possession_style_history_count: int = 200
    possession_style_sample_limit: int = 200
    possession_style_refresh_hours: int = 24
    possession_recall_history_count: int = 200
    possession_recall_result_limit: int = 12

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    def persona_prompt(self) -> str:
        path = Path(self.persona_prompt_file)
        prompt = path.read_text(encoding="utf-8").strip() if path.exists() else f"你是{self.persona_name}，请用自然、简短、友善的中文回答。"
        examples_path = Path(self.persona_examples_file)
        if examples_path.exists():
            examples = []
            for line in examples_path.read_text(encoding="utf-8").splitlines():
                try:
                    item = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if isinstance(item, dict) and isinstance(item.get("messages"), list):
                    examples.append(json.dumps(item["messages"], ensure_ascii=False))
            if examples:
                prompt += "\n\n以下是语气示例，只模仿表达方式，不要编造示例中的事实：\n" + "\n".join(examples[:8])
        return prompt

    def validate_security(self) -> None:
        if self.app_env.lower() != "production":
            return
        if len(self.jwt_secret_key) < 32 or self.jwt_secret_key in {"change-me", "replace-with-a-long-random-secret"}:
            raise RuntimeError("生产环境必须配置至少 32 字符的 JWT_SECRET_KEY")
        if self.admin_password in {"change-me-now", "password"} or len(self.admin_password) < 12:
            raise RuntimeError("生产环境必须配置至少 12 字符的 ADMIN_PASSWORD")


@lru_cache
def get_settings() -> Settings:
    # AstrBot 的工作目录可能不同，配置仍从本项目读取。
    return Settings(_env_file=Path(__file__).resolve().parents[1] / ".env")
