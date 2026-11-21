"""日志导出可以再带提交的源码（勾了“包含原文”之下再勾“包含提交的源码”，app/logs.py）。"""
import sqlalchemy as sa
from alembic import op

revision = "0019"
down_revision = "0018"
branch_labels = None
depends_on = None


def upgrade():
    conn = op.get_bind()
    existing = {c["name"] for c in sa.inspect(conn).get_columns("log_requests")}
    if "include_code" not in existing:
        op.add_column("log_requests", sa.Column("include_code", sa.Boolean(), nullable=False, server_default=sa.false()))


def downgrade():
    with op.batch_alter_table("log_requests") as batch:
        batch.drop_column("include_code")
