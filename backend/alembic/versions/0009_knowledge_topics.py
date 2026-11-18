"""知识问答：问答主题（chat_topics），消息记上属于哪个主题（chat_messages.topic_id）

Revision ID: 0009
Revises: 0008
Create Date: 2026-11-19 18:00:00
"""
from alembic import op
import sqlalchemy as sa
import app.db


def _has(table: str) -> bool:
    """以前用 create_all 建过的库，这张表可能已经有了（见 app/db.py 的 migrate）。"""
    return table in sa.inspect(op.get_bind()).get_table_names()


def _columns(table: str) -> set[str]:
    return {c["name"] for c in sa.inspect(op.get_bind()).get_columns(table)}


revision = '0009'
down_revision = '0008'
branch_labels = None
depends_on = None


def upgrade() -> None:
    if not _has('chat_topics'):
        op.create_table('chat_topics',
        sa.Column('id', sa.BigInteger().with_variant(sa.Integer(), 'sqlite'), autoincrement=True, nullable=False),
        sa.Column('user_id', sa.String(length=32), nullable=False),
        sa.Column('course', sa.String(length=8), nullable=False, server_default='C'),
        sa.Column('title', sa.String(length=40), nullable=False, server_default=''),
        sa.Column('created_at', app.db.UTCDateTime(), nullable=False),
        sa.Column('last_message_at', app.db.UTCDateTime(), nullable=False),
        sa.PrimaryKeyConstraint('id')
        )
        op.create_index('ix_chat_topics_user_id', 'chat_topics', ['user_id'])
    if 'topic_id' not in _columns('chat_messages'):
        with op.batch_alter_table('chat_messages') as batch:
            batch.add_column(sa.Column('topic_id', sa.Integer(), nullable=True))
        op.create_index('ix_chat_messages_topic_id', 'chat_messages', ['topic_id'])


def downgrade() -> None:
    op.drop_index('ix_chat_messages_topic_id', table_name='chat_messages')
    with op.batch_alter_table('chat_messages') as batch:
        batch.drop_column('topic_id')
    op.drop_index('ix_chat_topics_user_id', table_name='chat_topics')
    op.drop_table('chat_topics')
