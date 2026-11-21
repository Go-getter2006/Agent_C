"""预测的历史版本与首交条件；旧预测保留原值。"""
import sqlalchemy as sa
from alembic import op

revision = "0020"
down_revision = "0019"
branch_labels = None
depends_on = None


def upgrade():
    conn = op.get_bind()
    inspector = sa.inspect(conn)
    if not inspector.has_table("fusion_weights_history"):
        table = op.create_table("fusion_weights_history",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("course", sa.String(8), nullable=False, index=True),
            sa.Column("fitted_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("weights", sa.JSON(), nullable=False))
        conn.execute(table.insert().from_select(["course", "fitted_at", "weights"],
            sa.text("SELECT course, updated_at, weights FROM model_weights").columns(
                course=sa.String(), updated_at=sa.DateTime(), weights=sa.JSON())))
    if "provenance" not in {c["name"] for c in inspector.get_columns("prediction_log")}:
        op.add_column("prediction_log", sa.Column("provenance", sa.JSON(), nullable=True))


def downgrade():
    with op.batch_alter_table("prediction_log") as batch:
        batch.drop_column("provenance")
    op.drop_table("fusion_weights_history")
