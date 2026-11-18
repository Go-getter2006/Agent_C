"""C 语言和数据结构分开登录、分开显示：对话、回合、页面事件、日志申请记上是哪门课

已有的对话：绑定了题的按题所在的服务器定课程，没绑定的（综合对话）算 C 语言。
顺带：teacher_sources.user_id 在 PostgreSQL 上改成和 users.id 一样的 BIGINT（0003 建成了 INTEGER）。

Revision ID: 0004
Revises: 0003
Create Date: 2026-11-18 12:00:00
"""
from alembic import op
import sqlalchemy as sa

revision = '0004'
down_revision = '0003'
branch_labels = None
depends_on = None

TABLES = ("chat_rounds", "chat_messages", "ui_events", "log_requests")


def upgrade() -> None:
    if op.get_bind().dialect.name == "postgresql":
        op.alter_column('teacher_sources', 'user_id', type_=sa.BigInteger(), existing_type=sa.Integer(),
                        existing_nullable=False)
    for table in TABLES:
        op.add_column(table, sa.Column('course', sa.String(length=8), nullable=False, server_default='C'))
    for table in ("chat_rounds", "chat_messages"):
        op.execute(f"UPDATE {table} SET course = (SELECT s.course FROM xdoj_servers s WHERE s.id = {table}.server_id) "
                   f"WHERE server_id IS NOT NULL AND EXISTS (SELECT 1 FROM xdoj_servers s WHERE s.id = {table}.server_id)")


def downgrade() -> None:
    for table in TABLES:
        with op.batch_alter_table(table) as batch:
            batch.drop_column('course')
    if op.get_bind().dialect.name == "postgresql":
        op.alter_column('teacher_sources', 'user_id', type_=sa.Integer(), existing_type=sa.BigInteger(),
                        existing_nullable=False)
