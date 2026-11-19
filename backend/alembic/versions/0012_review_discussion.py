"""保存每道复习小题反馈后的独立问答。"""
from alembic import op
import sqlalchemy as sa

revision = "0012"
down_revision = "0011"
branch_labels = None
depends_on = None


def upgrade() -> None:
    if "discussion" not in {c["name"] for c in sa.inspect(op.get_bind()).get_columns("quiz_attempts")}:
        op.add_column("quiz_attempts", sa.Column("discussion", sa.JSON(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("quiz_attempts") as batch:
        batch.drop_column("discussion")
