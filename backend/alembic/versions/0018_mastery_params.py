"""每周学一次的掌握程度参数（app/learn.py），预测记录里同时记学到的参数和经验值参数算的掌握分。"""
import sqlalchemy as sa
from alembic import op

revision = "0018"
down_revision = "0017"
branch_labels = None
depends_on = None


def upgrade():
    conn = op.get_bind()
    insp = sa.inspect(conn)
    if "mastery_params" not in insp.get_table_names():
        op.create_table(
            "mastery_params",
            sa.Column("id", sa.BigInteger().with_variant(sa.Integer(), "sqlite"), primary_key=True, autoincrement=True),
            sa.Column("course", sa.String(8), nullable=False),
            sa.Column("version", sa.String(24), nullable=False, unique=True),
            sa.Column("params", sa.JSON(), nullable=False),
            sa.Column("n", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("nll_prior", sa.Float(), nullable=True),
            sa.Column("nll_fit", sa.Float(), nullable=True),
            sa.Column("learned", sa.Boolean(), nullable=False, server_default=sa.false()),
            sa.Column("detail", sa.JSON(), nullable=False),
            sa.Column("fitted_at", sa.DateTime(timezone=True), nullable=False),
        )
        op.create_index("ix_mastery_params_course", "mastery_params", ["course"])
    cols = {c["name"] for c in insp.get_columns("prediction_log")}
    for name, col in (("p_score", sa.Float()), ("p_score_fixed", sa.Float()), ("params_version", sa.String(24))):
        if name not in cols:
            op.add_column("prediction_log", sa.Column(name, col, nullable=True))


def downgrade():
    with op.batch_alter_table("prediction_log") as batch:
        for name in ("p_score", "p_score_fixed", "params_version"):
            batch.drop_column(name)
    op.drop_table("mastery_params")
