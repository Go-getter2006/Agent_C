"""删除问答后，SQLite 不再复用原会话、消息或回合编号。"""
import sqlalchemy as sa
from alembic import op

revision = "0023"
down_revision = "0022"
branch_labels = None
depends_on = None

TABLES = ("chat_topics", "chat_messages", "chat_rounds")


def set_autoincrement(enabled: bool) -> None:
    conn = op.get_bind()
    if conn.dialect.name != "sqlite":
        return
    for table in TABLES:
        sql = conn.scalar(sa.text("SELECT sql FROM sqlite_master WHERE type = 'table' AND name = :name"),
                          {"name": table})
        if sql and ("AUTOINCREMENT" in sql.upper()) != enabled:
            with op.batch_alter_table(table, recreate="always", table_kwargs={"sqlite_autoincrement": enabled}):
                pass


def upgrade() -> None:
    set_autoincrement(True)


def downgrade() -> None:
    set_autoincrement(False)
