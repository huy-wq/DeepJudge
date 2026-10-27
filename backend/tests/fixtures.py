"""测试夹具：一份 TCP 三次握手的评分标准、一份学生答卷与一份模型输出样本。"""

import json

RUBRIC_DATA = {
    "question_id": "Q-01",
    "stem": "简述 TCP 三次握手的过程，并说明其目的。",
    "reference_answer": (
        "客户端发送 SYN=1、seq=x；服务器回复 SYN=1、ACK=1、seq=y、ack=x+1；"
        "客户端回复 ACK=1、seq=x+1、ack=y+1。目的是同步双方初始序列号、确认收发能力。"
    ),
    "total_score": 10,
    "items": [
        {
            "id": "P1",
            "desc": "客户端发送 SYN 及初始序列号",
            "weight": 3,
            "levels": [
                {"level": 1.0, "score": 3.0, "criterion": "明确 SYN=1、seq=x 并说明目的"},
                {"level": 0.5, "score": 1.5, "criterion": "只提到发送连接请求"},
                {"level": 0.0, "score": 0.0, "criterion": "未提及"},
            ],
        },
        {
            "id": "P2",
            "desc": "服务器回复 SYN+ACK",
            "weight": 4,
            "levels": [
                {"level": 1.0, "score": 4.0, "criterion": "SYN、ACK、seq、ack 均正确"},
                {"level": 0.5, "score": 2.0, "criterion": "只说服务器同意，缺字段"},
                {"level": 0.0, "score": 0.0, "criterion": "未提及"},
            ],
        },
        {
            "id": "P3",
            "desc": "客户端回复 ACK，连接建立",
            "weight": 3,
            "levels": [
                {"level": 1.0, "score": 3.0, "criterion": "ACK 与序号的确认关系正确"},
                {"level": 0.0, "score": 0.0, "criterion": "未提及或错误"},
            ],
        },
    ],
    "deductions": [
        {"id": "D1", "desc": "与四次挥手混淆", "penalty": -2, "cap": -2}
    ],
    "rules": {"allow_inference": False, "synonym_ok": True, "ignore_length": True},
}

ANSWER = (
    "客户端首先发送 SYN 报文，seq=x，请求建立连接；"
    "服务器回复 SYN=1、ACK=1，seq=y，ack=x+1；"
    "客户端再发送 ACK=1，seq=x+1，ack=y+1。"
    "三次握手用于同步双方初始序列号，并确认双方的收发能力。"
)

SAMPLE_PAYLOAD = {
    "items": [
        {
            "id": "P1",
            "hit": True,
            "level": 1.0,
            "evidence": "客户端首先发送 SYN 报文，seq=x",
            "reason": "SYN 与序号均正确",
        },
        {
            "id": "P2",
            "hit": True,
            "level": 0.5,
            "evidence": "服务器回复 SYN=1、ACK=1，seq=y，ack=x+1",
            "reason": "字段完整但未说明同意接收",
        },
        {
            "id": "P3",
            "hit": True,
            "level": 1.0,
            "evidence": "客户端再发送 ACK=1，seq=x+1，ack=y+1",
            "reason": "确认关系正确",
        },
    ],
    "deductions": [{"id": "D1", "hit": False, "evidence": ""}],
    "comment": "过程基本正确，第二次握手的目的说明不足。",
}


def payload_text(**overrides) -> str:
    """把 SAMPLE_PAYLOAD 序列化成模型输出文本，可局部覆盖顶层字段。"""
    data = json.loads(json.dumps(SAMPLE_PAYLOAD))
    data.update(overrides)
    return json.dumps(data, ensure_ascii=False)


def rubric_variant(**changes) -> dict:
    """在夹具标准上做局部修改，返回**未解析**的 dict。

    采分点按 id 指定（`_items={"P1": {"desc": ...}}`），其余键（`question_id`、`stem`、
    `total_score`……）直接覆盖顶层。深拷贝，避免测试之间互相改到同一份常量。
    """
    data = json.loads(json.dumps(RUBRIC_DATA))
    items = changes.pop("_items", {})
    data.update(changes)
    for item_id, patch in items.items():
        data["items"][int(item_id[1:]) - 1].update(patch)
    return data


def generation_payload(**overrides) -> str:
    """模型对"生成标准"的典型回答：摘要 + 采分点 + 扣分项。"""
    data = {
        "summary": "本题按三次握手的三个步骤拆成三个采分点，另设一个混淆扣分项。",
        "items": json.loads(json.dumps(RUBRIC_DATA["items"])),
        "deductions": json.loads(json.dumps(RUBRIC_DATA["deductions"])),
    }
    data.update(overrides)
    return json.dumps(data, ensure_ascii=False)


def payload_with_levels(*levels) -> str:
    """固定三个采分点的等级，生成一次采样的模型输出。"""
    data = json.loads(payload_text())
    for item, level in zip(data["items"], levels):
        item["level"] = level
    return json.dumps(data, ensure_ascii=False)


# 两次采样差异越大，总分极差越大 → risk 越高（risk 只用于复核队列排序）
MILD_DISAGREEMENT = [payload_with_levels(1, 1, 1), payload_with_levels(0, 1, 1)]  # risk 7
STRONG_DISAGREEMENT = [payload_with_levels(1, 0, 0), payload_with_levels(0, 1, 1)]  # risk 15
