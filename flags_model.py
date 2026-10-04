"""Swichi za Admin (key/value). Jedwali linatengenezwa na db.create_all()."""
from models import db


class AppFlag(db.Model):
    __tablename__ = 'app_flags'

    key = db.Column(db.String(60), primary_key=True)
    value = db.Column(db.String(200))
