"""每道题的解题步骤（作业引导里还没做的题问“怎么做”时给的流程图）：按（服务器, 题目）存一份，所有学生共用（app/chat/plan.py）。"""
import sqlalchemy as sa
from alembic import op

revision = "0016"
down_revision = "0015"
branch_labels = None
depends_on = None


def upgrade():
    conn = op.get_bind()
    if "problem_plans" in sa.inspect(conn).get_table_names():
        return
    op.create_table(
        "problem_plans",
        sa.Column("server_id", sa.Integer(), primary_key=True),
        sa.Column("problem_id", sa.BigInteger(), primary_key=True),
        sa.Column("steps", sa.JSON(), nullable=False),
        sa.Column("source", sa.String(16), nullable=False, server_default="template"),
        sa.Column("version", sa.String(16), nullable=False, server_default=""),
        sa.Column("content_hash", sa.String(64), nullable=False, server_default=""),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )


def downgrade():
    op.drop_table("problem_plans")
