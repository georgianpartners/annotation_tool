"""add entity_type to models with temporary nullabel true

Revision ID: 7c026be6d31e
Revises: 610c9b5a2493
Create Date: 2020-07-07 19:36:26.051540

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = '7c026be6d31e'
down_revision = '610c9b5a2493'
branch_labels = None
depends_on = None


def upgrade():
    # ### commands auto generated by Alembic - please adjust! ###
    with op.batch_alter_table('model', schema=None) as batch_op:
        batch_op.add_column(sa.Column('entity_type', sa.String(), nullable=True))
        batch_op.create_index(batch_op.f('ix_model_entity_type'), ['entity_type'], unique=False)

    # ### end Alembic commands ###


def downgrade():
    # ### commands auto generated by Alembic - please adjust! ###
    with op.batch_alter_table('model', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_model_entity_type'))
        batch_op.drop_column('entity_type')

    # ### end Alembic commands ###