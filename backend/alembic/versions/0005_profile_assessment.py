"""多维掌握画像：智能体评估“理解”、对话行为、融合的维度权重、智能体写的学情评语

Revision ID: 0005
Revises: 0004
Create Date: 2026-11-18 16:00:00
"""
from alembic import op
import sqlalchemy as sa
import app.db

revision = '0005'
down_revision = '0004'
branch_labels = None
depends_on = None

ID = sa.BigInteger().with_variant(sa.Integer(), 'sqlite')


def upgrade() -> None:
    op.create_table('agent_assessments',
    sa.Column('id', ID, autoincrement=True, nullable=False),
    sa.Column('user_id', sa.String(length=32), nullable=False),
    sa.Column('course', sa.String(length=8), nullable=False),
    sa.Column('skill_code', sa.String(length=16), nullable=False),
    sa.Column('round_id', sa.Integer(), nullable=False),
    sa.Column('level', sa.Integer(), nullable=True),
    sa.Column('raw_level', sa.Integer(), nullable=True),
    sa.Column('rule_level', sa.Integer(), nullable=True),
    sa.Column('confidence', sa.Float(), nullable=False),
    sa.Column('evidence', sa.JSON(), nullable=False),
    sa.Column('misconception', sa.String(length=120), nullable=False),
    sa.Column('adjusted', sa.String(length=80), nullable=False),
    sa.Column('source', sa.String(length=8), nullable=False),
    sa.Column('prompt_version', sa.String(length=24), nullable=False),
    sa.Column('created_at', app.db.UTCDateTime(), nullable=False),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('round_id', 'skill_code', name='uq_assessment_round_skill')
    )
    op.create_index(op.f('ix_agent_assessments_user_id'), 'agent_assessments', ['user_id'], unique=False)
    op.create_index(op.f('ix_agent_assessments_skill_code'), 'agent_assessments', ['skill_code'], unique=False)
    op.create_index(op.f('ix_agent_assessments_round_id'), 'agent_assessments', ['round_id'], unique=False)
    op.create_table('agent_reviews',
    sa.Column('id', ID, autoincrement=True, nullable=False),
    sa.Column('user_id', sa.String(length=32), nullable=False),
    sa.Column('course', sa.String(length=8), nullable=False),
    sa.Column('profile_hash', sa.String(length=64), nullable=False),
    sa.Column('content', sa.JSON(), nullable=False),
    sa.Column('source', sa.String(length=8), nullable=False),
    sa.Column('requested_by', sa.Integer(), nullable=True),
    sa.Column('created_at', app.db.UTCDateTime(), nullable=False),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_agent_reviews_user_id'), 'agent_reviews', ['user_id'], unique=False)
    op.create_table('model_weights',
    sa.Column('course', sa.String(length=8), nullable=False),
    sa.Column('weights', sa.JSON(), nullable=False),
    sa.Column('n', sa.Integer(), nullable=False),
    sa.Column('brier', sa.Float(), nullable=True),
    sa.Column('learned', sa.Boolean(), nullable=False),
    sa.Column('updated_at', app.db.UTCDateTime(), nullable=False),
    sa.PrimaryKeyConstraint('course')
    )
    op.add_column('chat_steps', sa.Column('act', sa.String(length=16), nullable=False, server_default=''))
    op.add_column('chat_steps', sa.Column('answer_correct', sa.Boolean(), nullable=True))
    op.add_column('prediction_log', sa.Column('p_fused', sa.Float(), nullable=True))
    op.add_column('prediction_log', sa.Column('p_v2_base', sa.Float(), nullable=True))
    op.add_column('prediction_log', sa.Column('features', sa.JSON(), nullable=False, server_default='{}'))


def downgrade() -> None:
    with op.batch_alter_table('prediction_log') as batch:
        batch.drop_column('features')
        batch.drop_column('p_v2_base')
        batch.drop_column('p_fused')
    with op.batch_alter_table('chat_steps') as batch:
        batch.drop_column('answer_correct')
        batch.drop_column('act')
    op.drop_table('model_weights')
    op.drop_index(op.f('ix_agent_reviews_user_id'), table_name='agent_reviews')
    op.drop_table('agent_reviews')
    op.drop_index(op.f('ix_agent_assessments_round_id'), table_name='agent_assessments')
    op.drop_index(op.f('ix_agent_assessments_skill_code'), table_name='agent_assessments')
    op.drop_index(op.f('ix_agent_assessments_user_id'), table_name='agent_assessments')
    op.drop_table('agent_assessments')
