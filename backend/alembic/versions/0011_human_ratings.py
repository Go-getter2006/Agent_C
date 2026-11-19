"""老师盲评“理解”（human_ratings）；日志申请可以去标识（log_requests.deidentified）

Revision ID: 0011
Revises: 0010
Create Date: 2026-11-19 20:00:00
"""
from alembic import op
import sqlalchemy as sa
import app.db


def _has(table: str) -> bool:
    """以前用 create_all 建过的库，这张表可能已经有了（见 app/db.py 的 migrate）。"""
    return table in sa.inspect(op.get_bind()).get_table_names()


def _columns(table: str) -> set[str]:
    return {c["name"] for c in sa.inspect(op.get_bind()).get_columns(table)}


revision = '0011'
down_revision = '0010'
branch_labels = None
depends_on = None


def upgrade() -> None:
    if not _has('human_ratings'):
        op.create_table('human_ratings',
        sa.Column('id', sa.BigInteger().with_variant(sa.Integer(), 'sqlite'), autoincrement=True, nullable=False),
        sa.Column('assessment_id', sa.Integer(), nullable=False),
        sa.Column('rater_id', sa.Integer(), nullable=False),
        sa.Column('course', sa.String(length=8), nullable=False, server_default='C'),
        sa.Column('level', sa.Integer(), nullable=True),
        sa.Column('seconds', sa.Integer(), nullable=True),
        sa.Column('created_at', app.db.UTCDateTime(), nullable=False),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('assessment_id', 'rater_id', name='uq_human_rating')
        )
        op.create_index('ix_human_ratings_assessment_id', 'human_ratings', ['assessment_id'])
        op.create_index('ix_human_ratings_rater_id', 'human_ratings', ['rater_id'])
    if 'deidentified' not in _columns('log_requests'):
        with op.batch_alter_table('log_requests') as batch:
            batch.add_column(sa.Column('deidentified', sa.Boolean(), nullable=False, server_default=sa.false()))


def downgrade() -> None:
    with op.batch_alter_table('log_requests') as batch:
        batch.drop_column('deidentified')
    op.drop_index('ix_human_ratings_rater_id', table_name='human_ratings')
    op.drop_index('ix_human_ratings_assessment_id', table_name='human_ratings')
    op.drop_table('human_ratings')
