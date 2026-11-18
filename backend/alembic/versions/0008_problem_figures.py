"""题面：不用 OCR。PDF 文字层抽出的原文、大模型整理的时间，以及从页面上裁下来的示意图

Revision ID: 0008
Revises: 0007
Create Date: 2026-11-19 12:00:00
"""
from alembic import op
import sqlalchemy as sa
import app.db


def _has(table: str) -> bool:
    """以前用 create_all 建过的库，这张表可能已经有了（见 app/db.py 的 migrate）。"""
    return table in sa.inspect(op.get_bind()).get_table_names()


def _columns(table: str) -> set[str]:
    return {c["name"] for c in sa.inspect(op.get_bind()).get_columns(table)}


revision = '0008'
down_revision = '0007'
branch_labels = None
depends_on = None


def upgrade() -> None:
    cols = _columns('xdoj_problem_texts')
    with op.batch_alter_table('xdoj_problem_texts') as batch:
        if 'raw_text' not in cols:
            batch.add_column(sa.Column('raw_text', sa.Text(), nullable=False, server_default=''))
        if 'tidied_at' not in cols:
            batch.add_column(sa.Column('tidied_at', app.db.UTCDateTime(), nullable=True))
    if not _has('xdoj_problem_figures'):
        op.create_table('xdoj_problem_figures',
        sa.Column('server_id', sa.Integer(), nullable=False),
        sa.Column('problem_id', sa.BigInteger(), nullable=False),
        sa.Column('seq', sa.Integer(), nullable=False),
        sa.Column('page', sa.Integer(), nullable=False),
        sa.Column('png', sa.LargeBinary(), nullable=False),
        sa.Column('width', sa.Integer(), nullable=False),
        sa.Column('height', sa.Integer(), nullable=False),
        sa.PrimaryKeyConstraint('server_id', 'problem_id', 'seq')
        )


def downgrade() -> None:
    op.drop_table('xdoj_problem_figures')
    with op.batch_alter_table('xdoj_problem_texts') as batch:
        batch.drop_column('tidied_at')
        batch.drop_column('raw_text')
