import re
from collections import deque
from dataclasses import dataclass


IDENTITY_QUERY_SUFFIXES = (
    "指的是谁",
    "是什么",
    "叫什么",
    "是哪位",
    "是谁",
    "指谁",
)
DIRECTIONAL_VERBS = (
    "喜欢",
    "讨厌",
    "认识",
    "知道",
    "看过",
    "玩过",
    "用过",
    "听过",
    "吃过",
    "买过",
)
ROLE_WORDS = (
    "爸爸",
    "父亲",
    "妈妈",
    "母亲",
    "儿子",
    "女儿",
    "哥哥",
    "姐姐",
    "弟弟",
    "妹妹",
    "朋友",
    "老师",
    "学生",
    "老板",
    "同事",
    "队友",
    "对象",
    "男朋友",
    "女朋友",
    "老婆",
    "老公",
)


@dataclass(frozen=True)
class MemoryRelation:
    subject: str
    predicate: str
    object: str
    kind: str


@dataclass(frozen=True)
class MemoryAnswer:
    kind: str
    subject: str
    predicate: str
    object: str
    answers: tuple[str, ...]


def _clean(value: str, limit: int = 100) -> str:
    value = re.sub(r"\s+", " ", str(value or "")).strip(" ，,。；;：:")
    return value[:limit]


def _key(value: str) -> str:
    return _clean(value).casefold()


def parse_memory_relation(content: str) -> MemoryRelation | None:
    text = _clean(content, 300)
    if not text:
        return None

    patterns = (
        (
            rf"^(.+?)是(.+?)的({'|'.join(ROLE_WORDS)})$",
            lambda m: MemoryRelation(
                _clean(m.group(1)),
                _clean(m.group(3)),
                _clean(m.group(2)),
                "role",
            ),
        ),
        (
            r"^(.+?)的名字是(.+)$",
            lambda m: MemoryRelation(
                _clean(m.group(1)),
                "是",
                _clean(m.group(2)),
                "identity",
            ),
        ),
        (
            r"^(.+?)叫(.+)$",
            lambda m: MemoryRelation(
                _clean(m.group(1)),
                "是",
                _clean(m.group(2)),
                "identity",
            ),
        ),
        (
            r"^(.+?)(?:就是|是)(.+)$",
            lambda m: MemoryRelation(
                _clean(m.group(1)),
                "是",
                _clean(m.group(2)),
                "identity",
            ),
        ),
        (
            r"^(.+?)=(.+)$",
            lambda m: MemoryRelation(
                _clean(m.group(1)),
                "是",
                _clean(m.group(2)),
                "identity",
            ),
        ),
        (
            rf"^(.+?)({'|'.join(DIRECTIONAL_VERBS)})(.+)$",
            lambda m: MemoryRelation(
                _clean(m.group(1)),
                _clean(m.group(2)),
                _clean(m.group(3)),
                "directional",
            ),
        ),
    )
    for pattern, factory in patterns:
        match = re.match(pattern, text)
        if not match:
            continue
        relation = factory(match)
        if relation.subject and relation.object:
            return relation
    return None


def _identity_target(question: str) -> str:
    text = _clean(question, 120)
    for suffix in IDENTITY_QUERY_SUFFIXES:
        if text.endswith(suffix):
            return _clean(text[: -len(suffix)])
    match = re.fullmatch(r"谁(?:是|叫)(.+)", text)
    return _clean(match.group(1)) if match else ""


def _stable_choice(seed: str, options: tuple[str, ...]) -> str:
    return options[sum(ord(ch) for ch in seed) % len(options)]


def _identity_answers(target: str, relations: list[MemoryRelation]) -> tuple[str, ...]:
    graph: dict[str, list[str]] = {}
    labels: dict[str, str] = {}
    for relation in relations:
        if relation.kind != "identity":
            continue
        left, right = _key(relation.subject), _key(relation.object)
        if not left or not right or left == right:
            continue
        labels.setdefault(left, relation.subject)
        labels.setdefault(right, relation.object)
        graph.setdefault(left, []).append(right)
        graph.setdefault(right, []).append(left)

    start = _key(target)
    if start not in graph:
        return ()

    found: list[str] = []
    seen = {start}
    queue = deque([start])
    while queue and len(found) < 5:
        current = queue.popleft()
        for neighbor in graph.get(current, []):
            if neighbor in seen:
                continue
            seen.add(neighbor)
            queue.append(neighbor)
            found.append(labels.get(neighbor, neighbor))
            if len(found) >= 5:
                break
    return tuple(found)


def resolve_group_memory_question(
    question: str, memories: list[str]
) -> MemoryAnswer | None:
    relations = [
        relation
        for item in memories
        if (relation := parse_memory_relation(item)) is not None
    ]
    if not relations:
        return None

    target = _identity_target(question)
    if target:
        answers = _identity_answers(target, relations)
        if answers:
            return MemoryAnswer("identity", target, "是", "", answers)

    text = _clean(question, 120)

    match = re.fullmatch(
        rf"谁({'|'.join(DIRECTIONAL_VERBS)})(.+)",
        text,
    )
    if match:
        predicate, obj = _clean(match.group(1)), _clean(match.group(2))
        answers = tuple(
            relation.subject
            for relation in relations
            if relation.kind == "directional"
            and relation.predicate == predicate
            and _key(relation.object) == _key(obj)
        )
        if answers:
            return MemoryAnswer("inverse_directional", "", predicate, obj, answers[:5])

    match = re.fullmatch(
        rf"(.+?)({'|'.join(DIRECTIONAL_VERBS)})(?:谁|什么|哪个|哪一个)",
        text,
    )
    if match:
        subject, predicate = _clean(match.group(1)), _clean(match.group(2))
        answers = tuple(
            relation.object
            for relation in relations
            if relation.kind == "directional"
            and relation.predicate == predicate
            and _key(relation.subject) == _key(subject)
        )
        if answers:
            return MemoryAnswer("forward_directional", subject, predicate, "", answers[:5])

    match = re.fullmatch(
        rf"(.+?)的({'|'.join(ROLE_WORDS)})是谁",
        text,
    )
    if match:
        owner, role = _clean(match.group(1)), _clean(match.group(2))
        answers = tuple(
            relation.subject
            for relation in relations
            if relation.kind == "role"
            and relation.predicate == role
            and _key(relation.object) == _key(owner)
        )
        if answers:
            return MemoryAnswer("role", "", role, owner, answers[:5])

    return None


def format_group_memory_answer(answer: MemoryAnswer, question: str) -> str:
    joined = "、".join(answer.answers)
    if answer.kind == "identity":
        if len(answer.answers) == 1:
            templates = (
                "嗯，你说的“{target}”就是 {joined}，这个对应关系我还记着。",
                "记得。按之前记下来的关系，“{target}”对应的是 {joined}。",
                "有印象，“{target}”指的就是 {joined}，这条关系没忘。",
                "按之前的群记忆来看，“{target}”就是 {joined}，所以这次能对上。",
            )
        else:
            templates = (
                "按之前记住的对应关系，“{target}”能关联到 {joined}。",
                "我这边记着，“{target}”对应到 {joined}，不只一个。",
                "之前留下的关系里，“{target}”和 {joined} 都连得上。",
            )
        template = _stable_choice(question + joined, templates)
        return template.format(target=answer.subject, joined=joined)

    if answer.kind == "inverse_directional":
        templates = (
            "按之前记住的关系，{predicate}“{obj}”的是 {joined}。",
            "这条我记得：{joined} {predicate}“{obj}”。",
            "从之前记下来的关系看，答案是 {joined}——{predicate}的是“{obj}”。",
        )
        return _stable_choice(question + joined, templates).format(
            predicate=answer.predicate,
            obj=answer.object,
            joined=joined,
        )

    if answer.kind == "forward_directional":
        templates = (
            "之前记过，{subject}{predicate}的是 {joined}。",
            "有这条关系：{subject}{predicate} {joined}，我没忘。",
            "按之前的群记忆，{subject}{predicate}的对象是 {joined}。",
        )
        return _stable_choice(question + joined, templates).format(
            subject=answer.subject,
            predicate=answer.predicate,
            joined=joined,
        )

    templates = (
        "按之前记住的关系，{owner}的{role}是 {joined}。",
        "这条关系还在：{joined} 是 {owner}的{role}。",
        "记得，问到{owner}的{role}，对应的是 {joined}。",
    )
    return _stable_choice(question + joined, templates).format(
        owner=answer.object,
        role=answer.predicate,
        joined=joined,
    )


def group_memory_reasoning_hints(memories: list[str], limit: int = 20) -> str:
    hints: list[str] = []
    for item in memories:
        relation = parse_memory_relation(item)
        if relation is None:
            continue
        if relation.kind == "identity":
            hints.append(
                f"- 可逆对应：{relation.subject} ↔ {relation.object}。"
                f"问“{relation.object}是谁/谁是{relation.object}”时可反推出{relation.subject}。"
            )
        elif relation.kind == "directional":
            hints.append(
                f"- 定向关系：{relation.subject}{relation.predicate}{relation.object}；"
                f"可回答“谁{relation.predicate}{relation.object}”和"
                f"“{relation.subject}{relation.predicate}谁/什么”。"
            )
        else:
            hints.append(
                f"- 关系方向：{relation.subject}是{relation.object}的{relation.predicate}；"
                f"可回答“{relation.object}的{relation.predicate}是谁”，但不要颠倒亲属/关系方向。"
            )
        if len(hints) >= max(1, limit):
            break
    if not hints:
        return ""
    return (
        "关系推理规则：简单“A是B/A叫B/A的名字是B”按可逆对应处理；"
        "其他动词和亲属关系保持方向，只在问题把主客体互换时做对应反查。\n"
        + "\n".join(hints)
    )
