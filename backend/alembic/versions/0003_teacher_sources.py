"""一位老师关联多个 XDOJ 账号（每门课一台服务器，账号密码可能不同）

已有的“老师 → 一个数据源”关系照样有效（users.data_source_id），这里只加关联表，并把已有的关系抄一份进去。

Revision ID: 0003
Revises: 0002
Create Date: 2026-11-18 10:00:00
"""
from alembic import op
import sqlalchemy as sa
import app.db

revision = '0003'
down_revision = '0002'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table('teacher_sources',
    sa.Column('user_id', sa.Integer(), nullable=False),
    sa.Column('data_source_id', sa.Integer(), nullable=False),
    sa.Column('created_at', app.db.UTCDateTime(), nullable=False),
    sa.ForeignKeyConstraint(['data_source_id'], ['data_sources.id'], ),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], ),
    sa.PrimaryKeyConstraint('user_id', 'data_source_id')
    )
    op.execute("INSERT INTO teacher_sources (user_id, data_source_id, created_at) "
               "SELECT id, data_source_id, CURRENT_TIMESTAMP FROM users "
               "WHERE role = 'teacher' AND data_source_id IS NOT NULL")


def downgrade() -> None:
    op.drop_table('teacher_sources')
