"""题面按哪一版整理要求整理的：要求变了（比如公式改写成 LaTeX），后台把整理过的题面按新要求各重整一次。"""
import sqlalchemy as sa
from alembic import op

revision = "0014"
down_revision = "0013"
branch_labels = None
depends_on = None


def upgrade():
    conn = op.get_bind()
    existing = {c["name"] for c in sa.inspect(conn).get_columns("xdoj_problem_texts")}
    if "tidy_version" not in existing:
        # 以前整理过的都留空：后台会按现在的要求各重整一次（每道题一次，按服务器共用）
        op.add_column("xdoj_problem_texts", sa.Column("tidy_version", sa.String(16)))


def downgrade():
    with op.batch_alter_table("xdoj_problem_texts") as batch:
        batch.drop_column("tidy_version")
