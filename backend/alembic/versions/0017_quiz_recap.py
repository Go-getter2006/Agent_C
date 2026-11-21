"""小题做完以后的回顾（他以前在这里出过的错，程序整理事实、大模型分析）：每次作答算一次，存下来（app/recap.py）。"""
import sqlalchemy as sa
from alembic import op

revision = "0017"
down_revision = "0016"
branch_labels = None
depends_on = None


def upgrade():
    conn = op.get_bind()
    existing = {c["name"] for c in sa.inspect(conn).get_columns("quiz_attempts")}
    if "recap" not in existing:
        op.add_column("quiz_attempts", sa.Column("recap", sa.JSON(), nullable=True))


def downgrade():
    with op.batch_alter_table("quiz_attempts") as batch:
        batch.drop_column("recap")
