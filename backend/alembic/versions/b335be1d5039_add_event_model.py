"""Add Event model

Revision ID: b335be1d5039
Revises: a224ad0c4028
Create Date: 2026-07-22 13:00:00.000000

"""
from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = 'b335be1d5039'
down_revision = 'cecf3447d0de'
branch_labels = None
depends_on = None

def upgrade() -> None:
    op.create_table('events',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('robot_id', sa.Integer(), nullable=False),
        sa.Column('message', sa.String(length=512), nullable=False),
        sa.Column('timestamp', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_events_id'), 'events', ['id'], unique=False)
    op.create_index(op.f('ix_events_robot_id'), 'events', ['robot_id'], unique=False)
    op.create_index(op.f('ix_events_timestamp'), 'events', ['timestamp'], unique=False)
    op.create_index('ix_events_robot_id_desc', 'events', ['robot_id', sa.text('timestamp DESC')], unique=False)

def downgrade() -> None:
    op.drop_index('ix_events_robot_id_desc', table_name='events')
    op.drop_index(op.f('ix_events_timestamp'), table_name='events')
    op.drop_index(op.f('ix_events_robot_id'), table_name='events')
    op.drop_index(op.f('ix_events_id'), table_name='events')
    op.drop_table('events')
