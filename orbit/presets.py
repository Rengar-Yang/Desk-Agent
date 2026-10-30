"""Built-in Chat Completions services. The user only supplies an API key."""


class ApiPreset:
    def __init__(self, preset_id, label, endpoint, model):
        self.id = preset_id
        self.label = label
        self.endpoint = endpoint
        self.model = model


# OpenAI-compatible chat completions. Model names are the provider's stable aliases.
API_PRESETS = (
    ApiPreset("deepseek", "DeepSeek", "https://api.deepseek.com/chat/completions", "deepseek-chat"),
    ApiPreset("openai", "OpenAI GPT", "https://api.openai.com/v1/chat/completions", "gpt-4.1"),
    ApiPreset("qwen", "通义千问", "https://dashscope.aliyuncs.com/compatible-mode/v1/chat/completions", "qwen-plus"),
    ApiPreset("kimi", "Kimi", "https://api.moonshot.cn/v1/chat/completions", "moonshot-v1-auto"),
    ApiPreset("glm", "智谱 GLM", "https://open.bigmodel.cn/api/paas/v4/chat/completions", "glm-4-flash"),
)


def preset_by_id(preset_id):
    for preset in API_PRESETS:
        if preset.id == preset_id:
            return preset
    return None


def match_preset(endpoint, model):
    endpoint = (endpoint or "").strip().rstrip("/")
    model = (model or "").strip()
    for preset in API_PRESETS:
        if preset.endpoint.rstrip("/") == endpoint and preset.model == model:
            return preset
    return None
