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
    "老爸",
    "爸",
    "妈妈",
    "母亲",
    "老妈",
    "妈",
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
ROLE_ALIASES = {
    "父亲": "爸爸",
    "老爸": "爸爸",
    "爸": "爸爸",
    "母亲": "妈妈",
    "老妈": "妈妈",
    "妈": "妈妈",
}


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


def _canonical_role(value: str) -> str:
    role = _clean(value)
    return ROLE_ALIASES.get(role, role)


def _split_relation_entities(value: str) -> tuple[str, ...]:
    parts = [
        _clean(item)
        for item in re.split(r"(?:跟|和|与|以及|及|、|/|＆|&)", _clean(value))
    ]
    return tuple(dict.fromkeys(item for item in parts if item))


def parse_memory_relations(content: str) -> tuple[MemoryRelation, ...]:
    text = _clean(content, 300)
    if not text:
        return ()

    role_match = re.match(
        rf"^(.+?)是(.+?)的({'|'.join(ROLE_WORDS)})$",
        text,
    )
    if role_match:
        subject = _clean(role_match.group(1))
        role = _canonical_role(role_match.group(3))
        owners = _split_relation_entities(role_match.group(2))
        if subject and owners:
            return tuple(
                MemoryRelation(subject, role, owner, "role")
                for owner in owners
            )

    patterns = (
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
            return (relation,)
    return ()


def parse_memory_relation(content: str) -> MemoryRelation | None:
    relations = parse_memory_relations(content)
    return relations[0] if relations else None


def rewrite_first_person_identity_question(question: str, speaker_name: str) -> str:
    """Bind first-person identity questions to the actual QQ sender.

    This prevents prompts such as "你知道我是谁吗" from being misread as
    "你是谁". Only identity-style questions are rewritten; ordinary first-person
    chat remains untouched.
    """
    text = _clean(question, 120)
    speaker = _clean(speaker_name, 80)
    if not text or not speaker:
        return text
    patterns = (
        r"^(?:你(?:还)?(?:知道|记得|认识))?我是谁(?:吗|嘛|呢)?$",
        r"^(?:知道|记得|认识)我是谁(?:吗|嘛|呢)?$",
        r"^我是谁(?:啊|呀|呢|吗|嘛)?$",
        r"^(?:你)?猜猜我是谁(?:吧|啊|呀|呢)?$",
    )
    if any(re.fullmatch(pattern, text) for pattern in patterns):
        return f"{speaker}是谁"
    return text


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
        if not _clean(item).startswith(("我是", "我叫", "我的", "你是", "你叫", "你的"))
        for relation in parse_memory_relations(item)
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
        owner_text = _clean(match.group(1))
        owners = _split_relation_entities(owner_text) or (owner_text,)
        role = _canonical_role(match.group(2))
        candidate_sets: list[set[str]] = []
        labels: dict[str, str] = {}
        for owner in owners:
            matches = {
                _key(relation.subject)
                for relation in relations
                if relation.kind == "role"
                and _canonical_role(relation.predicate) == role
                and _key(relation.object) == _key(owner)
            }
            for relation in relations:
                if relation.kind == "role" and _key(relation.subject) in matches:
                    labels.setdefault(_key(relation.subject), relation.subject)
            if not matches:
                candidate_sets = []
                break
            candidate_sets.append(matches)
        if candidate_sets:
            common = set.intersection(*candidate_sets)
            answers = tuple(labels[key] for key in sorted(common) if key in labels)
            if answers:
                return MemoryAnswer("role", "", role, owner_text, answers[:5])

    return None


def format_group_memory_answer(answer: MemoryAnswer, question: str) -> str:
    joined = "、".join(answer.answers)
    if answer.kind == "identity":
        if len(answer.answers) == 1:
            templates = (
                "苟修金，“{target}”就是 {joined}。前面这层对应关系能直接对上。",
                "吾辈没记岔，“{target}”指的就是 {joined}，不是另一个人。",
                "这里说的“{target}”就是 {joined}，苟修金，这两个称呼是连着的。",
                "答案是 {joined}。吾辈看得很清楚，“{target}”在这里就是指这个。",
            )
        else:
            templates = (
                "苟修金，“{target}”这边能顺着关系连到 {joined}，所以不止一个称呼。",
                "吾辈看这几个称呼是连在一起的：“{target}”可以对应到 {joined}。",
                "顺着前面的关系往回看，“{target}”会连到 {joined}，吾辈没有看岔。",
            )
        template = _stable_choice(question + joined, templates)
        return template.format(target=answer.subject, joined=joined)

    if answer.kind == "inverse_directional":
        templates = (
            "苟修金，{predicate}“{obj}”的是 {joined}。这条关系正好能反查出来。",
            "吾辈看过了，这里对应的是 {joined}——{joined} {predicate}“{obj}”。",
            "答案是 {joined}；把关系反过来看就能对上“{obj}”，吾辈可没绕晕。",
        )
        return _stable_choice(question + joined, templates).format(
            predicate=answer.predicate,
            obj=answer.object,
            joined=joined,
        )

    if answer.kind == "forward_directional":
        templates = (
            "苟修金，{subject}{predicate}的是 {joined}，这条关系能直接对上。",
            "吾辈看得明白，这里对应的是 {joined}。也就是 {subject}{predicate}的对象。",
            "{subject}{predicate}的是 {joined}，没有绕到别的人身上，吾辈没看错。",
        )
        return _stable_choice(question + joined, templates).format(
            subject=answer.subject,
            predicate=answer.predicate,
            joined=joined,
        )

    templates = (
        "苟修金，{owner}的{role}是 {joined}。这层关系方向别反了就行。",
        "吾辈看的是这一边：{joined} 才是 {owner}的{role}。",
        "问到{owner}的{role}，答案就是 {joined}，吾辈没有把关系方向弄反。",
    )
    return _stable_choice(question + joined, templates).format(
        owner=answer.object,
        role=answer.predicate,
        joined=joined,
    )


def group_memory_reasoning_hints(memories: list[str], limit: int = 20) -> str:
    hints: list[str] = []
    for item in memories:
        if _clean(item).startswith(("我是", "我叫", "我的", "你是", "你叫", "你的")):
            continue
        for relation in parse_memory_relations(item):
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
        if len(hints) >= max(1, limit):
            break
    if not hints:
        return ""
    return (
        "关系推理规则：简单“A是B/A叫B/A的名字是B”按可逆对应处理；"
        "其他动词和亲属关系保持方向，只在问题把主客体互换时做对应反查。\n"
        + "\n".join(hints)
    )
