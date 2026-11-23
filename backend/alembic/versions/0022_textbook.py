"""私有教材版本、章节和配图。"""
import sqlalchemy as sa
from alembic import op

revision = "0022"
down_revision = "0021"
branch_labels = None
depends_on = None


def upgrade():
    existing = set(sa.inspect(op.get_bind()).get_table_names())
    key = sa.BigInteger().with_variant(sa.Integer(), "sqlite")
    if "textbook_editions" not in existing:
        op.create_table("textbook_editions",
                        sa.Column("id", key, primary_key=True, autoincrement=True),
                        sa.Column("digest", sa.String(64), nullable=False, unique=True),
                        sa.Column("title", sa.String(255), nullable=False),
                        sa.Column("published_at", sa.DateTime(timezone=True), nullable=False))
    if "textbook_sections" not in existing:
        op.create_table("textbook_sections",
                        sa.Column("edition_id", key, sa.ForeignKey("textbook_editions.id"), primary_key=True),
                        sa.Column("number", sa.Integer, primary_key=True),
                        sa.Column("parent", sa.Integer, nullable=True),
                        sa.Column("title", sa.String(255), nullable=False),
                        sa.Column("pages", sa.JSON, nullable=False),
                        sa.Column("anchors", sa.JSON, nullable=False))
    if "textbook_figures" not in existing:
        op.create_table("textbook_figures",
                        sa.Column("edition_id", key, sa.ForeignKey("textbook_editions.id"), primary_key=True),
                        sa.Column("digest", sa.String(64), primary_key=True),
                        sa.Column("png", sa.LargeBinary, nullable=False))


def downgrade():
    for table in ("textbook_figures", "textbook_sections", "textbook_editions"):
        op.drop_table(table)
