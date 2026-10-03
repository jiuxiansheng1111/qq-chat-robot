from collections import defaultdict, deque


class ConversationMemory:
    """可选的进程内短上下文，只在内存中使用，不会持久化。"""

    def __init__(self, max_messages: int = 10):
        self.max_messages = max(2, max_messages)
        self._history: dict[tuple[str, str, str], deque[dict[str, str]]] = defaultdict(
            lambda: deque(maxlen=self.max_messages)
        )

    def messages(
        self,
        group_id: str,
        user_id: str,
        system_prompt: str,
        user_text: str,
        enabled: bool,
        persona_id: str = "default",
    ) -> list[dict[str, str]]:
        key = (group_id, user_id, persona_id)
        result = [{"role": "system", "content": system_prompt}]
        if enabled:
            result.extend(self._history[key])
        result.append({"role": "user", "content": user_text})
        return result

    def append(
        self,
        group_id: str,
        user_id: str,
        user_text: str,
        assistant_text: str,
        enabled: bool,
        persona_id: str = "default",
    ) -> None:
        if not enabled:
            return
        history = self._history[(group_id, user_id, persona_id)]
        history.append({"role": "user", "content": user_text})
        history.append({"role": "assistant", "content": assistant_text})

    def clear(self, group_id: str, user_id: str) -> None:
        for key in tuple(self._history):
            if key[:2] == (group_id, user_id):
                self._history.pop(key, None)
