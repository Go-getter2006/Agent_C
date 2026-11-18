"""复习提升的独立作答：小题作答记下协议版本、题目版本、事件顺序、首次选行、首稿、提示和公开样例运行次数

Revision ID: 0010
Revises: 0009
Create Date: 2026-11-19 20:00:00
"""
from alembic import op
import sqlalchemy as sa


def _columns(table: str) -> set[str]:
    return {c["name"] for c in sa.inspect(op.get_bind()).get_columns(table)}


revision = '0010'
down_revision = '0009'
branch_labels = None
depends_on = None

_NEW = [
    ('protocol', sa.Integer(), '1'),
    ('item_version', sa.String(length=16), ''),
    ('events', sa.JSON(), None),
    ('first_lines', sa.JSON(), None),
    ('locate_hit', sa.Boolean(), None),
    ('first_fix', sa.Text(), ''),
    ('first_fix_pass', sa.Boolean(), None),
    ('first_fix_clean', sa.Boolean(), None),
    ('hints', sa.Integer(), '0'),
    ('sample_runs', sa.Integer(), '0'),
]


def upgrade() -> None:
    have = _columns('quiz_attempts')
    with op.batch_alter_table('quiz_attempts') as batch:
        for name, typ, default in _NEW:
            if name in have:
                continue
            if default is None:
                batch.add_column(sa.Column(name, typ, nullable=True))
            else:
                batch.add_column(sa.Column(name, typ, nullable=False, server_default=default))


def downgrade() -> None:
    with op.batch_alter_table('quiz_attempts') as batch:
        for name, _typ, _default in reversed(_NEW):
            batch.drop_column(name)
