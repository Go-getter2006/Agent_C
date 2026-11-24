"""按消息所属会话拆分旧答疑回合，撤销无法归属到单个会话的评估。"""
from collections import defaultdict

import sqlalchemy as sa
from alembic import op

revision = "0024"
down_revision = "0023"
branch_labels = None
depends_on = None

INDEX = "ix_chat_round_conversation"
HELP_ORDER = {"NA": -1, "H0": 0, "H1": 1, "H2": 2, "H3": 3}


def _conversation(row):
    problem = sa.literal("p") + sa.cast(row.c.server_id, sa.String) + "-" + sa.cast(row.c.problem_id, sa.String)
    return sa.case(
        (row.c.server_id.is_not(None) & row.c.problem_id.is_not(None), problem),
        else_="general",
    )


def _message_conversation(message):
    return sa.case(
        (message.c.topic_id.is_not(None), sa.literal("t") + sa.cast(message.c.topic_id, sa.String)),
        else_=_conversation(message),
    )


def _summary(messages, steps, key):
    """只根据所属会话留下的消息和步骤恢复计数。旧回合的结束原因无法可靠归给其中一组。"""
    help_levels = [step.help_level for step in steps if step.help_level in HELP_ORDER and step.help_level != "NA"]
    answered = [(msg.content or {}).get("blocks") or [] for msg in messages
                if msg.role == "agent" and msg.kind == "text" and msg.failure_submission_id is None
                and not (msg.content or {}).get("summary")]
    server, problem = next(((step.server_id, step.problem_id) for step in reversed(steps)
                            if step.server_id is not None and step.problem_id is not None), (None, None))
    if key.startswith("p"):
        server, problem = map(int, key[1:].split("-", 1))
    last = max(msg.created_at for msg in messages)
    return {
        "conversation_key": key, "started_at": min(msg.created_at for msg in messages),
        "last_message_at": last, "ended_at": last, "status": "closed", "end_reason": "会话拆分",
        "server_id": server, "problem_id": problem,
        "submission_id": next((step.submission_id for step in reversed(steps)
                               if step.submission_id is not None), None),
        "skill_code": next((step.root_skill_code for step in reversed(steps) if step.root_skill_code), ""),
        "error_code": next((step.root_error_code for step in reversed(steps) if step.root_skill_code), ""),
        "help_count": len(help_levels), "max_help_level": max(help_levels or ["NA"], key=HELP_ORDER.get),
        "last_help_level": help_levels[-1] if help_levels else "NA",
        "asked_back": sum(bool(blocks) and (blocks[-1].get("text") or "").rstrip().endswith("？")
                          for step in steps if step.help_level != "NA"
                          and (blocks := (step.reply or {}).get("blocks"))),
        "failed_replies": sum(step.intent == "REPLY" and step.answer_correct is False
                              and step.analysis_source != "button" for step in steps),
        "located": any((block.get("caption") or "").startswith(("你贴的代码", "你的第"))
                       for blocks in answered for block in blocks if block.get("t") == "code"),
        "recall_stage": "", "quiz_triggered": any(msg.quiz_attempt_id is not None for msg in messages),
    }


def _split(conn, rid, rounds, messages, steps, stuck, assessments, evidence):
    row = conn.execute(sa.select(rounds).where(rounds.c.id == rid)).one()
    groups = defaultdict(list)
    for msg in conn.execute(sa.select(messages, _message_conversation(messages).label("scope"))
                            .where(messages.c.round_id == rid).order_by(messages.c.id)):
        groups[msg.scope].append(msg)

    by_scope = defaultdict(list)
    scope_by_msg = {msg.id: scope for scope, group in groups.items() for msg in group}
    for step in conn.execute(sa.select(steps).join(messages, messages.c.id == steps.c.message_id)
                             .where(messages.c.round_id == rid).order_by(steps.c.message_id, steps.c.id)):
        by_scope[scope_by_msg[step.message_id]].append(step)

    bound = f"p{row.server_id}-{row.problem_id}"
    original = bound if bound in groups else next(iter(groups))
    new_rounds = {}
    for scope, group in groups.items():
        values = _summary(group, by_scope[scope], scope)
        if scope == original:
            conn.execute(rounds.update().where(rounds.c.id == rid).values(**values))
            new_rounds[scope] = rid
            continue
        result = conn.execute(rounds.insert().values(user_id=row.user_id, course=row.course, **values))
        new_rounds[scope] = result.inserted_primary_key[0]
        conn.execute(messages.update().where(messages.c.round_id == rid, _message_conversation(messages) == scope)
                     .values(round_id=new_rounds[scope]))
        conn.execute(steps.update().where(steps.c.message_id.in_(
            sa.select(messages.c.id).where(messages.c.round_id == new_rounds[scope])))
                     .values(round_id=new_rounds[scope]))

    # 卡点归给首次记录它的步骤。没有步骤的旧卡点只能按明确绑定的作业题匹配。
    owner_by_stuck = {}
    for scope, recorded in by_scope.items():
        for step in recorded:
            if step.stuck_point_id is not None:
                entry = (step.message_id, new_rounds[scope])
                owner_by_stuck[step.stuck_point_id] = min(entry, owner_by_stuck.get(step.stuck_point_id, entry))
    points = conn.execute(sa.select(stuck.c.id, stuck.c.server_id, stuck.c.problem_id).where(stuck.c.round_id == rid)).all()
    for point in points:
        fallback = new_rounds.get(f"p{point.server_id}-{point.problem_id}")
        owner = owner_by_stuck.get(point.id, (None, fallback))[1]
        conn.execute(stuck.update().where(stuck.c.id == point.id).values(round_id=owner))

    conn.execute(assessments.delete().where(assessments.c.round_id == rid))
    conn.execute(evidence.delete().where(evidence.c.user_id == row.user_id, evidence.c.source_key == f"assess:{rid}"))


def upgrade() -> None:
    conn = op.get_bind()
    inspector = sa.inspect(conn)
    if "conversation_key" not in {col["name"] for col in inspector.get_columns("chat_rounds")}:
        op.add_column("chat_rounds", sa.Column("conversation_key", sa.String(64), nullable=False, server_default=""))
    if INDEX not in {index["name"] for index in sa.inspect(conn).get_indexes("chat_rounds")}:
        op.create_index(INDEX, "chat_rounds", ["user_id", "course", "conversation_key", "status"])

    meta = sa.MetaData()
    rounds, messages, steps, stuck, assessments, evidence = (
        sa.Table(name, meta, autoload_with=conn)
        for name in ("chat_rounds", "chat_messages", "chat_steps", "stuck_points", "agent_assessments", "skill_evidence")
    )
    scope = _message_conversation(messages)
    first = (sa.select(scope).where(messages.c.round_id == rounds.c.id)
             .order_by(messages.c.id).limit(1).scalar_subquery())
    conn.execute(rounds.update().where(rounds.c.conversation_key == "")
                 .values(conversation_key=sa.func.coalesce(first, _conversation(rounds))))
    mixed = sa.select(messages.c.round_id).where(messages.c.round_id.is_not(None)).group_by(messages.c.round_id) \
        .having(sa.func.count(sa.distinct(scope)) > 1)
    for rid in conn.scalars(mixed).all():
        _split(conn, rid, rounds, messages, steps, stuck, assessments, evidence)


def downgrade() -> None:
    # 已拆分的数据保留分别归属的回合，旧评估无法恢复。
    op.drop_index(INDEX, table_name="chat_rounds")
    op.drop_column("chat_rounds", "conversation_key")
