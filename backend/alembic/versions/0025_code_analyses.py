"""源码画像的云端结果与待处理队列；保留以前所有记录。"""
from alembic import op
import sqlalchemy as sa

revision = "0025"
down_revision = "0024"
branch_labels = None
depends_on = None


def upgrade():
    from app.models import CodeAnalysis
    CodeAnalysis.__table__.create(bind=op.get_bind(), checkfirst=True)


def downgrade():
    if "code_analyses" in sa.inspect(op.get_bind()).get_table_names():
        op.drop_table("code_analyses")
