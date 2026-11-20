"""系统替学生做的决定（研究用）：复习先出哪个技能、出正确还是有错的代码、提交没过时发不发主动消息。
记下当时的候选和依据、规则版本、选了哪个、选中的概率、结果在哪（app/decisions.py）。"""
import sqlalchemy as sa
from alembic import op

revision = "0015"
down_revision = "0014"
branch_labels = None
depends_on = None


def upgrade():
    conn = op.get_bind()
    if "decision_logs" in sa.inspect(conn).get_table_names():
        return
    op.create_table(
        "decision_logs",
        sa.Column("id", sa.BigInteger().with_variant(sa.Integer(), "sqlite"), primary_key=True, autoincrement=True),
        sa.Column("user_id", sa.String(32), nullable=False),
        sa.Column("course", sa.String(8), nullable=False, server_default="C"),
        sa.Column("kind", sa.String(16), nullable=False),
        sa.Column("ts", sa.DateTime(timezone=True), nullable=False),
        sa.Column("rule", sa.String(32), nullable=False, server_default=""),
        sa.Column("candidates", sa.JSON(), nullable=False),
        sa.Column("chosen", sa.String(64), nullable=False, server_default=""),
        sa.Column("probability", sa.Float(), nullable=False, server_default="1"),
        sa.Column("context", sa.JSON(), nullable=False),
        sa.Column("ref", sa.JSON(), nullable=False),
    )
    op.create_index("ix_decision_logs_user_id", "decision_logs", ["user_id"])
    op.create_index("ix_decision_logs_kind", "decision_logs", ["kind"])


def downgrade():
    op.drop_index("ix_decision_logs_kind", table_name="decision_logs")
    op.drop_index("ix_decision_logs_user_id", table_name="decision_logs")
    op.drop_table("decision_logs")
