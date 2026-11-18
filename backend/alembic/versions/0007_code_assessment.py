"""智能体看代码评“理解”：没问过问题的学生，按他在 OJ 上怎么改代码来评

agent_assessments 加上依据（chat / code）、题和提交；round_id 可以为空（看代码评的没有对话回合）。

Revision ID: 0007
Revises: 0006
Create Date: 2026-11-19 10:00:00
"""
from alembic import op
import sqlalchemy as sa

revision = '0007'
down_revision = '0006'
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table('agent_assessments') as batch:
        batch.add_column(sa.Column('basis', sa.String(length=8), nullable=False, server_default='chat'))
        batch.add_column(sa.Column('server_id', sa.Integer(), nullable=True))
        batch.add_column(sa.Column('problem_id', sa.BigInteger(), nullable=True))
        batch.add_column(sa.Column('submission_id', sa.BigInteger(), nullable=True))
        batch.alter_column('round_id', existing_type=sa.Integer(), nullable=True)


def downgrade() -> None:
    op.execute("DELETE FROM agent_assessments WHERE round_id IS NULL")
    with op.batch_alter_table('agent_assessments') as batch:
        batch.alter_column('round_id', existing_type=sa.Integer(), nullable=False)
        batch.drop_column('submission_id')
        batch.drop_column('problem_id')
        batch.drop_column('server_id')
        batch.drop_column('basis')
