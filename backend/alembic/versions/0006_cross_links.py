"""跨课程：带类型的联系（人工初稿、智能体提出、老师采用），以及每条联系的数据检验

Revision ID: 0006
Revises: 0005
Create Date: 2026-11-18 20:00:00
"""
from alembic import op
import sqlalchemy as sa
import app.db


def _has(table: str) -> bool:
    """以前用 create_all 建过的库，这张表可能已经有了（见 app/db.py 的 migrate）。"""
    return table in sa.inspect(op.get_bind()).get_table_names()

revision = '0006'
down_revision = '0005'
branch_labels = None
depends_on = None


def upgrade() -> None:
    if not _has('cross_links'):
        op.create_table('cross_links',
        sa.Column('id', sa.BigInteger().with_variant(sa.Integer(), 'sqlite'), autoincrement=True, nullable=False),
        sa.Column('c_skill', sa.String(length=16), nullable=False),
        sa.Column('d_skill', sa.String(length=16), nullable=False),
        sa.Column('relation', sa.String(length=16), nullable=False),
        sa.Column('source', sa.String(length=8), nullable=False),
        sa.Column('status', sa.String(length=10), nullable=False),
        sa.Column('rationale', sa.String(length=120), nullable=False),
        sa.Column('confidence', sa.Float(), nullable=True),
        sa.Column('created_at', app.db.UTCDateTime(), nullable=False),
        sa.Column('reviewed_by', sa.Integer(), nullable=True),
        sa.Column('reviewed_at', app.db.UTCDateTime(), nullable=True),
        sa.Column('n_with', sa.Integer(), nullable=False),
        sa.Column('n_without', sa.Integer(), nullable=False),
        sa.Column('rate_with', sa.Float(), nullable=True),
        sa.Column('rate_without', sa.Float(), nullable=True),
        sa.Column('prob_support', sa.Float(), nullable=True),
        sa.Column('verdict', sa.String(length=12), nullable=False),
        sa.Column('checked_at', app.db.UTCDateTime(), nullable=True),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('c_skill', 'd_skill', name='uq_cross_link')
        )
        op.create_index(op.f('ix_cross_links_c_skill'), 'cross_links', ['c_skill'], unique=False)
        op.create_index(op.f('ix_cross_links_d_skill'), 'cross_links', ['d_skill'], unique=False)


def downgrade() -> None:
    op.drop_index(op.f('ix_cross_links_d_skill'), table_name='cross_links')
    op.drop_index(op.f('ix_cross_links_c_skill'), table_name='cross_links')
    op.drop_table('cross_links')
