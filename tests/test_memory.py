from app.llm.memory import ConversationMemory


def test_memory_is_opt_in_and_clearable():
    memory = ConversationMemory(max_messages=4)
    system = "persona"
    assert len(memory.messages("g", "u", system, "hello", False)) == 2
    memory.append("g", "u", "hello", "hi", False)
    assert len(memory.messages("g", "u", system, "next", False)) == 2

    memory.append("g", "u", "hello", "hi", True)
    assert len(memory.messages("g", "u", system, "next", True)) == 4
    memory.clear("g", "u")
    assert len(memory.messages("g", "u", system, "next", True)) == 2
