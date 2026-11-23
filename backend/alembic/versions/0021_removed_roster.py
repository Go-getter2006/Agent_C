"""开发者移除教师数据源或班级时保留历史记录。"""
import sqlalchemy as sa
from alembic import op

revision = "0021"
down_revision = "0020"
branch_labels = None
depends_on = None


def upgrade():
    inspector = sa.inspect(op.get_bind())
    for table in ("data_sources", "xdoj_classes"):
        if "removed_at" not in {column["name"] for column in inspector.get_columns(table)}:
            op.add_column(table, sa.Column("removed_at", sa.DateTime(timezone=True), nullable=True))


def downgrade():
    for table in ("data_sources", "xdoj_classes"):
        op.drop_column(table, "removed_at")
