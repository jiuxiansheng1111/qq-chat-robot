from collections import defaultdict, deque


class ConversationMemory:
    """Opt-in, process-local short context; it is intentionally not persisted."""

    def __init__(self, max_messages: int = 10):
        self.max_messages = max(2, max_messages)
        self._history: dict[tuple[str, str], deque[dict[str, str]]] = defaultdict(
            lambda: deque(maxlen=self.max_messages)
        )

    def messages(self, group_id: str, user_id: str, system_prompt: str, user_text: str, enabled: bool) -> list[dict[str, str]]:
        key = (group_id, user_id)
        result = [{"role": "system", "content": system_prompt}]
        if enabled:
            result.extend(self._history[key])
        result.append({"role": "user", "content": user_text})
        return result

    def append(self, group_id: str, user_id: str, user_text: str, assistant_text: str, enabled: bool) -> None:
        if not enabled:
            return
        history = self._history[(group_id, user_id)]
        history.append({"role": "user", "content": user_text})
        history.append({"role": "assistant", "content": assistant_text})

    def clear(self, group_id: str, user_id: str) -> None:
        self._history.pop((group_id, user_id), None)
