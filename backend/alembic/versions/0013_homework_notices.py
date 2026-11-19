"""每次失败提交一条作业提醒，保存学生的阅读状态和复习触发依据。"""
import sqlalchemy as sa
from alembic import op

revision = "0013"
down_revision = "0012"
branch_labels = None
depends_on = None


def upgrade():
    conn = op.get_bind()
    tables = {
        "xdoj_submissions": [sa.Column("notice_eligible", sa.Boolean())],
        "review_items": [sa.Column("trigger_submission_id", sa.BigInteger()), sa.Column("trigger_at", sa.DateTime(timezone=True))],
        "chat_messages": [sa.Column("failure_submission_id", sa.BigInteger()), sa.Column("notice_read_at", sa.DateTime(timezone=True))],
    }
    for table, columns in tables.items():
        existing = {c["name"] for c in sa.inspect(conn).get_columns(table)}
        for col in columns:
            if col.name not in existing:
                op.add_column(table, col)
    msg = sa.Table("chat_messages", sa.MetaData(), autoload_with=conn)
    if "ux_msg_failure" not in {i["name"] for i in sa.inspect(conn).get_indexes(msg.name)}:
        op.create_index("ux_msg_failure", msg.name, ["server_id", "failure_submission_id"], unique=True)
    # 已经发过的旧提醒保持原样，升级后不会突然亮起一批历史红点。
    old = msg.c.content["proactive"]["submission"].as_integer()
    rows = sa.select(sa.func.min(msg.c.id), old).where(old.is_not(None)).group_by(msg.c.server_id, old)
    for mid, sid in conn.execute(rows):
        conn.execute(msg.update().where(msg.c.id == mid).values(failure_submission_id=sid, notice_read_at=msg.c.created_at))


def downgrade():
    op.drop_index("ux_msg_failure", table_name="chat_messages")
    for table, names in (("chat_messages", ["failure_submission_id", "notice_read_at"]),
                         ("review_items", ["trigger_submission_id", "trigger_at"]),
                         ("xdoj_submissions", ["notice_eligible"])):
        with op.batch_alter_table(table) as batch:
            for name in names:
                batch.drop_column(name)
